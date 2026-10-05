"""Bounded, pixel-backed whole-line emphasis recovery for scanned verse."""

import numpy as np
import pymupdf
import difflib
import re
import json
import subprocess
from pathlib import Path
from functools import lru_cache
from scipy.ndimage import label, find_objects

from figures import crop_artwork_pixels


def line_slant(image, stems_only=False):
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
    regions = find_objects(components)
    heights = [r[0].stop - r[0].start for r in regions if r is not None]
    # Word crops need to ignore detached dots and punctuation; these offer no
    # stem direction and otherwise overwhelm short italic phrases.
    minimum_height = (
        max(7, 0.6 * np.percentile(heights, 75)) if stems_only and heights else 7
    )
    # Cover approximately +/-22 degrees in 0.025-slope steps, including upright.
    shears = np.linspace(-0.4, 0.4, 33)
    upright = int(np.argmin(np.abs(shears)))
    readings = []
    for index, region in enumerate(regions, 1):
        if region is None:
            continue
        glyph = components[region] == index
        height, width = glyph.shape
        # Dots, rules and joined words do not provide reliable stem direction.
        if (
            height < minimum_height
            or width < 2
            or width > (2.5 if stems_only else 1.5) * height
            or glyph.sum() < 1.5 * height
        ):
            continue
        yy, xx = np.nonzero(glyph)
        # Batch trial shears into separate histograms: the same vertical
        # projection without 33 Python calls per glyph. Padding adds no ink.
        bins = width + height + 4
        projected_x = (
            xx[None, :]
            + np.rint((yy[None, :] - height / 2) * shears[:, None]).astype(int)
            + height // 2
            + 2
        )
        indices = projected_x + np.arange(len(shears))[:, None] * bins
        counts = (
            np.bincount(indices.ravel(), minlength=len(shears) * bins)
            .reshape(len(shears), bins)
            .astype(np.float64)
        )
        # Cubing rewards aligned stems, instead of large rounded bowls.
        scores = np.sum(np.power(counts, 3), axis=1).tolist()
        maximum = max(scores)
        # Ties favor the upright reading, rather than an arbitrary grid edge.
        best = min(
            (i for i, score in enumerate(scores) if score == maximum),
            key=lambda i: abs(shears[i]),
        )
        reading = dict(shear=float(shears[best]), gain=maximum / scores[upright])
        if stems_only:
            reading.update(x0=region[1].start, x1=region[1].stop)
        readings.append(reading)
    if len(readings) < (6 if stems_only else 8):
        return dict(
            status="uncertain", reason="insufficient-glyphs", glyphs=len(readings)
        )
    return dict(
        status="measured",
        glyphs=len(readings),
        readings=readings,
        median_shear=float(np.median([r["shear"] for r in readings])),
    )


