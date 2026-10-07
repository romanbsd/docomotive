import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import file_digest
from source_document import open_source
from scan_pixels import isolated_scan


class SourceDocumentTests(unittest.TestCase):
    def test_pdf_needs_no_djvulibre_and_preserves_native_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72, 72), "Original native text")
                doc.save(path)
            with patch("source_document.shutil.which", return_value=None):
                with open_source(path, native=True) as doc:
                    self.assertIn("Original native text", doc[0].get_text())
                    self.assertFalse(hasattr(doc, "source_rendering"))

    def test_djvu_requires_decoder_and_rejects_native_mode(self):
        with patch("source_document.shutil.which", return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, "DjVuLibre"):
                open_source("scan.DJVU")
            with self.assertRaisesRegex(ValueError, "PDF-only"):
                open_source("scan.djv", native=True)

    @unittest.skipUnless(
        all(shutil.which(name) for name in ("c44", "djvm", "ddjvu", "djvused")),
        "DjVuLibre integration tools unavailable",
    )
    def test_real_multipage_geometry_pixels_cache_and_corruption_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = []
            for number, size in enumerate(((300, 600), (600, 300))):
                ppm = root / f"{number}.ppm"
                image = Image.new("RGB", size, "white")
                image.paste("black", (25, 35, 100, 110))
                image.save(ppm)
                page = root / f"{number}.djvu"
                subprocess.run(["c44", "-dpi", "300", str(ppm), str(page)], check=True)
                pages.append(page)
            source = root / "book with spaces.DJV"
            subprocess.run(["djvm", "-c", str(source), *map(str, pages)], check=True)
            cache = root / "cache"
            with open_source(source, cache) as doc:
                self.assertEqual(len(doc), 2)
                self.assertEqual(tuple(doc[0].rect), (0, 0, 72, 144))
                self.assertEqual(tuple(doc[1].rect), (0, 0, 144, 72))
                original = doc.source_rendering
                self.assertEqual(
                    original["fingerprint"]["source_sha256"], file_digest(source)
                )
                pdf = Path(doc.name)
                for n, page in enumerate(doc):
                    ppm = root / f"direct-{n}.ppm"
                    subprocess.run(
                        ["ddjvu", "-format=ppm", f"-page={n+1}", str(source), str(ppm)],
                        check=True,
                    )
                    scan, _ = isolated_scan(page, page.rect)
                    with Image.open(ppm) as direct:
                        self.assertEqual(scan.size, direct.size)
                        self.assertEqual(
                            scan.tobytes(), direct.convert("RGB").tobytes()
                        )
            with patch(
                "source_document.subprocess.run",
                side_effect=AssertionError("cache rerendered"),
            ):
                with open_source(source, cache) as doc:
                    self.assertEqual(doc.source_rendering, original)
            pdf.write_bytes(b"damaged cache")
            with open_source(source, cache) as doc:
                self.assertEqual(len(doc), 2)
                self.assertEqual(doc.source_rendering["pdf_sha256"], file_digest(pdf))
            # A failed decoder must not publish either a PDF or a provenance stamp.
            with patch(
                "source_document.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "ddjvu"),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    open_source(source, root / "failed")
            self.assertFalse(list((root / "failed").rglob("raster.pdf")))
            self.assertFalse(list((root / "failed").rglob("provenance.json")))
            with patch("source_document.subprocess.check_output", return_value="3\n"):
                with self.assertRaisesRegex(ValueError, "page count"):
                    open_source(source, root / "wrong-count")
            self.assertFalse(list((root / "wrong-count").rglob("provenance.json")))
            subprocess.run(
                ["djvm", "-c", str(source), *map(str, reversed(pages))], check=True
            )
            with open_source(source, cache) as doc:
                self.assertEqual(tuple(doc[0].rect), (0, 0, 144, 72))
                self.assertNotEqual(
                    doc.source_rendering["fingerprint"]["source_sha256"],
                    original["fingerprint"]["source_sha256"],
                )
                self.assertNotEqual(Path(doc.name), pdf)


if __name__ == "__main__":
    unittest.main()
