"""Prevent answer leakage in the adaptive-feedback feasibility experiment."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from adaptive_profile_research import evaluate, proposals


def case(ident, page, before, after, book="one", expected="correct"):
    return dict(
        id=ident,
        page=page,
        before=before,
        after=after,
        book=book,
        expected=expected,
        book_only_candidate_recall=False,
    )


class AdaptiveProfileResearchTests(unittest.TestCase):
    def test_same_page_other_books_and_controls_cannot_seed_feedback(self):
        cases = [
            case("1", 1, "bam", "barn"),
            case("2", 1, "modem", "modern"),
            case("3", 2, "modem", "modern"),
            case("control", 2, "modem", "modem", expected="retain"),
            case("other", 1, "modem", "modern", book="two"),
        ]
        result = evaluate(cases, lambda w: True, "leave-page-out")
        split = next(s for s in result["splits"] if "1" in s["evaluation_ids"])
        self.assertEqual(split["seed_ids"], ["3"])
        self.assertNotIn("2", split["seed_ids"])
        self.assertEqual(cases[0]["before"], "bam")

    def test_prefix_has_no_future_feedback_and_retains_original(self):
        cases = [case("seed", 1, "bam", "barn"), case("later", 2, "modem", "modern")]
        result = evaluate(cases, lambda w: w == "modern", "prefix", 0.5)
        row = result["cases"][0]
        self.assertEqual(row["id"], "later")
        self.assertTrue(row["additional_answer"])
        self.assertTrue(row["original_retained"])
        self.assertEqual(row["alternatives"]["modern"][0]["seed_id"], "seed")

    def test_reviewed_rare_target_and_control_exposure_are_distinct(self):
        seed = case("seed", 1, "xhina", "xuma")
        self.assertIn("xuma", proposals("xumu", [seed], lambda w: False))
        cases = [
            case("seed", 1, "bam", "barn"),
            case("control", 2, "modem", "modem", expected="retain"),
        ]
        result = evaluate(cases, lambda w: True, "prefix", 0.2)
        self.assertEqual(result["summary"]["correct_controls_exposed"], 1)
        self.assertEqual(result["summary"]["additional_answers"], 0)

    def test_multi_chunk_or_long_edits_do_not_supply_short_rules(self):
        seeds = [
            case("multiple", 1, "xbc", "abz"),
            case("long", 1, "xxxxhellox", "yyyyhelloy"),
        ]
        self.assertNotIn("abc", proposals("xbc", seeds, lambda w: True))


if __name__ == "__main__":
    unittest.main()
