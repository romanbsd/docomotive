"""Source figure, glossary and scanned paragraph regressions."""

import json
import sys
import unittest
from pathlib import Path
import pymupdf
import io
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from book_model import classify_row, reconstruct, JoinPolicy
from figures import incorporate_figures, crop_artwork_pixels
from render_text import block_html


def row(text, y, x=0.1, kind="text"):
    return dict(text=text, bbox=[x, y, 0.8, y + 0.015], kind=kind, column=0)


class FigureTests(unittest.TestCase):
    def test_framed_scan_keeps_native_pixels_but_visible_text_requires_render(self):
        doc = pymupdf.open()
        page = doc.new_page(width=100, height=100)
        raw = io.BytesIO()
        Image.new("RGB", (180, 180), "white").save(raw, format="PNG")
        page.insert_image(pymupdf.Rect(5, 5, 95, 95), stream=raw.getvalue())
        crop, evidence = crop_artwork_pixels(page, pymupdf.Rect(10, 10, 90, 90))
        self.assertEqual(evidence["method"], "embedded-page-scan")
        self.assertEqual(crop.size, (160, 160))
        page.insert_text((20, 40), "Visible overlay", fontsize=8)
        _, evidence = crop_artwork_pixels(page, pymupdf.Rect(10, 10, 90, 90))
        self.assertEqual(evidence["method"], "rendered-page")

    def test_only_image_region_and_reviewed_blank_page_are_excluded(self):
        b = dict(
            figures=[dict(page=1, rect=[0.1, 0.2, 0.9, 0.5])],
            excluded_pages={"2": "blank scan"},
        )
        self.assertEqual(
            classify_row(1, row("Label", 0.3), b)[1], "preserved-figure-region"
        )
        self.assertEqual(classify_row(1, row("Caption", 0.6), b)[0], "body")
        self.assertEqual(
            classify_row(2, row("Bleed-through", 0.5), b)[1], "profile-excluded-page"
        )

    def test_figure_is_inserted_before_caption_with_a_checked_source_crop(self):
        doc = pymupdf.open()
        doc.new_page(width=100, height=100)
        model = [
            dict(
                chapter=1,
                source_pages=[1, 1],
                blocks=[
                    dict(
                        sources=[dict(page=1, bbox=[0.1, 0.6, 0.8, 0.7])],
                        text="Caption",
                    )
                ],
            )
        ]
        book = dict(
            figures=[
                dict(
                    page=1,
                    rect=[0.1, 0.2, 0.9, 0.5],
                    name="figure",
                    alt="Source illustration",
                )
            ]
        )
        files = {}
        report = incorporate_figures(model, book, doc, files)
        self.assertEqual(model[0]["blocks"][0]["kind"], "figure")
        self.assertTrue(files["OEBPS/figure.jpg"].startswith(b"\xff\xd8"))
        book["figure_color_mode"] = "grayscale"
        grayscale_files = {}
        incorporate_figures(
            [dict(chapter=1, source_pages=[1, 1], blocks=[])],
            book,
            doc,
            grayscale_files,
        )
        self.assertEqual(
            Image.open(io.BytesIO(grayscale_files["OEBPS/figure.jpg"])).mode, "L"
        )
        self.assertEqual(report[0]["chapter"], 1)
        self.assertIn(
            'alt="Source illustration"',
            block_html(model[0]["blocks"][0], book, set(), [], "chapter.xhtml"),
        )
        book["figures"][0]["rect"] = [0, 0.7, 1, 0.3]
        with self.assertRaises(ValueError):
            incorporate_figures(model, book, doc, {})

    def test_continuous_indented_quote_rows_join_their_line_wrap(self):
        pages = {
            1: [
                row("Body paragraph.", 0.2),
                row("A quotation about chromo-", 0.3, 0.14),
                row("somes and genes.", 0.32, 0.14),
            ]
        }
        b = dict(
            chapters=[[1, 1, "Chapter"]],
            chapter_body_starts={"1": 0},
            continuous_indented_rows=True,
        )
        m = reconstruct(pages, b, JoinPolicy(protected=["chromosomes"]), [])
        self.assertEqual(
            [x["text"] for x in m[0]["blocks"]],
            ["Body paragraph.", "A quotation about chromosomes and genes."],
        )

    def test_glossary_start_prevents_false_cross_page_continuation(self):
        b = dict(
            chapters=[[1, 2, "Glossary"]],
            chapter_body_starts={"1": 0},
            glossary_terms={"1": ["Allele"], "2": ["Chromosome"]},
            glossary_style="em",
        )
        m = reconstruct(
            {
                1: [row("Allele A form of a gene.", 0.2)],
                2: [row("Chromosome: A threadlike body.", 0.2)],
            },
            b,
            JoinPolicy(),
            [],
        )
        self.assertEqual(len(m[0]["blocks"]), 2)
        self.assertEqual(m[0]["blocks"][1]["inline"][0]["tags"], ["em"])

    def test_explicit_cross_page_quote_requires_matching_source_text(self):
        b = dict(
            chapters=[[1, 2, "Chapter"]],
            chapter_body_starts={"1": 0},
            cross_page_continuations={"2": dict(before="analo-", after="gous")},
        )
        m = reconstruct(
            {1: [row("Formally analo-", 0.8)], 2: [row("gous elements.", 0.2, 0.14)]},
            b,
            JoinPolicy(protected=["analogous"]),
            [],
        )
        self.assertEqual(m[0]["blocks"][0]["text"], "Formally analogous elements.")
        with self.assertRaises(ValueError):
            reconstruct(
                {1: [row("Other text.", 0.8)], 2: [row("gous elements.", 0.2, 0.14)]},
                b,
                JoinPolicy(),
                [],
            )

    def test_quote_indent_is_profile_specific(self):
        block = dict(kind="quote", text="Quoted text.", sources=[], page_breaks=[])
        default = block_html(block, {}, set(), [], "chapter.xhtml")
        reviewed = block_html(
            block, {"quote_first_line_indent": False}, set(), [], "chapter.xhtml"
        )
        self.assertEqual(default, "<blockquote><p>Quoted text.</p></blockquote>")
        self.assertIn('<blockquote><p class="noindent">', reviewed)

    def test_organism_artifacts_retain_figures_glossary_and_numbered_notes(self):
        root = Path(__file__).resolve().parents[1]
        out = root / "output/feeling-organism"
        if not (out / "report.json").exists():
            self.skipTest("Build organism fixture first")
        r = json.loads((out / "report.json").read_text())
        self.assertEqual(len(r["figures"]), 18)
        self.assertEqual(r["apparatus"]["endnotes"], 157)
        self.assertEqual(
            r["apparatus"]["chapter_links"] + r["apparatus"]["superscript_links"], 157
        )
        m = json.loads((out / "book-model.json").read_text())
        preface = m[5]["blocks"]
        introduction = next(b for b in preface if "Kimber Award" in b["text"])
        self.assertIn("chosen for the Kimber Award", introduction["text"])
        quotation = next(
            b for b in preface if "One of the remarkable things" in b["text"]
        )
        self.assertEqual(quotation["kind"], "quote")
        self.assertTrue(
            quotation["text"].endswith("cytogenetics.1")
            if (out / "scanned-endnote-analysis.json").exists()
            else quotation["text"].endswith("cytogenetics.'")
        )
        for opening in ("As the summer passed", "Most evenings"):
            quote = next(b for b in m[16]["blocks"] if b["text"].startswith(opening))
            self.assertEqual(quote["kind"], "quote")
            self.assertTrue(quote["first_line_indent"])
            self.assertTrue(0.85 <= quote["quote_font_scale"] <= 0.95)
        self.assertIn("into which the ball all too often went.22", quote["text"])
        glossary = m[20]["blocks"]
        self.assertEqual(len(glossary), 87)
        self.assertTrue(
            all(any("em" in s["tags"] for s in b.get("inline", [])) for b in glossary)
        )
        for chapter in m:
            for block in chapter["blocks"]:
                from lxml import etree

                node = etree.fromstring(
                    (
                        '<body xmlns:epub="http://www.idpf.org/2007/ops">'
                        + block_html(block, {}, set(), [], "chapter.xhtml")
                        + "</body>"
                    ).encode()
                )
                for backlink in node.xpath('.//p[@class="note-backlinks"]'):
                    backlink.getparent().remove(backlink)
                for br in node.findall(".//br"):
                    br.tail = " " + (br.tail or "")
                self.assertEqual(
                    " ".join("".join(node.itertext()).split()),
                    " ".join(block["text"].split()),
                )


if __name__ == "__main__":
    unittest.main()
