"""High-risk OCR text must be resolved before publishing an EPUB."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from text_quality import findings, unclassified_note_footers

BOX = [0.1, 0.2, 0.8, 0.22]


def model(text):
    source = dict(page=17, bbox=BOX, text=text, start=0, end=len(text))
    return [
        dict(
            chapter=4, blocks=[dict(kind="text", text=text, sources=[source])], notes=[]
        )
    ]


def disagreement(primary, vision, rapid, status="needs-review"):
    return dict(
        page=17,
        bbox=BOX,
        kind="ocr-disagreement",
        status=status,
        primary=primary,
        engine_texts=dict(vision=vision, rapid=rapid),
    )


class TextQualityTests(unittest.TestCase):
    def test_missing_numbered_footer_blocks_build_until_region_is_classified(self):
        rows = [
            dict(
                text="Ordinary body prose",
                bbox=[0.1, 0.2 + i * 0.035, 0.9, 0.22 + i * 0.035],
                font_size=11,
            )
            for i in range(7)
        ]
        rows.extend(
            [
                dict(
                    text="1 A source note begins here",
                    bbox=[0.14, 0.55, 0.9, 0.57],
                    font_size=8,
                ),
                dict(
                    text="and continues on this line",
                    bbox=[0.14, 0.57, 0.9, 0.59],
                    font_size=8,
                ),
            ]
        )
        book = dict(link_numeric_footnotes=True, note_starts={})
        self.assertEqual(
            [x["page"] for x in unclassified_note_footers({40: rows}, book)], [40]
        )
        book["note_starts"] = {"40": 0.54}
        self.assertEqual(unclassified_note_footers({40: rows}, book), [])

    def test_unresolved_opening_and_mixed_case_block_publication(self):
        for bad, vision, rapid in (
            (
                "Lis is much too horrible today.",
                "LIFE is much too horrible today.",
                "Lire is much too horrible today.",
            ),
            (
                "‘Tue involvement of the gods continues.",
                "The involvement of the gods continues.",
                "The involvement of the gods continues.",
            ),
            (
                "Srory-TELLNc has been a serious business.",
                "STORY-TELLING has been a serious business.",
                "Srory-reLLInc has been a serious business.",
            ),
        ):
            with self.subTest(bad=bad):
                self.assertEqual(
                    len(findings(model(bad), [disagreement(bad, vision, rapid)])), 1
                )

    def test_review_or_source_repair_resolves_risk(self):
        bad = "Lis is much too horrible today."
        peer = "Life is much too horrible today."
        row = disagreement(bad, peer, peer)
        self.assertEqual(findings(model(peer), [row]), [])
        self.assertEqual(findings(model(bad), [dict(row, status="scan-reviewed")]), [])
        self.assertEqual(
            findings(
                model(bad),
                [
                    disagreement(
                        bad, "Life is much better today.", "Life is much better today."
                    )
                ],
            ),
            [],
        )

    def test_unrelated_disagreements_do_not_block(self):
        primary = "The original sentence ends here."
        row = disagreement(
            primary,
            "The original sentence ends there.",
            "The original sentence ends here.",
        )
        self.assertEqual(findings(model(primary), [row]), [])


if __name__ == "__main__":
    unittest.main()
