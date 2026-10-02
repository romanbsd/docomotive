"""Changed recognition evidence must never inherit a scan acknowledgement."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from lxml import html

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from review_decisions import bound_decision, match_decisions, make_sheet
from common import file_digest, write_json, read_json


class ReviewDecisionTests(unittest.TestCase):
    def row(self, **changes):
        return dict(
            dict(
                page=3,
                bbox=[0.1, 0.2, 0.8, 0.22],
                kind="ocr-disagreement",
                primary="The reviewed sentence.",
                engine_texts={"vision": "The reviewed sentence."},
            ),
            **changes,
        )

    def test_exact_source_and_evidence_matches_after_geometry_shift(self):
        row = self.row()
        decision = bound_decision(row, "a" * 64)
        result = match_decisions(
            [self.row(bbox=[0.1, 0.24, 0.8, 0.26])], [decision], "a" * 64
        )
        self.assertEqual(result["matches"], {0: [0]})
        self.assertEqual(result["issues"], [])

    def test_source_text_and_witness_changes_do_not_match_same_geometry(self):
        row = self.row()
        decision = bound_decision(row, "a" * 64)
        cases = [
            (row, "b" * 64, "source-mismatch"),
            (self.row(primary="Changed text."), "a" * 64, "text-mismatch"),
            (
                self.row(engine_texts={"vision": "Changed witness."}),
                "a" * 64,
                "evidence-mismatch",
            ),
        ]
        for current, source, reason in cases:
            with self.subTest(reason=reason):
                result = match_decisions([current], [decision], source)
                self.assertEqual(result["matches"], {})
                self.assertEqual(result["issues"][0]["reason"], reason)

    def test_partial_binding_cannot_fall_back_to_legacy(self):
        row = self.row()
        decision = dict(page=3, bbox=row["bbox"], source_sha256="a" * 64)
        result = match_decisions([row], [decision], "a" * 64)
        self.assertEqual(result["matches"], {})
        self.assertEqual(result["legacy"], [])
        self.assertEqual(
            result["issues"][0]["reason"], "incomplete-or-unsupported-binding"
        )

    def test_legacy_retains_effect_but_is_explicitly_unbound(self):
        row = self.row()
        decision = dict(
            page=3, bbox=row["bbox"], text="Old legacy text", decision="corrected"
        )
        result = match_decisions([row], [decision], "a" * 64)
        self.assertEqual(result["matches"], {0: [0]})
        self.assertEqual(result["legacy"], [0])

    def test_uncertain_decision_never_acknowledges_row(self):
        row = self.row()
        decision = bound_decision(row, "a" * 64, "uncertain")
        result = match_decisions([row], [decision], "a" * 64)
        self.assertEqual(result["matches"], {})
        self.assertEqual(result["issues"][0]["reason"], "not-acknowledged")

    def test_duplicate_text_uses_geometry_and_rejects_remaining_ambiguity(self):
        row = self.row()
        decision = bound_decision(row, "a" * 64)
        result = match_decisions(
            [row, self.row(bbox=[0.1, 0.3, 0.8, 0.32])], [decision], "a" * 64
        )
        self.assertEqual(result["matches"], {0: [0]})
        result = match_decisions([row, row], [decision], "a" * 64)
        self.assertEqual(result["matches"], {})
        self.assertEqual(result["issues"][0]["reason"], "ambiguous-location")

    def test_confidence_and_status_are_not_text_evidence(self):
        row = self.row()
        decision = bound_decision(row, "a" * 64)
        result = match_decisions(
            [self.row(confidence=0.99, status="needs-review")], [decision], "a" * 64
        )
        self.assertEqual(result["matches"], {0: [0]})

    def test_offline_sheet_records_are_pending_and_escape_embedded_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "scan.pdf"
            profile = root / "book.json"
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((50, 100), "The reviewed sentence.")
                doc.save(pdf)
            source = file_digest(pdf)
            write_json(
                profile,
                dict(
                    title="Book",
                    author="Author",
                    language="en",
                    slug="book",
                    source_sha256=source,
                    chapters=[[1, 1, "Chapter"]],
                ),
            )
            row = self.row(
                page=1, primary='Danger </script> & "quotes"', status="needs-review"
            )
            write_json(root / "ocr-comparison.json", [row])
            write_json(root / "review-decisions.json", [])
            output = root / "review.html"
            with patch("builtins.print"):
                make_sheet(pdf, profile, root, output)
            report = read_json(output.with_suffix(".json"))
            self.assertEqual(report["records"][0]["decision"], "pending")
            record = {k: v for k, v in report["records"][0].items() if k != "id"}
            self.assertEqual(match_decisions([row], [record], source)["matches"], {})
            tree = html.fromstring(output.read_text())
            self.assertEqual(len(tree.xpath("//script")), 1)
            self.assertEqual(len(tree.xpath("//img")), 1)
            self.assertIn(r"\u003c/script>", tree.xpath("//script")[0].text)


if __name__ == "__main__":
    unittest.main()
