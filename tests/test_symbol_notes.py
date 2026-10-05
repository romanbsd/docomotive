"""Printed footnote symbols must link to a unique source-page target."""

import copy
import sys
import unittest
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from apparatus import link_symbol_footnotes
from render_text import block_html, notes_html


def model(
    body="First page. A quoted statement.* Another sentence.", note="* Footnote text."
):
    return [
        dict(
            chapter=1,
            blocks=[
                dict(
                    kind="text",
                    text=body,
                    page_breaks=[dict(page=41, offset=0), dict(page=42, offset=12)],
                    sources=[
                        dict(page=41, start=0, end=11),
                        dict(page=42, start=12, end=len(body)),
                    ],
                )
            ],
            notes=[
                dict(
                    kind="note",
                    id="note-42",
                    root_page=42,
                    text=note,
                    sources=[dict(page=42)],
                    page_breaks=[],
                )
            ],
            note_targets={42: 42, 43: 42},
        )
    ]


class SymbolFootnoteTests(unittest.TestCase):
    def test_joined_note_marker_does_not_require_ocr_space(self):
        value = model(note="*I have a source footnote.")
        result = link_symbol_footnotes(value, {"link_symbol_footnotes": True})
        self.assertEqual(result["symbol_links"], 1)
        self.assertEqual(value[0]["notes"][0]["text"], "*I have a source footnote.")

    def test_exact_symbol_and_backlink_preserve_text_and_page_scope(self):
        value = model()
        original = copy.deepcopy(value)
        report = link_symbol_footnotes(value, {"link_symbol_footnotes": True})
        self.assertEqual(report["symbol_links"], 1)
        self.assertEqual(
            value[0]["blocks"][0]["text"], original[0]["blocks"][0]["text"]
        )
        self.assertEqual(value[0]["linked_note_roots"], [42])
        book = dict(
            _note_targets={
                n: root
                for n, root in value[0]["note_targets"].items()
                if root not in value[0]["linked_note_roots"]
            }
        )
        markup = block_html(value[0]["blocks"][0], book, set(), [], "chapter-01.xhtml")
        self.assertIn('href="#note-42"><sup>*</sup></a>', markup)
        self.assertNotIn("[note]", markup)
        notes = notes_html(value[0]["notes"], {})
        self.assertIn('href="#footnoteref-42" role="doc-backlink"', notes)
        self.assertEqual(
            "".join(
                etree.fromstring(
                    '<root xmlns:epub="http://www.idpf.org/2007/ops">'
                    + markup
                    + "</root>"
                ).itertext()
            ),
            original[0]["blocks"][0]["text"],
        )

    def test_ambiguous_symbols_keep_fallback_access(self):
        for body, note in [
            ("First page. One.* Two.*", "* Footnote."),
            ("First page. One.*", "* First note. † Second note."),
            ("First page. Product 3*4.", "* Footnote."),
        ]:
            value = model(body, note)
            report = link_symbol_footnotes(value, {"link_symbol_footnotes": True})
            self.assertEqual(report["symbol_links"], 0)
            self.assertNotIn("inline", value[0]["blocks"][0])
            self.assertNotIn("linked_note_roots", value[0])
            self.assertIn(
                "[note]",
                block_html(
                    value[0]["blocks"][0],
                    {"_note_targets": value[0]["note_targets"]},
                    set(),
                    [],
                    "test",
                ),
            )

    def test_symbol_on_other_page_cannot_supply_reference(self):
        value = model("First page.* A different page follows.")
        value[0]["blocks"][0]["sources"][0]["end"] = 12
        report = link_symbol_footnotes(value, {"link_symbol_footnotes": True})
        self.assertEqual(report["symbol_links"], 0)
        self.assertEqual(report["unresolved"][0]["candidates"], 0)
        value = model()
        original = copy.deepcopy(value)
        self.assertEqual(link_symbol_footnotes(value, {}), {"mode": "not-configured"})
        self.assertEqual(value, original)


if __name__ == "__main__":
    unittest.main()
