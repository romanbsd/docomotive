"""Existing-EPUB optimization preserves content and follows static dependencies."""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from lxml import etree
from PIL import Image, ImageCms

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from optimize_epub import (
    HTML,
    OPF,
    Policy,
    content_signature,
    encode_image,
    local_path,
    optimize,
    parse_xml,
    references,
    render_svg,
)


def raster(mode="RGB", size=(2400, 900), color="red", fmt="JPEG", **options):
    stream = io.BytesIO()
    Image.new(mode, size, color).save(stream, format=fmt, **options)
    return stream.getvalue()


def book(path, body, assets=None, *, extra="", cover="", styles=""):
    """Use a non-OEBPS package path, arbitrary media names and manifest comments."""
    assets = assets or {}
    rows = []
    for i, (name, (media, data)) in enumerate(assets.items()):
        properties = ' properties="cover-image"' if name == cover else ""
        rows.append(
            f'<item id="media-{i}" href="{name}" media-type="{media}"{properties}/>'
        )
    package = f"""<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">test</dc:identifier><dc:title>A different book</dc:title><dc:language>en</dc:language>{extra}</metadata>
    <manifest><!-- keep this comment --><item id="text" href="chapters/read.xhtml" media-type="application/xhtml+xml" properties="svg"/>
    <item id="css" href="styles.css" media-type="text/css"/>{''.join(rows)}</manifest>
    <spine><itemref idref="text"/></spine></package>"""
    document = f"""<?xml version="1.0" encoding="utf-8"?>
    <html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title><link rel="stylesheet" href="../styles.css"/></head>
    <body><p id="opening">Before <em>and after</em> <a id="ref" href="#note" role="doc-noteref">1</a>.</p>{body}
    <aside id="note">A note. <a href="#ref" role="doc-backlink">Back</a></aside></body></html>"""
    files = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="Library/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "Library/book.opf": package.encode(),
        "Library/chapters/read.xhtml": document.encode(),
        "Library/styles.css": styles.encode(),
        **{"Library/" + name: data for name, (media, data) in assets.items()},
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return files


def svg():
    # Real embedded payloads are large; padding makes synthetic rendering worth
    # replacing without tying the tests to SVG/JPEG compression minutiae.
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="320px" height="240px"><!--'
        + bytes(range(32, 127)) * 500
        + b'--><rect width="320" height="240" fill="red"/></svg>'
    )


class OptimizeEpubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "arbitrary title.epub"
        self.output = Path(self.temp.name) / "result.epub"

    def test_arbitrary_package_paths_cover_metadata_and_explicit_photo_budget(self):
        original = book(
            self.source,
            '<img src="../cover.jpg"/><img src="../holiday.jpg"/><img src="../diagram.jpg"/>',
            {
                "cover.jpg": ("image/jpeg", raster()),
                "holiday.jpg": ("image/jpeg", raster()),
                "diagram.jpg": ("image/jpeg", raster()),
            },
            cover="cover.jpg",
        )
        report = optimize(
            self.source, self.output, Policy(paperwhite=True, photos=("Library/*.jpg",))
        )
        with zipfile.ZipFile(self.output) as archive:
            cover = Image.open(io.BytesIO(archive.read("Library/cover.jpg")))
            self.assertEqual(cover.size, (1264, 474))
            self.assertEqual(cover.mode, "RGB")
            for name in ("holiday.jpg", "diagram.jpg"):
                image = Image.open(io.BytesIO(archive.read("Library/" + name)))
                self.assertEqual(image.size, (632, 237))
                self.assertEqual(image.mode, "L")
            self.assertEqual(
                archive.read("Library/chapters/read.xhtml"),
                original["Library/chapters/read.xhtml"],
            )
            self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
        self.assertTrue(report["content_preserved"])
        with zipfile.ZipFile(self.source) as archive:
            self.assertEqual(
                dict((n, archive.read(n)) for n in archive.namelist()), original
            )

    def test_no_implicit_photo_naming_or_grayscale_and_epub2_cover_id(self):
        book(
            self.source,
            '<img src="../f01.jpg"/><img src="../jacket.jpg"/>',
            {
                "f01.jpg": ("image/jpeg", raster()),
                "jacket.jpg": ("image/jpeg", raster()),
            },
            extra='<meta name="cover" content="media-1"/>',
        )
        report = optimize(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            for name in ("f01.jpg", "jacket.jpg"):
                image = Image.open(io.BytesIO(archive.read("Library/" + name)))
                self.assertEqual(image.size, (1264, 474))
                self.assertEqual(image.mode, "RGB")
        self.assertTrue(
            next(
                row for row in report["images"] if row["source"].endswith("jacket.jpg")
            )["cover"]
        )

    @patch("optimize_epub.render_svg", return_value=raster("L", (320, 240), 80, "PNG"))
    def test_svg_object_shared_fallback_css_reference_collision_and_text(
        self, renderer
    ):
        original = book(
            self.source,
            """<figure><object id="art" class="illustration" data="../draw.svg" type="image/svg+xml"><img alt="A real description" src="../shared.jpg"/></object><figcaption>Keep the caption.</figcaption></figure><img src="../shared.jpg"/>""",
            {
                "draw.svg": ("image/svg+xml", svg()),
                "shared.jpg": ("image/jpeg", raster()),
                "draw.optimized.png": ("image/png", raster("L", (20, 20), 0, "PNG")),
                "unused.svg": ("image/svg+xml", svg()),
            },
            styles='body { background-image: url("draw.optimized.png"); }',
        )
        report = optimize(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertIn("Library/shared.jpg", archive.namelist())
            self.assertIn("Library/draw.optimized.png", archive.namelist())
            self.assertIn("Library/draw.optimized-1.png", archive.namelist())
            self.assertNotIn("Library/unused.svg", archive.namelist())
            root = parse_xml(archive.read("Library/chapters/read.xhtml"))
            self.assertEqual(
                content_signature(root),
                content_signature(parse_xml(original["Library/chapters/read.xhtml"])),
            )
            image = root.find(".//" + HTML + 'img[@id="art"]')
            self.assertEqual(image.get("width"), "320")
            self.assertEqual(image.get("alt"), "A real description")
            self.assertEqual(image.get("class"), "illustration")
            self.assertIn("max-width:100%", image.get("style"))
            package = parse_xml(archive.read("Library/book.opf"))
            self.assertNotIn(
                "svg",
                package.find(OPF + "manifest/" + OPF + 'item[@id="text"]').get(
                    "properties", ""
                ),
            )
        self.assertIn("Library/draw.svg", report["removed_resources"])
        renderer.assert_called_once()

    @patch("optimize_epub.render_svg", return_value=raster("L", (320, 240), 80, "PNG"))
    def test_retained_svg_dependency_and_direct_image_references(self, renderer):
        book(
            self.source,
            '<img id="picture" src="../draw.svg"/>',
            {
                "draw.svg": ("image/svg+xml", svg()),
            },
            styles='body { background: url("draw.svg"); }',
        )
        optimize(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertIn("Library/draw.svg", archive.namelist())
            root = parse_xml(archive.read("Library/chapters/read.xhtml"))
            self.assertTrue(root.find(".//" + HTML + "img").get("src").endswith(".png"))
            package = parse_xml(archive.read("Library/book.opf"))
            self.assertNotIn(
                "svg",
                package.find(OPF + "manifest/" + OPF + 'item[@id="text"]').get(
                    "properties", ""
                ),
            )

    def test_complex_object_and_css_escapes_are_retained(self):
        book(
            self.source,
            '<object data="../draw.svg"><p>Meaningful fallback text.</p></object>',
            {
                "draw.svg": ("image/svg+xml", svg()),
                "space name.png": ("image/png", raster("L", (20, 20), 0, "PNG")),
            },
            styles=r"body {background:url(space\20 name.png)}",
        )
        report = optimize(self.source, self.output)
        self.assertIsNotNone(report["pruning_skipped"])
        with zipfile.ZipFile(self.output) as archive:
            self.assertIn("Library/draw.svg", archive.namelist())
            self.assertIn("Library/space name.png", archive.namelist())
            self.assertIn(
                b"Meaningful fallback text.",
                archive.read("Library/chapters/read.xhtml"),
            )

    @patch("optimize_epub.render_svg")
    def test_object_specific_author_styles_prevent_replacement(self, renderer):
        book(
            self.source,
            '<object data="../draw.svg"><img src="../fallback.jpg" alt="Art"/></object>',
            {
                "draw.svg": ("image/svg+xml", svg()),
                "fallback.jpg": ("image/jpeg", raster()),
            },
            styles="@media screen { object { width: 50%; border: 2px solid red; } }",
        )
        optimize(self.source, self.output)
        renderer.assert_not_called()
        with zipfile.ZipFile(self.output) as archive:
            self.assertIn("Library/draw.svg", archive.namelist())
            self.assertIn(b"<object", archive.read("Library/chapters/read.xhtml"))

    def test_markup_repairs_are_opt_in_and_not_book_or_count_specific(self):
        body = '<nav aria-labelledby="contents"><h2 id="contents">Contents</h2></nav><hgroup class="head"><h1>Part</h1><h1>Subtitle</h1></hgroup>'
        original = book(self.source, body)
        optimize(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(
                archive.read("Library/chapters/read.xhtml"),
                original["Library/chapters/read.xhtml"],
            )
        report = optimize(self.source, self.output, Policy(fix_markup=True), force=True)
        self.assertEqual(len(report["markup_repairs"][0]["rules"]), 2)
        with zipfile.ZipFile(self.output) as archive:
            data = archive.read("Library/chapters/read.xhtml")
            self.assertNotIn(b"hgroup", data)
            self.assertNotIn(b"aria-labelledby", data)
            self.assertIn(b'class="head"', data)

    def test_transparency_animation_icc_and_exif_orientation(self):
        data = raster("RGBA", (1600, 900), (200, 0, 0, 70), "PNG")
        encoded, _ = encode_image(data, "image/png", Policy(paperwhite=True))
        image = Image.open(io.BytesIO(encoded)).convert("RGBA")
        self.assertEqual(image.size, (1264, 711))
        self.assertEqual(image.getchannel("A").getextrema(), (70, 70))
        animated = io.BytesIO()
        frames = [Image.new("RGB", (30, 30), color) for color in ("red", "blue")]
        frames[0].save(animated, format="PNG", save_all=True, append_images=frames[1:])
        encoded, evidence = encode_image(animated.getvalue(), "image/png", Policy())
        self.assertEqual(encoded, animated.getvalue())
        self.assertIn("skipped", evidence)
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        exif = Image.Exif()
        exif[274] = 6
        data = raster(size=(2000, 1000), exif=exif, icc_profile=profile)
        encoded, _ = encode_image(data, "image/jpeg", Policy())
        self.assertEqual(Image.open(io.BytesIO(encoded)).size, (840, 1680))

    def test_uri_resolution_and_reference_detection(self):
        self.assertEqual(
            local_path("OPS/chapter.xhtml", "../art/my%20map.png#detail"),
            "art/my map.png",
        )
        self.assertIsNone(local_path("OPS/a.xhtml", "https://example.com/image.jpg"))
        self.assertEqual(
            references(
                "OPS/a.css", b'@import "b.css"; a{background:url(../my%20map.png)}'
            ),
            ["OPS/b.css", "my map.png"],
        )

    def test_input_and_existing_output_are_protected_and_failures_leave_no_output(self):
        book(self.source, "")
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "different files"):
            optimize(self.source, self.source, force=True)
        self.output.write_bytes(b"old output")
        with self.assertRaisesRegex(ValueError, "Output exists"):
            optimize(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b"old output")
        with self.assertRaisesRegex(ValueError, "JPEG quality"):
            optimize(self.source, self.output, Policy(jpeg_quality=101), force=True)
        self.assertEqual(self.source.read_bytes(), original)

    def test_image_maps_and_package_id_dependencies_are_preserved(self):
        original = book(
            self.source,
            '<img src="../mapped.jpg" usemap="#areas"/><map name="areas" id="areas"><area shape="rect" coords="0,0,2400,900" href="#opening"/></map>',
            {
                "mapped.jpg": ("image/jpeg", raster()),
                "by-id.png": ("image/png", raster("L", (10, 10), 100, "PNG")),
            },
            extra='<meta refines="#media-1" property="description">A package reference</meta>',
        )
        optimize(self.source, self.output, Policy(paperwhite=True))
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(
                archive.read("Library/mapped.jpg"), original["Library/mapped.jpg"]
            )
            self.assertIn("Library/by-id.png", archive.namelist())

    def test_guide_only_cover_retains_color_and_full_budget(self):
        book(
            self.source,
            '<img src="../jacket.jpg"/>',
            {
                "jacket.jpg": ("image/jpeg", raster()),
            },
        )
        with zipfile.ZipFile(self.source) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files["Library/book.opf"] = files["Library/book.opf"].replace(
            b"</package>",
            b'<guide><reference type="cover" href="chapters/read.xhtml"/></guide></package>',
        )
        with zipfile.ZipFile(self.source, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        optimize(self.source, self.output, Policy(paperwhite=True, photos=("*",)))
        with zipfile.ZipFile(self.output) as archive:
            image = Image.open(io.BytesIO(archive.read("Library/jacket.jpg")))
            self.assertEqual(image.size, (1264, 474))
            self.assertEqual(image.mode, "RGB")

    def test_signed_epub_is_rejected(self):
        book(self.source, "")
        with zipfile.ZipFile(self.source, "a") as archive:
            archive.writestr("META-INF/signatures.xml", b"signed")
        with self.assertRaisesRegex(ValueError, "Signed EPUB"):
            optimize(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_already_compact_jpeg_is_not_recompressed_on_repeat(self):
        first, _ = encode_image(raster(quality=100), "image/jpeg", Policy())
        again, evidence = encode_image(first, "image/jpeg", Policy())
        self.assertEqual(first, again)
        self.assertTrue(evidence["kept_original"])
        low_quality = raster(size=(400, 300), quality=60)
        self.assertEqual(
            encode_image(low_quality, "image/jpeg", Policy())[0], low_quality
        )

    def test_encrypted_media_are_not_parsed_or_rewritten(self):
        book(
            self.source,
            '<img src="../locked.svg"/>',
            {
                "locked.svg": ("image/svg+xml", b"opaque encrypted payload"),
            },
        )
        with zipfile.ZipFile(self.source, "a") as archive:
            archive.writestr(
                "META-INF/encryption.xml",
                b'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherReference URI="Library/locked.svg"/></CipherData></EncryptedData></encryption>',
            )
        optimize(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(
                archive.read("Library/locked.svg"), b"opaque encrypted payload"
            )

    @unittest.skipUnless(shutil.which("rsvg-convert"), "librsvg is not installed")
    def test_real_svg_renderer_retains_color_and_paperwhite_palette(self):
        data = b'<svg xmlns="http://www.w3.org/2000/svg" width="2000" height="1000"><rect width="2000" height="1000" fill="red"/></svg>'
        normal = Image.open(io.BytesIO(render_svg(data, Policy())))
        self.assertEqual(normal.size, (1264, 632))
        self.assertEqual(normal.convert("RGB").getpixel((100, 100)), (255, 0, 0))
        compact = Image.open(io.BytesIO(render_svg(data, Policy(paperwhite=True))))
        self.assertEqual(compact.mode, "P")
        self.assertLessEqual(len(compact.getcolors()), 16)

    def test_external_and_dynamic_svg_are_not_rendered(self):
        for child in ('<image href="../private.png"/>', "<script>something()</script>"):
            with self.assertRaises(ValueError):
                render_svg(
                    (
                        '<svg xmlns="http://www.w3.org/2000/svg">' + child + "</svg>"
                    ).encode(),
                    Policy(),
                )

    def test_cli_default_names_and_report(self):
        book(self.source, "")
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts/optimize_epub.py"),
            str(self.source),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.source.with_name(self.source.stem + ".optimized.epub")
        report = json.loads(output.with_suffix(".optimization.json").read_text())
        self.assertEqual(Path(report["output"]), output)
        self.assertTrue(report["content_preserved"])
        again = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)


if __name__ == "__main__":
    unittest.main()
