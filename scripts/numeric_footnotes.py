"""Recover page-local numbered footnote calls from raised scan glyphs.

Footer numbers supply labels to verify, never unseen body digits. Calls can occur inside a row or at its end; ambiguous glyphs remain review.
"""

import difflib
import inspect
import io
import re
from pathlib import Path

import pymupdf
from PIL import Image
import numpy as np
from scipy.ndimage import distance_transform_edt

from common import apply_edits, digest, read_json, write_json, cache_path
from ocr import nearest, merge_rows
from ocr_cache import traineddata_digest
from scanned_notes import (
    components,
    prose_baseline,
    glyph_readings,
    retry_readings,
    nearby_cap,
    replacement,
)
from vocabulary import TOKEN


def dot_below(stem, boxes, cap):
    # A small dot directly below a raised stem distinguishes ! from serif 1.
    return any(
        0 < dot[3] - dot[1] < 0.25 * cap
        and stem[3] <= dot[1] <= stem[3] + 0.4 * cap
        and abs((dot[0] + dot[2] - stem[0] - stem[2]) / 2) < 0.25 * cap
        for dot in boxes
    )


def printed_terminal_mark(anchor_right, marker_box, boxes, cap, baseline):
    """A question or exclamation mark needs both its tall stroke and dot."""
    return any(
        anchor_right <= stem[0] < marker_box[0]
        and stem[3] < baseline - 0.25 * cap
        and dot_below(stem, boxes, cap)
        for stem in boxes
        if stem[3] - stem[1] > 0.5 * cap
    )


def conflicting_digit(readings, label):
    return any(
        value.isascii()
        and value.isdigit()
        and len(value) == len(label)
        and value != label
        for value in readings
    )


def marker_boxes(boxes, model):
    cap, baseline, slope = model
    return [
        b
        for b in boxes
        if 0.42 * cap < b[3] - b[1] < cap
        and b[3] < baseline + slope * (b[0] + b[2]) / 2 - 0.25 * cap
        and b[2] - b[0] > 0.07 * cap
        and not dot_below(b, boxes, cap)
    ]  # An exclamation stem has a detached dot.


def glyph_signature(glyph):
    return np.asarray(glyph.convert("L").resize((12, 28))) < 150


def font_match(glyph, templates, label, isolated_prefix=False):
    """Use two independently OCR-verified examples of this document's font."""
    if label != "1":
        return None  # Other digit confusions need a multi-class template bank.
    signature = glyph_signature(glyph)
    witnesses = []
    for template in templates:
        reference = template["glyph"]
        # Resizing a short quotation stroke can make it resemble serif 1.
        # Retain physical type size as well as normalized shape evidence.
        if not 0.75 <= glyph.height / reference.height <= 1.35:
            continue
        if abs(glyph.width / glyph.height - reference.width / reference.height) > 0.18:
            # At a known isolated footer prefix, a damaged serif can leave
            # only the full-height stem. This exception never licenses an
            # inline quotation stroke: height, shape and two-page agreement
            # remain required, and the stem must retain one third of its width.
            if not (isolated_prefix and 0.35 <= glyph.width / reference.width < 1):
                continue
        other = glyph_signature(reference)
        if not signature.any() or not other.any():
            continue
        # Symmetric chamfer distance, in fractions of the 28-pixel height.
        # Less than one pixel on average is a close font/scan-shape match.
        score = (
            distance_transform_edt(~other)[signature].mean()
            + distance_transform_edt(~signature)[other].mean()
        ) / 56
        if score <= 0.03:
            witnesses.append(
                dict(
                    page=template["page"],
                    bbox=template["bbox"],
                    score=round(float(score), 6),
                )
            )
    if len({w["page"] for w in witnesses}) >= 2:
        return witnesses
    return None


def footer_label(page, row, recognize):
    """Read the isolated raised footer prefix without masking it as noise."""
    observation = recognize.line_words(page, row, raw=True)
    words = [w for w in observation["words"] if re.search(r"[А-Яа-яA-Za-z]", w[4])]
    if not words:
        return None
    image = Image.open(
        io.BytesIO(page.get_pixmap(dpi=400, colorspace=pymupdf.csGRAY).tobytes("png"))
    ).convert("L")
    cap = max(w[3] - w[1] for w in words) * image.height / page.rect.height
    left = max(0, int(row["bbox"][0] * image.width) - 3)
    top = max(0, int(row["bbox"][1] * image.height) - 3)
    right = int(words[0][0] / page.rect.width * image.width)
    bottom = int(max(w[3] for w in words) / page.rect.height * image.height)
    crop = image.crop((left, top, right, bottom))
    regions = [
        b
        for b in components(crop)
        if 0.3 * cap < b[3] - b[1] < cap and b[2] - b[0] > 0.06 * cap
    ]
    if len(regions) != 1:
        return None
    box = regions[0]
    bbox = [
        (box[0] + left) / image.width,
        (box[1] + top) / image.height,
        (box[2] + left) / image.width,
        (box[3] + top) / image.height,
    ]
    readings = glyph_readings(crop, box, 99)
    digits = [v for v in readings if v.isascii() and v.isdigit() and 1 <= int(v) <= 99]
    # Single-character retries retain the complete original raised glyph.
    if len(digits) < 2 or len(set(digits)) != 1:
        readings += retry_readings(page, bbox)["readings"]
        digits = [
            v for v in readings if v.isascii() and v.isdigit() and 1 <= int(v) <= 99
        ]
    label = digits[0] if len(digits) >= 2 and len(set(digits)) == 1 else None
    return dict(label=label, readings=readings, bbox=bbox, glyph=crop.crop(box))


def marker_strip(image, row, words, page, clearance=0.5, cap_hint=None):
    """Enclose a full raised digit even when the line contains only x-height letters."""
    cap = cap_hint or max(w[3] - w[1] for w in words) * image.height / page.rect.height
    left = max(0, int(row["bbox"][0] * image.width) - 3)
    # Half an x-height clips the top of some full-height superscript digits.
    # Extra clearance finds the entire component; baseline, word position and
    # glyph corroboration still reject neighboring-line ink and quote strokes.
    top = max(
        0,
        int(
            min(w[1] for w in words) * image.height / page.rect.height - clearance * cap
        ),
    )
    bottom = min(
        image.height,
        int(max(w[3] for w in words) * image.height / page.rect.height + 0.15 * cap),
    )
    right = min(image.width, int(row["bbox"][2] * image.width + cap))
    return image.crop((left, top, right, bottom)), left, top, cap


