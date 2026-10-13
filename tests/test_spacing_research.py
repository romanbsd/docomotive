"""Validate research measurements and prevent candidate self-training."""

from collections import Counter
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from spacing_research import boundaries, context_evidence, gap_evidence


class Ranker:
    language = "en"
    general = Counter({("a", "of"): 50, ("of", "the"): 50, ("the", "book"): 50})
    outgoing = Counter(a=100, of=100, the=100)

    def __init__(self, words):
        self.pairs = Counter(zip(words, words[1:]))
        self.book_outgoing = Counter(a for a, _ in self.pairs)


class SpacingResearchTests(unittest.TestCase):
    def test_space_boundary_measurement_preserves_nonspace_characters(self):
        self.assertEqual(boundaries("of life"), ("oflife", {2}))
        self.assertEqual(boundaries("of  life"), ("oflife", {2}))
        observation = dict(
            text="of life",
            characters=[
                dict(start=i, end=i + 1, bbox=[i * 2, 0, i * 2 + 1, 10])
                for i, c in enumerate("of life")
                if not c.isspace()
            ],
        )
        evidence = gap_evidence(observation, "oflife", "of life")
        self.assertEqual(evidence["reason"], "aligned")
        self.assertAlmostEqual(evidence["gaps"][0]["gap_over_height"], 0.3)
        self.assertEqual(evidence["gaps"][0]["operation"], "insert-space")
        with self.assertRaisesRegex(ValueError, "preserve"):
            gap_evidence(observation, "oflife", "of lives")

    def test_glyph_measurement_abstains_on_ambiguous_or_missing_readings(self):
        for text in ["different letters", "oflife oflife"]:
            evidence = gap_evidence(dict(text=text, characters=[]), "oflife", "of life")
            self.assertEqual(evidence["reason"], "missing-or-ambiguous-literal-glyphs")
            self.assertEqual(evidence["gaps"], [])

    def test_leave_out_removes_erroneous_source_pair_without_mutating_book(self):
        ranker = Ranker(["a", "ofthe", "book"])
        original = ranker.pairs.copy()
        result = context_evidence(
            ranker,
            dict(row_text="a ofthe book", before="ofthe", after="of the"),
            "a ofthe book",
        )
        self.assertGreater(
            result["leave_source_row_out_delta"], result["original_delta"]
        )
        self.assertEqual(
            result["excluded_pairs"],
            [
                dict(words=["a", "ofthe"], count=1),
                dict(words=["ofthe", "book"], count=1),
            ],
        )
        self.assertEqual(ranker.pairs, original)

    def test_leave_out_removes_a_reviewed_answer_instead_of_training_on_it(self):
        ranker = Ranker(["a", "of", "the", "book"])
        result = context_evidence(
            ranker,
            dict(row_text="a ofthe book", before="ofthe", after="of the"),
            "a of the book",
        )
        self.assertEqual(
            result["excluded_pairs"],
            [
                dict(words=["a", "of"], count=1),
                dict(words=["of", "the"], count=1),
                dict(words=["the", "book"], count=1),
            ],
        )

    def test_context_comparison_requires_a_unique_source_occurrence(self):
        result = context_evidence(
            Ranker(["ofthe", "ofthe"]),
            dict(row_text="ofthe ofthe", before="ofthe", after="of the"),
            "ofthe ofthe",
        )
        self.assertEqual(result["reason"], "ambiguous-source-occurrence")


if __name__ == "__main__":
    unittest.main()
