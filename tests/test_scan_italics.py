"""Italic recovery needs source glyph consensus, not quotation semantics."""

import copy
import sys
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path

import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from native_pdf import extract_page
from scan_italics import (
    line_slant,
    italic_evidence,
    attach_scan_italics,
    inline_italic_spans,
    attach_scan_inline_italics,
    aligned_word,
)


def sample(font="tiro", text="A small part of the natural world."):
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=70)
        page.insert_text((10, 40), text, fontname=font, fontsize=14)
        rect = pymupdf.Rect(page.get_text("dict")["blocks"][0]["bbox"])
        return line_slant(page.get_pixmap(dpi=400, clip=rect).pil_image())


class ScanItalicTests(unittest.TestCase):
    def test_style_alignment_accepts_single_ocr_edits_without_spelling_rewrites(self):
        for source, witness in (
            ("example", "exarnple"),
            ("small", "smal"),
            ("word", "w0rd"),
        ):
            with self.subTest(source=source, witness=witness):
                expected = (source, witness) != ("example", "exarnple")
                self.assertEqual(aligned_word(source, witness), expected)
        self.assertFalse(aligned_word("of", "or"))
        self.assertFalse(aligned_word("foreign", "unrelated"))

    def test_inline_cache_replays_styles_and_negative_results_with_input_binding(self):
        for spans in ([], [dict(start=0, end=6, tags=["em"], measurement={})]):
            with (
                self.subTest(spans=spans),
                tempfile.TemporaryDirectory() as work,
                pymupdf.open() as doc,
            ):
                page = doc.new_page(width=400, height=400)
                rows = [
                    dict(
                        text="Sample prose supplies the upright page reference.",
                        bbox=[0.1, y, 0.9, y + 0.04],
                    )
                    for y in (0.2, 0.3)
                ]
                book = dict(
                    chapters=[[1, 1, "Chapter"]],
                    chapter_body_starts={"1": 0},
                    source_sha256="a" * 64,
                )
                with (
                    patch("scan_italics.inline_runtime", return_value={"version": 1}),
                    patch(
                        "scan_italics.crop_artwork_pixels",
                        return_value=(Image.new("L", (400, 400), 255), {}),
                    ) as pixels,
                    patch(
                        "scan_italics.line_slant",
                        return_value={"status": "measured", "median_shear": 0},
                    ),
                    patch(
                        "scan_italics.inline_italic_spans", return_value=spans
                    ) as probe,
                ):
                    first = copy.deepcopy(rows)
                    first_audit = []
                    attach_scan_inline_italics(
                        first, page, 1, book, first_audit, work=work
                    )
                    calls = probe.call_count
                    warm = copy.deepcopy(rows)
                    warm_audit = []
                    attach_scan_inline_italics(
                        warm, page, 1, book, warm_audit, work=work
                    )
                    self.assertEqual(warm, first)
                    self.assertEqual(warm_audit, first_audit)
                    self.assertEqual(probe.call_count, calls)
                    self.assertEqual(pixels.call_count, 1)
                    attach_scan_inline_italics(
                        copy.deepcopy(rows),
                        page,
                        1,
                        dict(
                            book,
                            title="Different title",
                            cover_file={"path": "new-cover.jpg"},
                        ),
                        [],
                        work=work,
                    )
                    self.assertEqual(probe.call_count, calls)
                    attach_scan_inline_italics(
                        copy.deepcopy(rows),
                        page,
                        1,
                        dict(book, source_sha256="b" * 64),
                        [],
                        work=work,
                    )
                    self.assertGreater(probe.call_count, calls)

    def test_inline_words_use_pixels_and_preserve_upright_neighbors(self):
        for font in ("tiit", "tiro", "tibo"):
            with self.subTest(font=font), pymupdf.open() as doc:
                page = doc.new_page(width=500, height=70)
                x = 10
                for text, face in (
                    ("Upright words ", "tiro"),
                    ("foreign phrase", font),
                    (" neighboring words.", "tiro"),
                ):
                    page.insert_text((x, 40), text, fontname=face, fontsize=14)
                    x += pymupdf.get_text_length(text, fontname=face, fontsize=14)
                im = page.get_pixmap(dpi=400).pil_image()
                bbox = page.get_text("dict")["blocks"][0]["bbox"]
                origin = [round(bbox[0] * 400 / 72), round(bbox[1] * 400 / 72)]
                im = im.crop(
                    (
                        origin[0],
                        origin[1],
                        round(bbox[2] * 400 / 72),
                        round(bbox[3] * 400 / 72),
                    )
                )
                raw = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"]
                chars = []
                text = ""
                for span in raw:
                    for char in span["chars"]:
                        b = char["bbox"]
                        chars.append(
                            dict(
                                start=len(text),
                                end=len(text) + 1,
                                bbox=[
                                    round(v * 400 / 72) - origin[i % 2]
                                    for i, v in enumerate(b)
                                ],
                            )
                        )
                        text += char["c"]
                controls = [sample(), sample()]
                recognizer = lambda image, **kwargs: (text, chars)
                spans = inline_italic_spans(im, text, controls, recognizer)
                if font == "tiit":
                    self.assertEqual(
                        [text[s["start"] : s["end"]] for s in spans], ["foreign phrase"]
                    )
                else:
                    self.assertFalse(spans)
                self.assertFalse(
                    inline_italic_spans(im, text, [controls[0]], recognizer)
                )
                self.assertFalse(
                    inline_italic_spans(
                        im,
                        text,
                        controls,
                        lambda image, **kwargs: ("unrelated words", []),
                    )
                )

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
