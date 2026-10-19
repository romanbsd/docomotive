"""Detached accents require both glyph identity and overlapping placement."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from native_pdf import extract_page
from native_accents import two_raised_dots


class NativeAccentTests(unittest.TestCase):
    def test_overlapping_diaeresis_preserves_style_and_offsets(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            text = "fu¨r and more"
            chars = [
                dict(
                    c=c,
                    origin=(50 + i * 6, 100),
                    bbox=(50 + i * 6, 90, 56 + i * 6, 103),
                )
                for i, c in enumerate(text)
            ]
            chars[2].update(origin=(56.4, 100), bbox=(56.4, 90, 62.4, 100))
            span = dict(text=text, font="Helvetica-Oblique", size=12, flags=2)
            line = dict(bbox=(50, 90, 130, 103), spans=[span])
            data = dict(blocks=[dict(lines=[line])])
            raw = dict(
                blocks=[dict(lines=[dict(line, spans=[dict(span, chars=chars)])])]
            )
            audit = []
            with patch.object(page, "get_text", return_value=raw):
                rows = extract_page(page, {}, 1, audit, text_data=data)
            self.assertEqual(rows[0]["text"], "für and more")
            self.assertEqual(rows[0]["inline"], [dict(start=0, end=12, tags=["em"])])
            self.assertEqual(audit[0]["kind"], "native-detached-accent")
            self.assertEqual(
                line["spans"][0]["text"], text
            )  # Shared dict is immutable.

    def test_real_currency_and_nonoverlapping_diaeresis_survive(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 100), "a ¨ a", fontsize=12)
            page.insert_font(
                fontname="Currency", fontbuffer=pymupdf.Font("helv").buffer
            )
            page.insert_text((50, 130), "€a", fontsize=12, fontname="Currency")
            rows = extract_page(page, {}, 1, [])
            self.assertIn("a ¨ a", [r["text"] for r in rows])
            self.assertIn("€a", [r["text"] for r in rows])
            self.assertFalse(any("ä" in r["text"] for r in rows))

    def test_false_currency_glyph_requires_two_raised_dots(self):
        mask = np.zeros((120, 120), dtype=np.uint8)
        mask[33:43, 20:30] = 255
        mask[33:43, 40:50] = 255
        self.assertTrue(two_raised_dots(mask, 100, 100))
        mask[40:90, 25:45] = 255
        self.assertFalse(two_raised_dots(mask, 100, 100))
