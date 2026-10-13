"""Offline same-book feedback feasibility; no EM, OCR, ranking or book edits."""

import argparse
from collections import defaultdict
from functools import lru_cache
import math
from pathlib import Path

from spylls.hunspell import Dictionary

from common import ROOT, file_digest, read_json, write_json
from text_edits import edit_chunks


def distance(a, b):
    """Unit edit distance; vocabulary lookup only, not a learned error score."""
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(
                min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return previous[-1]


def proposals(word, seeds, accepts):
    """Literal feedback and one bounded chunk rewrite; retain original separately."""
    result = defaultdict(set)
    for seed in seeds:
        target = seed["after"]
        if target != word and distance(word, target) <= 2:
            result[target].add(("reviewed-lexicon", seed["id"]))
        chunks = list(edit_chunks(seed["before"], target))
        if len(chunks) != 1 or max(map(len, chunks[0])) > 3:
            continue
        observed, replacement = chunks[0]
        positions = range(len(word) + 1) if not observed else range(len(word))
        for position in positions:
            if not word.startswith(observed, position):
                continue
            candidate = word[:position] + replacement + word[position + len(observed) :]
            if candidate != word and candidate.isalpha() and accepts(candidate):
                result[candidate].add(("single-chunk-pattern", seed["id"]))
    return {
        candidate: [dict(generator=g, seed_id=i) for g, i in sorted(evidence)]
        for candidate, evidence in sorted(result.items())
    }


def evaluate(cases, accepts, mode, fraction=0.2):
    """Hold out whole source pages, never use controls as correction feedback."""
    books = sorted({c["book"] for c in cases})
    records, splits = [], []
    for book in books:
        own = [c for c in cases if c["book"] == book]
        errors = [c for c in own if c["expected"] == "correct"]
        pages = sorted({c["page"] for c in errors})
        if mode == "prefix":
            # Fraction of annotated error pages, NOT fraction of the complete book.
            seed_pages = set(pages[: math.ceil(len(pages) * fraction)])
            partitions = [(seed_pages, [c for c in own if c["page"] not in seed_pages])]
        elif mode == "leave-page-out":
            partitions = [
                (set(pages) - {page}, [c for c in own if c["page"] == page])
                for page in pages
            ]
        else:
            raise ValueError("Unknown feedback split")
        for seed_pages, held in partitions:
            seeds = [c for c in errors if c["page"] in seed_pages]
            splits.append(
                dict(
                    book=book,
                    seed_pages=sorted(seed_pages),
                    seed_ids=sorted(c["id"] for c in seeds),
                    evaluation_ids=sorted(c["id"] for c in held),
                )
            )
            for case in held:
                generated = proposals(case["before"], seeds, accepts)
                baseline = case["book_only_candidate_recall"]
                records.append(
                    dict(
                        id=case["id"],
                        book=book,
                        page=case["page"],
                        expected=case["expected"],
                        before=case["before"],
                        after=case["after"],
                        baseline_answer_present=baseline,
                        additional_answer=not baseline and case["after"] in generated,
                        original_retained=True,
                        alternatives=generated,
                    )
                )
    errors = [c for c in records if c["expected"] == "correct"]
    controls = [c for c in records if c["expected"] == "retain"]
    summary = dict(
        errors_evaluated=len(errors),
        baseline_answers_present=sum(c["baseline_answer_present"] for c in errors),
        additional_answers=sum(c["additional_answer"] for c in errors),
        errors_with_feedback_alternatives=sum(bool(c["alternatives"]) for c in errors),
        correct_controls_evaluated=len(controls),
        correct_controls_exposed=sum(bool(c["alternatives"]) for c in controls),
        correct_controls_with_alternatives=sum(
            len(c["alternatives"]) for c in controls
        ),
        error_alternatives=sum(len(c["alternatives"]) for c in errors),
    )
    return dict(
        mode=mode,
        fraction=fraction if mode == "prefix" else None,
        summary=summary,
        splits=splits,
        cases=records,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=ROOT / "docs/statistical-ocr-evaluation.json"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "work/adaptive-ocr-literature/evaluation.json",
    )
    args = parser.parse_args()
    dictionary_path = ROOT / "work/models/en_US"
    dictionary = Dictionary.from_files(str(dictionary_path))
    accepts = lru_cache(maxsize=100000)(dictionary.lookup)
    cases = read_json(args.input)["cases"]
    result = dict(
        purpose="Selected-overlay feedback feasibility; no detector precision, EM replication, time savings or automatic edits",
        input_sha256=file_digest(args.input),
        helper_sha256=file_digest(Path(__file__)),
        edit_helper_sha256=file_digest(ROOT / "scripts/text_edits.py"),
        dictionary_sha256={
            suffix: file_digest(dictionary_path.with_suffix(suffix))
            for suffix in (".aff", ".dic")
        },
        experiments=[evaluate(cases, accepts, "prefix", f) for f in (0.2, 0.5)]
        + [evaluate(cases, accepts, "leave-page-out")],
    )
    write_json(args.output, result)
    for experiment in result["experiments"]:
        print(experiment["mode"], experiment["fraction"], experiment["summary"])


if __name__ == "__main__":
    main()