def footer_tokens(text):
    """Compare lexical citation evidence, including common Roman OCR shapes."""
    tokens = []
    for value in re.findall(r"[^\W_]+", text):
        if re.fullmatch(r"[IVXLCDMХУШ]+", value) and len(value) > 1:
            value = value.translate(str.maketrans({"Х": "X", "У": "V", "Ш": "III"}))
        elif re.search(r"[А-Яа-я]", value):
            # Mixed-script lookalikes in a Cyrillic word are the same scan ink.
            value = value.translate(str.maketrans("ACEMOPTXaceopxy", "АСЕМОРТХасеорху"))
        tokens.append(value.lower())
    return tokens[1:] if tokens and re.match(r"^\s*\d{1,2}\s", text) else tokens


def recover_merged_footer(number, rows, cutoff, page, work, recognize, audit):
    """Split a lost multi-line footer only with two cached and fresh witnesses."""
    notes = [r for r in rows if r["bbox"][1] >= cutoff]
    if len(notes) != 1 or notes[0].get("inline"):
        return False
    merged = notes[0]
    banks = []
    for engine in ("tesseract", "rapid"):
        if not (Path(work) / (engine + "-cache.txt")).exists():
            return False
        path = cache_path(work, engine) / f"{number:04}.json"
        if not path.exists():
            return False
        peers = merge_rows(read_json(path)["lines"])
        peers = [
            r
            for r in peers
            if r["bbox"][1] >= cutoff
            and merged["bbox"][1] <= sum(r["bbox"][1::2]) / 2 <= merged["bbox"][3]
        ]
        banks.append(peers)
    if len(banks[0]) < 2 or len(banks[0]) != len(banks[1]):
        return False
    if not all(re.match(r"^\d{1,2}\s", bank[0]["text"]) for bank in banks):
        return False
    proposed, evidence = [], []
    for first, second in zip(*banks):
        if abs(sum(first["bbox"][1::2]) - sum(second["bbox"][1::2])) > 0.016:
            return False  # Half a typical line pitch; never align adjacent lines.
        target = first["text"]
        if footer_tokens(target) != footer_tokens(second["text"]):
            return False
        if len(footer_tokens(target)) < 5:
            return False
        # Prefer an independently observed Latin Roman numeral over its Cyrillic
        # OCR lookalike. This transfers observed spelling, not inferred content.
        for a, b in zip(
            re.findall(r"[^\W_]+", target), re.findall(r"[^\W_]+", second["text"])
        ):
            if (
                re.fullmatch(r"[IVXLCDM]+", b)
                and len(b) > 1
                and footer_tokens(a) == footer_tokens(b)
            ):
                target = re.sub(r"(?<!\w)" + re.escape(a) + r"(?!\w)", b, target)
        observations = [
            recognize.line_words(page, first, raw=True, dpi=dpi)["text"]
            for dpi in (600, 300)
        ]
        # Agreement on the complete citation plus two tight source crops permits
        # minor punctuation/letter noise, but cannot invent a missing clause.
        if not all(
            difflib.SequenceMatcher(
                None, " ".join(footer_tokens(target)), " ".join(footer_tokens(v))
            ).ratio()
            >= 0.95
            for v in observations
        ):
            return False
        proposed.append(
            dict(first, page=number, text=target, recovered_source_row=True)
        )
        evidence.append(
            dict(
                bbox=first["bbox"],
                witnesses=[first["text"], second["text"]],
                readings=observations,
                after=target,
            )
        )
    rows.remove(merged)
    rows.extend(proposed)
    rows.sort(key=lambda r: sum(r["bbox"][1::2]))
    audit.append(
        dict(
            page=number,
            kind="source-verified-merged-footer",
            applied=True,
            before=merged["text"],
            bbox=merged["bbox"],
            recovered=evidence,
        )
    )
    return True


def restore_numbered_bracket_gap(number, rows, page, audit):
    """Restore a note-label space only when the PDF text independently has it."""
    source_blocks = page.get_text("blocks")
    for row in rows:
        match = re.match(r"^(\d{1,2})\[", row["text"])
        if not match:
            continue
        label = match[1]
        if not any(
            abs(block[1] / page.rect.height - row["bbox"][1]) < 0.015
            and re.match(rf"^{re.escape(label)}\s+\[", block[4])
            for block in source_blocks
        ):
            continue
        before = label + "["
        apply_edits(
            [row],
            [
                dict(
                    page=number,
                    before=before,
                    after=label + " [",
                    prefix="",
                    suffix=row["text"][len(before) :],
                    count=1,
                    whole_word=False,
                )
            ],
            audit,
            "source-numbered-bracket-gap",
        )


