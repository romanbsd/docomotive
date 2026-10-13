"""Split unknown fused tokens using literal row and crop witnesses."""

import difflib
import re
from functools import lru_cache
from pathlib import Path

import symspellpy
from wordfreq import zipf_frequency

from book_model import row_id
from common import apply_edits, digest, read_json
from ocr import merge_rows, nearest
from vocabulary import TOKEN


def split_candidates(text, witness):
    """Return spacing-only one-to-many changes with two neighboring anchors."""
    own = list(TOKEN.finditer(text))
    other = list(TOKEN.finditer(witness))
    left = [m.group() for m in own]
    right = [m.group() for m in other]
    result = {}
    for tag, i, j, k, l in difflib.SequenceMatcher(
        None, left, right, autojunk=False
    ).get_opcodes():
        if tag != "replace" or j - i != 1 or not 2 <= l - k <= 3:
            continue
        word, parts = left[i], right[k:l]
        if not word.isalpha() or not all(part.isalpha() for part in parts):
            continue  # Numbers, apostrophes and compounds are not word spaces.
        start, end = own[i].span()
        target = witness[other[k].start() : other[l - 1].end()]
        if not re.fullmatch(r"[^\W\d_]+(?:[ \t]+[^\W\d_]+){1,2}", target):
            continue
        if word != "".join(parts):
            continue  # Preserve every letter and its case.
        anchors = sum(
            left[i - d] == right[k - d] and len(left[i - d]) >= 2
            for d in (1, 2)
            if i >= d and k >= d
        ) + sum(
            left[j + d] == right[l + d] and len(left[j + d]) >= 2
            for d in (0, 1)
            if j + d < len(left) and l + d < len(right)
        )
        if anchors >= 2:
            result[(start, end, target)] = dict(
                before=word, after=target, parts=parts, anchors=anchors
            )
    return result


class SpacingLexicon:
    def __init__(self, dictionary, language, protected=()):
        self.dictionary = dictionary
        self.language = language
        self.protected = {w.lower() for w in protected}
        path = Path(symspellpy.__file__).parent / "frequency_dictionary_en_82_765.txt"
        self.words = (
            {line.rsplit(" ", 1)[0] for line in path.read_text().splitlines()}
            if language == "en"
            else set()
        )
        self.resources = {path.name: digest(path.read_bytes())} if self.words else {}

    @lru_cache(maxsize=None)
    def known(self, word):
        return bool(
            word.lower() in self.protected
            or word.lower() in self.words
            or zipf_frequency(word.lower(), self.language) >= 2
            or self.dictionary.lookup(word)
            or self.dictionary.lookup(word.lower())
        )

    def accepts(self, word, parts):
        pronoun = parts[0] == "I" and word.startswith("I") and word[1:].islower()
        return (
            (word.islower() or pronoun)
            and not self.known(word)
            and all(self.known(part) for part in parts)
        )


def repair_word_spacing(pages, book, witness_paths, lexicon, recognize, audit):
    """Apply checked source-row edits before reviewed manual overlays."""
    if book.get("text_source") == "native":
        return dict(corrected=0, decisions=[], reason="native-text")
    decisions = []
    for n, rows in pages.items():
        primary = "vision" if n in book.get("vision_primary_pages", []) else "tesseract"
        engines = ["tesseract" if primary == "vision" else "vision", "rapid"]
        observations = [
            merge_rows(read_json(Path(witness_paths[e]) / f"{n:04}.json")["lines"])
            for e in engines
        ]
        for row in rows:
            text = row["text"]
            peers = [nearest(row, source) for source in observations]
            if not all(peers):
                continue
            witnesses = [p["text"] for p in peers]
            proposals = [split_candidates(text, w) for w in witnesses]
            shared = proposals[0].keys() & proposals[1].keys()
            edits = []
            for key in sorted(shared):
                start, end, target = key
                candidate = proposals[0][key]
                if not lexicon.accepts(candidate["before"], candidate["parts"]):
                    continue
                record = dict(
                    page=n,
                    row_id=row_id(n, row, book["source_sha256"]),
                    bbox=row["bbox"],
                    before=candidate["before"],
                    after=target,
                    action="review",
                    reason="crop-spacing-conflict",
                    engines=engines,
                    witnesses=witnesses,
                    anchors=[p[key]["anchors"] for p in proposals],
                    crop_readings=[],
                    crop_scales=[600, 300],
                )
                decisions.append(record)
                for dpi in record["crop_scales"]:
                    record["crop_readings"].append(
                        recognize.line_words(
                            recognize.doc[n - 1], row, raw=True, dpi=dpi
                        )["text"]
                    )
                if not all(
                    key in split_candidates(text, w) for w in record["crop_readings"]
                ):
                    continue
                record.update(action="correct", reason="literal-row-and-crop-spacing")
                edits.append((start, end, record))
            for start, end, record in reversed(edits):
                edit = dict(
                    page=n,
                    before=record["before"],
                    after=record["after"],
                    whole_word=True,
                    prefix=row["text"][:start],
                    suffix=row["text"][end:],
                    count=1,
                    bbox=row["bbox"],
                    evidence=record,
                )
                apply_edits([row], [edit], audit, "source-word-spacing")
    return dict(
        corrected=sum(d["action"] == "correct" for d in decisions),
        decisions=decisions,
        resources=lexicon.resources,
    )
