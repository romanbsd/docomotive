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
    cache_path,
    load_profile,
)
from ocr import nearest, merge_rows

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
    records = []
    cards = []
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
    doc = open_source(pdf)
    loaded = {}

    def witnesses(page, row):
        if book.get("text_source") == "native":
            return {"native-pdf": {"text": row["text"], "confidence": None}}
        result = {}
        for engine in ["tesseract", "vision", "rapid"]:
            key = (engine, page)
            if key not in loaded:
                loaded[key] = merge_rows(
                    read_json(cache_path(work, engine) / f"{page:04}.json")["lines"]
                )
            hit = nearest(row, loaded[key])
            result[engine] = (
                None
                if hit is None
                else {"text": hit["text"], "confidence": hit.get("confidence")}
            )
        return result

    for item in extra + candidates:
        ident = digest(
            json.dumps(
                [
                    source,
                    item["word"],
                    [
                        (
                            o["chapter"],
                            o["paragraph"],
                            o["offset"],
                            [r["row_id"] for r in o["sources"]],
                        )
                        for o in item["occurrences"]
                    ],
                ],
                sort_keys=True,
            ).encode()
        )[:20]
        record = {"id": ident, "source_sha256": source, **item}
        evidence = []
        images = []
        # Every occurrence remains in JSON; show the first three contexts without redundant copies.
        for occurrence in item["occurrences"][:3]:
            seen = set()
            for row in occurrence["sources"]:
                if row["row_id"] in seen:
                    continue
                seen.add(row["row_id"])
                n = row["page"]
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
                images.append(
                    f'<figure><figcaption>PDF {n} · original page {book.get("page_labels", {}).get(str(n), str(n+book.get("printed_page_offset",0)))}</figcaption><img loading="lazy" width="{pixmap.width}" height="{pixmap.height}" alt="Source line for {html.escape(item["word"],quote=True)}" src="data:image/jpeg;base64,{base64.b64encode(data).decode()}"/></figure>'
                )
                evidence.append(
                    {
                        "page": n,
                        "row_id": row["row_id"],
                        "bbox": row["bbox"],
                        "expected_source_text": row["text"],
                        "engines": witnesses(n, row),
                    }
                )
        record["evidence"] = evidence
        records.append(record)
        stats = item.get("statistics", {})
        statistics_html = ""
        if stats:
            statistics_html = (
                "<p>Whole-book evidence: "
                + html.escape(
                    f"{stats['count']} occurrences; {stats['page_df']} pages; {stats['chapter_df']} chapters; chapter IDF {stats['chapter_idf']:.2f}; general-language Zipf {stats['general_zipf']:.2f}. {item['recommendation']}."
                )
                + "</p>"
            )
            statistics_html += "<p>" + html.escape("; ".join(item["reasons"])) + "</p>"
            if item["in_book_variants"]:
                statistics_html += (
                    "<p>Nearby book spellings: "
                    + html.escape(
                        ", ".join(
                            f"{v['term']} ({v['count']})"
                            for v in item["in_book_variants"]
                        )
                    )
                    + "</p>"
                )
        options = (
            ", ".join(s["term"] for s in item["suggestions"]) or "No automatic proposal"
        )
        context = item["occurrences"][0]["context"]
        offset = item["occurrences"][0]["offset"]
        lo = max(0, offset - 180)
        hi = min(len(context), offset + len(item["word"]) + 180)
        snippet = (
            html.escape(context[lo:offset])
            + "<mark>"
            + html.escape(context[offset : offset + len(item["word"])])
            + "</mark>"
            + html.escape(context[offset + len(item["word"]) : hi])
        )
        comparisons = "".join(
            "<details><summary>OCR witnesses · PDF "
            + str(e["page"])
            + "</summary><pre>"
            + html.escape(json.dumps(e["engines"], ensure_ascii=False, indent=2))
            + "</pre></details>"
            for e in evidence
        )
        radios = "".join(
            f'<label><input type="radio" name="{ident}" value="{choice}" onchange="save(this)"/> {label}</label>'
            for choice, label in [
                ("keep", "Keep source"),
                ("ocr-error", "OCR error"),
                ("printed-typo", "Printed typo"),
                ("uncertain", "Uncertain"),
            ]
        )
        cards.append(
            f'<article id="{ident}"><h2>{html.escape(item["word"])}</h2><p>{html.escape(item.get("kind",item.get("category","lexical")))} · {len(item["occurrences"])} occurrence(s)</p><p>Proposals: {html.escape(options)}</p><p>{snippet}</p>'
            + "".join(images)
            + comparisons
            + "<div>"
            + radios
            + '</div><label>Verified replacement / note <input class="replacement" onchange="saveNote(this)"/></label></article>'
        )
    payload = json.dumps(records, ensure_ascii=False).replace("<", r"\u003c")
    javascript = """const records=PAYLOAD;const key='docomotive-proof-'+SOURCE;let decisions={};try{decisions=JSON.parse(localStorage.getItem(key)||'{}');}catch(e){}
function persist(){try{localStorage.setItem(key,JSON.stringify(decisions));}catch(e){document.getElementById('storage-status').textContent='Browser storage unavailable; export JSON before closing.';}}
function save(input){const id=input.name;decisions[id]={...(decisions[id]||{}),decision:input.value};persist();}
function saveNote(input){const id=input.closest('article').id;decisions[id]={...(decisions[id]||{}),replacement_or_note:input.value};persist();}
for(const [id,d] of Object.entries(decisions)){const a=document.getElementById(id);if(!a)continue;const r=a.querySelector('input[value="'+d.decision+'"]');if(r)r.checked=true;a.querySelector('.replacement').value=d.replacement_or_note||'';}
function download(){const output=records.filter(r=>decisions[r.id]).map(r=>({...r,...decisions[r.id]}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({source_sha256:SOURCE,mode:'review-only; not automatically applied',decisions:output},null,2)],{type:'application/json'}));a.download='proofreading-decisions.json';a.click();URL.revokeObjectURL(a.href);}
""".replace(
        "SOURCE", json.dumps(source)
    ).replace(
        "PAYLOAD", payload
    )
    body = (
        '<!doctype html><html lang="en"><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><title>Scan-backed proofreading</title><style>body{font:17px/1.5 system-ui;max-width:1000px;margin:2em auto;padding:0 1em;background:#f5f4ef}article{background:white;padding:1.3em;margin:1em 0;border:1px solid #ddd}img{max-width:100%;height:auto}figure{margin:1em 0}label{display:inline-block;margin:.5em}pre{white-space:pre-wrap}input.replacement{width:25em;max-width:90%}button{padding:.6em}</style><h1>Scan-backed proofreading</h1><p>'
        + str(len(records))
        + ' grouped items. Decisions stay in this browser; export JSON to retain them. No requests, automatic edits or server required. OCR confidence is engine-specific, not a correctness probability.</p><p id="storage-status"></p><button onclick="download()">Export reviewed decisions</button>'
        + repair_summary
        + "".join(cards)
        + "<script>"
        + javascript
        + "</script></html>"
    )
    (out / "proofreading-review.html").write_text(body)
    write_json(
        out / "proofreading-review.json",
        {
            "source_sha256": source,
            "items": records,
            "diagnostics": len(extra),
            "lexical_items": len(candidates),
            "mode": "scan-review-required",
            "automatic_repairs": accepted_repairs,
        },
    )
    print(
        f"{len(candidates)} lexical groups + {len(extra)} diagnostics; offline review sheet written"
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
