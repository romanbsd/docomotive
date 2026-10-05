"""Paper removal must preserve faint ink and photograph interiors."""

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from figure_cleanup import clean_figure, jpeg_export, export_color
from figures import incorporate_figures
from common import digest, write_json
from verify_figure_cleanup import verify_cleanup


def encoded(array, mode=None):
    output = io.BytesIO()
    Image.fromarray(array, mode=mode).save(output, format="PNG")
    return output.getvalue()


def decoded(data):
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def paper(size=320):
    yy, xx = np.mgrid[:size, :size]
    shade = 5 * xx / size + 3 * (yy / size) ** 2
    return np.rint(np.array([229, 223, 213]) + shade[:, :, None]).astype(np.uint8)


class FigureCleanupTests(unittest.TestCase):
    def test_auto_grayscale_keeps_even_small_colored_marks(self):
        source = np.full((64, 64, 3), 240, dtype=np.uint8)
        source[20:40, 20:40] = 80
        image = Image.fromarray(source)
        result, evidence = export_color(image)
        self.assertEqual(result.mode, "L")
        self.assertEqual(evidence["reason"], "neutral-pixels")
        source[30, 30] = [80, 110, 80]
        image = Image.fromarray(source)
        result, evidence = export_color(image)
        self.assertEqual(result.mode, "RGB")
        self.assertTrue(np.array_equal(np.asarray(result), source))
        result, evidence = export_color(image, "grayscale")
        self.assertEqual(result.mode, "L")
        self.assertEqual(evidence["reason"], "reviewed-monochrome")
        self.assertEqual(export_color(Image.fromarray(source[:, :, 0]))[0].mode, "L")
        self.assertEqual(
            export_color(Image.fromarray(source[:, :, 0]), "rgb")[0].mode, "RGB"
        )
        with self.assertRaises(ValueError):
            export_color(image, "invalid")

    def test_progressive_export_preserves_baseline_decoded_pixels(self):
        image = Image.fromarray(paper())
        for quality in (95, 98):
            baseline = io.BytesIO()
            image.save(
                baseline, format="JPEG", quality=quality, subsampling=0, optimize=True
            )
            result = jpeg_export(image, quality=quality)
            self.assertTrue(Image.open(io.BytesIO(result)).info.get("progressive"))
            self.assertTrue(
                np.array_equal(decoded(result), decoded(baseline.getvalue()))
            )
            self.assertEqual(result, jpeg_export(image, quality=quality))

    def test_shaded_diagram_whitens_paper_without_erasing_faint_gray_strokes(self):
        pixels = paper()
        original = pixels.copy()
        # Both isolated hairlines and gray interiors matter, not just dark text.
        pixels[55:57, 35:285] -= 5
        pixels[105:107, 35:285] -= 12
        pixels[150:153, 35:285] -= 70
        pixels[205:240, 110:150] -= 9
        data = encoded(pixels)
        cleaned, evidence = clean_figure(data)
        result = decoded(cleaned).mean(axis=2)
        self.assertEqual(evidence["status"], "applied")
        self.assertEqual(evidence["kind"], "line-art")
        background = result[25, 35:285].mean()
        self.assertGreater(background, 253)
        for y, contrast in [(55, 3), (105, 9), (150, 60)]:
            self.assertGreater(background - result[y, 35:285].mean(), contrast)
        self.assertGreater(background - result[210:230, 115:145].mean(), 6)
        self.assertEqual(clean_figure(data)[0], cleaned)
        self.assertEqual(clean_figure(data)[1], evidence)
        self.assertTrue(np.array_equal(pixels[25], original[25]))

    def test_photo_highlights_and_halftones_are_retained_pixel_for_pixel(self):
        pixels = paper()
        yy, xx = np.mgrid[:240, :260]
        halftone = np.where((xx + yy) % 2, 80, 160).astype(np.uint8)
        pixels[40:280, 30:290] = halftone[:, :, None]
        # A bright region connected to the photo edge must not become paper.
        pixels[40:110, 110:190] = [229, 226, 220]
        data = encoded(pixels)
        cleaned, e = clean_figure(data)
        result = decoded(cleaned)
        self.assertEqual(e["status"], "applied")
        self.assertEqual(e["kind"], "photograph-or-dense-artwork")
        self.assertTrue(e["protected_pixels_unchanged"])
        self.assertTrue(np.array_equal(result[40:280, 30:290], pixels[40:280, 30:290]))
        self.assertGreater(result[10, 100:200].mean(), 253)

    def test_single_narrow_photo_margin_uses_one_dimensional_field(self):
        pixels = paper()
        pixels[:, :305] = 100
        data = encoded(pixels)
        result, e = clean_figure(data)
        self.assertEqual(e["status"], "applied")
        self.assertEqual(e["field_basis"], "quadratic-y")
        self.assertTrue(np.array_equal(decoded(result)[:, :305], pixels[:, :305]))
        self.assertGreater(decoded(result)[:, 310:].mean(), 253)

    def test_uncertain_colored_alpha_dark_and_small_inputs_are_unchanged(self):
        colored = paper()
        colored[50:250, 50:250] = [210, 20, 30]
        cases = [
            (encoded(colored), "colored-artwork"),
            (
                encoded(np.full((320, 320, 3), 90, dtype=np.uint8)),
                "no-light-neutral-paper",
            ),
            (
                encoded(np.full((320, 320, 4), 220, dtype=np.uint8), "RGBA"),
                "unsupported-color-or-alpha-mode",
            ),
            (encoded(paper(32)), "insufficient-pixels"),
            (encoded(np.full((320, 320, 3), 255, dtype=np.uint8)), "already-white"),
        ]
        for data, reason in cases:
            with self.subTest(reason=reason):
                result, e = clean_figure(data)
                self.assertEqual(result, data)
                self.assertEqual(e["status"], "skipped")
                self.assertEqual(e["reason"], reason)

    def test_nonfinite_fits_abstain_without_emitting_modified_pixels(self):
        data = encoded(paper())
        with patch(
            "figure_cleanup.np.linalg.lstsq",
            return_value=(np.full((6, 3), np.nan), None, None, None),
        ):
            result, e = clean_figure(data)
        self.assertEqual(result, data)
        self.assertEqual(e["status"], "skipped")

    def test_low_contrast_dense_artwork_is_not_mistaken_for_blank_paper(self):
        image = paper()
        image[30:290, 30:290] -= 12
        data = encoded(image)
        result, evidence = clean_figure(data)
        self.assertEqual(evidence["kind"], "photograph-or-dense-artwork")
        self.assertEqual(evidence["status"], "skipped")
        self.assertEqual(result, data)

    def test_opt_in_export_cleans_direct_pixels_and_retains_lossless_review_assets(
        self,
    ):
        with pymupdf.open() as doc, tempfile.TemporaryDirectory() as folder:
            page = doc.new_page(width=320, height=320)
            pixels = paper()
            pixels[100:104, 50:250] = 100
            page.insert_image(page.rect, stream=encoded(pixels))
            book = dict(
                figure_cleanup="white",
                figures=[
                    dict(page=1, rect=[0, 0, 1, 1], name="diagram", alt="Diagram")
                ],
            )
            model = [dict(chapter=1, source_pages=[1, 1], blocks=[])]
            files = {}
            # Any call to the legacy JPEG cropper would reintroduce quantization
            # before normalization. The opt-in path must use raw pixel crops.
            with patch(
                "figures.crop_artwork", side_effect=AssertionError("intermediate JPEG")
            ):
                report = incorporate_figures(
                    model, book, doc, files, cleanup_dir=Path(folder)
                )
            self.assertEqual(report[0]["image"], "diagram.jpg")
            original = (Path(folder) / "originals/diagram.png").read_bytes()
            self.assertTrue(original.startswith(b"\x89PNG"))
            self.assertTrue(np.array_equal(decoded(original), pixels))
            self.assertEqual(
                files["OEBPS/diagram.jpg"],
                (Path(folder) / "results/diagram.jpg").read_bytes(),
            )
            self.assertEqual(model[0]["blocks"][0]["image"], "diagram.jpg")
            evidence = json.loads((Path(folder) / "report.json").read_text())
            self.assertEqual(evidence["figures"][0]["cleanup"], report[0]["cleanup"])
            self.assertEqual(report[0]["cleanup"]["jpeg"]["quality"], 98)
            self.assertIn(
                "originals/diagram.png", (Path(folder) / "index.html").read_text()
            )
            self.assertNotIn("OEBPS/diagram.png", files)
            self.assertTrue((Path(folder) / "lossless/diagram.png").exists())
            book["figure_cleanup"] = "none"
            with patch(
                "figure_cleanup.normalize_paper",
                side_effect=AssertionError("default cleanup"),
            ):
                old_files = {}
                incorporate_figures(
                    [dict(chapter=1, source_pages=[1, 1], blocks=[])],
                    book,
                    doc,
                    old_files,
                )
            legacy = io.BytesIO()
            Image.open(io.BytesIO(original)).save(legacy, format="JPEG", quality=95)
            self.assertEqual(old_files["OEBPS/diagram.jpg"], legacy.getvalue())

    def test_cleanup_requires_original_retention_and_safe_names(self):
        with pymupdf.open() as doc:
            doc.new_page()
            book = dict(figure_cleanup="white", figures=[])
            with self.assertRaisesRegex(ValueError, "review directory"):
                incorporate_figures([], book, doc, {})
            book["figure_cleanup"] = "transparent"
            with self.assertRaisesRegex(ValueError, "policy"):
                incorporate_figures([], book, doc, {})
            book.update(figure_cleanup="white", figures=[dict(name="../escape")])
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaisesRegex(ValueError, "Unsafe figure name"):
                    incorporate_figures([], book, doc, {}, cleanup_dir=folder)
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_independent_verifier_rejects_changed_photo_pixels_even_with_valid_hashes(
        self,
    ):
        source = paper()
        source[40:280, 30:290] = 100
        original = encoded(source)
        result, evidence = clean_figure(original)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            root = out / "figure-cleanup"
            root.mkdir()
            (root / "original.png").write_bytes(original)
            rows = [
                dict(
                    name="photo",
                    original="original.png",
                    result="photo.png",
                    cleanup=evidence,
                )
            ]
            book = dict(slug="book", figures=[dict(name="photo")])

            def publish(data):
                (root / "photo.png").write_bytes(data)
                evidence["sha256"] = digest(data)
                write_json(root / "report.json", dict(figures=rows))
                with zipfile.ZipFile(out / "book.epub", "w") as archive:
                    archive.writestr("OEBPS/photo.png", data)

            publish(result)
            verified = verify_cleanup(out, book)
            self.assertTrue(verified["figures"][0]["protected_pixels_unchanged"])
            modified = decoded(result).copy()
            modified[150, 150] = 255
            publish(encoded(modified))
            with self.assertRaisesRegex(ValueError, "photographic interior changed"):
                verify_cleanup(out, book)
            self.assertEqual(
                json.loads((root / "verification.json").read_text())["status"], "failed"
            )


if __name__ == "__main__":
    unittest.main()