def recover_tail_quote(
    number,
    word,
    row,
    digit,
    boxes,
    cap,
    left,
    top,
    image,
    page,
    work,
    cache,
    provenance,
):
    """Restore an omitted closing quote from scan ink and a cached witness."""
    peers = []
    for engine in ("rapid", "tesseract"):
        if not (Path(work) / (engine + "-cache.txt")).exists():
            continue
        path = cache_path(work, engine) / f"{number:04}.json"
        if not path.exists():
            continue
        for peer in read_json(path)["lines"]:
            if abs(
                sum(peer["bbox"][1::2]) - sum(row["bbox"][1::2])
            ) <= 0.02 and re.search(
                r"(?<!\w)" + re.escape(word) + r"""[.!?…]*["”»]""", peer["text"], re.I
            ):
                peers.append(dict(engine=engine, text=peer["text"], bbox=peer["bbox"]))
    if not peers:
        return None
    # A closing double quote has two short raised strokes directly before the
    # full-height digit. A lone apostrophe or distant punctuation cannot qualify.
    strokes = sorted(
        (
            b
            for b in boxes
            if 0.2 * (digit[3] - digit[1]) < b[3] - b[1] < 0.7 * (digit[3] - digit[1])
            and 0 < digit[0] - b[2] < 1.2 * cap
            and abs(b[3] - digit[3]) <= 0.2 * cap
        ),
        key=lambda b: b[0],
    )
    if len(strokes) != 2 or not 0 <= strokes[1][0] - strokes[0][2] <= 0.3 * cap:
        return None
    box = (
        strokes[0][0],
        min(b[1] for b in strokes),
        strokes[1][2],
        max(b[3] for b in strokes),
    )
    bbox = [
        (box[0] + left) / image.width,
        (box[1] + top) / image.height,
        (box[2] + left) / image.width,
        (box[3] + top) / image.height,
    ]
    path = cache / (
        "quote-"
        + digest(
            image.crop(
                (box[0] + left, box[1] + top, box[2] + left, box[3] + top)
            ).tobytes()
            + repr(bbox).encode()
            + digest(image.tobytes()).encode()
            + provenance.encode()
        )
        + ".json"
    )
    if path.exists():
        readings = read_json(path)["readings"]
    else:
        readings = retry_readings(page, bbox)["readings"]
        write_json(path, dict(readings=readings, bbox=bbox, provenance=provenance))
    # A recognizer may append a stray question mark to a positively read quote;
    # the two observed quote readings and cached spelling still must agree.
    if sum(bool(re.fullmatch(r'["”»]\??', v)) for v in readings) < 2:
        return None
    return dict(bbox=bbox, readings=readings, witnesses=peers)


def anchored_baseline(boxes, cap, row, top, image):
    # A tall one-word OCR box can borrow a neighboring row's cap height. Fit
    # normal letters whose bottoms lie within this row, keeping raised ink out
    # of the baseline vote while retaining it for subsequent glyph detection.
    lower = row["bbox"][1] * image.height - top + 0.5 * cap
    upper = row["bbox"][3] * image.height - top
    return prose_baseline([b for b in boxes if lower <= b[3] <= upper], cap)


def has_marker_character(observation, bbox, page):
    """Fresh character geometry must locate a punctuation/digit-like glyph.

    Thirty percent of the smaller box permits OCR to see only a glyph's lower
    serif, while still excluding a quote or character on a neighboring row.
    """
    target = [
        v * (page.rect.width if i % 2 == 0 else page.rect.height)
        for i, v in enumerate(bbox)
    ]
    return any(
        all(
            min(target[i + 2], c["bbox"][i + 2]) - max(target[i], c["bbox"][i])
            >= 0.3 * min(target[i + 2] - target[i], c["bbox"][i + 2] - c["bbox"][i])
            for i in (0, 1)
        )
        and (
            not observation["text"][c["start"] : c["end"]].isalpha()
            or observation["text"][c["start"] : c["end"]] in {"I", "i", "l", "|"}
        )
        for c in observation["characters"]
    )


def attach_marker(change):
    """Keep a source-verified superscript adjacent to its preceding punctuation."""
    offset = change["marker_start"] - change["start"]
    attached = change["after"][:offset].rstrip()
    removed = offset - len(attached)
    if removed:
        change["after"] = attached + change["after"][offset:]
        change["marker_start"] -= removed
        change["marker_end"] -= removed
    trailing = re.search(r"\s+$", change["before"])
    if trailing and not change["after"][-1].isspace():
        change["after"] += trailing.group()
    return change


def preserve_source_marker_gap(change, witness, symbol_start):
    """Retain a printed gap between a raised call and the next quotation."""
    if not re.match(r"\s+[\"'“‘]", witness[symbol_start + 1 :]):
        return change
    offset = change["marker_end"] - change["start"]
    if offset < len(change["after"]) and change["after"][offset] in "\"'“‘":
        change["after"] = change["after"][:offset] + " " + change["after"][offset:]
    return change


def verified_caret_gap(change):
    """Allow one OCR symbol mistaken for a pixel-verified source caret.

    The ordinary gap policy deliberately protects quotation marks. A raised
    numeral often becomes a spurious closing quote in the primary OCR, so the
    source PDF's adjacent punctuation must also bound that substitution.
    """
    from citation_research import preserve_gap

    guarded = preserve_gap(change)
    if guarded:
        return guarded
    before, after = change["before"], change["after"]
    marker = after[change["marker_start"] - change["start"]]
    if (
        len(before) > 8
        or len(after) > 8
        or not marker.isdigit()
        or any(c.isalnum() and c not in "IltoOa" for c in before)
        or any(c.isdigit() for c in before)
    ):
        return None
    source_marks = re.sub(r"\s+", "", after.replace(marker, "", 1))
    observed_marks = re.sub(r"\s+", "", before)
    normalized = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
    source_marks = source_marks.translate(normalized)
    observed_marks = observed_marks.translate(normalized)
    if not source_marks or any(c not in ".!?;:,)]'\"" for c in source_marks):
        return None
    # At most one extra OCR character may masquerade as the raised digit.
    # One source punctuation mark may be absent from OCR (e.g. Hell^ -> Hell.1).
    matcher = difflib.SequenceMatcher(None, observed_marks, source_marks)
    removed = "".join(
        observed_marks[a:aa]
        for op, a, aa, _, _ in matcher.get_opcodes()
        if op != "equal"
    )
    inserted = "".join(
        source_marks[b:bb] for op, _, _, b, bb in matcher.get_opcodes() if op != "equal"
    )
    paired_punctuation_error = bool(
        re.fullmatch(r"[,:;][*?+°]", observed_marks) and source_marks == "."
    )
    if not paired_punctuation_error and (
        len(removed) > 1
        or len(inserted) > 1
        or any(c not in "*?+°ºIltoOa'\"!>" for c in removed)
    ):
        return None
    return change


