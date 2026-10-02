"""Evidence-backed typography and artwork boundaries on synthetic sources."""

import sys
import unittest
import json
import io
from pathlib import Path
from unittest.mock import patch

import pymupdf
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from typography import attach_typography, verse_evidence
from native_pdf import extract_page
from ocr import merge_rows, correct_page
from figures import outside_figures, crop_artwork
from PIL import Image
from book_model import reconstruct, JoinPolicy
from render_text import block_html
from build import opf_metadata


def row(text, i=0, width=0.35, italic=False, x=0.2):
    r = dict(
        text=text,
        bbox=[x, 0.2 + i * 0.025, x + width, 0.22 + i * 0.025],
        column=0,
        row_id=str(i),
        font_size=11,
    )
    if italic:
        r["inline"] = [dict(start=0, end=len(text), tags=["em"])]
    return r


class TypographyTests(unittest.TestCase):
    def test_scan_artwork_ignores_visible_text_layer_overlays(self):
        image = Image.new("RGB", (100, 100), "white")
        data = io.BytesIO()
        image.save(data, format="PNG")
        doc = pymupdf.open()
        page = doc.new_page(width=100, height=100)
        page.insert_image(page.rect, stream=data.getvalue())
        page.insert_text((30, 60), "1", fontsize=40, render_mode=3)
        cropped, evidence = crop_artwork(page, pymupdf.Rect(20, 20, 80, 80))
        im = Image.open(io.BytesIO(cropped))
        self.assertEqual(im.getextrema(), ((255, 255), (255, 255), (255, 255)))
        self.assertEqual(evidence["method"], "embedded-page-scan")
        page.insert_text((30, 80), "Caption", fontsize=8)
        _, labeled = crop_artwork(page, pymupdf.Rect(20, 20, 90, 90))
        self.assertEqual(labeled["method"], "rendered-page")
        vector = pymupdf.open()
        p = vector.new_page(width=100, height=100)
        p.insert_text((30, 60), "1", fontsize=40)
        _, fallback = crop_artwork(p, pymupdf.Rect(20, 20, 80, 80))
        self.assertEqual(fallback["method"], "rendered-page")

    def test_reviewed_overlay_filter_preserves_captions_and_source(self):
        doc = pymupdf.open()
        page = doc.new_page(width=100, height=100)
        page.insert_text((30, 60), "1", fontsize=40, fontname="hebo")
        page.insert_text((25, 90), "Caption", fontsize=8)
        rules = [dict(font="Helvetica-Bold", min_size=35, pattern="[1I]")]
        data, evidence = crop_artwork(
            page, pymupdf.Rect(20, 15, 80, 65), excluded_overlays=rules
        )
        self.assertEqual(len(evidence["excluded_overlays"]), 1)
        self.assertEqual(
            Image.open(io.BytesIO(data)).getextrema(),
            ((255, 255), (255, 255), (255, 255)),
        )
        labeled, _ = crop_artwork(
            page, pymupdf.Rect(20, 15, 80, 95), excluded_overlays=rules
        )
        self.assertLess(Image.open(io.BytesIO(labeled)).getextrema()[0][0], 200)
        self.assertIn("1", page.get_text())
        self.assertIn("Caption", page.get_text())

    def test_pharmako_artifact_keeps_prose_between_separate_ideograms(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "output/pharmako-poeia/book-model.json"
        )
        if not path.exists():
            self.skipTest("Build Pharmako fixture first")
        model = json.loads(path.read_text())
        text = " ".join(b["text"] for c in model for b in c["blocks"])
        self.assertEqual(len(model), 54)
        self.assertEqual(
            sum(b["kind"] == "figure" for c in model for b in c["blocks"]), 110
        )
        self.assertIn(
            'ideograms for "male hemp," "female hemp," and "hemp fruits."', text
        )
        self.assertIn("why destroying wild habitat is parricide", text)
        self.assertEqual(model[9]["blocks"][-1]["kind"], "verse")

    def test_whole_line_style_survives_verified_ocr_spelling_difference(self):
        source = row("The italicized passage continues.", italic=True)
        target = row("The italicised passage continues.")
        audit = []
        with patch("typography.extract_page", return_value=[source]):
            attach_typography([target], None, 1, audit)
        self.assertEqual(target["text"], "The italicised passage continues.")
        self.assertEqual(
            target["inline"], [dict(start=0, end=len(target["text"]), tags=["em"])]
        )
        self.assertGreater(audit[0]["agreement"], 0.85)

    def test_partial_style_is_mapped_after_inserted_characters(self):
        source = row("An old botanical name follows.")
        source["inline"] = [dict(start=7, end=21, tags=["em"])]
        target = row("An older botanical name follows.")
        with patch("typography.extract_page", return_value=[source]):
            attach_typography([target], None, 1, [])
        fragments = [target["text"][s["start"] : s["end"]] for s in target["inline"]]
        self.assertEqual("".join(fragments), "botanical name")
        self.assertTrue(all(s["start"] >= 9 for s in target["inline"]))

    def test_unrelated_hidden_ocr_does_not_supply_typography(self):
        target = row("Fresh reliable text from the scan.")
        with patch(
            "typography.extract_page",
            return_value=[row("Unrelated mangled words.", italic=True)],
        ):
            attach_typography([target], None, 1, [])
        self.assertNotIn("inline", target)

    def test_large_side_glyph_does_not_dominate_body_font_size(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text(
            (50, 100), "A normal line with many ordinary letters", fontsize=11
        )
        page.insert_text((400, 100), "P", fontsize=30)
        rows = extract_page(page, {}, 1, [])
        body = next(r for r in rows if "ordinary" in r["text"])
        self.assertEqual(body["font_size"], 11)

    def test_small_cap_table_label_is_separate_from_its_value(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 100), "ELEMENT", fontsize=8)
        page.insert_text((110, 100), "Wood", fontsize=11)
        source = extract_page(page, {}, 1, [])
        self.assertEqual(source[0]["label_end"], len("ELEMENT"))
        target = dict(source[0])
        target.pop("label_end")
        target.pop("inline")
        with patch("typography.extract_page", return_value=source):
            attach_typography([target], None, 1, [])
        self.assertTrue(target["paragraph_start"])
        self.assertEqual(target["inline"][0]["tags"], ["strong"])

    def test_foreword_credit_and_incomplete_source_are_metadata(self):
        book = dict(
            title="A book",
            author="Author",
            language="en",
            date="1995",
            publisher="Publisher",
            source_note="The supplied scan lacks the references.",
            contributors=[
                dict(name="Foreword writer", role="aui", file_as="Writer, Foreword")
            ],
        )
        node = etree.fromstring(opf_metadata(book, {}, "urn:test"))
        ns = {
            "dc": "http://purl.org/dc/elements/1.1/",
            "opf": "http://www.idpf.org/2007/opf",
        }
        self.assertEqual(node.find("dc:creator", ns).text, "Author")
        self.assertEqual(node.find("dc:contributor", ns).text, "Foreword writer")
        self.assertEqual(
            node.xpath(
                'opf:meta[@refines="#contributor-1"][@property="role"]/text()',
                namespaces=ns,
            ),
            ["aui"],
        )
        self.assertIn("lacks the references", node.find("dc:description", ns).text)

    def test_poem_dash_is_not_treated_as_a_split_word(self):
        rows = [
            row(t, i, 0.25 + (i % 3) * 0.06)
            for i, t in enumerate(
                [
                    "Come you wandering plants",
                    "and all the forgotten ones",
                    "Come forth you motley troop -",
                    "not a gentleman among you —",
                    "Not one that will remain",
                    "a guest in the garden",
                    "Come all you restless ones",
                    "Be fruitful in the morning",
                ]
            )
        ]
        self.assertEqual(verse_evidence(rows, 0.8), {str(i) for i in range(8)})

    def test_wrapped_italic_prose_and_short_roman_prose_are_rejected(self):
        wrapped = [
            row(
                "A prose sentence continues here",
                i,
                0.75 if i < 5 else 0.25,
                italic=True,
            )
            for i in range(6)
        ]
        roman = [
            row("An ordinary short paragraph", i, 0.4 - i * 0.04) for i in range(4)
        ]
        self.assertFalse(verse_evidence(wrapped, 0.8))
        self.assertFalse(verse_evidence(roman, 0.8))

    def test_alternating_short_lines_and_stanza_gap_preserve_structure(self):
        rows = [
            row(
                "A short poetic line continues",
                i,
                0.25 + (i % 3) * 0.06,
                italic=True,
                x=0.2 + (i % 2) * 0.05,
            )
            for i in range(6)
        ]
        for r in rows[3:]:
            r["bbox"][1] += 0.08
            r["bbox"][3] += 0.08
        book = dict(chapters=[[1, 1, "Poem"]], chapter_body_starts={"1": 0})
        model = reconstruct({1: rows}, book, JoinPolicy(), [])
        self.assertEqual([b["kind"] for b in model[0]["blocks"]], ["verse", "verse"])
        block = model[0]["blocks"][0]
        html = block_html(block, book, set(), [], "chapter.xhtml")
        self.assertEqual(html.count("<br/>"), 2)
        self.assertIn("padding-left:1.5em", html)

    def test_artwork_mask_prevents_side_letter_joining_prose(self):
        lines = [
            row("P", width=0.02, x=0.05),
            row("The complete body line remains", width=0.6),
        ]
        book = dict(
            figures=[dict(page=1, name="ornament", rect=[0.04, 0.15, 0.09, 0.9])]
        )
        audit = []
        result = merge_rows(outside_figures(lines, book, 1, audit))
        self.assertEqual(result[0]["text"], "The complete body line remains")
        self.assertEqual(audit[0]["figure"], "ornament")

    def test_garbled_tall_row_is_replaced_by_two_fresh_consensus_lines(self):
        primary = [dict(text="garbled row", bbox=[0.2, 0.2, 0.7, 0.24], confidence=1)]
        peers = [
            dict(
                text="The first reliable printed line",
                bbox=[0.2, 0.201, 0.7, 0.217],
                confidence=0.96,
            ),
            dict(
                text="And the second reliable printed line",
                bbox=[0.2, 0.222, 0.7, 0.238],
                confidence=0.96,
            ),
        ]
        audit = []
        result = correct_page(
            primary, [], peers, peers, audit, [], 1, recover_regions=True
        )
        self.assertEqual([r["text"] for r in result], [r["text"] for r in peers])
        self.assertEqual(audit[0]["kind"], "two-witness-region-recovery")
        original = correct_page(primary, [], peers, peers, [], [], 1)
        self.assertEqual(original[0]["text"], "garbled row")
        disagree = [dict(peers[0], text="A different reading"), peers[1]]
        unchanged = correct_page(
            primary, [], peers, disagree, [], [], 1, recover_regions=True
        )
        self.assertEqual(unchanged[0]["text"], "garbled row")

    def test_illustrated_page_has_one_anchor_even_with_two_figures(self):
        block = dict(
            kind="figure",
            text="",
            page_breaks=[dict(page=3, offset=0)],
            image="a.jpg",
            alt="Drawing",
        )
        links = []
        seen = set()
        first = block_html(
            block, dict(page_labels={"3": "xiii"}), seen, links, "chapter.xhtml"
        )
        second = block_html(block, {}, seen, links, "chapter.xhtml")
        self.assertIn('aria-label="xiii"', first)
        self.assertNotIn('id="page-3"', second)
        self.assertEqual(links, [("chapter.xhtml#page-3", "xiii")])


if __name__ == "__main__":
    unittest.main()
