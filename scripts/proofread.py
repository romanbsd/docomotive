"""Offline scan-backed review sheet and inexpensive source-linked diagnostics."""

import argparse
import base64
import html
import json
import re
from pathlib import Path
import pymupdf
from source_document import open_source
from common import (
    ROOT,
    read_json,
    write_json,
    digest,
    file_digest,
    load_profile,
)
from review_candidates import ReviewWitnesses
from review_sheet import prepare, render, compact

PATTERNS = {
    "suspicious-line-punctuation": r"\b\w+-[.,;:]\s+\w+",
    "repeated-word": r"\b([^\W\d_]{2,})\s+\1\b",
    "mixed-script-token": r"[A-Za-z]+[\u0400-\u04ff]+|[\u0400-\u04ff]+[A-Za-z]+",
    "digit-in-word": r"[^\W\d_]{2,}\d[^\W\d_]{2,}",
}


def diagnostics(model, book):
    results = []
    for chapter in model:
        for index, block in enumerate(chapter["blocks"] + chapter["notes"]):
            for source in block["sources"]:
                if source.get("uncertain_source_row"):
                    results.append(
                        {
                            "word": block["text"][source["start"] : source["end"]],
                            "kind": "uncertain-recovered-row",
                            "suggestions": [],
                            "occurrences": [
                                {
                                    "chapter": chapter["chapter"],
                                    "paragraph": index,
                                    "context": block["text"],
                                    "offset": source["start"],
                                    "sources": [source],
                                    "block_kind": block["kind"],
                                }
                            ],
                            "action": "scan-review-required",
                        }
                    )
            for kind, pattern in PATTERNS.items():
                for match in re.finditer(pattern, block["text"], re.IGNORECASE):
                    sources = [
                        s
                        for s in block["sources"]
                        if s["end"] > match.start() and s["start"] < match.end()
                    ]
                    if (
                        kind == "suspicious-line-punctuation"
                        and sources
                        and all(
                            s["page"] in book.get("index_pages", []) for s in sources
                        )
                    ):
                        continue  # dangling hyphens are valid index abbreviations
                    results.append(
                        {
                            "word": match.group(),
                            "kind": kind,
                            "suggestions": [],
                            "occurrences": [
                                {
                                    "chapter": chapter["chapter"],
                                    "paragraph": index,
                                    "context": block["text"],
                                    "offset": match.start(),
                                    "sources": sources,
                                    "block_kind": block["kind"],
                                }
                            ],
                            "action": "scan-review-required",
                        }
                    )
    return results


