"""Canonical splits preserve content and attach artwork to source rows."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from figures import figure_position, split_source_block, detect_figure_captions
from apparatus import link_symbol_footnotes
from render_text import block_html


class SourceAnchorTests(unittest.TestCase):
    def block(self):
        return dict(
            kind="text",
            text="Earlier words. Later words.",
            sources=[
                dict(
                    page=1,
                    row_id="a",
                    text="Earlier words.",
                    bbox=[0.1, 0.4, 0.9, 0.42],
                    start=0,
                    end=14,
                ),
                dict(
                    page=2,
                    row_id="b",
                    text="Later words.",
                    bbox=[0.1, 0.6, 0.9, 0.62],
                    start=15,
                    end=27,
                ),
            ],
            inline=[dict(start=15, end=20, tags=["em"])],
            page_breaks=[],
            lines=[dict(row_id="a"), dict(row_id="b")],
            fragments=[],
        )

    def test_cross_page_paragraph_splits_at_image_position(self):
        blocks = [self.block()]
        self.assertEqual(figure_position(blocks, 2, 0.2, False), 1)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(figure_position(blocks, 2, 0.2, True), 1)
        self.assertEqual(
            [b["text"] for b in blocks], ["Earlier words.", "Later words."]
        )
        self.assertEqual(
            [s["row_id"] for b in blocks for s in b["sources"]], ["a", "b"]
        )
        self.assertEqual([r["row_id"] for b in blocks for r in b["lines"]], ["a", "b"])
        self.assertEqual(blocks[1]["inline"][0]["start"], 0)
        self.assertEqual(blocks[1]["inline"][0]["end"], 5)
        self.assertTrue(blocks[1]["continuation"])

    def test_split_cannot_break_joined_word(self):
        b = self.block()
        b["text"] = "Early continuation."
        b["sources"][0].update(start=0, end=10)
        b["sources"][1].update(start=10, end=len(b["text"]))
        self.assertIsNone(split_source_block(b, 1))

    def test_small_photo_credit_is_caption_without_swallowing_prose(self):
        rows = [
            dict(
                text="Ordinary narrow prose line",
                bbox=[0.6, 0.1 + i * 0.05, 0.95, 0.125 + i * 0.05],
            )
            for i in range(10)
        ]
        rows += [
            dict(text="Shaman in ritual clothes.", bbox=[0.6, 0.87, 0.95, 0.885]),
            dict(text="Фото Иванова, 1978 г.", bbox=[0.6, 0.89, 0.95, 0.905]),
        ]
        pages = {1: rows}
        book = dict(
            source_figure_anchors=True,
            figures=[dict(page=1, name="photo", rect=[0.1, 0.1, 0.55, 0.95])],
        )
        detect_figure_captions(pages, book, [])
        self.assertEqual([r.get("kind") for r in rows[-2:]], ["caption", "caption"])
        self.assertTrue(all("figure_caption_for" not in r for r in rows[:-2]))

    def test_quote_and_verse_size_setting_with_block_override(self):
        for kind in ["quote", "verse"]:
            b = dict(
                kind=kind, text="Quoted words.", sources=[], page_breaks=[], inline=[]
            )
            self.assertNotIn("font-size", block_html(b, {}, set(), [], "test"))
            self.assertIn(
                "font-size:90%",
                block_html(b, {"quote_font_scale": 0.9}, set(), [], "test"),
            )
            b["quote_font_scale"] = 0.85
            self.assertIn(
                "font-size:85%",
                block_html(b, {"quote_font_scale": 0.9}, set(), [], "test"),
            )

    def numeric_model(self):
        return [
            dict(
                chapter=1,
                blocks=[
                    dict(
                        kind="text",
                        text="A year 1972. Quoted words.1",
                        sources=[dict(page=1, start=0, end=27)],
                        inline=[dict(start=26, end=27, tags=["sup"])],
                    )
                ],
                notes=[
                    dict(
                        text="1 Bibliographic note.",
                        root_page=1,
                        id="note-1",
                        sources=[dict(page=1, text="1 Bibliographic note.")],
                    )
                ],
            )
        ]

    def test_numeric_footnote_requires_superscript_and_page_scope(self):
        model = self.numeric_model()
        report = link_symbol_footnotes(model, {"link_numeric_footnotes": True})
        self.assertEqual(len(report["linked"]), 1)
        self.assertEqual(model[0]["notes"][0]["backlink"], "#footnoteref-1")
        model = self.numeric_model()
        model[0]["blocks"][0]["inline"] = []
        self.assertEqual(
            len(
                link_symbol_footnotes(model, {"link_numeric_footnotes": True})["linked"]
            ),
            0,
        )
        model = self.numeric_model()
        model[0]["blocks"][0]["sources"][0]["page"] = 2
        self.assertEqual(
            len(
                link_symbol_footnotes(model, {"link_numeric_footnotes": True})["linked"]
            ),
            0,
        )

    def test_multiple_footer_entries_cannot_share_one_numeric_target(self):
        model = self.numeric_model()
        model[0]["notes"][0]["sources"].append(dict(page=1, text="2 Other note."))
        self.assertEqual(
            len(
                link_symbol_footnotes(model, {"link_numeric_footnotes": True})["linked"]
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
