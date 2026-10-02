"""Recover raised endnote digits from scan pixels, independently of line OCR.

Only configured endnote chapters are examined. Recognition and placement need
source evidence; chapter-wide ordering rejects duplicate or displaced readings.
Uncertain candidates remain in the review queue, never guessed from sequence.
"""

import difflib
import inspect
import ast
import io
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageOps
from lxml import etree
from scipy.ndimage import find_objects, label
from scipy.stats import theilslopes

from common import digest, write_json


def components(image):
    array = np.asarray(image.convert("L"))
    labels, _ = label(array < 150)  # dark ink against white or beige paper
    boxes = []
    for i, slices in enumerate(find_objects(labels), 1):
        if slices is None or np.count_nonzero(labels[slices] == i) < 5:
            # At 400 dpi, fewer than five ink pixels is usually dust.
            continue
        y, x = slices
        if y.start == 0 or y.stop == array.shape[0]:
            continue  # clipped fragments of neighboring lines
        boxes.append((x.start, y.start, x.stop, y.stop))
    return boxes


def prose_baseline(boxes, cap_hint=None):
    # Ignore tiny punctuation when estimating the normal letter height.
    heights = [b[3] - b[1] for b in boxes if b[3] - b[1] > 10]
    if len(heights) < 5 and cap_hint is None:
        return None
    cap = cap_hint if cap_hint is not None else float(np.percentile(heights, 90))
    # Include x-height letters, but keep half-height note digits out of the fit.
    body = [b for b in boxes if b[3] - b[1] >= 0.55 * cap]
    if len(body) < 2:
        return None  # A neighbor's height still cannot establish this baseline.
    slope = 0.0
    if len(body) >= 8:  # fewer glyphs make skew estimates unstable
        slope = float(
            theilslopes([b[3] for b in body], [(b[0] + b[2]) / 2 for b in body])[0]
        )
        if abs(slope) > 0.04:  # over ~2.3 degrees usually indicates mixed lines
            slope = 0.0
    bottoms = [b[3] - slope * (b[0] + b[2]) / 2 for b in body]
    # Densest bottom cluster handles both x-height letters and descenders,
    # including very short final lines where descenders outnumber capitals.
    # Two pixels of tolerance absorb rasterization and minor baseline jitter.
    baseline = max(
        sorted(set(bottoms)), key=lambda y: sum(abs(v - y) <= 2 for v in bottoms)
    )
    return cap, baseline, slope


def raised_regions(image, cap_hint=None):
    """Find small connected glyphs whose bottoms sit above the prose baseline."""
    boxes = components(image)
    model = prose_baseline(boxes, cap_hint)
    if model is None:
        return []
    cap, baseline, slope = model
    # Note digits are roughly half-size and end at least a quarter cap-height
    # above the baseline. The width floor rejects narrow quote/dot fragments.
    candidates = sorted(
        b
        for b in boxes
        if 0.38 * cap < b[3] - b[1] < 0.66 * cap
        and b[3] < baseline + slope * (b[0] + b[2]) / 2 - 0.25 * cap
        and b[2] - b[0] > 0.18 * cap
    )
    groups = []
    for box in candidates:
        if groups and box[0] - groups[-1][-1][2] < 0.3 * cap:
            # Join adjacent digits; ordinary word spaces are usually wider.
            groups[-1].append(box)
        else:
            groups.append([box])
    return [
        (
            min(b[0] for b in g),
            min(b[1] for b in g),
            max(b[2] for b in g),
            max(b[3] for b in g),
        )
        for g in groups
        if len(g) <= 3  # support 1–999; larger runs are unlikely note markers
    ]


def tesseract(image, psm, hocr=False):
    data = io.BytesIO()
    image.save(data, format="PNG")
    command = ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", str(psm)]
    if hocr:
        command += ["-c", "hocr_char_boxes=1", "hocr"]
    return subprocess.run(
        command, input=data.getvalue(), capture_output=True, check=True
    ).stdout


