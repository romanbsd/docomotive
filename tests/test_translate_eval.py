"""The evaluation harness cuts reproducible windows and measures term drift."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import translate_eval as E
from common import write_epub

PAGE = (
    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>'
    "<div><p>{}</p><p>{}</p><p>{}</p></div></body></html>"
)


class TranslateEvalTests(unittest.TestCase):
    def book(self):
        folder = Path(tempfile.mkdtemp())
        page = PAGE.format("a" * 100, "b" * 100, "c" * 100).encode()
        write_epub(folder / "book.epub", {"text/c.html": page})
        return folder

    def test_window_selects_overlapping_units_below_wrappers(self):
        folder = self.book()
        with patch.object(E, "ROOT", folder):
            chosen = E.window("book.epub", "text/c.html", 150, 20)
        self.assertEqual(["".join(u.itertext())[0] for u in chosen], ["b"])

    def test_sample_has_one_document_per_window(self):
        folder = self.book()
        spec = {
            "samples": [
                {"name": "x", "epub": "book.epub", "document": "text/c.html"}
                | {"start": 0, "length": 120, "kind": "prose"},
                {"name": "y", "epub": "book.epub", "document": "text/c.html"}
                | {"start": 250, "length": 10, "kind": "notes"},
            ]
        }
        with patch.object(E, "ROOT", folder):
            E.build_sample(spec, folder / "sample.epub")
        with zipfile.ZipFile(folder / "sample.epub") as z:
            self.assertIn("aaa", z.read("s1.xhtml").decode())
            self.assertIn("bbb", z.read("s1.xhtml").decode())
            self.assertNotIn("aaa", z.read("s2.xhtml").decode())
            self.assertIn(
                "<dc:language>en</dc:language>", z.read("content.opf").decode()
            )

    def test_term_variants_report_only_inconsistent_recurring_terms(self):
        rows = [
            (
                "s",
                "prose",
                "ayahuasca and ayahuasca, Pichis",
                "аяуаска и айяуаски, Пичис",
            ),
        ]
        glossary = {"ayahuasca": "аяуаска", "Pichis": "Пичис"}
        self.assertEqual(
            E.term_variants(glossary, rows), {"ayahuasca": ["айяуас", "аяуаск"]}
        )
        peru = {"Peru": "Перу"}
        rows = [("s", "prose", "Peru, Peru first", "в Перу, из Перу, первый")]
        self.assertEqual(E.term_variants(peru, rows), {})
        rows = [("s", "prose", "ayahuasca, ayahuasca", "аяуаска, аяуаски")]
        self.assertEqual(E.term_variants(glossary, rows), {})


if __name__ == "__main__":
    unittest.main()
