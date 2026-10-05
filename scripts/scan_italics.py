"""Bounded, pixel-backed whole-line emphasis recovery for scanned verse."""

import numpy as np
import pymupdf
from scipy.ndimage import label, find_objects

from figures import crop_artwork_pixels


def line_slant(image):
    """Measure glyph lean by concentrating vertical stems under trial shears."""
    gray = np.asarray(image.convert("L"))
    low, paper = np.percentile(gray, [5, 90])
    if paper - low < 25:
        return dict(status="uncertain", reason="insufficient-contrast")
    mask = gray < (paper + low) / 2
    # A bounded ink fraction rejects empty crops and dense non-text artwork.
    if not 0.03 <= mask.mean() <= 0.45:
        return dict(status="uncertain", reason="non-text-density")
    components, _ = label(mask)
    # Cover approximately +/-22 degrees in 0.025-slope steps, including upright.
    shears = np.linspace(-0.4, 0.4, 33)
    upright = int(np.argmin(np.abs(shears)))
    readings = []
    for index, region in enumerate(find_objects(components), 1):
        if region is None:
            continue
        glyph = components[region] == index
        height, width = glyph.shape
        # Dots, rules and joined words do not provide reliable stem direction.
        if (
            height < 7
            or width < 2
            or width > 1.5 * height
            or glyph.sum() < 1.5 * height
        ):
            continue
        scores = []
        for shear in shears:
            projected = np.zeros((height, width + height + 4), dtype=bool)
            for y in range(height):
                offset = round((y - height / 2) * shear) + height // 2 + 2
                projected[y, offset : offset + width] = glyph[y]
            # Cubing rewards aligned stems, instead of large rounded bowls.
            counts = projected.sum(axis=0).astype(np.float64)
            scores.append(float(np.sum(np.power(counts, 3))))
        maximum = max(scores)
        # Ties favor the upright reading, rather than an arbitrary grid edge.
        best = min(
            (i for i, score in enumerate(scores) if score == maximum),
            key=lambda i: abs(shears[i]),
        )
        readings.append(dict(shear=float(shears[best]), gain=maximum / scores[upright]))
    if len(readings) < 8:
        return dict(
            status="uncertain", reason="insufficient-glyphs", glyphs=len(readings)
        )
    return dict(
        status="measured",
        glyphs=len(readings),
        readings=readings,
        median_shear=float(np.median([r["shear"] for r in readings])),
    )


def italic_evidence(candidate, controls):
    """Require independent upright prose controls and broad glyph agreement."""
    measured = [c for c in controls if c.get("status") == "measured"]
    if candidate.get("status") != "measured" or len(measured) < 2:
        return dict(accepted=False, reason="insufficient-line-evidence")
    baseline = float(np.median([c["median_shear"] for c in measured]))
    # Large page skew or predominantly italic control prose needs review.
    if abs(baseline) > 0.1:
        return dict(
            accepted=False,
            reason="uncertain-upright-reference",
            baseline_shear=baseline,
        )
    # About eight degrees of relative lean and 12% concentration improvement
    # separate italic stems from rounding/anti-aliasing and page-skew noise.
    votes = [
        r["shear"] - baseline >= 0.14 and r["gain"] >= 1.12
        for r in candidate["readings"]
    ]
    fraction = sum(votes) / len(votes)
    # Eighty percent agreement avoids italicizing a roman line with a few
    # naturally diagonal glyphs, or an inline italic word inside roman prose.
    return dict(
        accepted=fraction >= 0.8,
        reason="glyph-consensus" if fraction >= 0.8 else "mixed-or-upright-glyphs",
        baseline_shear=baseline,
        supporting_fraction=fraction,
        glyphs=candidate["glyphs"],
    )


def attach_scan_italics(rows, page, number, book, audit):
    """Recover whole lines in existing verse regions; never invent text or fonts."""
    regions = book.get("verse_regions", {}).get(str(number), [])
    candidates = [
        r
        for r in rows
        if r.get("kind") == "verse"
        or any(lo <= r["bbox"][1] <= hi for lo, hi in regions)
    ]
    if not candidates:
        return

    def measure(row):
        box = row["bbox"]
        rect = pymupdf.Rect(
            box[0] * page.rect.width,
            box[1] * page.rect.height,
            box[2] * page.rect.width,
            box[3] * page.rect.height,
        )
        # One PDF point retains anti-aliased glyph edges without a neighboring line.
        rect = (rect + (-1, -1, 1, 1)) & page.rect
        image, crop = crop_artwork_pixels(page, rect, dpi=400)
        return dict(line_slant(image), crop=crop)

    controls = [
        r
        for r in rows
        if not any(r is c for c in candidates)
        and r.get("kind", "text") == "text"
        and len(r["text"]) >= 60
        and r["bbox"][2] - r["bbox"][0] >= 0.6
        and not r.get("inline")
    ]
    # Three full prose lines bound work and establish an upright page reference.
    measured_controls = [measure(r) for r in controls[:3]]
    for row in candidates:
        candidate = measure(row)
        evidence = italic_evidence(candidate, measured_controls)
        if evidence["accepted"]:
            style = dict(start=0, end=len(row["text"]), tags=["em"])
            if style not in row.setdefault("inline", []):
                row["inline"].append(style)
        audit.append(
            dict(
                page=number,
                kind="scan-italic-evidence",
                text=row["text"],
                bbox=row["bbox"],
                candidate=candidate,
                controls=[
                    dict(text=r["text"], bbox=r["bbox"], measurement=m)
                    for r, m in zip(controls[:3], measured_controls)
                ],
                **evidence,
            )
        )
