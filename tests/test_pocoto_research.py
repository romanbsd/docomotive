"""Guard retrospective grouping scope and interpretation."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pocoto_research import groupability


def case(ident, before, after, book="one", expected="correct"):
    return dict(id=ident, before=before, after=after, book=book, expected=expected)


class PoCoToResearchTests(unittest.TestCase):
    def test_grouping_is_book_scoped_and_does_not_use_controls_as_errors(self):
        cases = [
            case("1", "fames", "james"),
            case("2", "fohn", "john"),
            case("3", "fean", "jean", book="other"),
            case("control", "flower", "flower", expected="retain"),
        ]
        original = [dict(c) for c in cases]
        result = groupability(cases)
        self.assertEqual(result["summary"]["repeated_groups"], 1)
        group = result["groups"][0]
        self.assertEqual(group["member_ids"], ["1", "2"])
        self.assertEqual(
            group["correct_controls_exposed_to_blanket_rewrite"], ["control"]
        )
        self.assertEqual(cases, original)

    def test_multiple_error_chunks_do_not_masquerade_as_one_pattern(self):
        result = groupability([case("1", "xbc", "abz"), case("2", "xbc", "abz")])
        self.assertEqual(result["groups"], [])

    def test_large_error_chunk_cannot_hide_a_second_small_error(self):
        cases = [
            case("1", "xxxxhellox", "yyyyhelloy"),
            case("2", "xxxxhellox", "yyyyhelloy"),
        ]
        self.assertEqual(groupability(cases)["groups"], [])


if __name__ == "__main__":
    unittest.main()
