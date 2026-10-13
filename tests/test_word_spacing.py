"""Source witnesses license literal spacing, never speculative segmentation."""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from word_spacing import SpacingLexicon, repair_word_spacing, split_candidates
from regression import snapshot


class Dictionary:
    def lookup(self, word):
        return word.lower() in {"of", "life", "or", "illegal", "i", "learned"}


class Recognizer:
    doc = [None]

    def __init__(self, readings):
        self.readings = readings
        self.calls = []

    def line_words(self, page, row, raw, dpi):
        self.calls.append(dpi)
        return dict(text=self.readings[dpi])


def run(text, witnesses, crops, protected=(), inline=(), native=False):
    box = [0.1, 0.2, 0.9, 0.22]
    row = dict(text=text, page=1, bbox=box, inline=list(inline))
    book = dict(language="en", source_sha256="0" * 64)
    if native:
        book["text_source"] = "native"
    audit = []
    recognize = Recognizer(dict(zip([600, 300], crops)))
    with tempfile.TemporaryDirectory() as temp:
        paths = {}
        for engine, value in zip(["vision", "rapid"], witnesses):
            path = Path(temp) / engine
            path.mkdir()
            (path / "0001.json").write_text(
                json.dumps(dict(lines=[dict(text=value, bbox=box, confidence=1)]))
            )
            paths[engine] = path
        report = repair_word_spacing(
            {1: [row]},
            book,
            paths,
            SpacingLexicon(Dictionary(), "en", protected),
            recognize,
            audit,
        )
    return row, report, audit, recognize


class WordSpacingTests(unittest.TestCase):
    def test_regression_requires_spacing_evidence_and_detects_audit_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for name, value in {
                "book-model.json": [],
                "corrected-pages.json": {},
                "text-coverage.json": dict(status="passed", counts={}),
                "apparatus-analysis.json": {},
            }.items():
                (out / name).write_text(json.dumps(value))
            (out / "test.txt").write_text("text")
            with zipfile.ZipFile(out / "test.epub", "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
            book = dict(
                slug="test", source_sha256="0" * 64, repair_ocr_word_spacing=True
            )
            with self.assertRaises(FileNotFoundError):
                snapshot(out, book)
            (out / "word-spacing-repair.json").write_text(json.dumps(dict(corrected=7)))
            before = snapshot(out, book)
            (out / "word-spacing-repair.json").write_text(json.dumps(dict(corrected=0)))
            after = snapshot(out, book)
            self.assertNotEqual(
                before["artifacts"]["word-spacing-repair.json"],
                after["artifacts"]["word-spacing-repair.json"],
            )

    def test_literal_split_needs_two_lexical_anchors(self):
        self.assertTrue(
            split_candidates("the best oflife is good", "the best of life is good")
        )
        self.assertTrue(
            split_candidates("Than the angels oflife?", "Than the angels of life?")
        )
        self.assertFalse(split_candidates("best oflife", "best of life"))
        self.assertFalse(
            split_candidates("the best oflife is good", "the worst of life is bad")
        )

    def test_letter_case_punctuation_and_compounds_are_preserved(self):
        for before, after in [
            ("the best oflife is good", "the best of Live is good"),
            ("a well-known fact now", "a well known fact now"),
            ("a it'shere phrase now", "a it's here phrase now"),
            ("the best oflife is good", "the best of, life is good"),
            ("the best of life is good", "the best oflife is good"),
        ]:
            self.assertFalse(split_candidates(before, after))

    def test_two_engines_and_two_crop_scales_with_style_offsets(self):
        text, target = "the best oflife is good", "the best of life is good"
        row, report, audit, recognize = run(
            text,
            [target] * 2,
            [target] * 2,
            inline=[
                dict(start=9, end=15, tags=["em"]),
                dict(start=19, end=23, tags=["em"]),
            ],
        )
        self.assertEqual(row["text"], target)
        self.assertEqual(report["corrected"], 1)
        self.assertEqual(recognize.calls, [600, 300])
        self.assertEqual(row["inline"][0], dict(start=9, end=16, tags=["em"]))
        self.assertEqual(row["inline"][1], dict(start=20, end=24, tags=["em"]))
        self.assertEqual(audit[0]["kind"], "source-word-spacing")
        self.assertEqual(audit[0]["evidence"]["witnesses"], [target] * 2)

    def test_conflicting_or_missing_witness_abstains_before_crop(self):
        text, target = "the best oflife is good", "the best of life is good"
        for other in [text, "", "the best of lives is good"]:
            row, report, audit, recognize = run(text, [target, other], [target] * 2)
            self.assertEqual(row["text"], text)
            self.assertEqual(report["corrected"], 0)
            self.assertEqual(audit, [])
            self.assertEqual(recognize.calls, [])

    def test_disagreeing_crop_and_lost_pronoun_keep_manual_reading(self):
        for text, target, crops in [
            (
                "the best oflife is good",
                "the best of life is good",
                ["the best of life is good", "the best oflife is good"],
            ),
            (
                "The Plant Ilearned about this",
                "The Plant I learned about this",
                ["The Plant learned about this", "The Plant J learned about this"],
            ),
        ]:
            row, report, audit, _ = run(text, [target] * 2, crops)
            self.assertEqual(row["text"], text)
            self.assertEqual(report["corrected"], 0)
            self.assertEqual(report["decisions"][0]["action"], "review")
            self.assertEqual(audit, [])

    def test_valid_words_brands_and_protected_terms_are_not_split(self):
        for text, target, protected in [
            ("a therapist works here", "a the rapist works here", ()),
            ("a nowhere sign appears", "a now here sign appears", ()),
            ("a BlueWidget works here", "a Blue Widget works here", ()),
            ("the best oflife is good", "the best of life is good", ("oflife",)),
        ]:
            row, report, audit, recognize = run(
                text, [target] * 2, [target] * 2, protected
            )
            self.assertEqual(row["text"], text)
            self.assertEqual(report["corrected"], 0)
            self.assertEqual(audit, [])
            self.assertEqual(recognize.calls, [])

    def test_repeated_fused_words_are_bound_to_their_own_context(self):
        text = "the best oflife is good and the rest oflife is poor"
        target = "the best of life is good and the rest oflife is poor"
        row, report, audit, _ = run(text, [target] * 2, [target] * 2)
        self.assertEqual(row["text"], target)
        self.assertEqual(report["corrected"], 1)
        self.assertEqual(len(audit), 1)

    def test_multiple_source_edits_use_frozen_offsets(self):
        text = "the best oflife is good and the rest oflife is poor"
        target = "the best of life is good and the rest of life is poor"
        row, report, audit, _ = run(text, [target] * 2, [target] * 2)
        self.assertEqual(row["text"], target)
        self.assertEqual(report["corrected"], 2)
        self.assertEqual(len(audit), 2)

    def test_native_input_does_not_consume_scan_witnesses(self):
        text = "the best oflife is good"
        row, report, audit, recognize = run(text, [""] * 2, [""] * 2, native=True)
        self.assertEqual(row["text"], text)
        self.assertEqual(report["reason"], "native-text")
        self.assertEqual(audit, [])
        self.assertEqual(recognize.calls, [])


if __name__ == "__main__":
    unittest.main()
