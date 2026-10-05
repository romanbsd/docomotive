"""Evidence-backed typography and artwork boundaries on synthetic sources."""

import sys
import unittest
import json
import io
from pathlib import Path
from unittest.mock import patch, Mock

import pymupdf
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from typography import (
    attach_typography,
    attach_scan_font_metrics,
    verse_evidence,
    inset_verse_evidence,
    image_only_headings,
    attach_numeric_superscripts,
)
from native_pdf import extract_page
from ocr import merge_rows, correct_page
from figures import outside_figures, crop_artwork
from PIL import Image
from book_model import reconstruct, JoinPolicy
from render_text import block_html
from build import opf_metadata


def row(text, i=0, width=0.35, italic=False, x=0.2):
    r = dict(
        text=text,
        bbox=[x, 0.2 + i * 0.025, x + width, 0.22 + i * 0.025],
        column=0,
        row_id=str(i),
        font_size=11,
    )
    if italic:
        r["inline"] = [dict(start=0, end=len(text), tags=["em"])]
    return r


class TypographyTests(unittest.TestCase):
    def test_introduced_ragged_inset_verse_needs_no_font_metadata(self):
        prose = [
            row("Ordinary surrounding prose.", i=i, width=0.75, x=0.1) for i in range(3)
        ]
        intro = row("A speaker begins:", i=3, width=0.4, x=0.13)
        chant = [
            row("some lower case words", i=4 + i, width=w, x=0.24)
            for i, w in enumerate((0.32, 0.25, 0.45, 0.30, 0.57, 0.23))
        ]
        for r in prose + [intro] + chant:
            r.pop("font_size", None)
        expected = {r["row_id"] for r in chant}
        self.assertEqual(inset_verse_evidence(prose + [intro] + chant, 0.8), expected)
        for change in (
            "no-introduction",
            "prose-wrap",
            "wandering",
            "hyphen",
            "column",
        ):
            import copy

            values = copy.deepcopy(prose + [intro] + chant)
            if change == "no-introduction":
                values[3]["text"] = "An ordinary sentence."
            if change == "prose-wrap":
                for r in values[4:]:
                    r["bbox"][2] = r["bbox"][0] + 0.65
            if change == "wandering":
                for i, r in enumerate(values[4:]):
                    r["bbox"][0] += i * 0.02
            if change == "hyphen":
                values[4]["text"] += "-"
            if change == "column":
                for r in values[4:]:
                    r["column"] = 1
            with self.subTest(change=change):
                self.assertFalse(inset_verse_evidence(values, 0.8))

    def test_numeric_attachment_reuses_text_only_extraction_with_scan_image(self):
        doc = pymupdf.open()
        self.addCleanup(doc.close)
        page = doc.new_page(width=300, height=300)
        image = io.BytesIO()
        Image.new("RGB", (300, 300), "white").save(image, format="PNG")
        page.insert_image(page.rect, stream=image.getvalue())
        text = "A source quotation."
        page.insert_text((30, 100), text, fontsize=12)
        x = 30 + pymupdf.get_text_length(text, fontsize=12)
        page.insert_text((x, 95), "3", fontsize=7)
        expected = extract_page(page, {}, 1, [])
        data = page.get_text(
            "dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        )
        self.assertFalse(any("image" in block for block in data["blocks"]))
        # Supplying extracted data preserves text, offsets and font geometry.
        with patch.object(page, "get_text", side_effect=AssertionError("re-extracted")):
            self.assertEqual(extract_page(page, {}, 1, [], text_data=data), expected)
        rows = [dict(text=text + "3", bbox=expected[0]["bbox"], column=0)]
        with patch.object(page, "get_text", wraps=page.get_text) as extraction:
            attach_numeric_superscripts(rows, page, 1, [])
        extraction.assert_called_once_with(
            "dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        )
        self.assertEqual(rows[0]["inline"], [dict(start=19, end=20, tags=["sup"])])

    def test_ambiguous_superscript_needs_crop_corroboration_before_replacement(self):
        doc = pymupdf.open()
        self.addCleanup(doc.close)
        page = doc.new_page(width=400, height=300)
        text = "A source quotation."
        page.insert_text((30, 100), text, fontsize=12)
        x = 30 + pymupdf.get_text_length(text, fontsize=12)
        page.insert_text((x, 95), "6", fontsize=7)
        page.insert_text((x + 5, 100), " Another sentence follows.", fontsize=12)
        source = extract_page(page, {}, 1, [])[0]
        value = source["text"].replace("6", "*®")
        for readings, accepted in [
            (["6", "6", "6"], True),
            (["6", "8", "6"], False),
            (["6", "*", "*"], False),
        ]:
            item = dict(text=value, bbox=source["bbox"], column=0, page=1)
            with patch(
                "typography.numeric_glyph_readings",
                return_value={"readings": readings, "evidence_sha256": "fixture"},
            ):
                attach_numeric_superscripts([item], page, 1, [])
            if accepted:
                self.assertNotIn("*", item["text"])
                self.assertEqual(item["text"][item["inline"][0]["start"]], "6")
            else:
                self.assertEqual(item["text"], value)
                self.assertNotIn("inline", item)

    def test_numeric_superscript_geometry_transfers_only_complete_matching_digits(self):
        doc = pymupdf.open()
        self.addCleanup(doc.close)
        page = doc.new_page(width=300, height=300)
        text = "A source quotation."
        page.insert_text((30, 100), text, fontsize=12)
        x = 30 + pymupdf.get_text_length(text, fontsize=12)
        page.insert_text((x, 95), "3", fontsize=7)
        source = extract_page(page, {}, 1, [])[0]
        rows = [dict(text=text + "3", bbox=source["bbox"], column=0)]
        audit = []
        attach_numeric_superscripts(rows, page, 1, audit)
        self.assertEqual(rows[0]["inline"], [dict(start=19, end=20, tags=["sup"])])
        self.assertEqual(len(audit), 1)
        block = dict(
            kind="text", text=rows[0]["text"], page_breaks=[], inline=rows[0]["inline"]
        )
        self.assertIn(
            "quotation.<sup>3</sup>", block_html(block, {}, set(), [], "test")
        )
        # Matching a single digit of a year or a changed OCR reading is unsafe.
        for value in [text + "30", text + "8"]:
            candidate = dict(text=value, bbox=source["bbox"], column=0)
            attach_numeric_superscripts([candidate], page, 1, [])
            self.assertNotIn("inline", candidate)

    def test_numeric_superscript_rejects_spurious_flags_and_small_baseline_digits(self):
        doc = pymupdf.open()
        self.addCleanup(doc.close)
        page = doc.new_page(width=300, height=300)
        text = "A source quotation."
        page.insert_text((30, 100), text, fontsize=12)
        x = 30 + pymupdf.get_text_length(text, fontsize=12)
        page.insert_text((x, 100), "3", fontsize=7)
        source = extract_page(page, {}, 1, [])[0]
        # Simulate an OCR layer falsely flagging its small digit as raised.
        source["inline"] = [dict(start=19, end=20, tags=["sup"])]
        rows = [dict(text=text + "3", bbox=source["bbox"], column=0)]
        data = page.get_text("dict")
        for block in data["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"] == "3":
                        span["flags"] |= 1
        proxy = Mock(rect=page.rect)
        proxy.get_text.return_value = data
        with patch("typography.extract_page", return_value=[source]):
            attach_numeric_superscripts(rows, proxy, 1, [])
        self.assertNotIn("inline", rows[0])

        # A raised ordinary word must never inherit a citation style.
        source["inline"] = [dict(start=2, end=8, tags=["sup"])]
        with patch("typography.extract_page", return_value=[source]):
            attach_numeric_superscripts(rows, page, 1, [])
        self.assertNotIn("inline", rows[0])

    def test_sparse_image_epigraph_requires_credit_alignment_and_quote(self):
        rows = [
            row('A quotation ends here."', 0, width=0.7, x=0.1),
            row("(A Source Author)", 1, x=0.7),
        ]
        audit = []
        image_only_headings(
            rows,
            {"chapters": [[1, 1, "Epigraphs"]], "chapter_body_starts": {"1": 0}},
            1,
            audit,
        )
        self.assertEqual(rows[1]["kind"], "attribution")
        rows[1].pop("kind")
        rows[0]["text"] = "An ordinary prose line."
        image_only_headings(
            rows,
            {"chapters": [[1, 1, "Epigraphs"]], "chapter_body_starts": {"1": 0}},
            1,
            [],
        )
        self.assertNotIn("kind", rows[1])

    def test_optional_prose_indent_uses_reconstructed_source_evidence(self):
        block = dict(
            kind="text", text="Source paragraph", page_breaks=[], continuation=True
        )
        self.assertIn(
            'class="noindent"',
            block_html(block, {"source_prose_indents": True}, set(), [], "test"),
        )
        self.assertNotIn('class="noindent"', block_html(block, {}, set(), [], "test"))
        block["continuation"] = False
        self.assertNotIn(
            'class="noindent"',
            block_html(block, {"source_prose_indents": True}, set(), [], "test"),
        )
        block["continuation"] = True
        self.assertIn(
            'class="reference"',
            block_html(
                block,
                {"source_prose_indents": True, "_hanging": True},
                set(),
                [],
                "test",
            ),
        )

    def test_image_heading_uses_center_pitch_and_rejects_sentence_tails(self):
        rows = [
            row(
                "A substantial prose line with enough words for estimating the body line pitch.",
                i,
                width=0.7,
                x=0.1,
            )
            for i in range(12)
        ]
        rows[4]["text"] = "A Section Heading"
        rows[4]["bbox"][1] += 0.005
        rows[4]["bbox"][3] += 0.005
        rows[6]["text"] = "ordinary wrapped words"
        rows[8]["text"] = "A sentence ends here."
        audit = []
        image_only_headings(
            rows, {"chapters": [[1, 2, "Test"]], "upper_margin_cutoff": 0.05}, 2, audit
        )
        self.assertEqual(
            [r["text"] for r in rows if r.get("kind") == "heading"],
            ["A Section Heading"],
        )
        self.assertEqual(len(audit), 1)
        for r in rows:
            r.pop("kind", None)
        image_only_headings(
            rows, {"chapters": [[1, 2, "Test"]], "reference_pages": [2]}, 2, []
        )
        self.assertFalse(any(r.get("kind") == "heading" for r in rows))

    def test_scan_font_metrics_require_alignment_and_do_not_transfer_styles(self):
        rows = [row("A matching prose line", width=0.7)]
        source = [
            dict(rows[0], font_size=9, inline=[dict(start=0, end=21, tags=["em"])])
        ]
        audit = []
        with patch("typography.extract_page", return_value=source):
            attach_scan_font_metrics(rows, None, 1, audit)
        self.assertEqual(rows[0]["scan_font_size"], 9)
        self.assertNotIn("inline", rows[0])
        rows[0]["text"] = "Completely unrelated content"
        rows[0].pop("scan_font_size")
        with patch("typography.extract_page", return_value=source):
            attach_scan_font_metrics(rows, None, 1, audit)
        self.assertNotIn("scan_font_size", rows[0])

    def test_scan_artwork_ignores_visible_text_layer_overlays(self):
        image = Image.new("RGB", (100, 100), "white")
        data = io.BytesIO()
        image.save(data, format="PNG")
        doc = pymupdf.open()
        page = doc.new_page(width=100, height=100)
        page.insert_image(page.rect, stream=data.getvalue())
        page.insert_text((30, 60), "1", fontsize=40, render_mode=3)
        cropped, evidence = crop_artwork(page, pymupdf.Rect(20, 20, 80, 80))
        im = Image.open(io.BytesIO(cropped))
        self.assertEqual(im.getextrema(), ((255, 255), (255, 255), (255, 255)))
        self.assertEqual(evidence["method"], "embedded-page-scan")
        page.insert_text((30, 80), "Caption", fontsize=8)
        _, labeled = crop_artwork(page, pymupdf.Rect(20, 20, 90, 90))
        self.assertEqual(labeled["method"], "rendered-page")
        vector = pymupdf.open()
        p = vector.new_page(width=100, height=100)
        p.insert_text((30, 60), "1", fontsize=40)
        _, fallback = crop_artwork(p, pymupdf.Rect(20, 20, 80, 80))
        self.assertEqual(fallback["method"], "rendered-page")

    def test_reviewed_overlay_filter_preserves_captions_and_source(self):
        doc = pymupdf.open()
        page = doc.new_page(width=100, height=100)
        page.insert_text((30, 60), "1", fontsize=40, fontname="hebo")
        page.insert_text((25, 90), "Caption", fontsize=8)
        rules = [dict(font="Helvetica-Bold", min_size=35, pattern="[1I]")]
        data, evidence = crop_artwork(
            page, pymupdf.Rect(20, 15, 80, 65), excluded_overlays=rules
        )
        self.assertEqual(len(evidence["excluded_overlays"]), 1)
        self.assertEqual(
            Image.open(io.BytesIO(data)).getextrema(),
            ((255, 255), (255, 255), (255, 255)),
        )
        labeled, _ = crop_artwork(
            page, pymupdf.Rect(20, 15, 80, 95), excluded_overlays=rules
        )
        self.assertLess(Image.open(io.BytesIO(labeled)).getextrema()[0][0], 200)
        self.assertIn("1", page.get_text())
        self.assertIn("Caption", page.get_text())

    def test_pharmako_artifact_keeps_prose_between_separate_ideograms(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "output/pharmako-poeia/book-model.json"
        )
        if not path.exists():
            self.skipTest("Build Pharmako fixture first")
        model = json.loads(path.read_text())
        text = " ".join(b["text"] for c in model for b in c["blocks"])
        self.assertEqual(len(model), 54)
        self.assertEqual(
            sum(b["kind"] == "figure" for c in model for b in c["blocks"]), 110
        )
        self.assertIn(
            'ideograms for "male hemp," "female hemp," and "hemp fruits."', text
        )
        self.assertIn("why destroying wild habitat is parricide", text)
        self.assertEqual(model[9]["blocks"][-1]["kind"], "verse")

    def test_whole_line_style_survives_verified_ocr_spelling_difference(self):
        source = row("The italicized passage continues.", italic=True)
        target = row("The italicised passage continues.")
        audit = []
        with patch("typography.extract_page", return_value=[source]):
            attach_typography([target], None, 1, audit)
        self.assertEqual(target["text"], "The italicised passage continues.")
        self.assertEqual(
            target["inline"], [dict(start=0, end=len(target["text"]), tags=["em"])]
        )
        self.assertGreater(audit[0]["agreement"], 0.85)

    def test_partial_style_is_mapped_after_inserted_characters(self):
        source = row("An old botanical name follows.")
        source["inline"] = [dict(start=7, end=21, tags=["em"])]
        target = row("An older botanical name follows.")
        with patch("typography.extract_page", return_value=[source]):
            attach_typography([target], None, 1, [])
        fragments = [target["text"][s["start"] : s["end"]] for s in target["inline"]]
        self.assertEqual("".join(fragments), "botanical name")
        self.assertTrue(all(s["start"] >= 9 for s in target["inline"]))

    def test_unrelated_hidden_ocr_does_not_supply_typography(self):
        target = row("Fresh reliable text from the scan.")
        with patch(
            "typography.extract_page",
            return_value=[row("Unrelated mangled words.", italic=True)],
        ):
            attach_typography([target], None, 1, [])
        self.assertNotIn("inline", target)

    def test_large_side_glyph_does_not_dominate_body_font_size(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text(
            (50, 100), "A normal line with many ordinary letters", fontsize=11
        )
        page.insert_text((400, 100), "P", fontsize=30)
        rows = extract_page(page, {}, 1, [])
        body = next(r for r in rows if "ordinary" in r["text"])
        self.assertEqual(body["font_size"], 11)

    def test_small_cap_table_label_is_separate_from_its_value(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 100), "ELEMENT", fontsize=8)
        page.insert_text((110, 100), "Wood", fontsize=11)
        source = extract_page(page, {}, 1, [])
        self.assertEqual(source[0]["label_end"], len("ELEMENT"))
        target = dict(source[0])
        target.pop("label_end")
        target.pop("inline")
        with patch("typography.extract_page", return_value=source):
            attach_typography([target], None, 1, [])
        self.assertTrue(target["paragraph_start"])
        self.assertEqual(target["inline"][0]["tags"], ["strong"])

    def test_foreword_credit_and_incomplete_source_are_metadata(self):
        book = dict(
            title="A book",
            author="Author",
            language="en",
            date="1995",
            publisher="Publisher",
            source_note="The supplied scan lacks the references.",
            contributors=[
                dict(name="Foreword writer", role="aui", file_as="Writer, Foreword")
            ],
        )
        node = etree.fromstring(opf_metadata(book, {}, "urn:test"))
        ns = {
            "dc": "http://purl.org/dc/elements/1.1/",
            "opf": "http://www.idpf.org/2007/opf",
        }
        self.assertEqual(node.find("dc:creator", ns).text, "Author")
        self.assertEqual(node.find("dc:contributor", ns).text, "Foreword writer")
        self.assertEqual(
            node.xpath(
                'opf:meta[@refines="#contributor-1"][@property="role"]/text()',
                namespaces=ns,
            ),
            ["aui"],
        )
        self.assertIn("lacks the references", node.find("dc:description", ns).text)

    def test_poem_dash_is_not_treated_as_a_split_word(self):
        rows = [
            row(t, i, 0.25 + (i % 3) * 0.06)
            for i, t in enumerate(
                [
                    "Come you wandering plants",
                    "and all the forgotten ones",
                    "Come forth you motley troop -",
                    "not a gentleman among you —",
                    "Not one that will remain",
                    "a guest in the garden",
                    "Come all you restless ones",
                    "Be fruitful in the morning",
                ]
            )
        ]
        self.assertEqual(verse_evidence(rows, 0.8), {str(i) for i in range(8)})

    def test_wrapped_italic_prose_and_short_roman_prose_are_rejected(self):
        wrapped = [
            row(
                "A prose sentence continues here",
                i,
                0.75 if i < 5 else 0.25,
                italic=True,
            )
            for i in range(6)
        ]
        roman = [
            row("An ordinary short paragraph", i, 0.4 - i * 0.04) for i in range(4)
        ]
        self.assertFalse(verse_evidence(wrapped, 0.8))
        self.assertFalse(verse_evidence(roman, 0.8))

    def test_alternating_short_lines_and_stanza_gap_preserve_structure(self):
        rows = [
            row(
                "A short poetic line continues",
                i,
                0.25 + (i % 3) * 0.06,
                italic=True,
                x=0.2 + (i % 2) * 0.05,
            )
            for i in range(6)
        ]
        for r in rows[3:]:
            r["bbox"][1] += 0.08
            r["bbox"][3] += 0.08
        book = dict(chapters=[[1, 1, "Poem"]], chapter_body_starts={"1": 0})
        model = reconstruct({1: rows}, book, JoinPolicy(), [])
        self.assertEqual([b["kind"] for b in model[0]["blocks"]], ["verse", "verse"])
        block = model[0]["blocks"][0]
        html = block_html(block, book, set(), [], "chapter.xhtml")
        self.assertEqual(html.count("<br/>"), 2)
        self.assertIn("padding-left:1.5em", html)

    def test_artwork_mask_prevents_side_letter_joining_prose(self):
        lines = [
            row("P", width=0.02, x=0.05),
            row("The complete body line remains", width=0.6),
        ]
        book = dict(
            figures=[dict(page=1, name="ornament", rect=[0.04, 0.15, 0.09, 0.9])]
        )
        audit = []
        result = merge_rows(outside_figures(lines, book, 1, audit))
        self.assertEqual(result[0]["text"], "The complete body line remains")
        self.assertEqual(audit[0]["figure"], "ornament")

    def test_star_quote_requires_two_fresh_complete_line_witnesses(self):
        primary = [row("*Carefully prepared,” he said, “ready now.")]
        peers = [row('"Carefully prepared," he said, "ready now.')]
        audit = []
        result = correct_page(primary, [], peers, peers, audit, [], 1)
        self.assertEqual(
            result[0]["text"], "“Carefully prepared,” he said, “ready now."
        )
        self.assertEqual(audit[0]["kind"], "two-fresh-quote-mark")
        for secondary, tertiary in (
            ([], peers),
            (peers, []),
            (peers, [row('"Differently prepared," he said, "ready now.')]),
        ):
            self.assertEqual(
                correct_page(primary, peers, secondary, tertiary, [], [], 1)[0]["text"],
                primary[0]["text"],
            )
        marker = [row("*A genuine footnote without quotation.")]
        self.assertEqual(
            correct_page(marker, [], marker, marker, [], [], 1)[0]["text"],
            marker[0]["text"],
        )

    def test_garbled_tall_row_is_replaced_by_two_fresh_consensus_lines(self):
        primary = [dict(text="garbled row", bbox=[0.2, 0.2, 0.7, 0.24], confidence=1)]
        peers = [
            dict(
                text="The first reliable printed line",
                bbox=[0.2, 0.201, 0.7, 0.217],
                confidence=0.96,
            ),
            dict(
                text="And the second reliable printed line",
                bbox=[0.2, 0.222, 0.7, 0.238],
                confidence=0.96,
            ),
        ]
        audit = []
        result = correct_page(
            primary, [], peers, peers, audit, [], 1, recover_regions=True
        )
        self.assertEqual([r["text"] for r in result], [r["text"] for r in peers])
        self.assertEqual(audit[0]["kind"], "two-witness-region-recovery")
        original = correct_page(primary, [], peers, peers, [], [], 1)
        self.assertEqual(original[0]["text"], "garbled row")
        disagree = [dict(peers[0], text="A different reading"), peers[1]]
        unchanged = correct_page(
            primary, [], peers, disagree, [], [], 1, recover_regions=True
        )
        self.assertEqual(unchanged[0]["text"], "garbled row")
        # Existing matching lines are retained once while the missing line is
        # restored; a tiny extra apparatus token needs embedded corroboration.
        primary_with_neighbor = [peers[0], primary[0]]
        extra_marker = [peers[0], dict(peers[1], text=peers[1]["text"] + " s")]
        restored = correct_page(
            primary_with_neighbor,
            peers,
            peers,
            extra_marker,
            [],
            [],
            1,
            recover_regions=True,
        )
        self.assertEqual([r["text"] for r in restored], [r["text"] for r in peers])
        unsupported = correct_page(
            primary, [], peers, extra_marker, [], [], 1, recover_regions=True
        )
        self.assertEqual(unsupported[0]["text"], "garbled row")

    def test_illustrated_page_has_one_anchor_even_with_two_figures(self):
        block = dict(
            kind="figure",
            text="",
            page_breaks=[dict(page=3, offset=0)],
            image="a.jpg",
            alt="Drawing",
        )
        links = []
        seen = set()
        first = block_html(
            block, dict(page_labels={"3": "xiii"}), seen, links, "chapter.xhtml"
        )
        second = block_html(block, {}, seen, links, "chapter.xhtml")
        self.assertIn('aria-label="xiii"', first)
        self.assertNotIn('id="page-3"', second)
        self.assertEqual(links, [("chapter.xhtml#page-3", "xiii")])


class CorroboratedGlyphTests(unittest.TestCase):
    def test_confusable_digit_needs_opt_in_and_three_witnesses(self):
        primary = [row("We need 1o compare results.")]
        witness = [row("We need to compare results.")]
        self.assertEqual(
            correct_page(primary, witness, witness, witness, [], [], 1)[0]["text"],
            primary[0]["text"],
        )
        self.assertEqual(
            correct_page(
                primary, witness, witness, witness, [], [], 1, recover_glyphs=True
            )[0]["text"],
            witness[0]["text"],
        )
        self.assertEqual(
            correct_page(primary, [], witness, witness, [], [], 1, recover_glyphs=True)[
                0
            ]["text"],
            primary[0]["text"],
        )

    def test_numbers_and_formulae_are_preserved(self):
        for before, after in [
            ("1918", "1913"),
            ("5HT", "SHT"),
            ("10mg", "long"),
            ("D2", "Do"),
        ]:
            primary, witness = [row(before)], [row(after)]
            self.assertEqual(
                correct_page(
                    primary, witness, witness, witness, [], [], 1, recover_glyphs=True
                )[0]["text"],
                before,
            )

    def test_aligned_bold_subheading_is_optional(self):
        candidate = row("Methods")
        candidate["kind"] = "heading"
        candidate["inline"] = [dict(start=0, end=7, tags=["strong"])]
        with patch(
            "typography.extract_page",
            return_value=[
                candidate,
                row("Ordinary body prose establishes body size.", 1),
            ],
        ):
            original = [row("Methods")]
            attach_typography(original, None, 1, [])
            self.assertNotIn("kind", original[0])
            attach_typography(original, None, 1, [], recover_headings=True)
            self.assertEqual(original[0]["kind"], "heading")
            mismatch = [row("A different reading")]
            attach_typography(mismatch, None, 1, [], recover_headings=True)
            self.assertNotIn("kind", mismatch[0])


if __name__ == "__main__":
    unittest.main()
