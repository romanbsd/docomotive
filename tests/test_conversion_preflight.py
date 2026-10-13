"""Conservative proposal and ebook sizing boundaries."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from conversion_preflight import (
    benchmark_page,
    chapter_ranges,
    index_split_proposal,
    printed_offset_runs,
    stratified_pages,
)
from figure_cleanup import EBOOK_MAX_EDGE, ebook_jpeg_asset, ebook_media_inventory


class ConversionPreflightTests(unittest.TestCase):
    def test_stratified_sample_keeps_edges_and_middle_of_each_kind(self):
        kinds = {n: "body" for n in range(1, 10)} | {10: "index", 11: "index"}
        self.assertEqual(stratified_pages(kinds), [1, 5, 9, 10, 11])

    def test_index_gutter_requires_two_row_clusters(self):
        left = [
            dict(bbox=[0.1, 0.1 + i * 0.02, 0.47, 0.11 + i * 0.02]) for i in range(10)
        ]
        right = [
            dict(bbox=[0.53, 0.1 + i * 0.02, 0.9, 0.11 + i * 0.02]) for i in range(10)
        ]
        self.assertAlmostEqual(index_split_proposal(left + right)["split"], 0.5)
        self.assertIsNone(index_split_proposal(left))

    def test_chapter_and_printed_offset_proposals_remain_review_only(self):
        pages = [
            dict(
                page=n,
                kind="body",
                headings=[dict(text=f"CHAPTER {n}", bbox=[0.3, 0.2, 0.7, 0.23])],
                printed_labels=[dict(text=str(n - 4))],
            )
            for n in (5, 8, 12)
        ]
        self.assertEqual(
            [(r["start"], r["end"]) for r in chapter_ranges(pages, 20)],
            [(5, 7), (8, 11), (12, 20)],
        )
        self.assertEqual(
            printed_offset_runs(pages),
            [dict(offset=-4, pages=[5, 8, 12], status="source-review-required")],
        )

    def test_ocr_peer_ranking_abstains_on_near_tie(self):
        with tempfile.TemporaryDirectory() as folder:
            caches = {}
            for name, text in (
                ("tesseract", "A clear line"),
                ("vision", "A clear line"),
                ("rapid", "A clear line"),
            ):
                path = Path(folder) / name
                path.mkdir()
                (path / "0001.json").write_text(
                    json.dumps(
                        dict(
                            page=1,
                            width=100,
                            height=100,
                            lines=[
                                dict(
                                    text=text, bbox=[0.1, 0.1, 0.8, 0.2], confidence=0.9
                                )
                            ],
                        )
                    )
                )
                caches[name] = path
            row = benchmark_page(1, caches)
            self.assertIsNone(row["review_first"])
            self.assertEqual(row["status"], "abstained")

    def test_oversized_jpeg_is_bounded_and_small_asset_unchanged(self):
        image = Image.new("RGB", (2400, 900), "white")
        stream = io.BytesIO()
        image.save(stream, format="JPEG", quality=95)
        result, evidence = ebook_jpeg_asset(stream.getvalue())
        self.assertTrue(evidence["resized"])
        self.assertEqual(Image.open(io.BytesIO(result)).size, (EBOOK_MAX_EDGE, 600))
        again, evidence = ebook_jpeg_asset(result)
        self.assertEqual(again, result)
        self.assertFalse(evidence["resized"])
        with self.assertRaisesRegex(ValueError, "Oversized EPUB image"):
            ebook_media_inventory({"OEBPS/large.jpg": stream.getvalue()})
        self.assertEqual(
            ebook_media_inventory({"OEBPS/resized.jpg": result})[0]["pixels"],
            [EBOOK_MAX_EDGE, 600],
        )


if __name__ == "__main__":
    unittest.main()