def character_line(image, clean=True, deskew=False):
    # Tall OCR boxes can include clipped descenders from the preceding line or
    # ascenders from the following one. They distort character box alignment.
    array = np.array(image.convert("L"))
    labels, _ = label(array < 150)
    for i, slices in enumerate(find_objects(labels), 1):
        if slices is not None and (
            slices[0].start == 0 or slices[0].stop == array.shape[0]
        ):
            patch = array[slices]
            patch[labels[slices] == i] = 255
    if clean:
        model = prose_baseline(components(image))
        if model:
            cap, baseline, slope = model
            yy, xx = np.indices(array.shape)
            # Keep the current baseline and raised notes, masking adjacent lines.
            array[
                (yy < baseline + slope * xx - 1.1 * cap)
                | (yy > baseline + slope * xx + 0.25 * cap)
            ] = 255
        image = Image.fromarray(array)
    model = prose_baseline(components(image))
    if deskew and model and abs(model[2]) > 0.001:
        slope = model[2]
        pad = (
            int(abs(slope) * image.width) + 8
        )  # shear clearance plus a small ink margin
        offset = pad if slope > 0 else 4
        image = image.transform(
            (image.width, image.height + pad + 8),
            Image.Transform.AFFINE,
            (1, 0, 0, slope, 1, -offset),
            resample=Image.Resampling.BICUBIC,
            fillcolor=255,
        )
    root = etree.fromstring(tesseract(image, 7, hocr=True))
    text = ""
    chars = []
    for word in root.xpath('//*[@class="ocrx_word"]'):
        if text:
            text += " "
        for char in word.xpath('.//*[@class="ocrx_cinfo"]'):
            box = list(
                map(int, re.search(r"x_bboxes ([\d ]+)", char.get("title"))[1].split())
            )
            chars.append(
                dict(start=len(text), end=len(text) + len(char.text or ""), bbox=box)
            )
            text += char.text or ""
    return text, chars


def replacement(text, witness, chars, box, digits):
    """Anchor a marker gap between matched prose words, preserving punctuation.

    The full line OCR can conflate a closing quote with extra digits. Rebuilding
    this small gap from character geometry also restores that punctuation.
    Never replace a prose word or a neighboring ordinary numeric expression.
    """
    x0, y0, x1, y1 = box
    selected = [
        c
        for c in chars
        if min(x1, c["bbox"][2]) - max(x0, c["bbox"][0])
        >= 0.3 * min(x1 - x0, c["bbox"][2] - c["bbox"][0])
    ]
    # A 30% horizontal overlap also finds OCR boxes merging quote and numeral.
    if not selected:
        return None
    start, end = selected[0]["start"], selected[-1]["end"]
    punctuation = ""
    if selected[0]["bbox"][0] < x0 - 0.4 * (x1 - x0) and witness[start:end] in (
        '"',
        "'",
        "”",
        "’",
    ):
        punctuation = witness[start:end]
    marked = witness[:start] + punctuation + "§" + witness[end:]
    start += len(punctuation)
    # A historical year is a lexical anchor too; otherwise its digits remain
    # in the edit gap and are rightly rejected as an unrelated numeric value.
    left = list(
        re.finditer(r"[A-Za-z]+|(?<!\d)(?:1\d{3}|20\d{2})(?!\d)", marked[:start])
    )
    right = re.search(r"[A-Za-z]+", marked[start + 1 :])
    if not left:
        return None
    left = left[-1]
    right_start = start + 1 + right.start() if right else len(marked)
    right_end = start + 1 + right.end() if right else len(marked)
    matcher = difflib.SequenceMatcher(None, marked, text, autojunk=False)

    def mapped(a, b):
        for sa, ta, n in matcher.get_matching_blocks():
            if sa <= a and b <= sa + n:
                return ta + a - sa, ta + b - sa
        return None

    l = mapped(left.start(), left.end())
    r = mapped(right_start, right_end) if right else (len(text), len(text))
    if not l:
        return None
    if not r and left.end() - left.start() >= 4:
        # An unreadable following word may use a left-only anchor, but require
        # a full word of at least four letters and edit only its punctuation gap.
        tail = text[l[1] :]
        gap_match = re.match(r"[^A-Za-z]*(?:[sSIlOoBZz](?=\s)[^A-Za-z]*)?", tail)
        if gap_match:
            r = (l[1] + gap_match.end(), l[1] + gap_match.end())
    if not r:
        return None
    before = text[l[1] : r[0]]
    gap = marked[left.end() : right_start]
    confused_letters = re.findall(r"[A-Za-z]", before)
    # These are common digit/letter OCR confusions (5/s, 1/I/l, 0/O, 8/B, 2/Z).
    # A 16-character limit bounds the edit to a marker and adjacent punctuation.
    if (
        len(before) > 16
        or len(confused_letters) > len(digits)
        or any(c not in "sSIlOoBZz" for c in confused_letters)
        or re.search(r"\d", gap)
    ):
        return None
    # Notes in prose normally follow punctuation; without it a small numeral
    # could be an exponent or part of a formula rather than an apparatus mark.
    # Citations inside parentheses may put the note before the closing bracket.
    # A trailing period alone is insufficient: it could follow a math exponent.
    # An unpunctuated name citation needs a full capitalized word, a following
    # word anchor and no original prose letters inside the replacement gap.
    if (
        not re.search(r"[.!?;:,)\]\"'”’]", gap[: gap.index("§")])
        and not re.search(r'[)\]"”’]', gap[gap.index("§") + 1 :])
        and not (
            left.group()[0].isupper()
            and len(left.group()) >= 4
            and right
            and gap.strip() == "§"
            and not re.search(r"[A-Za-z]", before)
        )
    ):
        return None
    gap = gap.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))
    if '"' in before and "'" in gap:
        gap = gap.replace("'", '"')
    # OCR sometimes splits one closing quote into two adjacent quote tokens.
    gap = re.sub(r'(["\'])\1+', r"\1", gap)
    offset = gap.index("§")
    gap = gap.replace("§", digits)
    return dict(
        start=l[1],
        end=r[0],
        before=before,
        after=gap,
        marker_start=l[1] + offset,
        marker_end=l[1] + offset + len(digits),
    )


