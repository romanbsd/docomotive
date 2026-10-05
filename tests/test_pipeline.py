import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build import page_blocks, merge_rows, join, validate_epub, opf_metadata
from metadata import isbn, isbn13, verify_edition, cover_info
from layout import infer
from jev_rank import validate, MODEL
from book_model import JoinPolicy, reconstruct, coverage, plain_text
from render_text import block_html
from common import apply_edits, load_profile
from proofread import diagnostics


def row(text, y, x=0.2):
    return {"text": text, "bbox": [x, y, 0.8, y + 0.02], "column": 0}


class PipelineTests(unittest.TestCase):
    def book(self):
        return {
            "note_starts": {},
            "verse_regions": {},
            "chapter_body_starts": {"25": 0.24},
        }

    def test_high_body_text_is_preserved(self):
        blocks, _ = page_blocks(
            26, [row("up after using a drug.", 0.07)], self.book(), [], False
        )
        self.assertEqual(blocks[0]["fragments"][0]["text"], "up after using a drug.")

    def test_short_title_page_keeps_first_body_line(self):
        blocks, _ = page_blocks(
            25,
            [
                row("Why People Take Drugs", 0.154),
                row("THE USE OF DRUGS TO ALTER CONSCIOUSNESS is nothing new.", 0.257),
            ],
            self.book(),
            [],
            True,
        )
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0]["fragments"][0]["text"].startswith("THE USE"))

    def test_row_fragments_keep_horizontal_order(self):
        r = merge_rows([row("second", 0.2, 0.4), row("first", 0.2, 0.1)])
        self.assertEqual(r[0]["text"], "first second")

    def test_hanging_index_entries_remain_separate(self):
        book = self.book()
        book["reference_pages"] = [229]
        blocks, _ = page_blocks(
            229,
            [
                row("Addiction, see Dependence; Her-", 0.31, 0.085),
                row("oin; Withdrawal", 0.334, 0.112),
                row("Age regression, 33", 0.358, 0.085),
            ],
            book,
            [],
            False,
        )
        self.assertEqual(len(blocks), 2)
        self.assertEqual(
            blocks[0]["fragments"][0]["text"],
            "Addiction, see Dependence; Heroin; Withdrawal",
        )

    def test_hyphenated_compound_and_line_split(self):
        self.assertEqual(join("a pick-me-", "up"), "a pick-me-up")
        self.assertEqual(join("the experi-", "ence"), "the experience")

    def test_rare_wrap_keeps_known_compound(self):
        policy = JoinPolicy(protected=["re-form"], rare_wraps=True)
        self.assertEqual(join("re-", "form", policy=policy), "re-form")
        self.assertEqual(join("asso-", "ciators", policy=policy), "associators")

    def test_verified_wrap_overrides_spurious_paragraph_flag(self):
        book = dict(self.book(), repair_word_wraps=True, infer_verse=False)
        lines = [
            row("mental representations (asso-", 0.3),
            row("ciators) whereas another experiences colours", 0.322),
        ]
        lines[1]["paragraph_start"] = True
        policy = JoinPolicy(rare_wraps=True)
        blocks, _ = page_blocks(26, lines, book, [], False, policy)
        self.assertEqual(len(blocks), 1)
        self.assertIn("associators)", blocks[0]["fragments"][0]["text"])

    def test_wrap_does_not_override_indented_new_paragraph(self):
        book = dict(self.book(), repair_word_wraps=True, infer_verse=False)
        lines = [
            row("mental representations (asso-", 0.3),
            row("ciators) a separately indented paragraph", 0.322, 0.25),
        ]
        lines[1]["paragraph_start"] = True
        blocks, _ = page_blocks(
            26, lines, book, [], False, JoinPolicy(protected=["associators"])
        )
        self.assertEqual(len(blocks), 2)

    def test_recurring_margin_does_not_consume_unique_body(self):
        pages = {
            str(n): [
                row("THE NATURAL MIND", 0.06),
                row("a unique body line " + str(n), 0.095),
                row(str(n - 8), 0.89),
            ]
            for n in range(9, 21)
        }
        model = infer(pages)
        self.assertEqual(len(model["headers"]), 1)
        self.assertEqual(len(model["footers"]), 12)
        self.assertEqual(model["printed_page_offset"], -8)

    def test_jev_invalid_options_are_rejected(self):
        with self.assertRaises(ValueError):
            validate(
                {
                    "model": MODEL,
                    "answers": {
                        "q": {"type": "choice", "choice": "invented", "confidence": 0.9}
                    },
                },
                {"q": {"criteria": {"keep": "preserve"}}},
            )

    def test_profile_rejects_unknown_keys(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.json"
            path.write_text(json.dumps({"slug": "x", "note_start": {}}))
            with self.assertRaisesRegex(ValueError, "note_start"):
                load_profile(path)
            path.write_text(
                json.dumps(
                    {
                        "slug": "x",
                        "comments": {"any": 1},
                        "title": "Book",
                        "author": "Author",
                        "language": "en",
                        "source_sha256": "a" * 64,
                        "chapters": [[1, 1, "Chapter"]],
                    }
                )
            )
            self.assertEqual(load_profile(path)["slug"], "x")

    def test_isbn_check_digits(self):
        self.assertEqual(isbn("0-395-13936-8"), "0395139368")
        self.assertEqual(isbn13("0395139368"), "9780395139363")
        with self.assertRaises(ValueError):
            isbn("0395139369")

    def test_wrong_isbn_edition_is_rejected(self):
        book = {
            "isbn": "0395139368",
            "title": "The Natural Mind",
            "author": "Andrew Weil",
            "publisher": "Houghton Mifflin",
        }
        with self.assertRaises(ValueError):
            verify_edition(
                {
                    "isbn_10": ["0395166128"],
                    "title": book["title"],
                    "publishers": [book["publisher"]],
                },
                book,
                ["Andrew Weil"],
            )

    def test_exact_non_isbn_edition_checks_identity_and_date(self):
        book = {
            "openlibrary_edition": "OL123M",
            "title": "Example",
            "author": "Manuel Córdova-Ríos",
            "publisher": "Publisher",
            "date": "1971",
        }
        record = {
            "key": "/books/OL123M",
            "title": "Example",
            "publishers": ["Publisher"],
            "publish_date": "1971",
        }
        verify_edition(record, book, ["Manuel Co\u0301rdova-Ri\u0301os"])
        for field, value in [("key", "/books/OL456M"), ("publish_date", "1975")]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_edition(dict(record, **{field: value}), book, [book["author"]])

    def test_metadata_is_escaped(self):
        from lxml import etree

        book = {
            "title": "A & B",
            "subtitle": "<Test>",
            "author": "One & Two",
            "language": "en",
            "date": "1972",
            "publisher": "P & Q",
        }
        root = etree.fromstring(opf_metadata(book, {}, "urn:test").encode())
        self.assertEqual(
            root.xpath('//*[local-name()="title"]/text()'), ["A & B: <Test>"]
        )

    def test_copyright_scan_is_absent(self):
        import zipfile

        path = Path(__file__).resolve().parents[1] / "output/the-natural-mind.epub"
        if not path.exists():
            self.skipTest("Build the benchmark EPUB first")
        with zipfile.ZipFile(path) as archive:
            self.assertFalse(
                any(
                    "copyright-scan" in name or "copyright-facsimile" in name
                    for name in archive.namelist()
                )
            )

    def test_generated_epub_links(self):
        path = Path(__file__).resolve().parents[1] / "output/the-natural-mind.epub"
        if not path.exists():
            self.skipTest("Build the benchmark EPUB first")
        self.assertEqual(validate_epub(path)["xml_and_internal_links"], "passed")

    def test_continued_notes_share_one_target(self):
        path = Path(__file__).resolve().parents[1] / "output/preview/chapter-03.xhtml"
        if not path.exists():
            self.skipTest("Build the benchmark EPUB first")
        text = path.read_text()
        self.assertIn('id="note-67"', text)
        self.assertNotIn('id="note-68"', text)
        self.assertIn('href="#note-67"', text)

    def synthetic_model(self):
        book = self.book()
        book.update(
            chapters=[[9, 10, "Example"]],
            source_sha256="fixture",
            reference_pages=[],
            protected_words=[],
        )
        pages = {
            9: [row("the allo-", 0.30)],
            10: [row("THE NATURAL MIND", 0.06), row("pathic method.", 0.12)],
        }
        model = reconstruct(pages, book, JoinPolicy(["allopathic"]), [])
        return book, pages, model

    def test_cross_page_text_is_canonical(self):
        book, pages, model = self.synthetic_model()
        self.assertEqual(model[0]["blocks"][0]["text"], "the allopathic method.")
        self.assertIn("the allopathic method.", plain_text(model))
        self.assertEqual(
            coverage(pages, book, model)["counts"], {"body": 2, "excluded": 1}
        )

    def test_note_link_cannot_split_a_joined_word(self):
        from lxml import etree

        book, _, model = self.synthetic_model()
        book["_note_targets"] = {10: 10}
        markup = block_html(model[0]["blocks"][0], book, set(), [], "test.xhtml")
        root = etree.fromstring(
            (
                '<body xmlns:epub="http://www.idpf.org/2007/ops">' + markup + "</body>"
            ).encode()
        )
        visible = "".join(root.itertext())
        self.assertEqual(visible, "the allopathic method. [note]")

    def test_coverage_rejects_missing_source_mapping(self):
        book, pages, model = self.synthetic_model()
        model[0]["blocks"][0]["sources"].pop()
        with self.assertRaises(ValueError):
            coverage(pages, book, model)

    def test_coverage_rejects_duplicate_source_mapping(self):
        book, pages, model = self.synthetic_model()
        model[0]["blocks"][0]["sources"].append(model[0]["blocks"][0]["sources"][0])
        with self.assertRaises(ValueError):
            coverage(pages, book, model)

    def test_lexical_hyphen_wins_over_joined_variant(self):
        self.assertEqual(
            join("a long-", "term plan", policy=JoinPolicy(["long-term", "longterm"])),
            "a long-term plan",
        )

    def test_rare_word_join_uses_book_lexicon(self):
        self.assertEqual(
            join("the ethnobota-", "nist", policy=JoinPolicy(["ethnobotanist"])),
            "the ethnobotanist",
        )

    def test_exact_row_overlay_preserves_matching_prose(self):
        items = [{"page": 1, "text": "-"}, {"page": 1, "text": "a - b"}]
        apply_edits(
            items,
            [dict(page=1, before="-", after="", exact_row=True)],
            [],
            "test",
        )
        self.assertEqual([item["text"] for item in items], ["", "a - b"])

    def test_skewed_hanging_entries_keep_numeric_continuations(self):
        book = self.book() | {"hanging_pages": [1], "recover_hanging_margins": True}
        rows = []
        for i in range(12):
            y = 0.1 + i * 0.06
            rows.append(row(f"Entry {i} 10, 12,", y, 0.1 + 0.07 * y))
            rows.append(row("14, 16", y + 0.024, 0.14 + 0.07 * (y + 0.024)))
        blocks, _ = page_blocks(1, rows, book, [], False)
        self.assertEqual(len(blocks), 12)
        self.assertTrue(all(len(b["lines"]) == 2 for b in blocks))
        self.assertTrue(all(b["kind"] == "text" for b in blocks))

    def test_sparse_hanging_layout_keeps_fallback(self):
        rows = [row("Entry 10,", 0.2), row("12, 14", 0.224, 0.24)]
        book = self.book() | {"hanging_pages": [1]}
        old, _ = page_blocks(1, rows, book, [], False)
        new, _ = page_blocks(
            1, rows, book | {"recover_hanging_margins": True}, [], False
        )
        self.assertEqual(old, new)

    def test_reviewed_verse_range_preserves_preceding_prose_tail(self):
        rows = [row("The prose ends here.", 0.2)]
        for i, text in enumerate(
            ["A poem begins", "With a shorter line", "And closes here"]
        ):
            rows.append(row(text, 0.24 + i * 0.025))
        book = self.book() | {
            "infer_verse": False,
            "verse_regions": {"1": [[0.24, 0.32]]},
        }
        blocks, _ = page_blocks(1, rows, book, [], False)
        self.assertEqual([b["kind"] for b in blocks], ["text", "verse"])
        self.assertEqual(blocks[0]["fragments"][0]["text"], "The prose ends here.")

    def test_overlay_failure_does_not_mutate_target(self):
        items = [{"page": 1, "text": "epend and dependence"}]
        with self.assertRaises(ValueError):
            apply_edits(
                items,
                [
                    {
                        "page": 1,
                        "before": "epend",
                        "after": "epená",
                        "count": 2,
                        "whole_word": True,
                    }
                ],
                [],
                "test",
            )
        self.assertEqual(items[0]["text"], "epend and dependence")

    def test_editorial_offsets_keep_evidence_attached(self):
        book = self.book()
        book.update(chapters=[[9, 9, "Example"]], source_sha256="fixture")
        model = reconstruct(
            {9: [row("An adminster works.", 0.3)]},
            book,
            JoinPolicy(),
            [],
            [{"page": 9, "printed": "adminster", "proposal": "administer"}],
        )
        block = model[0]["blocks"][0]
        self.assertEqual(block["text"], "An administer works.")
        self.assertEqual(block["sources"][0]["end"], len(block["text"]))

    def test_diagnostics_retain_source_geometry(self):
        book = self.book()
        book.update(chapters=[[9, 9, "Example"]], source_sha256="fixture")
        model = reconstruct(
            {9: [row("func-.", 0.3), row("tioning matters.", 0.324)]},
            book,
            JoinPolicy(),
            [],
        )
        issues = diagnostics(model, book)
        self.assertEqual(issues[0]["kind"], "suspicious-line-punctuation")
        self.assertEqual(len(issues[0]["occurrences"][0]["sources"]), 2)


class CorpusRenderingTests(unittest.TestCase):
    def test_rendered_text_matches_every_canonical_block(self):
        from lxml import etree
        from render_text import block_html, notes_html
        import json

        root = Path(__file__).resolve().parents[1]
        for folder in [root / "output", root / "output/reading-edition"]:
            path = folder / "book-model.json"
            if not path.exists():
                self.skipTest("Build benchmark editions first")
            model = json.loads(path.read_text())
            book = json.loads((root / "config/book.json").read_text())

            def normalized(text):
                return " ".join(text.split())

            for chapter in model:
                for block in chapter["blocks"]:
                    markup = block_html(block, book, set(), [], "chapter.xhtml")
                    node = etree.fromstring(
                        (
                            '<body xmlns:epub="http://www.idpf.org/2007/ops">'
                            + markup
                            + "</body>"
                        ).encode()
                    )
                    for br in node.findall(".//br"):
                        br.tail = " " + (br.tail or "")
                    # Backlink labels are renderer navigation, not source text.
                    for backlinks in node.findall(".//p[@class='note-backlinks']"):
                        backlinks.getparent().remove(backlinks)
                    self.assertEqual(
                        normalized("".join(node.itertext())), normalized(block["text"])
                    )
                markup = notes_html(chapter["notes"], book)
                if markup:
                    node = etree.fromstring(
                        (
                            '<body xmlns:epub="http://www.idpf.org/2007/ops">'
                            + markup
                            + "</body>"
                        ).encode()
                    )
                    for aside, note in zip(node.findall(".//aside"), chapter["notes"]):
                        self.assertEqual(
                            "".join(aside.find("p").itertext()), note["text"]
                        )


class WholeBookStatisticsTests(unittest.TestCase):
    def fixture(self):
        def chapter(number, page, text):
            return {
                "chapter": number,
                "source_pages": [page],
                "notes": [],
                "blocks": [
                    {
                        "text": text,
                        "sources": [{"page": page, "start": 0, "end": len(text)}],
                    }
                ],
            }

        return [
            chapter(1, 9, "Zorblax zorblax noisy noisey noisy noisy"),
            chapter(2, 10, "zorblax singleton"),
            chapter(3, 11, "zorblax"),
            chapter(4, 229, "zorblax singleton"),
        ], {"reference_pages": [229]}

    def test_statistics_run_across_chapters_and_separate_reference_counts(self):
        from vocabulary import analyze

        model, book = self.fixture()
        stats = analyze(model, book)
        self.assertEqual(stats["documents"], 3)
        term = stats["terms"]["zorblax"]
        self.assertEqual(
            (
                term["count"],
                term["chapter_df"],
                term["page_df"],
                term["reference_count"],
            ),
            (4, 3, 3, 1),
        )
        self.assertEqual(term["forms"], {"Zorblax": 1, "zorblax": 3})

    def test_high_idf_singleton_does_not_become_protected(self):
        from vocabulary import analyze, review_signals

        model, book = self.fixture()
        stats = analyze(model, book)
        self.assertGreater(
            stats["terms"]["singleton"]["chapter_idf"],
            stats["terms"]["zorblax"]["chapter_idf"],
        )
        self.assertEqual(
            review_signals("singleton", stats)["recommendation"], "inspect-spelling"
        )

    def test_dispersed_term_is_only_a_proposal(self):
        from vocabulary import analyze, review_signals

        model, book = self.fixture()
        signal = review_signals("zorblax", analyze(model, book))
        self.assertEqual(signal["recommendation"], "consider-protecting")
        self.assertFalse(signal["automatic_protection"])
        self.assertEqual(
            review_signals("noisy", analyze(model, book))["recommendation"],
            "inspect-spelling",
        )

    def test_book_spelling_competitor_raises_review_priority(self):
        from vocabulary import analyze, review_signals, edit_distance_one

        model, book = self.fixture()
        signal = review_signals("noisey", analyze(model, book))
        self.assertEqual(signal["in_book_variants"], [{"term": "noisy", "count": 3}])
        self.assertEqual(signal["review_priority"], 0)
        self.assertTrue(edit_distance_one("anxoius", "anxious"))
        self.assertFalse(edit_distance_one("drug", "mind"))


class NativePDFTests(unittest.TestCase):
    def test_native_bold_heading_can_contain_unstyled_positioned_spaces(self):
        from native_pdf import extract_page
        from types import SimpleNamespace

        def line(text, box, size):
            return dict(
                bbox=box,
                spans=[dict(text=text, bbox=box, font="Bold", size=size, flags=16)],
            )

        page = SimpleNamespace(
            rect=SimpleNamespace(width=100, height=100),
            get_text=lambda mode: dict(
                blocks=[
                    dict(
                        lines=[
                            line("A heading", [10, 20, 40, 25], 14),
                            line("LO 1.1", [60, 20, 80, 25], 9),
                        ]
                    )
                ]
            ),
        )
        row = extract_page(page, {}, 1, [])[0]
        self.assertEqual(row["text"], "A heading LO 1.1")
        self.assertEqual(row["kind"], "heading")

    def test_index_child_indent_survives_wrapped_entry_reconstruction(self):
        rows = [
            dict(text="Action", bbox=[0.1, 0.2, 0.8, 0.215], column=0),
            dict(text="helping clients to", bbox=[0.126, 0.23, 0.8, 0.245], column=0),
            dict(text="act, 21–23", bbox=[0.152, 0.25, 0.8, 0.265], column=0),
            dict(text="Alliance, 24", bbox=[0.1, 0.28, 0.8, 0.295], column=0),
        ]
        book = dict(
            text_source="native",
            chapters=[[1, 1, "Index"]],
            index_pages=[1],
            hanging_pages=[1],
            native_index_indents=True,
            chapter_body_starts={"1": 0},
            paragraph_margins={"1": 0.126},
        )
        blocks = reconstruct({1: rows}, book, JoinPolicy(), [])[0]["blocks"]
        self.assertEqual(
            [b["text"] for b in blocks],
            ["Action", "helping clients to act, 21–23", "Alliance, 24"],
        )
        self.assertEqual([b["index_indent_em"] for b in blocks], [0, 1.04, 0])
        self.assertIn(
            "margin-left:2.29em",
            block_html(blocks[1], dict(book, _hanging=True), set(), [], "index.xhtml"),
        )

    def test_wrapped_native_heading_merge_is_opt_in_and_keeps_spaced_headings(self):
        rows = [
            dict(
                text="A long heading",
                bbox=[0.1, 0.2, 0.8, 0.22],
                column=0,
                kind="heading",
                native=True,
                font_size=14,
            ),
            dict(
                text="with a second line",
                bbox=[0.1, 0.218, 0.8, 0.238],
                column=0,
                kind="heading",
                native=True,
                font_size=14,
            ),
            dict(
                text="A separate heading",
                bbox=[0.1, 0.27, 0.8, 0.29],
                column=0,
                kind="heading",
                native=True,
                font_size=14,
            ),
        ]
        book = dict(
            text_source="native",
            chapters=[[1, 1, "Test"]],
            chapter_body_starts={"1": 0},
        )
        original = reconstruct({1: rows}, book, JoinPolicy(), [])
        merged = reconstruct(
            {1: rows}, dict(book, native_heading_merge=True), JoinPolicy(), []
        )
        self.assertEqual(len(original[0]["blocks"]), 3)
        self.assertEqual(
            [b["text"] for b in merged[0]["blocks"]],
            ["A long heading with a second line", "A separate heading"],
        )

    def test_native_lists_keep_wrapped_items_separate_from_body(self):
        from native_pdf import extract_page
        from types import SimpleNamespace

        def span(text, x0, x1, y):
            return dict(
                text=text, bbox=[x0, y, x1, y + 2], font="Book", size=10, flags=0
            )

        def line(spans):
            return dict(
                bbox=[
                    spans[0]["bbox"][0],
                    spans[0]["bbox"][1],
                    spans[-1]["bbox"][2],
                    spans[-1]["bbox"][3],
                ],
                spans=spans,
            )

        page = SimpleNamespace(
            rect=SimpleNamespace(width=100, height=100),
            get_text=lambda mode: dict(
                blocks=[
                    dict(
                        lines=[
                            line(
                                [
                                    span("1. ", 10, 14, 20),
                                    span("First item with", 15, 80, 20),
                                ]
                            ),
                            line([span("a wrapped ending.", 15, 80, 23)]),
                            line(
                                [
                                    span("2. ", 10, 14, 26),
                                    span("Second item.", 15, 80, 26),
                                ]
                            ),
                            line([span("Ordinary prose follows.", 10, 80, 29)]),
                        ]
                    )
                ]
            ),
        )
        book = dict(
            native_list_layout=True,
            text_source="native",
            chapters=[[1, 1, "Test"]],
            chapter_body_starts={"1": 0},
            upper_margin_cutoff=0,
        )
        rows = extract_page(page, book, 1, [])
        model = reconstruct({1: rows}, book, JoinPolicy(), [])
        blocks = model[0]["blocks"]
        self.assertEqual(
            [b["kind"] for b in blocks], ["list-item", "list-item", "text"]
        )
        self.assertEqual(
            [b["text"] for b in blocks],
            [
                "1. First item with a wrapped ending.",
                "2. Second item.",
                "Ordinary prose follows.",
            ],
        )
        self.assertIn(
            'class="source-list-item"',
            block_html(blocks[0], book, set(), [], "test.xhtml"),
        )
        self.assertNotIn("list_text_left", extract_page(page, {}, 1, [])[0])

    def test_coauthor_metadata_uses_creator_with_author_role(self):
        from build import opf_metadata
        from lxml import etree

        book = dict(
            title="Test",
            author="First Author",
            language="en",
            date="2019",
            publisher="Test",
            contributors=[
                dict(name="Second Author", role="aut"),
                dict(name="Editor", role="edt"),
            ],
        )
        metadata = etree.fromstring(opf_metadata(book, {}, "test-id").encode())
        ns = {"dc": "http://purl.org/dc/elements/1.1/"}
        self.assertEqual(
            metadata.xpath("dc:creator/text()", namespaces=ns),
            ["First Author", "Second Author"],
        )
        self.assertEqual(
            metadata.xpath("dc:contributor/text()", namespaces=ns), ["Editor"]
        )

    def test_native_figure_labels_cannot_merge_into_adjacent_prose(self):
        from native_pdf import extract_page
        from types import SimpleNamespace

        def line(text, box):
            return dict(
                bbox=box, spans=[dict(text=text, font="Book", size=10, flags=0)]
            )

        page = SimpleNamespace(
            rect=SimpleNamespace(width=100, height=100),
            get_text=lambda mode: dict(
                blocks=[
                    dict(
                        lines=[
                            line("Retained prose", [10, 20, 40, 25]),
                            line("Diagram label", [60, 20, 90, 25]),
                        ]
                    )
                ]
            ),
        )
        book = dict(figures=[dict(page=1, name="diagram", rect=[0.5, 0.1, 1, 0.4])])
        audit = []
        rows = extract_page(page, book, 1, audit)
        self.assertEqual([r["text"] for r in rows], ["Retained prose"])
        self.assertEqual(rows[0]["bbox"], [0.1, 0.2, 0.4, 0.25])
        self.assertEqual(audit[0]["kind"], "excluded-figure-ocr")

    def test_native_corpus_rendering_preserves_all_canonical_text(self):
        from lxml import etree
        from render_text import notes_html
        import json

        root = Path(__file__).resolve().parents[1]
        path = root / "output/shamanic-trance/book-model.json"
        if not path.exists():
            self.skipTest("Build the native PDF benchmark first")
        model = json.loads(path.read_text())
        book = json.loads((root / "config/shamanic-trance/book.json").read_text())
        for chapter in model:
            for block in chapter["blocks"]:
                markup = block_html(block, book, set(), [], "chapter.xhtml")
                node = etree.fromstring(
                    (
                        '<body xmlns:epub="http://www.idpf.org/2007/ops">'
                        + markup
                        + "</body>"
                    ).encode()
                )
                for backlink in node.xpath('.//p[@class="note-backlinks"]'):
                    backlink.getparent().remove(backlink)
                for br in node.findall(".//br"):
                    br.tail = " " + (br.tail or "")
                self.assertEqual(
                    " ".join("".join(node.itertext()).split()),
                    " ".join(block["text"].split()),
                )
            markup = notes_html(chapter["notes"], book)
            if markup:
                node = etree.fromstring(
                    (
                        '<body xmlns:epub="http://www.idpf.org/2007/ops">'
                        + markup
                        + "</body>"
                    ).encode()
                )
                for aside, note in zip(node.findall(".//aside"), chapter["notes"]):
                    self.assertEqual("".join(aside.find("p").itertext()), note["text"])

    def test_encoding_normalization_keeps_explicit_end_discretionary_hyphen(self):
        from native_pdf import clean_text

        self.assertEqual(clean_text("\x07ﬁeld\t"), "field ")
        self.assertEqual(clean_text("trans\u00adformation"), "transformation")
        self.assertEqual(join("trans\u00ad", "formation"), "transformation")
        self.assertEqual(join("let-", "ter18"), "letter18")

    def test_hunspell_compound_acceptance_cannot_keep_an_artificial_wrap(self):
        class PermissiveDictionary:
            def lookup(self, word):
                return True

        self.assertEqual(
            join(
                "friend-", "ship", policy=JoinPolicy(dictionary=PermissiveDictionary())
            ),
            "friendship",
        )
        self.assertEqual(
            join(
                "mind-",
                "set",
                policy=JoinPolicy(["mind-set"], dictionary=PermissiveDictionary()),
            ),
            "mind-set",
        )

    def test_native_spans_keep_italics_and_superscript_offsets(self):
        from native_pdf import extract_page
        from types import SimpleNamespace

        page = SimpleNamespace(
            rect=SimpleNamespace(width=100, height=100),
            get_text=lambda mode: {
                "blocks": [
                    {
                        "lines": [
                            {
                                "bbox": [10, 20, 80, 30],
                                "spans": [
                                    {
                                        "text": "trance",
                                        "flags": 6,
                                        "font": "BookItalic",
                                        "size": 9.5,
                                    },
                                    {
                                        "text": "1",
                                        "flags": 5,
                                        "font": "Book",
                                        "size": 5.5,
                                    },
                                ],
                            }
                        ]
                    }
                ]
            },
        )
        rows = extract_page(page, {}, 1, [])
        self.assertEqual(rows[0]["text"], "trance1")
        self.assertEqual(
            rows[0]["inline"],
            [
                {"start": 0, "end": 6, "tags": ["em"]},
                {"start": 6, "end": 7, "tags": ["sup"]},
            ],
        )

    def apparatus_fixture(self):
        body = {
            "text": "a1 b2",
            "inline": [
                {"start": 1, "end": 2, "tags": ["sup"]},
                {"start": 4, "end": 5, "tags": ["sup"]},
            ],
            "kind": "text",
        }

        def note(number):
            return {
                "text": f"{number}. Text",
                "kind": "text",
                "sources": [{"page": 160, "bbox": [0.146, 0.3, 0.85, 0.32]}],
            }

        model = [
            {"chapter": 1, "blocks": [body]},
            {
                "chapter": 2,
                "blocks": [
                    {"kind": "heading", "text": "Chapter One"},
                    note(1),
                    note(2),
                ],
            },
        ]
        book = {
            "endnote_chapter": 2,
            "endnote_sections": [
                {"heading": "Chapter One", "source_chapter": 1, "expected_notes": 2}
            ],
        }
        return model, book

    def test_endnotes_require_complete_sequences_and_bidirectional_links(self):
        from apparatus import link_endnotes

        model, book = self.apparatus_fixture()
        result = link_endnotes(model, book)
        self.assertEqual((result["endnotes"], result["superscript_links"]), (2, 2))
        self.assertEqual(
            model[0]["blocks"][0]["inline"][0]["href"], "chapter-02.xhtml#endnote-1-1"
        )
        self.assertEqual(
            model[1]["blocks"][1]["backlinks"], ["chapter-01.xhtml#noteref-1-1-1"]
        )
        model, book = self.apparatus_fixture()
        model[1]["blocks"][2]["text"] = "3. Text"
        with self.assertRaises(ValueError):
            link_endnotes(model, book)

    def test_orphaned_endnote_reference_fails(self):
        from apparatus import link_endnotes

        model, book = self.apparatus_fixture()
        model[0]["blocks"][0]["text"] = "a1 b3"
        with self.assertRaises(ValueError):
            link_endnotes(model, book)

    def test_chapter_grouped_bibliography_links_repeated_numbers_and_fallbacks(self):
        from apparatus import link_endnotes
        from render_text import block_html, chapter_notes_navigation

        def body(text):
            return dict(
                kind="text",
                text=text,
                inline=[dict(start=len(text) - 1, end=len(text), tags=["sup"])],
                page_breaks=[],
            )

        def reference(number):
            return dict(
                kind="text",
                text=f"{number}. Bibliographic entry.",
                sources=[dict(page=80, bbox=[0.1, 0.3, 0.8, 0.4])],
                page_breaks=[],
            )

        model = [
            dict(chapter=1, blocks=[body("First claim.1"), body("Repeated claim.1")]),
            dict(chapter=2, blocks=[body("Different chapter.1")]),
            dict(
                chapter=3,
                blocks=[
                    dict(kind="heading", text="CHAPTER 1"),
                    reference(1),
                    reference(2),
                    dict(kind="heading", text="CHAPTER 2"),
                    reference(1),
                ],
            ),
        ]
        book = dict(
            endnote_chapter=3,
            endnote_reference_mode="chapter",
            endnote_sections=[
                dict(heading="CHAPTER 1", source_chapter=1, expected_notes=2),
                dict(heading="CHAPTER 2", source_chapter=2, expected_notes=1),
            ],
        )
        result = link_endnotes(model, book)
        self.assertEqual(result["superscript_links"], 3)
        self.assertEqual(result["chapter_links"], 1)
        self.assertEqual(result["unlinked_endnotes"], [])
        # Only the chapter with an actual missing marker needs end navigation.
        self.assertIn(
            "chapter-03.xhtml#endnote-1-1",
            chapter_notes_navigation(model[0], model[2]["blocks"], book),
        )
        self.assertEqual(
            chapter_notes_navigation(model[1], model[2]["blocks"], book), ""
        )
        self.assertEqual(
            chapter_notes_navigation(model[2], model[2]["blocks"], book), ""
        )
        self.assertEqual(
            chapter_notes_navigation(
                model[0],
                model[2]["blocks"],
                dict(book, endnote_reference_mode="inline"),
            ),
            "",
        )
        first, repeated = model[0]["blocks"]
        second = model[1]["blocks"][0]
        self.assertEqual(first["inline"][0]["href"], "chapter-03.xhtml#endnote-1-1")
        self.assertEqual(second["inline"][0]["href"], "chapter-03.xhtml#endnote-2-1")
        self.assertNotEqual(
            first["inline"][0]["anchor"], repeated["inline"][0]["anchor"]
        )
        self.assertEqual(
            model[2]["blocks"][1]["backlinks"],
            ["chapter-01.xhtml#noteref-1-1-1", "chapter-01.xhtml#noteref-1-1-2"],
        )
        # An unrecovered body marker gets chapter navigation, never an invented digit.
        self.assertEqual(
            model[2]["blocks"][2]["backlinks"], ["chapter-01.xhtml#chapter-start"]
        )
        rendered = block_html(first, {}, set(), [], "chapter-01.xhtml")
        self.assertIn('href="chapter-03.xhtml#endnote-1-1"><sup>1</sup></a>', rendered)
        self.assertIn('epub:type="noteref"', rendered)
        target = block_html(model[2]["blocks"][1], {}, set(), [], "chapter-03.xhtml")
        self.assertIn('id="endnote-1-1"', target)
        self.assertIn('href="chapter-01.xhtml#noteref-1-1-1"', target)

    def test_endnote_terms_contribute_to_the_source_chapter_statistics(self):
        from vocabulary import analyze

        model = [
            {
                "chapter": 1,
                "source_pages": [12],
                "blocks": [
                    {"text": "zorblax", "sources": [{"page": 12, "start": 0, "end": 7}]}
                ],
                "notes": [],
            },
            {
                "chapter": 2,
                "source_pages": [160],
                "blocks": [
                    {
                        "text": "zorblax",
                        "source_chapter": 1,
                        "sources": [{"page": 160, "start": 0, "end": 7}],
                    }
                ],
                "notes": [],
            },
        ]
        stats = analyze(model, {"reference_pages": [160]})
        term = stats["terms"]["zorblax"]
        self.assertEqual(
            (
                term["count"],
                term["chapter_df"],
                term["page_df"],
                term["reference_count"],
            ),
            (2, 1, 2, 0),
        )

    def test_transliteration_modifier_letters_are_part_of_the_word(self):
        from vocabulary import TOKEN

        self.assertEqual(
            TOKEN.findall("Baʿal Shem Peʿamim"), ["Baʿal", "Shem", "Peʿamim"]
        )

    def test_index_reference_routes_to_endnote_and_keeps_abbreviated_range(self):
        from lxml import etree

        block = {"text": "169n97; 163–64nn50–51; ix", "kind": "text", "page_breaks": []}
        book = {
            "page_labels": {"10": "ix"},
            "_index_note_refs": {
                "169:97": "notes.xhtml#n97",
                "163:50": "notes.xhtml#n50",
            },
        }
        markup = block_html(
            block,
            book,
            set(),
            [],
            "index.xhtml",
            {
                "169": "notes.xhtml#p169",
                "163": "notes.xhtml#p163",
                "ix": "preface.xhtml#page-10",
            },
        )
        node = etree.fromstring(markup.encode())
        self.assertEqual("".join(node.itertext()), block["text"])
        self.assertEqual(
            node.xpath(".//a/@href"),
            ["notes.xhtml#n97", "notes.xhtml#n50", "preface.xhtml#page-10"],
        )


if __name__ == "__main__":
    unittest.main()
