"""Retrospective PoCoTo-style groupability; no predictions or book edits."""

import argparse
from collections import defaultdict
from pathlib import Path

from common import ROOT, file_digest, read_json, write_json
from text_edits import edit_chunks


def groupability(cases):
    groups = defaultdict(list)
    for case in cases:
        if case["expected"] != "correct":
            continue
        rules = list(edit_chunks(case["after"], case["before"]))
        if len(rules) == 1 and max(map(len, rules[0])) <= 3:
            groups[case["book"], rules[0]].append(case)
    result = []
    for (book, (target, observed)), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        # Sensitivity to a naive global rewrite; NOT PoCoTo's actual acceptance.
        controls = [
            c["id"]
            for c in cases
            if c["book"] == book
            and c["expected"] == "retain"
            and observed in c["before"]
        ]
        result.append(
            dict(
                book=book,
                observed=observed,
                target=target,
                member_ids=[c["id"] for c in members],
                correct_controls_exposed_to_blanket_rewrite=controls,
            )
        )
    return dict(
        groups=result,
        summary=dict(
            repeated_groups=len(result),
            reviewed_pairs_groupable=sum(len(g["member_ids"]) for g in result),
            total_reviewed_pairs=sum(c["expected"] == "correct" for c in cases),
            correct_controls_exposed=len(
                {
                    i
                    for g in result
                    for i in g["correct_controls_exposed_to_blanket_rewrite"]
                }
            ),
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=ROOT / "docs/statistical-ocr-evaluation.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "work/ocr-literature/pocoto-groups.json"
    )
    args = parser.parse_args()
    result = groupability(read_json(args.input)["cases"])
    result.update(
        input_sha256=file_digest(args.input),
        helper_sha256=file_digest(Path(__file__)),
        edit_helper_sha256=file_digest(ROOT / "scripts/text_edits.py"),
        purpose="oracle grouping from reviewed answers; no predictive accuracy or time-savings claim",
    )
    write_json(args.output, result)
    print(result["summary"])


if __name__ == "__main__":
    main()
