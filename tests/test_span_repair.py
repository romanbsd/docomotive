"""Repairs need physical seams, assembled vocabulary and source witnesses."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import apply_edits
from book_model import JoinPolicy, reconstruct
from lexical_repair import ContextRanker, CropRecognizer, locate_word
from span_repair import repair_spans, corroborates, seam_candidates
from numeric_footnotes import (
    marker_boxes,
    font_match,
    marker_strip,
)
from PIL import Image, ImageDraw


class Dictionary:
    def lookup(self, word):
        return word in {
            "болезни",
            "практика",
            "принимают",
            "ему",
            "им",
            "ем",
            "было",
            "страны",
            "шаманки",
        }


def case(left, right):
    rows = [
        dict(
            page=1,
            row_id=str(i),
            text=t,
            bbox=[0.1, 0.2 + 0.025 * i, 0.9, 0.222 + 0.025 * i],
            column=0,
        )
        for i, t in enumerate([left, right])
    ]
    text = left + " " + right
    sources = [
        dict(rows[0], start=0, end=len(left)),
        dict(rows[1], start=len(left) + 1, end=len(text)),
    ]
    return [
        dict(
            chapter=1,
            source_pages=[1, 1],
            blocks=[dict(kind="text", text=text, sources=sources)],
            notes=[],
        )
    ], rows


class SpanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        model, _ = case(
            "Болезни болезни практика практика принимают принимают",
            "У нганасанской шаманки. У нганасанской шаманки. шаманки. Ему было важно. Ему было важно. Ему было важно.",
        )
        cls.ranker = ContextRanker(model, {"language": "ru"}, Dictionary(), ["баруси"])

    def run_span(self, left, right, readings=None):
        model, rows = case(left, right)

        def recognize(row, old, new):
            actual = {"б": "бо", "лезни": "лезни", "Wa": "ша", "Ем": "Ему"}.get(
                old, old
            )
            return dict(readings=readings or [actual, actual])

        with patch(
            "span_repair.composite_crop", return_value={"engine": "test-fragments"}
        ):
            result = repair_spans(model, {1: rows}, {}, self.ranker, recognize, [])
        return result, rows

    def test_split_words_and_mixed_scripts(self):
        for left, right, a, b in [
            ("было б", "лезни.", "было бо", "лезни."),
            ("прак.", "тика", "прак", "тика"),
            ("принима-", "• ют ее", "принима", "ют ее"),
            ("У нганасанской Wa-", "манки", "У нганасанской ша", "манки"),
            ("был бару.", "си", "был бару", "си"),
        ]:
            with self.subTest(left=left):
                result, rows = self.run_span(left, right)
                self.assertEqual(result["corrected"], 1)
                self.assertEqual([r["text"] for r in rows], [a, b])
                self.assertEqual(rows[1]["word_continuation_from"], "0")

    def test_crop_conflict_abstains_and_valid_words_do_not_join(self):
        result, rows = self.run_span("прак.", "тика", ["парк", "прак"])
        self.assertEqual(result["corrected"], 0)
        self.assertEqual(rows[0]["text"], "прак.")
        model, _ = case("ему", "было важно")
        self.assertEqual(list(seam_candidates(model[0]["blocks"][0], self.ranker)), [])
        model, _ = case("прак.", "тика")
        model[0]["blocks"][0]["sources"][1]["page"] = 2
        self.assertEqual(list(seam_candidates(model[0]["blocks"][0], self.ranker)), [])

    def test_short_valid_token_with_damaged_terminal_glyph(self):
        result, rows = self.run_span("Ем:", "было важно")
        self.assertTrue(
            any(
                d["before"] == "Ем:" and d["action"] == "correct"
                for d in result["decisions"]
            )
        )
        self.assertEqual(rows[0]["text"], "Ему")
        result, rows = self.run_span("Ем:", "было важно", ["емо", "емо"])
        self.assertEqual(rows[0]["text"], "Ем:")

    def test_valid_prefix_wrap_and_exact_join_outrank_changed_inflection(self):
        class D(Dictionary):
            def lookup(self, word):
                return word in {
                    "представ",
                    "представлялся",
                    "которых",
                    "которые",
                } or super().lookup(word)

        training, _ = case(
            "представлялся представлялся представлялся",
            "которые которые которых которых",
        )
        ranker = ContextRanker(training, {"language": "ru"}, D())
        for left, right, target in [
            ("обычно представ.", "лялся разделенным", "представлялся"),
            ("на кото-", "• рых вьет", "которых"),
        ]:
            model, rows = case(left, right)
            recognize = lambda row, old, new: dict(readings=[new, new])
            with (
                patch("span_repair.composite_crop", return_value={}),
                patch.object(ranker, "rank", wraps=ranker.rank),
            ):
                result = repair_spans(model, {1: rows}, {}, ranker, recognize, [])
            self.assertEqual(result["corrected"], 1)
            self.assertEqual(result["decisions"][0]["after"], target)
        model, _ = case("представ.", "было важно")
        self.assertEqual(list(seam_candidates(model[0]["blocks"][0], ranker)), [])

    def test_numeric_homoglyphs_and_unchanged_fragment_guard(self):
        self.assertTrue(corroborates({"readings": ["60", "60"]}, "бо"))
        self.assertFalse(corroborates({"readings": ["емо", "емо"]}, "ему"))
        evidence = {
            "readings": ["о манки", "о манки"],
            "alternate_crop": {"readings": ["манки", "манки"], "scores": [0.8, 0.8]},
        }
        self.assertTrue(corroborates(evidence, "манки", "манки"))
        self.assertFalse(corroborates(evidence, "манки", "манкн"))

    def test_exact_fragment_edit_and_style_offsets(self):
        row = dict(page=1, text="было б", inline=[dict(start=5, end=6, tags=["em"])])
        apply_edits(
            [row],
            [dict(page=1, before="б", after="бо", prefix="было ", suffix="", count=1)],
            [],
            "test",
        )
        self.assertEqual(row["text"], "было бо")
        self.assertEqual(row["inline"][0]["end"], 7)

    def test_verified_fragment_reconstruction_has_no_space(self):
        rows = [
            dict(text="Было бо", bbox=[0.1, 0.2, 0.9, 0.22], column=0),
            dict(text="лезни.", bbox=[0.1, 0.225, 0.9, 0.245], column=0),
        ]
        from book_model import row_id

        rows[1]["word_continuation_from"] = row_id(1, rows[0], "")
        model = reconstruct(
            {1: rows},
            dict(
                chapters=[[1, 1, "Title"]],
                chapter_heading=False,
                chapter_body_starts={"1": 0.1},
            ),
            JoinPolicy(),
            [],
        )
        self.assertEqual(model[0]["blocks"][0]["text"], "Было болезни.")
        self.assertEqual(len(model[0]["blocks"][0]["sources"]), 2)

    def test_exact_edge_word_can_anchor_with_corrupted_neighbor(self):
        words = [
            (0, 0, 10, 10, "манки"),
            (15, 0, 30, 10, "Нобуши"),
            (31, 0, 35, 10, "е"),
            (40, 0, 50, 10, "умер"),
        ]
        self.assertIsNone(locate_word("манки Нобуптие, умер-", words, "манки", "манки"))
        self.assertEqual(
            locate_word(
                "манки Нобуптие, умер-", words, "манки", "манки", edge_anchor=True
            ),
            words[0],
        )


class CropVariantTests(unittest.TestCase):
    def test_conflicted_word_uses_bounded_variants_and_retains_raw_evidence(self):
        recognizer = CropRecognizer.__new__(CropRecognizer)
        recognizer.language = "rus"
        calls = []

        def crop(row, word, candidate, **options):
            calls.append(options.get("variant", 0))
            value = "важнеиших" if not options.get("variant") else "важнейших"
            return dict(readings=[value, value])

        recognizer.recognize_crop = crop
        result = recognizer({}, "важнеиших", "важнейших")
        self.assertEqual(calls, [0, 1])
        self.assertEqual(result["readings"], ["важнейших", "важнейших"])
        self.assertEqual(
            result["crop_variants"][0]["readings"], ["важнеиших", "важнеиших"]
        )
        calls.clear()
        recognizer.recognize_crop = lambda *args, **kwargs: (
            calls.append(kwargs.get("variant", 0)) or dict(readings=["wrong", "wrong"])
        )
        result = recognizer({}, "damaged", "correct")
        self.assertEqual(calls, [0, 1, 2])
        self.assertEqual(result["readings"], ["wrong", "wrong"])
        calls.clear()
        recognizer.language = "eng"
        recognizer({}, "damaged", "correct")
        self.assertEqual(calls, [0])


class NumericShapeTests(unittest.TestCase):
    def test_xheight_body_strip_keeps_full_superscript_and_rejects_quote(self):
        from types import SimpleNamespace
        from scanned_notes import components, prose_baseline

        image = Image.new("L", (220, 100), 255)
        draw = ImageDraw.Draw(image)
        for x in [20, 40, 60, 80, 100, 120, 140]:
            draw.rectangle((x, 50, x + 8, 63), fill=0)
        draw.rectangle((180, 37, 183, 48), fill=0)
        draw.rectangle((165, 43, 167, 46), fill=0)
        page = SimpleNamespace(rect=SimpleNamespace(width=220, height=100))
        crop, left, top, cap = marker_strip(
            image,
            dict(bbox=[0.09, 0.5, 0.9, 0.64]),
            [(20, 50, 148, 64, "word")],
            page,
            clearance=1.2,
        )
        boxes = components(crop)
        regions = marker_boxes(boxes, prose_baseline(boxes, cap))
        self.assertEqual(
            [(b[0] + left, b[1] + top, b[2] + left, b[3] + top) for b in regions],
            [(180, 37, 184, 49)],
        )

    def test_exclamation_dot_rejects_stem_but_not_serif_one(self):
        boxes = [(10, 5, 15, 17), (30, 7, 34, 17), (30, 20, 33, 23), (40, 4, 42, 9)]
        self.assertEqual(marker_boxes(boxes, (20, 28, 0)), [boxes[0]])

    def test_font_matching_requires_two_independent_pages(self):
        one = Image.new("L", (7, 18), 255)
        draw = ImageDraw.Draw(one)
        draw.line((3, 1, 3, 16), fill=0, width=2)
        draw.line((1, 16, 6, 16), fill=0)
        templates = [dict(page=1, bbox=[], glyph=one)]
        self.assertIsNone(font_match(one, templates, "1"))
        templates.append(dict(page=2, bbox=[], glyph=one))
        self.assertIsNotNone(font_match(one, templates, "1"))
        eight = Image.new("L", (12, 18), 255)
        ImageDraw.Draw(eight).ellipse((0, 0, 11, 17), outline=0, width=3)
        self.assertIsNone(font_match(eight, templates, "1"))
        self.assertIsNone(font_match(one, templates, "2"))
        stem = Image.new("L", (2, 16), 0)
        self.assertIsNone(font_match(stem, templates, "1"))
        # A full-height narrow footer prefix needs its own isolated geometry;
        # short quote strokes remain excluded in both contexts.
        skinny_templates = [
            dict(page=n, bbox=[], glyph=Image.new("L", (5, 16), 0)) for n in (1, 2)
        ]
        self.assertIsNotNone(
            font_match(stem, skinny_templates, "1", isolated_prefix=True)
        )
        self.assertIsNone(font_match(stem, skinny_templates, "1"))
        short = Image.new("L", (2, 6), 0)
        self.assertIsNone(
            font_match(short, skinny_templates, "1", isolated_prefix=True)
        )


if __name__ == "__main__":
    unittest.main()
