"""OCR geometry alignment and conservative named-engine reconciliation."""

import copy
import difflib
import re
import statistics
from wordfreq import zipf_frequency

TOKEN = re.compile(r"[\w]+(?:['’-][\w]+)*|[^\w\s]", re.UNICODE)


def center(line):
    return (line["bbox"][1] + line["bbox"][3]) / 2


def merge_rows(lines):
    groups = []
    for line in sorted(
        lines, key=lambda l: (l.get("column", 0), center(l), l["bbox"][0])
    ):
        line = copy.deepcopy(line)
        if (
            groups
            and line.get("column", 0) == groups[-1][0].get("column", 0)
            and abs(center(line) - statistics.mean(center(p) for p in groups[-1]))
            < 0.008
        ):
            groups[-1].append(line)
        else:
            groups.append([line])
    rows = []
    for group in groups:
        parts = sorted(group, key=lambda l: l["bbox"][0])
        row = copy.deepcopy(parts[0])
        row["text"] = " ".join(p["text"] for p in parts)
        row["bbox"] = [
            min(p["bbox"][0] for p in parts),
            min(p["bbox"][1] for p in parts),
            max(p["bbox"][2] for p in parts),
            max(p["bbox"][3] for p in parts),
        ]
        row["confidence"] = min(p.get("confidence", 1) for p in parts)
        rows.append(row)
    return rows


def embedded(page, split=None, raw=False):
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            box = l["bbox"]
            box = [
                box[0] / page.rect.width,
                box[1] / page.rect.height,
                box[2] / page.rect.width,
                box[3] / page.rect.height,
            ]
            lines.append(
                {
                    "text": "".join(s["text"] for s in l["spans"]),
                    "bbox": box,
                    "column": int(split is not None and box[0] > split),
                    "confidence": 0,
                }
            )
    return lines if raw else merge_rows(lines)


def nearest(line, rows):
    matches = [
        r
        for r in rows
        if r.get("column", 0) == line.get("column", 0)
        and abs(center(line) - center(r)) < 0.012
    ]
    return min(matches, key=lambda r: abs(center(line) - center(r)), default=None)


def comparable(text):
    return re.findall(r"[\w]+(?:['’-][\w]+)*", text.lower().replace("’", "'"))


