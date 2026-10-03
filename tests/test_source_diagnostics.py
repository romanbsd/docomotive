import sys
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scan_pixels import isolated_scan
from source_diagnostics import inspect_page


class SourceDiagnosticsTests(unittest.TestCase):
    def scanned_page(self):
        original = pymupdf.open()
        page = original.new_page(width=300, height=420)
        for i in range(20):
            page.insert_text(
                (30, 35 + i * 17), "Original printed page text", fontsize=10
            )
        pixels = page.get_pixmap(dpi=150).tobytes("png")
        original.close()
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=420)
        page.insert_image(page.rect, stream=pixels)
        self.addCleanup(doc.close)
        return page, pixels

    def test_hidden_ocr_and_genuine_caption_do_not_trigger(self):
        page, _ = self.scanned_page()
        for i in range(40):
            page.insert_text((30, 40 + i * 8), "hidden", fontsize=8, render_mode=3)
        page.insert_text((30, 400), "Genuine caption", fontsize=10)
        self.assertEqual(inspect_page(page)["status"], "no-dense-painted-text")

    def test_dense_visible_overlay_triggers_pixel_diagnostic(self):
        page, _ = self.scanned_page()
        for i in range(40):
            page.insert_text((35, 38 + i * 8), "bad reconstruction", fontsize=8)
        result = inspect_page(page)
        self.assertEqual(result["status"], "possible-reconstructed-overlay")
        self.assertGreaterEqual(result["added_ink_pixels"], 500)

    def test_dense_hidden_ocr_is_not_visible_damage(self):
        page, _ = self.scanned_page()
        for i in range(40):
            page.insert_text(
                (35, 38 + i * 8), "hidden reconstruction", fontsize=8, render_mode=3
            )
        self.assertEqual(inspect_page(page)["status"], "no-dense-painted-text")

    def test_multiple_placed_scans_fail_closed(self):
        page, pixels = self.scanned_page()
        page.insert_image(page.rect, stream=pixels)
        with self.assertRaises(ValueError):
            isolated_scan(page, page.rect)

    def test_painted_text_occluded_by_scan_does_not_trigger(self):
        _, pixels = self.scanned_page()
        doc = pymupdf.open()
        self.addCleanup(doc.close)
        page = doc.new_page(width=300, height=420)
        for i in range(40):
            page.insert_text((35, 38 + i * 8), "covered reconstruction", fontsize=8)
        page.insert_image(page.rect, stream=pixels)
        self.assertEqual(inspect_page(page)["status"], "no-substantial-added-ink")


if __name__ == "__main__":
    unittest.main()
