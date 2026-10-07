#!/usr/bin/env python3
"""Translate a built EPUB with a local Ollama model.

Leaf blocks (paragraph, heading, list item, caption, TOC label, ...) are the unit
of reconstruction. Inline elements become numbered placeholders (<x1>...</x1>,
<x2/>) that the model keeps in place; the originals (note links, page-break
anchors, emphasis) are restored from them, so IDs and internal links survive
translation. Per-model settings (config/translation-models.json) choose the
prompt style, how many consecutive blocks share one request (each wrapped in
<segN> tags), how much preceding translation is shown as context, and sampling.
Blocks longer than the chunk size are split at top-level sentence boundaries.
Raw model outputs are cached in an append-only JSONL file keyed by the full
request, so interrupted runs resume and reruns are offline replays.
"""

import argparse
import copy
import html
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from urllib.parse import unquote
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lxml import etree
from tqdm import tqdm
from common import ROOT, digest, write_epub, write_json, write_text_atomic
from build import validate_epub

LANGS = {
    "en": "English",
    "ru": "Russian",
    "uk": "Ukrainian",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "pl": "Polish",
    "nl": "Dutch",
    "he": "Hebrew",
    "ja": "Japanese",
    "zh": "Chinese",
}
# Leaf blocks are translated; a block containing another block is descended into.
BLOCKS = set(
    "p h1 h2 h3 h4 h5 h6 li dt dd td th figcaption caption blockquote div title text".split()
)
SKIP = {"pre", "code", "script", "style", "head"}
MAX_CHUNK = 2000  # characters; TranslateGemma was trained on inputs up to ~2K tokens
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
DC = "{http://purl.org/dc/elements/1.1/}"


MODEL_CONFIGS = ROOT / "config/translation-models.json"


def model_config(model, path=MODEL_CONFIGS):
    """Settings for model, falling back to its untagged name, then "default".
    Like Ollama, a name without a tag means :latest."""
    configs = json.loads(Path(path).read_text())
    base = configs["default"]
    tagged = model if ":" in model else model + ":latest"
    for name in (tagged, model.split(":")[0]):
        if name in configs:
            own = configs[name]
            options = {**base.get("options", {}), **own.get("options", {})}
            return {**base, **own, "options": options, "name": name}
    return {**base, "name": "default"}


def narrator_note(gender):
    """Russian past-tense verbs and short adjectives agree with "I", which English
    does not mark, so a model given isolated chunks otherwise picks per chunk."""
    if not gender:
        return ""
    person, forms = {"male": ("man", "masculine"), "female": ("woman", "feminine")}[
        gender
    ]
    return (
        f'The narrator ("I") is a {person}: use {forms} forms for the first person.\n'
    )


def gemma_prompt(text, source, target, note=""):
    """The prompt format published with TranslateGemma; deviations degrade it."""
    s, t = LANGS[source], LANGS[target]
    return (
        f"You are a professional {s} ({source}) to {t} ({target}) translator. "
        f"Your goal is to accurately convey the meaning and nuances of the original {s} text "
        f"while adhering to {t} grammar, vocabulary, and cultural sensitivities.\n{note}"
        f"Produce only the {t} translation, without any additional explanations or commentary. "
        f"Please translate the following {s} text into {t}:\n\n\n{text}"
    )


def instruct_system(source, target, note=""):
    s, t = LANGS[source], LANGS[target]
    return (
        f"You are a professional literary translator from {s} into {t}. Translate "
        f"each user message into natural, fluent {t} that reads as if originally "
        f"written in {t}, preserving the meaning, tone and register of the book; "
        f"keep terminology and names consistent with your earlier translations. "
        f"Render foreign words and names fully in the {t} script; never mix "
        f"alphabets within one word.\n{note}"
        "The text contains placeholder tags such as <x1>...</x1> and <x2/>, and "
        "paragraph tags <seg1>...</seg1>. Keep every tag exactly as given: wrap the "
        "translation of the tagged words in the same tag, and translate every "
        "paragraph inside its own seg tag, in order.\n"
        f"Reply with the {t} translation only, without explanations or commentary."
    )


