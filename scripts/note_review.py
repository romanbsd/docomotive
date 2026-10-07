"""Build a self-contained visual sheet for source-located unresolved note markers."""

import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path

import pymupdf
from source_document import open_source

from common import load_profile


def missing_ranges(analysis, book):
    """Bracket missing numbers using recovered neighbors or chapter boundaries."""
    result = []
    for section in book.get("endnote_sections", []):
        chapter = section["source_chapter"]
        lo, hi, title = book["chapters"][chapter - 1]
        known = [
            c
            for c in analysis["candidates"]
            if c["chapter"] == chapter and c["status"] == "applied"
        ]
        for number in analysis["missing_by_chapter"].get(str(chapter), []):
            before = max(
                (c for c in known if c["number"] < number),
                key=lambda c: c["number"],
                default=None,
            )
            after = min(
                (c for c in known if c["number"] > number),
                key=lambda c: c["number"],
                default=None,
            )
            result.append(
                dict(
                    chapter=title,
                    number=number,
                    pdf_start=before["page"] if before else lo,
                    pdf_end=after["page"] if after else hi,
                    after_reference=before["number"] if before else None,
                    before_reference=after["number"] if after else None,
                )
            )
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("pdf", type=Path, metavar="source", help="PDF or DjVu source")
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    book = load_profile(a.profile)
    with a.pdf.open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != book["source_sha256"]:
            raise ValueError("Source PDF does not match the profile")
    analysis = json.loads((a.input / "scanned-endnote-analysis.json").read_text())
    model = json.loads((a.input / "book-model.json").read_text())
    corrected = json.loads((a.input / "corrected-pages.json").read_text())
    proposals = a.input / "jev-note-proposals.json"
    jev = json.loads(proposals.read_text()) if proposals.exists() else {}
    escape = html.escape
    parts = [
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        "<title>Note marker review</title><style>body{max-width:65em;margin:2em auto;padding:0 1em;font:18px/1.5 Georgia,serif}section{border-top:1px solid #aaa;padding:1em 0}img{max-width:100%;display:block;margin:.6em 0}code{font-size:.85em}blockquote{background:#f4f4f4;padding:1em}a{color:#174a8b}</style>",
        "<h1>Note marker review</h1><p>Resolved retries and unresolved locations, with scan crops, paragraph context and OCR readings. "
        "This sheet is offline and makes no book edits. PDF page numbers are one-based. "
        "Unlocated missing references are listed at the end.</p>",
    ]
    doc = open_source(a.pdf)
    candidates = [
        c
        for c in analysis["candidates"]
        if c["status"] != "applied" or c.get("retry_readings") or len(c["text"]) < 40
    ]
    parts.append(
        "<nav><ul>"
        + "".join(
            f'<li><a href="#pdf-{c["page"]}-row-{c["row"]}">PDF {c["page"]}: {escape(c["text"])}</a></li>'
            for c in candidates
        )
        + "</ul></nav>"
    )
    for c in candidates:
        n = c["page"]
        # Candidate text precedes the edit; canonical sources contain the
        # corrected row. Stable page/row identity supplies its current text.
        current_text = corrected[str(n)][c["row"]]["text"]
        label = str(
            book.get("page_labels", {}).get(
                str(n), str(n + book.get("printed_page_offset", 0))
            )
        )
        parts.append(
            f'<section id="pdf-{n}-row-{c["row"]}"><h2>PDF {n} · printed {escape(label)}</h2>'
        )
        parts.append(
            f'<p>Status: <code>{escape(c["status"])}</code>. Unverified OCR readings (not a transcription): <code>{escape(json.dumps(c["readings"]))}</code>.</p>'
        )
        if c.get("retry_readings"):
            parts.append(
                "<p>Sequence-triggered retry readings: <code>"
                + escape(json.dumps(c["retry_readings"]))
                + "</code>.</p>"
            )
        if c["status"] == "applied":
            parts.append(
                f'<p><strong>Resolved reference: {c["number"]}</strong> after chapter-sequence checks.</p>'
            )
        page = doc[n - 1]
        x0, y0, x1, y1 = c["bbox"]
        # Include neighboring lines to show the baseline, plus a larger marker crop.
        for bounds, caption in [
            (
                (0, max(0, y0 - 0.045), 1, min(1, y1 + 0.045)),
                "Source lines around marker",
            ),
            (
                (
                    max(0, x0 - 0.03),
                    max(0, y0 - 0.015),
                    min(1, x1 + 0.03),
                    min(1, y1 + 0.015),
                ),
                "Marker detail",
            ),
        ]:
            clip = pymupdf.Rect(
                bounds[0] * page.rect.width,
                bounds[1] * page.rect.height,
                bounds[2] * page.rect.width,
                bounds[3] * page.rect.height,
            )
            pix = page.get_pixmap(matrix=pymupdf.Matrix(4, 4), clip=clip)
            encoded = base64.b64encode(pix.tobytes("png")).decode()
            parts.append(
                f'<figure><figcaption>{caption}</figcaption><img alt="{caption}" src="data:image/png;base64,{encoded}"></figure>'
            )
        for ch in model:
            for b in ch["blocks"]:
                if any(
                    s["page"] == n and s["text"] == current_text
                    for s in b.get("sources", [])
                ):
                    parts.append(
                        "<h3>Paragraph context</h3><blockquote>"
                        + escape(b["text"])
                        + "</blockquote>"
                    )
        for name, state in jev.get("request", {}).get("state", {}).items():
            if any(
                v["page"] == n and v["marker_text"] == c["text"]
                for v in state["locations"].values()
            ):
                if c["status"] == "applied":
                    parts.append(
                        "<p>The earlier Jev experiment returned uncertain; subsequent local crop recognition resolved this location.</p>"
                    )
                    continue
                parts.append(
                    "<h3>Endnote supplied to Jev</h3><p>"
                    + escape(state["endnote"])
                    + "</p>"
                )
                answer = jev.get("response", {}).get("answers", {}).get(name, {})
                parts.append(
                    "<p>Jev choice: <strong>"
                    + escape(answer.get("choice", "not run"))
                    + "</strong>.</p>"
                )
        parts.append("</section>")
    ranges = missing_ranges(analysis, book)
    pdf_link = Path(
        os.path.relpath(a.pdf.resolve(), a.output.parent.resolve())
    ).as_posix()
    parts.append(
        "<h2>Where to check missing references</h2><p>Inclusive search ranges inferred from the nearest recovered numbers in each chapter, or its boundaries. These are not detected marker locations. Note numbers reset by chapter.</p><table><tr><th>Chapter</th><th>Note</th><th>PDF pages</th><th>Between references</th></tr>"
    )
    for item in ranges:
        start, end = item["pdf_start"], item["pdf_end"]
        span = str(start) if start == end else f"{start}–{end}"
        parts.append(
            f'<tr><td>{escape(item["chapter"])}</td><td>{item["number"]}</td><td><a href="{escape(pdf_link, quote=True)}#page={start}">{span}</a></td><td>{item["after_reference"] or "chapter start"} → {item["before_reference"] or "chapter end"}</td></tr>'
        )
    parts.append("</table></html>")
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text("\n".join(parts) + "\n")
    a.output.with_name("missing-reference-ranges.json").write_text(
        json.dumps(ranges, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"{len(candidates)} review locations, {analysis['unresolved']} still unresolved: {a.output}"
    )


if __name__ == "__main__":
    main()
