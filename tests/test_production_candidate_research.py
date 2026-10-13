"""Exercise production filters and witness gates rather than oracle pools."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from candidates import exclusion_reason
from production_candidate_research import row_model, evaluate_case, summarize
from review_candidates import ReviewWitnesses
from symspellpy import SymSpell


def row(text, page=1):
    return dict(text=text, page=page, row_id=f"r{page}", bbox=[0.1, 0.2, 0.9, 0.3])


class ProductionCandidateResearchTests(unittest.TestCase):
    def test_short_and_known_tokens_are_excluded_before_lexical_search(self):
        dictionary = Mock(lookup=lambda w: w == "modem")
        sym = SymSpell(max_dictionary_edit_distance=2)
        self.assertEqual(
            exclusion_reason("con", dictionary, set(), sym, "en"), "short-token"
        )
        self.assertEqual(
            exclusion_reason("modem", dictionary, set(), sym, "en"), "dictionary-known"
        )
        self.assertEqual(
            exclusion_reason("RareName", dictionary, {"rarename"}, sym, "en"),
            "protected",
        )

    def test_peer_disagreement_bypasses_dictionary_filter_with_anchors(self):
        bank = ReviewWitnesses(Path("/unused"), dict(text_source="native"))
        bank.peers = lambda source, all_engines=False: {
            "vision": row("old modern science today"),
            "rapid": row("old modern science today"),
        }
        case = dict(
            id="one",
            book="book",
            page=1,
            before="modem",
            after="modern",
            expected="correct",
        )
        result = evaluate_case(
            case,
            row("old modem science today"),
            bank,
            Mock(lookup=lambda w: True),
            set(),
            SymSpell(max_dictionary_edit_distance=2),
            "en",
        )
        self.assertEqual(result["lexical_exclusion"], "dictionary-known")
        self.assertTrue(result["detected"])
        self.assertTrue(result["candidate_recall"])
        self.assertEqual(result["generators"], {"aligned-ocr-witnesses": ["modern"]})
        result = evaluate_case(
            case,
            row("modem science"),
            bank,
            Mock(lookup=lambda w: True),
            set(),
            SymSpell(max_dictionary_edit_distance=2),
            "en",
        )
        self.assertFalse(result["detected"])

    def test_detected_item_without_candidates_is_retained_as_unresolved(self):
        bank = ReviewWitnesses(Path("/unused"), dict(text_source="native"))
        case = dict(
            id="one",
            book="book",
            page=1,
            before="zzzzxx",
            after="rareword",
            expected="correct",
        )
        with patch("candidates.zipf_frequency", return_value=0):
            result = evaluate_case(
                case,
                row("old zzzzxx story today"),
                bank,
                Mock(lookup=lambda w: False),
                set(),
                SymSpell(max_dictionary_edit_distance=2),
                "en",
            )
        self.assertTrue(result["unresolved"])
        self.assertFalse(result["candidate_recall"])
        self.assertEqual(summarize([result])["unresolved_errors"], 1)

    def test_raw_fixture_uses_profile_chapters_and_exact_source_spans(self):
        book = dict(chapters=[[5, 10, "First"], [11, 20, "Second"]])
        self.assertEqual(row_model(book, 3, [row("outside")]), [])
        result = row_model(book, 12, [row("literal source")])[0]
        self.assertEqual(result["chapter"], 2)
        source = result["blocks"][0]["sources"][0]
        self.assertEqual(source["end"], len(source["text"]))
        self.assertEqual(source["page"], 12)
        with self.assertRaisesRegex(ValueError, "Ambiguous chapter"):
            row_model(dict(chapters=[[1, 3, "A"], [2, 4, "B"]]), 2, [])

    def test_ambiguous_token_fails_instead_of_selecting_an_answer_location(self):
        case = dict(id="one", before="bad")
        with self.assertRaisesRegex(ValueError, "Ambiguous case token"):
            evaluate_case(case, row("bad bad"), None, None, set(), None, "en")


if __name__ == "__main__":
    unittest.main()
