"""Mixed full-width introductions and column text retain order and source boxes."""

import sys
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from page_columns import page_clips, page_box
from ocr import embedded, merge_rows
from native_pdf import extract_page
from profile_validation import validate_profile


class PageColumnsTests(unittest.TestCase):
    def setUp(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=400, height=600)
        self.page.insert_text(
            (40, 80), "A full-width introduction above the index columns."
        )
        self.page.insert_text((40, 200), "Left entry 12")
        self.page.insert_text((240, 200), "Right entry 34")
        self.book = dict(
            title="Book",
            author="Author",
            language="en",
            slug="book",
            source_sha256="a" * 64,
            chapters=[[1, 1, "Index"]],
            index_splits={"1": 0.5},
            index_column_starts={"1": 0.24},
        )

    def tearDown(self):
        self.doc.close()

    def test_clips_cover_header_and_columns_without_overlap(self):
        validate_profile(self.book, page_count=1)
        clips = page_clips(self.page, self.book, 1)
        self.assertEqual(
            [list(c) for c in clips],
            [
                [0, 0, 400, 144],
                [0, 144, 200, 600],
                [200, 144, 400, 600],
            ],
        )
        self.assertEqual(sum(c.get_area() for c in clips), self.page.rect.get_area())
        self.assertEqual(page_box([0, 0, 1, 1], clips[2], self.page), [0.5, 0.24, 1, 1])
        self.assertEqual(
            page_box([0.2, 0.25, 0.8, 0.5], clips[1], self.page), [0.1, 0.43, 0.4, 0.62]
        )

    def test_embedded_and_native_order_agree_and_keep_columns_separate(self):
        rows = embedded(self.page, 0.5, column_start=0.24)
        native = extract_page(self.page, self.book, 1, [])
        for data in [rows, merge_rows(native)]:
            self.assertEqual([r["column"] for r in data], [-1, 0, 1])
            self.assertEqual(
                [r["text"] for r in data],
                [
                    "A full-width introduction above the index columns.",
                    "Left entry 12",
                    "Right entry 34",
                ],
            )

    def test_default_and_full_height_column_clips_remain_unchanged(self):
        self.assertEqual(page_clips(self.page, {}, 1), [self.page.rect])
        book = dict(self.book, index_column_starts={})
        self.assertEqual(
            [list(c) for c in page_clips(self.page, book, 1)],
            [
                [0, 0, 200, 600],
                [200, 0, 400, 600],
            ],
        )
        for invalid in [0, 1, -0.1, 1.1, True]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_profile(dict(self.book, index_column_starts={"1": invalid}))
        with self.assertRaisesRegex(ValueError, "requires a column split"):
            validate_profile(dict(self.book, index_splits={}))


if __name__ == "__main__":
    unittest.main()
