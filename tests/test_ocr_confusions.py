"""Recurring glyph confusions repaired only with structural or witness evidence."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ocr import repair_confusions


def repaired(text, witnesses=("", ""), language="en", index_digits=True):
    row = dict(text=text, bbox=[0, 0, 1, 1])
    audit = []
    fresh = [dict(text=w) for w in witnesses]
    repair_confusions(row, fresh, 1, audit, language, index_digits)
    return row["text"], [a["kind"] for a in audit]


class ConfusionTests(unittest.TestCase):
    def test_em_dash_needs_both_fresh_engines(self):
        both = ("coincidence—that at", "coincidence— that at")
        self.assertEqual(
            repaired("in coincidence-that at", both)[0], "in coincidence—that at"
        )
        doubled = ("charming—often requires", "charming--often requires")
        self.assertEqual(
            repaired("charming-often requires", doubled)[0], "charming—often requires"
        )
        one = ("a well—known fact", "a well-known fact")
        self.assertEqual(repaired("a well-known fact", one)[0], "a well-known fact")
        spaced = ("слово — слово и что-то",) * 2
        self.assertEqual(
            repaired("слово-слово и что-то", spaced, "ru")[0], "слово — слово и что-то"
        )

    def test_index_digits_need_list_syntax_and_a_digit_witness(self):
        self.assertEqual(
            repaired(
                "Character, 39, I12, I51",
                ("Character, 39, 112, 151", "Character, 39, I12, I51"),
            )[0],
            "Character, 39, 112, 151",
        )
        # A contradicting reading of the slot, or a missing anchor word, wins.
        for other in ("Character, 39, 152, 151", "Charter, 39, 112, 151"):
            self.assertEqual(
                repaired("Character, 39, I12", ("Character, 39, 112", other))[0],
                "Character, 39, I12",
            )
        # Roman numerals and unwitnessed tokens stay as printed.
        self.assertEqual(repaired("Force, II, I30", ("", ""))[0], "Force, II, I30")
        self.assertEqual(
            repaired("Chapter I: II", ("Chapter 1: 11", ""))[0], "Chapter I: II"
        )

    def test_citation_years_but_not_quantities(self):
        self.assertEqual(
            repaired("(December 196g), p. 11; (Osmond, 196la, p.76)")[0],
            "(December 1969), p. 11; (Osmond, 1961a, p.76)",
        )
        self.assertEqual(repaired("196g), p. 41.")[0], "1969), p. 41.")
        self.assertEqual(
            repaired("add 150g, stir; take 196g, then")[0],
            "add 150g, stir; take 196g, then",
        )

    def test_bracket_l_only_for_common_words_without_open_bracket(self):
        self.assertEqual(
            repaired("they enter schoo]. Overwhelming contro]")[0],
            "they enter school. Overwhelming control",
        )
        self.assertEqual(repaired("see [sic] and contro]")[0], "see [sic] and contro]")
        self.assertEqual(repaired("the here] end")[0], "the here] end")


if __name__ == "__main__":
    unittest.main()