def marker_context(crop, box, digits):
    """Read prose without the tiny numeral, then reinsert its observed position."""
    array = np.array(crop.convert("L"))
    x, y, xx, yy = box
    array[max(0, y - 2) : yy + 2, max(0, x - 2) : xx + 2] = 255
    witness, chars = character_line(Image.fromarray(array))
    if not chars:
        return witness, chars
    following = [c for c in chars if c["bbox"][0] >= xx]
    at = following[0]["start"] if following else len(witness)
    # Insert before the inter-word space so a note remains attached to its word.
    while at and witness[at - 1].isspace():
        at -= 1
    shifted = [
        dict(
            c,
            start=c["start"] + (len(digits) if c["start"] >= at else 0),
            end=c["end"] + (len(digits) if c["start"] >= at else 0),
        )
        for c in chars
    ]
    shifted.append(dict(start=at, end=at + len(digits), bbox=list(box)))
    return witness[:at] + digits + witness[at:], sorted(
        shifted, key=lambda c: c["start"]
    )


def line_crop(image, row, marker_right=None):
    width, height = image.size
    x0, y0, x1, y1 = row["bbox"]
    left, top = max(0, int(x0 * width) - 3), max(0, int(y0 * height) - 8)
    # OCR often omits trailing quotes and superscripts from the text box.
    # One line-height of clearance fits a short raised number; the baseline
    # model and vertical clipping keep neighboring prose out.
    right_pad = max(8, round(0.8 * (y1 - y0) * height))
    right = (
        int(x1 * width) + right_pad
        if marker_right is None
        else max(int(x1 * width) + 8, round(marker_right) + 2)
    )
    return (
        image.crop(
            (
                left,
                top,
                min(width, right),
                min(height, int(y1 * height) + 3),
            )
        ),
        left,
        top,
    )


def nearby_cap(row, rows, image):
    # Short last lines may contain only x-height letters and a note digit.
    # Estimate size from up to three nearby long lines in the same column.
    neighbors = sorted(
        (
            r
            for r in rows
            if len(r["text"]) >= 40
            and r.get("kind", "text") == "text"
            and r.get("column") == row.get("column")
            and abs(r["bbox"][1] - row["bbox"][1]) <= 0.1
        ),
        key=lambda r: abs(r["bbox"][1] - row["bbox"][1]),
    )[:3]
    caps = []
    for neighbor in neighbors:
        crop, _, _ = line_crop(image, neighbor)
        model = prose_baseline(components(crop))
        if model:
            caps.append(model[0])
    return float(np.median(caps)) if caps else None


