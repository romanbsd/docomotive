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

    def test_russian_markov_ranking_and_source_gates(self):
        class RussianDictionary:
            def lookup(self, word):
                return word in {"шаманского", "алтайцы", "более"}

        dictionary = RussianDictionary()
        training, _ = entry(
            "основы шаманского культа. был еще более длинным. Алтайцы и хакасы называли рукоять."
        )
        ranker = ContextRanker([training, training], {"language": "ru"}, dictionary)
        for word, target, context in [
            ("аманского", "шаманского", "основы аманского культа"),
            ("болес", "более", "был еще болес длинным"),
            ("Алтанцы", "Алтайцы", "Алтанцы и хакасы называли рукоять"),
        ]:
            with self.subTest(word=word):
                self.assertEqual(
                    ranker.rank(word, context, context.index(word))[0]["term"],
                    target.lower(),
                )
                model, row = entry(context)
                report = repair(
                    [model],
                    {"1": [row]},
                    {"language": "ru"},
                    ranker,
                    lambda *args: dict(readings=[target, target]),
                    [],
                )
                self.assertEqual(report["corrected"], 1)
                self.assertIn(target, row["text"])
        self.assertGreater(
            ranker.transition("шаманского", "культа"),
            ranker.transition("шаманского", "рукоять"),
        )
        model, row = entry("основы аманского культа")
        report = repair(
            [model],
            {"1": [row]},
            {"language": "ru"},
            ranker,
            lambda *args: dict(readings=["шаманского", "аманского"]),
            [],
        )
        self.assertEqual(report["corrected"], 0)

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

    def test_russian_markov_context_changes_the_same_edit_distance_choice(self):
        class Dictionary:
            def lookup(self, word):
                return word in {"коды", "козы"}

        training, _ = entry("секретные коды доступа. дикие козы пасутся. " * 10)
        ranker = ContextRanker([training], {"language": "ru"}, Dictionary())
        # Both targets are one substitution from the same corrupted token.
        # Adjacent-word transitions must change the winner, not just Zipf.
        for text, expected in [
            ("секретные коцы доступа", "коды"),
            ("дикие коцы пасутся", "козы"),
        ]:
            self.assertEqual(
                ranker.rank("коцы", text, text.index("коцы"))[0]["term"], expected
            )

    def test_cyrillic_fallback_requires_stable_high_score_and_pins_models(self):
        from types import SimpleNamespace
        from PIL import Image

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            models = root / "models"
            models.mkdir()
            for name in (
                "cyrillic_PP-OCRv5_rec_mobile.onnx",
                "ch_PP-OCRv4_det_mobile.onnx",
                "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            ):
                (models / name).write_bytes(b"test-model")
            image = root / "word.png"
            Image.new("RGB", (100, 50), "white").save(image)
            recognizer = CropRecognizer.__new__(CropRecognizer)
            recognizer.models, recognizer.work = models, root
            calls = []

            def recognize(*args, **kwargs):
                calls.append(1)
                return SimpleNamespace(txts=("шаманского",), scores=(0.93,))

            recognizer.rapid = recognize
            evidence = dict(readings=["маманского", "маманского"])
            result = recognizer.with_fallback(evidence, image, "шаманского")
            self.assertEqual(result["readings"], ["шаманского", "шаманского"])
            self.assertEqual(result["tesseract_readings"], evidence["readings"])
            recognizer.with_fallback(evidence, image, "шаманского")
            self.assertEqual(len(calls), 2)
            (models / "cyrillic_PP-OCRv5_rec_mobile.onnx").write_bytes(b"changed-model")
            recognizer.rapid = lambda *args, **kwargs: SimpleNamespace(
                txts=("шаманского",), scores=(0.89,)
            )
            self.assertEqual(
                recognizer.with_fallback(evidence, image, "шаманского")["readings"],
                evidence["readings"],
            )
            (models / "cyrillic_PP-OCRv5_rec_mobile.onnx").write_bytes(b"another-model")
            readings = iter(["шаманского", "маманского"])
            recognizer.rapid = lambda *args, **kwargs: SimpleNamespace(
                txts=(next(readings),), scores=(0.99,)
            )
            self.assertEqual(
                recognizer.with_fallback(evidence, image, "шаманского")["readings"],
                evidence["readings"],
            )

    def test_title_case_crop_anchors_are_case_insensitive(self):
        words = [(0, 0, 0, 0, w) for w in "значения Алтайцы и хакасы".split()]
        self.assertEqual(
            locate_word("значения Алтанцы и хакасы", words, "Алтанцы", "алтайцы")[4],
            "Алтайцы",
        )

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
