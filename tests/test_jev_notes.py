"""Citation ranking never creates an unlocated or OCR-unsupported number."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jev_notes import prepare


class JevNotesTests(unittest.TestCase):
    def fixture(self):
        model = [
            dict(
                chapter=1,
                blocks=[
                    dict(
                        kind="text",
                        text=f"Paragraph {p}",
                        sources=[dict(page=p, text=f"Marker {p}")],
                    )
                    for p in (1, 2)
                ],
            ),
            dict(
                chapter=2,
                blocks=[
                    dict(kind="heading", text="Notes for section"),
                    dict(kind="text", text="1. A citation."),
                ],
            ),
        ]
        analysis = dict(
            missing_by_chapter={"1": [1, 2]},
            candidates=[
                dict(
                    chapter=1,
                    page=p,
                    text=f"Marker {p}",
                    status="sequence-ambiguous",
                    readings=["1"],
                    options=[dict(number=1)],
                )
                for p in (1, 2)
            ],
        )
        book = dict(
            endnote_sections=[dict(source_chapter=1, heading="Notes for section")]
        )
        return model, analysis, book

    def test_only_ambiguous_source_backed_locations_are_offered(self):
        payload = prepare(*self.fixture())
        self.assertEqual(list(payload["questions"]), ["chapter_1_note_1"])
        self.assertEqual(
            set(payload["questions"]["chapter_1_note_1"]["criteria"]),
            {"uncertain", "location_0", "location_1"},
        )

    def test_order_conflict_and_missing_locations_are_not_sent(self):
        model, analysis, book = self.fixture()
        analysis["candidates"][0]["status"] = "chapter-order-conflict"
        self.assertEqual(prepare(model, analysis, book)["questions"], {})

    def test_ambiguous_citation_text_is_not_guessed(self):
        model, analysis, book = self.fixture()
        model[-1]["blocks"].append(dict(kind="text", text="1. Another citation."))
        self.assertEqual(prepare(model, analysis, book)["questions"], {})
