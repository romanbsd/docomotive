"""Source evidence gates punctuation/case; layout recovery must preserve prose."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from punctuation import (
    anchored_gap,
    normalize_quotes,
    repair_source_punctuation,
    punctuation_form,
)
from case_repair import aligned_word
from book_model import resolve_body_top
from figure_bounds import framed_bounds
from partial_rows import truncated_rows
from numeric_footnotes import marker_boxes


def model(text, kind="text"):
    row = dict(page=1, row_id="r", text=text, bbox=[0.1, 0.2, 0.9, 0.22])
    source = dict(row, start=0, end=len(text))
    return [
        dict(chapter=1, blocks=[dict(kind=kind, text=text, sources=[source])], notes=[])
    ], row


class PunctuationTests(unittest.TestCase):
    def test_complete_pairs_preserve_offsets_and_nested_quotes(self):
        m, _ = model('Она сказала: „Внутри "другой" мир".')
        r = normalize_quotes(m, [])
        self.assertEqual(
            m[0]["blocks"][0]["text"], "Она сказала: «Внутри „другой“ мир»."
        )
        self.assertFalse(r["unresolved"])

    def test_orphan_does_not_pair_with_next_paragraph(self):
        m, _ = model("Незакрытая „цитата")
        b, _ = model('Это "полная" цитата.')
        m[0]["blocks"] += b[0]["blocks"]
        r = normalize_quotes(m, [])
        self.assertEqual(m[0]["blocks"][1]["text"], "Это «полная» цитата.")
        self.assertEqual(len(r["unresolved"]), 1)

    def test_gap_requires_unique_lexical_anchors(self):
        self.assertEqual(
            anchored_gap(
                "дух хозяин . воды бросает", ["дух", "хозяин"], ["воды", "бросает"]
            ),
            (10, 13, " . "),
        )
        self.assertIsNone(
            anchored_gap("хозяин . воды хозяин . воды", ["хозяин"], ["воды"])
        )
        self.assertIsNone(anchored_gap("хозяин новый воды", ["хозяин"], ["воды"]))

    def run_punctuation(self, original, cached, fresh):
        m, row = model(original)

        class Recognizer:
            doc = [None]

            def line_words(self, *args, **kwargs):
                return dict(text=fresh, characters=[], words=[])

        with patch(
            "punctuation.read_json", return_value=dict(lines=[dict(row, text=cached)])
        ):
            r = repair_source_punctuation(
                m, {1: [row]}, {}, Recognizer(), Path("/unused"), []
            )
        return row, r

    def test_one_damaged_anchor_requires_two_exact_substantial_neighbors(self):
        text = "лялся разделенным на несколько ярусов или „земель”. Голько"
        left, right = ["ярусов", "или", "земель"], ["только"]
        self.assertIsNone(anchored_gap(text, left, right))
        self.assertEqual(anchored_gap(text, left, right, fuzzy=True)[2], "”. ")
        self.assertIsNone(
            anchored_gap("земель”. Голько", ["земель"], right, fuzzy=True)
        )
        self.assertIsNone(
            anchored_gap(text.replace("ярусов", "ярусан"), left, right, fuzzy=True)
        )

    def test_double_quote_lookalike_does_not_license_lexical_apostrophe_changes(self):
        self.assertEqual(punctuation_form("’,", '" • '), '",')
        self.assertNotEqual(punctuation_form("'", " . "), punctuation_form('"', " . "))
        row, result = self.run_punctuation(
            'Нижний мир и „верхний" • также обычно',
            'Нижний мир и „верхний", также обычно',
            "Нижний мир и „верхний’, также обычно",
        )
        self.assertEqual(result["corrected"], 1)
        self.assertNotIn("•", row["text"])

    def test_remove_speck_only_on_literal_agreement(self):
        row, r = self.run_punctuation(
            "Старинный дух хозяин . воды бросает чешую.",
            "Старинный дух хозяин воды бросает чешую.",
            "Старинный дух хозяин воды бросает чешую.",
        )
        self.assertEqual(row["text"], "Старинный дух хозяин воды бросает чешую.")
        self.assertEqual(r["corrected"], 1)

    def test_disagreement_and_real_verse_question_are_retained(self):
        original = "Старинный дух хозяин . воды бросает чешую."
        row, r = self.run_punctuation(original, original, original.replace(" . ", " "))
        self.assertEqual(row["text"], original)
        long = "Это длинная строка текста, содержащая начало следующего поврежденного слова"
        row, r = self.run_punctuation(long, long + "-", long + "-")
        self.assertEqual(row["text"], long)
        self.assertEqual(r["decisions"][0]["reason"], "wrap-dash-needs-span-review")
        m, row = model("Почему беспокоится?..", "verse")

        class R:
            doc = [None]

            def line_words(self, *a, **k):
                return dict(text=row["text"], words=[], characters=[])

        with patch("punctuation.read_json", return_value=dict(lines=[row])):
            r = repair_source_punctuation(m, {1: [row]}, {}, R(), Path("/unused"), [])
        self.assertEqual(row["text"], "Почему беспокоится?..")
        self.assertEqual(r["corrected"], 0)

    def test_aligned_case_requires_neighbors(self):
        import re

        text = "без вИДИМЫХ Причин распевал песни"
        m = re.search("вИДИМЫХ", text)
        self.assertEqual(
            aligned_word(text, "без видимых причин распевал песни", m), "видимых"
        )
        self.assertIsNone(aligned_word(text, "совсем видимых следов не осталось", m))


class LayoutRecoveryTests(unittest.TestCase):
    def test_body_top_default_and_header_guard(self):
        rows = [
            dict(
                page=1,
                text="Длинная обычная строка содержит много слов и продолжает рассказ",
                bbox=[0.1, 0.05, 0.9, 0.07],
            )
        ]
        rows += [
            dict(
                page=1,
                text="Следующая обычная строка содержит много слов и продолжает рассказ",
                bbox=[0.1, y, 0.9, y + 0.02],
            )
            for y in [0.08, 0.11, 0.14, 0.17]
        ]
        rows.insert(0, dict(page=1, text="Название главы", bbox=[0.1, 0.01, 0.5, 0.03]))
        book = {"upper_margin_cutoff": 0.065}
        resolve_body_top({1: rows}, book, [])
        self.assertNotIn("_body_upper_margins", book)
        book["recover_body_top"] = True
        resolve_body_top({1: rows}, book, [])
        self.assertAlmostEqual(book["_body_upper_margins"]["1"], 0.048)

    def test_frame_keeps_caption_excludes_prose_and_subpanels(self):
        im = Image.new("L", (1000, 1000), 255)
        draw = ImageDraw.Draw(im)
        draw.rectangle((450, 70, 900, 720), outline=0, width=3)
        rows = [
            dict(
                text="A long ordinary body line containing enough characters here",
                bbox=[0.05, y, 0.95, y + 0.025],
            )
            for y in [0.1, 0.15, 0.2, 0.25, 0.3]
        ]
        rows += [
            dict(text="Small caption", bbox=[0.47, 0.74, 0.87, 0.758]),
            dict(
                text="A full-width body line below the picture",
                bbox=[0.05, 0.82, 0.95, 0.845],
            ),
        ]
        result = framed_bounds(im, [0.5, 0.07, 0.985, 0.83], rows)
        self.assertLess(result["rect"][0], 0.45)
        self.assertLess(result["rect"][3], 0.8)
        self.assertGreater(result["rect"][3], 0.758)
        self.assertIsNone(framed_bounds(im, [0.03, 0.07, 0.985, 0.83], rows))

    def test_broken_border_retry_keeps_caption_and_returns_first_prose_row(self):
        from figures import outside_figures

        rows = [
            dict(text="Short illustration caption", bbox=[0.55, y, 0.96, y + 0.015])
            for y in [0.725, 0.745, 0.765]
        ] + [
            dict(
                text="The first ordinary prose row beneath the illustration.",
                bbox=[0.05, y, 0.96, y + 0.025],
            )
            for y in [0.812, 0.845, 0.880]
        ]
        for gap, accepted in [(0, True), (5, True), (15, False)]:
            with self.subTest(gap=gap):
                im = Image.new("L", (400, 600), 255)
                d = ImageDraw.Draw(im)
                d.rectangle((20, 42, 388, 420), outline=0, width=2)
                if gap:
                    d.rectangle((385, 200, 392, 199 + gap), fill=255)
                    d.rectangle((18, 110, 24, 109 + gap), fill=255)
                result = framed_bounds(im, [0.04, 0.06, 0.985, 0.83], rows)
                self.assertEqual(bool(result), accepted)
                if not accepted:
                    continue
                self.assertEqual(bool(result.get("border_gap_repair")), bool(gap))
                self.assertGreater(result["rect"][3], 0.78)
                self.assertLess(result["rect"][3], 0.812)
                figure = dict(page=1, name="fixture", rect=result["rect"])
                retained = outside_figures(rows, dict(figures=[figure]), 1)
                self.assertEqual(retained, rows[3:])

    def test_side_caption_frame_excludes_prose_but_not_a_second_panel(self):
        im = Image.new("L", (1000, 1000), 255)
        draw = ImageDraw.Draw(im)
        draw.rectangle((50, 80, 600, 560), outline=0, width=3)
        rows = [
            dict(
                text="An ordinary long body line with enough characters",
                bbox=[0.05, y, 0.96, y + 0.03],
            )
            for y in [0.58, 0.62, 0.66, 0.70]
        ]
        for y in [0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.22]:
            draw.rectangle((660, int(y * 1000), 950, int(y * 1000) + 10), fill=0)
            # Recognition confidence is not a reliable font/layout classifier.
            rows.append(
                dict(
                    text="A small side caption here",
                    bbox=[0.66, y, 0.95, y + 0.015],
                    confidence=0.3,
                )
            )
        result = framed_bounds(im, [0.04, 0.07, 0.985, 0.61], rows)
        self.assertIsNotNone(result)
        self.assertLess(result["rect"][3], 0.58)
        self.assertGreater(result["rect"][2], 0.95)
        draw.rectangle((660, 300, 950, 550), outline=0, width=3)
        self.assertIsNone(framed_bounds(im, [0.04, 0.07, 0.985, 0.61], rows))

    def test_partial_row_requires_geometry_and_anchors(self):
        row = dict(text="у народов название слова", bbox=[0.6, 0.1, 0.95, 0.12])
        peer = dict(
            text="Полная строка текста у народов название слова",
            bbox=[0.05, 0.1, 0.95, 0.12],
        )
        self.assertEqual(len(truncated_rows([row], [peer])), 1)
        self.assertFalse(
            truncated_rows([row], [dict(peer, text="Совершенно другой текст")])
        )

    def test_smaller_raised_marker_and_exclamation_dot(self):
        self.assertEqual(
            marker_boxes([(10, 2, 14, 14)], (24, 30, 0)), [(10, 2, 14, 14)]
        )
        self.assertFalse(marker_boxes([(10, 2, 14, 14), (11, 16, 13, 19)], (24, 30, 0)))


class FontSizeGuardTests(unittest.TestCase):
    def test_quote_stroke_cannot_be_enlarged_into_digit(self):
        from numeric_footnotes import font_match

        digit = Image.new("L", (6, 16), 255)
        d = ImageDraw.Draw(digit)
        d.line((3, 0, 3, 15), fill=0, width=2)
        d.line((1, 15, 5, 15), fill=0)
        quote = digit.resize((3, 8))
        bank = [dict(page=p, bbox=[0.1, 0.1, 0.2, 0.2], glyph=digit) for p in [1, 2]]
        self.assertIsNone(font_match(quote, bank, "1"))
        self.assertIsNotNone(font_match(digit, bank, "1"))


class InteriorCallTests(unittest.TestCase):
    def test_adaptive_marker_needs_primary_digit_character_location_and_font(self):
        import pymupdf
        from numeric_footnotes import recover_numeric_footnotes
        from vocabulary import TOKEN

        doc = pymupdf.open()
        doc.new_page(width=100, height=100)
        observed = 'свое умение"!'
        words = [(10, 20, 16, 23, "свое"), (18, 20, 30, 23, "умение")]
        chars = [
            dict(start=m.start(), end=m.end(), bbox=list(words[i][:4]))
            for i, m in enumerate(TOKEN.finditer(observed))
        ]
        chars += [
            dict(start=11, end=12, bbox=[31, 18.5, 32, 19.5]),
            dict(start=12, end=13, bbox=[34, 17, 35.5, 19.2]),
        ]

        class R:
            version = "test-tesseract"

            def line_words(self, *a, **k):
                return dict(text=observed, words=words, characters=chars)

        for tail, font, located, expected in [
            ('"1', True, True, 1),
            ('"', True, True, 0),
            ('"1', False, True, 0),
            ('"1', True, False, 0),
        ]:
            with self.subTest(tail=tail, font=font, located=located):
                chars[-1]["bbox"] = (
                    [34, 17, 35.5, 19.2] if located else [34, 3, 35.5, 5]
                )
                row = dict(
                    page=1,
                    text="свое умение" + tail,
                    bbox=[0.1, 0.2, 0.4, 0.24],
                    kind="text",
                )
                note = dict(page=1, text="1 Источник.", bbox=[0.1, 0.8, 0.9, 0.84])
                with (
                    tempfile.TemporaryDirectory() as root,
                    patch("numeric_footnotes.footer_label", return_value=None),
                    patch("numeric_footnotes.components", return_value=[]),
                    patch("numeric_footnotes.prose_baseline", return_value=(17, 36, 0)),
                    patch(
                        "numeric_footnotes.marker_boxes",
                        side_effect=[[], [(140, 4, 144, 14)]],
                    ),
                    patch(
                        "numeric_footnotes.glyph_readings",
                        new=lambda *args: ["1", "1", "1"],
                    ),
                    patch(
                        "numeric_footnotes.font_match",
                        return_value=[dict(page=2), dict(page=3)] if font else None,
                    ) as match,
                    patch("numeric_footnotes.traineddata_digest", return_value="test"),
                ):
                    result = recover_numeric_footnotes(
                        {1: [row, note]},
                        doc,
                        dict(note_starts={"1": 0.8}),
                        root,
                        [],
                        R(),
                    )
                self.assertEqual(result["linkable"], expected)
                if expected:
                    self.assertTrue(match.called)
                    self.assertEqual(row["text"], 'свое умение"1')
        doc.close()

    def test_bounded_call_overlay_keeps_following_words_and_style(self):
        import pymupdf
        from numeric_footnotes import recover_numeric_footnotes
        from vocabulary import TOKEN

        text = 'видел один глаз" . По другим словам.'
        row = dict(
            page=1,
            text=text,
            bbox=[0.1, 0.2, 0.9, 0.24],
            kind="text",
            inline=[
                dict(
                    start=text.index("другим"),
                    end=text.index("другим") + 6,
                    tags=["em"],
                )
            ],
        )
        note = dict(page=1, text="1 Название источника.", bbox=[0.1, 0.8, 0.9, 0.84])
        positions = {
            "видел": (10, 15),
            "один": (17, 21),
            "глаз": (23, 27),
            "По": (35, 37),
            "другим": (39, 45),
            "словам": (48, 54),
        }
        chars = []
        words = []
        for m in TOKEN.finditer(text):
            x, xx = positions[m.group()]
            words.append((x, 20, xx, 23, m.group()))
            for i in range(m.start(), m.end()):
                chars.append(dict(start=i, end=i + 1, bbox=[x, 20, xx, 23]))
        quote_at = text.index('"')
        dot_at = text.index(".")
        chars += [
            dict(start=quote_at, end=quote_at + 1, bbox=[27, 20, 28, 21]),
            dict(start=dot_at, end=dot_at + 1, bbox=[32, 22, 32.5, 23]),
        ]

        class Recognizer:
            version = "test-tesseract"

            def line_words(self, *a, **k):
                return dict(text=text, words=words, characters=chars)

        doc = pymupdf.open()
        doc.new_page(width=100, height=100)
        # 400-dpi positions place the raised digit between the quote and stop.
        with (
            tempfile.TemporaryDirectory() as work,
            patch("numeric_footnotes.footer_label", return_value=None),
            patch("numeric_footnotes.components", return_value=[]),
            patch("numeric_footnotes.prose_baseline", return_value=(17, 30, 0)),
            patch("numeric_footnotes.marker_boxes", return_value=[(109, 2, 113, 12)]),
            patch("numeric_footnotes.glyph_readings", new=lambda *args: ["1", "1"]),
            patch("numeric_footnotes.traineddata_digest", return_value="test"),
        ):
            report = recover_numeric_footnotes(
                {1: [row, note]},
                doc,
                dict(note_starts={"1": 0.8}),
                work,
                [],
                Recognizer(),
            )
        self.assertEqual(report["linkable"], 1)
        self.assertIn("По другим словам.", row["text"])
        styles = row["inline"]
        sup = next(s for s in styles if s["tags"] == ["sup"])
        em = next(s for s in styles if s["tags"] == ["em"])
        self.assertEqual(row["text"][sup["start"] : sup["end"]], "1")
        self.assertEqual(row["text"][em["start"] : em["end"]], "другим")
        doc.close()


class MissingRowTests(unittest.TestCase):
    def test_missing_prefix_requires_valid_existing_wrap(self):
        from partial_rows import corroborated_gap_text

        class Dictionary:
            def lookup(self, w):
                return w == "падал"

        target = "дал в обморок а во сне по ночам его мучали духи"
        partial = "в обморок а во сне по ночам его мучали духи"
        self.assertTrue(
            corroborated_gap_text(target, partial, "Часто па-", Dictionary())
        )
        self.assertFalse(
            corroborated_gap_text(target, partial, "Другие слова.", Dictionary())
        )
        self.assertFalse(
            corroborated_gap_text(
                target.replace("дал", "зак", 1), partial, "Часто па-", Dictionary()
            )
        )
        self.assertFalse(
            corroborated_gap_text(
                target, partial.replace("мучали", "посетили"), "Часто па-", Dictionary()
            )
        )

    def test_geometric_gap_needs_witness_and_normal_pitch(self):
        from partial_rows import gap_candidates

        text = "Обычная строка содержит несколько последовательных слов"
        rows = [
            dict(text=text + str(i), bbox=[0.05, y, 0.95, y + 0.015])
            for i, y in enumerate([0.1, 0.125, 0.15, 0.2, 0.225, 0.25])
        ]
        peer = dict(
            text="Вставленная строка имеет восемь отдельных слов которые продолжают рассказ",
            bbox=[0.05, 0.175, 0.85, 0.19],
        )
        self.assertEqual(len(gap_candidates(rows, [peer])), 1)
        self.assertFalse(gap_candidates(rows, []))
        self.assertFalse(
            gap_candidates(rows, [dict(peer, bbox=[0.05, 0.3, 0.85, 0.32])])
        )
        rows.insert(3, dict(peer))
        self.assertFalse(gap_candidates(rows, [peer]))


if __name__ == "__main__":
    unittest.main()
