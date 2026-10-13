"""Score selected OCR cases or complete source-checked pages and review studies."""

import argparse
from collections import defaultdict
import math
from pathlib import Path
import random
import re
import statistics
import unicodedata

from common import ROOT, file_digest, read_json, write_json


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def normalize(text):
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def edit_distance(a, b):
    """Exact Levenshtein for characters or whitespace-token sequences."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(
                min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return previous[-1]


def detection(rows):
    counts = {name: 0 for name in ("tp", "fp", "tn", "fn")}
    correct_words = 0
    for row in rows:
        error = normalize(row["before"]) != normalize(row["truth"])
        counts[
            (
                ("tp" if row["detected"] else "fn")
                if error
                else ("fp" if row["detected"] else "tn")
            )
        ] += 1
        if not error:
            correct_words += len(normalize(row["truth"]).split())
    tp, fp, tn, fn = (counts[k] for k in ("tp", "fp", "tn", "fn"))
    return dict(
        **counts,
        precision=ratio(tp, tp + fp),
        recall=ratio(tp, tp + fn),
        f1=ratio(2 * tp, 2 * tp + fp + fn),
        f0_5=ratio(1.25 * tp, 1.25 * tp + 0.25 * fn + fp),
        false_positive_rate=ratio(fp, fp + tn),
        correct_words=correct_words,
        false_alarm_units_per_1000_correct_words=ratio(1000 * fp, correct_words),
    )


def candidates(rows, ks=(1, 3, 5)):
    errors = [r for r in rows if normalize(r["before"]) != normalize(r["truth"])]
    detected = [r for r in errors if r["detected"]]

    def hits(selected, k=None):
        return sum(
            r["detected"]
            and normalize(r["truth"])
            in {
                normalize(c)
                for c in (r["candidates"] if k is None else r["candidates"][:k])
            }
            for r in selected
        )

    return dict(
        errors=len(errors),
        detected_errors=len(detected),
        full_pool_answers=hits(errors),
        full_pool_recall=ratio(hits(errors), len(errors)),
        conditional_full_pool_recall=ratio(hits(detected), len(detected)),
        recall_at_k={
            str(k): dict(
                answers=hits(errors, k),
                recall=ratio(hits(errors, k), len(errors)),
                conditional_recall=ratio(hits(detected, k), len(detected)),
            )
            for k in ks
        },
        mean_proposals_per_detected_error=ratio(
            sum(len(r["candidates"]) for r in detected), len(detected)
        ),
    )


def correction(rows):
    changed = [r for r in rows if normalize(r["output"]) != normalize(r["before"])]
    errors = [r for r in rows if normalize(r["before"]) != normalize(r["truth"])]
    controls = [r for r in rows if normalize(r["before"]) == normalize(r["truth"])]
    resolved = sum(normalize(r["output"]) == normalize(r["truth"]) for r in errors)
    damaged = sum(normalize(r["output"]) != normalize(r["truth"]) for r in controls)
    return dict(
        changed_units=len(changed),
        resolved_error_units=resolved,
        damaged_correct_units=damaged,
        wrong_changes_on_error_units=sum(
            normalize(r["before"]) != normalize(r["truth"])
            and normalize(r["output"]) != normalize(r["truth"])
            for r in changed
        ),
        exact_change_precision=ratio(resolved, len(changed)),
        error_resolution_recall=ratio(resolved, len(errors)),
        correct_unit_damage_rate=ratio(damaged, len(controls)),
        net_resolved_error_units=resolved - damaged,
        unchanged_units=len(rows) - len(changed),
    )


def text_errors(pages):
    totals = dict(
        reference_characters=0,
        reference_words=0,
        before_character_edits=0,
        after_character_edits=0,
        before_word_edits=0,
        after_word_edits=0,
    )
    for page in pages:
        cached = page.get("_text_counts")
        if cached is not None:
            for key in totals:
                totals[key] += cached[key]
            continue
        truth = normalize(page["truth"])
        totals["reference_characters"] += len(truth)
        totals["reference_words"] += len(truth.split())
        for stage, key in (("before", "before"), ("output", "after")):
            observed = normalize(page[stage])
            totals[key + "_character_edits"] += edit_distance(truth, observed)
            totals[key + "_word_edits"] += edit_distance(
                truth.split(), observed.split()
            )
    for unit, denominator in (
        ("character", "reference_characters"),
        ("word", "reference_words"),
    ):
        abbreviation = "cer" if unit == "character" else "wer"
        for stage in ("before", "after"):
            totals[stage + "_" + abbreviation] = ratio(
                totals[stage + "_" + unit + "_edits"], totals[denominator]
            )
        before, after = (totals[k + "_" + unit + "_edits"] for k in ("before", "after"))
        totals[abbreviation + "_relative_error_reduction"] = ratio(
            before - after, before
        )
        totals[abbreviation + "_absolute_improvement"] = ratio(
            before - after, totals[denominator]
        )
    return totals


def ranked_review(rows):
    surfaced = [r for r in rows if r["detected"]]
    if not surfaced or any(
        type(r.get("review_score")) not in (int, float)
        or not math.isfinite(r["review_score"])
        for r in surfaced
    ):
        return dict(
            available=False,
            reason="Need finite review scores for all surfaced units; no labels used to invent ranking",
        )
    ordered = sorted(surfaced, key=lambda r: (-r["review_score"], r["id"]))
    errors = sum(normalize(r["before"]) != normalize(r["truth"]) for r in rows)
    prefixes = [
        dict(
            reviewed=0,
            errors=0,
            false_alarms=0,
            recall=ratio(0, errors),
            precision=None,
        )
    ]
    found = 0
    for n, row in enumerate(ordered, 1):
        found += normalize(row["before"]) != normalize(row["truth"])
        prefixes.append(
            dict(
                reviewed=n,
                errors=found,
                false_alarms=n - found,
                recall=ratio(found, errors),
                precision=found / n,
            )
        )
    return dict(
        available=True,
        order=[r["id"] for r in ordered],
        at_review_budgets={
            str(n): prefixes[min(n, len(ordered))] for n in (10, 25, 50, 100)
        },
        at_false_alarm_budgets={
            str(n): next(p for p in reversed(prefixes) if p["false_alarms"] <= n)
            for n in (0, 1, 5, 10)
        },
        meaning="Queue-prefix budgets on scored source spans; not calibrated probability thresholds",
    )


def benchmark_report(payload):
    rows = []
    seen = set()
    for case in payload["cases"]:
        if (
            case["id"] in seen
            or case["expected"] not in {"correct", "retain"}
            or type(case["detected"]) is not bool
        ):
            raise ValueError("Invalid or duplicate benchmark case")
        seen.add(case["id"])
        rows.append(
            dict(
                case,
                before=case["before"],
                truth=case["after"],
                candidates=list(dict.fromkeys(p["term"] for p in case["suggestions"])),
            )
        )

    def score(selected):
        return dict(
            case_control_diagnostic=detection(selected),
            candidate_availability=candidates(selected),
            review_budgets=ranked_review(selected),
        )

    generator_hits = [
        {g for g, terms in row.get("generators", {}).items() if row["truth"] in terms}
        for row in rows
        if row["detected"] and normalize(row["before"]) != normalize(row["truth"])
    ]
    return dict(
        mode="selected_case_diagnostic",
        scope=payload["purpose"],
        warning="Selected errors and counterfactual correct controls; precision/F1/false alarms below are sample diagnostics, not population estimates. Context reranking omitted in input: Recall@k reflects stored generator order, not deployed contextual ranking.",
        summary=score(rows),
        by_book={
            b: score([r for r in rows if r["book"] == b])
            for b in sorted({r["book"] for r in rows})
        },
        representative_detection_metrics=None,
        confidence_intervals=None,
        correction_metrics=None,
        page_cer_wer=None,
        review_efficiency=None,
        unavailable_reason="Need complete source-checked natural pages, actual correction outputs and independently scored timed sessions",
        generator_contributions={
            generator: dict(
                answers=sum(generator in hits for hits in generator_hits),
                unique_answers=sum(hits == {generator} for hits in generator_hits),
            )
            for generator in sorted({g for hits in generator_hits for g in hits})
        },
    )


def validate_annotations(payload):
    if (
        payload.get("format_version") != 1
        or payload.get("normalization") != "NFC"
        or payload.get("unit") != "source_span"
    ):
        raise ValueError("Unsupported annotation format, normalization or unit")
    if payload.get("sampling") not in {"random_complete_pages", "challenge"}:
        raise ValueError("Declare random_complete_pages or challenge sampling")
    if payload.get("output_kind") not in {"automatic", "reviewed", "pipeline"}:
        raise ValueError("Declare which output stage is evaluated")
    if (
        not isinstance(payload.get("transcription_policy"), str)
        or not payload["transcription_policy"].strip()
    ):
        raise ValueError("Declare the literal transcription policy")
    if not isinstance(payload.get("pages"), list) or not payload["pages"]:
        raise ValueError("Annotated pages required")
    identities, ids, sources, splits = set(), set(), {}, {}
    pages = []
    for page in payload["pages"]:
        if (
            type(page.get("page")) != int
            or page["page"] < 1
            or not isinstance(page.get("book"), str)
            or not page["book"]
        ):
            raise ValueError("Invalid book/page identity")
        key = page["book"], page["page"]
        if key in identities:
            raise ValueError("Duplicate annotated page")
        identities.add(key)
        if not isinstance(page.get("source_sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", page["source_sha256"]
        ):
            raise ValueError("Source SHA-256 required")
        if page.get("source_checked") is not True or page.get("split") not in {
            "calibration",
            "test",
        }:
            raise ValueError("Pages need independent source checking and a split")
        if any(not isinstance(page.get(k), str) for k in ("before", "truth", "output")):
            raise ValueError("Page before/truth/output text required")
        book = page["book"]
        if (
            sources.setdefault(book, page["source_sha256"]) != page["source_sha256"]
            or splits.setdefault(book, page["split"]) != page["split"]
        ):
            raise ValueError(
                "A book cannot change source or cross calibration/test splits"
            )
        tokens = list(re.finditer(r"\S+", page["before"]))
        starts, ends = {m.start() for m in tokens}, {m.end() for m in tokens}
        previous = 0
        rows = []
        if not isinstance(page.get("units"), list):
            raise ValueError("Complete ordered source-span units required")
        for unit in page["units"]:
            if (
                not isinstance(unit.get("id"), str)
                or not unit["id"]
                or unit["id"] in ids
            ):
                raise ValueError("Globally unique unit IDs required")
            ids.add(unit["id"])
            start, end = unit.get("start"), unit.get("end")
            if (
                type(start) != int
                or type(end) != int
                or not previous <= start <= end <= len(page["before"])
            ):
                raise ValueError("Invalid, overlapping or unordered source spans")
            if page["before"][previous:start].strip() or (
                start != end and (start not in starts or end not in ends)
            ):
                raise ValueError(
                    "Units must cover all source tokens with complete boundaries"
                )
            if start == end and unit.get("missing_source_text") is not True:
                raise ValueError("Zero-length spans require missing_source_text")
            if (
                any(not isinstance(unit.get(k), str) for k in ("truth", "output"))
                or type(unit.get("detected")) != bool
            ):
                raise ValueError("Unit truth/output and boolean detection required")
            proposals = unit.get("candidates")
            if (
                not isinstance(proposals, list)
                or any(not isinstance(c, str) or not c.strip() for c in proposals)
                or len(proposals) != len(set(proposals))
            ):
                raise ValueError("Unique ordered candidate strings required")
            if not unit["detected"] and proposals:
                raise ValueError("Undetected units cannot expose candidates")
            if "review_score" in unit and (
                type(unit["review_score"]) not in (int, float)
                or not math.isfinite(unit["review_score"])
            ):
                raise ValueError("Review scores must be finite numbers")
            rows.append(
                dict(
                    unit, book=book, page=page["page"], before=page["before"][start:end]
                )
            )
            previous = end
        if page["before"][previous:].strip():
            raise ValueError("Unannotated source text remains")
        for field in ("truth", "output"):
            if normalize(page[field]).split() != [
                w for row in rows for w in normalize(row[field]).split()
            ]:
                raise ValueError("Unit truth/output must agree with full page text")
        # Edit alignment runs once per page, not again in every bootstrap draw.
        raw = {k: v for k, v in page.items() if k != "_text_counts"}
        pages.append(dict(raw, rows=rows, _text_counts=text_errors([raw])))
    return pages


def page_score(pages):
    rows = [r for p in pages for r in p["rows"]]
    return dict(
        detection=detection(rows),
        candidates=candidates(rows),
        correction=correction(rows),
        text_errors=text_errors(pages),
        review_budgets=ranked_review(rows),
    )


def bootstrap(pages, replicates=1000, seed=0):
    """Paired hierarchy: resample books, then pages within each selected book."""
    grouped = defaultdict(list)
    for p in pages:
        grouped[p["book"]].append(p)
    if len(grouped) < 2 or replicates < 100:
        return dict(
            available=False,
            reason="Need at least two books and 100 resamples; few books still limit inference",
        )
    rng = random.Random(seed)
    names = sorted(grouped)
    values = defaultdict(list)
    for _ in range(replicates):
        selected = []
        for book in rng.choices(names, k=len(names)):
            own = grouped[book]
            selected.extend(rng.choices(own, k=len(own)))
        scored = page_score(selected)
        for section, keys in {
            "detection": (
                "precision",
                "recall",
                "f1",
                "false_alarm_units_per_1000_correct_words",
            ),
            "candidates": ("full_pool_recall",),
            "correction": ("exact_change_precision", "correct_unit_damage_rate"),
            "text_errors": (
                "before_cer",
                "after_cer",
                "before_wer",
                "after_wer",
                "cer_absolute_improvement",
                "wer_absolute_improvement",
            ),
        }.items():
            for key in keys:
                value = scored[section][key]
                if value is not None:
                    values[section + "." + key].append(value)
    intervals = {}
    for key, observations in sorted(values.items()):
        observations.sort()
        n = len(observations)
        intervals[key] = dict(
            low=observations[int((n - 1) * 0.025)],
            high=observations[math.ceil((n - 1) * 0.975)],
            defined_resamples=n,
        )
    return dict(
        available=True,
        method="95% percentile book/page hierarchical bootstrap",
        replicates=replicates,
        seed=seed,
        caution="Conditional on labeled books and declared sampling; few books give unstable intervals; undefined resamples are disclosed",
        intervals=intervals,
    )


def annotations_report(payload, replicates=1000):
    pages = validate_annotations(payload)
    selected = [p for p in pages if p["split"] == "test"]
    if not selected:
        raise ValueError("Held-out test pages required")
    return dict(
        mode="complete_source_checked_pages",
        sampling=payload["sampling"],
        population_estimates_supported=payload["sampling"] == "random_complete_pages",
        sampling_warning="Eligibility relies on declared sampling and source checking; this evaluator does not verify random selection or inspect source images. Challenge sets are not population samples.",
        transcription_policy=payload["transcription_policy"],
        normalization="NFC; case and literal glyph distinctions preserved; CRLF/CR normalized to LF",
        tokenization="Whitespace tokens for WER; declared source spans for detection",
        output_kind=payload["output_kind"],
        summary=page_score(selected),
        by_book={
            b: page_score([p for p in selected if p["book"] == b])
            for b in sorted({p["book"] for p in selected})
        },
        confidence_intervals=(
            bootstrap(selected, replicates)
            if payload["sampling"] == "random_complete_pages"
            else None
        ),
        calibration_pages_excluded=sum(p["split"] == "calibration" for p in pages),
        review_efficiency=None,
    )


def review_study(study, pages, dataset_hash):
    if study.get("format_version") != 1 or study.get("dataset_sha256") != dataset_hash:
        raise ValueError("Review study must bind to the exact annotation file")
    gold = {r["id"]: r for p in pages if p["split"] == "test" for r in p["rows"]}
    sessions = []
    seen = set()
    if not isinstance(study.get("sessions"), list) or not study["sessions"]:
        raise ValueError("Timed review sessions required")
    for session in study["sessions"]:
        if (
            not isinstance(session.get("id"), str)
            or not session["id"]
            or session["id"] in seen
        ):
            raise ValueError("Unique session IDs required")
        seen.add(session["id"])
        for field in ("reviewer", "strategy"):
            if not isinstance(session.get(field), str) or not session[field]:
                raise ValueError("Reviewer and strategy required")
        elapsed = session.get("elapsed_ms")
        if (
            type(elapsed) not in (int, float)
            or not math.isfinite(elapsed)
            or elapsed <= 0
        ):
            raise ValueError(
                "Positive total elapsed review time required; active-only timers are insufficient"
            )
        resolved = wrong = correct = abstained = 0
        ids = set()
        if not isinstance(session.get("decisions"), list):
            raise ValueError("Session decisions required")
        for decision in session["decisions"]:
            ident = decision.get("id")
            if ident not in gold or ident in ids:
                raise ValueError("Unknown, calibration or duplicate review unit")
            ids.add(ident)
            replacement = decision.get("replacement")
            if replacement is None:
                abstained += 1
                continue
            if not isinstance(replacement, str):
                raise ValueError("Replacement must be text or null for abstention")
            row = gold[ident]
            right = normalize(replacement) == normalize(row["truth"])
            correct += right
            wrong += not right
            resolved += right and normalize(row["before"]) != normalize(row["truth"])
        sessions.append(
            dict(
                id=session["id"],
                reviewer=session["reviewer"],
                strategy=session["strategy"],
                elapsed_ms=elapsed,
                reviewed_units=len(ids),
                correct_decisions=correct,
                wrong_decisions=wrong,
                abstentions=abstained,
                resolved_errors=resolved,
                correct_errors_per_minute=resolved * 60000 / elapsed,
            )
        )
    return dict(
        sessions=sessions,
        by_strategy={
            s: dict(
                sessions=sum(r["strategy"] == s for r in sessions),
                median_correct_errors_per_minute=statistics.median(
                    r["correct_errors_per_minute"]
                    for r in sessions
                    if r["strategy"] == s
                ),
                wrong_decisions=sum(
                    r["wrong_decisions"] for r in sessions if r["strategy"] == s
                ),
            )
            for s in sorted({r["strategy"] for r in sessions})
        },
        warning="Session outcomes scored against source-checked gold; experimental counterbalancing/matched assignments remain protocol requirements, not inferred from timing",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--benchmark", type=Path)
    inputs.add_argument("--annotations", type=Path)
    parser.add_argument("--review-study", type=Path)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "work/evaluation-metrics/report.json"
    )
    args = parser.parse_args()
    source = (
        args.annotations
        or args.benchmark
        or ROOT / "docs/production-candidate-evaluation.json"
    )
    payload = read_json(source)
    if args.review_study and not args.annotations:
        parser.error("Review studies require source-checked annotations")
    report = (
        annotations_report(payload, args.bootstrap)
        if args.annotations
        else benchmark_report(payload)
    )
    report.update(
        format_version=1,
        input_sha256=file_digest(source),
        helper_sha256=file_digest(Path(__file__)),
        common_sha256=file_digest(ROOT / "scripts/common.py"),
    )
    if args.review_study:
        report["review_efficiency"] = review_study(
            read_json(args.review_study),
            validate_annotations(payload),
            file_digest(source),
        )
        report["review_study_sha256"] = file_digest(args.review_study)
    write_json(args.output, report)
    print(report["summary"])


if __name__ == "__main__":
    main()
