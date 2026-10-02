"""Bind scan-review acknowledgements to source/text; export them after human review."""

import argparse
import base64
import html
import json
from pathlib import Path

import pymupdf

from common import ROOT, digest, file_digest, load_profile, read_json, write_json

BINDING_KEYS = {"source_sha256", "text_sha256", "evidence_sha256", "format_version"}


def reviewed_text(row):
    return row.get("primary", row.get("text", row.get("embedded", "")))


def evidence_digest(row):
    # Confidence, cache paths, status and bbox can change without changing the
    # reviewed words. Alternative glyph readings are evidence, so retain them.
    fields = {
        key: row[key]
        for key in (
            "kind",
            "primary_engine",
            "primary",
            "text",
            "embedded",
            "witness",
            "vision",
            "tesseract",
            "paddle",
            "engine_texts",
            "readings",
            "retry_readings",
        )
        if key in row
    }
    return digest(
        json.dumps(
            fields, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    )


def bound_decision(row, source_hash, decision="reviewed"):
    return dict(
        page=row["page"],
        bbox=row["bbox"],
        text=reviewed_text(row),
        decision=decision,
        format_version=1,
        source_sha256=source_hash,
        text_sha256=digest(reviewed_text(row).encode()),
        evidence_sha256=evidence_digest(row),
    )


def nearby(decision, row):
    # Retain historical center tolerances for legacy entries. Bound entries
    # use them only to disambiguate identical evidence repeated on one page.
    d, r = decision["bbox"], row["bbox"]
    return (
        abs((d[1] + d[3] - r[1] - r[3]) / 2) < 0.005
        and abs((d[0] + d[2] - r[0] - r[2]) / 2) < 0.15
    )


def match_decisions(rows, decisions, source_hash):
    matches = {}
    matched = set()
    issues = []
    legacy = []
    if not isinstance(decisions, list):
        raise ValueError("review-decisions.json must contain an array")
    for index, d in enumerate(decisions):
        if (
            not isinstance(d, dict)
            or type(d.get("page")) is not int
            or not isinstance(d.get("bbox"), list)
            or len(d["bbox"]) != 4
        ):
            raise ValueError(f"Invalid review decision {index}: page and bbox required")
        if any(type(v) not in (float, int) or not 0 <= v <= 1 for v in d["bbox"]):
            raise ValueError(
                f"Invalid review decision {index}: normalized bbox required"
            )
        reason = None
        bound = bool(BINDING_KEYS & d.keys())
        candidates = [i for i, r in enumerate(rows) if r["page"] == d["page"]]
        if bound:
            if (
                not BINDING_KEYS <= d.keys()
                or type(d.get("format_version")) is not int
                or d["format_version"] != 1
            ):
                reason = "incomplete-or-unsupported-binding"
            elif d["source_sha256"] != source_hash:
                reason = "source-mismatch"
            elif d.get("decision") not in (
                "reviewed",
                "corrected",
                "keep",
                "scan-reviewed",
            ):
                reason = "not-acknowledged"
            else:
                text_matches = [
                    i
                    for i in candidates
                    if digest(reviewed_text(rows[i]).encode()) == d["text_sha256"]
                ]
                candidates = [
                    i
                    for i in text_matches
                    if evidence_digest(rows[i]) == d["evidence_sha256"]
                ]
                if not candidates:
                    reason = "evidence-mismatch" if text_matches else "text-mismatch"
                elif len(candidates) > 1:
                    candidates = [i for i in candidates if nearby(d, rows[i])]
                    if len(candidates) != 1:
                        reason = "ambiguous-location"
        else:
            legacy.append(index)
            candidates = [i for i in candidates if nearby(d, rows[i])]
            if d.get("decision") == "uncertain":
                reason = "not-acknowledged"
            elif not candidates:
                reason = "unmatched-location"
        if reason:
            issues.append(dict(index=index, page=d["page"], reason=reason))
            continue
        matched.add(index)
        for i in candidates:
            matches.setdefault(i, []).append(index)
    return dict(matches=matches, matched=matched, issues=issues, legacy=legacy)


def make_sheet(pdf, profile, input_dir, output):
    book = load_profile(profile)
    source = file_digest(pdf)
    if source != book["source_sha256"]:
        raise ValueError("Review source PDF mismatch")
    rows = read_json(input_dir / "ocr-comparison.json")
    decisions = read_json(profile.parent / "review-decisions.json")
    bindings = match_decisions(rows, decisions, source)
    records = []
    cards = []
    with pymupdf.open(pdf) as doc:
        for index, row in enumerate(rows):
            if row.get("status") == "not-rendered":
                continue
            record = bound_decision(row, source, "pending")
            ident = digest(json.dumps(record, sort_keys=True).encode())[:24]
            records.append(dict(id=ident, **record))
            box = row["bbox"]
            page = doc[row["page"] - 1]
            # Review-only clearance retains punctuation and raised digits;
            # 180 dpi keeps offline sheets manageable without changing OCR.
            clip = pymupdf.Rect(
                max(0, box[0] - 0.015) * page.rect.width,
                max(0, box[1] - 0.01) * page.rect.height,
                min(1, box[2] + 0.015) * page.rect.width,
                min(1, box[3] + 0.012) * page.rect.height,
            )
            data = page.get_pixmap(dpi=180, clip=clip).pil_tobytes(
                format="JPEG", quality=85
            )
            indexes = bindings["matches"].get(index, [])
            label = (
                "Previously reviewed with source/text binding"
                if indexes and any(i not in bindings["legacy"] for i in indexes)
                else (
                    "Legacy geometry-only acknowledgement; check before upgrading"
                    if indexes
                    else "Needs scan review"
                )
            )
            cards.append(
                f'<article id="{ident}"><h2>PDF {row["page"]}</h2><p>{label}</p>'
                f'<img alt="Source scan line" src="data:image/jpeg;base64,{base64.b64encode(data).decode()}"/>'
                f"<p>{html.escape(reviewed_text(row))}</p><details><summary>Recognition evidence</summary><pre>{html.escape(json.dumps(row,ensure_ascii=False,indent=2))}</pre></details>"
                '<label><input type="radio" value="reviewed" name="'
                + ident
                + '" onchange="save(this)"/> Reviewed against scan</label>'
                '<label><input type="radio" value="uncertain" name="'
                + ident
                + '" onchange="save(this)"/> Uncertain</label></article>'
            )
    payload = json.dumps(records, ensure_ascii=False).replace("<", r"\u003c")
    script = """const records=PAYLOAD;const key='docomotive-row-review-'+SOURCE;let choices={};try{choices=JSON.parse(localStorage.getItem(key)||'{}');}catch(e){}
function save(input){choices[input.name]=input.value;try{localStorage.setItem(key,JSON.stringify(choices));}catch(e){document.getElementById('notice').textContent='Storage unavailable; export before closing.';}}
for(const r of records){const a=document.getElementById(r.id);const value=choices[r.id];if(value==='reviewed'||value==='uncertain'){a.querySelector('input[value="'+value+'"]').checked=true;}}
function download(){const data=records.filter(r=>choices[r.id]).map(({id,...r})=>({...r,decision:choices[id]}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.download='review-decisions.json';a.click();URL.revokeObjectURL(a.href);}
""".replace(
        "PAYLOAD", payload
    ).replace(
        "SOURCE", json.dumps(source)
    )
    body = (
        '<!doctype html><html lang="en"><meta charset="utf-8"/><title>Scan review decisions</title><style>body{font:17px/1.5 system-ui;max-width:1000px;margin:2em auto}article{border:1px solid #ddd;padding:1em;margin:1em 0}img{max-width:100%}pre{white-space:pre-wrap}label{margin:1em}</style><h1>Scan review decisions</h1><p>Check the scan before choosing Reviewed. Decisions acknowledge this evidence; they do not edit text. Uncertain items remain in review. The export contains only selected entries; merge it with existing profile decisions, replacing entries you reviewed.</p><p id="notice"></p><button onclick="download()">Export decisions</button>'
        + "".join(cards)
        + "<script>"
        + script
        + "</script></html>"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body)
    write_json(
        output.with_suffix(".json"),
        dict(
            source_sha256=source,
            mode="review-only; pending entries are not acknowledgements",
            records=records,
            legacy_decisions=bindings["legacy"],
            issues=bindings["issues"],
        ),
    )
    print(
        f"{len(records)} review locations; {len(bindings['legacy'])} legacy decisions: {output}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--profile", type=Path, default=ROOT / "config/book.json")
    parser.add_argument("--input", type=Path, default=ROOT / "output")
    parser.add_argument("--output", type=Path, default=ROOT / "output/scan-review.html")
    args = parser.parse_args()
    make_sheet(args.pdf, args.profile, args.input, args.output)


if __name__ == "__main__":
    main()
