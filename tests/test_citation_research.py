"""Expected citation numbers constrain observation, never manufacture a digit."""

import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from citation_research import (
    citation_searches,
    corroborate_observation,
    marker_witness_matches,
    title_marker_change,
    normalized_observations,
    placement_available,
    preserve_gap,
    research_missing_markers,
)


def fixture():
    rows = [
        dict(
            text="Earlier statement.1",
            bbox=[0.1, 0.3, 0.8, 0.33],
            inline=[dict(start=18, end=19, tags=["sup"])],
        ),
        dict(text="Later statement.", bbox=[0.1, 0.4, 0.8, 0.43]),
        dict(
            text="Final statement.3",
            bbox=[0.1, 0.5, 0.8, 0.53],
            inline=[dict(start=16, end=17, tags=["sup"])],
        ),
    ]
    # Derive offsets so fixture prose can change without silently changing the test.
    for row in (rows[0], rows[2]):
        row["inline"][0].update(start=len(row["text"]) - 1, end=len(row["text"]))
    return {1: rows}, dict(
        chapters=[[1, 1, "Body"], [2, 2, "References"]],
        endnote_sections=[dict(source_chapter=1, expected_notes=3, heading="Chapter")],
    )


def observation(number=2, readings=None, row=1):
    return dict(
        row=row,
        text="Later statement.",
        bbox=[0.6, 0.39, 0.62, 0.41],
        readings=readings or [str(number)] * 3,
        options=[
            dict(
                number=number,
                votes=(readings or [str(number)] * 3).count(str(number)),
                witness="Later statement.2",
                start=15,
                end=16,
                before=".",
                after=f".{number}",
                marker_start=16,
                marker_end=17,
            )
        ],
    )


