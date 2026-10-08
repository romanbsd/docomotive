import sys
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from symbol_markers import (
    corroborated_symbol,
    match_symbol_font,
    disagreement_candidates,
    recover_disputed_marker,
    gap_glyphs,
)
from apparatus import link_symbol_footnotes


class SymbolMarkerTests(unittest.TestCase):
    def row(self, text, y=0.4):
        return dict(text=text, bbox=[0.1, y, 0.9, y + 0.02])

    def test_registered_confusion_requires_two_aligned_complete_star_readings(self):
        row = self.row("word® plants are discussed here.")
        target = "word* plants are discussed here."
        a = self.row("word * plants are discussed here.")
        b = self.row(target)
        self.assertIsNotNone(
            corroborated_symbol(row, dict(vision=[a], rapid=[b]), target)
        )
        self.assertIsNone(corroborated_symbol(row, dict(vision=[a]), target))
        self.assertIsNone(
            corroborated_symbol(
                row, dict(vision=[a], rapid=[self.row(target, 0.5)]), target
            )
        )
        self.assertIsNone(
            corroborated_symbol(
                row, dict(vision=[a], rapid=[self.row("word* unrelated text.")]), target
            )
        )

    def test_actual_registered_symbol_is_preserved(self):
        row = self.row("Brand® is registered.")
        self.assertIsNone(
            corroborated_symbol(
                row, dict(vision=[row], rapid=[row]), "Brand* is registered."
            )
        )

    def test_font_match_needs_two_distinct_pages_and_rejects_circle_and_quote(self):
        star = Image.new("L", (20, 20), 255)
        d = ImageDraw.Draw(star)
        for a, b in [
            ((2, 2), (18, 18)),
            ((2, 18), (18, 2)),
            ((10, 0), (10, 19)),
            ((0, 10), (19, 10)),
        ]:
            d.line((a, b), fill=0, width=3)
        bank = [dict(page=1, glyph=star, bbox=[0.1, 0.1, 0.2, 0.2], witnesses=[])]
        self.assertIsNone(match_symbol_font(star, bank))
        bank.append(dict(bank[0], page=2))
        self.assertIsNotNone(match_symbol_font(star, bank))
        circle = Image.new("L", (20, 20), 255)
        ImageDraw.Draw(circle).ellipse((1, 1, 18, 18), outline=0, width=2)
        self.assertIsNone(match_symbol_font(circle, bank))
        self.assertIsNone(match_symbol_font(star.resize((7, 9)), bank))

    def test_quoted_note_prefix_links_at_exact_body_symbol(self):
        for opening in ["“", '"', "‘", "«", "("]:
            with self.subTest(opening=opening):
                model = [
                    dict(
                        chapter=1,
                        blocks=[
                            dict(
                                kind="text",
                                text="First sentence. coca,* more text.",
                                sources=[dict(page=1, start=0, end=33)],
                            )
                        ],
                        notes=[
                            dict(
                                root_page=1,
                                id="note-1",
                                text="*" + opening + "the effects are described here.",
                            )
                        ],
                    )
                ]
                report = link_symbol_footnotes(model, dict(link_symbol_footnotes=True))
                self.assertEqual(report["symbol_links"], 1)
                b = model[0]["blocks"][0]
                s = b["inline"][0]
                self.assertEqual(b["text"][s["start"] : s["end"]], "*")
                self.assertEqual(b["text"][: s["start"]][-5:], "coca,")


