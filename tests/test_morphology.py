"""Rare Russian joins need lexical evidence, never suffix-based guesses."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from morphology import RussianWrapLexicon
from book_model import JoinPolicy


class MorphologicalWrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lexicon = RussianWrapLexicon()

    def test_rare_inflection_is_known_and_only_changes_the_boundary(self):
        policy = JoinPolicy(language="ru", morphology=self.lexicon)
        self.assertEqual(
            policy.boundary("производили диа-", "хроническую стратификацию"),
            (1, "", "remove-morphological"),
        )
        self.assertEqual(
            JoinPolicy(language="ru").boundary(
                "производили диа-", "хроническую стратификацию"
            ),
            (0, "", "retain"),
        )

    def test_dictionary_backed_negative_noun(self):
        self.assertTrue(self.lexicon.supports("не", "изученность"))
        self.assertFalse(self.lexicon.supports("не", "работать"))

    def test_guessed_suffix_and_hyphen_requiring_prefix_abstain(self):
        for left, right in (
            ("сравнительно", "типологического"),
            ("арт", "дизайнер"),
            ("шаман", "щщ"),
            ("диа", "English"),
            ("не", "я"),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(self.lexicon.supports(left, right))

    def test_attested_compound_takes_precedence(self):
        policy = JoinPolicy(
            protected=["психо-активный"], language="ru", morphology=self.lexicon
        )
        self.assertEqual(policy.boundary("психо-", "активный"), (0, "", "retain"))

    def test_existing_join_keeps_existing_audit_action(self):
        policy = JoinPolicy(
            protected=["аспекты"], language="ru", morphology=self.lexicon
        )
        self.assertEqual(
            policy.boundary("разные аспек-", "ты исследования"), (1, "", "remove")
        )

    def test_provenance_binds_the_actual_dictionary(self):
        evidence = self.lexicon.provenance
        self.assertEqual(evidence["engine"], "pymorphy3")
        self.assertTrue(evidence["files"])
        self.assertTrue(all(len(value) == 64 for value in evidence["files"].values()))


if __name__ == "__main__":
    unittest.main()
