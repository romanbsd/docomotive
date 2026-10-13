"""Focused OCR review retains all disagreements and bounds Jev requests."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from review_focus import classify, jev_preview, triage


def row(page, primary, embedded, vision, rapid):
    return dict(
        kind="ocr-disagreement",
        page=page,
        bbox=[0.1, 0.2, 0.8, 0.22],
        primary=primary,
        engine_texts=dict(
            embedded=embedded,
            vision=vision,
            rapid=rapid,
        ),
    )


class ReviewFocusTests(unittest.TestCase):
    def test_consensus_tiers_preserve_every_review_item(self):
        unanimous = row(
            2,
            "ft in the frame",
            "fit in the frame",
            "fit in the frame",
            "fit in the frame",
        )
        fresh = row(
            3, "K6énig arrived", "Konig arrived", "König arrived", "König arrived"
        )
        disputed = row(
            4, "The source line", "The source line", "The source line", "A source line"
        )
        entries = triage([disputed, fresh, unanimous])
        self.assertEqual(len(entries), 3)
        self.assertEqual(
            [entry["tier"] for entry in entries],
            ["three-peer-consensus", "fresh-peer-consensus", "other"],
        )
        self.assertEqual(classify(unanimous), "three-peer-consensus")
        self.assertEqual(
            entries[0]["lexical_choice"], dict(before="ft", candidate="fit")
        )

    def test_jev_preview_is_small_and_unsent(self):
        entries = triage(
            [
                row(
                    n,
                    "ft in the frame",
                    "it in the frame",
                    "fit in the frame",
                    "fit in the frame",
                )
                for n in range(30)
            ]
        )
        preview = jev_preview(entries, limit=5)
        self.assertFalse(preview["sent"])
        self.assertEqual(len(preview["request"]["questions"]), 5)
        self.assertTrue(
            all(
                "uncertain" in question["criteria"]
                for question in preview["request"]["questions"].values()
            )
        )


if __name__ == "__main__":
    unittest.main()
