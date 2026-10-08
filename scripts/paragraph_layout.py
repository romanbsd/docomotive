"""Deterministic, skew-aware margins and conservative scanned quotation evidence."""

import re
import statistics
from scipy.stats import theilslopes


def _quantile(values, fraction):
    values = sorted(values)
    return values[round((len(values) - 1) * fraction)]


def _edge(rows, side, fraction):
    ys = [r["bbox"][1] for r in rows]
    xs = [r["bbox"][side] for r in rows]
    if max(ys) - min(ys) < 0.25:
        return None
    slope = float(theilslopes(xs, ys).slope)
    intercept = _quantile([x - slope * y for x, y in zip(xs, ys)], fraction)
    inliers = [
        r
        for r in rows
        if abs(r["bbox"][side] - slope * r["bbox"][1] - intercept) < 0.008
    ]
    if (
        len(inliers) < 8
        or max(r["bbox"][1] for r in inliers) - min(r["bbox"][1] for r in inliers)
        < 0.25
    ):
        return None
    slope = float(
        theilslopes(
            [r["bbox"][side] for r in inliers], [r["bbox"][1] for r in inliers]
        ).slope
    )
    intercept = statistics.median(
        r["bbox"][side] - slope * r["bbox"][1] for r in inliers
    )
    error = statistics.median(
        abs(r["bbox"][side] - slope * r["bbox"][1] - intercept) for r in inliers
    )
    if abs(slope) > 0.12 or error > 0.004:
        return None
    return dict(
        slope=slope, intercept=intercept, median_error=error, inliers=len(inliers)
    )


