"""Transfer PDF typography to fresh OCR only where text and geometry agree."""

import difflib
import statistics
import re
import pymupdf
import io
import json
import subprocess
from pathlib import Path
from native_pdf import extract_page
from ocr import nearest


def image_only_headings(rows, book, number, audit):
    """Isolated short labels between prose lines; no title/phrase whitelist."""
    if number in book.get("reference_pages", []) or str(number) in book.get(
        "excluded_pages", {}
    ):
        return
    first = number in {a for a, _, _ in book["chapters"]}
    cutoff = (
        book.get("chapter_body_starts", {}).get(str(number), 0.27)
        if first
        else book.get("upper_margin_cutoff", 0.035)
    )
    body = sorted(
        (r for r in rows if r["bbox"][1] >= cutoff and r["bbox"][3] < 0.96),
        key=lambda r: r["bbox"][1],
    )
    for i, r in enumerate(body):
        # An isolated parenthesized credit at the far right after a closing
        # quote has source alignment evidence, even on sparse epigraph leaves.
        if (
            i
            and r.get("kind", "text") == "text"
            and re.fullmatch(r"\([A-Z][^()]{2,70}\)", r["text"].strip())
            and r["bbox"][0] >= 0.65
            and re.search(r'["”]$', body[i - 1]["text"].strip())
            and r["bbox"][1] > body[i - 1]["bbox"][1]
        ):
            r["kind"] = "attribution"
            audit.append(
                dict(
                    page=number,
                    kind="image-only-attribution",
                    text=r["text"],
                    bbox=r["bbox"],
                    evidence="right-aligned parenthesized credit after closing quote",
                )
            )
    long = [r for r in body if len(r["text"]) >= 60]
    if len(long) < 6:
        return
    margin = sorted(r["bbox"][0] for r in long)[len(long) // 5]
    heights = [r["bbox"][3] - r["bbox"][1] for r in long]
    height = statistics.median(heights)
    # OCR boxes can overlap vertically; center pitch is more stable than edge gaps.
    pitches = [
        (b["bbox"][1] + b["bbox"][3] - a["bbox"][1] - a["bbox"][3]) / 2
        for a, b in zip(body, body[1:])
        if len(a["text"]) >= 60 and len(b["text"]) >= 60 and b["bbox"][1] > a["bbox"][1]
    ]
    if len(pitches) < 4:
        return
    pitch = statistics.median(pitches)
    for i, r in enumerate(body[:-1]):
        text = r["text"].strip()
        following = body[i + 1]
        previous = body[i - 1] if i else None
        # Headings have prose on the next line, no sentence-ending punctuation,
        # and extra clearance above. An ordinary short paragraph tail does not.
        if (
            r.get("kind", "text") != "text"
            or not 3 <= len(text) <= 55
            or not text[0].isupper()
            or re.search(r'[.!?;:,"”]$', text)
            or len(following["text"]) < 60
            or not following["text"][0].isupper()
            or abs(r["bbox"][0] - margin) > 0.015
            or abs(following["bbox"][0] - margin) > 0.015
            or not 0.6 * height <= r["bbox"][3] - r["bbox"][1] <= 1.6 * height
            or not 0.65 * pitch
            <= (
                following["bbox"][1]
                + following["bbox"][3]
                - r["bbox"][1]
                - r["bbox"][3]
            )
            / 2
            <= 1.55 * pitch
        ):
            continue
        if previous and (
            not re.search(r'[.!?]["”’]?(?:\d+)?$', previous["text"].strip())
            # A 15% extra line pitch distinguishes a separated label from prose.
            or (r["bbox"][1] + r["bbox"][3] - previous["bbox"][1] - previous["bbox"][3])
            / 2
            < 1.15 * pitch
        ):
            continue
        r["kind"] = "heading"
        audit.append(
            dict(
                page=number,
                kind="image-only-subheading",
                text=text,
                bbox=r["bbox"],
                evidence="short margin-aligned label, prose neighbor and extra preceding spacing",
            )
        )


def aligned_source(row, source):
    candidate = nearest(row, source)
    if not candidate:
        return None, None
    match = difflib.SequenceMatcher(
        None, candidate["text"].lower(), row["text"].lower(), autojunk=False
    )
    # Geometry alone can pair adjacent lines; require substantial text agreement.
    return (candidate, match) if match.ratio() >= 0.85 else (None, None)


def numeric_glyph_readings(page, span, work=None):
    """Reread a source-located digit; bind cached readings to pixels and runtime."""
    from PIL import Image
    import scanned_notes
    from ocr_cache import traineddata_digest
    from common import digest, write_json

    rect = pymupdf.Rect(span["bbox"])
    # One point retains anti-aliased edges; wider crops admit adjacent quotes.
    rect += (-1, -1, 1, 1)
    pix = page.get_pixmap(dpi=600, clip=rect)
    fingerprint = (
        subprocess.run(
            ["tesseract", "--version"], capture_output=True, check=True
        ).stdout
        + traineddata_digest().encode()
        + Path(__file__).read_bytes()
        + Path(scanned_notes.__file__).read_bytes()
        + pymupdf.VersionBind.encode()
    )
    key = digest(pix.tobytes("png") + fingerprint)
    cache = Path(work) / "numeric-superscripts" / (key + ".json") if work else None
    if cache and cache.exists():
        return json.loads(cache.read_text())
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    readings = scanned_notes.glyph_readings(
        image, (0, 0, image.width, image.height), 999
    )
    padding = [1]
    if readings.count(span["text"].strip()) < 2:
        # Quotes or a following capital can enter the one-point crop. Retry
        # half-point clearance while retaining the conflicting first readings.
        tight = pymupdf.Rect(span["bbox"])
        tight += (-0.5, -0.5, 0.5, 0.5)
        image = Image.open(
            io.BytesIO(page.get_pixmap(dpi=600, clip=tight).tobytes("png"))
        ).convert("L")
        readings.extend(
            scanned_notes.glyph_readings(image, (0, 0, image.width, image.height), 999)
        )
        padding.append(0.5)
    result = dict(readings=readings, evidence_sha256=key, padding_points=padding)
    if cache:
        write_json(cache, result)
    return result


def attach_numeric_superscripts(rows, page, number, audit, work=None):
    """Recover existing citation digits without importing noisy hidden-OCR styles."""
    # Images are irrelevant to source text and can dominate extraction on scans.
    data = page.get_text(
        "dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    )
    source = extract_page(page, {}, number, [], text_data=data)
    spans = [
        span
        for block in data["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
    ]
    for row in rows:
        candidate, match = aligned_source(row, source)
        if not candidate or row["text"].strip().isdigit():
            continue
        styles = [
            style
            for style in candidate.get("inline", [])
            if "sup" in style["tags"]
            and re.fullmatch(
                r"\d{1,3}", candidate["text"][style["start"] : style["end"]]
            )
        ]
        # Most prose has no numeric superscripts; avoid scanning all spans for it.
        if not styles:
            continue
        rect = pymupdf.Rect(
            candidate["bbox"][0] * page.rect.width,
            candidate["bbox"][1] * page.rect.height,
            candidate["bbox"][2] * page.rect.width,
            candidate["bbox"][3] * page.rect.height,
        )
        # Normalizing and denormalizing boxes can move a corner by a few ulps.
        rect += (-0.05, -0.05, 0.05, 0.05)
        nearby = [
            s
            for s in spans
            if rect.contains(pymupdf.Rect(s["bbox"]).tl)
            and rect.contains(pymupdf.Rect(s["bbox"]).br)
        ]
        # Ignore punctuation and tiny OCR fragments when estimating the baseline.
        prose = [
            s
            for s in nearby
            if len(s["text"].strip()) >= 3
            and any(c.isalpha() for c in s["text"])
            and s["size"] >= 0.85 * candidate.get("font_size", 0)
        ]
        if not prose:
            continue
        size = statistics.median(s["size"] for s in prose)
        baseline = statistics.median(s["origin"][1] for s in prose)
        for style in styles:
            # A previous repaired glyph may have changed later OCR offsets.
            match = difflib.SequenceMatcher(
                None, candidate["text"].lower(), row["text"].lower(), autojunk=False
            )
            label = candidate["text"][style["start"] : style["end"]]
            if "sup" not in style["tags"] or not re.fullmatch(r"\d{1,3}", label):
                continue
            evidence = [
                s
                for s in nearby
                if s["text"].strip() == label and s["flags"] & 1
                # A citation is visibly smaller and raised; flags alone
                # can mark ordinary words in reconstructed OCR layers.
                and s["size"] <= 0.8 * size and baseline - s["origin"][1] >= 0.18 * size
            ]
            # Repeated identical digits in a row need positional disambiguation;
            # abstain rather than borrowing another occurrence's glyph evidence.
            if len(evidence) != 1 or candidate["text"].count(label) != 1:
                continue
            positions = []
            for a, b, length in match.get_matching_blocks():
                if not (a <= style["start"] and style["end"] <= a + length):
                    continue
                start = b + style["start"] - a
                end = start + len(label)
                positions.append((start, end))
            if not positions:
                for op, a, aa, b, bb in match.get_opcodes():
                    if op != "replace" or not (
                        a <= style["start"] and style["end"] <= aa
                    ):
                        continue
                    left = candidate["text"][a : style["start"]]
                    right = candidate["text"][style["end"] : aa]
                    target = re.fullmatch(
                        r"""[\s."“”'‘’]*([*†‡?®°º0-9Il|oOa]{1,3})[\s."“”'‘’]*""",
                        row["text"][b:bb],
                    )
                    # Strong text anchors on both sides bound one ambiguous glyph;
                    # never replace an alphabetic word or manufacture an absent digit.
                    if (
                        not target
                        or re.search(r"\w", left + right)
                        or a < 6
                        or len(candidate["text"]) - aa < 6
                    ):
                        continue
                    token = target[1]
                    if row["text"].count(token) != 1:
                        continue
                    result = numeric_glyph_readings(page, evidence[0], work)
                    readings = result["readings"]
                    # Segmentation modes are correlated evidence, not independent votes.
                    if readings.count(label) < 2 or any(
                        v.isdigit() and v != label for v in readings
                    ):
                        continue
                    start = b + target.start(1)
                    from common import apply_edits

                    apply_edits(
                        [row],
                        [
                            dict(
                                page=number,
                                before=token,
                                after=label,
                                count=1,
                                bbox=row["bbox"],
                                glyph_bbox=list(evidence[0]["bbox"]),
                                crop_evidence=result,
                            )
                        ],
                        audit,
                        "pdf-numeric-superscript-repair",
                    )
                    positions.append((start, start + len(label)))
            for start, end in positions:
                # The complete digit run must match; never style a partial year.
                if (start and row["text"][start - 1].isdigit()) or (
                    end < len(row["text"]) and row["text"][end].isdigit()
                ):
                    continue
                recovered = dict(start=start, end=end, tags=["sup"])
                if recovered not in row.setdefault("inline", []):
                    row["inline"].append(recovered)
                    audit.append(
                        dict(
                            page=number,
                            kind="pdf-numeric-superscript",
                            text=row["text"],
                            source_text=candidate["text"],
                            bbox=row["bbox"],
                            inline=recovered,
                            glyph_bbox=list(evidence[0]["bbox"]),
                            agreement=match.ratio(),
                        )
                    )


def attach_scan_font_metrics(rows, page, number, audit):
    """Transfer only glyph size; hidden OCR fonts do not establish font families."""
    source = extract_page(page, {}, number, [])
    for row in rows:
        candidate, match = aligned_source(row, source)
        if candidate and candidate.get("font_size"):
            row["scan_font_size"] = candidate["font_size"]
            audit.append(
                dict(
                    page=number,
                    kind="scan-font-size-evidence",
                    bbox=row["bbox"],
                    agreement=match.ratio(),
                    font_size=candidate["font_size"],
                )
            )


def attach_typography(rows, page, number, audit, recover_headings=False):
    source = extract_page(page, {}, number, [])
    sizes = [
        r["font_size"] for r in source if r.get("font_size") and len(r["text"]) > 30
    ]
    body_size = statistics.median(sizes) if sizes else None
    prose_left = [r["bbox"][0] for r in source if len(r["text"]) > 50]
    body_margin = statistics.median(prose_left) if prose_left else None
    for row in rows:
        candidate, match = aligned_source(row, source)
        if not candidate:
            continue
        styles = []
        source_styles = list(candidate.get("inline", []))
        if (
            candidate.get("label_end")
            and candidate["text"][: candidate["label_end"]].isupper()
        ):
            source_styles.append(
                dict(start=0, end=candidate["label_end"], tags=["strong"])
            )
        for style in source_styles:
            if style["start"] == 0 and style["end"] == len(candidate["text"]):
                styles.append(dict(start=0, end=len(row["text"]), tags=style["tags"]))
                continue
            for a, b, length in match.get_matching_blocks():
                lo, hi = max(a, style["start"]), min(a + length, style["end"])
                if hi > lo:
                    styles.append(
                        dict(start=b + lo - a, end=b + hi - a, tags=style["tags"])
                    )
        if styles:
            row["inline"] = styles
        if candidate.get("font_size"):
            row["font_size"] = candidate["font_size"]
        if candidate.get("label_end"):
            row["paragraph_start"] = True
        if body_margin is not None and candidate["bbox"][0] < body_margin - 0.02:
            if any(
                s["start"] == 0
                and 3 <= s["end"] <= 30
                and s["end"] < len(candidate["text"])
                and "em" in s["tags"]
                for s in candidate.get("inline", [])
            ):
                row["paragraph_start"] = True
        if (
            body_size
            and candidate.get("font_size", 0) < 0.95 * body_size
            and re.match(r"^[—–-]\s*[A-Z]", row["text"])
            and len(row["text"]) < 160
        ):
            row["kind"] = "attribution"
        if (
            body_size
            and candidate.get("font_size", 0) > 1.45 * body_size
            and 3 <= len(row["text"]) <= 120
        ):
            row["kind"] = "heading"
        if recover_headings and candidate.get("kind") == "heading":
            # Native classification requires bold evidence on every glyph;
            # aligned_source also requires matching text and nearby geometry.
            row["kind"] = "heading"
        if styles or row.get("kind") == "heading":
            audit.append(
                dict(
                    page=number,
                    kind="pdf-typography-evidence",
                    text=row["text"],
                    source_text=candidate["text"],
                    bbox=row["bbox"],
                    agreement=match.ratio(),
                    inline=styles,
                )
            )


def inset_verse_evidence(rows, body_width):
    """An introduced, aligned ragged run can be verse without italic fonts."""
    result = set()
    long_rows = [
        r
        for r in rows
        if r.get("kind", "text") == "text"
        and r["bbox"][2] - r["bbox"][0] >= 0.8 * body_width
    ]
    if len(long_rows) < 3:
        return result
    margin = statistics.median(r["bbox"][0] for r in long_rows)
    body_height = statistics.median(r["bbox"][3] - r["bbox"][1] for r in long_rows)
    for i, intro in enumerate(rows[:-1]):
        introduced = intro["text"].rstrip().endswith(":")
        # Longer, smaller-type verse can follow a completed sentence. Require
        # a visible block gap; short prose tails alone never establish verse.
        if not introduced and not (
            intro["text"].rstrip().endswith((".", "!", "?", "”", '"', "»"))
            and rows[i + 1]["bbox"][1] - intro["bbox"][3] >= 0.008
        ):
            continue
        run = []
        for r in rows[i + 1 :]:
            x0, y0, x1, y1 = r["bbox"]
            previous = run[-1] if run else intro
            # A deep inset and short lines distinguish verse from paragraph
            # indents; a bounded gap keeps unrelated blocks out of the run.
            if (
                r.get("kind", "text") != "text"
                or r.get("column", 0) != intro.get("column", 0)
                or x0 - margin < (0.06 if introduced else 0.12) * body_width
                or x1 - x0 >= 0.95 * body_width
                # Ascenders/descenders can make adjacent OCR boxes overlap.
                # Limit overlap to half the shorter box, with advancing centers.
                or y0 - previous["bbox"][3]
                < -0.5 * min(y1 - y0, previous["bbox"][3] - previous["bbox"][1])
                or y0 - previous["bbox"][3] > 0.035
                or (y0 + y1 - previous["bbox"][1] - previous["bbox"][3]) / 2 < 0.004
                or (run and abs(x0 - run[0]["bbox"][0]) > 0.012)
            ):
                break
            run.append(r)
        # Four lines and measurable raggedness reject isolated labels and
        # wrapped prose. Hyphenated continuations remain ordinary paragraphs.
        widths = [r["bbox"][2] - r["bbox"][0] for r in run]
        if (
            (4 if introduced else 8) <= len(run) <= 30
            and (
                introduced
                or statistics.median(r["bbox"][3] - r["bbox"][1] for r in run)
                <= 0.9 * body_height
            )
            and max(widths) - min(widths) > 0.05 * body_width
            and sum(w < 0.7 * body_width for w in widths) >= 0.7 * len(widths)
            # Wrapped narrow prose still has a repeated full line width.
            and sum(max(widths) - w < 0.025 * body_width for w in widths)
            <= 0.5 * len(widths)
            and not any(re.search(r"[^\W\d_]-$", r["text"]) for r in run)
            and not any(re.match(r"\s*(?:\d+[.)]|[•▪])\s", r["text"]) for r in run)
        ):
            result.update(r["row_id"] for r in run)
    return result


def verse_evidence(rows, body_width):
    """Conservatively preserve line breaks in short, ragged italic runs."""
    result = set()
    run = []
    if 4 <= len(rows) <= 14 and all(
        r.get("font_size") and r.get("kind", "text") == "text" for r in rows
    ):
        widths = [r["bbox"][2] - r["bbox"][0] for r in rows]
        styled = sum(bool(r.get("inline")) for r in rows)
        if (
            max(widths) < 0.65 * body_width
            and max(widths) - min(widths) > 0.05 * body_width
            and styled >= 0.4 * len(rows)
        ):
            return {r["row_id"] for r in rows}

    def finish():
        if len(run) < 3:
            return
        widths = [r["bbox"][2] - r["bbox"][0] for r in run]
        # Wrapped prose has a common full line width, even when italicized.
        if sum(w < 0.65 * body_width for w in widths) < 0.7 * len(widths):
            return
        if max(widths) - min(widths) < 0.05 * body_width:
            return
        if sum(r.get("_italic_line", False) for r in run) < 0.8 * len(run):
            offsets = [r["bbox"][0] for r in run]
            steps = [b - a for a, b in zip(offsets, offsets[1:]) if abs(b - a) > 0.02]
            alternating = sum(a * b < 0 for a, b in zip(steps, steps[1:])) >= 2
            if (len(run) < 8 and not alternating) or statistics.median(
                len(r["text"].split()) for r in run
            ) < 3:
                return
            capitals = sum(r["text"].lstrip('("“')[:1].isupper() for r in run)
            if capitals < 0.45 * len(run):
                return
            if any(
                re.search(r"[A-Za-z]-$", r["text"])
                and i + 1 < len(run)
                and run[i + 1]["text"][:1].islower()
                for i, r in enumerate(run)
            ):
                return
        result.update(r["row_id"] for r in run)

    for row in rows:
        italic = sum(
            s["end"] - s["start"] for s in row.get("inline", []) if "em" in s["tags"]
        )
        candidate = (
            row.get("kind", "text") == "text"
            and row.get("font_size")
            and (
                italic >= 0.8 * len(row["text"])
                or row["bbox"][2] - row["bbox"][0] < 0.65 * body_width
            )
        )
        gap = bool(run) and row["bbox"][1] - run[-1]["bbox"][3] > 0.035
        if not candidate or gap:
            finish()
            run = []
        if candidate:
            run.append(dict(row, _italic_line=italic >= 0.8 * len(row["text"])))
    finish()
    return result