class CitationResearchTests(unittest.TestCase):
    def run_research(self, observations, pages=None, book=None):
        default_pages, default_book = fixture()
        pages = copy.deepcopy(pages if pages is not None else default_pages)
        book = book if book is not None else default_book
        audit, review = [], []
        with (
            tempfile.TemporaryDirectory() as work,
            patch(
                "citation_research.observation_provenance",
                return_value={"fixture": "v1"},
            ),
            patch(
                "citation_research.normalized_observations", return_value=observations
            ),
        ):
            report = research_missing_markers(
                pages, [object()], book, Path(work), "source", audit, review
            )
        return pages, report, audit, review

    def test_witness_requires_same_word_gap_and_complete_digit(self):
        primary = "Earlier 1974 result. Later statement."
        gap = dict(start=primary.index("statement") + len("statement"))
        self.assertTrue(
            marker_witness_matches(
                primary, gap, "2", "Earlier 1974 result. Later statement.2"
            )
        )
        self.assertFalse(
            marker_witness_matches(
                primary, gap, "4", "Earlier 1974 result. Later statement."
            )
        )
        self.assertFalse(
            marker_witness_matches(
                primary, gap, "2", "Earlier result.2 Later statement."
            )
        )
        self.assertFalse(
            marker_witness_matches(
                primary, gap, "2", "Earlier result.2 Later statement.2"
            )
        )

    def test_title_citation_replaces_only_marker_before_parenthetical(self):
        primary = "published in Natural History* (reprinted later)"
        witness = "published in Natural History4 (reprinted later)"
        change = title_marker_change(primary, witness, "4")
        self.assertEqual(
            primary[: change["start"]] + change["after"] + primary[change["end"] :],
            witness,
        )
        self.assertTrue(marker_witness_matches(primary, change, "4", witness))
        self.assertIsNone(
            title_marker_change(
                primary, "published in Natural History4 (different continuation)", "4"
            )
        )
        self.assertIsNone(
            title_marker_change(
                "ordinary history (reprinted later)",
                "ordinary history4 (reprinted later)",
                "4",
            )
        )
        self.assertIsNone(
            title_marker_change("Natural Historyword (reprinted later)", witness, "4")
        )

    def test_raw_pixels_and_second_engine_resolve_digit_quote_confusion(self):
        image = Image.new("L", (100, 100), 255)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        page = SimpleNamespace(
            get_pixmap=lambda **kw: SimpleNamespace(
                tobytes=lambda fmt: stream.getvalue()
            )
        )
        row = dict(text="Later statement.”", bbox=[0.1, 0.4, 0.8, 0.5])
        proposed = dict(
            start=15, end=17, before=".”", after=".2", marker_start=16, marker_end=17
        )
        observed = dict(bbox=[0.7, 0.4, 0.72, 0.42], readings=["8", "8", "$"])
        witness = [dict(engine="rapid", text="Later statement.2", bbox=row["bbox"])]
        for raw, witnesses, accepted in (
            (["2", "2", "2"], witness, True),
            (["2", "2", "3"], witness, False),
            (["2", "2", "2"], [], False),
        ):
            with (
                tempfile.TemporaryDirectory() as work,
                patch(
                    "citation_research.scan.retry_readings",
                    return_value=dict(readings=raw),
                ),
                patch("citation_research.scan.line_crop", return_value=(image, 0, 0)),
                patch(
                    "citation_research.scan.character_line",
                    return_value=("Later statement.2", []),
                ),
                patch(
                    "citation_research.scan.replacement",
                    return_value=copy.deepcopy(proposed),
                ),
            ):
                result = corroborate_observation(
                    page, row, observed, witnesses, Path(work), dict(source="fixture")
                )
                self.assertEqual(bool(result["options"]), accepted)
                if accepted:
                    self.assertTrue(result["options"][0]["quote_glyph_confusion"])
                    self.assertEqual(result["options"][0]["after"], ".2")

    def test_search_bounds_use_same_page_positions_and_chapter_resets(self):
        pages, book = fixture()
        searches, anchors = citation_searches(pages, book)
        self.assertEqual(
            [(s["number"], s["lower"][:2], s["upper"][:2]) for s in searches],
            [(2, [1, 0], [1, 2])],
        )
        book["chapters"].insert(1, [2, 2, "Second body"])
        book["endnote_sections"].append(
            dict(source_chapter=2, expected_notes=1, heading="Second")
        )
        pages[2] = [dict(text="Unmarked prose.", bbox=[0.1, 0.3, 0.8, 0.33])]
        searches, _ = citation_searches(pages, book)
        self.assertEqual(
            [(s["chapter"], s["number"]) for s in searches], [(1, 2), (2, 1)]
        )

    def test_unique_fresh_crop_inserts_digit_and_shifts_later_style(self):
        pages, book = fixture()
        pages[1][1]["inline"] = [dict(start=0, end=16, tags=["em"])]
        pages, report, audit, _ = self.run_research([observation()], pages, book)
        self.assertEqual(pages[1][1]["text"], "Later statement.2")
        self.assertEqual(pages[1][1]["inline"][0]["end"], 17)
        self.assertEqual(pages[1][1]["inline"][1], dict(start=16, end=17, tags=["sup"]))
        self.assertEqual((report["applied"], report["remaining"]), (1, 0))
        self.assertEqual(audit[0]["kind"], "targeted-citation-recovery")

    def test_missing_pixels_competing_readings_and_duplicate_sites_abstain(self):
        for observations in (
            [],
            [observation(readings=["2", "2", "3"])],
            [observation(readings=["2", "2", "02"])],
            [observation(), observation()],
            [observation(number=1)],
        ):
            with self.subTest(observations=observations):
                pages, report, audit, _ = self.run_research(observations)
                self.assertEqual(pages[1][1]["text"], "Later statement.")
                self.assertEqual(report["applied"], 0)
                self.assertEqual(audit, [])

    def test_only_positive_symbol_evidence_can_discard_competing_site(self):
        valid = observation()
        valid.update(raw_readings=["2", "2", "2"])
        valid["options"][0]["raw_corroborated"] = True
        valid["options"][0]["corroborating_engines"] = [dict(engine="rapid")]
        for symbol, expected in ((True, 1), (False, 0)):
            competitor = observation(readings=["2", "2", "'"])
            competitor.update(raw_readings=["*", ":", ":"], raw_symbol_evidence=symbol)
            # A second location with inconsistent numeric reads stays ambiguous;
            # positively observed punctuation can rule out a numeric marker.
            if not symbol:
                competitor["readings"] = ["2", "2", "3"]
            _, report, _, _ = self.run_research([valid, competitor])
            self.assertEqual(report["applied"], expected)

    def test_candidate_before_lower_anchor_cannot_fill_missing_number(self):
        pages, report, audit, _ = self.run_research([observation(row=0)])
        self.assertEqual(report["applied"], 0)
        self.assertEqual(audit, [])

    def test_known_marker_and_existing_link_cannot_be_reused(self):
        for tags, extra in [(["sup"], {}), (["em"], {"href": "#target"})]:
            row = dict(inline=[dict(start=16, end=18, tags=tags, **extra)])
            self.assertFalse(
                placement_available(row, dict(marker_start=17, marker_end=18))
            )

    def test_overlapping_detached_digit_is_absorbed_after_verified_recovery(self):
        pages, book = fixture()
        pages[1].extend(
            [
                dict(text="2", bbox=[0.6, 0.39, 0.62, 0.41]),
                dict(text="4", bbox=[0.8, 0.39, 0.82, 0.41]),
            ]
        )
        pages, report, audit, _ = self.run_research([observation()], pages, book)
        self.assertEqual(pages[1][3]["kind"], "absorbed-note-marker")
        self.assertNotIn("kind", pages[1][4])
        self.assertEqual(audit[-1]["kind"], "absorbed-note-marker")

    def test_nonmonotonic_anchors_are_quarantined_without_rewriting(self):
        pages, book = fixture()
        pages[1][0]["text"] = "Earlier statement.3"
        pages[1][2]["text"] = "Final statement.1"
        result, report, audit, _ = self.run_research([observation()], pages, book)
        self.assertEqual(report["searches"][0]["status"], "applied")
        self.assertTrue(all(not a["trusted"] for a in report["anchors"]))
        self.assertEqual(result[1][0]["text"], pages[1][0]["text"])
        self.assertEqual(result[1][2]["text"], pages[1][2]["text"])

    def test_punctuation_preservation_and_year_anchor(self):
        self.assertEqual(
            preserve_gap(dict(before=".” 1 ", after='." 1 '))["after"], ".” 1 "
        )
        self.assertEqual(preserve_gap(dict(before=".®", after=".6"))["after"], ".6")
        self.assertIsNone(preserve_gap(dict(before="word", after="2")))
        self.assertIsNone(preserve_gap(dict(before=".", after='."2')))
        from scanned_notes import replacement

        witness = "In 1969.2 Not surprisingly"
        chars = [
            dict(start=i, end=i + 1, bbox=[i * 10, 0, i * 10 + 9, 10])
            for i in range(len(witness))
        ]
        change = replacement(
            "In 1969.2 Not surprisingly", witness, chars, (80, 0, 89, 10), "2"
        )
        self.assertEqual(change["before"], ".2 ")
        self.assertNotIn("1969", change["before"])

    def test_normalization_preserves_thin_raised_glyph_on_beige_paper(self):
        image = Image.new("L", (600, 150), 185)
        draw = ImageDraw.Draw(image)
        for x in range(30, 430, 40):
            draw.rectangle((x, 60, x + 20, 105), fill=40)
        draw.rectangle((450, 60, 454, 84), fill=40)
        data = io.BytesIO()
        image.save(data, format="PNG")
        page = SimpleNamespace(
            get_pixmap=lambda **kw: SimpleNamespace(tobytes=lambda fmt: data.getvalue())
        )
        row = dict(
            text="A long body line with enough prose to establish the baseline.",
            bbox=[0.04, 0.35, 0.82, 0.72],
        )
        with (
            patch(
                "citation_research.scan.glyph_readings", return_value=["1", "1", "1"]
            ) as readings,
            patch("citation_research.scan.character_line", return_value=("", [])),
            patch("citation_research.scan.marker_context", return_value=("", [])),
        ):
            normalized_observations(page, [row], 3)
        self.assertTrue(readings.called)
        box = readings.call_args.args[1]
        self.assertLess(box[2] - box[0], 8)

    def test_observation_cache_replays_without_ocr(self):
        pages, book = fixture()
        with (
            tempfile.TemporaryDirectory() as work,
            patch(
                "citation_research.observation_provenance",
                return_value={"fixture": "v1"},
            ),
            patch(
                "citation_research.normalized_observations",
                return_value=[observation()],
            ) as observe,
        ):
            first = research_missing_markers(
                copy.deepcopy(pages), [object()], book, Path(work), "source", [], []
            )
            second = research_missing_markers(
                copy.deepcopy(pages), [object()], book, Path(work), "source", [], []
            )
            self.assertEqual(first, second)
            self.assertEqual(observe.call_count, 1)
