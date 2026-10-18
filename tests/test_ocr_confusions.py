"""Recurring glyph confusions repaired only with structural or witness evidence."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ocr import comparable, repair_confusions, same_but_unaccented


def repaired(
    text, witnesses=("", ""), language="en", index_digits=True, index_page_labels=None
):
    row = dict(text=text, bbox=[0, 0, 1, 1])
    audit = []
    fresh = [dict(text=w) for w in witnesses]
    repair_confusions(
        row,
        fresh,
        1,
        audit,
        language,
        index_digits,
        index_page_labels=index_page_labels,
    )
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
        self.assertEqual(
            repaired("doses were 50g, 196g, and 30g")[0],
            "doses were 50g, 196g, and 30g",
        )

    def test_extended_index_digits_preserve_note_suffix_and_bind_the_slot(self):
        raw = "Homeopathy, 6g, 168-16gn"
        fixed = "Homeopathy, 69, 168-169n"
        labels = {"69", "168", "169"}
        self.assertEqual(
            repaired(raw, (fixed, raw), index_page_labels=labels)[0], fixed
        )
        self.assertEqual(
            repaired(
                "Rapture, 31, g0. See",
                ("Rapture, 31, 90. See",) * 2,
                index_page_labels={"90"},
            )[0],
            "Rapture, 31, 90. See",
        )
        self.assertEqual(
            repaired("Notes, I12n", ("Notes, 112n",) * 2, index_page_labels={"112"})[0],
            "Notes, 112n",
        )
        for witnesses, available, enabled in [
            ((fixed, "Homeopathy, 69, 168-167n"), labels, True),
            ((fixed, "Homeopathy, 169, 168-69n"), labels, True),
            ((fixed, "Homeopathy, 69, 168-169"), labels, True),
            ((fixed, "Homeopath, 69, 168-169n"), labels, True),
            ((raw, raw), labels, True),
            ((fixed, ""), labels, True),
            ((fixed, fixed), set(), True),
            ((fixed, fixed), None, True),
            ((fixed, fixed), labels, False),
        ]:
            with self.subTest(witnesses=witnesses, labels=available, enabled=enabled):
                result = repaired(
                    raw, witnesses, index_digits=enabled, index_page_labels=available
                )[0]
                self.assertEqual(result, raw)
        raw = "Soma, 6g, 69"
        self.assertEqual(
            repaired(raw, ("Soma, 68, 69", "Soma, 69, 69"), index_page_labels={"69"})[
                0
            ],
            raw,
        )
        # A unit outside reference-list syntax and a pure Roman numeral stay put.
        for raw, fixed in [
            ("add 16g of salt", "add 169 of salt"),
            ("Force, II", "Force, 11"),
        ]:
            self.assertEqual(
                repaired(raw, (fixed,) * 2, index_page_labels={"169", "11"})[0], raw
            )

    def test_split_citation_year_needs_two_aligned_literal_witnesses(self):
        raw = "Luke, D. (201 1). Discarnate entities"
        fixed = "Luke, D. (2011). Discarnate entities"
        self.assertEqual(
            repaired(raw, (fixed,) * 2), (fixed, ["witnessed-citation-year-spacing"])
        )
        for raw, fixed in [
            ("(Smith, 19 84a, p. 20)", "(Smith, 1984a, p. 20)"),
            ("(2 0 1 1); title", "(2011); title"),
            ("201 1), p. 20", "2011), p. 20"),
        ]:
            self.assertEqual(repaired(raw, (fixed,) * 2)[0], fixed)
        raw = "Luke, D. (201 1). Discarnate entities"
        fixed = "Luke, D. (2011). Discarnate entities"
        for witnesses in [
            (fixed, ""),
            (fixed, raw),
            (fixed, "Luke, D. (2017). Discarnate entities"),
            (fixed, "Luke, D. (2011). Disparate entities"),
            ("Luke, D. (201 1). Discarnate entities; 2011",) * 2,
        ]:
            # Reset the source after the accepted variants above.
            source = "Luke, D. (201 1). Discarnate entities"
            self.assertEqual(repaired(source, witnesses)[0], source)
        for raw, fixed in [
            ("take 201 1 tablets", "take 2011 tablets"),
            ("doses, 201 1, stir", "doses, 2011, stir"),
            ("(20 111)", "(20111)"),
            ("(12 34)", "(1234)"),
        ]:
            self.assertEqual(repaired(raw, (fixed,) * 2)[0], raw)

    def test_bracket_l_only_for_common_words_without_open_bracket(self):
        self.assertEqual(
            repaired("they enter schoo]. Overwhelming contro]")[0],
            "they enter school. Overwhelming control",
        )
        self.assertEqual(repaired("see [sic] and contro]")[0], "see [sic] and contro]")
        self.assertEqual(repaired("the here] end")[0], "the here] end")

    def test_russian_rows_drop_letters_outside_the_alphabet(self):
        witnesses = ("в Х1Х в., И. И. Гмелиным", "в XIХ в., И. И. Гмелиным")
        self.assertEqual(
            repaired("в ХІХ в., И. І. Імелиным", witnesses, "ru")[0],
            "в XIX в., И. И. Гмелиным",
        )
        self.assertEqual(
            repaired("оказываєтся такоє, т. є. 1928, Nº 1", language="ru")[0],
            "оказывается такое, т. є. 1928, № 1",
        )
        # Disagreeing witnesses, or English rows, leave the token alone.
        split = ("и т. д., т. е. речь", "и т. д., 1. е. речь")
        self.assertEqual(
            repaired("и т. д., І. е. речь", split, "ru")[0], "и т. д., І. е. речь"
        )
        self.assertEqual(repaired("Nº 5 and ХІХ")[0], "Nº 5 and ХІХ")
        # Latin look-alike words between Russian words; not numerals,
        # word fragments, non-words or Latin context.
        for raw, fixed in [
            ("снег, a снег Ha тундру", "снег, а снег на тундру"),
            ("самка oT пяти, жира HO они", "самка от пяти, жира но они"),
            ("озера H реки, быст po грубеет", "озера H реки, быст po грубеет"),
            ("у XIX столетия, Te-дят", "у XIX столетия, Te-дят"),
            ("Washington, 1899, p. 208", "Washington, 1899, p. 208"),
        ]:
            self.assertEqual(repaired(raw, language="ru")[0], fixed)

    def test_corroborated_dotted_letters_are_not_dictionary_normalized(self):
        for source, witnesses in [
            ("Акі башту 172", ("Ак! башту 172", "Акі башту 172")),
            ("Мір и покой", ("мір и покой", "Мир и покой")),
            (
                "Акі баштыг 171 172 176",
                ("Аки баштыг 171 172 176", "Акі баштыг 171 172 176"),
            ),
        ]:
            with self.subTest(source=source):
                text, kinds = repaired(source, witnesses, "ru")
                self.assertEqual(text, source)
                self.assertNotIn("russian-letter-lookalike", kinds)
                self.assertNotIn("russian-letter-witness", kinds)
        # The ordinary no-witness lexical fallback remains available.
        self.assertEqual(
            repaired("оказываєтся такоє", language="ru")[0], "оказывается такое"
        )
        self.assertEqual(
            repaired("оказываєтся такоє", ("оказываєтся такоє",) * 2, "ru")[0],
            "оказывается такое",
        )

    def test_nonword_takes_a_fresh_common_word_one_letter_away(self):
        self.assertEqual(
            repaired("in the тувдре of the", ("in the тундре of the", ""), "ru")[0],
            "in the тундре of the",
        )
        self.assertEqual(
            repaired("Cognitive Theorv and", ("Cognitive Theory and", "x"))[0],
            "Cognitive Theory and",
        )
        # Row-edge fragments, real words, competing proposals, two-letter edits.
        for raw, witness in [
            ("лучения в тундре", "мучения в тундре"),
            ("see the worm now", "see the warm now"),
            ("a frabjous day", "a fabulous day"),
            ("expérience observés au", "expérience observes au"),
        ]:
            self.assertEqual(repaired(raw, (witness, ""))[0], raw)
        # Several common words fit the slot: истории or история.
        self.assertEqual(
            repaired(
                "Проблемы историн общественного",
                ("Проблемы история общественного", ""),
                "ru",
            )[0],
            "Проблемы историн общественного",
        )
        # A token repeated on the page is a name, not an OCR slip.
        row = dict(text="was called Xano, and", bbox=[0, 0, 1, 1])
        repair_confusions(
            row, [dict(text="was called Nano, and")], 1, [], "en", False, {"Xano": 6}
        )
        self.assertEqual(row["text"], "was called Xano, and")
        self.assertEqual(
            repaired("the worns are", ("the worms are", "the warns are"))[0],
            "the worns are",
        )

    def test_witness_dropping_accents_confirms_only_rare_words(self):
        agree = lambda a, b, lang="en": same_but_unaccented(
            comparable(a), comparable(b), lang
        )
        self.assertTrue(agree("Max Delbrück, a", "Max Delbruck, a"))
        self.assertFalse(agree("seems tó come", "seems to come"))
        self.assertFalse(agree("ходьбе й спо", "ходьбе и спо", "ru"))
        self.assertFalse(agree("Max Delbruck", "Max Delbrück"))


if __name__ == "__main__":
    unittest.main()
