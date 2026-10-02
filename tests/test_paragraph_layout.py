"""Generic scan geometry regressions, without book titles or phrase overrides."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from book_model import JoinPolicy, reconstruct
from paragraph_layout import infer_paragraph_layout
from render_text import block_html


def scan_rows(slope=-0.04, quotation=True, one_sided=False):
    result = []
    for i in range(30):
        y = 0.1 + 0.025 * i
        inset = 0.04 if 5 <= i < 11 and quotation else 0
        x0 = 0.12 + slope * y + inset
        x1 = 0.92 + slope * y - (0 if one_sided else inset)
        if i == 10 and quotation:
            x1 -= (
                0.3  # Ragged final line is evidence of membership, not a right margin.
            )
        result.append(
            dict(
                text=f"Line {i} continues",
                bbox=[x0, y, x1, y + 0.02],
                column=0,
                row_id=str(i),
            )
        )
    return result


class ParagraphLayoutTests(unittest.TestCase):
    def test_skewed_continuation_and_inset_quote_are_inferred(self):
        first = scan_rows(quotation=False)
        second = scan_rows()
        first[-1]["text"] = "the preceding paragraph and"
        second[0]["text"] = "its continuation on another page."
        for r in first:
            r["row_id"] = "first-" + r["row_id"]
        book = dict(chapters=[[1, 2, "Section"]], chapter_body_starts={"1": 0})
        model = reconstruct({1: first, 2: second}, book, JoinPolicy(), [])
        blocks = model[0]["blocks"]
        self.assertIn("and its continuation", blocks[0]["text"])
        quote = next(b for b in blocks if b["kind"] == "quote")
        self.assertEqual(len(quote["lines"]), 6)
        self.assertIn(
            '<blockquote><p class="noindent">',
            block_html(quote, book, set(), [], "chapter.xhtml"),
        )

    def test_paragraph_indent_is_preserved_after_skew_compensation(self):
        rows = scan_rows(quotation=False)
        rows[15]["bbox"][0] += 0.035
        model = reconstruct(
            {1: rows},
            dict(chapters=[[1, 1, "Section"]], chapter_body_starts={"1": 0}),
            JoinPolicy(),
            [],
        )
        self.assertEqual(len(model[0]["blocks"]), 2)
        self.assertTrue(model[0]["blocks"][1]["text"].startswith("Line 15"))

    def test_one_sided_indentation_is_not_a_block_quote(self):
        evidence, report = infer_paragraph_layout(scan_rows(one_sided=True))
        self.assertEqual(report["status"], "accepted")
        self.assertFalse(any(r.get("kind") == "quote" for r in evidence.values()))

    def test_sparse_pages_fall_back_without_guessing(self):
        evidence, report = infer_paragraph_layout(scan_rows()[:6])
        self.assertEqual(evidence, {})
        self.assertEqual(report["status"], "fallback")

    def test_native_and_hanging_layouts_keep_their_existing_roles(self):
        for setting in [dict(text_source="native"), dict(reference_pages=[1])]:
            audit = []
            model = reconstruct(
                {1: scan_rows()},
                dict(
                    chapters=[[1, 1, "Section"]],
                    chapter_body_starts={"1": 0},
                    **setting,
                ),
                JoinPolicy(),
                audit,
            )
            self.assertFalse(any(b["kind"] == "quote" for b in model[0]["blocks"]))
            self.assertFalse(any(a.get("kind") == "paragraph-layout" for a in audit))


if __name__ == "__main__":
    unittest.main()