def inline_italic_spans(image, text, controls, recognizer=None):
    """Align complete word groups to locally recognized pixels; never edit text."""
    from scanned_notes import character_line

    measured = [c for c in controls if c.get("status") == "measured"]
    if len(measured) < 2:
        return []
    baseline = float(np.median([c["median_shear"] for c in measured]))
    if abs(baseline) > 0.1:
        return []
    # Measure each glyph once, rather than recomputing trial shears for every
    # overlapping word group. Line-scale stem filtering remains consistent.
    line = line_slant(image, stems_only=True)
    if line["status"] != "measured":
        return []
    # Apply the group consensus guards to contiguous pixel stems first.
    # Isolated diagonal roman letters must not trigger whole-line OCR.
    ordered = sorted(line["readings"], key=lambda r: r["x0"])
    votes = np.cumsum([0] + [int(supporting_stem(r, baseline)) for r in ordered])
    lean = np.cumsum([0] + [int(r["shear"] - baseline >= 0.14) for r in ordered])
    if not any(
        np.any(
            (votes[size:] - votes[:-size] >= 0.75 * size)
            & (lean[size:] - lean[:-size] >= 0.5 * size)
        )
        for size in range(6, len(ordered) + 1)
    ):
        return []
    recognizer = recognizer or character_line
    witness, chars = recognizer(image, clean=False)
    source_words = list(re.finditer(r"[^\W\d_]+", text))
    pixel_words = list(re.finditer(r"[^\W\d_]+", witness))
    # Changed word counts or weak whole-line agreement make placement unsafe.
    if (
        len(source_words) != len(pixel_words)
        or difflib.SequenceMatcher(None, text, witness).ratio() < 0.9
    ):
        return []
    candidates = []
    word_readings = []
    for word in pixel_words:
        selected = [
            c for c in chars if word.start() <= c["start"] and c["end"] <= word.end()
        ]
        if not selected:
            word_readings.append([])
            continue
        x0 = min(c["bbox"][0] for c in selected)
        x1 = max(c["bbox"][2] for c in selected)
        word_readings.append(
            [r for r in line["readings"] if x0 <= r["x0"] and r["x1"] <= x1]
        )
    for start in range(len(source_words)):
        # Up to three neighboring words supply enough stems for short names.
        for length in range(1, min(3, len(source_words) - start) + 1):
            stop = start + length
            if any(
                not aligned_word(s.group(), p.group())
                for s, p in zip(source_words[start:stop], pixel_words[start:stop])
            ):
                continue
            # Every included word needs its own leaning stems. A long italic
            # word must not drag short upright neighbors into an accepted group.
            if any(
                len(rs) < 2
                or sum(supporting_stem(r, baseline) for r in rs) < 0.6 * len(rs)
                for rs in word_readings[start:stop]
            ):
                continue
            selected = [
                c
                for c in chars
                if c["start"] >= pixel_words[start].start()
                and c["end"] <= pixel_words[stop - 1].end()
            ]
            if not selected:
                continue
            x0 = min(c["bbox"][0] for c in selected)
            x1 = max(c["bbox"][2] for c in selected)
            readings = [r for r in line["readings"] if x0 <= r["x0"] and r["x1"] <= x1]
            if len(readings) < 6:
                continue
            measurement = dict(
                status="measured",
                glyphs=len(readings),
                readings=readings,
                median_shear=float(np.median([r["shear"] for r in readings])),
            )
            votes = [supporting_stem(r, baseline) for r in measurement["readings"]]
            fraction = sum(votes) / len(votes)
            # Relative median lean plus 75% stem agreement allows curved
            # italic letters, while upright words and mixed groups abstain.
            if measurement["median_shear"] - baseline >= 0.14 and fraction >= 0.75:
                candidates.append(
                    dict(
                        start=source_words[start].start(),
                        end=source_words[stop - 1].end(),
                        tags=["em"],
                        measurement=measurement,
                        supporting_fraction=fraction,
                        baseline_shear=baseline,
                        witness=witness,
                    )
                )
    # Keep complete adjacent italic words together after each has supplied
    # independent stem support; styles never cross into unsupported words.
    accepted = []
    for c in sorted(candidates, key=lambda c: (-(c["end"] - c["start"]), c["start"])):
        if not any(c["start"] < a["end"] and c["end"] > a["start"] for a in accepted):
            accepted.append(c)
    return sorted(accepted, key=lambda c: c["start"])


def supporting_stem(reading, baseline):
    # Short word groups need a modest per-stem vote, followed by stronger
    # group median and consensus guards; whole-line verse uses stricter votes.
    return reading["shear"] - baseline >= 0.12 and reading["gain"] >= 1.04


def aligned_word(source, witness):
    """Allow one local OCR edit inside an otherwise aligned complete word."""
    a, b = source.casefold(), witness.casefold()
    if a == b:
        return True
    if min(len(a), len(b)) < 3:
        return False
    # A single substitution (x/z), omission or insertion can still establish
    # word boundaries. This only transfers style and never rewrites spelling.
    return (
        sum(
            max(i2 - i1, j2 - j1)
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes()
            if tag != "equal"
        )
        <= 1
    )


def row_rect(page, row):
    b = row["bbox"]
    rect = pymupdf.Rect(
        b[0] * page.rect.width,
        b[1] * page.rect.height,
        b[2] * page.rect.width,
        b[3] * page.rect.height,
    )
    # One PDF point keeps anti-aliased edges without a neighboring line.
    return (rect + (-1, -1, 1, 1)) & page.rect


