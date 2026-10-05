#!/usr/bin/env python3
"""Translate a built EPUB block by block with a local Ollama TranslateGemma model.

Each leaf block (paragraph, heading, list item, caption, TOC label, ...) is one
request, so the model never sees markup it could reorder across blocks. Inline
elements become numbered placeholders (<x1>...</x1>, <x2/>) that TranslateGemma
keeps in place; the originals (note links, page-break anchors, emphasis) are
restored from them, so IDs and internal links survive translation. Blocks longer
than MAX_CHUNK are split at top-level sentence boundaries. Raw model outputs are
cached in an append-only JSONL file keyed by model and prompt, so interrupted
runs resume and reruns are offline replays.
"""

import argparse
import copy
import html
import json
import posixpath
import re
import subprocess
import sys
import urllib.request
from urllib.parse import unquote
import zipfile
from pathlib import Path

from lxml import etree
from tqdm import tqdm
from common import ROOT, digest, write_epub, write_json
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


def prompt(text, source, target):
    """The prompt format published with TranslateGemma; deviations degrade it."""
    s, t = LANGS[source], LANGS[target]
    return (
        f"You are a professional {s} ({source}) to {t} ({target}) translator. "
        f"Your goal is to accurately convey the meaning and nuances of the original {s} text "
        f"while adhering to {t} grammar, vocabulary, and cultural sensitivities.\n"
        f"Produce only the {t} translation, without any additional explanations or commentary. "
        f"Please translate the following {s} text into {t}:\n\n\n{text}"
    )


class Ollama:
    def __init__(self, model, cache, host="http://localhost:11434"):
        self.model, self.cache_path, self.host = model, Path(cache), host
        self.cache = {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text().splitlines():
                row = json.loads(line)
                self.cache[row["key"]] = row["output"]
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.calls = 0

    def __call__(self, text, temperature=0.0):
        key = digest(f"{self.model}\0{temperature}\0{text}".encode())
        if key not in self.cache:
            body = {
                "model": self.model,
                "stream": False,
                # Reasoning models (qwen3) otherwise spend the budget thinking.
                "think": False,
                "messages": [{"role": "user", "content": text}],
                # num_predict bounds runaway repetition; one token per input
                # character is far above any real translation length.
                "options": {
                    "temperature": temperature,
                    "num_ctx": 8192,
                    "num_predict": len(text),
                },
            }
            request = urllib.request.Request(
                self.host + "/api/chat",
                json.dumps(body).encode(),
                {"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=600) as response:
                output = json.load(response)["message"]["content"].strip()
            self.cache[key] = output
            with self.cache_path.open("a") as stream:
                stream.write(json.dumps({"key": key, "output": output}) + "\n")
            self.calls += 1
        return self.cache[key]


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
            elif flatten and not linked(child):
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


def translate_text(text, translate, source, target, temperature=0.0, keep=None):
    out = []
    for kept, piece in chunks(text, keep=keep):
        if kept:
            out.append(piece.strip())
            continue
        result = translate(prompt(piece.strip(), source, target), temperature)
        if len(result) > 3 * len(piece) + 200:
            raise ValueError("runaway output")
        out.append(result)
    return " ".join(out)


def translate_block(block, translate, source, target, keep=None):
    """Return the fallback level used: 0 markup, 1 markup retry, 2 no emphasis, 3 plain."""
    attempts = [(False, False, 0.0), (False, False, 0.4), (True, False, 0.0)]
    attempts.append((True, True, 0.0))
    for level, (flatten, strip, temperature) in enumerate(attempts):
        text, slots, breaks, anchors = encode(block, flatten, strip)
        try:
            output = translate_text(text, translate, source, target, temperature, keep)
            if strip:  # no placeholders left: any markup in the output is text
                output = html.escape(html.unescape(output), quote=False)
            decode(output, block, slots, breaks, anchors, strict=not strip)
            return level
        except ValueError, etree.XMLSyntaxError:
            if strip:
                raise


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


def translate_epub(epub, out, translate, target):
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
    for name, block in tqdm(work, unit="block", desc=f"{source}→{target}"):
        context = apparatus(block) or ("reference" if block in inferred else None)
        if context == "index" or (
            context == "reference"
            and (block in listed or citation_entry(encode(block)[0]))
        ):
            # The index is alphabetized and paged for the source edition.
            kept["index" if context == "index" else "citation"] += 1
            block.set("lang", source)
            block.set(XML_LANG, source)
            continue
        keep = citation_sentence if context == "reference" else None
        level = translate_block(block, translate, source, target, keep)
        levels[level] += 1
        if level:
            failures.append({"file": name, "id": block.get("id"), "level": level})
    for name, root in documents.items():
        for attr in ("lang", XML_LANG):
            if root.get(attr):
                root.set(attr, target)
        files[name] = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    language.text = target
    title = opf.find(f".//{DC}title")
    if title is not None and has_letters(title.text or ""):
        title.text = translate_text(title.text, translate, source, target)
    identifier = opf.find(f".//{DC}identifier")
    identifier.text += f"-{target}"
    files[opf_name] = etree.tostring(opf, xml_declaration=True, encoding="utf-8")
    write_epub(out, files)
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
    }


class Evaluated(Exception):
    pass


def evaluator(translate, min_chars=200):
    """Translate the first prose-sized chunk, print it, and stop the run."""

    def call(prompt, temperature=0.0):
        text = prompt.split("\n\n\n", 1)[1]
        if len(text) < min_chars:  # titles and TOC entries say little about a model
            return text
        print(text, end="\n\n", file=sys.stderr)
        print(translate(prompt, temperature))
        raise Evaluated

    return call


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("epub", type=Path)
    parser.add_argument("--lang", default="ru", choices=sorted(LANGS))
    parser.add_argument("--model", default="translategemma")
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
        action="store_true",
        help="print the first chunk of 200+ characters (source on stderr, "
        "translation on stdout) and exit without writing the EPUB",
    )
    a = parser.parse_args()
    out = a.epub.with_name(f"{a.epub.stem}.{a.lang}.epub")
    if a.output:
        out = a.output / out.name if a.output.is_dir() else a.output
    cache = a.cache or ROOT / "work/translations" / (
        re.sub(r"\W", "_", a.model) + f"-{a.lang}.jsonl"
    )
    translate = Ollama(a.model, cache, a.host)
    if a.evaluate:
        try:
            translate_epub(a.epub, out, evaluator(translate), a.lang)
        except Evaluated:
            return
        raise SystemExit("No chunk of 200+ characters to translate")
    report = translate_epub(a.epub, out, translate, a.lang)
    report["model_calls"] = translate.calls
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
