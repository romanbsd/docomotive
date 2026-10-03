"""Whole-book noisy-channel ranking, gated by local word-crop recognition.

Scores are ranking evidence, not calibrated probabilities. Never repair names,
accepted spellings, protected terms, references, or an uncorroborated guess.
"""

import difflib
import math
import re
import subprocess
from collections import Counter
from pathlib import Path

import pymupdf
import symspellpy
from PIL import Image, ImageOps
from symspellpy import SymSpell, Verbosity
from wordfreq import zipf_frequency

from common import apply_edits, digest, read_json, write_json
from book_model import row_id
from vocabulary import TOKEN

# Common scanned serif confusions. Other edits may rank, but cannot auto-apply.
CONFUSIONS = {frozenset(pair) for pair in ("ce", "ft", "it", "il", "bh", "nr")}


def confusable(before, after):
    differences = [(a, b) for a, b in zip(before, after) if a != b]
    return (
        len(before) == len(after)
        and len(differences) == 1
        and frozenset(differences[0]) in CONFUSIONS
    )


def locate_word(text, words, word, candidate):
    """Anchor a crop by neighboring words, not spelling similarity alone."""
    tokens = [m.group().lower() for m in TOKEN.finditer(text)]
    if tokens.count(word) != 1:
        return None
    index = tokens.index(word)
    old, owners = [], []
    for owner, value in enumerate(words):
        for token in TOKEN.findall(value[4].lower()):
            old.append(token)
            owners.append(owner)
    scored = []
    for i, token in enumerate(old):
        similarity = max(
            difflib.SequenceMatcher(None, value, token).ratio()
            for value in (word, candidate)
        )
        if similarity < 0.75:
            continue
        # Two neighbors in either direction disambiguate repeated similar words
        # and prevent a correct word elsewhere in the line supplying false OCR.
        anchors = sum(
            tokens[index + d] == old[i + d]
            for d in (-2, -1, 1, 2)
            if 0 <= index + d < len(tokens) and 0 <= i + d < len(old)
        )
        if anchors or len(tokens) == len(old) == 1:
            scored.append(((anchors, similarity), owners[i]))
    scored.sort(reverse=True)
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return words[scored[0][1]]


class ContextRanker:
    """Smoothed bigram Markov model with fixed general and book priors."""

    def __init__(self, model, book, dictionary, protected=()):
        self.dictionary = dictionary
        self.protected = {w.lower() for w in protected}
        root = Path(symspellpy.__file__).parent
        unigram = root / "frequency_dictionary_en_82_765.txt"
        bigram = root / "frequency_bigramdictionary_en_243_342.txt"
        self.resources = {p.name: digest(p.read_bytes()) for p in (unigram, bigram)}
        self.sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        if not self.sym.load_dictionary(str(unigram), 0, 1):
            raise ValueError("Cannot load pinned frequency dictionary")
        self.general = {}
        self.outgoing = Counter()
        for line in bigram.read_text().splitlines():
            left, right, count = line.split()
            count = int(count)
            self.general[left, right] = count
            self.outgoing[left] += count
        self.counts = Counter()
        self.pairs = Counter()
        self.book_outgoing = Counter()
        for entry in model:
            if self.excluded(entry, book):
                continue
            for block in entry["blocks"] + entry["notes"]:
                # Do not train across paragraph or sentence boundaries.
                for sentence in re.split(r"[.!?;]\s+", block["text"]):
                    words = [m.group().lower() for m in TOKEN.finditer(sentence)]
                    self.counts.update(words)
                    self.pairs.update(zip(words, words[1:]))
                    self.book_outgoing.update(words[:-1])

    @staticmethod
    def excluded(entry, book):
        return entry["chapter"] in book.get("statistics_excluded_chapters", []) or (
            entry["source_pages"][0]
            in book.get("reference_pages", []) + book.get("index_pages", [])
        )

    def known(self, word):
        low = word.lower()
        return (
            low in self.protected
            or self.dictionary.lookup(word)
            or self.dictionary.lookup(low)
            or low in self.sym.words
            or zipf_frequency(low, "en") >= 2
        )

    def transition(self, left, right):
        # Ten book observations' worth of general-language prior keeps sparse
        # book bigrams from dominating. Missing general pairs back off to Zipf.
        prior = 10 ** (zipf_frequency(right, "en") - 9)
        # Reserve 10% for unigram backoff: an absent pair in a finite corpus
        # must not make a common word less likely than an unrelated rare word.
        general = (
            0.9 * self.general.get((left, right), 0) / (self.outgoing[left] + 1)
            + 0.1 * prior
        )
        return (self.pairs[left, right] + 10 * general) / (
            self.book_outgoing[left] + 10
        )

    def rank(self, word, context, offset):
        matches = list(TOKEN.finditer(context))
        index = next((i for i, m in enumerate(matches) if m.start() == offset), None)
        left = matches[index - 1].group().lower() if index and index > 0 else None
        right = (
            matches[index + 1].group().lower()
            if index is not None and index + 1 < len(matches)
            else None
        )
        if index is not None:
            if left and re.search(
                r"[.!?;]", context[matches[index - 1].end() : offset]
            ):
                left = None
            if right and re.search(
                r"[.!?;]", context[matches[index].end() : matches[index + 1].start()]
            ):
                right = None
        choices = []
        for candidate in self.sym.lookup(word.lower(), Verbosity.CLOSEST, 2):
            term = candidate.term
            score = math.log(max(candidate.count, 1))
            score += 0.5 * math.log1p(self.counts[term])
            if left:
                score += math.log(max(self.transition(left, term), 1e-15))
            if right:
                score += math.log(max(self.transition(term, right), 1e-15))
            # A known glyph substitution gets a modest likelihood advantage;
            # crop OCR still has to read the winning word, in both modes.
            supported = confusable(word.lower(), term)
            score += 2 if supported else 0
            choices.append(
                dict(
                    term=term,
                    distance=candidate.distance,
                    frequency=candidate.count,
                    book_count=self.counts[term],
                    confusion_supported=supported,
                    context_score=round(score, 6),
                )
            )
        return sorted(choices, key=lambda c: (-c["context_score"], c["term"]))


