"""Italic recovery needs source glyph consensus, not quotation semantics."""

import copy
import sys
import unittest
from pathlib import Path

import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from native_pdf import extract_page
from scan_italics import line_slant, italic_evidence, attach_scan_italics


def sample(font="tiro", text="A small part of the natural world."):
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=70)
        page.insert_text((10, 40), text, fontname=font, fontsize=14)
        rect = pymupdf.Rect(page.get_text("dict")["blocks"][0]["bbox"])
        return line_slant(page.get_pixmap(dpi=400, clip=rect).pil_image())


class ScanItalicTests(unittest.TestCase):
    def test_serif_italic_passes_but_roman_bold_and_sans_prose_do_not(self):
        upright = sample()
        self.assertTrue(italic_evidence(sample("tiit"), [upright, upright])["accepted"])
        for font in ("tiro", "tibo", "helv"):
            with self.subTest(font=font):
                self.assertFalse(
                    italic_evidence(sample(font), [upright, upright])["accepted"]
                )

    def test_short_blank_and_faint_crops_abstain(self):
        for evidence in (
            sample(text="I"),
            line_slant(Image.new("L", (100, 25), 255)),
            line_slant(Image.new("L", (100, 25), 240)),
        ):
            self.assertEqual(evidence["status"], "uncertain")

    def test_skewed_controls_and_missing_controls_abstain(self):
        upright, italic = sample(), sample("tiit")
        self.assertFalse(italic_evidence(italic, [upright])["accepted"])
        skewed = dict(upright, median_shear=0.25)
        self.assertFalse(italic_evidence(italic, [skewed, skewed])["accepted"])

    def test_mixed_style_requires_broad_glyph_agreement(self):
        upright, italic = sample(), sample("tiit")
        mixed = copy.deepcopy(italic)
        for reading in mixed["readings"][: len(mixed["readings"]) // 2]:
            reading.update(shear=0, gain=1)
        self.assertFalse(italic_evidence(mixed, [upright, upright])["accepted"])

    def test_whole_line_recovery_preserves_credit_text_and_existing_spans(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=500)
            page.insert_text(
                (85, 170),
                "A small part of the natural world.",
                fontname="tiit",
                fontsize=12,
            )
            page.insert_text((160, 190), "ANOTHER AUTHOR", fontname="tiro", fontsize=9)
            for y in (270, 295, 320):
                page.insert_text(
                    (20, y),
                    "This is upright surrounding prose that supplies a reliable reference.",
                    fontname="tiro",
                    fontsize=12,
                )
            rows = extract_page(page, {}, 1, [])
            # Simulate fresh OCR: source font flags and names are unavailable.
            for row in rows:
                row.pop("inline", None)
            quote = next(r for r in rows if r["text"].startswith("A small"))
            credit = next(r for r in rows if r["text"] == "ANOTHER AUTHOR")
            quote["inline"] = [dict(start=0, end=1, tags=["strong"])]
            before = [r["text"] for r in rows]
            audit = []
            attach_scan_italics(rows, page, 1, {}, audit)
            self.assertEqual(audit, [])
            attach_scan_italics(
                rows, page, 1, dict(verse_regions={"1": [[0.3, 0.4]]}), audit
            )
            self.assertIn(
                dict(start=0, end=len(quote["text"]), tags=["em"]), quote["inline"]
            )
            self.assertIn(dict(start=0, end=1, tags=["strong"]), quote["inline"])
            self.assertFalse(credit.get("inline"))
            self.assertEqual([r["text"] for r in rows], before)
            self.assertEqual(sum(a["accepted"] for a in audit), 1)