@lru_cache(maxsize=1)
def inline_runtime():
    """Bind positive and negative observations to helpers and local OCR data."""
    from common import file_digest
    from ocr_cache import traineddata_digest
    from PIL import __version__ as pillow_version

    return dict(
        helpers={
            name: file_digest(Path(__file__).with_name(name))
            for name in (
                "scan_italics.py",
                "scanned_notes.py",
                "figures.py",
                "scan_pixels.py",
                "book_model.py",
            )
        },
        traineddata=traineddata_digest(),
        tesseract=subprocess.run(
            ["tesseract", "--version"], check=True, capture_output=True, text=True
        ).stdout.splitlines()[0],
        pymupdf=pymupdf.VersionBind,
        numpy=np.__version__,
        pillow=pillow_version,
    )


def attach_scan_inline_italics(rows, page, number, book, audit, work=None):
    """Optional source-pixel inline emphasis with page-local prose controls."""
    from book_model import classify_row
    from common import digest, write_json

    if str(number) in book.get("excluded_pages", {}):
        return
    first = number in {a for a, _, _ in book["chapters"]}
    body = [
        r
        for r in rows
        if classify_row(number, r, book, first)[0] == "body"
        and r.get("kind", "text") == "text"
    ]
    control_rows = [
        r for r in body if len(r["text"]) >= 40 and r["bbox"][2] - r["bbox"][0] >= 0.6
    ][:3]
    if len(control_rows) < 2:
        return
    cache = None
    provenance = None
    if work is not None:
        body_ids = {id(r) for r in body}
        provenance = digest(
            json.dumps(
                # Derived eligible row identities bind layout decisions without
                # invalidating pixel evidence when a cover or title changes.
                dict(
                    rows=rows,
                    body=[i for i, r in enumerate(rows) if id(r) in body_ids],
                    source_sha256=book.get("source_sha256"),
                    scan_raster_only=book.get("scan_raster_only", False),
                    page=number,
                    runtime=inline_runtime(),
                ),
                sort_keys=True,
            ).encode()
        )
        cache = Path(work) / "inline-italics" / f"{number:04}.json"
        if cache.exists():
            record = json.loads(cache.read_text())
            if record.get("provenance") == provenance:
                for index, spans in record["styles"]:
                    rows[index].setdefault("inline", []).extend(spans)
                audit.extend(record["audit"])
                return
    audit_start = len(audit)
    original_styles = {id(r): len(r.get("inline", [])) for r in rows}
    # Decode/render the page once; every row crop stays in original pixels.
    image, _ = crop_artwork_pixels(
        page, page.rect, dpi=400, raster_only=book.get("scan_raster_only", False)
    )

    def crop(row):
        rect = row_rect(page, row)
        return image.crop(
            (
                round(rect.x0 / page.rect.width * image.width),
                round(rect.y0 / page.rect.height * image.height),
                round(rect.x1 / page.rect.width * image.width),
                round(rect.y1 / page.rect.height * image.height),
            )
        )

    controls = [line_slant(crop(r)) for r in control_rows]
    for row in body:
        spans = inline_italic_spans(crop(row), row["text"], controls)
        for span in spans:
            style = {k: span[k] for k in ("start", "end", "tags")}
            if any(
                s["start"] < style["end"] and s["end"] > style["start"]
                for s in row.get("inline", [])
            ):
                continue
            row.setdefault("inline", []).append(style)
            audit.append(
                dict(
                    page=number,
                    kind="scan-inline-italic-evidence",
                    text=row["text"],
                    bbox=row["bbox"],
                    **span,
                    controls=[
                        dict(text=r["text"], bbox=r["bbox"], measurement=m)
                        for r, m in zip(control_rows, controls)
                    ],
                )
            )
    if cache is not None:
        write_json(
            cache,
            dict(
                provenance=provenance,
                audit=audit[audit_start:],
                styles=[
                    (i, r["inline"][original_styles[id(r)] :])
                    for i, r in enumerate(rows)
                    if len(r.get("inline", [])) > original_styles[id(r)]
                ],
            ),
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
        rect = row_rect(page, row)
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
