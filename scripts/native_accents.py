"""Compose separately positioned accents only with glyph and placement evidence."""

import unicodedata

import numpy as np
import pymupdf
from scipy import ndimage

MARKS = {"¨": "\u0308", "´": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303"}


def two_raised_dots(alpha, baseline, size):
    """Recognize a diaeresis outline, including fonts with a false Unicode map."""
    labels, count = ndimage.label(alpha > 128)
    if count != 2:
        return False
    left, right = sorted(ndimage.find_objects(labels), key=lambda box: box[1].start)
    heights = [b[0].stop - b[0].start for b in (left, right)]
    widths = [b[1].stop - b[1].start for b in (left, right)]
    return (
        all(0.04 * size <= v <= 0.18 * size for v in heights + widths)
        and abs(heights[0] - heights[1]) <= 0.03 * size
        and abs(left[0].start - right[0].start) <= 0.03 * size
        and 0.03 * size <= right[1].start - left[1].stop <= 0.3 * size
        and all(b[0].stop < baseline - 0.4 * size for b in (left, right))
    )


def misencoded_diaeresis(page, font_name):
    """Verify the actual embedded glyph, never infer an accent from a euro sign."""
    name = font_name.split("+")[-1]
    fonts = [f for f in page.get_fonts() if f[3].split("+")[-1] == name]
    if len(fonts) != 1:
        return False
    buffer = page.parent.extract_font(fonts[0][0])[3]
    if not buffer:
        return False
    try:
        with pymupdf.open() as document:
            probe = document.new_page(width=120, height=120)
            probe.insert_font(fontname="AccentProbe", fontbuffer=buffer)
            probe.insert_text((10, 100), "€", fontname="AccentProbe", fontsize=100)
            pix = probe.get_pixmap(alpha=True)
            alpha = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, pix.n
            )[:, :, -1]
            return two_raised_dots(alpha, 100, 100)
    except ValueError, RuntimeError:
        return False


def compose_line(line, raw_line, page, number, audit, font_evidence):
    """Return span copies; composed letters retain the base glyph's typography."""
    spans = [{**s} for s in line["spans"]]
    glyphs = [
        (si, ci, ch)
        for si, span in enumerate(raw_line["spans"])
        for ci, ch in enumerate(span["chars"])
    ]
    values = [list(s["text"]) for s in spans]
    for index, (si, ci, mark) in enumerate(glyphs):
        character = mark["c"]
        combining = MARKS.get(character)
        if character == "€":
            font = spans[si]["font"]
            if font not in font_evidence:
                font_evidence[font] = misencoded_diaeresis(page, font)
            if font_evidence[font]:
                combining = "\u0308"
        if not combining:
            continue
        candidates = []
        for neighbour in (index - 1, index + 1):
            if not 0 <= neighbour < len(glyphs):
                continue
            bi, bc, base = glyphs[neighbour]
            size = spans[bi]["size"]
            composed = unicodedata.normalize("NFC", base["c"] + combining)
            if (
                base["c"].isalpha()
                and len(composed) == 1
                and abs(base["origin"][0] - mark["origin"][0]) <= 0.15 * size
                and abs(base["origin"][1] - mark["origin"][1]) <= 0.2 * size
                and min(base["bbox"][2], mark["bbox"][2])
                > max(base["bbox"][0], mark["bbox"][0])
            ):
                candidates.append((bi, bc, base, composed))
        if len(candidates) != 1:
            continue
        bi, bc, base, composed = candidates[0]
        values[si][ci] = ""
        values[bi][bc] = composed
        audit.append(
            dict(
                page=number,
                kind="native-detached-accent",
                before=(
                    character + base["c"]
                    if index + 1 < len(glyphs) and glyphs[index + 1][2] is base
                    else base["c"] + character
                ),
                after=composed,
                bbox=[
                    base["bbox"][0] / page.rect.width,
                    base["bbox"][1] / page.rect.height,
                    base["bbox"][2] / page.rect.width,
                    base["bbox"][3] / page.rect.height,
                ],
                evidence=dict(
                    rule="overlapping-accent-glyph",
                    mark=character,
                    font=spans[si]["font"],
                    verified_outline=character == "€",
                    mark_origin=mark["origin"],
                    base_origin=base["origin"],
                ),
            )
        )
    for span, chars in zip(spans, values):
        span["text"] = "".join(chars)
    return spans


def accent_lines(page, data, number, audit):
    """Return only changed lines; retain the existing full-dict extraction path."""
    candidates = [
        line
        for block in data["blocks"]
        for line in block.get("lines", [])
        if any(c in MARKS or c == "€" for s in line["spans"] for c in s["text"])
    ]
    if not candidates:
        return {}
    raw = page.get_text("rawdict")
    indexed = {
        tuple(line["bbox"]): line
        for block in raw["blocks"]
        for line in block.get("lines", [])
    }
    result, fonts = {}, {}
    for line in candidates:
        glyph_line = indexed.get(tuple(line["bbox"]))
        if not glyph_line or [s["text"] for s in line["spans"]] != [
            "".join(c["c"] for c in s["chars"]) for s in glyph_line["spans"]
        ]:
            continue
        result[id(line)] = compose_line(line, glyph_line, page, number, audit, fonts)
    return result
