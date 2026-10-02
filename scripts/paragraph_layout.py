"""Deterministic, skew-aware margins and conservative scanned quotation evidence."""

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


def infer_paragraph_layout(rows):
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
        offsets = [residuals[i][0] for i in run]
        if len(matched) < 2 or max(offsets) - min(offsets) > 0.012:
            return
        quote_left = statistics.median(offsets)
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
    return evidence, dict(
        status="accepted", left=left, right=right, quotation_runs=runs
    )