def anchored_caret_change(primary, witness, caret_start, label):
    """Map a tiny caret gap when one neighboring OCR word is misspelled."""
    left_words = list(re.finditer(r"[A-Za-z]{3,}", witness[:caret_start]))
    right = re.search(r"[A-Za-z]+", witness[caret_start + 1 :])
    if not left_words:
        return None
    left = left_words[-1]
    targets = list(re.finditer(r"[A-Za-z]+", primary))
    matches = [
        target
        for target in targets
        if difflib.SequenceMatcher(
            None, left.group().casefold(), target.group().casefold()
        ).ratio()
        >= 0.75
    ]
    if len(matches) != 1:
        return None
    target_left = matches[0]
    target_right = None
    source_right_start = len(witness)
    if right:
        source_right_start = caret_start + 1 + right.start()
        right_word = right.group()
        following = [
            target
            for target in targets
            if target.start() > target_left.end()
            and target.group().casefold() == right_word.casefold()
        ]
        if len(following) != 1:
            return None
        target_right = following[0]
    start = target_left.end()
    end = target_right.start() if target_right else len(primary)
    after = witness[left.end() : source_right_start].replace("^", label, 1)
    if after.count(label) != 1 or len(primary[start:end]) > 16:
        return None
    offset = after.index(label)
    return dict(
        start=start,
        end=end,
        before=primary[start:end],
        after=after,
        marker_start=start + offset,
        marker_end=start + offset + len(label),
    )


def source_dot_before_marker(box, boxes, scale):
    """Find the separate baseline period immediately before a raised glyph."""
    x = box[0] / scale
    bottom = box[3] / scale
    return any(
        0.5 < x - (dot[0] + dot[2]) / (2 * scale) < 4
        and 1 < dot[1] / scale - bottom < 4
        and 0.5 <= (dot[2] - dot[0]) / scale < 2.5
        and 0.5 <= (dot[3] - dot[1]) / scale < 2.5
        for dot in boxes
    )


def embedded_caret_calls(
    page, rows, cutoff, label, templates=(), include_confusions=False
):
    """Verify an ABBYY marker hint against a raised source glyph and OCR gap.

    The hidden text supplies a location, not the digit. This bounded fallback
    runs only where the wider raster search found no call.
    """
    if not hasattr(page, "get_text"):
        return []
    carets = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            witness, chars = "", []
            for span in line["spans"]:
                for char in span["chars"]:
                    start = len(witness)
                    witness += char["c"]
                    chars.append(dict(start=start, end=len(witness), bbox=char["bbox"]))
            for char in chars:
                symbol = witness[char["start"] : char["end"]]
                if symbol != "^" and not (
                    include_confusions
                    and symbol in "d}“”'\"?®-"
                    and (char["end"] == len(witness) or witness[char["end"]].isspace())
                ):
                    continue
                x, y, xx, yy = char["bbox"]
                middle = (y + yy) / (2 * page.rect.height)
                if middle >= cutoff:
                    continue
                matching = [
                    row
                    for row in rows
                    if row["bbox"][1] < middle < row["bbox"][3]
                    and row["bbox"][1] < cutoff
                    and len(row["text"]) >= 5
                    and row.get("kind", "text") == "text"
                ]
                if len(matching) == 1:
                    carets.append(
                        (
                            matching[0],
                            witness,
                            chars,
                            char["bbox"],
                            symbol,
                            char["start"],
                        )
                    )
    if not carets:
        return []
    image = Image.open(
        io.BytesIO(page.get_pixmap(dpi=400, colorspace=pymupdf.csGRAY).tobytes("png"))
    ).convert("L")
    scale = image.width / page.rect.width
    boxes = components(image)
    candidates = []
    for row, witness, chars, caret, symbol, symbol_start in carets:
        x, y, xx, yy = caret
        nearby = []
        for box in boxes:
            bx, by, bxx, byy = (value / scale for value in box)
            if (
                x - 1 < (bx + bxx) / 2 < xx + 1
                and y - 1 < (by + byy) / 2 < yy + 1
                and 0.7 <= byy - by <= 7
            ):
                nearby.append(box)
        options = []
        for box in nearby:
            height = (box[3] - box[1]) / scale
            center_y = (box[1] + box[3]) / (2 * scale)
            if (
                2 <= height <= 6
                and center_y < y + 0.6 * (yy - y)
                and (symbol == "^" or height >= 3.5)
            ):
                options.append(box)
        # A printed superscript 2 is often two disconnected vertical strokes.
        # Nearby quotation marks form side-by-side strokes and cannot pass.
        for upper in nearby:
            for lower in nearby:
                if lower[1] <= upper[1] or lower[1] - upper[3] > 1.3 * scale:
                    continue
                if (upper[1] + upper[3]) / (2 * scale) >= y + 0.6 * (yy - y):
                    continue
                overlap = min(upper[2], lower[2]) - max(upper[0], lower[0])
                if overlap < 0.55 * min(upper[2] - upper[0], lower[2] - lower[0]):
                    continue
                union = (
                    min(upper[0], lower[0]),
                    upper[1],
                    max(upper[2], lower[2]),
                    lower[3],
                )
                height = (union[3] - union[1]) / scale
                if 3.5 <= height <= 6 and union[2] - union[0] < union[3] - union[1]:
                    options.append(union)
        for box in options:
            bx, by, bxx, byy = (value / scale for value in box)
            readings = glyph_readings(image, box, int(label))
            numeric = [
                value for value in readings if value.isascii() and value.isdigit()
            ]
            if any(value != label for value in numeric):
                continue
            witnesses = None
            if readings.count(label) < 2:
                witnesses = font_match(
                    image.crop(box),
                    [t for t in templates if t["label"] == label],
                    label,
                )
                if not witnesses:
                    continue
            surrogate = (
                witness
                if symbol == "^"
                else witness[:symbol_start] + "^" + witness[symbol_start + 1 :]
            )
            dot_witness = (
                symbol != "^"
                and symbol_start > 0
                and witness[symbol_start - 1].isalpha()
                and source_dot_before_marker(box, boxes, scale)
            )
            if dot_witness:
                surrogate = witness[:symbol_start] + ".^" + witness[symbol_start + 1 :]
                change = anchored_caret_change(
                    row["text"], surrogate, symbol_start + 1, label
                )
            else:
                change = replacement(row["text"], surrogate, chars, caret, label)
            if not change:
                change = anchored_caret_change(
                    row["text"],
                    surrogate,
                    symbol_start + int(dot_witness),
                    label,
                )
            change = verified_caret_gap(change) if change else None
            if change:
                change = attach_marker(change)
                change = preserve_source_marker_gap(change, witness, symbol_start)
            if not change or any(
                "sup" in style.get("tags", [])
                and style["start"] < change["end"]
                and style["end"] > change["start"]
                for style in row.get("inline", [])
            ):
                continue
            candidates.append(
                dict(
                    row=row,
                    change=change,
                    bbox=[
                        bx / page.rect.width,
                        by / page.rect.height,
                        bxx / page.rect.width,
                        byy / page.rect.height,
                    ],
                    embedded_bbox=list(caret),
                    embedded_char=symbol,
                    readings=readings,
                    **({"font_witnesses": witnesses} if witnesses else {}),
                )
            )
    return candidates


