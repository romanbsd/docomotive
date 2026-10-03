import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lexical_repair import ContextRanker, CropRecognizer, repair, locate_word
import pymupdf


class TestDictionary:
    def lookup(self, word):
        return word in {"amrita", "beliefs", "after", "each", "earlier", "amnesia"}


def entry(text, chapter=1, page=1):
    row = dict(text=text, page=page, row_id=str(page), bbox=[0.1, 0.2, 0.9, 0.22])
    block = dict(text=text, kind="text", sources=[dict(row, start=0, end=len(text))])
    return dict(chapter=chapter, source_pages=[page], blocks=[block], notes=[]), row


class LexicalRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        training, _ = entry(
            "amnesia on the part of the receivers. extreme beliefs. viewed earlier. in each trial. soon after."
        )
        cls.ranker = ContextRanker(
            [training, training],
            {},
            TestDictionary(),
            ["cach"],
        )

    def run_repair(self, text, readings, book=None):
        model, row = entry(text)
        row["inline"] = (
            [dict(start=text.index("beliets"), end=len(text), tag="em")]
            if "beliets" in text
            else []
        )
        audit = []
        result = repair(
            [model],
            {"1": [row]},
            book or {},
            self.ranker,
            lambda *args: dict(readings=readings, embedded_word=readings[0]),
            audit,
        )
        return row, result, audit

    def test_context_disambiguates_common_errors(self):
        for word, target, context in [
            ("amnesta", "amnesia", "amnesta on the part of the receivers"),
            ("beliets", "beliefs", "extreme beliets"),
            ("carlier", "earlier", "viewed carlier"),
            ("atter", "after", "soon atter"),
        ]:
            with self.subTest(word=word):
                self.assertEqual(
                    self.ranker.rank(word, context, context.index(word))[0]["term"],
                    target,
                )

    def test_corroborated_repair_preserves_inline_offsets(self):
        row, report, audit = self.run_repair("extreme beliets", ["beliefs", "beliefs"])
        self.assertEqual(row["text"], "extreme beliefs")
        self.assertEqual(row["inline"], [dict(start=8, end=15, tag="em")])
        self.assertEqual(report["corrected"], 1)
        self.assertEqual(audit[0]["kind"], "whole-book-lexical-repair")

    def test_crop_conflict_abstains(self):
        row, report, _ = self.run_repair("extreme beliets", ["beliefs", "beliets"])
        self.assertEqual(row["text"], "extreme beliets")
        self.assertEqual(report["corrected"], 0)

    def test_protected_names_and_accepted_words_are_untouched(self):
        row, report, _ = self.run_repair(
            "Carlier cach amrita beliefs", ["each", "each"]
        )
        self.assertEqual(row["text"], "Carlier cach amrita beliefs")
        self.assertEqual(report["corrected"], 0)

    def test_references_are_neither_training_nor_repair_input(self):
        row, report, _ = self.run_repair(
            "extreme beliets", ["beliefs", "beliefs"], {"reference_pages": [1]}
        )
        self.assertEqual(report["decisions"], [])
        reference, _ = entry("a fictitiouspropername", chapter=2, page=2)
        self.assertTrue(
            ContextRanker.excluded(reference, {"statistics_excluded_chapters": [2]})
        )

    def test_missing_general_pair_has_unigram_backoff(self):
        self.assertGreater(
            self.ranker.transition("unattested-left", "after"),
            self.ranker.transition("unattested-left", "cartier"),
        )

    def test_crop_does_not_borrow_matching_word_elsewhere_in_row(self):
        words = [(0, 0, 0, 0, w) for w in "soon atler we went after lunch".split()]
        located = locate_word("soon atter we went after lunch", words, "atter", "after")
        self.assertEqual(located[4], "atler")

    def test_ambiguous_crop_position_abstains(self):
        words = [(0, 0, 0, 0, w) for w in "some amnesia here some amnesia here".split()]
        self.assertIsNone(locate_word("some amnesta here", words, "amnesta", "amnesia"))

    @unittest.skipUnless(
        shutil.which("tesseract"), "Local OCR integration requires Tesseract"
    )
    def test_image_only_crop_uses_fresh_geometry_without_embedded_vote(self):
        native = pymupdf.open()
        page = native.new_page(width=300, height=100)
        page.insert_text((20, 50), "extreme beliefs", fontsize=16)
        pixels = page.get_pixmap(dpi=300)
        scan = pymupdf.open()
        scanned = scan.new_page(width=300, height=100)
        scanned.insert_image(scanned.rect, stream=pixels.tobytes("png"))
        self.assertEqual(scanned.get_text("words"), [])
        row = dict(page=1, text="extreme beliets", bbox=[0.06, 0.32, 0.5, 0.53])
        with tempfile.TemporaryDirectory() as work:
            recognizer = CropRecognizer(scan, work)
            result = recognizer(row, "beliets", "beliefs")
            self.assertEqual(result["geometry_source"], "local-line-ocr")
            self.assertEqual(result["embedded_word"], "")
            self.assertTrue(
                all("beliefs" in reading.lower() for reading in result["readings"])
            )
            self.assertEqual(recognizer(row, "beliets", "beliefs"), result)
        scan.close()
        native.close()


if __name__ == "__main__":
    unittest.main()
