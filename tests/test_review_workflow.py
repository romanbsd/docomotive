"""Review proposals must remain individually bound and require source checking."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import apply_edits, digest, file_digest
from proofread import make_sheet
from candidates import merge_suggestions, merge_review_items
from review_candidates import (
    ReviewWitnesses,
    anchored_changes,
    map_span,
    occurrence_binding,
)
from review_edits import checked_overlays
from review_sheet import prepare, render


def row(text, page=1, ident="row"):
    return dict(
        text=text, page=page, row_id=ident, bbox=[0.1, 0.2, 0.9, 0.3], inline=[]
    )


def occurrence(text, token, page=1, ident="row", start=None):
    offset = text.index(token) if start is None else start
    return dict(
        context=text,
        original=token,
        offset=offset,
        chapter="one",
        paragraph=0,
        sources=[dict(row(text, page, ident), start=0, end=len(text))],
    )


def model(text, page=1):
    r = dict(row(text, page, str(page)), start=0, end=len(text))
    return [
        dict(
            chapter="one",
            blocks=[dict(kind="paragraph", text=text, sources=[r])],
            notes=[],
        )
    ]


class WitnessTests(unittest.TestCase):
    def test_aligned_words_and_spacing_require_two_anchors(self):
        self.assertTrue(
            anchored_changes("old modem science today", "old modern science today")
        )
        self.assertTrue(
            anchored_changes("the oflife story today", "the of life story today")
        )
        self.assertFalse(anchored_changes("modem science", "modern science"))
        self.assertFalse(
            anchored_changes("old Science grows today", "old science grows today")
        )
        self.assertFalse(
            anchored_changes(
                "old co-operate science today", "old cooperate science today"
            )
        )

    def test_mapping_abstains_on_ambiguous_repeat_and_wrap(self):
        self.assertEqual(map_span("one bad bad two", "one bad bad two", 8, 11), (8, 11))
        self.assertIsNone(map_span("one bad bad two", "bad two", 8, 11))
        o = occurrence("one inter national two", "inter national")
        o["sources"] = [
            dict(row("one inter"), start=0, end=9),
            dict(row("national two", ident="r2"), start=10, end=22),
        ]
        self.assertIsNone(occurrence_binding(o))

    def test_book_vocabulary_requires_three_engines_and_two_other_pages(self):
        bank = ReviewWitnesses(Path("/unused"), dict(text_source="native"))
        bank.peers = lambda source, all_engines=False: {
            engine: row(source["text"]) for engine in ["tesseract", "vision", "rapid"]
        }
        bank.collect(
            model("the mycologist studies fungi", 1)
            + model("the mycologist studies fungi", 2)
            + model("the mycologist studies fungi", 3)
        )
        proposals = bank.suggestions(
            "mycoiogist", [occurrence("a mycoiogist works", "mycoiogist", 1)]
        )
        self.assertEqual(proposals[0]["term"], "mycologist")
        self.assertEqual(proposals[0]["attested_pages"], [2, 3])
        self.assertEqual(
            bank.suggestions(
                "mycoiogist",
                [occurrence("a mycoiogist works", "mycoiogist", p) for p in [1, 2]],
            ),
            [],
        )
        other = ReviewWitnesses(Path("/unused"), dict(text_source="native"))
        other.peers = lambda source, all_engines=False: {
            "vision": row(source["text"]),
            "rapid": row(source["text"]),
        }
        other.collect(
            model("the mycologist studies fungi", 1)
            + model("the mycologist studies fungi", 2)
        )
        self.assertEqual(other.sym.words, {})

    def test_two_non_primary_engines_flag_real_word_without_editing(self):
        bank = ReviewWitnesses(Path("/unused"), dict(text_source="native"))
        bank.peers = lambda source: {
            "vision": row("old modern science today"),
            "rapid": row("old modern science today"),
        }
        m = model("old modem science today")
        before = deepcopy(m)
        item = bank.disagreements(m)[0]
        self.assertEqual(item["occurrences"][0]["witness_proposal"], "modern")
        self.assertEqual(m, before)
        bank.peers = lambda source: {
            "vision": row("old modern science today"),
            "rapid": row("old modem science today"),
        }
        self.assertEqual(bank.disagreements(m), [])

    def test_foreign_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "tesseract-cache.txt").write_text(str(p))
            (p / "provenance.json").write_text(json.dumps({"source_sha256": "foreign"}))
            with self.assertRaisesRegex(ValueError, "source mismatch"):
                ReviewWitnesses(p, {"source_sha256": "expected"})

    def test_candidate_merging_preserves_evidence_and_occurrence_exceptions(self):
        suggestions = [dict(term="modern", distance=1, context_score=3)]
        merge_suggestions(
            suggestions,
            [
                dict(
                    term="modern",
                    generators=["witnessed-book-vocabulary"],
                    attested_pages=[2, 3],
                )
            ],
        )
        merge_suggestions(
            suggestions,
            [dict(term="modern", generators=["aligned-ocr-witnesses"])],
            front=True,
        )
        self.assertEqual(
            suggestions[0]["generators"],
            ["aligned-ocr-witnesses", "general-lexicon", "witnessed-book-vocabulary"],
        )
        self.assertEqual(suggestions[0]["context_score"], 3)
        self.assertEqual(suggestions[0]["attested_pages"], [2, 3])
        first = occurrence("old modem science today", "modem", ident="first")
        second = occurrence("old modem science today", "modem", page=2, ident="second")
        second["paragraph"] = 1
        results = [
            dict(word="modem", occurrences=[first, second], suggestions=suggestions)
        ]
        proposal = deepcopy(first)
        proposal["witness_proposal"] = "modern"
        item = dict(
            word="modem",
            occurrences=[proposal, proposal],
            suggestions=[dict(term="modern", generators=["aligned-ocr-witnesses"])],
        )
        merge_review_items(results, [item])
        self.assertEqual(len(results[0]["occurrences"]), 2)
        self.assertEqual(results[0]["occurrences"][0]["witness_proposal"], "modern")
        self.assertNotIn("witness_proposal", results[0]["occurrences"][1])


class CheckedExportTests(unittest.TestCase):
    def setUp(self):
        self.text = "one bad and bad two"
        self.rows = {"1": [row(self.text)]}
        self.payload = dict(
            format_version=2,
            source_sha256="source",
            model_sha256="model",
            profile_sha256="profile",
            decisions=[],
        )

    def add(self, start, after, decision="ocr-error"):
        o = occurrence(self.text, "bad", start=start)
        self.payload["decisions"].append(
            dict(
                id=str(start),
                binding=occurrence_binding(o),
                decision=decision,
                reviewed=True,
                replacement_or_note=after,
            )
        )

    def export(self, **kwargs):
        return checked_overlays(
            self.payload, self.rows, "source", "model", "profile", **kwargs
        )

    def test_only_checked_occurrences_export_and_rebase_same_line(self):
        self.rows["1"][0]["inline"] = [dict(start=16, end=19, tags=["em"])]
        self.add(4, "excellent")
        self.add(12, "fine")
        original = deepcopy(self.rows)
        edits = self.export()
        output = deepcopy(self.rows["1"])
        apply_edits(output, edits, [], "ocr")
        self.assertEqual(output[0]["text"], "one excellent and fine two")
        self.assertEqual(self.rows, original)
        self.assertEqual(output[0]["inline"], [dict(start=23, end=26, tags=["em"])])
        self.assertEqual(edits[0]["before"], "bad")
        self.assertEqual(edits[0]["prefix"], "one bad and ")

    def test_unserialized_row_ids_use_the_build_identity(self):
        from book_model import row_id

        self.add(4, "good")
        actual = self.rows["1"][0]
        self.payload["decisions"][0]["binding"]["row_id"] = row_id(1, actual, "source")
        actual.pop("row_id")
        self.assertEqual(len(self.export()), 1)
        self.assertNotIn("row_id", actual)

    def test_malformed_exports_cannot_silently_write_empty_overlays(self):
        self.payload.pop("decisions")
        with self.assertRaisesRegex(ValueError, "decisions array"):
            self.export()
        self.payload["decisions"] = [{}]
        with self.assertRaisesRegex(ValueError, "Unsupported review decision"):
            self.export()
        self.payload["decisions"] = []
        self.add(4, "good")
        self.payload["decisions"][0]["replacement_or_note"] = None
        with self.assertRaisesRegex(ValueError, "text replacement"):
            self.export()

    def test_excluded_occurrence_and_editorial_are_separate(self):
        self.add(4, "good")
        self.add(12, "printed", decision="printed-typo")
        self.assertEqual(len(self.export()), 1)
        self.assertEqual(self.export(kind="editorial")[0]["proposal"], "printed")
        self.payload["decisions"][0]["decision"] = "keep"
        self.assertEqual(self.export(), [])

    def test_stale_bindings_and_unchecked_exports_fail(self):
        self.add(4, "good")
        self.payload["decisions"][0]["reviewed"] = False
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            self.export()
        self.payload["decisions"][0]["reviewed"] = True
        self.rows["1"][0]["text"] += "!"
        with self.assertRaisesRegex(ValueError, "changed"):
            self.export()
        self.payload["profile_sha256"] = "old"
        with self.assertRaisesRegex(ValueError, "stale"):
            self.export()

    def test_conflicting_and_overlapping_edits_fail(self):
        self.add(4, "good")
        self.add(4, "wrong")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.export()
        self.payload["decisions"].pop()
        other = deepcopy(self.payload["decisions"][0])
        other["binding"].update(start=4, end=11, before="bad and")
        other["replacement_or_note"] = "fine"
        self.payload["decisions"].append(other)
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.export()

    def test_identical_rows_do_not_allow_accidental_global_replacement(self):
        self.add(4, "good")
        self.rows["1"].append(row(self.text, ident="other"))
        with self.assertRaises(ValueError):
            self.export()


class SheetTests(unittest.TestCase):
    def test_all_occurrences_grouped_with_literal_case_and_safe_payload(self):
        items = []
        for word, after in [("fames", "james"), ("fohn", "john")]:
            items.append(
                dict(
                    word=word,
                    suggestions=[{"term": after}],
                    occurrences=[
                        occurrence(f"one {word} two </script>", word, p, f"{word}{p}")
                        for p in range(1, 5)
                    ],
                )
            )
        records, groups = prepare(items, "source")
        self.assertEqual(len(records), 8)
        self.assertEqual(list(groups), ["f → j"])
        self.assertTrue(all(r["binding"] for r in records))
        payload = dict(items=records, groups=groups, images={})
        sheet = render(payload)
        self.assertEqual(sheet.count("<article id="), 8)
        self.assertNotIn("two </script>", sheet)
        self.assertIn("two \\u003c/script>", sheet)
        self.assertIn("checked-source acknowledgement", sheet)

    def test_native_sheet_renders_every_occurrence_and_source_crop(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            pdf = folder / "source.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((60, 150), "one bad two")
            doc.save(pdf)
            doc.close()
            profile = folder / "book.json"
            profile.write_text("{}")
            m = model("one bad two")
            (folder / "book-model.json").write_text(json.dumps(m))
            item = dict(
                word="bad",
                suggestions=[dict(term="good")],
                occurrences=[
                    occurrence("one bad two", "bad", ident=str(i)) for i in range(4)
                ],
            )
            (folder / "correction-candidates.json").write_text(
                json.dumps(dict(candidates=[item]))
            )
            with patch(
                "proofread.load_profile",
                return_value=dict(source_sha256=file_digest(pdf), text_source="native"),
            ):
                make_sheet(pdf, profile, folder, folder)
            broken = MagicMock()
            broken.__getitem__.return_value.rect = pymupdf.Rect(0, 0, 595, 842)
            broken.__getitem__.return_value.get_pixmap.side_effect = ValueError(
                "broken crop"
            )
            with (
                patch("proofread.open_source", return_value=broken),
                patch(
                    "proofread.load_profile",
                    return_value=dict(
                        source_sha256=file_digest(pdf), text_source="native"
                    ),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "broken crop"):
                    make_sheet(pdf, profile, folder, folder)
            broken.close.assert_called_once()
            data = json.loads((folder / "proofreading-review.json").read_text())
            self.assertEqual(len(data["items"]), 4)
            self.assertEqual(data["format_version"], 2)
            self.assertTrue(
                all(r["evidence"][0]["engines"]["native-pdf"] for r in data["items"])
            )
            sheet = (folder / "proofreading-review.html").read_text()
            self.assertEqual(sheet.count("<article id="), 4)
            self.assertEqual(sheet.count("data:image/jpeg;base64,"), 1)

    def test_group_patterns_share_case_variants_and_deduplicate_occurrences(self):
        from review_sheet import pattern

        self.assertEqual(pattern("Fohn", "John"), pattern("fohn", "john"))
        o = occurrence("one bad two", "bad")
        item = dict(word="bad", suggestions=[dict(term="good")], occurrences=[o, o])
        records, _ = prepare([item], "source")
        self.assertEqual(len(records), 1)

    def test_diagnostics_keep_exact_original_case(self):
        item = dict(
            word="modem",
            suggestions=[dict(term="modern")],
            occurrences=[occurrence("one Modem two", "Modem")],
        )
        records, _ = prepare([item], "source")
        self.assertEqual(records[0]["proposal"], "Modern")
        self.assertEqual(records[0]["binding"]["before"], "Modem")


if __name__ == "__main__":
    unittest.main()
