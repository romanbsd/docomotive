"""Scan artifact recovery needs opt-in and two unchanged independent rows."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ocr import correct_page, repair_witnessed_edge_artifacts
from profile_validation import validate_profile

BOX = [0.1, 0.2, 0.9, 0.23]


def fixed(text, witnesses):
    row = dict(text=text, bbox=BOX)
    audit = []
    repair_witnessed_edge_artifacts(
        row, [dict(text=w) if w is not None else None for w in witnesses], 1, audit
    )
    return row["text"], audit


class EdgeArtifactTests(unittest.TestCase):
    def test_word_edges_and_numbered_prefix_use_exact_independent_rows(self):
        for before, after in [
            ("_this present book is cited here.", "this present book is cited here."),
            (
                "an_ efficient manner is required here.",
                "an efficient manner is required here.",
            ),
            (
                "1]. Author Name, Book Title (New York).",
                "11. Author Name, Book Title (New York).",
            ),
        ]:
            text, audit = fixed(before, (after, after))
            self.assertEqual(text, after)
            self.assertEqual(len(audit), 1)
            self.assertEqual(audit[0]["kind"], "two-fresh-edge-artifact")
            self.assertEqual(audit[0]["bbox"], BOX)

    def test_one_peer_or_changed_prose_cannot_license_repair(self):
        raw = "_this present book is cited here."
        after = raw[1:]
        for witnesses in [
            (after, raw),
            (after, None),
            (after, after.replace("cited", "quoted")),
        ]:
            self.assertEqual(fixed(raw, witnesses)[0], raw)

    def test_identifier_and_apparatus_syntax_remain_literal(self):
        for raw in [
            "some_name is stored over here.",
            "__private value is stored over here.",
            "_NAME is stored over here.",
            "_x is defined in this formula.",
            "_value = the next input value.",
            "[1]. Author Name, Book Title.",
            "1]. See [other] references for details.",
        ]:
            self.assertEqual(
                fixed(raw, (raw.replace("_", "").replace("1].", "11."),) * 2)[0], raw
            )

    def test_default_is_unchanged_and_manual_overlays_keep_preconditions(self):
        raw = "_this present book is cited here."
        after = raw[1:]
        args = [
            [dict(text=raw, bbox=BOX)],
            [],
            [dict(text=after, bbox=BOX)],
            [dict(text=after, bbox=BOX)],
            [],
            [],
            1,
        ]
        self.assertEqual(correct_page(*copy.deepcopy(args))[0]["text"], raw)
        self.assertEqual(
            correct_page(*copy.deepcopy(args), recover_edge_artifacts=True)[0]["text"],
            after,
        )
        self.assertEqual(
            correct_page(
                *copy.deepcopy(args),
                recover_edge_artifacts=True,
                protected_edits=[dict(before=raw, after=after)],
            )[0]["text"],
            raw,
        )

    def test_profile_flag_is_boolean(self):
        import json

        book = json.loads(Path("config/synchronicity/book.json").read_text())
        validate_profile(book)
        for value in (1, "true", None):
            with self.assertRaises(ValueError):
                validate_profile(dict(book, recover_ocr_edge_artifacts=value))
