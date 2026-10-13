"""Hand-calculated metrics and controls against invalid evaluation claims."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluation_metrics import (
    annotations_report,
    benchmark_report,
    bootstrap,
    edit_distance,
    normalize,
    page_score,
    ranked_review,
    review_study,
    validate_annotations,
)


def annotation(book="one", page=1):
    return dict(
        format_version=1,
        normalization="NFC",
        unit="source_span",
        sampling="random_complete_pages",
        output_kind="automatic",
        transcription_policy="Literal print, including typos",
        pages=[
            dict(
                book=book,
                page=page,
                source_sha256="a" * 64,
                source_checked=True,
                split="test",
                before="bvrn modem good",
                truth="burn modern good",
                output="burn modem bad",
                units=[
                    dict(
                        id=f"{book}-a",
                        start=0,
                        end=4,
                        truth="burn",
                        output="burn",
                        detected=True,
                        candidates=["barn", "burn"],
                        review_score=2,
                    ),
                    dict(
                        id=f"{book}-b",
                        start=5,
                        end=10,
                        truth="modern",
                        output="modem",
                        detected=False,
                        candidates=[],
                    ),
                    dict(
                        id=f"{book}-c",
                        start=11,
                        end=15,
                        truth="good",
                        output="bad",
                        detected=True,
                        candidates=["bad"],
                        review_score=1,
                    ),
                ],
            )
        ],
    )


class EvaluationMetricsTests(unittest.TestCase):
    def test_hand_computed_confusion_candidate_and_damage_scores(self):
        report = annotations_report(annotation())
        d = report["summary"]["detection"]
        self.assertEqual([d[k] for k in ("tp", "fp", "tn", "fn")], [1, 1, 0, 1])
        self.assertEqual(d["precision"], 0.5)
        self.assertEqual(d["recall"], 0.5)
        self.assertEqual(d["f1"], 0.5)
        c = report["summary"]["candidates"]
        self.assertEqual(c["full_pool_recall"], 0.5)
        self.assertEqual(c["conditional_full_pool_recall"], 1)
        self.assertEqual(c["recall_at_k"]["1"]["recall"], 0)
        correction = report["summary"]["correction"]
        self.assertEqual(correction["resolved_error_units"], 1)
        self.assertEqual(correction["damaged_correct_units"], 1)
        self.assertEqual(correction["exact_change_precision"], 0.5)
        self.assertEqual(correction["net_resolved_error_units"], 0)

    def test_exact_full_page_cer_and_wer_can_expose_net_damage(self):
        values = annotations_report(annotation())["summary"]["text_errors"]
        self.assertEqual(values["reference_characters"], 16)
        self.assertEqual(values["before_character_edits"], 3)
        self.assertEqual(values["after_character_edits"], 5)
        self.assertEqual(values["before_cer"], 3 / 16)
        self.assertEqual(values["after_wer"], 2 / 3)
        self.assertLess(values["cer_absolute_improvement"], 0)

    def test_canonical_unicode_is_equivalent_but_accents_and_case_remain(self):
        self.assertEqual(normalize("cafe\u0301"), "café")
        self.assertEqual(edit_distance("", "word"), 4)
        self.assertEqual(edit_distance(["oflife"], ["of", "life"]), 2)
        self.assertNotEqual(normalize("café"), normalize("cafe"))
        self.assertNotEqual(normalize("Name"), normalize("name"))

    def test_coverage_omissions_partial_tokens_and_page_disagreement_fail(self):
        for change in ("omit", "partial", "truth", "duplicate"):
            data = annotation()
            page = data["pages"][0]
            if change == "omit":
                page["units"].pop()
            if change == "partial":
                page["units"][0]["end"] = 3
            if change == "truth":
                page["truth"] = "different text"
            if change == "duplicate":
                page["units"][1]["id"] = page["units"][0]["id"]
            with self.assertRaises(ValueError):
                validate_annotations(data)

    def test_unchecked_source_and_book_split_leakage_fail(self):
        data = annotation()
        data["pages"][0]["source_checked"] = False
        with self.assertRaises(ValueError):
            validate_annotations(data)
        data = annotation()
        second = deepcopy(data["pages"][0])
        second.update(page=2, split="calibration")
        data["pages"].append(second)
        with self.assertRaisesRegex(ValueError, "cross calibration"):
            validate_annotations(data)

    def test_challenge_data_never_claim_population_metrics_or_intervals(self):
        data = annotation()
        data["sampling"] = "challenge"
        report = annotations_report(data)
        self.assertFalse(report["population_estimates_supported"])
        self.assertIsNone(report["confidence_intervals"])

    def test_undefined_denominators_are_null(self):
        data = annotation()
        page = data["pages"][0]
        page["truth"] = page["before"]
        page["output"] = page["before"]
        for row in page["units"]:
            row.update(
                truth=page["before"][row["start"] : row["end"]],
                output=page["before"][row["start"] : row["end"]],
                detected=False,
                candidates=[],
            )
        report = annotations_report(data)["summary"]
        self.assertIsNone(report["detection"]["precision"])
        self.assertIsNone(report["detection"]["recall"])
        self.assertIsNone(report["correction"]["exact_change_precision"])
        self.assertIsNone(report["text_errors"]["cer_relative_error_reduction"])

    def test_cluster_bootstrap_is_deterministic_and_does_not_realign_text(self):
        data = annotation()
        data["pages"] += annotation("two")["pages"]
        pages = validate_annotations(data)
        with patch(
            "evaluation_metrics.edit_distance",
            side_effect=AssertionError("Repeated alignment"),
        ):
            first = bootstrap(pages, 100, seed=3)
            self.assertEqual(first, bootstrap(pages, 100, seed=3))
        self.assertTrue(first["available"])
        self.assertEqual(first["intervals"]["detection.f1"]["defined_resamples"], 100)

    def test_review_budget_order_uses_scores_not_truth(self):
        rows = validate_annotations(annotation())[0]["rows"]
        report = ranked_review(rows)
        self.assertEqual(report["at_false_alarm_budgets"]["0"]["errors"], 1)
        self.assertEqual(report["at_false_alarm_budgets"]["0"]["recall"], 0.5)
        altered = deepcopy(rows)
        for r in altered:
            r["truth"] = r["before"]
        self.assertEqual(report["order"], ranked_review(altered)["order"])
        altered[0].pop("review_score")
        self.assertFalse(ranked_review(altered)["available"])

    def test_review_sessions_use_gold_and_total_time_not_self_selected_labels(self):
        pages = validate_annotations(annotation())
        study = dict(
            format_version=1,
            dataset_sha256="dataset",
            sessions=[
                dict(
                    id="s",
                    reviewer="r",
                    strategy="current",
                    elapsed_ms=120000,
                    decisions=[
                        dict(id="one-a", replacement="burn"),
                        dict(id="one-c", replacement="bad"),
                        dict(id="one-b", replacement=None),
                    ],
                )
            ],
        )
        report = review_study(study, pages, "dataset")["sessions"][0]
        self.assertEqual(report["resolved_errors"], 1)
        self.assertEqual(report["correct_errors_per_minute"], 0.5)
        self.assertEqual(report["wrong_decisions"], 1)
        self.assertEqual(report["abstentions"], 1)
        with self.assertRaises(ValueError):
            review_study(study, pages, "stale")
        study["sessions"][0]["elapsed_ms"] = 0
        with self.assertRaises(ValueError):
            review_study(study, pages, "dataset")

    def test_synthetic_benchmark_metrics_remain_diagnostics(self):
        payload = dict(
            purpose="selected",
            cases=[
                dict(
                    id="a",
                    book="b",
                    before="bvrn",
                    after="burn",
                    expected="correct",
                    detected=True,
                    suggestions=[dict(term="burn")],
                    generators={"book": ["burn"], "dictionary": ["barn"]},
                )
            ],
        )
        report = benchmark_report(payload)
        self.assertEqual(report["summary"]["case_control_diagnostic"]["f1"], 1)
        self.assertIsNone(report["representative_detection_metrics"])
        self.assertIsNone(report["page_cer_wer"])
        self.assertIsNone(report["review_efficiency"])
        self.assertEqual(
            report["generator_contributions"],
            {"book": {"answers": 1, "unique_answers": 1}},
        )

    def test_calibration_books_are_not_scored(self):
        data = annotation()
        calibration = annotation("train")["pages"][0]
        calibration["split"] = "calibration"
        data["pages"].append(calibration)
        report = annotations_report(data)
        self.assertEqual(report["calibration_pages_excluded"], 1)
        self.assertEqual(list(report["by_book"]), ["one"])


if __name__ == "__main__":
    unittest.main()
