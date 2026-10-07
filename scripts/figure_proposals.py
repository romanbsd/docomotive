"""Source-pixel illustration proposals for review, never automatic profile edits."""

import re

import numpy as np
from scipy import ndimage


def propose_figures(image, rows):
    """Locate substantial ink unexplained by ordinary OCR lines.

    Captions can corroborate thin line drawings; OCR masks affect detection
    only. Export always crops the unchanged original source pixels.
    """
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    ink = gray < max(80, np.percentile(gray, 95) - 70)
    captions = []
    for row in rows:
        text, box = row["text"], row["bbox"]
        caption = re.match(r"^(?:Рис|Fig)\s*[.,]?\s*\d+", text, re.I)
        if caption:
            captions.append(row)
        # Dense prose and small captions have shallow boxes. Tall diagram
        # labels cannot authorize removal of their surrounding illustration.
        if (
            not caption
            and box[3] - box[1] <= 0.035
            and sum(c.isalpha() for c in text) >= 30
        ):
            x0, y0, x1, y1 = box
            ink[
                max(0, int(y0 * height) - 2) : min(height, int(y1 * height) + 2),
                max(0, int(x0 * width) - 2) : min(width, int(x1 * width) + 2),
            ] = False
    # Margin rules/page numbers and isolated specks are not artwork evidence.
    ink[: int(0.09 * height)] = False
    ink[int(0.965 * height) :] = False
    ink[:, : int(0.04 * width)] = False
    ink[:, int(0.96 * width) :] = False
    joined = ndimage.binary_dilation(ink, iterations=5)
    labels, count = ndimage.label(joined)
    proposals = []
    for label, region in enumerate(ndimage.find_objects(labels), 1):
        if region is None:
            continue
        ys, xs = region
        pixels = int(np.count_nonzero(ink[region] & (labels[region] == label)))
        box = [xs.start / width, ys.start / height, xs.stop / width, ys.stop / height]
        dx, dy = box[2] - box[0], box[3] - box[1]
        nearby = [
            r
            for r in captions
            if -0.01 <= r["bbox"][1] - box[3] <= 0.06
            and r["bbox"][2] > box[0]
            and r["bbox"][0] < box[2]
        ]
        # Small character remnants fail area/extent guards. A caption supplies
        # extra evidence for long, shallow tools or arrows drawn as thin lines.
        if pixels < 180 or not (dx >= 0.08 and dy >= 0.045 or nearby and dx >= 0.15):
            continue
        if nearby:
            caption = min(nearby, key=lambda r: abs(r["bbox"][1] - box[3]))
            box = [
                min(box[0], caption["bbox"][0]),
                box[1],
                max(box[2], caption["bbox"][2]),
                max(box[3], caption["bbox"][3]),
            ]
        proposals.append(
            dict(
                rect=[
                    max(0, box[0] - 0.005),
                    max(0, box[1] - 0.005),
                    min(1, box[2] + 0.005),
                    min(1, box[3] + 0.005),
                ],
                ink_pixels=pixels,
                caption=nearby[0]["text"] if nearby else "",
                evidence="unmasked source ink; requires source review",
            )
        )
    return proposals


def group_proposals(proposals, rows):
    """Join neighboring graphic fragments without swallowing intervening prose."""
    groups = [dict(p) for p in proposals]
    prose = [
        r
        for r in rows
        if sum(c.isalpha() for c in r["text"]) >= 65
        and r["bbox"][2] - r["bbox"][0] >= 0.45
        and not re.match(r"^(?:Рис|Fig)\s*[.,]?\s*\d+", r["text"], re.I)
    ]

    def contains(rect, row):
        x0, y0, x1, y1 = row["bbox"]
        return (
            rect[0] <= (x0 + x1) / 2 <= rect[2] and rect[1] <= (y0 + y1) / 2 <= rect[3]
        )

    changed = True
    while changed:
        changed = False
        for i, a in enumerate(groups):
            for j in range(i + 1, len(groups)):
                b = groups[j]
                ra, rb = a["rect"], b["rect"]
                if max(ra[1], rb[1]) - min(ra[3], rb[3]) > 0.035:
                    continue
                combined = [
                    min(ra[0], rb[0]),
                    min(ra[1], rb[1]),
                    max(ra[2], rb[2]),
                    max(ra[3], rb[3]),
                ]
                if any(
                    contains(combined, r) and not (contains(ra, r) or contains(rb, r))
                    for r in prose
                ):
                    continue
                a["rect"] = combined
                a["ink_pixels"] += b["ink_pixels"]
                a["caption"] = " / ".join(x for x in (a["caption"], b["caption"]) if x)
                groups.pop(j)
                changed = True
                break
            if changed:
                break
    return sorted(groups, key=lambda p: (p["rect"][1], p["rect"][0]))