def make_sheet(pdf, profile, out, work):
    book = load_profile(profile)
    source = file_digest(pdf)
    if source != book["source_sha256"]:
        raise ValueError("Review source PDF mismatch")
    candidates = read_json(out / "correction-candidates.json")["candidates"]
    extra = diagnostics(read_json(out / "book-model.json"), book)
    repair_summary = ""
    accepted_repairs = []
    if book.get("repair_lexical_confusions"):
        lexical = read_json(out / "lexical-repair.json")
        accepted_repairs = [d for d in lexical["decisions"] if d["action"] == "correct"]
        entries = []
        for decision in accepted_repairs:
            crop = decision["crop"]
            data = (work / "lexical-crops" / (crop["cache_key"] + ".png")).read_bytes()
            if digest(data) != crop["crop_sha256"]:
                raise ValueError("Lexical repair crop checksum mismatch")
            entries.append(
                f'<p>PDF {decision["page"]}: <del>{html.escape(decision["before"])}</del> → '
                f'<strong>{html.escape(decision["after"])}</strong> · crop readings: '
                + html.escape(" / ".join(crop["readings"]))
                + " · "
                + html.escape(crop.get("engine", "tesseract"))
                + (
                    " · alternate Tesseract readings: "
                    + html.escape(" / ".join(crop["tesseract_readings"]))
                    if crop.get("tesseract_readings")
                    else ""
                )
                + '<br/><img alt="Verified source crop" style="max-width:100%;max-height:5em;width:auto" src="data:image/png;base64,'
                + base64.b64encode(data).decode()
                + '"/></p>'
            )
        repair_summary = (
            f"<details><summary>{len(accepted_repairs)} automatic local spelling repairs — show source crops</summary>"
            "<p>Whole-book context and edit-distance candidates rank proposals. Fresh local word crops "
            "must agree in two Tesseract segmentation modes or two RapidOCR raster scales; "
            "these are two readings by one engine, not independent votes. "
            "Weak ranking margins also require agreement with the embedded OCR. Uncertain proposals remain below.</p>"
            "<p>Context-line repairs instead require whole-page OCR and two local raster scales "
            "to read the same shortlist word at a position bound by two exact neighboring words. "
            "These are stability checks by one engine; the complete source line is shown.</p>"
            + "".join(entries)
            + "</details>"
        )
    bank = ReviewWitnesses(work, book)

    def witnesses(row):
        if book.get("text_source") == "native":
            return {"native-pdf": {"text": row["text"], "confidence": None}}
        return {
            engine: {"text": hit["text"], "confidence": hit.get("confidence")}
            for engine, hit in bank.peers(row, all_engines=True).items()
        }

    records, groups = prepare(extra + candidates, source)
    image_data = {}
    doc = open_source(pdf)
    try:
        for record in records:
            for row in record["occurrence"]["sources"]:
                if any(e["row_id"] == row["row_id"] for e in record["evidence"]):
                    continue
                n = row["page"]
                image_key = digest(json.dumps([source, n, row["bbox"]]).encode())[:24]
                if image_key not in image_data:
                    page = doc[n - 1]
                    x0, y0, x1, y1 = row["bbox"]
                    clip = pymupdf.Rect(
                        max(0, x0 - 0.015) * page.rect.width,
                        max(0, y0 - 0.01) * page.rect.height,
                        min(1, x1 + 0.015) * page.rect.width,
                        min(1, y1 + 0.012) * page.rect.height,
                    )
                    pixmap = page.get_pixmap(dpi=180, clip=clip)
                    data = pixmap.pil_tobytes(format="JPEG", quality=85)
                    image_data[image_key] = (
                        "data:image/jpeg;base64," + base64.b64encode(data).decode()
                    )
                record["evidence"].append(
                    dict(
                        page=n,
                        row_id=row["row_id"],
                        bbox=row["bbox"],
                        expected_source_text=row["text"],
                        printed_page=book.get("page_labels", {}).get(
                            str(n), str(n + book.get("printed_page_offset", 0))
                        ),
                        engines=witnesses(row),
                        image_key=image_key,
                    )
                )
    finally:
        doc.close()
    payload = dict(
        format_version=2,
        source_sha256=source,
        model_sha256=file_digest(out / "book-model.json"),
        profile_sha256=file_digest(profile),
        items=records,
        groups=groups,
        review_witnesses=bank.resources,
        images=image_data,
        diagnostics=len(extra),
        lexical_items=len(candidates),
        mode="scan-review-required",
        automatic_repairs=accepted_repairs,
    )
    payload = compact(payload)
    (out / "proofreading-review.html").write_text(render(payload, repair_summary))
    # Images remain in the self-contained HTML; JSON stores their source bindings.
    write_json(
        out / "proofreading-review.json",
        {k: v for k, v in payload.items() if k != "images"},
    )
    print(
        f"{len(records)} occurrences in {len(groups)} pattern groups; offline review sheet written"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("pdf", type=Path, metavar="source", help="PDF or DjVu source")
    p.add_argument("--profile", type=Path, default=ROOT / "config/book.json")
    p.add_argument("--output", type=Path, default=ROOT / "output")
    p.add_argument("--work", type=Path, default=ROOT / "work")
    a = p.parse_args()
    make_sheet(a.pdf, a.profile, a.output, a.work)


if __name__ == "__main__":
    main()
