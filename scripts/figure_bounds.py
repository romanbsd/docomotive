"""Refine reviewed crops around visible rectangular frames and small captions."""

import io
import statistics
import numpy as np
import cv2
import pymupdf
from PIL import Image
from common import read_json


def side_caption(frame, proposed, rows, body_height):
    """Small text alongside a frame is distinct from a second artwork panel."""
    from ocr import merge_rows

    candidates = [
        r
        for r in merge_rows(rows)
        if r["bbox"][0] >= frame[2] + 0.005
        and r["bbox"][2] <= proposed[2] + 0.03
        and r["bbox"][1] >= frame[1] - 0.03
        and r["bbox"][3] <= frame[3] + 0.02
    ]
    candidates.sort(key=lambda r: r["bbox"][1])
    # A compact side caption can wrap to twelve short rows. Too little text
    # is more likely a diagram label; size and ink coverage provide other gates.
    if not 2 <= len(candidates) <= 12 or sum(len(r["text"]) for r in candidates) < 30:
        return []
    if (
        statistics.median(r["bbox"][3] - r["bbox"][1] for r in candidates)
        > 0.85 * body_height
    ):
        return []
    if any(
        b["bbox"][1] - a["bbox"][3] > 1.7 * body_height
        for a, b in zip(candidates, candidates[1:])
    ):
        return []
    return candidates


def outside_ink_is_text(ink, frame, proposed, captions, rows):
    """Do not crop away a second diagram merely because one frame was found."""
    h, w = ink.shape

    def mask_region(mask, box, pad=0):
        x, y, xx, yy = box
        mask[
            max(0, int((y - pad) * h)) : min(h, int((yy + pad) * h)),
            max(0, int((x - pad) * w)) : min(w, int((xx + pad) * w)),
        ] = False

    outside = np.zeros((h, w), dtype=bool)
    x, y, xx, yy = proposed
    outside[int(y * h) : int(yy * h), int(x * w) : int(xx * w)] = (
        ink[int(y * h) : int(yy * h), int(x * w) : int(xx * w)] > 0
    )
    mask_region(outside, frame, 0.01)
    total = outside.sum()
    for r in captions:
        mask_region(outside, r["bbox"], 0.006)
    for r in rows:
        # Only prose below the frame can explain excess crop height. OCR
        # labels inside another artwork cannot license discarding that artwork.
        if r["bbox"][1] >= frame[3] - 0.01 and len(r["text"]) >= 30:
            mask_region(outside, r["bbox"], 0.006)
    # Five percent tolerates border dust and tiny unrecognized diacritics,
    # not the substantial connected ink of another illustration panel.
    return total == 0 or outside.sum() <= 0.05 * total


def bottom_caption(frame, rows, body_height):
    candidates = sorted(
        [
            r
            for r in rows
            if frame[3] - 0.005 <= r["bbox"][1] <= frame[3] + 0.16
            and r["bbox"][0] >= frame[0] - 0.02
            and r["bbox"][2] <= frame[2] + 0.03
            and r["bbox"][3] - r["bbox"][1] <= 0.82 * body_height
        ],
        key=lambda r: r["bbox"][1],
    )
    run = []
    bottom = frame[3]
    for row in candidates:
        if row["bbox"][1] - bottom > 1.7 * body_height:
            break
        run.append(row)
        bottom = row["bbox"][3]
    # Small multi-line captions can be longer than four OCR rows. A bounded
    # run must still fit a compact caption area and end before body-size prose.
    return run if len(run) <= 8 else []


def framed_bounds(image, proposed, rows, repair_gaps=False):
    w, h = image.size
    ink = (np.asarray(image.convert("L")) < 230).astype("uint8") * 255
    # Retry broken borders only after the original detector abstains. Seven
    # pixels bridge sub-millimeter scan breaks in the caller's 200-dpi preview;
    # coverage checks still use original ink, so repaired pixels are not evidence.
    contour_ink = (
        cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        if repair_gaps
        else ink
    )
    contours, _ = cv2.findContours(contour_ink, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    body = [
        r["bbox"][3] - r["bbox"][1]
        for r in rows
        if len(r["text"]) >= 30
        and (r["bbox"][1] >= proposed[3] - 0.015 or r["bbox"][3] <= proposed[1])
    ]
    if len(body) < 3:
        body = [r["bbox"][3] - r["bbox"][1] for r in rows if len(r["text"]) >= 30]
    if len(body) < 3:
        return None
    body_height = statistics.median(body)
    frames = []
    for c in contours:
        perimeter = cv2.arcLength(c, True)
        quad = cv2.approxPolyDP(c, 0.008 * perimeter, True)
        if len(quad) != 4 or not cv2.isContourConvex(quad):
            continue
        x, y, ww, hh = cv2.boundingRect(quad)
        if ww < 0.25 * w or hh < 0.25 * h or ww > 0.97 * w or hh > 0.97 * h:
            continue
        if abs(cv2.contourArea(quad)) / (ww * hh) < 0.9:
            continue
        f = [x / w, y / h, (x + ww) / w, (y + hh) / h]
        overlap = max(0, min(f[2], proposed[2]) - max(f[0], proposed[0])) * max(
            0, min(f[3], proposed[3]) - max(f[1], proposed[1])
        )
        # Reject a sub-panel frame inside a reviewed multi-panel figure.
        proposed_area = (proposed[2] - proposed[0]) * (proposed[3] - proposed[1])
        if overlap / ((f[2] - f[0]) * (f[3] - f[1])) >= 0.7:
            side = side_caption(f, proposed, rows, body_height)
            bottom = bottom_caption(f, rows, body_height)
            if overlap / proposed_area >= 0.7 or (
                (
                    side
                    or (bottom and (f[2] - f[0]) / (proposed[2] - proposed[0]) >= 0.7)
                )
                and outside_ink_is_text(ink, f, proposed, side + bottom, rows)
            ):
                frames.append((f, side, bottom))
    if not frames:
        return (
            None
            if repair_gaps
            else framed_bounds(image, proposed, rows, repair_gaps=True)
        )
    frame, side, run = max(
        frames, key=lambda v: (v[0][2] - v[0][0]) * (v[0][3] - v[0][1])
    )
    bottom = max([frame[3]] + [r["bbox"][3] for r in run])
    # One percent of page width clears frame strokes; a smaller vertical margin
    # protects the adjacent full-width body line following a caption.
    return dict(
        rect=[
            max(0, frame[0] - 0.01),
            max(0, min([frame[1]] + [r["bbox"][1] for r in side]) - 0.006),
            min(1, max([frame[2]] + [r["bbox"][2] for r in side]) + 0.01),
            min(1, bottom + 0.006),
        ],
        frame=frame,
        caption_rows=[r["bbox"] for r in run + side],
        **({"border_gap_repair": True} if repair_gaps else {}),
    )


def resolve_figure_frames(doc, book, cache, audit):
    if not book.get("recover_figure_frames"):
        return
    for figure in book.get("figures", []):
        page = doc[figure["page"] - 1]
        image = Image.open(
            io.BytesIO(
                page.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY).tobytes("png")
            )
        )
        rows = read_json(cache / f'{figure["page"]:04}.json')["lines"]
        proposal = framed_bounds(image, figure["rect"], rows)
        if (
            proposal
            and max(abs(a - b) for a, b in zip(proposal["rect"], figure["rect"])) > 0.01
        ):
            audit.append(
                dict(
                    page=figure["page"],
                    kind="source-frame-crop",
                    before=figure["rect"],
                    **proposal,
                )
            )
            figure["rect"] = proposal["rect"]