class Translator:
    """Ollama chat client: prompt style, context and sampling come from the model config."""

    def __init__(self, model, cache, config, host="http://localhost:11434", note=""):
        self.model, self.cache_path, self.host = model, Path(cache), host
        self.config, self.note = config, note
        self.chunk_chars = config["chunk_chars"]
        self.batch = config["batch"]
        self.history = []  # (source, translation) pairs for context
        self.glossary = {}  # source term -> target rendering
        self.brief = {}  # genre, register, period, people: {name: male|female}
        # Approved (source, translation) pairs shown as style examples; filled
        # from the book's corrections file by translate_epub.
        self.examples = []
        self.example_count = config.get("examples", 3)
        self.cache = {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text().splitlines():
                row = json.loads(line)
                self.cache[row["key"]] = row["output"]
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.timings = []  # Ollama counters of the requests made in this run
        self.parallel = config.get("parallel", 1)
        self.lock = threading.Lock()  # cache and log, shared with forks

    @property
    def calls(self):
        return len(self.timings)

    def fork(self):
        """A translator for one document translated in parallel: own history,
        shared cache, timings, glossary and brief."""
        twin = copy.copy(self)
        twin.history = []
        return twin

    def messages(self, text, source, target):
        if self.config["prompt"] == "hy-mt":
            return [{"role": "user", "content": self.hy_prompt(text, source, target)}]
        if self.config["prompt"] == "translategemma":
            return [
                {
                    "role": "user",
                    "content": gemma_prompt(text, source, target, self.note),
                }
            ]
        context = [
            {"role": role, "content": content}
            for pair in self.examples + self.recent()
            for role, content in zip(("user", "assistant"), pair)
        ]
        system = instruct_system(source, target, self.note)
        about = brief_lines(self.brief, text)
        if about:
            system += "\n" + about
        terms = glossary_lines(self.glossary, text)
        if terms:
            system += (
                "\nTranslate these terms consistently as given, inflecting as "
                "grammar requires:\n" + terms
            )
        return [{"role": "system", "content": system}, *context] + [
            {"role": "user", "content": text}
        ]

    def recent(self):
        """Most recent (source, translation) pairs within context_chars of source."""
        pairs, size = [], 0
        for previous, translation in reversed(self.history):
            size += len(previous)
            if size > self.config["context_chars"]:
                break
            pairs.insert(0, (previous, translation))
        return pairs

    def hy_prompt(self, text, source, target):
        """Tencent Hy-MT2's published templates (background, terminology,
        delimiters), combined into the one user turn the model is trained on;
        it has no system prompt."""
        t = LANGS[target]
        parts = []
        approved = "\n".join(
            f"Approved example:\n{source_text}\n=>\n{translation}"
            for source_text, translation in self.examples
        )
        background = "\n".join(
            part
            for part in [brief_lines(self.brief, text), approved]
            + [translation for _, translation in self.recent()]
            if part
        )
        if background:
            parts.append(f"[Background Information]\n{background}")
        terms = [
            f"{term} translates to {rendering}"
            for term, rendering in self.glossary.items()
            if re.search(rf"(?<!\w){re.escape(term)}", text, re.I)
        ]
        if terms:
            parts.append("Reference the following translations:\n" + "\n".join(terms))
        instruction = f"Please accurately translate the following text into {t}"
        instruction += (
            ", taking the provided background information into consideration."
            if background
            else "."
        )
        if "<" in text:
            instruction += (
                " You must retain the exact same tags (such as <x1>...</x1>, <x2/> "
                "and <seg1>...</seg1>) in the translation, around the translation of "
                "the same words. Strictly do not omit, escape, or translate these tags."
            )
        if self.note:
            instruction += " " + self.note.strip()
        instruction += (
            " You must ONLY output the translated result without any additional "
            "explanation:"
        )
        parts.append(
            instruction + "\n\n" + (f"[Source Text]\n{text}" if background else text)
        )
        return "\n\n".join(parts)

    def __call__(self, text, source, target, retry=False):
        options = dict(self.config["options"])
        if retry:
            options["temperature"] = self.config["retry_temperature"]
        # num_predict bounds runaway repetition; one token per input character
        # is far above any real translation length.
        output = self.chat(self.messages(text, source, target), options, len(text))
        self.history.append((text, output))
        return output

    def chat(self, messages, options, length, format=None):
        think = self.config["think"]
        body = {
            "model": self.model,
            "stream": False,
            "think": think,
            "messages": messages,
            "options": {
                **options,
                "num_ctx": self.config["num_ctx"],
                "num_predict": length + 256 + (8192 if think else 0),
            },
        }
        if format:
            body["format"] = format
        key = digest(json.dumps(body, sort_keys=True, ensure_ascii=False).encode())
        with self.lock:
            cached = self.cache.get(key)
        if cached is None:
            request = urllib.request.Request(
                self.host + "/api/chat",
                json.dumps(body).encode(),
                {"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=1800) as response:
                answer = json.load(response)
            output = answer["message"]["content"].strip()
            # Ollama reports durations in nanoseconds; prefill (prompt) versus
            # decode (output) time decides whether prompt reuse is worth doing.
            timing = {
                name: answer.get(name, 0)
                for name in (
                    "prompt_eval_count",
                    "prompt_eval_duration",
                    "eval_count",
                    "eval_duration",
                    "load_duration",
                    "total_duration",
                )
            }
            with self.lock:
                self.timings.append(timing)
                self.cache[key] = output
                with self.cache_path.open("a") as stream:
                    row = {"key": key, "output": output, "timing": timing}
                    stream.write(json.dumps(row) + "\n")
            cached = output
        return cached

    def judge_batch(self, items, source, target):
        """Score (source, translation) plain-text pairs 0-100 for accuracy and
        fluency, with short error labels; None for items the judge skipped.
        The judge should be a different model from the translator."""
        s, t = LANGS[source], LANGS[target]
        system = (
            f"You are a strict professional reviewer of {s} to {t} book translations. "
            "For each numbered item, compare the translation with the source and give "
            "a score from 0 to 100: 100 is complete, accurate and natural; deduct for "
            "omissions, additions, mistranslations, wrong terms or names, grammar "
            "errors and unnatural phrasing, in proportion to their severity. Reply "
            'with a JSON object mapping each item number to {"score": n, "errors": '
            '["short label: what is wrong"]}, with an empty errors list for good items.'
        )
        listing = {
            str(i): {"source": src, "translation": tgt}
            for i, (src, tgt) in enumerate(items, 1)
        }
        output = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(listing, ensure_ascii=False)},
            ],
            {"temperature": 0.0},
            120 * len(items),
            format="json",
        )
        try:
            answer = json.loads(output)
        except json.JSONDecodeError:
            answer = {}
        results = []
        for i in range(1, len(items) + 1):
            item = answer.get(str(i)) if isinstance(answer, dict) else None
            score = item.get("score") if isinstance(item, dict) else None
            errors = item.get("errors") if isinstance(item, dict) else None
            if isinstance(score, (int, float)) and 0 <= score <= 100:
                labels = (
                    [str(e)[:160] for e in errors] if isinstance(errors, list) else []
                )
                results.append((int(score), labels))
            else:
                results.append((None, []))
        return results

    def post_edit(self, source_text, draft, source, target, glossary=None, brief=None):
        """Second-opinion translation: the source with its placeholder tags and a
        weak draft in, a corrected translation that uses the source's tags out.
        A different model reviews, because self-refinement repeats its own bias."""
        s, t = LANGS[source], LANGS[target]
        system = (
            f"You are an expert editor of {s} to {t} literary translations. You get "
            f"a {s} source paragraph and a draft {t} translation that may omit, "
            "garble or mistranslate parts. Reply with the corrected, complete, natural "
            f"{t} translation of the whole source. The source contains placeholder "
            "tags such as <x1>...</x1> and <x2/>; keep every tag exactly once, around "
            "the translation of the same words. Reply with the translation only."
        )
        if self.note:
            system += "\n" + self.note.strip()
        about = brief_lines(brief or {}, source_text)
        if about:
            system += "\n" + about
        terms = glossary_lines(glossary or {}, source_text)
        if terms:
            system += "\nUse these renderings, inflected as needed:\n" + terms
        user = f"Source:\n{source_text}\n\nDraft translation:\n{draft}"
        options = dict(self.config["options"])
        return self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            options,
            len(source_text),
        )

    def make_brief(self, excerpt, names, source, batch=30):
        """Book brief from the opening prose and, for each recurring name, the
        sentences that mention it: genre, register, period and each person's
        gender (needed for verb and adjective agreement in the target). Names go
        in batches so each request fits small context windows (hy-mt2: 8192)."""
        system = (
            f"You read the beginning of a {LANGS[source]} book and prepare notes for "
            "its translator. Reply with a JSON object with keys: genre, register "
            "(e.g. first-person scholarly memoir, formal academic), period (when "
            "and where it is set), and people: an object mapping each given name to "
            '"male", "female" or "unknown", judged only from pronouns and context '
            'in the sentences provided. Use "unknown" for places, peoples, '
            "organizations and anything not a single person."
        )
        brief, people = {}, {}
        items = list(names.items()) or [None]
        for i in range(0, len(items), batch):
            part = dict(p for p in items[i : i + batch] if p)
            output = self.chat(
                [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"opening": excerpt, "names": part}, ensure_ascii=False
                        ),
                    },
                ],
                {"temperature": 0.0},
                300 + 20 * len(part),
                format="json",
            )
            try:
                answer = json.loads(output)
            except json.JSONDecodeError:
                continue
            for key in ("genre", "register", "period"):
                value = answer.get(key)
                if key not in brief and isinstance(value, str) and value.strip():
                    brief[key] = value.strip()
            found = (
                answer.get("people") if isinstance(answer.get("people"), dict) else {}
            )
            people |= {
                name: gender
                for name, gender in found.items()
                if name in part and gender in ("male", "female")
            }
        brief["people"] = people
        return brief

    def translate_terms(self, terms, source, target, batch=30):
        """{term: rendering} for (term, example sentence) pairs, one JSON request
        per batch. Renderings much longer than their term (an echoed example
        sentence) are dropped; the term is then left to the translator."""
        s, t = LANGS[source], LANGS[target]
        system = (
            f"You prepare a glossary for translating a book from {s} into {t}. The "
            "user sends a JSON object mapping each term to one sentence showing how "
            f"the book uses it. Reply with a JSON object mapping each term, exactly "
            f"as given, to the {t} word or phrase a professional translator would "
            f"use for that term throughout the book: established {t} spellings for "
            f"names, places, peoples and foreign words, fully in the {t} script, in "
            "the base form (nominative; plural if the term is plural). Give only the "
            "rendering of the term itself, never a translation of the sentence."
        )
        glossary = {}
        for i in range(0, len(terms), batch):
            part = dict(terms[i : i + batch])
            output = self.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(part, ensure_ascii=False)},
                ],
                {"temperature": 0.0},
                4 * sum(len(term) + 20 for term in part),
                format="json",
            )
            try:
                answer = json.loads(output)
            except json.JSONDecodeError:
                continue
            for term in part:
                rendering = answer.get(term)
                if not isinstance(rendering, str) or not rendering.strip():
                    continue
                rendering = rendering.strip()
                words = len(term.split()) + 2
                if (
                    len(rendering) <= 3 * len(term) + 15
                    and len(rendering.split()) <= words
                ):
                    glossary[term] = rendering
        return glossary


TOKEN = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")


def glossary_lines(glossary, text):
    """Entries whose term occurs in text (case-insensitive, as a word prefix so
    plurals and possessives match)."""
    lines = [
        f"{term} = {rendering}"
        for term, rendering in glossary.items()
        if re.search(rf"(?<!\w){re.escape(term)}", text, re.I)
    ]
    return "\n".join(lines)


def brief_lines(brief, text):
    """The book description plus the gender of each person named in text."""
    if not brief:
        return ""
    lines = []
    about = "; ".join(brief[k] for k in ("genre", "register", "period") if brief.get(k))
    if about:
        lines.append(f"About the book: {about}.")
    people = [
        f"{name} ({'man' if gender == 'male' else 'woman'})"
        for name, gender in brief.get("people", {}).items()
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text)
    ]
    if people:
        lines.append(
            "People in this passage: "
            + ", ".join(people)
            + ". Use the matching grammatical gender for each."
        )
    return "\n".join(lines)


def brief_inputs(texts, glossary, excerpt_chars=3000, per_name=1):
    """Opening prose and up to per_name sentences for each capitalized glossary
    term (candidate person names)."""
    plain_texts = [plain(t) for t in texts]
    excerpt = ""
    for text in plain_texts:
        if len(excerpt) >= excerpt_chars:
            break
        excerpt += text + "\n"
    names = {}
    for term in glossary:
        if not term[:1].isupper() or term.isupper():
            continue
        found = []
        for text in plain_texts:
            for sentence in re.split(r"(?<=[.!?])\s+", text):
                if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", sentence):
                    found.append(sentence[:240])
                if len(found) >= per_name:
                    break
            if len(found) >= per_name:
                break
        if found:
            names[term] = found
    return excerpt[:excerpt_chars], names


PHRASE_OPENERS = frozenset(
    "from the a an in on at by for and of to with after before see as".split()
)
NAME_PHRASE = re.compile(
    r"(?<![\w’'-])[A-Z][\w’'-]+(?:\s+[A-Z][\w’'-]+){1,3}(?![\w’'-])"
)