class CropRecognizer:
    def __init__(self, doc, work):
        self.doc = doc
        self.work = Path(work) / "lexical-crops"
        self.version = subprocess.run(
            ["tesseract", "--version"], capture_output=True, text=True, check=True
        ).stdout
        languages = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, check=True
        )
        match = re.search(r'"([^"]+)"', languages.stdout + languages.stderr)
        if not match or not (Path(match[1]) / "eng.traineddata").is_file():
            raise ValueError("Cannot fingerprint local Tesseract English model")
        self.model_hash = digest((Path(match[1]) / "eng.traineddata").read_bytes())

    def __call__(self, row, word, candidate):
        page = self.doc[row["page"] - 1]
        cy = (row["bbox"][1] + row["bbox"][3]) / 2
        # Embedded words locate the crop. Their old OCR reading is retained
        # separately; it can resolve a weak context margin only with fresh OCR.
        words = [
            w
            for w in page.get_text("words")
            if abs((w[1] + w[3]) / 2 / page.rect.height - cy) < 0.012
            and row["bbox"][0] - 0.02 <= w[0] / page.rect.width <= row["bbox"][2]
        ]

        if not words:
            return dict(readings=[], reason="no-word-geometry")
        located = locate_word(
            row["text"], sorted(words, key=lambda w: w[0]), word, candidate
        )
        if located is None:
            return dict(readings=[], reason="ambiguous-word-geometry")
        rect = pymupdf.Rect(located[:4])
        # One PDF point surrounds the glyph box; a white 20-pixel frame lets
        # single-word segmentation work without neighboring annotation noise.
        rect = pymupdf.Rect(rect.x0 - 1, rect.y0 - 1, rect.x1 + 1, rect.y1 + 1)
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(600 / 72, 600 / 72),
            clip=rect,
            colorspace=pymupdf.csGRAY,
        )
        image = ImageOps.expand(
            Image.frombytes("L", (pix.width, pix.height), pix.samples),
            border=20,
            fill=255,
        )
        key = digest(
            pix.samples
            + self.version.encode()
            + repr(list(rect)).encode()
            + self.model_hash.encode()
            + b"word-crop-v3-600dpi-psm8-13"
        )
        cache = self.work / (key + ".json")
        if cache.exists():
            return read_json(cache)
        self.work.mkdir(parents=True, exist_ok=True)
        image.save(self.work / (key + ".png"))
        readings = []
        for psm in (8, 13):
            result = subprocess.run(
                [
                    "tesseract",
                    str(self.work / (key + ".png")),
                    "stdout",
                    "--psm",
                    str(psm),
                    "-l",
                    "eng",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            readings.append(result.stdout.strip())
        evidence = dict(
            readings=readings,
            embedded_word=located[4],
            cache_key=key,
            crop_sha256=digest((self.work / (key + ".png")).read_bytes()),
            traineddata_sha256=self.model_hash,
            rect=list(rect),
            engine="tesseract",
            version=self.version.splitlines()[0],
            dpi=600,
            segmentation_modes=[8, 13],
        )
        write_json(cache, evidence)
        return evidence


def repair(model, pages, book, ranker, recognize, audit):
    """Decide from one immutable assembled book; apply exact source-row edits."""
    rows = {
        r.get("row_id", row_id(int(n), r, book.get("source_sha256", ""))): r
        for n, rr in pages.items()
        for r in rr
    }
    decisions, seen = [], set()
    edits = []
    for entry in model:
        if ranker.excluded(entry, book):
            continue
        for block in entry["blocks"] + entry["notes"]:
            if block["kind"] not in ("text", "quote", "note"):
                continue
            for match in TOKEN.finditer(block["text"]):
                word = match.group()
                if (
                    len(word) < 4
                    or not word.isalpha()
                    or not word.islower()
                    or ranker.known(word)
                ):
                    continue
                sources = [
                    s
                    for s in block["sources"]
                    if s["start"] <= match.start() and s["end"] >= match.end()
                ]
                if len(sources) != 1:
                    continue  # Never patch a reconstructed word across source rows.
                row = rows[sources[0]["row_id"]]
                ident = sources[0]["row_id"]
                identity = (ident, word)
                if identity in seen:
                    continue
                seen.add(identity)
                choices = ranker.rank(word, block["text"], match.start())
                if not choices:
                    continue
                best = choices[0]
                margin = (
                    best["context_score"] - choices[1]["context_score"]
                    if len(choices) > 1
                    else None
                )
                decision = dict(
                    page=row["page"],
                    row_id=ident,
                    before=word,
                    after=best["term"],
                    bbox=row["bbox"],
                    candidates=choices[:5],
                    margin=round(margin, 6) if margin is not None else None,
                    action="review",
                )
                decisions.append(decision)
                # Common, already-attested targets bound automatic edits;
                # rare terminology remains review-only.
                if (
                    not best["confusion_supported"]
                    or best["book_count"] < 2
                    or zipf_frequency(best["term"], "en") < 3
                ):
                    decision["reason"] = "insufficient-lexical-evidence"
                    continue
                if len(re.findall(r"\b" + re.escape(word) + r"\b", row["text"])) != 1:
                    decision["reason"] = "ambiguous-row-occurrence"
                    continue
                evidence = recognize(row, word, best["term"])
                decision["crop"] = evidence
                readings = [TOKEN.findall(s.lower()) for s in evidence["readings"]]
                if len(readings) != 2 or any(
                    words != [best["term"]] for words in readings
                ):
                    decision["reason"] = "crop-does-not-corroborate"
                    continue
                # An e-fold margin is a ranking guard, not a confidence value.
                # Weak margins additionally require the independent embedded
                # OCR word to agree with both fresh crop segmentation modes.
                if (
                    margin is not None
                    and margin < 1
                    and TOKEN.findall(evidence.get("embedded_word", "").lower())
                    != [best["term"]]
                ):
                    decision["reason"] = "weak-context-without-embedded-corroboration"
                    continue
                decision["action"] = "correct"
                decision["reason"] = "context-and-two-segmentation-crop-readings"
                edits.append(
                    (
                        row,
                        dict(
                            page=row["page"],
                            before=word,
                            after=best["term"],
                            count=1,
                            whole_word=True,
                            evidence=decision,
                        ),
                    )
                )
    for row, edit in edits:
        apply_edits([row], [edit], audit, "whole-book-lexical-repair")
    return dict(
        mode="local-crop-corroborated; immutable whole-book context",
        resources=ranker.resources,
        corrected=len(edits),
        decisions=decisions,
    )