def infer_paragraph_layout(rows, allow_full_width_quotes=False):
    """Return row evidence; never alter wording or use book/page/phrase identities.

    Full-width lines constrain robust margin envelopes. Three or more contiguous
    lines inset on the left, with at least two matching right insets, support a
    quotation. Short last lines may belong to that run, but cannot prove it.
    """
    eligible = [
        r
        for r in rows
        if r.get("kind", "text") == "text" and not r.get("paragraph_start")
    ]
    if len(eligible) < 12:
        return {}, dict(status="fallback", reason="insufficient-body-lines")
    widths = [r["bbox"][2] - r["bbox"][0] for r in eligible]
    width = _quantile(widths, 0.9)
    long_rows = [r for r in eligible if r["bbox"][2] - r["bbox"][0] >= 0.8 * width]
    if len(long_rows) < 8:
        return {}, dict(status="fallback", reason="insufficient-long-lines")
    left = _edge(long_rows, 0, 0.2)
    right = _edge(long_rows, 2, 0.85)
    if left is None or right is None:
        return {}, dict(status="fallback", reason="unstable-margin-envelope")
    evidence = {}
    inset = max(0.018, 0.025 * width)
    residuals = []
    for r in rows:
        x0, y, x1, _ = r["bbox"]
        dl = x0 - (left["intercept"] + left["slope"] * y)
        dr = right["intercept"] + right["slope"] * y - x1
        residuals.append((dl, dr))
        evidence[r["row_id"]] = dict(left_offset=dl)
    runs = []
    run = []

    def finish():
        if len(run) < 3:
            return
        matched = [
            i
            for i in run
            if inset <= residuals[i][1] <= 0.15 * width
            and abs(residuals[i][0] - residuals[i][1]) <= 0.02
        ]
        if len(matched) < 2:
            return
        quote_left = statistics.median(residuals[i][0] for i in matched)
        offsets = [residuals[i][0] - quote_left for i in run]
        # A run beginning inside labeled/hanging prose can look like a quote.
        # Without type measurements, relax the old stable-margin test only
        # when the run itself starts with a distinct paragraph indent.
        if (
            max(offsets) - min(offsets) > 0.012
            and offsets[0] <= 0.018
            and not any(rows[i].get("scan_font_size") for i in run)
        ):
            return
        # A stable inset is the quotation margin; occasional deeper lines are
        # paragraph indents. Reject wandering margins and hanging outliers.
        if sum(abs(x) <= 0.012 for x in offsets) < 0.7 * len(run) or any(
            x < -0.012 or x > 0.06 * width for x in offsets
        ):
            return
        runs.append([rows[i]["row_id"] for i in run])
        for i in run:
            evidence[rows[i]["row_id"]].update(
                kind="quote", left_offset=residuals[i][0] - quote_left
            )

    for i, r in enumerate(rows):
        dl, _ = residuals[i]
        candidate = (
            r.get("kind", "text") == "text"
            and not r.get("paragraph_start")
            and inset <= dl <= 0.15 * width
        )
        gap = bool(run) and r["bbox"][1] - rows[run[-1]]["bbox"][3] > 0.018
        if not candidate or gap:
            finish()
            run = []
        if candidate:
            run.append(i)
    finish()
    if allow_full_width_quotes:
        # Full-width quotes need an introduction and gaps at both boundaries.
        # Smaller glyph boxes alone are noisy: require tighter line pitch too.
        for i, intro in enumerate(rows[:-1]):
            if (
                not intro["text"].rstrip().endswith(":")
                or rows[i + 1]["bbox"][1] - intro["bbox"][3] < 0.008
            ):
                continue
            small = []
            for j in range(i + 1, len(rows)):
                row = rows[j]
                if row.get("kind", "text") != "text" or row.get(
                    "column", 0
                ) != intro.get("column", 0):
                    break
                if abs(residuals[j][0]) > (0.06 * width if not small else 0.012):
                    break
                if small and row["bbox"][1] - rows[small[-1]]["bbox"][3] >= 0.008:
                    break
                small.append(j)
            if len(small) < 3 or small[-1] + 1 >= len(rows):
                continue
            following = rows[small[-1] + 1]
            if following["bbox"][1] - rows[small[-1]]["bbox"][3] < 0.008:
                continue
            if not re.search(
                r"""[.!?][”"']?\d{0,3}$""", rows[small[-1]]["text"].rstrip()
            ):
                continue
            controls = [
                r for j, r in enumerate(rows) if j not in small and r in long_rows
            ]
            if len(controls) < 8:
                continue
            scale = statistics.median(
                rows[j]["bbox"][3] - rows[j]["bbox"][1] for j in small
            ) / statistics.median(r["bbox"][3] - r["bbox"][1] for r in controls)
            body_pitch = [
                b["bbox"][1] - a["bbox"][1]
                for a, b in zip(controls, controls[1:])
                if 0.008 < b["bbox"][1] - a["bbox"][1] < 0.04
            ]
            quote_pitch = [
                rows[b]["bbox"][1] - rows[a]["bbox"][1]
                for a, b in zip(small, small[1:])
            ]
            # A 4% size and 6% pitch reduction reject ordinary colon paragraphs.
            if (
                not 0.8 <= scale <= 0.96
                or len(body_pitch) < 5
                or not 0.75
                <= statistics.median(quote_pitch) / statistics.median(body_pitch)
                <= 0.94
            ):
                continue
            ids = [rows[j]["row_id"] for j in small]
            runs.append(ids)
            for j in small:
                evidence[rows[j]["row_id"]].update(
                    kind="quote", quote_font_scale=round(scale, 2)
                )
    # Compare measured glyph sizes only after geometry establishes the quote.
    # A 4% difference avoids treating OCR metric noise as a smaller typeface.
    body_sizes = [
        r["scan_font_size"]
        for r in long_rows
        if r.get("scan_font_size") and evidence[r["row_id"]].get("kind") != "quote"
    ]
    if len(body_sizes) >= 8:
        body_size = statistics.median(body_sizes)
        for ids in runs:
            sizes = [
                r["scan_font_size"]
                for r in rows
                if r["row_id"] in ids and r.get("scan_font_size")
            ]
            if len(sizes) >= 3:
                scale = statistics.median(sizes) / body_size
                # Extreme changes suggest mismatched OCR metrics, not prose.
                if 0.8 <= scale <= 0.96:
                    for row_id in ids:
                        evidence[row_id]["quote_font_scale"] = round(scale, 2)
    return evidence, dict(
        status="accepted", left=left, right=right, quotation_runs=runs
    )