class DisputedSymbolTests(unittest.TestCase):
    def row(self, text, y=0.4):
        return dict(text=text, bbox=[0.1, y, 0.9, y + 0.02], page=1)

    def candidates(self, primary, a, b, footer=False):
        return disagreement_candidates(
            self.row(primary),
            dict(vision=[self.row(a)], rapid=[self.row(b)]),
            footer=footer,
        )

    def test_arbitrary_quote_bullet_replacement_and_omission(self):
        for primary, a, b, before in [
            (
                "We encounter phenomena.” Then we continue.",
                "We encounter phenomena.* Then we continue.",
                "We encounter phenomena. Then we continue.",
                "”",
            ),
            (
                "We encounter phenomena.¤ Then we continue.",
                "We encounter phenomena.* Then we continue.",
                "We encounter phenomena. Then we continue.",
                "¤",
            ),
            (
                "We encounter phenomena. Then we continue.",
                "We encounter phenomena.* Then we continue.",
                "We encounter phenomena. Then we continue.",
                "",
            ),
        ]:
            with self.subTest(primary=primary):
                candidates = self.candidates(primary, a, b)
                self.assertEqual(len(candidates), 1)
                c = candidates[0]
                self.assertEqual(primary[c["start"] : c["end"]], before)
                self.assertEqual(primary[: c["start"]][-1], ".")

    def test_two_differing_nonstar_readings_can_trigger_scan_search(self):
        primary = "We encounter phenomena.¤ Then we continue."
        self.assertEqual(
            len(
                self.candidates(
                    primary, primary.replace("¤", "•"), primary.replace("¤", "")
                )
            ),
            1,
        )

    def test_footer_prefix_and_quotes_are_preserved(self):
        primary = "¤“These are the effects of the substance."
        c = self.candidates(
            primary, primary.replace("¤", ""), primary.replace("¤", "*"), footer=True
        )[0]
        self.assertEqual(primary[c["start"] : c["end"]], "¤")
        self.assertEqual(primary[c["end"] :][0], "“")

    def test_lexical_disagreement_digits_existing_star_and_short_lines_reject(self):
        base = "We encounter phenomena.” Then we continue."
        for primary, a, b in [
            (base, base.replace("phenomena", "other"), base.replace("”", "*")),
            (base.replace("”", "1"), base.replace("”", "*"), base.replace("”", "")),
            (base.replace("”", "*"), base.replace("”", ""), base.replace("”", "•")),
            ("Word.” Next.", "Word.* Next.", "Word. Next."),
        ]:
            self.assertFalse(self.candidates(primary, a, b))
        self.assertFalse(
            disagreement_candidates(
                self.row(base), dict(vision=[self.row(base.replace("”", "*"))])
            )
        )
        self.assertFalse(
            disagreement_candidates(
                self.row(base),
                dict(
                    vision=[self.row(base.replace("”", "*"))],
                    rapid=[self.row(base.replace("”", "*"), 0.6)],
                ),
            )
        )

    def test_scan_validation_rejects_no_match_and_ambiguous_components(self):
        from unittest.mock import patch

        glyph = Image.new("L", (20, 20), 255)
        row = self.row("We encounter phenomena.” Then we continue.")
        c = self.candidates(
            row["text"], row["text"].replace("”", "*"), row["text"].replace("”", "")
        )[0]
        item = dict(glyph=glyph, bbox=[0.2, 0.2, 0.21, 0.21])
        with (
            patch("symbol_markers.gap_glyphs", return_value=[item]),
            patch("symbol_markers.match_symbol_font", return_value=None),
        ):
            self.assertIsNone(recover_disputed_marker(None, row, c, [], None, 1))
        with (
            patch("symbol_markers.gap_glyphs", return_value=[item, item]),
            patch(
                "symbol_markers.match_symbol_font",
                return_value=[dict(page=2), dict(page=3)],
            ),
        ):
            self.assertIsNone(recover_disputed_marker(None, row, c, [], None, 1))
        with (
            patch("symbol_markers.gap_glyphs", return_value=[item]),
            patch(
                "symbol_markers.match_symbol_font",
                return_value=[dict(page=2), dict(page=3)],
            ),
        ):
            self.assertEqual(
                recover_disputed_marker(None, row, c, [], None, 1)["bbox"], item["bbox"]
            )

    def test_pixel_crop_preserves_period_and_requires_real_star(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        primary = "We encounter phenomena.” Then we continue."
        fresh = primary.replace("”", "*")
        row = self.row(primary)
        row["bbox"][3] = 0.44
        candidate = self.candidates(primary, fresh, primary.replace("”", ""))[0]
        star = Image.new("L", (20, 20), 255)
        draw = ImageDraw.Draw(star)
        for a, b in [
            ((1, 1), (18, 18)),
            ((1, 18), (18, 1)),
            ((10, 1), (10, 18)),
            ((1, 10), (18, 10)),
        ]:
            draw.line((a, b), fill=0, width=3)
        chars = []
        x = 100
        marker_x = None
        for i, char in enumerate(fresh):
            width = 20 if char == "*" else 8
            # Lowercase x-height dominates, with taller capitals/ascenders.
            top = 405 if char in "WThlbdfk" else 415
            chars.append(dict(start=i, end=i + 1, bbox=[x, top, x + width, 435]))
            if char == "*":
                marker_x = x
            x += width
        recognize = Mock()
        recognize.line_words.return_value = dict(text=fresh, characters=chars)
        page = SimpleNamespace(rect=SimpleNamespace(width=1000, height=1000))
        templates = [
            dict(page=n, glyph=star, bbox=[0, 0, 0.02, 0.02], witnesses=[])
            for n in [2, 3]
        ]
        image = Image.new("L", (1000, 1000), 255)
        image.paste(star, (marker_x, 404))
        with patch("symbol_markers.page_image", return_value=image):
            evidence = recover_disputed_marker(
                page, row, candidate, templates, recognize, 1
            )
            self.assertIsNotNone(evidence)
            self.assertGreaterEqual(evidence["bbox"][0], marker_x / 1000)
        # Even an identical star on the prose baseline is not a superscript.
        image = Image.new("L", (1000, 1000), 255)
        image.paste(star, (marker_x, 416))
        with patch("symbol_markers.page_image", return_value=image):
            self.assertIsNone(
                recover_disputed_marker(page, row, candidate, templates, recognize, 1)
            )
        # The same OCR disagreement cannot authorize a normal quotation mark.
        image = Image.new("L", (1000, 1000), 255)
        ImageDraw.Draw(image).line(
            (marker_x + 8, 410, marker_x + 8, 420), fill=0, width=2
        )
        with patch("symbol_markers.page_image", return_value=image):
            self.assertIsNone(
                recover_disputed_marker(page, row, candidate, templates, recognize, 1)
            )

    def test_page_recovery_needs_one_missing_call_and_one_verified_location(self):
        from copy import deepcopy
        from unittest.mock import patch
        from symbol_markers import repair_symbol_markers

        base = "We encounter phenomena.¤ Then we continue."
        for mode, expected in [
            ("missing", 1),
            ("ambiguous", 0),
            ("existing", 0),
            ("multiple-notes", 0),
            ("footer", 1),
        ]:
            with self.subTest(mode=mode):
                pages = {}
                banks = {}
                for number in [1, 2, 3]:
                    body = self.row(base if number == 1 else base.replace("¤", "*"))
                    note = self.row("* These are the effects of the substance.", 0.8)
                    body["page"] = note["page"] = number
                    pages[number] = [body, note]
                if mode == "ambiguous":
                    second = dict(pages[1][0], bbox=[0.1, 0.5, 0.9, 0.52])
                    pages[1].insert(1, second)
                if mode == "existing":
                    pages[1][0]["text"] = base.replace("¤", "*")
                if mode == "multiple-notes":
                    pages[1].append(dict(pages[1][-1], bbox=[0.1, 0.85, 0.9, 0.87]))
                if mode == "footer":
                    pages[1][0]["text"] = base.replace("¤", "*")
                    pages[1][-1]["text"] = pages[1][-1]["text"].replace("*", "¤")
                for number, rows in pages.items():
                    banks[number] = deepcopy(rows)
                    for row in banks[number]:
                        row["text"] = row["text"].replace("¤", "*")
                audit = []
                star = Image.new("L", (20, 20), 0)
                with (
                    patch("symbol_markers.Path.exists", return_value=True),
                    patch("symbol_markers.cache_path", return_value=Path("/cache")),
                    patch(
                        "symbol_markers.read_json",
                        side_effect=lambda path: dict(lines=banks[int(path.stem)]),
                    ),
                    patch(
                        "symbol_markers.prefix_glyph",
                        return_value=dict(glyph=star, bbox=[0, 0, 0.02, 0.02]),
                    ),
                    patch(
                        "symbol_markers.recover_disputed_marker",
                        return_value=dict(
                            bbox=[0, 0, 0.02, 0.02],
                            font_witnesses=[dict(page=2), dict(page=3)],
                        ),
                    ),
                ):
                    repair_symbol_markers(
                        pages,
                        [None] * 3,
                        dict(note_starts={"1": 0.75, "2": 0.75, "3": 0.75}),
                        Path("/work"),
                        audit,
                        None,
                    )
                edits = [v for v in audit if v.get("applied")]
                self.assertEqual(len(edits), expected)
                if expected:
                    self.assertEqual(edits[0]["before"], "¤")
                    self.assertEqual(edits[0]["after"], "*")

    def test_fresh_geometry_requires_all_lexical_anchors(self):
        from unittest.mock import Mock

        recognize = Mock()
        recognize.line_words.return_value = dict(
            text="Different text from another line.", characters=[]
        )
        self.assertEqual(
            gap_glyphs(
                None, self.row("These are the correct prose anchors."), 2, recognize
            ),
            [],
        )


class FullWidthQuoteTests(unittest.TestCase):
    def rows(self, smaller=True, introduced=True):
        result = []
        y = 0.08
        for i in range(10):
            text = "Normal prose contains enough words to fill the whole line."
            if i == 9:
                text = (
                    "The writer introduces the following:"
                    if introduced
                    else "A completed paragraph ends here."
                )
            result.append(
                dict(row_id=str(len(result)), text=text, bbox=[0.1, y, 0.9, y + 0.02])
            )
            y += 0.03
        y += 0.02
        for i in range(5):
            result.append(
                dict(
                    row_id=str(len(result)),
                    text=(
                        "Quote words filling a complete ordinary line."
                        if i == 4
                        else "Quote words filling a complete ordinary line"
                    ),
                    bbox=[0.1, y, 0.9, y + (0.018 if smaller else 0.02)],
                )
            )
            y += 0.025 if smaller else 0.03
        y += 0.02
        for i in range(10):
            result.append(
                dict(
                    row_id=str(len(result)),
                    text="Normal prose contains enough words to fill the whole line.",
                    bbox=[0.1, y, 0.9, y + 0.02],
                )
            )
            y += 0.03
        return result

    def test_introduced_smaller_tighter_run_is_quote_only_when_enabled(self):
        from paragraph_layout import infer_paragraph_layout

        for enabled, smaller, introduced, expected in [
            (False, True, True, False),
            (True, True, True, True),
            (True, False, True, False),
            (True, True, False, False),
        ]:
            with self.subTest(enabled=enabled, smaller=smaller, introduced=introduced):
                result, _ = infer_paragraph_layout(
                    self.rows(smaller, introduced), allow_full_width_quotes=enabled
                )
                quotes = [v for v in result.values() if v.get("kind") == "quote"]
                self.assertEqual(bool(quotes), expected)
                if expected:
                    self.assertEqual(len(quotes), 5)
                    self.assertTrue(all(v["quote_font_scale"] == 0.9 for v in quotes))


if __name__ == "__main__":
    unittest.main()
