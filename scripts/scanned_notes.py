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
from scipy.ndimage import find_objects, label, binary_closing
from scipy.stats import theilslopes

from common import ROOT, digest, write_json, file_digest
from ocr_cache import traineddata_digest
from build_resources import measured, page_image, line_ocr, count


def note_prefix_replacement(row, witnesses):
    """Collapse a duplicated OCR marker only with two aligned literal witnesses."""
    marker = re.match(r"^[°º]([*†‡])(?=\s|[^\W\d_])", row["text"])
    if not marker:
        return None
    tail = row["text"][marker.end() :].strip()
    agreeing = []
    for engine, rows in witnesses.items():
        matches = []
        for source in rows:
            candidate = re.match(r"^\s*([*†‡])\s*(.+)$", source["text"])
            if (
                candidate
                and candidate[1] == marker[1]
                and abs(source["bbox"][1] - row["bbox"][1]) <= 0.008
                and difflib.SequenceMatcher(None, tail, candidate[2]).ratio() >= 0.95
            ):
                matches.append(source["text"])
        if len(matches) == 1:
            agreeing.append(dict(engine=engine, text=matches[0]))
    return (
        dict(before=marker[0], after=marker[1], witnesses=agreeing)
        if len(agreeing) >= 2
        else None
    )


def repair_note_marker_prefixes(pages, doc, book, work, audit):
    """Only first rows in configured footnote regions can supply marker prefixes."""
    from common import cache_path, apply_edits
    from ocr import embedded, merge_rows

    for label, cutoff in book.get("note_starts", {}).items():
        if label in book.get("note_continuations", {}):
            continue
        number = int(label)
        rows = [r for r in pages[number] if r["bbox"][1] >= cutoff]
        if not rows:
            continue
        first = min(rows, key=lambda r: r["bbox"][1])
        if not re.match(r"^[°º][*†‡]", first["text"]):
            continue
        witnesses = {"embedded": merge_rows(embedded(doc[number - 1], raw=True))}
        for engine in ("vision", "rapid"):
            data = json.loads(
                (cache_path(work, engine) / f"{number:04}.json").read_text()
            )
            witnesses[engine] = merge_rows(data["lines"])
        repair = note_prefix_replacement(first, witnesses)
        if repair and first["text"].count(repair["before"]) == 1:
            apply_edits(
                [first],
                [dict(page=number, bbox=first["bbox"], **repair)],
                audit,
                "corroborated-note-marker-prefix",
            )


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


def raised_regions(image, cap_hint=None, variable=False):
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
        if 0.38 * cap < b[3] - b[1] < (0.76 if variable else 0.66) * cap
        and b[3] < baseline + slope * (b[0] + b[2]) / 2 - 0.25 * cap
        # Narrow serif 1s can be only 7% of a normal capital's height. The
        # optional wider size range still requires raised ink and fresh OCR.
        and b[2] - b[0] > (0.07 if variable else 0.18) * cap
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


# Instrument the subprocess primitive without changing extraction fingerprints.
tesseract = measured(tesseract)


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


def line_crop(image, row, marker_right=None, variable=False):
    width, height = image.size
    x0, y0, x1, y1 = row["bbox"]
    top_pad = 8
    if variable and len(row["text"]) < 40:
        # Short OCR tails often exclude the top of a separately recognized note.
        top_pad = max(top_pad, round(0.3 * (y1 - y0) * height))
    left, top = max(0, int(x0 * width) - 3), max(0, int(y0 * height) - top_pad)
    # OCR often omits trailing quotes and superscripts from the text box.
    # One line-height of clearance fits a short raised number; the baseline
    # model and vertical clipping keep neighboring prose out.
    right_pad = max(8, round(0.8 * (y1 - y0) * height))
    right = (
        int(x1 * width) + right_pad
        if marker_right is None
        else max(int(x1 * width) + 8, round(marker_right) + 2)
    )
    crop = image.crop(
        (
            left,
            top,
            min(width, right),
            min(height, int(y1 * height) + 3),
        )
    )
    if variable:
        # Light, compressed scans break small strokes. At this pass's 400 dpi,
        # reconnect only two-pixel vertical breaks; do not join adjacent digits.
        ink = binary_closing(
            np.asarray(crop.convert("L")) < 200, structure=np.ones((3, 1))
        )
        crop = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8))
    return crop, left, top


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