def correct_page(
    primary,
    baseline,
    secondary,
    tertiary,
    audit,
    review,
    page,
    primary_name="tesseract",
    secondary_name="vision",
    recover_regions=False,
):
    rows = merge_rows(primary)
    # Some engines collapse two printed lines into a confident but garbled row.
    # Recover the region only when two fresh engines independently agree on
    # every line, their geometry covers the tall row, and its text disagrees.
    recovered = []
    for row in rows:
        peers = [
            r
            for r in secondary
            if recover_regions
            and r.get("column", 0) == row.get("column", 0)
            and row["bbox"][1] <= center(r) <= row["bbox"][3] + 0.002
            and abs(r["bbox"][0] - row["bbox"][0]) < 0.035
            and abs(r["bbox"][2] - row["bbox"][2]) < 0.05
        ]
        agreement = [nearest(r, tertiary) for r in peers]

        # Old embedded OCR can corroborate a fresh line when the other fresh
        # engine disagrees only at a tiny apparatus glyph. Prose must match
        # exactly in two witnesses; the third may add only one digit-confusion
        # token (a common note glyph error), never substitute prose words.
        def agrees(r, other):
            if not other:
                return False
            own, alternate = comparable(r["text"]), comparable(other["text"])
            old = nearest(other, baseline)
            changes = difflib.SequenceMatcher(None, own, alternate).get_opcodes()
            return own == alternate or (
                old
                and comparable(old["text"]) in (own, alternate)
                and sum(
                    (j - i) + (l - k) for tag, i, j, k, l in changes if tag != "equal"
                )
                == 1
                and all(
                    tag == "equal"
                    or (
                        tag in ("insert", "delete")
                        and (j - i) + (l - k) == 1
                        and all(
                            len(token) <= 3
                            and all(c in "0123456789silobz" for c in token)
                            for token in own[i:j] + alternate[k:l]
                        )
                    )
                    for tag, i, j, k, l in changes
                )
            )

        tall = peers and row["bbox"][3] - row["bbox"][1] > 1.5 * statistics.median(
            r["bbox"][3] - r["bbox"][1] for r in peers
        )
        if (
            len(peers) >= 2
            and tall
            and all(
                other
                and agrees(r, other)
                and r.get("confidence", 0) >= 0.85
                and other.get("confidence", 0) >= 0.85
                for r, other in zip(peers, agreement)
            )
            and difflib.SequenceMatcher(
                None,
                comparable(row["text"]),
                comparable(" ".join(r["text"] for r in peers)),
            ).ratio()
            < 0.6
            and all(
                not (
                    existing := nearest(
                        r, [other for other in rows if other is not row]
                    )
                )
                or comparable(existing["text"]) == comparable(other["text"])
                for r, other in zip(peers, agreement)
            )
        ):
            recovered.extend(
                copy.deepcopy(
                    r
                    if comparable(r["text"]) == comparable(other["text"])
                    or (
                        (old := nearest(r, baseline))
                        and comparable(r["text"]) == comparable(old["text"])
                    )
                    else other
                )
                for r, other in zip(peers, agreement)
                if not nearest(
                    r, [existing for existing in rows if existing is not row]
                )
            )
            audit.append(
                dict(
                    page=page,
                    kind="two-witness-region-recovery",
                    before=row["text"],
                    bbox=row["bbox"],
                    after=[r["text"] for r in peers],
                    evidence=f"{secondary_name}/rapid line agreement, with embedded corroboration for small discrepancies",
                )
            )
        else:
            recovered.append(row)
    rows = recovered
    for row in baseline:
        if len(row["text"]) < 8 or nearest(row, rows):
            continue
        alt = nearest(row, secondary)
        other = nearest(row, tertiary)
        if (
            alt
            and other
            and difflib.SequenceMatcher(
                None, comparable(alt["text"]), comparable(other["text"])
            ).ratio()
            > 0.8
        ):
            new = copy.deepcopy(alt)
            new["bbox"] = row["bbox"]
            new["recovered"] = True
            rows.append(new)
            audit.append(
                {
                    "page": page,
                    "kind": "missing-line-recovery",
                    "bbox": row["bbox"],
                    "after": new["text"],
                    "evidence": f"{secondary_name} and rapid agree at embedded OCR geometry",
                }
            )
        else:
            review.append(
                {
                    "page": page,
                    "kind": "possible-missing-line",
                    "bbox": row["bbox"],
                    "embedded": row["text"],
                }
            )
    rows = merge_rows(rows)
    for row in rows:
        raw_primary = row["text"]
        witnesses = [nearest(row, source) for source in [baseline, secondary, tertiary]]
        spans = list(TOKEN.finditer(row["text"]))
        vt = [m.group() for m in spans]
        proposals = {}
        for witness in witnesses:
            if witness is None:
                continue
            wt = TOKEN.findall(witness["text"])
            for tag, i, j, k, l in difflib.SequenceMatcher(
                None, vt, wt, autojunk=False
            ).get_opcodes():
                if tag == "replace" and j - i == l - k == 1:
                    proposals.setdefault((i, wt[k]), 0)
                    proposals[(i, wt[k])] += 1
        edits = []
        for (i, after), votes in proposals.items():
            before = vt[i]
            if votes < 2 or before == after:
                continue
            plausible = (
                before.isalpha()
                and after.isalpha()
                and len(after) > 2
                and difflib.SequenceMatcher(None, before, after).ratio() >= 0.65
                and zipf_frequency(before, "en") < 2
                and (zipf_frequency(after, "en") >= 2 or votes == 3)
            )
            plausible = plausible or (
                before in {"J", "1"} and after == "I" and votes >= 2
            )
            if plausible:
                edits.append((spans[i].start(), spans[i].end(), after))
                audit.append(
                    {
                        "page": page,
                        "kind": "two-witness-token",
                        "before": before,
                        "after": after,
                        "votes": votes,
                        "bbox": row["bbox"],
                    }
                )
        for start, end, text in sorted(edits, reverse=True):
            row["text"] = row["text"][:start] + text + row["text"][end:]
        own = comparable(row["text"])
        best = [comparable(w["text"]) for w in witnesses[1:] if w]
        # Agreement on lexical content resolves harmless OCR typography differences.
        if (
            best
            and not any(own == other for other in best)
            and not (witnesses[0] and own == comparable(witnesses[0]["text"]))
        ):
            texts = {
                primary_name: raw_primary,
                "embedded": witnesses[0]["text"] if witnesses[0] else "",
                secondary_name: witnesses[1]["text"] if witnesses[1] else "",
                "rapid": witnesses[2]["text"] if witnesses[2] else "",
            }
            review.append(
                {
                    "page": page,
                    "kind": "ocr-disagreement",
                    "primary": row["text"],
                    "primary_engine": primary_name,
                    "engine_texts": texts,
                    "column": row.get("column", 0),
                    "embedded": texts["embedded"],
                    "vision": texts.get("vision", ""),
                    "tesseract": texts.get("tesseract", ""),
                    "paddle": texts["rapid"],
                    "bbox": row["bbox"],
                }
            )
    return rows
