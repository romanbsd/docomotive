"""Transfer PDF typography to fresh OCR only where text and geometry agree."""

import difflib
import statistics
import re
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
