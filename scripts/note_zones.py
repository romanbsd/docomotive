"""Reviewable footer-zone evidence from source-pixel separator rules."""

import numpy as np
from scipy import ndimage
import re
import statistics


def small_type_proposals(rows, figure_rects=()):
    """Suggest unruled footers using a marker and relative OCR line height."""

    def outside(row):
        x0, y0, x1, y1 = row["bbox"]
        return not any(
            r[0] <= (x0 + x1) / 2 <= r[2] and r[1] <= (y0 + y1) / 2 <= r[3]
            for r in figure_rects
        )

    rows = sorted((r for r in rows if outside(r)), key=lambda r: r["bbox"][1])
    prose = [
        r
        for r in rows
        if len(r["text"]) >= 25
        and r["bbox"][2] - r["bbox"][0] > 0.5
        and 0.1 < r["bbox"][1] < 0.8
    ]
    if len(prose) < 10:
        return []  # Sparse illustration leaves cannot establish body type.
    body = statistics.median(r["bbox"][3] - r["bbox"][1] for r in prose)
    for i, row in enumerate(rows):
        box = row["bbox"]
        # Bottom third, smaller tail type, and a gap before a reference marker
        # distinguish apparatus from ordinary paragraph endings. OCR boxes
        # vary with descenders, so compare the tail's median, not one row.
        if not (
            0.68 < box[1] < 0.98
            and i > 0
            and re.match(
                r"^\s*(?:[＊*†‡•'\"]|\d{1,2}(?!\d)|[¹²³⁴⁵⁶⁷⁸⁹])\s*", row["text"]
            )
        ):
            continue
        tail = rows[i:]
        # Initials plus a four-digit publication year supply independent
        # bibliographic evidence when OCR line heights are too noisy.
        citation = bool(
            re.search(r"\b[^\W\d_]\.", row["text"])
            and re.search(
                r"\b(?:18|19|20)\d{2}\b", " ".join(r["text"] for r in tail[:3])
            )
        )
        # An 8% type reduction is evidence for review, not an auto-cutoff.
        if not citation and (
            box[1] - rows[i - 1]["bbox"][3] < 0.008
            or statistics.median(r["bbox"][3] - r["bbox"][1] for r in tail)
            >= 0.92 * body
        ):
            continue
        # Any full-sized prose below the marker defeats a footer proposal.
        if not citation and any(
            r["bbox"][3] - r["bbox"][1] >= 0.98 * body and len(r["text"]) > 15
            for r in tail[1:]
        ):
            continue
        return [
            dict(
                cutoff=box[1] - 0.001,
                row=row["text"],
                body_height=body,
                evidence="small-type footer with reference marker; source review required",
            )
        ]
    return []


def separator_proposals(image, figure_rects=()):
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    # A footer separator spans many character widths but is only a few pixels
    # thick. Long page rules and ink inside artwork cannot establish notes.
    threshold = max(80, np.percentile(gray, 95) - 70)
    mask = ndimage.binary_opening(
        gray < threshold, structure=np.ones((1, max(20, round(0.09 * width))))
    )
    labels, _ = ndimage.label(mask)
    results = []
    for region in ndimage.find_objects(labels):
        ys, xs = region
        rect = [xs.start / width, ys.start / height, xs.stop / width, ys.stop / height]
        if not (
            0.60 < rect[1] < 0.94
            # Tight or uneven scan margins can put a real rule near the edge.
            and 0.01 < rect[0] < 0.30
            and 0.09 < rect[2] - rect[0] < 0.35
            and ys.stop - ys.start <= max(4, round(height * 0.003))
        ):
            continue
        if any(
            r[0] <= (rect[0] + rect[2]) / 2 <= r[2] and r[1] <= rect[1] <= r[3]
            for r in figure_rects
        ):
            continue
        results.append(
            dict(
                rect=rect,
                cutoff=rect[3] + 0.001,
                evidence="short thin footer rule; source review required",
            )
        )
    return results
