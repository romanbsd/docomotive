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


def recover_opening_epigraphs(pages, book, audit):
    """Opening inset prose with a styled credit and smaller scan type."""
    if book.get("text_source") == "native" or not book.get("recover_opening_epigraphs"):
        return
    for number, _, _ in book["chapters"]:
        if number in book.get("reference_pages", []):
            continue
        cutoff = book.get("chapter_body_starts", {}).get(str(number), 0.27)
        rows = sorted(
            (
                r
                for r in pages.get(number, [])
                if cutoff
                <= r["bbox"][1]
                < book.get("note_starts", {}).get(str(number), 1)
            ),
            key=lambda r: r["bbox"][1],
        )
        for i in range(4, min(33, len(rows) - 8)):
            quote, credit, following = rows[:i], rows[i], rows[i + 1 :]
            # Four wrapped lines and eight broad prose controls rule out short
            # subtitles and sparse title leaves. The credit is a full italic run.
            if (
                credit.get("kind", "text") != "text"
                or not 12 <= len(credit["text"]) <= 120
                or credit["bbox"][1] > 0.7
                or not all(
                    not c.isalnum()
                    or any(
                        "em" in s.get("tags", []) and s["start"] <= j < s["end"]
                        for s in credit.get("inline", [])
                    )
                    for j, c in enumerate(credit["text"])
                )
            ):
                continue
            eligible = [r for r in following if len(r["text"]) >= 30]
            widths = sorted(r["bbox"][2] - r["bbox"][0] for r in eligible)
            broad = widths[round(0.9 * (len(widths) - 1))] if widths else 0
            controls = [
                r for r in eligible if r["bbox"][2] - r["bbox"][0] >= 0.8 * broad
            ]
            if len(controls) < 8 or any(r.get("kind", "text") != "text" for r in quote):
                continue
            left = statistics.median(r["bbox"][0] for r in controls)
            right = statistics.median(r["bbox"][2] for r in controls)
            qleft = statistics.median(r["bbox"][0] for r in quote)
            body_height = statistics.median(
                r["bbox"][3] - r["bbox"][1] for r in controls
            )
            qheight = statistics.median(r["bbox"][3] - r["bbox"][1] for r in quote)
            font = lambda r: r.get("scan_font_size", r.get("font_size", 0))
            bfonts = [font(r) for r in controls if font(r)]
            qfonts = [font(r) for r in quote if font(r)]
            # Require both box-height and aligned source-font evidence:
            # indentation or an old OCR font by itself cannot prove a quotation.
            # The 6%/8% reductions exceed minor box and font-estimation noise.
            if (
                len(bfonts) < 8
                or len(qfonts) < 0.8 * len(quote)
                or qheight >= 0.94 * body_height
                or statistics.median(qfonts) >= 0.92 * statistics.median(bfonts)
                or qleft < left + 0.15 * (right - left)
                or any(
                    abs(r["bbox"][0] - qleft) > 0.04 or r["bbox"][2] > right + 0.035
                    for r in quote
                )
                or credit["bbox"][0] < qleft + 0.05
                or abs(credit["bbox"][2] - right) > 0.035
                or following[0]["bbox"][1] - credit["bbox"][3] < 0.008
                or following[0]["bbox"][2] - following[0]["bbox"][0]
                < 0.8 * (right - left)
                or abs(following[0]["bbox"][0] - left) > 0.04
            ):
                continue
            centers = [(r["bbox"][1] + r["bbox"][3]) / 2 for r in quote]
            pitches = [b - a for a, b in zip(centers, centers[1:])]
            pitch = statistics.median(pitches)
            if pitch <= 0 or any(
                not 0.55 * pitch <= p <= 1.55 * pitch for p in pitches
            ):
                continue  # A separated paragraph must not join this epigraph.
            scale = round(
                # Keep very small inferred source type readable on EPUB screens.
                max(0.7, statistics.median(qfonts) / statistics.median(bfonts)),
                2,
            )
            # Typography runs before reconstruction assigns canonical IDs.
            # Use the same identity helper so audits and later sources agree.
            from book_model import row_id

            for r in quote + [credit]:
                r.setdefault("row_id", row_id(number, r, book.get("source_sha256", "")))
            group = quote[0]["row_id"]
            for r in quote:
                r.update(kind="quote", epigraph_group=group, quote_font_scale=scale)
            credit["kind"] = "attribution"
            audit.append(
                dict(
                    page=number,
                    kind="opening-scan-epigraph",
                    rows=[r["row_id"] for r in quote],
                    attribution=credit["row_id"],
                    quote_font_scale=scale,
                    evidence="opening inset smaller-type run, full italic credit and separated broad prose",
                )
            )
            break


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
    adaptive = book.get("recover_centered_headings", False)
    if adaptive:
        # Character counts vary with type size and page width. Thirty letters
        # plus 75% of the local text width establishes ordinary prose controls.
        eligible = [r for r in body if len(r["text"]) >= 30]
        widths = sorted(r["bbox"][2] - r["bbox"][0] for r in eligible)
        width = widths[round(0.9 * (len(widths) - 1))] if widths else 0
        long = [r for r in eligible if r["bbox"][2] - r["bbox"][0] >= 0.75 * width]
    else:
        long = [r for r in body if len(r["text"]) >= 60]
    if len(long) < 6:
        return
    margin = sorted(r["bbox"][0] for r in long)[len(long) // 5]
    right = sorted(r["bbox"][2] for r in long)[4 * len(long) // 5]
    height = statistics.median(r["bbox"][3] - r["bbox"][1] for r in long)
    control_ids = {id(r) for r in long}
    pitches = [
        (b["bbox"][1] + b["bbox"][3] - a["bbox"][1] - a["bbox"][3]) / 2
        for a, b in zip(body, body[1:])
        if id(a) in control_ids and id(b) in control_ids and b["bbox"][1] > a["bbox"][1]
    ]
    if len(pitches) < 4:
        return
    pitch = statistics.median(pitches)

    def styled(row, tag):
        return all(
            not c.isalnum()
            or any(
                tag in v.get("tags", []) and v["start"] <= j < v["end"]
                for v in row.get("inline", [])
            )
            for j, c in enumerate(row["text"])
        )

    def central(row):
        # A 2.5% midpoint tolerance absorbs skew and glyph-box leftovers, while
        # the width bound prevents normal full-width prose proving centering.
        return (
            row["bbox"][2] - row["bbox"][0] <= 0.65 * (right - margin)
            and abs((row["bbox"][0] + row["bbox"][2] - margin - right) / 2) <= 0.025
        )

    for i, r in enumerate(body[:-1]):
        text = r["text"].strip()
        centered = adaptive and central(r) and styled(r, "em")
        following = body[i + 1]
        previous = body[i - 1] if i else None
        label = text.lstrip("•·{").strip() if centered else text
        # The first prose line may carry an ordinary paragraph indent.
        following_margin = (
            -0.015
            <= following["bbox"][0] - margin
            <= max(0.015, 0.06 * (right - margin))
            if adaptive
            else abs(following["bbox"][0] - margin) <= 0.015
        )
        following_prose = (
            len(following["text"]) >= 30
            and following["bbox"][2] - following["bbox"][0] >= 0.65 * (right - margin)
            if adaptive
            else len(following["text"]) >= 60
        )
        if (
            r.get("kind", "text") != "text"
            # The page edge supplies no preceding line or spacing evidence.
            # First-row labels need independent title geometry or case;
            # full-width mixed-case prose can satisfy the other checks.
            or (
                previous is None
                and not centered
                and not text.isupper()
                and r["bbox"][2] - r["bbox"][0] > 0.65 * (right - margin)
            )
            or not 3 <= len(text) <= 55
            or not label
            or not label[0].isupper()
            or re.search(r'[.!?;:,"”]$', text)
            or not following_prose
            or not following["text"][0].isupper()
            or (abs(r["bbox"][0] - margin) > 0.015 and not centered)
            or not following_margin
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
        title = (
            centered
            and previous
            and 3 <= len(previous["text"]) <= 40
            and previous["text"].strip().isupper()
            and central(previous)
            and styled(previous, "strong")
        )
        if (
            title
            and i >= 2
            and not re.search(r'[.!?]["”’]?$', body[i - 2]["text"].strip())
        ):
            title = False
        if previous and (
            not title
            and not re.search(r'[.!?]["”’]?(?:\d+)?$', previous["text"].strip())
            or (r["bbox"][1] + r["bbox"][3] - previous["bbox"][1] - previous["bbox"][3])
            / 2
            < 1.15 * pitch
        ):
            continue
        selected = [previous, r] if title else [r]
        for item in selected:
            item["kind"] = "heading"
            audit.append(
                dict(
                    page=number,
                    kind="image-only-subheading",
                    text=item["text"],
                    bbox=item["bbox"],
                    evidence=(
                        "page-first label with narrow measure, uppercase or centered style and prose neighbor"
                        if previous is None
                        else (
                            "centered styled label or title pair, prose neighbor and extra preceding spacing"
                            if centered
                            else "short margin-aligned label, prose neighbor and extra preceding spacing"
                        )
                    ),
                )
            )


def recover_scanned_headings(pages, book, audit):
    """Use fresh OCR geometry even when a scan carries an old hidden text layer."""
    if book.get("text_source") == "native" or not book.get(
        "recover_image_only_headings"
    ):
        return
    # The detector uses OCR line geometry, not embedded PDF fonts. A hidden
    # OCR layer neither proves nor invalidates a visually separated subheading.
    for number, rows in pages.items():
        image_only_headings(rows, book, number, audit)


def aligned_source(row, source):
    candidate = nearest(row, source)
    if not candidate:
        return None, None
    match = difflib.SequenceMatcher(
        None, candidate["text"].lower(), row["text"].lower(), autojunk=False
    )
    # Geometry alone can pair adjacent lines; require substantial text agreement.
    if match.ratio() >= 0.85:
        return candidate, match
    # Old OCR layers may encode a tracked heading as "I n te r lu d e".
    # Three isolated letters establish tracking; exact compact agreement keeps
    # ordinary word-boundary differences ("now here"/"nowhere") out of this path.
    isolated = [w for w in candidate["text"].split() if len(w) == 1 and w.isalpha()]
    if (
        len(isolated) >= 3
        and re.sub(r"\s+", "", candidate["text"]).lower()
        == re.sub(r"\s+", "", row["text"]).lower()
    ):
        return candidate, match
    return None, None


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