class RapidMarkerRecognizer:
    """Recognition-only English CTC; detector boxes cannot establish tiny digits."""

    def __init__(self, models):
        import importlib.metadata
        from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType

        models = Path(models).resolve()
        self.provenance = dict(
            rapidocr=importlib.metadata.version("rapidocr"),
            onnxruntime=importlib.metadata.version("onnxruntime"),
            models={p.name: file_digest(p) for p in sorted(models.glob("*.onnx"))},
            minimum_score=0.75,
            padding=[5, 20],
            mode="recognition-only English PP-OCRv4",
        )
        self.engine = RapidOCR(
            params={
                "Rec.lang_type": LangRec.EN,
                "Rec.ocr_version": OCRVersion.PPOCRV4,
                "Rec.model_type": ModelType.MOBILE,
                "Global.model_root_dir": str(models),
                "Rec.model_path": str(models / "en_PP-OCRv4_rec_mobile.onnx"),
                "Det.model_path": str(models / "ch_PP-OCRv4_det_mobile.onnx"),
                "Cls.model_path": str(models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
                "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )

    def __call__(self, image):
        readings = []
        for pad in self.provenance["padding"]:
            result = self.engine(
                np.asarray(ImageOps.expand(image.convert("L"), border=pad, fill=255)),
                use_det=False,
                use_cls=False,
                use_rec=True,
            )
            # Scores are model confidence, not calibrated correctness odds.
            if result.txts and result.scores[0] >= self.provenance["minimum_score"]:
                readings.append(result.txts[0].strip())
        return readings


def glyph_readings(crop, box, maximum, recognizer=None):
    x, y, xx, yy = box
    glyph = crop.crop((x - 2, y - 2, xx + 2, yy + 2))
    glyph = ImageOps.expand(
        glyph.resize((glyph.width * 4, glyph.height * 4)), border=30, fill=255
    )
    readings = [tesseract(glyph, psm).decode().strip() for psm in (7, 13, 8)]
    if recognizer is not None:
        readings.extend(recognizer(crop.crop(box)))
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


def page_candidates(page, rows, maximum, variable=False, recognizer=None):
    # 400 dpi supplies enough pixels for tiny note digits without replacing the
    # existing whole-page OCR. Only small glyph crops are recognized again.
    image = Image.open(io.BytesIO(page.get_pixmap(dpi=400).tobytes("png"))).convert("L")
    width, height = image.size
    proposals = []
    for index, row in enumerate(rows):
        if len(row["text"]) < 3 or row.get("kind", "text") != "text":
            continue
        crop, left, top = line_crop(image, row, variable=variable)
        cap = nearby_cap(row, rows, image) if len(row["text"]) < 40 else None
        model = prose_baseline(components(crop), cap)
        faint = model and crop.height > 2.4 * model[0]
        if faint:
            # Overlapping, low-contrast OCR boxes fragment half-size digits.
            # A bounded darker-ink threshold reconnects them; fresh OCR and
            # chapter order must still support the reading and its placement.
            crop = crop.point(lambda p: 0 if p < 180 else 255)
        regions = raised_regions(crop, cap, variable=variable)
        if not regions:
            continue
        # Search clearance is not OCR context. Trim it after locating ink;
        # extra blank space can change whole-line recognition and placement.
        crop, left, top = line_crop(
            image, row, left + max(b[2] for b in regions), variable=variable
        )
        if faint:
            crop = crop.point(lambda p: 0 if p < 180 else 255)
        witness = chars = None
        witnesses = {}
        for box in regions:
            x, y, xx, yy = box
            # Line, raw-line, and word segmentation provide complementary reads;
            # they are modes of one local engine, not independent OCR engines.
            readings = glyph_readings(crop, box, maximum, recognizer=recognizer)
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
    image = page_image(page)
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
        witness, chars = line_ocr(crop, character_line, clean=clean, deskew=deskew)
        for digits in set(retry["readings"]):
            if digits.isascii() and digits.isdigit() and digits not in changes:
                change = replacement(row["text"], witness, chars, box, digits)
                if change:
                    changes[digits] = dict(change, witness=witness)
        if all(d in changes for d in retry["readings"] if d.isascii() and d.isdigit()):
            break
    return changes


def cached_retry_placements(page, row, retry, work, provenance):
    """Cache verified placements and successful empty results, never OCR failures."""
    key = digest(
        json.dumps(
            dict(
                provenance=provenance,
                page=page.number,
                text=row["text"],
                bbox=row["bbox"],
                retry=retry,
            ),
            sort_keys=True,
        ).encode()
    )
    path = Path(work) / "scanned-note-placements" / (key + ".json")
    if path.exists():
        count("retry_placement_cache_hits")
        cached = json.loads(path.read_text())
        if cached.get("key") != key or not isinstance(cached.get("changes"), dict):
            raise ValueError("Invalid retry placement cache")
        return cached["changes"]
    count("retry_placement_cache_misses")
    changes = retry_placements(page, row, retry)
    write_json(path, dict(key=key, changes=changes))
    return changes


def placement_provenance(source_hash, version, model_hash):
    import PIL
    import scipy
    import platform

    return dict(
        source_sha256=source_hash,
        python=platform.python_version(),
        tesseract=version,
        traineddata=model_hash,
        pymupdf=pymupdf.VersionBind,
        pillow=PIL.__version__,
        numpy=np.__version__,
        scipy=scipy.__version__,
        dpi=400,
        helpers=digest(
            "".join(
                ast.dump(ast.parse(inspect.getsource(f)), include_attributes=False)
                for f in (
                    retry_placements,
                    character_line,
                    line_crop,
                    components,
                    prose_baseline,
                    replacement,
                    tesseract,
                    page_image,
                    line_ocr,
                )
            ).encode()
        ),
    )


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
    from marker_evidence import MarkerEvidence

    # The research pass consumes this same evidence representation; this
    # adapter preserves the initial selector's existing scores and decisions.
    weights = [
        MarkerEvidence.from_candidate(candidates[i], o, i).weight for i, o in nodes
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


def absorb_marker_fragments(pages, records, audit):
    """Account for a detached OCR digit already represented by a recovered note."""
    for marker in records:
        if marker["status"] != "applied":
            continue
        a = marker["bbox"]
        for index, row in enumerate(pages[marker["page"]]):
            if index == marker["row"] or not row["text"].strip().isdigit():
                continue
            b = row["bbox"]
            overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
                0, min(a[3], b[3]) - max(a[1], b[1])
            )
            area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
            # Require most of the smaller box, not mere proximity to a digit.
            if area > 0 and overlap / area >= 0.6:
                row["kind"] = "absorbed-note-marker"
                audit.append(
                    dict(
                        page=marker["page"],
                        kind="absorbed-note-marker",
                        text=row["text"],
                        bbox=b,
                        number=marker["number"],
                        target_row=marker["row"],
                    )
                )


def recover_reference_markers(
    pages, doc, book, work, audit, models=ROOT / "work/models"
):
    """Read lost hanging entry numbers; sequence never supplies unobserved digits."""
    sections = {s["heading"]: s for s in book["endnote_sections"]}
    first, last, _ = book["chapters"][book["endnote_chapter"] - 1]
    expected = None
    number = 0
    recognizer = (
        RapidMarkerRecognizer(models)
        if book.get("recover_variable_superscripts")
        else None
    )
    version = subprocess.run(
        ["tesseract", "--version"], capture_output=True, check=True
    ).stdout
    fingerprint = digest(
        version
        + traineddata_digest().encode()
        + Path(__file__).read_bytes()
        + json.dumps(
            recognizer.provenance if recognizer else {}, sort_keys=True
        ).encode()
    )
    for page_number in range(first, last + 1):
        rows = pages[page_number]
        starts = [r["bbox"][0] for r in rows if re.match(r"^\d+[.,]\s", r["text"])]
        if not starts:
            continue
        # A page-specific marker envelope survives cropping and gradual skew.
        margin = float(np.percentile(starts, 20))
        page = doc[page_number - 1]
        for row in rows:
            if row.get("kind") == "heading":
                expected = sections[row["text"]]["expected_notes"]
                number = 0
                continue
            if (
                expected is None
                or number >= expected
                or row["bbox"][1] < book.get("upper_margin_cutoff", 0.035)
            ):
                continue
            match = re.match(r"^(\d+)\.\s", row["text"])
            if (
                match
                and int(match[1]) == number + 1
                and row["bbox"][0] < book.get("endnote_marker_limit", 0.17)
            ):
                number += 1
                continue
            x0, y0, x1, y1 = row["bbox"]
            clip = (
                pymupdf.Rect(
                    max(0, margin - 0.015) * page.rect.width,
                    max(0, y0 * page.rect.height - 2),
                    x1 * page.rect.width + 2,
                    y1 * page.rect.height + 2,
                )
                & page.rect
            )
            pix = page.get_pixmap(dpi=400, clip=clip)
            key = digest(pix.samples + fingerprint.encode())
            path = Path(work) / "reference-markers" / (key + ".json")
            if path.exists():
                evidence = json.loads(path.read_text())
            else:
                image = Image.open(io.BytesIO(pix.tobytes("png")))
                text, chars = character_line(image)
                evidence = dict(text=text, chars=chars)
                write_json(path, evidence)
            observed = re.match(r"^([0-9IlT]{1,3})[.,]\s+", evidence["text"])
            if not observed:
                continue
            tail = re.sub(r"^[0-9IlT]{1,3}[.,]?\s+", "", row["text"])
            source_tail = evidence["text"][observed.end() :]
            letters = lambda value: re.sub(r"\W", "", value).lower()
            # Keep all wording, and reject a number found on an adjacent line.
            if (
                difflib.SequenceMatcher(
                    None, letters(tail), letters(source_tail), autojunk=False
                ).ratio()
                < 0.9
            ):
                continue
            chars = [c for c in evidence["chars"] if c["start"] < len(observed[1])]
            if not chars:
                continue
            digits = observed[1]
            if not digits.isdigit() or int(digits) != number + 1:
                # Il/11 and T/7 need a newly read digit crop, not substitution.
                box = (
                    min(c["bbox"][0] for c in chars),
                    min(c["bbox"][1] for c in chars),
                    max(c["bbox"][2] for c in chars),
                    max(c["bbox"][3] for c in chars),
                )
                if "marker_readings" not in evidence:
                    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
                    evidence["marker_readings"] = glyph_readings(
                        image, box, expected, recognizer=recognizer
                    )
                    write_json(path, evidence)
                if evidence["marker_readings"].count(str(number + 1)) < 2:
                    continue
            before = row["text"]
            row["text"] = str(number + 1) + ". " + tail
            row["bbox"][0] = (
                (pix.x + min(c["bbox"][0] for c in chars))
                / (400 / 72)
                / page.rect.width
            )
            audit.append(
                dict(
                    page=page_number,
                    kind="source-reference-marker",
                    before=before,
                    after=row["text"],
                    source_reading=evidence["text"],
                    bbox=row["bbox"],
                    crop_key=key,
                )
            )
            number += 1


def recover_endnotes(
    pages, doc, book, work, source_hash, audit, review, models=ROOT / "work/models"
):
    """Apply only source-read, uniquely placed, chapter-ordered note markers."""
    version = (
        subprocess.run(["tesseract", "--version"], capture_output=True, check=True)
        .stdout.decode()
        .splitlines()[0]
    )
    model_hash = traineddata_digest()
    recognizer = (
        RapidMarkerRecognizer(models)
        if book.get("recover_variable_superscripts")
        else None
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
                + model_hash
                + pymupdf.VersionBind
                + np.__version__
                + (
                    json.dumps(recognizer.provenance, sort_keys=True)
                    + ast.dump(
                        ast.parse(inspect.getsource(RapidMarkerRecognizer)),
                        include_attributes=False,
                    )
                    if recognizer
                    else ""
                )
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
    placement_runtime = placement_provenance(source_hash, version, model_hash)
    records = []
    for section in book["endnote_sections"]:
        chapter_id = section["source_chapter"]
        chapter = book["chapters"][chapter_id - 1]
        candidates = []
        for number in range(chapter[0], chapter[1] + 1):
            rows = pages[number]
            key = digest(
                json.dumps(
                    dict(
                        rows=rows,
                        maximum=section["expected_notes"],
                        **(
                            {"variable": True}
                            if book.get("recover_variable_superscripts")
                            else {}
                        ),
                    ),
                    sort_keys=True,
                ).encode()
            )[:16]
            path = cache / f"{number:04}-{key}.json"
            if path.exists():
                count("marker_observation_cache_hits")
                found = json.loads(path.read_text())
            else:
                count("marker_observation_cache_misses")
                found = page_candidates(
                    doc[number - 1],
                    rows,
                    section["expected_notes"],
                    variable=book.get("recover_variable_superscripts", False),
                    recognizer=recognizer,
                )
                write_json(path, found)
            candidates.extend(dict(p, page=number, chapter=chapter_id) for p in found)
        if book.get("recover_variable_superscripts"):
            from book_model import classify_row

            # Wider glyph limits also see chapter numbers. Source-region
            # classification excludes those before they influence the counter.
            candidates = [
                p
                for p in candidates
                if classify_row(
                    p["page"], pages[p["page"]][p["row"]], book, p["page"] == chapter[0]
                )[0]
                == "body"
            ]
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
                    + model_hash
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
                count("glyph_retry_cache_hits")
                retry = json.loads(retry_path.read_text())
            else:
                count("glyph_retry_cache_misses")
                retry = retry_readings(doc[p["page"] - 1], p["bbox"])
                write_json(retry_path, retry)
            p["retry_bbox"] = retry["bbox"]
            changes = cached_retry_placements(
                doc[p["page"] - 1],
                pages[p["page"]][p["row"]],
                retry,
                work,
                placement_runtime,
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
    if book.get("recover_variable_superscripts"):
        absorb_marker_fragments(pages, records, audit)
    return dict(
        provenance=dict(
            source_sha256=source_hash,
            tesseract=version,
            eng_traineddata_sha256=model_hash,
            dpi=400,
            cache=str(cache),
            **({"rapid_marker": recognizer.provenance} if recognizer else {}),
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


recover_endnotes = measured(recover_endnotes)
retry_placements = measured(retry_placements)
retry_readings = measured(retry_readings)
