"""Source corroboration for damaged footer rows and raised call geometry."""

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import write_json
from numeric_footnotes import (
    anchored_baseline,
    footer_tokens,
    has_marker_character,
    recover_merged_footer,
    recover_numeric_footnotes,
    recover_tail_quote,
)


class MergedFooterTests(unittest.TestCase):
    def test_citation_comparison_preserves_observed_roman_number(self):
        self.assertEqual(
            footer_tokens("АН СCСР, т. ХУШ, с. 12—15."),
            footer_tokens("АН СССР, т. XVIII, с. 12-15."),
        )
        self.assertNotEqual(footer_tokens("т. XVII"), footer_tokens("т. XVIII"))
        self.assertEqual(footer_tokens("1958, страница 15"), ["1958", "страница", "15"])

    def recover(self, conflict=None):
        first = dict(
            text="1 Автор А. Б. Название статьи. Сборник Музея",
            bbox=[0.1, 0.8, 0.95, 0.825],
        )
        second = dict(
            text="антропологии АН СССР, т. ХУШ, с. 12—15.",
            bbox=[0.04, 0.83, 0.95, 0.855],
        )
        peers = [first, second]
        other = copy.deepcopy(peers)
        other[1]["text"] = other[1]["text"].replace("ХУШ", "XVIII")
        if conflict == "cached":
            other[0]["text"] = "1 Совершенно другая статья без согласующихся слов"
        if conflict == "geometry":
            other[0]["bbox"] = [0.1, 0.84, 0.95, 0.86]
        rows = [
            dict(page=1, text="Поврежденная строка", bbox=[0.03, 0.795, 0.96, 0.86])
        ]
        before = copy.deepcopy(rows)

        class Recognizer:
            def line_words(self, page, row, **kwargs):
                text = row["text"] if conflict != "fresh" else "Несогласующийся текст"
                return dict(text=text)

        with tempfile.TemporaryDirectory() as work:
            paths = {}
            for engine, data in [("tesseract", peers), ("rapid", other)]:
                paths[engine] = Path(work) / engine
                write_json(paths[engine] / "0001.json", dict(lines=data))
                (Path(work) / (engine + "-cache.txt")).write_text(str(paths[engine]))
            audit = []
            accepted = recover_merged_footer(
                1, rows, 0.79, None, work, Recognizer(), audit
            )
        return accepted, rows, before, audit

    def test_two_cached_engines_and_fresh_scales_restore_complete_footer(self):
        accepted, rows, _, audit = self.recover()
        self.assertTrue(accepted)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["text"].startswith("1 Автор"))
        self.assertIn("XVIII", rows[1]["text"])
        self.assertEqual(len(audit[0]["recovered"]), 2)

    def test_conflict_leaves_original_rows_untouched(self):
        for conflict in ["cached", "fresh", "geometry"]:
            with self.subTest(conflict=conflict):
                accepted, rows, before, audit = self.recover(conflict)
                self.assertFalse(accepted)
                self.assertEqual(rows, before)
                self.assertFalse(audit)

    def test_unavailable_comparator_is_not_an_error_or_permission_to_repair(self):
        with tempfile.TemporaryDirectory() as work:
            rows = [dict(text="Damaged footer", bbox=[0.1, 0.8, 0.9, 0.9])]
            self.assertFalse(recover_merged_footer(1, rows, 0.7, None, work, None, []))


class MissingQuoteTests(unittest.TestCase):
    def test_quote_requires_cached_spelling_two_strokes_and_positive_source_reads(self):
        for cached, strokes, readings, accepted in [
            (
                'слово..."ї',
                [(90, 19, 96, 27), (97, 19, 103, 25)],
                ["”", "”?", "7"],
                True,
            ),
            ("слово...ї", [(90, 19, 96, 27), (97, 19, 103, 25)], ["”", "”?"], False),
            ('слово..."ї', [(90, 19, 96, 27)], ["”", "”?"], False),
            ('слово..."ї', [(90, 19, 96, 27), (97, 19, 103, 25)], ["7", "77"], False),
        ]:
            with (
                self.subTest(cached=cached, strokes=strokes, readings=readings),
                tempfile.TemporaryDirectory() as work,
            ):
                path = Path(work) / "rapid"
                (Path(work) / "rapid-cache.txt").write_text(str(path))
                write_json(
                    path / "0001.json",
                    dict(lines=[dict(text=cached, bbox=[0.1, 0.2, 0.4, 0.25])]),
                )
                with patch(
                    "numeric_footnotes.retry_readings",
                    return_value=dict(readings=readings),
                ):
                    result = recover_tail_quote(
                        1,
                        "слово",
                        dict(bbox=[0.1, 0.2, 0.4, 0.25]),
                        (108, 13, 112, 27),
                        strokes,
                        18.7,
                        0,
                        0,
                        Image.new("L", (400, 400)),
                        None,
                        work,
                        Path(work) / "crops",
                        "test",
                    )
                self.assertEqual(bool(result), accepted)


