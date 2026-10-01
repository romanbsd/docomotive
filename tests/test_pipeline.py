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
from common import apply_edits
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


if __name__ == "__main__":
    unittest.main()