def footer_entry_labels(rows, cutoff):
    """Read a complete numbered run at the source footer's hanging margin."""
    notes = sorted(
        (row for row in rows if row["bbox"][1] >= cutoff),
        key=lambda row: row["bbox"][1],
    )
    if not notes:
        return []
    labels = []
    margin = notes[0]["bbox"][0]
    for row in notes:
        match = re.match(r"^\s*([1-9])(?=\s|\[)", row["text"])
        if match and abs(row["bbox"][0] - margin) < 0.04:
            labels.append(match[1])
    return labels if labels == [str(i) for i in range(1, len(labels) + 1)] else []


def recover_numeric_footnotes(pages, doc, book, work, audit, recognize):
    cache = Path(work) / "numeric-footnote-crops"
    provenance = digest(
        Path(inspect.getsourcefile(glyph_readings)).read_bytes()
        + traineddata_digest().encode()
        + recognize.version.encode()
    )
    found, unresolved, proposals, templates, alternates = [], [], [], [], []
    footer_evidence = {}
    for key, cutoff in book.get("note_starts", {}).items():
        restore_numbered_bracket_gap(
            int(key),
            [r for r in pages[int(key)] if r["bbox"][1] >= cutoff],
            doc[int(key) - 1],
            audit,
        )
        notes = [r for r in pages[int(key)] if r["bbox"][1] >= cutoff]
        if notes:
            number = int(key)
            page = doc[number - 1]
            first = min(notes, key=lambda r: r["bbox"][1])
            evidence = footer_label(page, first, recognize)
            if evidence is None and recover_merged_footer(
                number, pages[number], cutoff, page, work, recognize, audit
            ):
                first = min(
                    (r for r in pages[number] if r["bbox"][1] >= cutoff),
                    key=lambda r: r["bbox"][1],
                )
                evidence = footer_label(page, first, recognize)
            footer_evidence[number] = evidence
    footer_templates = [
        dict(page=n, **e) for n, e in footer_evidence.items() if e and e["label"] == "1"
    ]
    for key, cutoff in book.get("note_starts", {}).items():
        if key in book.get("note_continuations", {}):
            continue
        number = int(key)
        notes = [r for r in pages[number] if r["bbox"][1] >= cutoff]
        if not notes:
            continue
        first = min(notes, key=lambda r: r["bbox"][1])
        prefix = re.match(r"^\s*(\d{1,2})(?=\s|\[)", first["text"])
        evidence = footer_evidence.get(number)
        label = evidence and evidence["label"]
        font_witnesses = None
        if evidence and label != "1":
            font_witnesses = font_match(
                evidence["glyph"],
                [t for t in footer_templates if t["page"] != number],
                "1",
                isolated_prefix=True,
            )
            if font_witnesses:
                label = "1"
        if not label:
            if prefix:
                label = prefix[1]
            else:
                unresolved.append(
                    dict(
                        page=number,
                        reason="unverified-footer-label",
                        readings=evidence and evidence["readings"],
                    )
                )
                continue
        if not prefix or prefix[1] != label:
            old = re.match(r"^\s*(?:\d{1,2}|[^\w\s]+)\s*", first["text"])
            if not old:
                continue
            apply_edits(
                [first],
                [
                    dict(
                        page=number,
                        before=old[0],
                        after=label + " ",
                        prefix="",
                        suffix=first["text"][old.end() :],
                        count=1,
                        whole_word=False,
                    )
                ],
                audit,
                "verified-footer-label",
            )
            audit.append(
                dict(
                    page=number,
                    kind="isolated-footer-label",
                    label=label,
                    readings=evidence["readings"],
                    bbox=evidence["bbox"],
                    font_witnesses=font_witnesses,
                )
            )
        body = [
            r
            for r in pages[number]
            if r["bbox"][1] < cutoff
            and len(r["text"]) >= 5
            and r.get("kind", "text") == "text"
        ]
        page = doc[number - 1]
        image = Image.open(
            io.BytesIO(
                page.get_pixmap(dpi=400, colorspace=pymupdf.csGRAY).tobytes("png")
            )
        ).convert("L")
        page_fingerprint = digest(image.tobytes())
        candidates = []
        for row in body:
            tokens = list(TOKEN.finditer(row["text"]))
            if not tokens:
                continue
            for index in range(len(tokens)):
                last = tokens[index]
                following = tokens[index + 1] if index + 1 < len(tokens) else None
                gap_end = following.start() if following else len(row["text"])
                tail = row["text"][last.end() : gap_end]
                # This bounded recovery pass needs a quotation/punctuation tail;
                # raised interior mathematical numbers are not trailing citations.
                if len(re.sub(r"\s", "", tail)) > 8 or not re.search(
                    r'[.!?;:,"”’*†‡\d]', tail
                ):
                    continue
                if any(value != label for value in re.findall(r"\d+", tail)):
                    continue  # A year or other ordinary number is not this note.
                observation = recognize.line_words(page, row, raw=True)
                words = observation["words"]
                if not words:
                    continue
                # Fresh character boxes isolate this baseline. Primary OCR boxes
                # often overlap the preceding verse line and cannot define the crop.
                crop, left, top, cap = marker_strip(image, row, words, page)
                boxes = components(crop)
                model = prose_baseline(boxes, cap)
                short_retry = False
                if not model and len(tokens) <= 3:
                    hint = nearby_cap(row, body, image)
                    if hint:
                        crop, left, top, cap = marker_strip(
                            image, row, words, page, clearance=1.2, cap_hint=hint
                        )
                        boxes = components(crop)
                        model = anchored_baseline(boxes, cap, row, top, image)
                        short_retry = True
                if not model:
                    continue
                _, baseline, slope = model
                # Keep components separate: a nearby closing quote must not be
                # merged into a one-digit citation. Small serif 1s can approach
                # x-height, so vertical displacement is the stronger constraint.
                regions = marker_boxes(boxes, model)
                adaptive = short_retry
                # Expand only for a literal primary-OCR number in this gap that
                # has no candidate in the ordinary strip. This preserves the
                # established search instead of admitting preceding-row ink.
                if not regions and re.findall(r"\d+", tail) == [label]:
                    # The same bounded retry retains a trailing full stop that
                    # the primary box may cut off just after the raised digit.
                    observation = recognize.line_words(
                        page,
                        dict(
                            row,
                            bbox=[
                                *row["bbox"][:2],
                                min(1, row["bbox"][2] + 0.025),
                                row["bbox"][3],
                            ],
                        ),
                        raw=True,
                    )
                    words = observation["words"]
                    if not words:
                        continue
                    crop, left, top, cap = marker_strip(
                        image, row, words, page, clearance=1.2
                    )
                    boxes = components(crop)
                    model = prose_baseline(boxes, cap)
                    if not model:
                        continue
                    regions = marker_boxes(boxes, model)
                    adaptive = True
                anchors = []
                for token in TOKEN.finditer(observation["text"]):
                    if (
                        difflib.SequenceMatcher(
                            None, token.group().lower(), last.group().lower()
                        ).ratio()
                        < 0.75
                    ):
                        continue
                    chars = [
                        c
                        for c in observation["characters"]
                        if token.start() <= c["start"] < token.end()
                    ]
                    if chars:
                        anchors.append(
                            [
                                min(c["bbox"][0] for c in chars),
                                min(c["bbox"][1] for c in chars),
                                max(c["bbox"][2] for c in chars),
                                max(c["bbox"][3] for c in chars),
                                token.group(),
                            ]
                        )
                if len(anchors) != 1:
                    continue
                anchor = anchors[0]
                next_anchor = None
                if following:
                    next_words = [
                        w
                        for w in words
                        if w[0] >= anchor[2]
                        and difflib.SequenceMatcher(
                            None, w[4].lower(), following.group().lower()
                        ).ratio()
                        >= 0.75
                    ]
                    if len(next_words) != 1 and index + 2 < len(tokens):
                        # A following short function word may itself be wrong
                        # (По/Но). Its next exact lexical neighbor anchors the
                        # physical boundary without changing either word.
                        next_words = []
                        for wi, w in enumerate(words[1:], 1):
                            if (
                                w[0] > anchor[2]
                                and w[4].lower() == tokens[index + 2].group().lower()
                            ):
                                next_words.append(words[wi - 1])
                    if len(next_words) != 1:
                        continue
                    next_anchor = next_words[0]
                for box in regions:
                    bbox = [
                        (box[0] + left) / image.width,
                        (box[1] + top) / image.height,
                        (box[2] + left) / image.width,
                        (box[3] + top) / image.height,
                    ]
                    # The marker must follow the anchored last word physically.
                    # OCR can merge the raised digit into the last word box. Its
                    # physical center must still be in that word's terminal zone.
                    if (bbox[0] + bbox[2]) / 2 * page.rect.width < anchor[2] - 0.2:
                        continue
                    if next_anchor and bbox[2] * page.rect.width >= next_anchor[0]:
                        continue
                    if adaptive and not short_retry:
                        # Fresh punctuation/digit character geometry locates the
                        # reported mark; a crop from another row cannot supply it.
                        if not has_marker_character(observation, bbox, page):
                            continue
                    glyph = crop.crop(box)
                    key_hash = digest(
                        # Raw OCR caching depends on all pixels the recognizers
                        # see and their resources, not the selection algorithm.
                        crop.crop(
                            (box[0] - 2, box[1] - 2, box[2] + 2, box[3] + 2)
                        ).tobytes()
                        + repr((box, bbox, label, page_fingerprint)).encode()
                        + provenance.encode()
                    )
                    path = cache / (key_hash + ".json")
                    if path.exists():
                        readings = read_json(path)["readings"]
                    else:
                        readings = glyph_readings(crop, box, int(label))
                        if readings.count(label) < 2:
                            readings += retry_readings(page, bbox)["readings"]
                        write_json(
                            path,
                            dict(readings=readings, bbox=bbox, provenance=provenance),
                        )
                    # A rival OCR digit is decisive only when that digit also
                    # labels a separate note in the printed footer. On pages
                    # with a sole note 1, font evidence can still rescue 1
                    # from a noisy OCR reading of 2 or 4.
                    numeric_readings = [
                        value
                        for value in readings
                        if value.isascii() and value.isdigit() and len(value) == 1
                    ]
                    footer_labels = footer_entry_labels(pages[number], cutoff)
                    conflicts_footer = any(
                        value != label and value in footer_labels
                        for value in numeric_readings
                    )
                    verified = readings.count(label) >= 2 and not conflicts_footer
                    alternate_label = (
                        numeric_readings[0]
                        if len(numeric_readings) >= 2
                        and len(set(numeric_readings)) == 1
                        and numeric_readings[0] != label
                        and numeric_readings[0] in footer_labels
                        else None
                    )
                    if conflicts_footer and not alternate_label:
                        continue
                    ambiguous_one = label == "1" and any(
                        v
                        in (
                            {"I", "i", "l", "L", "t", "|"}
                            if adaptive
                            else {"I", "l", "L", "t", "|"}
                        )
                        for v in readings
                    )
                    if not verified and not ambiguous_one and label != "1":
                        continue
                    if (
                        not verified
                        and not ambiguous_one
                        and not short_retry
                        and not has_marker_character(observation, bbox, page)
                    ):
                        continue
                    # For one, the document's independently verified font can
                    # resolve even conflicting OCR (e.g. 1 read as c or 2).
                    # Quote strokes still fail physical-size and shape matching.
                    # The adaptive crop must match independently established
                    # narrow-strip font samples, even if its retries read 1.
                    direct = verified and not adaptive
                    if direct:
                        templates.append(
                            dict(page=number, label=label, bbox=bbox, glyph=glyph)
                        )
                    witness = observation["text"]
                    match = list(re.finditer(re.escape(anchor[4]), witness, re.I))[-1]
                    witness_end = len(witness)
                    if next_anchor:
                        next_matches = list(
                            re.finditer(
                                re.escape(next_anchor[4]), witness[match.end() :], re.I
                            )
                        )
                        if not next_matches:
                            continue
                        witness_end = match.end() + next_matches[0].start()
                    suffix = witness[match.end() : witness_end]
                    punctuation = re.sub(r"[\d*†‡\s]", "", tail)
                    # OCR may call the raised 1 another exclamation mark. Remove
                    # only punctuation whose fresh character box overlaps this
                    # verified glyph, preserving actual neighbouring punctuation.
                    before_punctuation, after_punctuation = "", ""
                    for char in observation["characters"]:
                        if char["start"] < match.end() or char["start"] >= witness_end:
                            continue
                        value = witness[char["start"] : char["end"]]
                        center = (
                            (char["bbox"][0] + char["bbox"][2]) / 2 / page.rect.width
                        )
                        if (
                            char["bbox"][0] / page.rect.width < bbox[2]
                            and char["bbox"][2] / page.rect.width > bbox[0]
                        ):
                            continue
                        if not re.fullmatch(r'[.!?;:,"”’—–-]', value):
                            continue
                        if center < bbox[0]:
                            before_punctuation += value
                        else:
                            after_punctuation += (
                                " " + value if value in "—–-" else value
                            )
                    if before_punctuation or after_punctuation:
                        punctuation = before_punctuation
                    elif not re.search(r'["”]', punctuation) and re.search(
                        r'["”]', suffix
                    ):
                        punctuation += '"'
                    quote_evidence = None
                    if short_retry and not re.search(r'["”]', punctuation):
                        quote_evidence = recover_tail_quote(
                            number,
                            last.group(),
                            row,
                            box,
                            boxes,
                            cap,
                            left,
                            top,
                            image,
                            page,
                            work,
                            cache,
                            provenance,
                        )
                        if quote_evidence:
                            punctuation += '"'
                    # A separately printed full stop may lie outside the primary
                    # OCR row box. Require a tiny baseline dot just after the digit.
                    if not after_punctuation and any(
                        box[2] < dot[0] <= box[2] + 0.7 * cap
                        and dot[2] - dot[0] < 0.35 * cap
                        and dot[3] - dot[1] < 0.35 * cap
                        and abs(dot[3] - baseline) <= 0.2 * cap
                        for dot in boxes
                    ):
                        after_punctuation = "."
                    if any(
                        mark in punctuation for mark in "!?"
                    ) and not printed_terminal_mark(
                        anchor[2] * image.width / page.rect.width - left,
                        box,
                        boxes,
                        cap,
                        baseline,
                    ):
                        punctuation = punctuation.replace("!", "").replace("?", "")
                    if (
                        punctuation == after_punctuation == "."
                        and not before_punctuation
                    ):
                        punctuation = ""  # The sole printed dot follows the marker.
                    after = (
                        punctuation
                        + label
                        + after_punctuation
                        + (" " if following else "")
                    )
                    candidates.append(
                        dict(
                            row=row,
                            verified=direct,
                            glyph=glyph,
                            start=last.end(),
                            end=gap_end,
                            before=tail,
                            after=after,
                            marker_start=last.end() + len(punctuation),
                            bbox=bbox,
                            readings=readings,
                            alternate_label=alternate_label,
                            **(
                                {"quote_evidence": quote_evidence}
                                if quote_evidence
                                else {}
                            ),
                        )
                    )
        proposals.append((number, label, candidates))
    for number, label, candidates in proposals:
        accepted = []
        for candidate in candidates:
            glyph = candidate.pop("glyph")
            alternate_label = candidate.pop("alternate_label")
            if alternate_label:
                candidate.pop("verified")
                offset = candidate["marker_start"] - candidate["start"]
                candidate["after"] = (
                    candidate["after"][:offset]
                    + alternate_label
                    + candidate["after"][offset + len(label) :]
                )
                candidate["observed_label"] = alternate_label
                alternates.append((number, candidate))
                continue
            if not candidate.pop("verified"):
                matching = font_match(
                    glyph,
                    [
                        t
                        for t in templates
                        if t["label"] == label and t["page"] != number
                    ],
                    label,
                )
                if not matching:
                    continue
                candidate["font_witnesses"] = matching
            accepted.append(candidate)
        candidates = accepted
        # Overlapping masks can find the same glyph twice; source identity and
        # physical overlap deduplicate it without turning two masks into votes.
        unique = []
        for candidate in candidates:
            if not any(
                c["row"] is candidate["row"]
                and abs(c["bbox"][0] - candidate["bbox"][0]) < 0.005
                for c in unique
            ):
                unique.append(candidate)
        if len(unique) == 0:
            caret = embedded_caret_calls(
                doc[number - 1],
                pages[number],
                book["note_starts"][str(number)],
                label,
                templates,
            )
            if len(caret) == 1:
                candidate = caret[0]
                row = candidate["row"]
                change = candidate["change"]
                original = row["text"]
                apply_edits(
                    [row],
                    [
                        dict(
                            page=number,
                            before=change["before"],
                            after=change["after"],
                            prefix=original[: change["start"]],
                            suffix=original[change["end"] :],
                            count=1,
                            whole_word=False,
                        )
                    ],
                    audit,
                    "embedded-caret-footnote-overlay",
                )
                row.setdefault("inline", []).append(
                    dict(
                        start=change["marker_start"],
                        end=change["marker_end"],
                        tags=["sup"],
                    )
                )
                record = dict(
                    page=number,
                    label=label,
                    bbox=candidate["bbox"],
                    embedded_bbox=candidate["embedded_bbox"],
                    readings=candidate["readings"],
                    **(
                        {"font_witnesses": candidate["font_witnesses"]}
                        if candidate.get("font_witnesses")
                        else {}
                    ),
                    before=change["before"],
                    after=change["after"],
                    marker_start=change["marker_start"],
                    method="embedded-caret-source-glyph",
                )
                audit.append(
                    dict(record, kind="raised-page-footnote-call", applied=True)
                )
                found.append(record)
                continue
        if len(unique) != 1:
            unresolved.append(
                dict(
                    page=number,
                    label=label,
                    reason="nonunique-verified-call",
                    candidates=len(unique),
                    locations=[
                        dict(
                            text=c["row"]["text"],
                            bbox=c["bbox"],
                            readings=c["readings"],
                        )
                        for c in unique
                    ],
                )
            )
            continue
        candidate = unique[0]
        row = candidate.pop("row")
        # A bounded gap overlay preserves following prose and any inline
        # ranges when the call occurs in the middle of a source row.
        original = row["text"]
        start, end = candidate["start"], candidate["end"]
        apply_edits(
            [row],
            [
                dict(
                    page=number,
                    before=candidate["before"],
                    after=candidate["after"],
                    prefix=original[:start],
                    suffix=original[end:],
                    count=1,
                    whole_word=False,
                )
            ],
            audit,
            "raised-page-footnote-overlay",
        )
        row.setdefault("inline", []).append(
            dict(
                start=candidate["marker_start"],
                end=candidate["marker_start"] + len(label),
                tags=["sup"],
            )
        )
        record = dict(page=number, label=label, **candidate)
        audit.append(dict(record, kind="raised-page-footnote-call", applied=True))
        found.append(record)
    # Clear conflicting digit readings belong to their own numbered footer
    # entry. They must never be shape-matched back to label 1.
    for number, label in sorted({(n, c["observed_label"]) for n, c in alternates}):
        if any(item["page"] == number and item["label"] == label for item in found):
            continue
        options = [
            candidate
            for n, candidate in alternates
            if n == number and candidate["observed_label"] == label
        ]
        if len(options) != 1:
            continue
        candidate = dict(options[0])
        row = candidate.pop("row")
        candidate.pop("observed_label")
        original = row["text"]
        start, end = candidate["start"], candidate["end"]
        apply_edits(
            [row],
            [
                dict(
                    page=number,
                    before=candidate["before"],
                    after=candidate["after"],
                    prefix=original[:start],
                    suffix=original[end:],
                    count=1,
                    whole_word=False,
                )
            ],
            audit,
            "raised-page-footnote-overlay",
        )
        row.setdefault("inline", []).append(
            dict(
                start=candidate["marker_start"],
                end=candidate["marker_start"] + len(label),
                tags=["sup"],
            )
        )
        record = dict(
            page=number, label=label, method="observed-other-digit", **candidate
        )
        audit.append(dict(record, kind="raised-page-footnote-call", applied=True))
        found.append(record)
    # A physical page may contain several independently numbered footnotes.
    # The first, wider scan above stays intact; source carets then fill only
    # labels that are still missing, one verified glyph per footer entry.
    for key, cutoff in book.get("note_starts", {}).items():
        if key in book.get("note_continuations", {}):
            continue
        number = int(key)
        labels = footer_entry_labels(pages[number], cutoff)
        present = {item["label"] for item in found if item["page"] == number}
        for label in labels:
            if label in present:
                continue
            candidates = embedded_caret_calls(
                doc[number - 1],
                pages[number],
                cutoff,
                label,
                templates,
                include_confusions=True,
            )
            candidates = [
                candidate
                for candidate in candidates
                if not any(
                    item["page"] == number
                    and abs(item["bbox"][0] - candidate["bbox"][0]) < 0.005
                    and abs(item["bbox"][1] - candidate["bbox"][1]) < 0.005
                    for item in found
                )
            ]
            # Independent OCR agreement is stronger evidence than a font-only
            # match, which may also admit a nearby punctuation component.
            agreeing = [
                candidate
                for candidate in candidates
                if candidate["readings"].count(label) >= 2
            ]
            if agreeing:
                candidates = agreeing
            if len(candidates) != 1:
                continue
            candidate = candidates[0]
            row, change = candidate["row"], candidate["change"]
            original = row["text"]
            if change["before"] != change["after"]:
                apply_edits(
                    [row],
                    [
                        dict(
                            page=number,
                            before=change["before"],
                            after=change["after"],
                            prefix=original[: change["start"]],
                            suffix=original[change["end"] :],
                            count=1,
                            whole_word=False,
                        )
                    ],
                    audit,
                    "embedded-caret-footnote-overlay",
                )
            row.setdefault("inline", []).append(
                dict(
                    start=change["marker_start"],
                    end=change["marker_end"],
                    tags=["sup"],
                )
            )
            record = dict(
                page=number,
                label=label,
                bbox=candidate["bbox"],
                embedded_bbox=candidate["embedded_bbox"],
                embedded_char=candidate["embedded_char"],
                readings=candidate["readings"],
                before=change["before"],
                after=change["after"],
                marker_start=change["marker_start"],
                method="embedded-caret-source-glyph",
                **(
                    {"font_witnesses": candidate["font_witnesses"]}
                    if candidate.get("font_witnesses")
                    else {}
                ),
            )
            audit.append(dict(record, kind="raised-page-footnote-call", applied=True))
            found.append(record)
            present.add(label)
    failures = {item["page"]: item for item in unresolved}
    for key, cutoff in book.get("note_starts", {}).items():
        number = int(key)
        labels = footer_entry_labels(pages[number], cutoff)
        if not labels:
            continue
        present = {item["label"] for item in found if item["page"] == number}
        missing = [label for label in labels if label not in present]
        if missing:
            failures[number] = dict(
                page=number,
                reason="missing-note-calls",
                labels=missing,
            )
        else:
            failures.pop(number, None)
    return dict(linkable=len(found), found=found, unresolved=list(failures.values()))
