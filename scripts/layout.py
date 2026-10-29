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
    return " ".join("".join(c if c.isalpha() else " " for c in text.upper()).split())


def extend_footers(pages, offset, folios):
    """Recognize merged folios and recurring printer labels from margin evidence."""
    evidence = dict(
        offset=offset,
        supporting_pages=sorted({r["page"] for r in folios}),
        accepted=[],
        review=[],
    )
    if len(evidence["supporting_pages"]) < 5:
        return [], evidence
    # OCR may read a compact gathering number as a look-alike letter.
    label = r"[0-9ЗзОоІIl]{1,3}\s*[*∗]?"
    numbered = re.compile(r"^(\S{1,8})\s+(.+?)\s+(\d{1,4})$")
    bare = re.compile(rf"^({label})\s+(.+?)$")
    pair = re.compile(r"^\d{1,3}\s*(?:[*∗%]\s*|\s+)(\d{1,4})$")
    decorated = re.compile(r"^[•·*∗—–-]\s*(\d{1,4})$")

    def geometry(n, row, outer=True):
        nearby = [r for r in folios if abs(r["page"] - n) <= 12]
        controls = nearby if len(nearby) >= 3 else folios
        centers = [(r["bbox"][1] + r["bbox"][3]) / 2 for r in controls]
        center = statistics.median(centers)
        mad = statistics.median(abs(v - center) for v in centers)
        box = row["bbox"]
        tolerance = min(0.035, max(0.015, 4 * mad))
        aligned = (
            box[1] > 0.84
            and box[3] - box[1] <= 0.035
            and abs((box[1] + box[3]) / 2 - center) <= tolerance
        )
        peers = [r for r in controls if r["page"] % 2 == n % 2]
        peers = peers or controls
        edge = statistics.median(r["bbox"][2] for r in peers)
        return aligned and (not outer or abs(box[2] - edge) <= 0.04), dict(
            control_pages=[r["page"] for r in controls],
            center_y=center,
            tolerance_y=tolerance,
            outer_right=edge,
        )

    families = defaultdict(set)
    for n, rows in pages.items():
        n = int(n)
        for row in rows:
            match = numbered.fullmatch(row["text"].strip())
            if (
                match
                and re.fullmatch(label, match[1])
                and int(match[3]) == n + offset
                and 4 <= len(signature(match[2])) <= 50
                and row["bbox"][2] - row["bbox"][0] >= 0.4
                and geometry(n, row)[0]
            ):
                families[signature(match[2])].add(n)
    known = {key: sorted(ns) for key, ns in families.items() if len(ns) >= 3}
    evidence["printer_label_families"] = known

    def family_for(text):
        key = signature(text)
        if key in known:
            return key
        # Corrupt initials and compact gathering numbers must not defeat an
        # established label. Keep its final long word exact and reject ties;
        # fuzzy matches never become new seeds or form transitive chains.
        words = key.split()
        matches = [
            candidate
            for candidate in known
            if words
            and len(words[-1]) >= 5
            and candidate.split()[-1] == words[-1]
            and difflib.SequenceMatcher(None, candidate, key).ratio() >= 0.75
        ]
        return matches[0] if len(matches) == 1 else ""

    existing = {(r["page"], tuple(r["bbox"]), r["text"]) for r in folios}
    additions = []
    for n, rows in pages.items():
        n = int(n)
        for row in rows:
            text = row["text"].strip()
            if (
                row["bbox"][1] <= 0.84
                or (n, tuple(row["bbox"]), row["text"]) in existing
            ):
                continue
            match = numbered.fullmatch(text)
            short = pair.fullmatch(text) or decorated.fullmatch(text)
            family = family_for(match[2]) if match else ""
            full_label = bare.fullmatch(text)
            if not family and full_label:
                family = family_for(full_label[2])
            kind = None
            if match and family in known and int(match[3]) == n + offset:
                kind = "printer-label-and-folio"
            elif short and int(short[1]) == n + offset:
                # Two plain numbers need a broad box; compact mathematical or
                # citation pairs cannot establish independent margin positions.
                if decorated.fullmatch(text) or row["bbox"][2] - row["bbox"][0] >= 0.4:
                    kind = "gathering-or-decoration-and-folio"
            elif (
                not match
                and full_label
                and family in known
                and row["bbox"][0] < 0.4
                and row["bbox"][2] - row["bbox"][0] <= 0.5
            ):
                kind = "recurring-printer-label"
            if not kind:
                continue
            supported, proof = geometry(n, row, outer=kind != "recurring-printer-label")
            item = dict(
                page=n, bbox=row["bbox"], text=row["text"], kind=kind, evidence=proof
            )
            if family in known:
                item["evidence"]["family_pages"] = known[family]
            evidence["accepted" if supported else "review"].append(item)
            if supported:
                additions.append(dict(page=n, bbox=row["bbox"], text=row["text"]))
    return additions, evidence


def footer_leakage(model, layout):
    """Catch classified margin rows reintroduced or edited after layout analysis."""
    footers = {(r["page"], tuple(r["bbox"])): r for r in layout.get("footers", [])}
    leaks = {}
    for chapter in model:
        for block in chapter["blocks"] + chapter["notes"]:
            for source in block.get("sources", []):
                key = source["page"], tuple(source["bbox"])
                if key in footers:
                    leaks[key] = dict(footers[key], rendered_text=source["text"])
    return list(leaks.values())


def infer(pages):
    candidates = defaultdict(list)
    for page, rows in sorted(pages.items()):
        for row in rows:
            text = row["text"]
            y = row["bbox"][1]
            letters = [c for c in text if c.isalpha()]
            if (
                0.025 < y < 0.14
                and len(letters) > 4
                # Scanned running heads often use small caps or OCR damages a
                # single initial, leaving the rest of an otherwise uppercase
                # line intact. Recurrence and aligned margin geometry below
                # provide the stronger evidence; keep one-off title case out.
                and sum(c.isupper() for c in letters) / len(letters) >= 0.55
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
            if abs(r["bbox"][1] - median) <= max(0.1, 4 * mad)
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
    offset = max(
        offsets, key=lambda k: (len({n for n, _ in offsets[k]}), -abs(k)), default=None
    )
    footers = (
        []
        if offset is None or len({n for n, _ in offsets[offset]}) < 5
        else [
            {"page": n, "bbox": r["bbox"], "text": r["text"]}
            for n, r in offsets[offset]
        ]
    )
    additional, footer_analysis = extend_footers(pages, offset, footers)
    footers.extend(additional)
    return {
        "mode": "conservative-recurrence",
        "headers": groups,
        "printed_page_offset": offset,
        "footers": footers,
        "footer_analysis": footer_analysis,
        "limitations": [
            "Beta intervals describe recurrence within the observed span, not probability of header correctness.",
            "Unrecognized or ambiguously positioned margin matter still requires source review.",
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
