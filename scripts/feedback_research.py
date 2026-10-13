"""Forward-page evaluation of the advisory ledger using simulated review exports."""

import argparse
from copy import deepcopy
from pathlib import Path

from common import ROOT, digest, file_digest, read_json, write_json
from review_feedback import FeedbackLedger
from simple_ocr_research import verify_frozen
from statistical_ocr_research import load_books
from vocabulary import TOKEN


def simulate_ledger(seeds, source, profile, kind):
    """Oracle seed labels, never held-out targets; controls are a separate universe."""
    pages, decisions = {}, []
    for case in seeds:
        text = case["context"]
        matches = [
            m for m in TOKEN.finditer(text) if m.group().lower() == case["before"]
        ]
        if len(matches) != 1:
            raise ValueError("Ambiguous simulated review occurrence")
        match = matches[0]
        row = dict(page=case["page"], row_id=case["id"], text=text, bbox=case["bbox"])
        pages.setdefault(case["page"], []).append(row)
        decisions.append(
            dict(
                id=case["id"],
                reviewed=True,
                decision=kind,
                replacement_or_note=case["after"],
                binding=dict(
                    page=case["page"],
                    row_id=case["id"],
                    bbox=case["bbox"],
                    before=match.group(),
                    start=match.start(),
                    end=match.end(),
                    row_text_sha256=digest(text.encode()),
                ),
            )
        )
    payload = dict(
        format_version=2,
        source_sha256=source,
        profile_sha256=profile,
        model_sha256="simulated-source-row-fixtures",
        decisions=decisions,
    )
    return FeedbackLedger(
        payload, pages, source, "simulated-source-row-fixtures", profile
    )


def evaluate(frozen, baseline):
    original = {c["id"]: c for c in frozen["cases"]}
    if baseline["input_sha256"] != file_digest(
        ROOT / "docs/statistical-ocr-evaluation.json"
    ):
        raise ValueError("Production baseline uses different frozen inputs")
    if set(original) != {c["id"] for c in baseline["cases"]}:
        raise ValueError("Production baseline case inventory changed")
    rows, splits = [], []
    for book in sorted(frozen["books"]):
        own = [c for c in frozen["cases"] if c["book"] == book]
        pages = sorted({c["page"] for c in own})
        seed_pages = pages[: max(1, (len(pages) + 4) // 5)]
        meta = frozen["books"][book]
        if baseline["books"][book]["frozen_book"] != meta:
            raise ValueError("Production baseline book resources changed")
        corrected = [
            c for c in own if c["page"] in seed_pages and c["expected"] == "correct"
        ]
        kept = [c for c in own if c["page"] in seed_pages and c["expected"] == "retain"]
        correction_ledger = simulate_ledger(
            corrected, meta["source_sha256"], meta["profile_sha256"], "ocr-error"
        )
        keep_ledger = simulate_ledger(
            kept, meta["source_sha256"], meta["profile_sha256"], "keep"
        )
        splits.append(
            dict(
                book=book,
                seed_pages=seed_pages,
                correction_seed_ids=[c["id"] for c in corrected],
                counterfactual_keep_seed_ids=[c["id"] for c in kept],
            )
        )
        for base in [
            r
            for r in baseline["cases"]
            if r["book"] == book and r["page"] not in seed_pages
        ]:
            case = original[base["id"]]
            for key in ("book", "page", "before", "after", "expected"):
                if base[key] != case[key]:
                    raise ValueError("Production baseline case evidence changed")
            occurrence = dict(
                original=case["before"], sources=[dict(page=case["page"])]
            )
            item = dict(
                word=case["before"],
                occurrences=[occurrence],
                suggestions=deepcopy(base["suggestions"]),
                review_priority=1,
            )
            if base["detected"]:
                correction_ledger.annotate(item)
                keep_ledger.annotate(item)
            added = [
                p
                for p in item["suggestions"]
                if "confirmed-book-feedback" in p.get("generators", [])
            ]
            rows.append(
                dict(
                    id=case["id"],
                    expected=case["expected"],
                    detected=base["detected"],
                    baseline_answer=base["candidate_recall"],
                    additional_answer=not base["candidate_recall"]
                    and case["after"] in {p["term"] for p in added},
                    feedback_suggestions=added,
                    unchanged_feedback=item.get("unchanged_feedback"),
                    deprioritized=item["review_priority"] == 3,
                )
            )
    errors = [r for r in rows if r["expected"] == "correct"]
    controls = [r for r in rows if r["expected"] == "retain"]
    return dict(
        purpose="Oracle earlier-page corrections and separate counterfactual keep feedback; advisory only",
        seed_policy="First 20 percent of annotated pages, not book pages or all tokens",
        summary=dict(
            held_errors=len(errors),
            baseline_answers=sum(r["baseline_answer"] for r in errors),
            additional_answers=sum(r["additional_answer"] for r in errors),
            errors_with_feedback_suggestions=sum(
                bool(r["feedback_suggestions"]) for r in errors
            ),
            held_controls=len(controls),
            controls_with_feedback_suggestions=sum(
                bool(r["feedback_suggestions"]) for r in controls
            ),
            deprioritized_errors=sum(r["deprioritized"] for r in errors),
            deprioritized_controls=sum(r["deprioritized"] for r in controls),
        ),
        splits=splits,
        cases=rows,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "work/feedback-research/evaluation.json"
    )
    args = parser.parse_args()
    frozen_path = ROOT / "docs/statistical-ocr-evaluation.json"
    baseline_path = ROOT / "docs/production-candidate-evaluation.json"
    frozen = read_json(frozen_path)
    books, cases, _ = load_books()
    verify_frozen(books, cases, frozen)
    result = evaluate(frozen, read_json(baseline_path))
    result.update(
        input_sha256=file_digest(frozen_path),
        baseline_sha256=file_digest(baseline_path),
        helpers_sha256={
            name: file_digest(ROOT / "scripts" / name)
            for name in (
                "feedback_research.py",
                "review_feedback.py",
                "review_edits.py",
                "candidates.py",
            )
        },
    )
    write_json(args.output, result)
    print(result["summary"])


if __name__ == "__main__":
    main()
