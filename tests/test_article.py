"""Regressions for native article typography, provenance and title notes."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from native_pdf import extract_page
from common import apply_edits
from book_model import classify_row, reconstruct, JoinPolicy
from metadata import enrich_doi


class ArticleTests(unittest.TestCase):
    def test_watermark_excluded_before_geometry_merge_and_font_aliases(self):
        def line(text, font, size=10, flags=4):
            return dict(
                bbox=[10, 20, 80, 30],
                spans=[dict(text=text, font=font, size=size, flags=flags)],
            )

        page = SimpleNamespace(
            rect=SimpleNamespace(width=100, height=100),
            get_text=lambda mode: dict(
                blocks=[
                    dict(
                        lines=[
                            line("Title--", "Opaque"),
                            line("74", "Body", 7),
                            line("Downloaded", "Arial"),
                        ]
                    )
                ]
            ),
        )
        book = dict(
            native_font_styles={"Opaque": ["em"]},
            native_excluded_fonts=["Arial"],
            native_superscript_max_size=8,
            native_small_numeric_sup=True,
            native_typography={"--": "–"},
        )
        audit = []
        rows = extract_page(page, book, 1, audit)
        self.assertEqual(rows[0]["text"], "Title–74")
        self.assertEqual(rows[0]["inline"][0]["tags"], ["em"])
        self.assertEqual(rows[0]["inline"][1]["tags"], ["sup"])
        self.assertTrue(any(a["kind"] == "excluded-native-annotation" for a in audit))

    def test_checked_row_edit_preserves_following_superscript(self):
        item = dict(
            page=1,
            text="fu¨r74",
            inline=[
                dict(start=0, end=4, tags=["em"]),
                dict(start=4, end=6, tags=["sup"]),
            ],
        )
        apply_edits([item], [dict(page=1, before="fu¨r", after="für")], [], "verified")
        self.assertEqual(item["text"], "für74")
        self.assertEqual(
            [(s["start"], s["end"]) for s in item["inline"]], [(0, 3), (3, 5)]
        )

    def test_cross_page_quote_continues_one_block(self):
        def row(text, y):
            return dict(text=text, bbox=[0.2, y, 0.8, y + 0.02], kind="quote")

        book = dict(chapters=[[1, 2, "Article"]], chapter_body_starts={"1": 0})
        model = reconstruct(
            {1: [row("A quotation continues", 0.8)], 2: [row("across the page.", 0.2)]},
            book,
            JoinPolicy(),
            [],
        )
        self.assertEqual(
            model[0]["blocks"][0]["text"], "A quotation continues across the page."
        )
        self.assertEqual(len(model[0]["blocks"]), 1)

    def test_doi_identity_and_advance_date_remain_distinct(self):
        record = dict(
            DOI="10.1234/example",
            author=[dict(given="Boaz", family="Huss")],
            title=["Title: Subtitle1"],
            **{
                "published-print": {"date-parts": [[2021, 1, 22]]},
                "container-title": ["Journal"],
            },
        )
        cache = SimpleNamespace(get=lambda url: dict(message=record), sources=[])
        book = dict(
            doi="10.1234/example",
            title="Title",
            subtitle="Subtitle",
            metadata_title_suffix="1",
            author="Boaz Huss",
            date="2020-12-21",
            source_sha256="test",
        )
        result = enrich_doi(book, cache)
        self.assertEqual(result["publication_date"], "2020-12-21")
        self.assertEqual(result["final_publication_date"], "2021-01-22")
        record["title"] = ["Other article"]
        with self.assertRaises(ValueError):
            enrich_doi(book, cache)

    def test_article_artifact_has_title_note_and_all_source_rows(self):
        root = Path(__file__).resolve().parents[1]
        out = root / "output/letter-kills"
        if not (out / "apparatus-analysis.json").exists():
            self.skipTest("Build article fixture first")
        report = json.loads((out / "apparatus-analysis.json").read_text())
        self.assertEqual((report["endnotes"], report["superscript_links"]), (89, 89))
        model = json.loads((out / "book-model.json").read_text())
        note = next(b for b in model[1]["blocks"] if b.get("id") == "endnote-1-1")
        self.assertEqual(note["backlinks"], ["title.xhtml#noteref-title-1"])
        self.assertIn(
            "“FOR THE LETTER KILLS, BUT THE SPIRIT GIVES LIFE”",
            [b["text"] for b in model[0]["blocks"] if b["kind"] == "heading"],
        )
        self.assertEqual(
            json.loads((out / "text-coverage.json").read_text())["status"], "passed"
        )

    def test_rendered_article_matches_all_canonical_blocks(self):
        import zipfile
        from lxml import etree

        root = Path(__file__).resolve().parents[1]
        out = root / "output/letter-kills"
        if not (out / "letter-kills.epub").exists():
            self.skipTest("Build article fixture first")
        model = json.loads((out / "book-model.json").read_text())
        with zipfile.ZipFile(out / "letter-kills.epub") as archive:
            for chapter in model:
                doc = etree.fromstring(
                    archive.read(f"OEBPS/chapter-{chapter['chapter']:02}.xhtml")
                )
                for backlink in doc.xpath(
                    '//*[local-name()="p" and @class="note-backlinks"]'
                ):
                    backlink.getparent().remove(backlink)
                body = doc.find("{http://www.w3.org/1999/xhtml}body")
                actual = " ".join("".join(child.itertext()) for child in body)
                expected = " ".join(block["text"] for block in chapter["blocks"])
                self.assertEqual(" ".join(actual.split()), " ".join(expected.split()))


if __name__ == "__main__":
    unittest.main()
