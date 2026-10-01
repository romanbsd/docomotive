"""Deterministic recurring-margin detector. Scores are evidence, not calibrated truth."""

import argparse
import difflib
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from scipy.stats import beta


def signature(text):
    return re.sub(r"[^A-Z]+", " ", text.upper()).strip()


def infer(pages):
    candidates = defaultdict(list)
    for page, rows in sorted(pages.items()):
        for row in rows:
            text = row["text"]
            y = row["bbox"][1]
            letters = [c for c in text if c.isalpha()]
            if (
                0.025 < y < 0.11
                and len(letters) > 4
                and sum(c.isupper() for c in letters) / len(letters) > 0.8
            ):
                candidates[signature(text)].append((int(page), row))
    # Join OCR variants only to a frequent seed; no transitive fuzzy chains.
    groups = []
    used = set()
    for key, items in sorted(candidates.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if key in used:
            continue
        used.add(key)
        members = list(items)
        variants = [key]
        if len(items) >= 3:
            for other in sorted(candidates):
                if (
                    other not in used
                    and difflib.SequenceMatcher(None, key, other).ratio() >= 0.9
                ):
                    members.extend(candidates[other])
                    variants.append(other)
                    used.add(other)
        if len({n for n, _ in members}) < 3:
            continue
        ys = [r["bbox"][1] for _, r in members]
        median = statistics.median(ys)
        mad = statistics.median(abs(y - median) for y in ys)
        accepted = [
            (n, r)
            for n, r in members
            if abs(r["bbox"][1] - median) <= max(0.015, 4 * mad)
        ]
        ns = sorted({n for n, _ in accepted})
        parity = max(sum(n % 2 == p for n in ns) for p in [0, 1]) / len(ns)
        opportunities = len(range(ns[0], ns[-1] + 1, 2 if parity >= 0.9 else 1))
        opportunities = max(opportunities, len(ns))
        groups.append(
            {
                "text": key,
                "variants": variants,
                "pages": ns,
                "median_y": median,
                "mad_y": mad,
                "parity_concentration": parity,
                "recurrence_posterior_mean": (len(ns) + 1) / (opportunities + 2),
                "recurrence_95_interval": [
                    float(beta.ppf(q, len(ns) + 1, opportunities - len(ns) + 1))
                    for q in [0.025, 0.975]
                ],
                "rows": [
                    {"page": n, "bbox": r["bbox"], "text": r["text"]}
                    for n, r in accepted
                ],
            }
        )
    # Footer number model: robust consensus offset for printed number minus PDF page.
    numeric = []
    for page, rows in sorted(pages.items()):
        for r in rows:
            if r["bbox"][1] > 0.84 and re.fullmatch(r"\d{1,4}", r["text"].strip()):
                numeric.append((int(r["text"]) - int(page), int(page), r))
    offsets = defaultdict(list)
    for offset, n, r in numeric:
        offsets[offset].append((n, r))
    offset = max(offsets, key=lambda k: (len(offsets[k]), -abs(k)), default=None)
    footers = (
        []
        if offset is None or len(offsets[offset]) < 5
        else [
            {"page": n, "bbox": r["bbox"], "text": r["text"]}
            for n, r in offsets[offset]
        ]
    )
    return {
        "mode": "conservative-recurrence",
        "headers": groups,
        "printed_page_offset": offset,
        "footers": footers,
        "limitations": [
            "Beta intervals describe recurrence within the observed span, not probability of header correctness.",
            "Unique ornaments and OCR-damaged page numbers require profile rules or review.",
        ],
    }


def excluded(page, row, model):
    return any(
        m["page"] == page
        and m["text"] == row["text"]
        and abs(m["bbox"][1] - row["bbox"][1]) < 0.003
        for m in model.get("excluded_rows", [])
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=Path("output/corrected-pages.json"))
    p.add_argument("--output", type=Path, default=Path("output/layout-analysis.json"))
    a = p.parse_args()
    result = infer(json.loads(a.input.read_text()))
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        f"{len(result['headers'])} recurring header groups; {len(result['footers'])} numbered footers"
    )


if __name__ == "__main__":
    main()