def glyph_readings(crop, box, maximum):
    x, y, xx, yy = box
    glyph = crop.crop((x - 2, y - 2, xx + 2, yy + 2))
    glyph = ImageOps.expand(
        glyph.resize((glyph.width * 4, glyph.height * 4)), border=30, fill=255
    )
    readings = [tesseract(glyph, psm).decode().strip() for psm in (7, 13, 8)]
    if any(v.isascii() and v.isdigit() and 1 <= int(v) <= maximum for v in readings):
        return readings
    # Geometrically raised digits can be read as letters (9/a/o, 11/ll).
    # Try tight single-character crops before discarding that observation;
    # chapter sequence can then choose between actual alternative readings.
    tight = crop.crop(box)
    for scale in (2, 4):
        for resampling in (Image.Resampling.NEAREST, Image.Resampling.LANCZOS):
            glyph = ImageOps.expand(
                tight.resize((tight.width * scale, tight.height * scale), resampling),
                border=30,
                fill=255,
            )
            readings.append(tesseract(glyph, 10).decode().strip())
    return readings


def page_candidates(page, rows, maximum):
    # 400 dpi supplies enough pixels for tiny note digits without replacing the
    # existing whole-page OCR. Only small glyph crops are recognized again.
    image = Image.open(io.BytesIO(page.get_pixmap(dpi=400).tobytes("png"))).convert("L")
    width, height = image.size
    proposals = []
    for index, row in enumerate(rows):
        if len(row["text"]) < 3 or row.get("kind", "text") != "text":
            continue
        crop, left, top = line_crop(image, row)
        cap = nearby_cap(row, rows, image) if len(row["text"]) < 40 else None
        model = prose_baseline(components(crop), cap)
        faint = model and crop.height > 2.4 * model[0]
        if faint:
            # Overlapping, low-contrast OCR boxes fragment half-size digits.
            # A bounded darker-ink threshold reconnects them; fresh OCR and
            # chapter order must still support the reading and its placement.
            crop = crop.point(lambda p: 0 if p < 180 else 255)
        regions = raised_regions(crop, cap)
        if not regions:
            continue
        # Search clearance is not OCR context. Trim it after locating ink;
        # extra blank space can change whole-line recognition and placement.
        crop, left, top = line_crop(image, row, left + max(b[2] for b in regions))
        if faint:
            crop = crop.point(lambda p: 0 if p < 180 else 255)
        witness = chars = None
        witnesses = {}
        for box in regions:
            x, y, xx, yy = box
            # Line, raw-line, and word segmentation provide complementary reads;
            # they are modes of one local engine, not independent OCR engines.
            readings = glyph_readings(crop, box, maximum)
            if not any(
                v.isascii() and v.isdigit() and 1 <= int(v) <= maximum for v in readings
            ):
                continue
            record = dict(
                row=index,
                text=row["text"],
                bbox=[
                    (left + x) / width,
                    (top + y) / height,
                    (left + xx) / width,
                    (top + yy) / height,
                ],
                readings=readings,
            )
            if witness is None:
                witness, chars = character_line(crop)
            options = []
            for digits in sorted(set(readings)):
                if (
                    not digits.isascii()
                    or not digits.isdigit()
                    or digits.startswith("0")
                    or not 1 <= int(digits) <= maximum
                ):
                    continue
                change = replacement(row["text"], witness, chars, box, digits)
                used_witness = witness
                for variant in ((False, False), (True, True), (False, True)):
                    # Try unmasked then deskewed crops only when placement fails.
                    if change is not None:
                        break
                    if variant not in witnesses:
                        witnesses[variant] = character_line(
                            crop, clean=variant[0], deskew=variant[1]
                        )
                    used_witness, used_chars = witnesses[variant]
                    change = replacement(
                        row["text"], used_witness, used_chars, box, digits
                    )
                if change is None:
                    used_witness, used_chars = marker_context(crop, box, digits)
                    change = replacement(
                        row["text"], used_witness, used_chars, box, digits
                    )
                if change:
                    votes = readings.count(digits)
                    # Primary OCR agreement promotes an otherwise weak crop read.
                    if votes == 1 and re.search(
                        r"(?<!\d)" + digits + r"(?!\d)", change["before"]
                    ):
                        votes += 1
                    options.append(
                        dict(
                            number=int(digits),
                            votes=votes,
                            witness=used_witness,
                            **change,
                        )
                    )
            if options:
                proposals.append(
                    dict(record, witness=witness, options=options, status="candidate")
                )
    return proposals


