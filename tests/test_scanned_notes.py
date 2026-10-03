"""Raised scan glyphs, conservative placement and book-wide reference checks."""

import json
import io
import sys
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from PIL import Image, ImageDraw
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scanned_notes import (
    raised_regions,
    replacement,
    select_sequence,
    add_retry_options,
    glyph_confusion_cost,
    complete_marker_crop,
    line_crop,
    glyph_readings,
    marker_context,
    absorb_marker_fragments,
    recover_reference_markers,
    RapidMarkerRecognizer,
)


def candidate(*readings, votes=2):
    return dict(
        status="candidate", options=[dict(number=n, votes=votes) for n in readings]
    )


def characters(text):
    return [
        dict(start=i, end=i + 1, bbox=[10 * i, 5, 10 * i + 8, 25])
        for i in range(len(text))
        if text[i] != " "
    ]


class ScannedNotesTests(unittest.TestCase):
    def test_recognition_only_fallback_keeps_observed_digits(self):
        image = Image.new("L", (100, 60), 255)
        with patch("scanned_notes.tesseract", return_value=b"||") as local:
            readings = glyph_readings(
                image, (20, 10, 40, 40), 20, recognizer=lambda crop: ["11", "11"]
            )
        self.assertEqual(readings, ["||", "||", "||", "11", "11"])
        self.assertEqual(local.call_count, 3)

    def test_recognition_only_confidence_gate_and_modes(self):
        recognizer = RapidMarkerRecognizer.__new__(RapidMarkerRecognizer)
        recognizer.provenance = dict(padding=[5, 20], minimum_score=0.75)
        from unittest.mock import Mock

        recognizer.engine = Mock(
            side_effect=[
                SimpleNamespace(txts=("11",), scores=(0.81,)),
                SimpleNamespace(txts=("12",), scores=(0.7,)),
            ]
        )
        self.assertEqual(recognizer(Image.new("L", (20, 30), 255)), ["11"])
        self.assertTrue(
            all(
                call.kwargs == dict(use_det=False, use_cls=False, use_rec=True)
                for call in recognizer.engine.call_args_list
            )
        )

    def test_reference_counter_requires_fresh_aligned_digit_and_restores_margin(self):
        book = dict(
            chapters=[[1, 1, "References"]],
            endnote_chapter=1,
            endnote_sections=[
                dict(heading="Section", expected_notes=2, source_chapter=1)
            ],
        )
        doc = pymupdf.open()
        doc.new_page(width=600, height=800)
        for observed, accepted in [
            ("1. Source content here", True),
            ("3. Source content here", False),
            ("1. Unrelated source words", False),
        ]:
            pages = {
                1: [
                    dict(text="Section", kind="heading", bbox=[0.3, 0.1, 0.6, 0.13]),
                    dict(text="Source content here", bbox=[0.2, 0.2, 0.6, 0.23]),
                    dict(text="2. Next entry", bbox=[0.1, 0.25, 0.5, 0.28]),
                ]
            }
            with (
                tempfile.TemporaryDirectory() as work,
                patch("scanned_notes.traineddata_digest", return_value="fixture"),
                patch(
                    "scanned_notes.subprocess.run",
                    return_value=SimpleNamespace(stdout=b"fixture"),
                ),
                patch(
                    "scanned_notes.character_line",
                    return_value=(observed, characters(observed)),
                ),
            ):
                audit = []
                recover_reference_markers(pages, doc, book, work, audit)
            self.assertEqual(pages[1][1]["text"].startswith("1. "), accepted)
            if accepted:
                self.assertLess(pages[1][1]["bbox"][0], 0.2)
                self.assertEqual(len(audit), 1)
            else:
                self.assertEqual(audit, [])

    def test_detached_marker_is_excluded_only_after_verified_recovery(self):
        from book_model import classify_row

        pages = {
            1: [
                dict(text="Prose4", bbox=[0.1, 0.2, 0.8, 0.25]),
                dict(text="4", bbox=[0.8, 0.19, 0.82, 0.21]),
                dict(text="5", bbox=[0.84, 0.19, 0.86, 0.21]),
            ]
        }
        marker = dict(
            page=1,
            row=0,
            number=4,
            bbox=[0.8, 0.19, 0.82, 0.21],
            status="sequence-ambiguous",
        )
        audit = []
        absorb_marker_fragments(pages, [marker], audit)
        self.assertNotIn("kind", pages[1][1])
        marker["status"] = "applied"
        absorb_marker_fragments(pages, [marker], audit)
        self.assertEqual(
            classify_row(1, pages[1][1], {}),
            ("excluded", "represented-by-recovered-note"),
        )
        self.assertNotIn("kind", pages[1][2])
        self.assertEqual(len(audit), 1)

    def test_variable_crop_reconnects_vertical_breaks_without_joining_digits(self):
        image = Image.new("L", (200, 100), 255)
        draw = ImageDraw.Draw(image)
        for x in (50, 60):
            draw.rectangle((x, 25, x + 3, 32), fill=170)
            draw.rectangle((x, 35, x + 3, 43), fill=170)
        row = dict(text="short tail", bbox=[0.1, 0.2, 0.8, 0.5])
        raw, _, _ = line_crop(image, row)
        changed, _, _ = line_crop(image, row, variable=True)
        from scanned_notes import components

        self.assertEqual(components(raw), [])
        self.assertEqual(len(components(changed)), 2)
        self.assertTrue(all(b[3] - b[1] == 19 for b in components(changed)))

    def test_variable_superscripts_keep_narrow_ones_and_larger_raised_digits(self):
        image = Image.new("L", (600, 100), 255)
        draw = ImageDraw.Draw(image)
        for x in range(10, 410, 25):
            draw.rectangle((x, 30, x + 15, 69), fill=0)
        draw.rectangle((450, 15, 453, 39), fill=0)
        draw.rectangle((480, 10, 494, 39), fill=0)
        draw.rectangle((510, 55, 513, 69), fill=0)
        self.assertEqual(raised_regions(image), [])
        self.assertEqual(
            raised_regions(image, variable=True),
            [(450, 15, 454, 40), (480, 10, 495, 40)],
        )

    def test_year_anchor_preserves_year_before_note(self):
        witness = "before 1920.7 These women"
        at = witness.index("7")
        change = replacement(
            "before 1920.? These women",
            witness,
            characters(witness),
            (10 * at, 5, 10 * at + 8, 25),
            "7",
        )
        self.assertEqual(change["after"], ".7 ")
        self.assertEqual(change["before"], ".? ")

    def test_capitalized_name_marker_does_not_replace_prose(self):
        witness = "Hopkins8 and others"
        change = replacement(
            "Hopkins® and others", witness, characters(witness), (70, 5, 78, 25), "8"
        )
        self.assertEqual(change["after"], "8 ")
        self.assertIsNone(
            replacement(
                "Hopkins o and others",
                witness,
                characters(witness),
                (70, 5, 78, 25),
                "8",
            )
        )

    def test_masked_marker_context_keeps_short_prose_and_punctuation(self):
        with patch(
            "scanned_notes.character_line", return_value=("it.", characters("it."))
        ):
            witness, chars = marker_context(
                Image.new("L", (80, 60), 255), (40, 5, 60, 25), "11"
            )
        change = replacement('it."', witness, chars, (40, 5, 60, 25), "11")
        self.assertEqual(change["after"], ".11")

    def test_nonnumeric_crop_readings_get_a_tight_character_retry(self):
        image = Image.new("L", (50, 50), 255)
        responses = [b"1]", b"qi", b"qi", b"tl", b"1]", b"11", b"11"]
        with patch("scanned_notes.tesseract", side_effect=responses):
            readings = glyph_readings(image, (10, 10, 30, 30), 16)
        self.assertEqual(readings.count("11"), 2)
        self.assertNotIn("9", readings)

    def test_numeric_initial_readings_do_not_repeat_ocr(self):
        with patch("scanned_notes.tesseract", side_effect=[b"9", b"9", b"9"]) as ocr:
            self.assertEqual(
                glyph_readings(Image.new("L", (50, 50), 255), (10, 10, 30, 30), 16),
                ["9"] * 3,
            )
        self.assertEqual(ocr.call_count, 3)

    def test_neighbor_cap_recovers_note_after_two_letter_final_line(self):
        image = Image.new("L", (160, 100), 255)
        draw = ImageDraw.Draw(image)
        for x in (10, 45):
            draw.rectangle((x, 50, x + 20, 77), fill=0)
        for x in (82, 93):
            draw.rectangle((x, 30, x + 6, 42), fill=0)
        draw.rectangle((110, 30, 125, 51), fill=0)
        self.assertNotIn((110, 30, 126, 52), raised_regions(image))
        self.assertEqual(raised_regions(image, cap_hint=44), [(110, 30, 126, 52)])

    def test_line_crop_retains_marker_beyond_ocr_right_edge(self):
        image = Image.new("L", (100, 80), 255)
        ImageDraw.Draw(image).rectangle((72, 15, 84, 37), fill=0)
        crop, left, top = line_crop(image, dict(bbox=[0.1, 0.2, 0.64, 0.84]))
        self.assertGreater(left + crop.width, 84)
        self.assertEqual(crop.getpixel((80 - left, 30 - top)), 0)

    def test_tight_retry_crop_includes_broken_adjacent_digit(self):
        image = Image.new("L", (120, 60), 255)
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 20, 30), fill=0)
        draw.rectangle((28, 8, 42, 18), fill=0)
        draw.rectangle((28, 25, 42, 30), fill=0)
        draw.rectangle(
            (46, 18, 56, 55), fill=0
        )  # Neighboring prose is clipped below the superscript.
        data = io.BytesIO()
        image.save(data, format="PNG")
        doc = pymupdf.open()
        page = doc.new_page(width=120, height=60)
        page.insert_image(page.rect, stream=data.getvalue())
        _, bounds = complete_marker_crop(page, [10 / 120, 10 / 60, 21 / 120, 31 / 60])
        self.assertGreater(bounds[2], 42 / 120)
        self.assertLess(bounds[2], 46 / 120)

    def test_raised_digits_detected_despite_skew_and_neighbor_fragments(self):
        im = Image.new("L", (1000, 120), 255)
        draw = ImageDraw.Draw(im)
        for i, x in enumerate(range(10, 780, 25)):
            bottom = 60 + round(x * 0.02)
            height = 43 if i % 3 == 0 else 28
            draw.rectangle((x, bottom - height, x + 12, bottom - 1), fill=0)
        for x in (850, 872):
            bottom = 60 + round(x * 0.02) - 23
            draw.rectangle((x, bottom - 22, x + 15, bottom - 1), fill=0)
        # Quotation marks, clipped neighbors and lowered numbers are not notes.
        draw.rectangle((820, 32, 824, 42), fill=0)
        draw.rectangle((100, 0, 115, 17), fill=0)
        draw.rectangle((400, 105, 415, 119), fill=0)
        draw.rectangle((940, 77, 955, 98), fill=0)
        self.assertEqual(raised_regions(im), [(850, 32, 888, 54)])

    def test_marker_replaced_at_matched_word_gap(self):
        source = "genetics.2* As he"
        change = replacement(
            "genetics.?3 As he", source, characters(source), (90, 5, 108, 25), "23"
        )
        self.assertEqual(change["before"], ".?3 ")
        self.assertEqual(change["after"], ".23 ")
        self.assertEqual((change["marker_start"], change["marker_end"]), (9, 11))

    def test_closing_quote_not_left_as_phantom_digits(self):
        source = "physics.”27"
        change = replacement(
            "physics. 2927", source, characters(source), (90, 5, 108, 25), "27"
        )
        self.assertEqual(change["before"], ". 2927")
        self.assertEqual(change["after"], '."27')

    def test_split_quote_and_citation_inside_parentheses(self):
        source = "action.’”3 In return"
        change = replacement(
            'action."3 In return', source, characters(source), (90, 5, 98, 25), "3"
        )
        self.assertEqual(change["after"], '."3 ')
        source = "Winnicott1) comes"
        change = replacement(
            "Winnicott) comes", source, characters(source), (90, 5, 98, 25), "1"
        )
        self.assertEqual(change["after"], "1) ")

    def test_prose_letters_and_adjacent_numeric_expressions_not_replaced(self):
        source = "genetics.2* As he"
        self.assertIsNone(
            replacement(
                "genetics. bad As he",
                source,
                characters(source),
                (90, 5, 108, 25),
                "23",
            )
        )
        source = "value 12.2* As he"
        self.assertIsNone(
            replacement(source, source, characters(source), (90, 5, 108, 25), "23")
        )
        source = "power2."
        self.assertIsNone(
            replacement(source, source, characters(source), (50, 5, 58, 25), "2")
        )

    def test_digit_misread_as_letter_and_unreadable_following_word(self):
        source = "personality”® that matters"
        change = replacement(
            'personality"s that matters',
            source,
            characters(source),
            (120, 5, 128, 25),
            "5",
        )
        self.assertEqual(change["after"], '"5 ')
        source = "result.2 garbled"
        change = replacement(
            "result.? Although", source, characters(source), (70, 5, 78, 25), "2"
        )
        self.assertEqual(change["after"], ".2 ")

    def test_sequence_resolves_ambiguous_crop_read_across_pages(self):
        items = [candidate(4), candidate(5, 9, votes=1), candidate(6)]
        items[0]["page"] = 1
        items[1]["page"] = items[2]["page"] = 2
        select_sequence(items, 12)
        self.assertEqual([p["number"] for p in items], [4, 5, 6])
        self.assertTrue(all(p["status"] == "selected" for p in items))

    def test_duplicate_positions_remain_ambiguous(self):
        items = [candidate(1), candidate(2), candidate(2), candidate(3)]
        select_sequence(items, 3)
        self.assertEqual(
            [p["status"] for p in items],
            ["selected", "sequence-ambiguous", "sequence-ambiguous", "selected"],
        )

    def test_sequence_retry_resolves_six_misread_as_duplicate_eight(self):
        wrong = candidate(8)
        wrong["options"][0].update(
            start=0, end=2, before=".®", after=".8", marker_start=1, marker_end=2
        )
        items = [candidate(5), wrong, candidate(8)]
        select_sequence(items, 9)
        self.assertEqual(wrong["status"], "sequence-ambiguous")
        add_retry_options(wrong, ["8", "6", "8", "6"], 9)
        select_sequence(items, 9)
        self.assertEqual([p["number"] for p in items], [5, 6, 8])
        self.assertEqual(wrong["after"], ".6")

    def test_sequence_alone_never_supplies_unread_digit(self):
        wrong = candidate(8)
        items = [candidate(5), wrong, candidate(8)]
        select_sequence(items, 9)
        add_retry_options(wrong, ["8", "", "eight", "99"], 9)
        select_sequence(items, 9)
        self.assertNotIn(6, [o["number"] for o in wrong["options"]])
        self.assertEqual(wrong["status"], "sequence-ambiguous")

    def test_single_weak_read_needs_neighbors_and_sequence_resets(self):
        items = [candidate(3, votes=1)]
        select_sequence(items, 6)
        self.assertEqual(items[0]["status"], "recognition-needs-neighbors")
        for _ in range(2):
            chapter = [candidate(1), candidate(2)]
            select_sequence(chapter, 2)
            self.assertEqual([p["number"] for p in chapter], [1, 2])

    def test_similar_glyph_prior_is_weaker_than_consecutive_evidence(self):
        self.assertLess(glyph_confusion_cost("8", "6"), glyph_confusion_cost("4", "6"))
        self.assertLess(glyph_confusion_cost("2", "9"), glyph_confusion_cost("4", "9"))
        self.assertEqual(glyph_confusion_cost("26", "28"), 0.5)

    def test_sequence_retry_recovers_nine_from_conflicting_two(self):
        wrong = candidate(2, votes=1)
        wrong["options"][0].update(
            start=0, end=2, before=".°", after=".2", marker_start=1, marker_end=2
        )
        items = [candidate(8), wrong]
        select_sequence(items, 9)
        self.assertEqual(wrong["status"], "chapter-order-conflict")
        add_retry_options(wrong, ["9", "9", "9", ""], 9)
        select_sequence(items, 9)
        self.assertEqual(wrong["number"], 9)
        self.assertEqual(wrong["after"], ".9")

    def test_neighboring_four_and_six_support_retried_five(self):
        wrong = candidate(3, votes=1)
        wrong["options"][0].update(
            start=0, end=2, before='."', after=".3", marker_start=1, marker_end=2
        )
        items = [candidate(4), wrong, candidate(6)]
        select_sequence(items, 9)
        add_retry_options(wrong, ["5", "5", "5", "5"], 9)
        select_sequence(items, 9)
        self.assertEqual([p["number"] for p in items], [4, 5, 6])

    def test_organism_page_197_notes_have_exact_inline_targets(self):
        out = Path(__file__).resolve().parents[1] / "output/feeling-organism"
        if not (out / "scanned-endnote-analysis.json").exists():
            self.skipTest("Build organism with scan note recovery first")
        model = json.loads((out / "book-model.json").read_text())
        chapter = model[16]
        markers = {}
        for block in chapter["blocks"]:
            for s in block.get("inline", []):
                if s.get("noteref"):
                    markers[int(block["text"][s["start"] : s["end"]])] = (block, s)
        for n in range(22, 28):
            block, style = markers[n]
            self.assertEqual(style["href"], f"chapter-20.xhtml#endnote-17-{n}")
            self.assertTrue(any(s["page"] == 197 for s in block["sources"]))
            note = next(
                b for b in model[19]["blocks"] if b.get("id") == f"endnote-17-{n}"
            )
            self.assertIn(f'chapter-17.xhtml#{style["anchor"]}', note["backlinks"])
        self.assertIn("important issues of genetics.23 As he", markers[23][0]["text"])
        self.assertIn('physics."27', markers[27][0]["text"])
        self.assertNotIn("2927", markers[27][0]["text"])

    def test_organism_previously_ambiguous_markers_have_verified_numbers(self):
        out = Path(__file__).resolve().parents[1] / "output/feeling-organism"
        if not (out / "scanned-endnote-analysis.json").exists():
            self.skipTest("Build organism fixture first")
        records = json.loads((out / "scanned-endnote-analysis.json").read_text())[
            "candidates"
        ]
        for page, number in ((84, 6), (91, 8), (128, 9), (178, 5), (220, 12)):
            marker = next(
                c for c in records if c["page"] == page and c.get("number") == number
            )
            self.assertEqual(marker["status"], "applied")
            self.assertEqual(marker["number"], number)
            if page == 220:
                self.assertEqual(marker["after"], ".12")

    def test_organism_preface_and_chapter_one_reported_markers(self):
        out = Path(__file__).resolve().parents[1] / "output/feeling-organism"
        if not (out / "scanned-endnote-analysis.json").exists():
            self.skipTest("Build organism fixture first")
        model = json.loads((out / "book-model.json").read_text())
        for chapter, number, page in ((6, 3, 21), (6, 6, 23), (8, 9, 40), (8, 11, 42)):
            href = f"chapter-20.xhtml#endnote-{chapter}-{number}"
            blocks = [
                b
                for b in model[chapter - 1]["blocks"]
                if any(s.get("href") == href for s in b.get("inline", []))
            ]
            self.assertEqual(len(blocks), 1)
            self.assertTrue(any(s["page"] == page for s in blocks[0]["sources"]))
            if number == 11:
                self.assertEqual(blocks[0]["kind"], "quote")
                self.assertIn("within biology.11", blocks[0]["text"])
        preface_numbers = {
            int(b["text"][s["start"] : s["end"]])
            for b in model[5]["blocks"]
            for s in b.get("inline", [])
            if s.get("noteref")
        }
        self.assertEqual(preface_numbers, set(range(1, 7)))

    def test_organism_final_seven_markers_and_complete_backlinks(self):
        out = Path(__file__).resolve().parents[1] / "output/feeling-organism"
        if not (out / "book-model.json").exists():
            self.skipTest("Build organism fixture first")
        model = json.loads((out / "book-model.json").read_text())
        for chapter, number, page in (
            (10, 7, 85),
            (11, 8, 106),
            (11, 11, 107),
            (12, 1, 111),
            (15, 3, 161),
            (17, 28, 199),
            (18, 3, 206),
        ):
            href = f"chapter-20.xhtml#endnote-{chapter}-{number}"
            blocks = [
                b
                for b in model[chapter - 1]["blocks"]
                if any(s.get("href") == href for s in b.get("inline", []))
            ]
            self.assertEqual(len(blocks), 1, (chapter, number))
            self.assertTrue(any(s["page"] == page for s in blocks[0]["sources"]))
        notes = [
            b for b in model[19]["blocks"] if b.get("id", "").startswith("endnote-")
        ]
        self.assertEqual(len(notes), 157)
        self.assertTrue(all(b.get("backlinks") for b in notes))
        self.assertFalse(
            any("plantations poo" in b["text"] for b in model[14]["blocks"])
        )


if __name__ == "__main__":
    unittest.main()
