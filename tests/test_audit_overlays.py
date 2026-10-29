import json
import tempfile
import unittest
from pathlib import Path

from scripts.audit_overlays import audit_profiles


class OverlayAuditTests(unittest.TestCase):
    def test_overlay_audit_reports_exact_shared_match_and_recurring_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            config = tmp_path / "config"
            output = tmp_path / "output"
            for slug in ("one", "two"):
                profile = config / slug
                profile.mkdir(parents=True)
                (profile / "corrections.json").write_text(
                    json.dumps([{"page": 3, "before": "teh", "after": "the", "bbox": [0, 0, 1, 1]}])
                )
                book_output = output / slug
                book_output.mkdir(parents=True)
                events = [
                    {"page": 3, "kind": "two-witness-token", "before": "teh", "after": "the"},
                    {"page": 3, "kind": "scan-verified-manual", "before": "teh", "after": "the", "applied": True},
                ] if slug == "one" else []
                (book_output / "corrections-applied.json").write_text(json.dumps(events))

            before = (config / "one" / "corrections.json").read_bytes()
            report = audit_profiles(config, output)

            first = report["books"][0]["overlays"][0]
            self.assertEqual(first["status"], "reproduced-by-shared-event")
            self.assertEqual(first["shared_events"], ["two-witness-token"])
            self.assertTrue(report["recurring_exact_changes"][0]["candidate_only"])
            self.assertEqual((config / "one" / "corrections.json").read_bytes(), before)

    def test_overlay_audit_marks_missing_build_evidence_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            profile = config / "book"
            profile.mkdir(parents=True)
            (profile / "corrections.json").write_text("[]")

            report = audit_profiles(config, Path(directory) / "output")

            self.assertEqual(
                report["books"],
                [{"book": "book", "status": "missing-build-audit", "overlays": 0}],
            )