class DamagedCallTests(unittest.TestCase):
    def test_font_fallback_cannot_borrow_a_neighbors_character(self):
        doc = pymupdf.open()
        doc.new_page(width=100, height=100)
        source = dict(
            text="'", characters=[dict(start=0, end=1, bbox=[30, 18, 32, 20])]
        )
        self.assertTrue(has_marker_character(source, [0.30, 0.18, 0.32, 0.20], doc[0]))
        self.assertFalse(has_marker_character(source, [0.30, 0.03, 0.32, 0.05], doc[0]))
        source["text"] = "а"
        self.assertFalse(has_marker_character(source, [0.30, 0.18, 0.32, 0.20], doc[0]))
        doc.close()

    def test_short_line_baseline_excludes_neighbor_and_raised_digit_votes(self):
        boxes = [
            (5, 5, 20, 25),
            (23, 7, 31, 24),
            (50, 10, 55, 24),
            (5, 32, 20, 45),
            (23, 34, 33, 45),
            (36, 30, 48, 44),
        ]
        row = dict(bbox=[0.0, 0.27, 0.6, 0.52])
        model = anchored_baseline(boxes, 18, row, 0, Image.new("L", (100, 100)))
        self.assertEqual(model[1], 44)

    def test_conflicting_digit_ocr_requires_document_font_and_keeps_spaced_dash(self):
        from vocabulary import TOKEN

        text = 'слова..."! , - дальше'
        observed = "слова...”', — дальше"
        words = [(10, 20, 27, 23, "слова"), (40, 20, 60, 23, "дальше")]
        chars = [
            dict(start=m.start(), end=m.end(), bbox=list(words[i][:4]))
            for i, m in enumerate(TOKEN.finditer(observed))
        ]
        coords = {
            ".": [28, 22, 28.2, 23],
            "”": [29, 19, 30, 20],
            "'": [31, 18, 32, 20],
            ",": [33, 22, 34, 23],
            "—": [35, 21, 38, 21.5],
        }
        for i, c in enumerate(observed):
            if c in coords:
                chars.append(dict(start=i, end=i + 1, bbox=coords[c]))

        class Recognizer:
            version = "test"

            def line_words(self, *a, **k):
                return dict(text=observed, words=words, characters=chars)

        for witnesses, expected, original in [
            (None, 0, text),
            ([dict(page=2), dict(page=3)], 1, text),
            ([dict(page=2), dict(page=3)], 0, text.replace("слова", "слова 1929")),
        ]:
            with (
                self.subTest(font=bool(witnesses)),
                tempfile.TemporaryDirectory() as work,
            ):
                doc = pymupdf.open()
                doc.new_page(width=100, height=100)
                row = dict(page=1, text=original, bbox=[0.1, 0.2, 0.65, 0.24])
                footer = dict(
                    page=1, text="1 Source reference", bbox=[0.1, 0.8, 0.9, 0.84]
                )
                with (
                    patch("numeric_footnotes.footer_label", return_value=None),
                    patch("numeric_footnotes.components", return_value=[]),
                    patch("numeric_footnotes.prose_baseline", return_value=(17, 30, 0)),
                    patch(
                        "numeric_footnotes.marker_boxes",
                        return_value=[(120, 2, 125, 13)],
                    ),
                    patch(
                        "numeric_footnotes.glyph_readings",
                        new=lambda *args: ["2", "c", "c"],
                    ),
                    patch(
                        "numeric_footnotes.retry_readings",
                        return_value=dict(readings=["c"]),
                    ),
                    patch("numeric_footnotes.font_match", return_value=witnesses),
                    patch("numeric_footnotes.traineddata_digest", return_value="test"),
                ):
                    report = recover_numeric_footnotes(
                        {1: [row, footer]},
                        doc,
                        dict(note_starts={"1": 0.8}),
                        work,
                        [],
                        Recognizer(),
                    )
                self.assertEqual(report["linkable"], expected)
                if expected:
                    self.assertIn("”1, — дальше", row["text"])
                    self.assertEqual(report["found"][0]["font_witnesses"], witnesses)
                else:
                    self.assertEqual(row["text"], original)
                doc.close()


if __name__ == "__main__":
    unittest.main()
