import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from languages import language_code, dictionary_locale, ocr_settings, ui_label
from book_model import JoinPolicy
from build import xhtml
from layout import signature
from metadata import verify_edition
from book_model import reconstruct
from profile_validation import validate_profile
from unittest.mock import patch
from ocr import xml_safe_ocr


class LanguageTests(unittest.TestCase):
    def test_invalid_xml_glyphs_are_visible_and_offsets_are_preserved(self):
        text, evidence = xml_safe_ocr("слово\ufffe\x01\nтекст")
        self.assertEqual(text, "слово\ufffd\ufffd\nтекст")
        self.assertEqual([x["offset"] for x in evidence], [5, 6])
        self.assertEqual([x["codepoint"] for x in evidence], ["U+FFFE", "U+0001"])

    def test_source_gap_separates_continuing_paragraphs(self):
        def block(text, continuation=False):
            return dict(
                kind="text",
                text=text,
                continuation=continuation,
                lines=[dict(text=text)],
                fragments=[],
                sources=[],
                page_breaks=[],
            )

        book = dict(
            chapters=[[1, 2, "Section"]],
            source_gaps=[
                dict(
                    page=2,
                    text="Missing source leaves.",
                    evidence="Printed pagination gap.",
                )
            ],
        )
        with (
            patch(
                "book_model.page_blocks",
                side_effect=[([block("Before")], []), ([block("After", True)], [])],
            ),
            patch("book_model.normalize_block", side_effect=lambda b, *a: b),
        ):
            model = reconstruct({1: [], 2: []}, book, JoinPolicy(), [])
        self.assertEqual(
            [b["kind"] for b in model[0]["blocks"]], ["text", "source-gap", "text"]
        )
        self.assertEqual(model[0]["blocks"][1]["sources"], [])

    def test_language_and_gap_profile_values_are_validated(self):
        book = dict(
            title="Title",
            author="Author",
            language="ru",
            slug="test",
            source_sha256="0" * 64,
            chapters=[[1, 2, "Section"]],
            ocr_tesseract_page_languages={"2": "rus+eng"},
            source_gaps=[dict(page=2, text="Gap", evidence="Source pages checked")],
        )
        validate_profile(book, page_count=2)
        with self.assertRaises(ValueError):
            validate_profile(dict(book, ocr_tesseract_page_languages={"2": "../rus"}))
        with self.assertRaises(ValueError):
            validate_profile(dict(book, source_gaps=[dict(page=2, text="Gap")]))

    def test_english_defaults_and_russian_resources(self):
        self.assertEqual(ocr_settings({"language": "en"})["tesseract"], "eng")
        book = {"language": "ru-RU"}
        self.assertEqual(language_code(book), "ru")
        self.assertEqual(dictionary_locale(book), "ru_RU")
        self.assertEqual(ocr_settings(book)["tesseract"], "rus")
        self.assertEqual(ocr_settings(book)["rapid"], "cyrillic")
        self.assertEqual(ui_label(book, "notes"), "Примечания")

    def test_cyrillic_hyphenation_and_header_signatures(self):
        policy = JoinPolicy(language="ru")
        self.assertEqual(policy.boundary("экономи-", "ческим")[2], "remove")
        self.assertNotEqual(signature("Глава 1"), signature("Оленеводство"))
        self.assertEqual(signature("CHAPTER 1"), "CHAPTER")
        self.assertIn(b'lang="ru"', xhtml("Заглавие", "<p>Текст</p>", "ru"))
        self.assertIn(b'lang="en"', xhtml("Title", "<p>Text</p>"))

    def test_reviewed_author_alias_preserves_other_identity_checks(self):
        record = {
            "isbn_10": ["5020167576"],
            "title": "Материальная культура чукчей",
            "publishers": ["Наука"],
        }
        book = {
            "isbn": "5020167576",
            "title": record["title"],
            "publisher": "Наука",
            "author": "Владимир Богораз",
            "metadata_author_aliases": ["Waldemar Bogoras"],
        }
        verify_edition(record, book, ["Waldemar Bogoras"])
        with self.assertRaises(ValueError):
            verify_edition(dict(record, title="Other"), book, ["Waldemar Bogoras"])
        with self.assertRaises(ValueError):
            verify_edition(record, book, ["Different author"])


if __name__ == "__main__":
    unittest.main()