def complete_marker_crop(page, bbox):
    """Include nearby raised ink fragments, then trim to the complete marker."""
    rect = pymupdf.Rect(
        bbox[0] * page.rect.width,
        bbox[1] * page.rect.height,
        bbox[2] * page.rect.width,
        bbox[3] * page.rect.height,
    )
    height = rect.height
    # Search roughly one digit to the right. A broken second digit can fail
    # the initial connected-component height test; clipped prose is excluded.
    rect.x1 += 1.3 * height
    rect.y0 -= 0.2 * height
    rect.y1 += 0.3 * height
    pix = page.get_pixmap(dpi=400, clip=rect)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    scale = 400 / 72
    right = bbox[2] * page.rect.width * scale - pix.x
    selected = []
    for box in sorted(components(image)):
        if box[0] <= right + 0.5 * height * scale:
            selected.append(box)
            right = max(right, box[2])
    if not selected:
        return image, bbox
    bounds = (
        min(b[0] for b in selected),
        min(b[1] for b in selected),
        max(b[2] for b in selected),
        max(b[3] for b in selected),
    )
    expanded = [
        (pix.x + bounds[0]) / scale / page.rect.width,
        (pix.y + bounds[1]) / scale / page.rect.height,
        (pix.x + bounds[2]) / scale / page.rect.width,
        (pix.y + bounds[3]) / scale / page.rect.height,
    ]
    return image.crop(bounds), expanded


def retry_readings(page, bbox):
    """Recognize only a conflicted marker with tight single-character crops."""
    image, expanded = complete_marker_crop(page, bbox)
    readings = []
    # The original padded crop uses line/word segmentation and nearest scaling.
    # Tight single-character crops plus smooth scaling preserve small openings
    # that distinguish 6 from 8. Two scales test resampling sensitivity.
    for scale in (2, 4):
        for resampling in (Image.Resampling.NEAREST, Image.Resampling.LANCZOS):
            glyph = ImageOps.expand(
                image.resize((image.width * scale, image.height * scale), resampling),
                border=30,
                fill=255,
            )
            readings.append(tesseract(glyph, 10).decode().strip())
    return dict(readings=readings, bbox=expanded)


def retry_placements(page, row, retry):
    """Re-anchor the complete marker, including fragments mistaken for punctuation."""
    image = Image.open(io.BytesIO(page.get_pixmap(dpi=400).tobytes("png"))).convert("L")
    width, height = image.size
    b = retry["bbox"]
    crop, left, top = line_crop(image, row, b[2] * width)
    box = (
        b[0] * width - left,
        b[1] * height - top,
        b[2] * width - left,
        b[3] * height - top,
    )
    changes = {}
    for clean, deskew in ((True, False), (False, False), (True, True)):
        witness, chars = character_line(crop, clean=clean, deskew=deskew)
        for digits in set(retry["readings"]):
            if digits.isascii() and digits.isdigit() and digits not in changes:
                change = replacement(row["text"], witness, chars, box, digits)
                if change:
                    changes[digits] = dict(change, witness=witness)
        if all(d in changes for d in retry["readings"] if d.isascii() and d.isdigit()):
            break
    return changes


def glyph_confusion_cost(observed, alternative):
    """Relative visual penalty, not a calibrated probability or replacement rule."""
    similar = {frozenset(pair) for pair in ("68", "38", "56", "29", "17", "08")}
    if len(observed) != len(alternative):
        return 2.0
    # A half-point cost for common closed-loop/stroke confusions is weaker
    # than the +2 consecutive-number bonus. Unrelated digits cost two points.
    return sum(
        0 if a == b else 0.5 if frozenset((a, b)) in similar else 2.0
        for a, b in zip(observed, alternative)
    )