def glossary_terms(
    texts, known=lambda word: False, limit=150, min_count=3, phrase_limit=40
):
    """(term, example sentence) for recurring names and foreign or coined words:
    the words a model renders differently from chunk to chunk. known(word) says
    whether a spelling dictionary accepts the lowercase word; ordinary
    vocabulary is left to the translator. Names recur capitalized and never in
    lower case; one that is also a dictionary word (Crick, Amazon) must be
    capitalized mid-sentence in most occurrences, which sentence openers such as
    "Strangely" are not. Multi-word names (Carlos Perez Shuma, Pichis Valley):
    runs of two or three capitalized words recurring min_count times, at least
    once mid-sentence, so a full name gets one rendering too."""
    counts, lower, inner, forms, examples = {}, set(), {}, {}, {}
    phrases, phrase_inner, phrase_examples = {}, set(), {}
    for text in texts:
        plain = re.sub(r"<[^>]+>", "", text)
        for sentence in re.split(r"(?<=[.!?])\s+", plain):
            for match in NAME_PHRASE.finditer(sentence):
                words = match[0].split()
                # A run at the sentence start includes its opener ("Then", "The").
                if not sentence[: match.start()].strip():
                    words = words[1:]
                # Captions and credits capitalize prepositions ("From Clark").
                while words and words[0].lower() in PHRASE_OPENERS:
                    words = words[1:]
                if not 2 <= len(words) <= 3 or any(w.isupper() for w in words):
                    continue
                phrase = re.sub(r"['’]s$", "", " ".join(words))
                phrases[phrase] = phrases.get(phrase, 0) + 1
                if match.start():
                    phrase_inner.add(phrase)
                phrase_examples.setdefault(phrase, sentence.strip()[:200])
            for position, match in enumerate(TOKEN.finditer(sentence)):
                word = re.sub(r"['’]s$", "", match[0])
                if len(word) < 3:
                    continue
                key = word.lower()
                if word[0].islower():
                    lower.add(key)
                elif position:
                    inner[key] = inner.get(key, 0) + 1
                counts[key] = counts.get(key, 0) + 1
                forms.setdefault(key, word)
                examples.setdefault(key, sentence.strip()[:200])
    terms = []
    for key, count in counts.items():
        if count < min_count:
            continue
        if key in lower:
            keep = not known(key)
        else:
            keep = inner.get(key, 0) * 2 > count if known(key) else key in inner
        if keep:
            terms.append((count, key if key in lower else forms[key]))
    terms.sort(key=lambda item: (-item[0], item[1]))
    named = sorted(
        (
            (count, phrase)
            for phrase, count in phrases.items()
            if count >= min_count and phrase in phrase_inner
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return [(word, examples[word.lower()]) for _, word in terms[:limit]] + [
        (phrase, phrase_examples[phrase]) for _, phrase in named[:phrase_limit]
    ]


WIKIPEDIA = "https://{}.wikipedia.org/w/api.php"


def wikipedia_titles(terms, source, target, cache):
    """{term: (source article title, target-language title)} for terms whose
    source-language article links to one, after Wikipedia's normalization and
    redirects. Disambiguation pages and missing articles give nothing. Only
    the terms are sent; responses are cached per batch."""
    found = {}
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(terms), 50):
        batch = terms[i : i + 50]
        params = {
            "action": "query",
            "format": "json",
            "titles": "|".join(batch),
            "prop": "langlinks|pageprops",
            "lllang": target,
            "ppprop": "disambiguation",
            "redirects": "1",
        }
        path = cache / (
            digest(json.dumps([source, params], sort_keys=True).encode()) + ".json"
        )
        if path.exists():
            answer = json.loads(path.read_text())
        else:
            request = urllib.request.Request(
                WIKIPEDIA.format(source) + "?" + urllib.parse.urlencode(params),
                headers={"User-Agent": "docomotive/1.0 (translation glossary lookup)"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                answer = json.load(response)
            path.write_text(json.dumps(answer, ensure_ascii=False))
        query = answer.get("query", {})
        forward = {}
        for kind in ("normalized", "redirects"):
            for step in query.get(kind, []):
                forward[step["from"]] = step["to"]
        pages = {
            page["title"]: page
            for page in query.get("pages", {}).values()
            if page.get("langlinks")
            and "disambiguation" not in page.get("pageprops", {})
        }
        for term in batch:
            # Wikipedia capitalizes first letters, then follows redirects.
            title = forward.get(term, term[:1].upper() + term[1:])
            title = forward.get(title, title)
            if title in pages:
                found[term] = (title, pages[title]["langlinks"][0]["*"])
    return found


def encyclopedia_spelling(term, rendering, title):
    """The rendering respelled as the encyclopedia title, or None when the title
    is a different word (another entity, or a translation rather than a
    respelling). Parenthesized qualifiers are dropped, "Surname, Name" titles
    are reordered for full names and cut to the surname for single words, and
    a common-noun rendering keeps its lowercase initial."""
    from difflib import SequenceMatcher

    title = re.sub(r"\s*\([^)]*\)", "", title).strip()
    if "," in title:
        surname, _, given = (part.strip() for part in title.partition(","))
        title = f"{given} {surname}" if " " in term.strip() else surname
    if rendering[:1].islower():
        title = title[:1].lower() + title[1:]
    if not title or title.lower() == rendering.lower():
        return None
    # Different words for one entity (Иаков/Яков, адвентизм/адвентист) fall
    # below 0.75; respellings (аяуаска/айяуаска) are above 0.9.
    if SequenceMatcher(None, title.lower(), rendering.lower()).ratio() < 0.75:
        return None
    return title


def verify_spellings(glossary, source, target, cache):
    """Respell glossary renderings that an encyclopedia spells differently;
    returns the changes {term: (old, new)}."""
    import unicodedata

    fold = lambda text: "".join(
        c
        for c in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(c)
    )
    titles = wikipedia_titles(sorted(glossary), source, target, cache)
    changes = {}
    for term, (article, title) in titles.items():
        rendering = glossary.get(term)
        # A redirect to another word (Siberian → Siberia, introns → Intron)
        # would change the word, not its spelling.
        if not isinstance(rendering, str) or fold(article) != fold(term):
            continue
        spelled = encyclopedia_spelling(term, rendering, title)
        if spelled:
            changes[term] = (rendering, spelled)
            glossary[term] = spelled
    # Derived terms (ayahuasquero from ayahuasca) have no article of their own;
    # renderings built on a respelled stem take the new stem.
    for term, (old, new) in list(changes.items()):
        old_stem, new_stem = old[:-1].lower(), new[:-1].lower()
        if len(old_stem) < 5:
            continue
        for other, rendering in glossary.items():
            if (
                other not in changes
                and isinstance(rendering, str)
                and rendering.lower().startswith(old_stem)
            ):
                respelled = new_stem + rendering[len(old_stem) :]
                if rendering[:1].isupper():
                    respelled = respelled[:1].upper() + respelled[1:]
                changes[other] = (rendering, respelled)
                glossary[other] = respelled
    return changes


def spelling_dictionary(language):
    """Lowercase-word lookup from the Hunspell dictionary scripts/bootstrap.py
    installs; without one for the language, every word counts as unknown."""
    path = ROOT / "work/models" / {"en": "en_US"}.get(language, language)
    if not path.with_suffix(".dic").exists():
        print(
            f"No spelling dictionary at {path}.dic; glossary keeps common words",
            file=sys.stderr,
        )
        return lambda word: False
    from spylls.hunspell import Dictionary

    dictionary = Dictionary.from_files(str(path))
    return lambda word: dictionary.lookup(word)


def timing_summary(timings):
    """Totals of Ollama's per-request counters: where the model's time went."""
    total = lambda name: sum(t.get(name, 0) for t in timings)
    seconds = lambda name: round(total(name) / 1e9, 1)
    prefill, decode = total("prompt_eval_duration"), total("eval_duration")
    return {
        "requests": len(timings),
        "prompt_tokens": total("prompt_eval_count"),
        "prompt_seconds": seconds("prompt_eval_duration"),
        "output_tokens": total("eval_count"),
        "output_seconds": seconds("eval_duration"),
        "load_seconds": seconds("load_duration"),
        "total_seconds": seconds("total_duration"),
        "prefill_share": round(prefill / max(1, prefill + decode), 3),
    }


def has_letters(text):
    return any(ch.isalpha() for ch in text)


def local(node):
    return etree.QName(node).localname if isinstance(node.tag, str) else None


def atomic(node):
    """Kept verbatim: comments, page breaks, <br/>, note references like <a><sup>3</sup></a>."""
    return not isinstance(node.tag, str) or not has_letters("".join(node.itertext()))


def linked(node):
    """Link targets and sources must survive even when the model drops the markup."""
    return atomic(node) or node.get("href") is not None or node.get("id") is not None


FLOAT = ""  # private-use marker for an element's position before translation


def glued(child, out):
    """An element inside a word (faux small caps: F<span>OREST</span>) splits it
    for the model; such elements are unwrapped and the word translated whole."""
    before = "".join(out)[-1:]
    inner = "".join(child.itertext())
    return bool(inner) and (
        (before.isalpha() and inner[0].isalpha())
        or (inner[-1].isalpha() and (child.tail or "")[:1].isalpha())
    )


def faux_small_caps(block):
    """(tag, attributes) of the span in headings set as F<span>OREST</span>: a
    capital outside, the rest of the word in a smaller-type uppercase span,
    across every word. glued() unwraps those spans for translation; the
    translated words are re-set the same way by small_caps()."""
    words = re.findall(r"[^\W\d_]+", "".join(block.itertext()))
    spans = [
        e
        for e in block.iter()
        if e is not block
        and not atomic(e)
        and (e.text or "").isupper()
        and not len(e)
        and re.search(r"(?<![^\W\d_])[^\W\d_]$", previous_text(e))
    ]
    if not spans or not words or any(not w.isupper() for w in words):
        return None
    if len({(local(e), tuple(sorted(e.attrib.items()))) for e in spans}) != 1:
        return None
    return spans[0].tag, dict(spans[0].attrib)


def previous_text(element):
    before = element.getprevious()
    return (before.tail if before is not None else element.getparent().text) or ""


def small_caps(block, style):
    """Re-set a translated plain-text heading as capital + uppercase span per word."""
    if len(block) or not block.text:
        return
    tag, attributes = style
    text, block.text = block.text, ""
    last = None
    for piece in re.split(r"([^\W\d_]+)", text):
        if not piece:
            continue
        if re.fullmatch(r"[^\W\d_]+", piece) and len(piece) > 1:
            head = piece[0].upper()
            if last is None:
                block.text += head
            else:
                last.tail = (last.tail or "") + head
            last = etree.SubElement(block, tag, attributes)
            last.text = piece[1:].upper()
        elif last is None:
            block.text += piece
        else:
            last.tail = (last.tail or "") + piece
    block.text = block.text or None


def escape(text):
    """Collapse source line wrapping; only <br/> may become a newline."""
    return html.escape(re.sub(r"\s+", " ", text or ""), quote=False)


def encode(block, flatten=False, strip=False):
    """Block content as placeholder text, plus slots, <br/>s and floating elements.

    TranslateGemma keeps paired tags but drops empty ones, so only content-bearing
    elements become placeholders (<x1>3</x1> for a note link). <br/> becomes a
    newline; empty elements such as page breaks float out and are reinserted at
    the same relative text position, as are note links the model drops. strip
    omits even note-link placeholders.
    """
    slots, breaks, anchored = [], [], []

    def walk(node):
        out = [escape(node.text)]
        for child in node:
            if local(child) == "br":
                breaks.append(child)
                out.append("\n")
            elif atomic(child) and not "".join(child.itertext()).strip():
                anchored.append(child)
                out.append(FLOAT)
            elif strip and linked(child):
                slots.append(child)
                anchored.append(len(slots))
                out.append(FLOAT)
            elif (flatten or glued(child, out)) and not linked(child):
                out.append(walk(child))
            else:
                slots.append(child)
                n = len(slots)
                inner = (
                    html.escape("".join(child.itertext()), quote=False)
                    if atomic(child)
                    else walk(child)
                )
                out.append(f"<x{n}>{inner}</x{n}>")
                if linked(child):
                    anchored.append(n)
                    out.append(FLOAT)
            out.append(escape(child.tail))
        return "".join(out)

    text = walk(block)
    visible = re.sub(r"<[^>]+>", "", text)
    length = max(1, len(visible) - len(anchored))
    # Each marker's offset excludes the markers before it.
    ratios = [
        (m.start() - i) / length for i, m in enumerate(re.finditer(FLOAT, visible))
    ]
    # Anchors map a slot number or a floating element to its relative position.
    return text.replace(FLOAT, ""), slots, breaks, list(zip(anchored, ratios))


def decode(text, block, slots, breaks, anchors, strict=True):
    """Rebuild block content from translated placeholder text.

    Dropped inline elements with words (emphasis) raise. strict also raises
    when line breaks don't match; otherwise they become spaces.
    """
    # The model sometimes emits bare ampersands; anything else malformed is rejected.
    text = re.sub(r"&(?!#?\w+;)", "&amp;", text)
    if text.count("\n") != len(breaks):
        if strict:
            raise ValueError("line breaks changed")
        text, breaks = re.sub(r"\s*\n\s*", " ", text), []
    root = etree.fromstring(f"<r>{text}</r>")
    seen, pending = [], iter(breaks)
    holder = etree.Element("holder")

    def add_text(parent, value):
        lines = (value or "").split("\n")
        if len(parent):
            parent[-1].tail = lines[0]
        else:
            parent.text = lines[0]
        for line in lines[1:]:
            br = copy.deepcopy(next(pending))
            br.tail = line
            parent.append(br)

    def build(src, dst):
        add_text(dst, src.text)
        for child in src:
            match = re.fullmatch(
                r"x(\d+)", child.tag if isinstance(child.tag, str) else ""
            )
            if not match or not 0 < int(match[1]) <= len(slots):
                raise ValueError(f"unknown placeholder {child.tag!r}")
            seen.append(int(match[1]))
            original = slots[int(match[1]) - 1]
            if atomic(original):
                new = copy.deepcopy(original)
                new.tail = None
                dst.append(new)
            else:
                new = etree.Element(original.tag, dict(original.attrib))
                dst.append(new)
                build(child, new)
            add_text(dst, child.tail)

    build(root, holder)
    if len(seen) != len(set(seen)):
        raise ValueError("placeholders duplicated")
    kept = {a for a, _ in anchors if isinstance(a, int)}
    if any(i not in seen and i not in kept for i in range(1, len(slots) + 1)):
        raise ValueError("inline element dropped")
    replace_content(block, holder.text, list(holder))
    for anchor, ratio in anchors:
        if isinstance(anchor, int):
            if anchor in seen:
                continue
            anchor = slots[anchor - 1]
        insert_at(block, copy.deepcopy(anchor), ratio)


def insert_at(block, element, ratio):
    """Insert element at a top-level word boundary nearest ratio of the visible text."""
    element.tail = None
    target = ratio * len("".join(block.itertext()))
    offset = 0
    segments = [(None, block.text or "")] + [(c, c.tail or "") for c in block]
    for i, (owner, text) in enumerate(segments):
        inner = len("".join(owner.itertext())) if owner is not None else 0
        offset += inner
        last = i == len(segments) - 1
        if target <= offset + len(text) or last:
            local_offset = max(0, int(target - offset))
            m = re.compile(r"\s").search(text, local_offset)
            cut = m.start() if m else len(text)
            element.tail = text[cut:]
            if owner is None:
                block.text = text[:cut]
                block.insert(0, element)
            else:
                owner.tail = text[:cut]
                owner.addnext(element)
            return
        offset += len(text)


def replace_content(block, text, children):
    for child in list(block):
        block.remove(child)
    block.text = text
    for child in children:
        block.append(child)


# Bibliographic apparatus is kept in the source language: translated titles,
# publishers and "Ibid." would no longer identify the cited work.
STRONG_CITATION = re.compile(
    r"\bpp?\.\s*[\dxivlc]|\b(?:vols?|eds?|trans|repr|rev|chap|ch|fol|nos?)\.\s"
    r"|\b(?:ibid|op\.\s*cit|loc\.\s*cit|et\s+al)\b|\b(?:press|university|verlag)\b"
    r"|\(\s*(?:1[5-9]|20)\d\d[a-z]?\s*[,)]|[A-Z][a-z]+:\s+[A-Z][\w&]+.*\d{4}"
    r"|,\s*\d+(?:[–-]\d+)?\.?$|\b[\dIVXLC]+:\d+\b|https?://|\bdoi\b",
    re.I,
)
YEAR = re.compile(r"\b(?:1[5-9]|20)\d\d[a-z]?\b")
# "SMITH, J. R. (1990)." or "————." (same author as above) open a bibliography entry.
ENTRY = re.compile(
    r"^(?:[—–-]{2,}|[^\W\d_][\w’'-]+,(?:\s+[A-Z]\.)+[\s,&]|.{0,120}\(\d{4}[a-z]?\)\.)"
)
# Pronouns and finite verbs: frequent in commentary, rare in titles.
PROSE = set("""is was are were be been being that which who he she it they we i you his
    her its their our not but had has have would could should may might will does
    did this these those there""".split())
ABBREVIATIONS = set(
    """vol vols p pp ed eds trans no nos cf e.g i.e fol ch chap n nn col repr rev
    ff al op cit loc mr mrs ms dr st jr sr""".split()
)


def words(text):
    return re.findall(r"[^\W\d_]+", re.sub(r"<[^>]+>", "", text))


def prose_words(text):
    return sum(w.lower() in PROSE for w in words(text))


def citation_entry(text):
    """A whole note or bibliography paragraph with citation cues and no commentary."""
    text = re.sub(r"<[^>]+>", "", text).strip()
    if ENTRY.match(text):
        return True
    clauses = re.split(r"[.;:()\[\]]\s|[;()\[\]]", text)
    commentary = any(len(words(c)) >= 7 and prose_words(c) >= 2 for c in clauses)
    return bool(STRONG_CITATION.search(text) or YEAR.search(text)) and not commentary


def citation_sentence(text):
    """One sentence of a mixed note: "Smith, Title (London, 1990), 23." but not "He died in 1950." """
    plain = re.sub(r"<[^>]+>", "", text).strip()
    cue = STRONG_CITATION.search(plain) or (
        YEAR.search(plain) and plain.count(",") >= 2
    )
    return bool(cue) and prose_words(plain) < 2


def sentences(text):
    """Split at sentence ends outside placeholders, not after initials or abbreviations."""
    depth, start, out = 0, 0, []
    for m in re.finditer(r"<(/?)x\d+(/?)>|[.!?…][\"”’)\]]*\s+", text):
        if m[0].startswith("<"):
            depth += 0 if m[2] else (-1 if m[1] else 1)
            continue
        token = re.search(r"[^\s(\[“\"‘]*$", text[: m.start()])[0].lower()
        following = text[m.end() : m.end() + 1]
        if (
            depth
            or not following
            or not (following.isupper() or following in '“"‘([<—')
        ):
            continue
        if (len(token) == 1 and token.isalpha()) or token in ABBREVIATIONS:
            continue
        out.append(text[start : m.end()])
        start = m.end()
    return out + [text[start:]] if start < len(text) else out


def chunks(text, limit=MAX_CHUNK, keep=None):
    """(kept, piece) pairs: sentences matching keep stay verbatim, the rest are
    packed into pieces up to limit so the model sees neighbouring context."""
    if len(text) <= limit and keep is None:
        return [(False, text)]
    pieces = []
    for sentence in sentences(text):
        kept = keep is not None and (not has_letters(sentence) or keep(sentence))
        if pieces and not kept and not pieces[-1][0]:
            if len(pieces[-1][1]) + len(sentence) <= limit:
                pieces[-1][1] += sentence
                continue
        pieces.append([kept, sentence])
    return [tuple(p) for p in pieces]


def translate_text(text, translate, source, target, retry=False, keep=None):
    out = []
    limit = getattr(translate, "chunk_chars", MAX_CHUNK)
    for kept, piece in chunks(text, limit, keep):
        if kept:
            out.append(piece.strip())
            continue
        result = translate(piece.strip(), source, target, retry)
        # Models told about paragraph tags sometimes add one to a lone paragraph.
        if "<seg" not in piece:
            result = re.sub(r"^\s*<seg1>(.*)</seg1>\s*$", r"\1", result, flags=re.S)
        if len(result) > 3 * len(piece) + 200:
            raise ValueError("runaway output")
        out.append(result)
    return " ".join(out)


def translate_block(block, translate, source, target, keep=None):
    """Return the fallback level used: 0 markup, 1 markup retry, 2 no emphasis, 3 plain."""
    attempts = [(False, False, False), (False, False, True), (True, False, False)]
    attempts.append((True, True, False))
    for level, (flatten, strip, retry) in enumerate(attempts):
        text, slots, breaks, anchors = encode(block, flatten, strip)
        try:
            output = translate_text(text, translate, source, target, retry, keep)
            if strip:  # no placeholders left: any markup in the output is text
                output = html.escape(html.unescape(output), quote=False)
            decode(output, block, slots, breaks, anchors, strict=not strip)
            return level
        except ValueError, etree.XMLSyntaxError:
            if strip:
                raise


def segments(output, count):
    """Inner placeholder text of <seg1>...<segN>, in order; raise if any is missing."""
    output = re.sub(r"&(?!#?\w+;)", "&amp;", output)
    root = etree.fromstring(f"<r>{output}</r>")
    if [c.tag for c in root] != [f"seg{i}" for i in range(1, count + 1)]:
        raise ValueError("paragraph segments changed")
    if has_letters((root.text or "") + "".join(c.tail or "" for c in root)):
        raise ValueError("text outside paragraph segments")
    return [
        (
            html.escape(seg.text or "", quote=False)
            + "".join(etree.tostring(c, encoding="unicode") for c in seg)
        ).strip()
        for seg in root
    ]


def translate_batch(blocks, translate, source, target):
    """Translate consecutive blocks in one request so the model sees them together;
    blocks whose segment does not decode are retried one by one."""
    if len(blocks) == 1:
        return [translate_block(blocks[0], translate, source, target)]
    encoded = [encode(b) for b in blocks]
    text = "\n\n".join(f"<seg{i}>{e[0]}</seg{i}>" for i, e in enumerate(encoded, 1))
    try:
        output = translate(text, source, target)
        if len(output) > 3 * len(text) + 200:
            raise ValueError("runaway output")
        parts = segments(output, len(blocks))
    except ValueError, etree.XMLSyntaxError:
        parts = [None] * len(blocks)
    levels = []
    for block, (_, slots, breaks, anchors), part in zip(blocks, encoded, parts):
        try:
            if part is None:
                raise ValueError("no segment")
            decode(part, block, slots, breaks, anchors)
            levels.append(0)
        except ValueError, etree.XMLSyntaxError:
            levels.append(translate_block(block, translate, source, target))
    return levels


ENGLISH_FUNCTION_WORDS = frozenset(
    "the and of to was that with which were had his her their this from".split()
)
SENTENCE_END = re.compile(r"[.!?…][\"”’»)\]]*(?=\s|$)")


def plain(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def quality_flags(source, target, language, narrator=None, min_ratio=0.8):
    """Signs that a translated paragraph lost or garbled text, from plain source
    and target strings. Ratios use characters; Russian runs about 1.0-1.1 times
    English, and evaluated good output never fell below 0.82, so min_ratio 0.8
    flags truncation and heavy compression. Sentence counts may legitimately
    differ, so a drop counts only together with a short ratio."""
    source, target = plain(source), plain(target)
    flags = []
    ratio = len(target) / max(1, len(source))
    if len(source) >= 80:
        if ratio < min_ratio:
            flags.append("short")
        elif ratio > 1.8:
            flags.append("long")
        if (
            len(SENTENCE_END.findall(target)) < len(SENTENCE_END.findall(source))
            and ratio < 0.9
        ):
            flags.append("lost_sentence")
    words = re.findall(r"[^\W\d_]+", target)
    if language != "en":
        english = [w for w in words if w.lower() in ENGLISH_FUNCTION_WORDS]
        if len(english) >= 3:
            flags.append("untranslated")
        latin_script = language in ("en", "de", "fr", "es", "it", "pt", "pl", "nl")
        if not latin_script and any(
            re.search(r"[A-Za-z]", w) and re.search(r"[^\x00-ɏ]", w) for w in words
        ):
            flags.append("mixed_script")
    if language == "ru" and narrator:
        # Past tense after "я": -л/-лся masculine, -ла/-лась feminine.
        wrong = r"(?:ла|лась)" if narrator == "male" else r"(?:л|лся)"
        if re.search(rf"(?i)\bя\s+(?:не\s+)?(?:\w+\s+)?\w+{wrong}\b", target):
            flags.append("narrator_gender")
    return flags


def term_flags(source, target, glossary):
    """["terminology"] when a glossary term in source appears in target under a
    spelling other than its glossary rendering (аяуаска vs айяуаска). A variant
    starts with the rendering's first letter, is similar to it (difflib ratio
    0.7) and differs within the first four letters, so inflection (Египет,
    Египта), other glossary names (ашанинка, манинкари) and compounds
    (биофотон, фотоны) do not count. Renderings under six letters are skipped."""
    from difflib import SequenceMatcher

    words = [w.lower() for w in re.findall(r"[^\W\d_]+", plain(target))]
    for term, rendering in glossary.items():
        rendering = rendering if isinstance(rendering, str) else rendering["rendering"]
        tokens = re.findall(r"[^\W\d_]+", rendering.lower())
        stem = tokens[0] if tokens else ""
        if len(stem) < 6 or not re.search(rf"(?<!\w){re.escape(term)}", source, re.I):
            continue
        for word in words:
            if (
                word[0] == stem[0]
                and word[:4] != stem[:4]
                and SequenceMatcher(None, word[: len(stem)], stem).ratio() >= 0.7
            ):
                return ["terminology"]
    return []


TYPESET_LANGUAGES = {"ru", "uk"}
NBSP = " "


def note_reference(node):
    """A link whose visible text is only a number (with optional brackets)."""
    return (
        local(node) in ("a", "sup")
        and re.fullmatch(r"[\[(]?\d+[\])]?", "".join(node.itertext()).strip())
        is not None
    )


def typeset(block, language):
    """Russian/Ukrainian typography on a translated block's text nodes: «» quotes
    (nested „“), spaced em dashes with a non-breaking space before, non-breaking
    spaces after initials, and no space between sentence punctuation and a note
    reference. Element content of note links is never touched."""
    if language not in TYPESET_LANGUAGES:
        return
    segments = []  # (node, attribute) in reading order

    def walk(node):
        if node is not block and atomic(node):
            segments.append((node, "tail"))
            return
        segments.append((node, "text"))
        for child in node:
            walk(child)
        if node is not block:
            segments.append((node, "tail"))

    walk(block)
    depth = 0
    previous = " "
    for node, attribute in segments:
        text = getattr(node, attribute)
        if not text:
            continue
        out = []
        for char in text:
            if char in '"“”„':
                opening = char in "“„" or (
                    char == '"' and (previous.isspace() or previous in "([{—–-")
                )
                if char == "”":
                    opening = False
                if opening:
                    out.append("«" if depth == 0 else "„")
                    depth += 1
                else:
                    depth = max(0, depth - 1)
                    out.append("»" if depth == 0 else "“")
            else:
                out.append(char)
            previous = char
        text = "".join(out)
        text = re.sub(r"(?<=\S)[  ]+[-–—][  ]+", NBSP + "— ", text)
        text = re.sub(r"\b([А-ЯЁЄІЇ]\.)[ ]+(?=[А-ЯЁЄІЇ])", r"\1" + NBSP, text)
        setattr(node, attribute, text)
    for child in block.iter():
        if child is block or not note_reference(child):
            continue
        before = child.getprevious()
        owner, attribute = (
            (before, "tail")
            if before is not None
            else (
                child.getparent(),
                "text",
            )
        )
        text = getattr(owner, attribute) or ""
        if re.search(r"[.,;:!?»”)\]][  ]+$", text):
            setattr(owner, attribute, text.rstrip(" " + NBSP))


def split_sentences(blocks):
    """Runs of three or more consecutive same-tag, same-class blocks of at most
    three words each, without links, that together form one sentence ending
    in the run's last block: a phrase set one word per line (an epigraph).
    Translated block by block it comes out word for word."""
    runs, current = [], []
    for block in blocks:
        text = "".join(block.itertext()).strip()
        tiny = 0 < len(text.split()) <= 3 and not any(
            linked(child) for child in block.iter() if child is not block
        )
        same = current and (
            local(current[-1]) == local(block)
            and current[-1].get("class") == block.get("class")
        )
        if not tiny:
            current = []
            continue
        current = current + [block] if same else [block]
        if re.search(r"[.!?…][\"”’»)]*$", text):
            if len(current) >= 3:
                runs.append(current)
            current = []
    return runs


def spread(words, count):
    """Split words over count lines as evenly as possible, earlier lines first."""
    size, extra = divmod(len(words), count)
    lines, start = [], 0
    for i in range(count):
        end = start + size + (1 if i < extra else 0)
        lines.append(" ".join(words[start:end]))
        start = end
    return lines


def apparatus(block):
    """ "index", "reference" (notes, bibliographies, epigraph sources) or None."""
    for node in block.iterancestors():
        if local(node) == "div" and "index" in (node.get("class") or "").split():
            return "index"
    for node in [block, *block.iterancestors()]:
        kind = node.get("{http://www.idpf.org/2007/ops}type") or ""
        classes = (node.get("class") or "").split()
        if local(node) == "aside" or {"endnote", "footnote", "bibliography"} & set(
            kind.split()
        ):
            return "reference"
        if {"reference", "attribution"} & set(classes):
            return "reference"
    return None


HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6", "title", "text"}


def apparatus_document(blocks, share=0.25):
    """EPUBs from other tools carry no note/bibliography markup; a document whose
    body is largely citation entries is notes, bibliography or index. Body
    chapters measured at most 12% (epigraph sources, quoted titles); notes
    sections with commentary about 35%. Short documents (acknowledgments) are
    too small a sample."""
    body = [b for b in blocks if local(b) not in HEADINGS]
    entries = sum(citation_entry(encode(b)[0]) for b in body)
    return len(body) >= 10 and entries >= share * len(body)


def bibliography(blocks, share=0.8, whole=False):
    """Lists (not notes) that are mostly citation entries are kept whole, so
    entries the source splits across paragraphs stay together. whole considers
    every body block of an unmarked apparatus document."""
    listed = [
        b
        for b in blocks
        if (local(b) not in HEADINGS if whole else apparatus(b) == "reference")
        and not any(local(a) == "aside" for a in b.iterancestors())
    ]
    entries = sum(citation_entry(encode(b)[0]) for b in listed)
    return set(listed) if len(listed) >= 5 and entries >= share * len(listed) else set()


def leaf_blocks(root):
    def walk(node):
        name = local(node)
        if name in SKIP:
            return
        if name in BLOCKS and not any(
            local(d) in BLOCKS for d in node.iterdescendants()
        ):
            # A TOC entry is all link: translate inside it so the link can't be dropped.
            while (
                len(node) == 1
                and not (node.text or "").strip()
                and not (node[0].tail or "").strip()
                and not atomic(node[0])
            ):
                node = node[0]
            if has_letters("".join(node.itertext())):
                yield node
            return
        for child in node:
            yield from walk(child)

    # <head><title> is the one head element worth translating.
    titles = [
        t for t in root.iter() if local(t) == "title" and local(t.getparent()) == "head"
    ]
    return titles + list(walk(root))


def translate_epub(
    epub,
    out,
    translate,
    target,
    progress=True,
    prepare=None,
    checks=None,
    validate=True,
    review=None,
    corrections=None,
    judge=None,
    judge_share=0.05,
    quotes=None,
):
    """prepare, if given, receives the source text of every prose block before
    translation starts (used to build the glossary). checks(source, target)
    returns quality flags for a translated prose block; a flagged block is
    retried alone at the retry temperature and the attempt with fewer flags is
    kept. review(source, draft, source_language, target_language), if given,
    post-edits prose blocks still flagged or that lost inline markup; an edit
    is kept only if it decodes with the source's markup and clears flags (or,
    for an unflagged block, restores markup without raising any). corrections maps "<document>#<block index>" to {source_sha,
    translation}: reviewed replacements applied instead of the model; a
    correction whose source text changed stops the run. judge(pairs), if given,
    scores every translated prose block after translation; with review, the
    lowest-scoring judge_share are post-edited and an edit is kept only if the
    judge scores it higher. quotes(quotation, author), if given, returns an
    established translation of an attributed quotation (an epigraph set one
    word per line, or a block followed by an attribution) or None. The
    report's "pairs" lists every block's source and result for the review
    sheet."""
    with zipfile.ZipFile(epub) as z:
        files = {n: z.read(n) for n in z.namelist() if n != "mimetype"}
    opf_name = next(n for n in files if n.endswith(".opf"))
    opf = etree.fromstring(files[opf_name])
    language = opf.find(f".//{DC}language")
    source = (language.text or "en").split("-")[0]
    for code in (source, target):
        if code not in LANGS:
            raise SystemExit(f"Unsupported language {code!r}; add it to LANGS")
    # Content documents come from the manifest: Calibre and others use .html names.
    base = str(Path(opf_name).parent)
    documents = {
        n: etree.fromstring(files[n])
        for n in sorted(
            posixpath.normpath(posixpath.join(base, unquote(item.get("href"))))
            for item in opf.iter("{http://www.idpf.org/2007/opf}item")
            if item.get("media-type")
            in ("application/xhtml+xml", "application/x-dtbncx+xml")
        )
        if n in files
    }
    work, listed, inferred = [], set(), set()
    for name, root in documents.items():
        blocks = leaf_blocks(root)
        work += [(name, b) for b in blocks]
        whole = not any(apparatus(b) for b in blocks) and apparatus_document(blocks)
        if whole:
            inferred |= {b for b in blocks if local(b) not in HEADINGS}
        listed |= bibliography(blocks, whole=whole)
    levels, failures, kept = [0] * 4, [], {"index": 0, "citation": 0}
    flagged, reviews = [], []
    originals, scores, judged = {}, {}, []  # judge pass
    joined_phrases, established = [], []
    sources = {}  # block -> untranslated copy, for checks and retries

    lock = threading.RLock()  # shared results, when documents run in parallel

    def record(name, block, level, translator):
        def warmer(text, source_language, target_language, retry=False):
            return translator(text, source_language, target_language, True)

        warmer.chunk_chars = getattr(translator, "chunk_chars", MAX_CHUNK)
        with lock:
            original = sources.pop(block, None)
        text = lambda node: "".join(node.itertext())
        found = []
        if checks and original is not None:
            found = checks(text(original), text(block))
            if found:
                retry = copy.deepcopy(original)
                retry_level = translate_block(retry, warmer, source, target)
                again = checks(text(original), text(retry))
                if len(again) < len(found):
                    replace_content(block, retry.text, list(retry))
                    found, level = again, retry_level
        entry = None
        if review and original is not None and (found or level >= 2):
            candidate = copy.deepcopy(original)
            encoded, slots, breaks, anchors = encode(candidate)
            entry = {
                "file": name,
                "id": block.get("id"),
                "flags": found,
                "level": level,
            }
            try:
                output = review(encoded, plain(text(block)), source, target)
                decode(output, candidate, slots, breaks, anchors)
                after = checks(text(original), text(candidate)) if checks else []
                # Fewer flags, or markup restored without new flags; an edit with
                # the same flags would only swap one imperfect output for another.
                entry["accepted"] = len(after) < len(found) or (not found and not after)
            except ValueError, etree.XMLSyntaxError:
                entry["accepted"] = False
            if entry["accepted"]:
                replace_content(block, candidate.text, list(candidate))
                found, level = after, 0
        if contexts.get(block) is None:
            typeset(block, target)
        if block in capitals:
            small_caps(block, capitals[block])
        with lock:
            if entry:
                reviews.append(entry)
            if found:
                flagged.append({"file": name, "id": block.get("id"), "flags": found})
            block_level[block], block_flags[block] = level, found
            levels[level] += 1
            if level:
                failures.append({"file": name, "id": block.get("id"), "level": level})

    positions, encoded_source = {}, {}
    for name, root in documents.items():
        for index, block in enumerate(b for n, b in work if n == name):
            positions[block] = f"{name}#{index}"
            encoded_source[block] = encode(block)[0]
    corrections = corrections or {}
    applied, block_level, block_flags = [], {}, {}
    count = getattr(translate, "example_count", 0)
    if count and hasattr(translate, "examples"):
        # The longest corrected prose blocks teach register and wording best.
        approved = sorted(
            (
                (encoded_source[block], corrections[positions[block]]["translation"])
                for _, block in work
                if positions[block] in corrections
                and apparatus(block) is None
                and len(encoded_source[block]) >= 200
            ),
            key=lambda pair: -len(pair[0]),
        )
        translate.examples = approved[:count]
    phrases = {}  # first block of a word-per-line sentence -> all its blocks
    authors = {}  # first block of an attributed quotation -> author's name
    for name, root in documents.items():
        blocks = [b for n, b in work if n == name and local(b) != "title"]
        following = dict(zip(blocks, blocks[1:]))
        for run_ in split_sentences(blocks):
            phrases[run_[0]] = run_
            after = following.get(run_[-1])
            credit = (
                "".join(after.itertext()).strip(" —–-") if after is not None else ""
            )
            if 0 < len(credit.split()) <= 4:
                authors[run_[0]] = credit.title() if credit.isupper() else credit
        for block, after in following.items():
            if "attribution" in (after.get("class") or "").split():
                credit = "".join(after.itertext()).strip(" —–-")
                if 0 < len(credit.split()) <= 6:
                    authors.setdefault(block, credit)
    in_phrase = {b for run_ in phrases.values() for b in run_[1:]}
    contexts = {}
    capitals = {b: style for _, b in work if (style := faux_small_caps(b))}
    for name, block in work:
        context = apparatus(block) or ("reference" if block in inferred else None)
        if context == "reference" and (
            block in listed or citation_entry(encode(block)[0])
        ):
            context = "citation"
        contexts[block] = context
    if prepare:
        prepare([encode(b)[0] for _, b in work if contexts[b] is None], source)

    def run(items, translator, bar):
        """Translate items (one document, or the whole book when serial) in
        order with one translator, whose history carries context between them."""
        batching = getattr(translator, "batch", False)
        limit = getattr(translator, "chunk_chars", MAX_CHUNK)
        pending = []  # consecutive blocks of one document and context

        def flush():
            blocks = [b for _, b in pending]
            for (name, block), level in zip(
                pending, translate_batch(blocks, translator, source, target)
            ):
                record(name, block, level, translator)
            bar.update(len(pending))
            pending.clear()

        for name, block in items:
            context = contexts[block]
            if block in in_phrase:
                bar.update(1)
                continue
            if block in phrases and context is None:
                if pending:
                    flush()
                blocks = phrases[block]
                texts = ["".join(b.itertext()).strip() for b in blocks]
                joined = " ".join(texts)
                shouting = joined.isupper()
                if shouting:  # models translate sentence case more naturally
                    joined = joined[:1] + joined[1:].lower()
                known = (
                    quotes(joined, authors[block])
                    if quotes and block in authors
                    else None
                )
                if known:
                    result = known
                    with lock:
                        established.append(positions[block])
                else:
                    result = translate_text(
                        html.escape(joined, quote=False), translator, source, target
                    )
                    result = plain(html.unescape(result))
                if shouting:
                    result = result.upper()
                for b, line in zip(blocks, spread(result.split(), len(blocks))):
                    replace_content(b, line or None, [])
                with lock:
                    joined_phrases.append(positions[block])
                    for b in blocks:
                        levels[0] += 1
                        block_level[b] = 0
                bar.update(1)
                continue
            if context in ("index", "citation"):
                # The index is alphabetized and paged for the source edition.
                with lock:
                    kept[context] += 1
                block.set("lang", source)
                block.set(XML_LANG, source)
                bar.update(1)
                continue
            if (
                quotes
                and context is None
                and block in authors
                and not any(linked(c) for c in block.iter() if c is not block)
            ):
                known = quotes(plain("".join(block.itertext())), authors[block])
                if known:
                    if pending:
                        flush()
                    replace_content(block, known, [])
                    typeset(block, target)
                    with lock:
                        established.append(positions[block])
                        levels[0] += 1
                        block_level[block] = 0
                    bar.update(1)
                    continue
            key = positions[block]
            if key in corrections:
                fix = corrections[key]
                if fix.get("source_sha") != digest(encoded_source[block].encode()):
                    raise SystemExit(
                        f"Correction {key} no longer matches its source text"
                    )
                if pending:
                    flush()
                _, slots, breaks, anchors = encode(block)
                decode(fix["translation"], block, slots, breaks, anchors)
                if context is None:
                    typeset(block, target)
                with lock:
                    applied.append(key)
                    levels[0] += 1
                    block_level[block] = 0
                bar.update(1)
                continue
            if (checks or review or judge) and context is None:
                with lock:
                    sources[block] = copy.deepcopy(block)
                    if judge:
                        originals[block] = sources[block]
            text = encode(block)[0]
            size = len(text)
            # Notes with no citation sentence batch like prose; mixed notes keep
            # the sentence-level path so their citations stay verbatim.
            commentary = context == "reference" and not any(
                kept and has_letters(piece)
                for kept, piece in chunks(text, limit, citation_sentence)
            )
            if batching and (context is None or commentary) and size < limit:
                batched = sum(len(encode(b)[0]) for _, b in pending)
                if pending and (
                    pending[0][0] != name
                    or contexts[pending[0][1]] != context
                    or batched + size > limit
                ):
                    flush()
                pending.append((name, block))
                continue
            if pending:
                flush()
            keep = citation_sentence if context == "reference" else None
            level = translate_block(block, translator, source, target, keep)
            record(name, block, level, translator)
            bar.update(1)
        if pending:
            flush()

    parallel = getattr(translate, "parallel", 1)
    with tqdm(
        total=len(work), unit="block", desc=f"{source}→{target}", disable=not progress
    ) as bar:
        if parallel > 1 and hasattr(translate, "fork"):
            # Documents are independent: each gets its own translator history
            # (context restarts per document); the cache and glossary are shared.
            groups = [[(n, b) for n, b in work if n == name] for name in documents]
            with ThreadPoolExecutor(parallel) as pool:
                for done in [
                    pool.submit(run, group, translate.fork(), bar)
                    for group in groups
                    if group
                ]:
                    done.result()
        else:
            run(work, translate, bar)
    if judge:
        text = lambda node: "".join(node.itertext())
        candidates = [
            block
            for _, block in work
            if block in originals and positions[block] not in applied
        ]
        batches, batch, size = [], [], 0
        for block in candidates:
            length = len(text(block))
            if batch and (size + length > 4000 or len(batch) >= 12):
                batches.append(batch)
                batch, size = [], 0
            batch.append(block)
            size += length
        if batch:
            batches.append(batch)
        for batch in tqdm(batches, unit="batch", desc="judge", disable=not progress):
            results = judge(
                [(plain(text(originals[b])), plain(text(b))) for b in batch]
            )
            for block, (score, errors) in zip(batch, results):
                if score is not None:
                    scores[block] = (score, errors)
        ranked = sorted(scores, key=lambda b: scores[b][0])
        worst = ranked[: max(1, int(len(ranked) * judge_share))] if ranked else []
        for block in worst if review else []:
            score, errors = scores[block]
            original = originals[block]
            candidate = copy.deepcopy(original)
            encoded, slots, breaks, anchors = encode(candidate)
            entry = {"key": positions[block], "score": score, "errors": errors}
            try:
                output = review(encoded, plain(text(block)), source, target)
                decode(output, candidate, slots, breaks, anchors)
                new = judge([(plain(text(original)), plain(text(candidate)))])[0][0]
                before = checks(text(original), text(block)) if checks else []
                after = checks(text(original), text(candidate)) if checks else []
                entry["new_score"] = new
                entry["accepted"] = (
                    new is not None and new > score and len(after) <= len(before)
                )
            except ValueError, etree.XMLSyntaxError:
                entry["accepted"] = False
            if entry["accepted"]:
                replace_content(block, candidate.text, list(candidate))
                typeset(block, target)
                scores[block] = (entry["new_score"], [])
            judged.append(entry)
    for name, root in documents.items():
        for attr in ("lang", XML_LANG):
            if root.get(attr):
                root.set(attr, target)
        files[name] = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    language.text = target
    title = opf.find(f".//{DC}title")
    if title is not None and has_letters(title.text or ""):
        try:
            title.text = translate_text(title.text, translate, source, target)
        except ValueError:
            pass  # a runaway answer keeps the source title
    identifier = opf.find(f".//{DC}identifier")
    identifier.text += f"-{target}"
    files[opf_name] = etree.tostring(opf, xml_declaration=True, encoding="utf-8")
    write_epub(out, files)
    if validate:  # evaluation samples cut note links, so they skip this
        validate_epub(out)
    return {
        "source": str(epub),
        "output": str(out),
        "languages": [source, target],
        "blocks": len(work),
        "kept_in_source_language": kept,
        "fallback_levels": dict(
            zip(["markup", "markup_retry", "no_emphasis", "plain"], levels)
        ),
        "fallback_blocks": failures,
        "flagged_blocks": flagged,
        "reviewed_blocks": reviews,
        "corrections_applied": applied,
        "joined_phrases": joined_phrases,
        "established_quotations": established,
        "judge": {
            "scored": len(scores),
            "mean_score": (
                round(sum(v[0] for v in scores.values()) / len(scores), 1)
                if scores
                else None
            ),
            "below_70": sum(1 for v in scores.values() if v[0] < 70),
            "reviewed": judged,
        },
        "pairs": [
            {
                "key": positions[block],
                "context": contexts[block],
                "source": encoded_source[block],
                "source_sha": digest(encoded_source[block].encode()),
                "target": plain("".join(block.itertext())),
                "level": block_level.get(block),
                "flags": block_flags.get(block, []),
                "corrected": positions[block] in applied,
                "score": scores.get(block, (None, []))[0],
                "errors": scores.get(block, (None, []))[1],
            }
            for _, block in work
        ],
    }


def review_sheet(pairs, reviews, path, title):
    """Offline side-by-side review page: every block's source (with the
    placeholder tags a correction must reuse), its translation, and any flags,
    fallback level or reviewer decision. Flagged rows are highlighted and a
    checkbox hides the rest."""
    reviewed = {(r["file"], r.get("id")): r for r in reviews}
    rows = []
    for pair in pairs:
        notes = list(pair["flags"])
        if pair["level"]:
            notes.append(f"fallback {pair['level']}")
        if pair["corrected"]:
            notes.append("corrected")
        if pair["context"] in ("index", "citation"):
            notes.append(f"kept: {pair['context']}")
        score = pair.get("score")
        if score is not None:
            notes.append(f"score {score}")
            notes += pair.get("errors", [])
        attention = bool(
            pair["flags"] or pair["level"] or (score is not None and score < 70)
        )
        rows.append(
            f'<tr class="{"flag" if attention else "ok"}"><td class="key">'
            f'{html.escape(pair["key"])}<br><small>{pair["source_sha"][:12]}</small></td>'
            f'<td>{html.escape(pair["source"])}</td><td>{html.escape(pair["target"])}</td>'
            f'<td>{html.escape(", ".join(notes))}</td></tr>'
        )
    attention = sum(
        1
        for p in pairs
        if p["flags"] or p["level"] or (p.get("score") is not None and p["score"] < 70)
    )
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} review</title><style>
:root {{ --bg:#fff; --fg:#1c1c1c; --line:#ddd; --flag:#fff4d6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161616; --fg:#e8e8e8; --line:#333; --flag:#3a3016; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; margin:16px; }}
table {{ border-collapse:collapse; width:100%; table-layout:fixed; }}
td, th {{ border-bottom:1px solid var(--line); padding:6px 8px; vertical-align:top; overflow-wrap:anywhere; }}
th {{ text-align:left; position:sticky; top:0; background:var(--bg); }}
.key {{ width:9em; font-family:ui-monospace, monospace; font-size:12px; }}
tr.flag {{ background:var(--flag); }} body.only tr.ok {{ display:none; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p>{len(pairs)} blocks, {attention} need attention, {len(reviewed)} reviewed by a second model.
To correct a block, add to <code>.corrections.json</code>:
<code>{{"&lt;key&gt;": {{"source_sha": "&lt;full sha&gt;", "translation": "..."}}}}</code>,
reusing the source's &lt;xN&gt; tags. Full shas are in <code>.pairs.json</code>.</p>
<label><input type="checkbox" onchange="document.body.classList.toggle('only', this.checked)">
Only blocks that need attention</label>
<table><thead><tr><th class="key">Block</th><th>Source</th><th>Translation</th><th>Notes</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></body></html>"""
    write_text_atomic(path, page)


class Evaluated(Exception):
    pass


def evaluator(translate, count=1, min_chars=200):
    """Translate and print the first count prose-sized chunks, then stop the run."""
    done = []

    def call(text, source, target, retry=False):
        if len(text) < min_chars:  # titles and TOC entries say little about a model
            return text
        if done:
            print(file=sys.stderr)
        print(text, end="\n\n", file=sys.stderr, flush=True)
        output = translate(text, source, target, retry)
        print(output, flush=True)
        done.append(text)
        if len(done) >= count:
            raise Evaluated
        return output

    call.done = done
    call.chunk_chars = getattr(translate, "chunk_chars", MAX_CHUNK)
    call.batch = getattr(translate, "batch", False)
    return call


def keep_awake():
    """macOS idle-sleeps an unattended Mac mid-run, pausing the model for
    minutes at a time; caffeinate holds it awake until this process exits."""
    if shutil.which("caffeinate"):
        subprocess.Popen(["caffeinate", "-is", "-w", str(os.getpid())])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("epub", type=Path)
    parser.add_argument("--lang", default="ru", choices=sorted(LANGS))
    parser.add_argument("--model", default="hy-mt2:latest")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="file or existing directory; default: <epub stem>.<lang>.epub beside the input",
    )
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument(
        "--cache", type=Path, help="default: work/translations/<model>-<lang>.jsonl"
    )
    parser.add_argument(
        "--epubcheck",
        type=Path,
        default=ROOT / "work/tools/epubcheck-5.4.0/epubcheck.jar",
    )
    parser.add_argument(
        "--evaluate",
        nargs="?",
        const=1,
        type=int,
        metavar="N",
        help="print the first N (default 1) chunks of 200+ characters (source on "
        "stderr, translation on stdout) and exit without writing the EPUB",
    )
    parser.add_argument(
        "--narrator",
        choices=["male", "female"],
        default="male",
        help="gender of the first-person narrator, for languages that inflect it "
        "(default: male)",
    )
    parser.add_argument(
        "--review-glossary",
        action="store_true",
        help="build <output>.glossary.json and <output>.brief.json, then exit so "
        "they can be checked and edited before a full run",
    )
    parser.add_argument(
        "--no-lookup",
        action="store_true",
        help="do not check new glossary spellings against Wikipedia (sends only "
        "the glossary terms)",
    )
    parser.add_argument(
        "--no-quotes",
        action="store_true",
        help="do not look up established translations of attributed quotations "
        "on Wikiquote (sends the author's name; the quotation goes to the chooser)",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="skip scoring translated paragraphs after translation",
    )
    parser.add_argument(
        "--no-jev",
        action="store_true",
        help="judge with the local --judge-model instead of TypeSafe Jev, which "
        "sends each paragraph and its translation to TypeSafe's API",
    )
    parser.add_argument(
        "--judge-model",
        default="gemma4:26b-nvfp4",
        help="local judge when Jev is off or no TYPESAFE_API_KEY is set; with "
        "--review-model the lowest-scoring --judge-share are post-edited",
    )
    parser.add_argument("--judge-share", type=float, default=0.05)
    parser.add_argument(
        "--review-model",
        help="second model that post-edits blocks still flagged after the retry "
        "or that lost inline markup (e.g. qwen3.8:27b-nvfp4)",
    )
    a = parser.parse_args()
    keep_awake()
    out = a.epub.with_name(f"{a.epub.stem}.{a.lang}.epub")
    if a.output:
        out = a.output / out.name if a.output.is_dir() else a.output
    cache = a.cache or ROOT / "work/translations" / (
        re.sub(r"\W", "_", a.model) + f"-{a.lang}.jsonl"
    )
    config = model_config(a.model)
    translate = Translator(a.model, cache, config, a.host, narrator_note(a.narrator))
    glossary_path = out.with_suffix(".glossary.json")
    brief_path = out.with_suffix(".brief.json")

    def prepare(texts, source):
        """Reuse existing (possibly hand-edited) glossary and brief, else build
        them; the brief needs the glossary's name candidates."""
        if not config.get("glossary"):
            return
        if glossary_path.exists():
            translate.glossary = json.loads(glossary_path.read_text())
        else:
            terms = glossary_terms(texts, spelling_dictionary(source))
            print(f"Translating {len(terms)} glossary terms", file=sys.stderr)
            translate.glossary = translate.translate_terms(terms, source, a.lang)
            if not a.no_lookup:
                try:
                    changes = verify_spellings(
                        translate.glossary,
                        source,
                        a.lang,
                        ROOT / "work/translations/wikipedia",
                    )
                except OSError as error:
                    print(f"Spelling lookup skipped: {error}", file=sys.stderr)
                    changes = {}
                for term, (old, new) in changes.items():
                    print(
                        f"Glossary: {term}: {old} → {new} (Wikipedia)", file=sys.stderr
                    )
            write_json(glossary_path, translate.glossary)
        if not config.get("brief"):
            return
        if brief_path.exists():
            translate.brief = json.loads(brief_path.read_text())
            return
        print("Writing the book brief", file=sys.stderr)
        translate.brief = translate.make_brief(
            *brief_inputs(texts, translate.glossary), source
        )
        write_json(brief_path, translate.brief)

    if a.review_glossary:

        def stop(texts, source):
            prepare(texts, source)
            raise Evaluated

        with tempfile.TemporaryDirectory() as scratch:
            try:
                translate_epub(
                    a.epub, Path(scratch) / out.name, translate, a.lang, False, stop
                )
            except Evaluated:
                pass
        people = translate.brief.get("people", {})
        print(f"{len(translate.glossary)} glossary terms: {glossary_path}")
        if config.get("brief"):
            print(f"Brief with {len(people)} people: {brief_path}")
        return
    if a.evaluate:
        evaluate = evaluator(translate, a.evaluate)
        # A book with fewer chunks than N runs to the end; discard that EPUB.
        with tempfile.TemporaryDirectory() as scratch:
            try:
                translate_epub(
                    a.epub, Path(scratch) / out.name, evaluate, a.lang, False, prepare
                )
            except Evaluated:
                pass
        if not evaluate.done:
            raise SystemExit("No chunk of 200+ characters to translate")
        return
    checks = None
    if config.get("checks", True):
        min_ratio = config.get("min_length_ratio", 0.8)

        def checks(source_text, target_text):
            return quality_flags(
                source_text, target_text, a.lang, a.narrator, min_ratio
            ) + term_flags(source_text, target_text, translate.glossary)

    review = reviewer = None
    if a.review_model:
        reviewer = Translator(
            a.review_model,
            ROOT
            / "work/translations"
            / (re.sub(r"\W", "_", a.review_model) + f"-{a.lang}.jsonl"),
            model_config(a.review_model),
            a.host,
            narrator_note(a.narrator),
        )

        def review(source_text, draft, source, target):
            return reviewer.post_edit(
                source_text, draft, source, target, translate.glossary, translate.brief
            )

    judge = judger = None
    use_jev = not (a.no_judge or a.no_jev)
    if use_jev:
        from jev_rank import key

        try:
            key()
        except ValueError:
            print("No TYPESAFE_API_KEY: judging with the local model", file=sys.stderr)
            use_jev = False
    if use_jev:
        import jev_judge

        jev_cache = ROOT / "work/translations/jev-judge"
        judge = lambda pairs: jev_judge.judge_pairs(
            pairs, translate.glossary, jev_cache
        )
    elif not a.no_judge:
        judger = Translator(
            a.judge_model,
            ROOT
            / "work/translations"
            / (re.sub(r"\W", "_", a.judge_model) + f"-judge-{a.lang}.jsonl"),
            model_config(a.judge_model),
            a.host,
        )
        judge = lambda pairs: judger.judge_batch(pairs, "en", a.lang)
    quotes = None
    if not (a.no_quotes or a.no_lookup):
        import quotations

        quote_cache = ROOT / "work/translations/quotations"
        if use_jev:
            choose = quotations.jev_chooser(quote_cache / "jev")
        else:
            choose = quotations.model_chooser(
                judger
                or Translator(
                    a.judge_model,
                    ROOT / "work/translations" / f"quote-choice-{a.lang}.jsonl",
                    model_config(a.judge_model),
                    a.host,
                )
            )

        def quotes(quotation, author):
            try:
                return quotations.established(
                    quotation, author, "en", a.lang, quote_cache, choose
                )
            except OSError as error:
                print(f"Quotation lookup skipped: {error}", file=sys.stderr)
                return None

    corrections_path = out.with_suffix(".corrections.json")
    corrections = (
        json.loads(corrections_path.read_text()) if corrections_path.exists() else {}
    )
    report = translate_epub(
        a.epub,
        out,
        translate,
        a.lang,
        prepare=prepare,
        checks=checks,
        review=review,
        corrections=corrections,
        judge=judge,
        judge_share=a.judge_share,
        quotes=quotes,
    )
    if use_jev:
        report["judge_model"] = jev_judge.MODEL
    elif judger:
        report["judge_model"] = a.judge_model
        report["judge_timing"] = timing_summary(judger.timings)
    pairs = report.pop("pairs")
    review_sheet(
        pairs, report["reviewed_blocks"], out.with_suffix(".review.html"), out.stem
    )
    write_json(out.with_suffix(".pairs.json"), pairs)
    if reviewer:
        report["review_model"] = a.review_model
        report["review_timing"] = timing_summary(reviewer.timings)
    report["model_calls"] = translate.calls
    report["timing"] = timing_summary(translate.timings)
    report["model_config"] = config
    report["glossary_terms"] = len(translate.glossary)
    report["narrator"] = a.narrator
    if a.epubcheck.exists():
        check = lambda path: subprocess.run(
            ["java", "-jar", str(a.epubcheck), str(path)], capture_output=True
        )
        result = check(out)
        report["epubcheck"] = "passed" if result.returncode == 0 else "failed"
        if result.returncode:
            # Errors the input already has (e.g. Calibre IDs) are not translation defects.
            report["epubcheck"] += (
                " (input fails too)" if check(a.epub).returncode else ""
            )
            print(result.stdout.decode(errors="replace"), file=sys.stderr)
    write_json(out.with_suffix(".translation.json"), report)
    if report.get("epubcheck") == "failed":
        raise SystemExit("EPUBCheck found errors the input does not have")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "fallback_blocks"}, indent=2
        )
    )


if __name__ == "__main__":
    main()
