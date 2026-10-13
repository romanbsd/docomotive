"""Guard literal output, bounded retrieval and label-free review priorities."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from simple_ocr_research import (
    VocabularyIndex,
    TrigramIndex,
    accent_key,
    peer_candidate,
    queue_report,
    suppression_report,
    measure,
    verify_frozen,
)


def case(ident, page, before, after=None, book="one", expected="correct", readings=()):
    return dict(
        id=ident,
        page=page,
        book=book,
        before=before,
        after=after or before,
        expected=expected,
        readings=list(readings),
        book_only_candidate_recall=False,
    )


class SimpleOCRResearchTests(unittest.TestCase):
    def test_accent_key_is_only_an_index_and_preserves_colliding_literals(self):
        self.assertEqual(accent_key("café"), accent_key("cafe\u0301"))
        bank = VocabularyIndex({"café": 3, "cafe": 2}, radius=1, folded=True)
        self.assertEqual(bank.lookup("cafè"), {"cafe", "café"})
        self.assertEqual(bank.lookup("cafe"), {"café"})

    def test_radius_three_only_expands_retrieval(self):
        words = {"abcdefg": 3}
        self.assertEqual(VocabularyIndex(words).lookup("abxyzfg"), set())
        self.assertEqual(
            VocabularyIndex(words, radius=3).lookup("abxyzfg"), {"abcdefg"}
        )

    def test_trigram_fallback_is_bounded_and_does_not_use_short_tokens(self):
        bank = TrigramIndex({f"abcdef{chr(97+i)}": 2 for i in range(20)})
        self.assertEqual(len(bank.lookup("abcdefz")), 10)
        self.assertEqual(bank.lookup("abc"), set())
        self.assertNotIn("abcdefa", bank.lookup("abcdefa"))

    def test_peer_suggestion_requires_two_identical_non_original_words(self):
        self.assertEqual(
            peer_candidate(case("a", 1, "bvrn", readings=("burn", "burn"))), "burn"
        )
        for readings in (
            ("burn",),
            ("burn", "barn"),
            ("bvrn", "bvrn"),
            ("b rn", "b rn"),
        ):
            self.assertIsNone(peer_candidate(case("a", 1, "bvrn", readings=readings)))

    def test_queue_order_is_independent_of_answers_and_labels(self):
        cases = [case("b", 2, "xyz"), case("a", 1, "word", readings=("ward", "ward"))]
        original = queue_report(cases, lambda w: w == "word", True)["order"]
        altered = deepcopy(cases[::-1])
        for row in altered:
            row.update(after="anything", expected="retain")
        self.assertEqual(
            original, queue_report(altered, lambda w: w == "word", True)["order"]
        )
        self.assertEqual(original, ["a", "b"])

    def test_unchanged_feedback_is_page_and_book_local_and_has_peer_veto(self):
        cases = [
            case("seed", 1, "rare", expected="retain"),
            case("repeat", 2, "rare", expected="retain"),
            case("error", 2, "rare", readings=("care", "care")),
            case("other_seed", 1, "else", book="two", expected="retain"),
            case("other", 2, "rare", book="two"),
        ]
        snapshot = deepcopy(cases)
        result = suppression_report(cases, lambda w: False)
        self.assertEqual(result["suppressed_control_ids"], ["repeat"])
        self.assertEqual(result["suppressed_error_ids"], [])
        self.assertEqual(result["disagreement_veto_ids"], ["error"])
        self.assertEqual(cases, snapshot)

    def test_recall_and_control_alternatives_are_distinct_metrics(self):
        cases = [
            case("error", 1, "bvrn", "burn"),
            case("control", 2, "burn", expected="retain"),
        ]
        result = measure(cases, {"error": ["burn"], "control": ["barn"]})
        self.assertEqual(result["additional_answers"], 1)
        self.assertEqual(result["controls_with_alternatives"], 1)

    def test_changed_frozen_cases_fail_before_baseline_comparison(self):
        original = case("a", 1, "bvrn", "burn")
        changed = dict(original, before="bvm")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            verify_frozen({}, [changed], {"cases": [original]})
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            verify_frozen({}, [], {"cases": [original]})


if __name__ == "__main__":
    unittest.main()