def add_retry_options(candidate, readings, maximum, changes=None):
    """Reuse the verified character placement, but only supply freshly read digits."""
    candidate["retry_readings"] = readings
    existing = {o["number"] for o in candidate["options"]}
    for digits in sorted(set(readings)):
        if (
            not digits.isascii()
            or not digits.isdigit()
            or digits.startswith("0")
            or not 1 <= int(digits) <= maximum
            or int(digits) in existing
        ):
            continue
        option = dict(candidate["options"][0])
        if changes is not None:
            if not changes.get(digits):
                continue  # A wider crop still needs verified text placement.
            option.update(changes[digits])
        else:
            start = option["marker_start"] - option["start"]
            end = option["marker_end"] - option["start"]
            option["after"] = option["after"][:start] + digits + option["after"][end:]
        option.update(
            number=int(digits),
            votes=readings.count(digits),
            marker_end=option["marker_start"] + len(digits),
            evidence="sequence-triggered-crop-retry",
            confusion_cost=min(glyph_confusion_cost(str(n), digits) for n in existing),
        )
        candidate["options"].append(option)


def select_sequence(candidates, maximum):
    """Select unambiguous readings on the best increasing chapter-wide path.

    Two agreeing crop reads score six, one scores three, consecutive references
    add two. Missing references are allowed. All tied optimal paths are checked:
    ambiguous positions/numbers stay unmodified. A single crop reading also
    needs a consecutive neighbor as additional sequence evidence.
    """
    nodes = [
        (i, option)
        for i, p in enumerate(candidates)
        for option in p["options"]
        if 1 <= option["number"] <= maximum
    ]
    if not nodes:
        return
    # Relative evidence scores, not probabilities: agreement doubles the weak
    # score; the +2 sequence bonus stays smaller than even one crop reading.
    weights = [
        (6 if o["votes"] >= 2 else 3) - o.get("confusion_cost", 0) for _, o in nodes
    ]

    def edge(a, b):
        return (
            nodes[a][0] < nodes[b][0] and nodes[a][1]["number"] < nodes[b][1]["number"]
        )

    def bonus(a, b):
        return 2 if nodes[b][1]["number"] == nodes[a][1]["number"] + 1 else 0

    forward = weights[:]
    backward = weights[:]
    for b in range(len(nodes)):
        forward[b] = max(
            [weights[b]]
            + [forward[a] + weights[b] + bonus(a, b) for a in range(b) if edge(a, b)]
        )
    for a in reversed(range(len(nodes))):
        backward[a] = max(
            [weights[a]]
            + [
                backward[b] + weights[a] + bonus(a, b)
                for b in range(a + 1, len(nodes))
                if edge(a, b)
            ]
        )
    best = max(forward)
    optimal = [
        j for j in range(len(nodes)) if forward[j] + backward[j] - weights[j] == best
    ]
    unique = {}
    for j in optimal:
        i, option = nodes[j]
        if (
            sum(nodes[k][0] == i for k in optimal) == 1
            and sum(nodes[k][1]["number"] == option["number"] for k in optimal) == 1
        ):
            unique[i] = option
    chosen = {o["number"] for o in unique.values()}
    for i, p in enumerate(candidates):
        option = unique.get(i)
        if option is None:
            p["status"] = (
                "sequence-ambiguous"
                if any(nodes[j][0] == i for j in optimal)
                else "chapter-order-conflict"
            )
        elif option["votes"] < 2 and not (
            option["number"] - 1 in chosen or option["number"] + 1 in chosen
        ):
            p["status"] = "recognition-needs-neighbors"
        else:
            p.update(option, status="selected", sequence_score=best)


