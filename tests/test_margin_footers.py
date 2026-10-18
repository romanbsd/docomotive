"""Source-position evidence separates printer matter from low body text."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from layout import infer, footer_leakage
from book_model import classify_row


def row(text, x0=0.1, x1=0.94, y=0.95):
    return dict(text=text, bbox=[x0, y, x1, y + 0.014], column=0)


class MarginFooterTests(unittest.TestCase):
    def pages(self):
        return {
            n: [
                row("Ordinary prose and legitimate footnotes remain here.", y=0.9),
                row(str(n - 8), x0=0.91),
            ]
            for n in range(9, 21)
        }

    def test_composite_folios_need_sequence_band_and_horizontal_extent(self):
        for text in ("4* 7", "4∗7", "4 7", "4% 7", "• 7"):
            with self.subTest(text=text):
                pages = self.pages()
                candidate = row(text, x0=0.9 if text.startswith("•") else 0.18)
                pages[15].append(candidate)
                model = infer(pages)
                found = model["footer_analysis"]["accepted"]
                self.assertEqual([(r["page"], r["text"]) for r in found], [(15, text)])
                book = dict(_layout=dict(excluded_rows=model["footers"]))
                self.assertEqual(classify_row(15, candidate, book)[0], "excluded")
                self.assertEqual(classify_row(15, pages[15][0], book)[0], "body")

    def test_low_citations_math_and_wrong_page_or_edge_are_preserved(self):
        cases = [
            row("4* 8"),
            row("4*7", x0=0.8),
            row("4% 7", x0=0.8),
            row("4 7", x0=0.8),
            row("4* 7", y=0.87),
            row("4* 7", x1=0.7),
            row("4* 7 The numbered note is prose."),
            row("7–9"),
            row("(7)"),
            row("A. Writer. Page 7."),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                pages = self.pages()
                pages[15].append(candidate)
                model = infer(pages)
                self.assertEqual(model["footer_analysis"]["accepted"], [])
                self.assertFalse(
                    any(r["text"] == candidate["text"] for r in model["footers"])
                )
        # A plausible but mispositioned candidate stays visible for review.
        pages = self.pages()
        pages[15].append(row("4* 7", y=0.87))
        self.assertEqual(len(infer(pages)["footer_analysis"]["review"]), 1)

    def test_sparse_or_single_page_consensus_cannot_authorize_composites(self):
        for pages in (
            {15: [row("7")] * 6 + [row("4* 7")]},
            {n: [row(str(n - 8))] for n in range(9, 13)},
        ):
            model = infer(pages)
            self.assertEqual(model["footer_analysis"]["accepted"], [])

    def test_recurring_printer_family_handles_damaged_initials_without_whitelists(self):
        pages = self.pages()
        for n in (9, 11, 13):
            pages[n].append(row(f"2 A. B. Longname {n - 8}"))
        pages[15].append(row("3 A. D. Longname 7"))
        pages[16].append(row("9.1. 11. Longname 8"))
        pages[17].append(row("4 A. B. Longname", x1=0.35))
        pages[19].append(row("5 A. B. Othername 11"))
        before = copy.deepcopy(pages)
        model = infer(pages)
        accepted = model["footer_analysis"]["accepted"]
        self.assertEqual([r["page"] for r in accepted], [9, 11, 13, 15, 16, 17])
        self.assertEqual(
            model["footer_analysis"]["printer_label_families"],
            {"A B LONGNAME": [9, 11, 13]},
        )
        self.assertEqual(pages, before)

    def test_ambiguous_damaged_label_cannot_choose_between_printer_families(self):
        pages = self.pages()
        for n in (9, 11, 13):
            pages[n].append(row(f"2 A. B. Longname {n - 8}"))
        for n in (10, 12, 14):
            pages[n].append(row(f"2 A. C. Longname {n - 8}"))
        pages[15].append(row("3 A. D. Longname 7"))
        accepted = infer(pages)["footer_analysis"]["accepted"]
        self.assertEqual(sorted(r["page"] for r in accepted), list(range(9, 15)))

    def test_unconfirmed_name_and_wrong_folio_do_not_become_family_evidence(self):
        pages = self.pages()
        for n in (9, 11):
            pages[n].append(row(f"2 A. B. Longname {n - 8}"))
        pages[13].append(row("2 A. B. Longname 99"))
        self.assertEqual(infer(pages)["footer_analysis"]["accepted"], [])
        pages[13][-1]["text"] = "2 A. B. Longname 5"
        pages[15].append(row("3 A. B. Longname 99"))
        self.assertEqual(
            [r["page"] for r in infer(pages)["footer_analysis"]["accepted"]],
            [9, 11, 13],
        )

    def test_repeated_numeric_rows_on_one_page_cannot_outvote_page_sequence(self):
        pages = self.pages()
        pages[15].extend([row("100")] * 30)
        pages[15].append(row("4* 7"))
        model = infer(pages)
        self.assertEqual(model["printed_page_offset"], -8)
        self.assertEqual(len(model["footer_analysis"]["accepted"]), 1)

    def test_leakage_gate_uses_source_geometry_even_after_text_edits(self):
        footer = dict(page=15, **row("4* 7"))
        layout = dict(footers=[footer])
        source = dict(footer, text="4*7")
        for location in ("blocks", "notes"):
            model = [dict(blocks=[], notes=[])]
            model[0][location].append(dict(sources=[source]))
            self.assertEqual(footer_leakage(model, layout)[0]["rendered_text"], "4*7")
        source["bbox"] = [0.1, 0.8, 0.94, 0.814]
        self.assertEqual(footer_leakage(model, layout), [])


if __name__ == "__main__":
    unittest.main()
