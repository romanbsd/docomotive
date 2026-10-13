"""Research-component invariants, separate from production correction gates."""

from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from statistical_ocr_research import (
    CharacterModel,
    ErrorModel,
    aligned_readings,
    candidate_pool,
    edit_rules,
    fit_ridge,
    ridge_score,
)


class StatisticalResearchTests(unittest.TestCase):
    def test_directed_multichar_channel_and_minimum_path(self):
        self.assertEqual(list(edit_rules("modem", "modern")), [("rn", "m")])
        model = ErrorModel([("modem", "modern"), ("bam", "barn")])
        self.assertLess(model.distance("modern", "modem"), 1)
        self.assertEqual(model.distance("modem", "modern"), 2)
        self.assertEqual(model.distance("modern", "modern"), 0)
        self.assertEqual(ErrorModel([]).distance("kitten", "sitting"), 3)

    def test_bounded_rewrite_lattice_includes_original_and_multichar_edit(self):
        model = ErrorModel([("modem", "modern"), ("talk", "stalk")])
        variants = model.variants("modem", budget=1)
        self.assertEqual(variants["modem"], 0)
        self.assertIn("modern", variants)
        self.assertEqual(model.variants("modem", budget=0), {"modem": 0})
        self.assertLessEqual(len(model.variants("talk", budget=2, beam=4)), 9)

    def test_original_remains_candidate_without_dictionary_or_witness_support(self):
        class Empty:
            def lookup(self, *args):
                return []

        class Ranker:
            sym = Empty()

        self.assertEqual(
            candidate_pool("unknown", Ranker(), Empty(), ErrorModel([]), True),
            ["unknown"],
        )

    def test_witness_requires_unique_position_and_neighbors(self):
        self.assertEqual(
            aligned_readings(
                "the modem science book", "modem", ["the modern science book"]
            ),
            ["modern"],
        )
        self.assertEqual(aligned_readings("modem", "modem", ["modern"]), [])
        self.assertEqual(
            aligned_readings("modem and modem", "modem", ["modern and modem"]), []
        )

    def test_character_prior_uses_corpus_and_has_finite_unseen_backoff(self):
        model = CharacterModel(["modern", "modem", "science"])
        self.assertGreater(model.score("modern"), model.score("zzzzzzz"))
        self.assertTrue(np.isfinite(model.score("unseen")))

    def test_ridge_is_repeatable_with_constant_features(self):
        groups = [
            (dict(after="modern"), {"modem": [0, 0], "modern": [1, 0]}),
            (dict(after="book"), {"book": [1, 0], "boon": [0, 0]}),
        ]
        fit, repeated = fit_ridge(groups), fit_ridge(groups)
        self.assertGreater(ridge_score([1, 0], fit), ridge_score([0, 0], fit))
        for a, b in zip(fit, repeated):
            np.testing.assert_array_equal(a, b)


if __name__ == "__main__":
    unittest.main()