def recover_endnotes(pages, doc, book, work, source_hash, audit, review):
    """Apply only source-read, uniquely placed, chapter-ordered note markers."""
    version = (
        subprocess.run(["tesseract", "--version"], capture_output=True, check=True)
        .stdout.decode()
        .splitlines()[0]
    )
    # Fingerprint detection semantics, not comments. Sequence tuning reuses the
    # cached pixel/OCR evidence; changed detection code or inputs invalidate it.
    cache = (
        Path(work)
        / "scanned-notes"
        / digest(
            (
                source_hash
                + version
                + pymupdf.VersionBind
                + np.__version__
                + "".join(
                    ast.dump(ast.parse(inspect.getsource(f)), include_attributes=False)
                    for f in (
                        components,
                        prose_baseline,
                        raised_regions,
                        tesseract,
                        character_line,
                        marker_context,
                        replacement,
                        line_crop,
                        nearby_cap,
                        glyph_readings,
                        page_candidates,
                    )
                )
            ).encode()
        )[:16]
    )
    records = []
    for section in book["endnote_sections"]:
        chapter_id = section["source_chapter"]
        chapter = book["chapters"][chapter_id - 1]
        candidates = []
        for number in range(chapter[0], chapter[1] + 1):
            rows = pages[number]
            key = digest(
                json.dumps(
                    dict(rows=rows, maximum=section["expected_notes"]), sort_keys=True
                ).encode()
            )[:16]
            path = cache / f"{number:04}-{key}.json"
            if path.exists():
                found = json.loads(path.read_text())
            else:
                found = page_candidates(
                    doc[number - 1], rows, section["expected_notes"]
                )
                write_json(path, found)
            candidates.extend(dict(p, page=number, chapter=chapter_id) for p in found)
        select_sequence(candidates, section["expected_notes"])
        # Chapter order triggers additional observation, rather than treating
        # missing OCR alternatives as proof that an expected digit is absent.
        for p in candidates:
            if p["status"] not in (
                "sequence-ambiguous",
                "chapter-order-conflict",
                "recognition-needs-neighbors",
            ):
                continue
            retry_key = digest(
                (
                    source_hash
                    + version
                    + pymupdf.VersionBind
                    + "".join(
                        ast.dump(
                            ast.parse(inspect.getsource(f)), include_attributes=False
                        )
                        for f in (retry_readings, complete_marker_crop, components)
                    )
                    + json.dumps(dict(page=p["page"], bbox=p["bbox"]), sort_keys=True)
                ).encode()
            )[:16]
            retry_path = Path(work) / "scanned-notes-retries" / (retry_key + ".json")
            if retry_path.exists():
                retry = json.loads(retry_path.read_text())
            else:
                retry = retry_readings(doc[p["page"] - 1], p["bbox"])
                write_json(retry_path, retry)
            p["retry_bbox"] = retry["bbox"]
            changes = retry_placements(
                doc[p["page"] - 1], pages[p["page"]][p["row"]], retry
            )
            add_retry_options(p, retry["readings"], section["expected_notes"], changes)
        select_sequence(candidates, section["expected_notes"])
        # Right-to-left edits keep the cached source offsets stable.
        for p in reversed(candidates):
            if p["status"] != "selected":
                review.append(dict(p, kind="scanned-endnote-review"))
                continue
            row = pages[p["page"]][p["row"]]
            if row["text"][p["start"] : p["end"]] != p["before"]:
                raise ValueError("Scanned endnote replacement precondition failed")
            delta = len(p["after"]) - (p["end"] - p["start"])
            row["inline"] = [
                s
                for s in row.get("inline", [])
                if not (
                    "sup" in s["tags"]
                    and s["start"] < p["end"]
                    and s["end"] > p["start"]
                )
            ]
            for style in row.get("inline", []):
                for bound in ("start", "end"):
                    if style[bound] >= p["end"]:
                        style[bound] += delta
                    elif style[bound] > p["start"]:
                        style[bound] = p["start"] + (
                            len(p["after"]) if bound == "end" else 0
                        )
            row["text"] = (
                row["text"][: p["start"]] + p["after"] + row["text"][p["end"] :]
            )
            row.setdefault("inline", []).append(
                dict(start=p["marker_start"], end=p["marker_end"], tags=["sup"])
            )
            row["inline"].sort(key=lambda s: s["start"])
            p["status"] = "applied"
            audit.append(dict(p, kind="scanned-endnote-recovery"))
        records.extend(candidates)
        print(
            f'Scanned notes: {section["heading"]}: {sum(p["status"] == "applied" for p in candidates)}/{section["expected_notes"]}',
            flush=True,
        )
    return dict(
        provenance=dict(
            source_sha256=source_hash, tesseract=version, dpi=400, cache=str(cache)
        ),
        expected=sum(s["expected_notes"] for s in book["endnote_sections"]),
        candidates=records,
        applied=sum(p["status"] == "applied" for p in records),
        unresolved=sum(p["status"] != "applied" for p in records),
        missing_by_chapter={
            str(s["source_chapter"]): [
                n
                for n in range(1, s["expected_notes"] + 1)
                if not any(
                    p["chapter"] == s["source_chapter"]
                    and p["status"] == "applied"
                    and p["number"] == n
                    for p in records
                )
            ]
            for s in book["endnote_sections"]
        },
    )
