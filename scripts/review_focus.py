"""Small, source-bound OCR disagreement queue with an optional Jev preview."""

import argparse
import base64
import difflib
import html
import json
from collections import Counter
from pathlib import Path

import pymupdf

from common import digest, file_digest, load_profile, write_json
from jev_rank import MODEL
from ocr import comparable
from source_document import open_source


def classify(row):
    """Prioritize independent agreements without treating votes as source truth."""
    texts = row.get("engine_texts", {})
    primary = comparable(row["primary"])
    vision = comparable(texts.get("vision", ""))
    rapid = comparable(texts.get("rapid", ""))
    embedded = comparable(texts.get("embedded", ""))
    if not primary or not vision or not rapid:
        return "other"
    if vision == rapid != primary:
        return "three-peer-consensus" if embedded == vision else "fresh-peer-consensus"
    return "other"


def lexical_choice(row):
    """Return one token substitution suitable for context ranking, if present."""
    primary = comparable(row["primary"])
    peer = comparable(row.get("engine_texts", {}).get("vision", ""))
    changes = [
        (kind, a, aa, b, bb)
        for kind, a, aa, b, bb in difflib.SequenceMatcher(
            None, primary, peer, autojunk=False
        ).get_opcodes()
        if kind != "equal"
    ]
    if len(changes) != 1:
        return None
    kind, a, aa, b, bb = changes[0]
    if kind != "replace" or aa - a != 1 or bb - b != 1:
        return None
    return dict(before=primary[a], candidate=peer[b])


def triage(rows):
    entries = []
    for row in rows:
        if row.get("kind") != "ocr-disagreement":
            continue
        tier = classify(row)
        entries.append(
            dict(
                id=digest(
                    json.dumps(
                        [row["page"], row["bbox"], row["primary"]],
                        ensure_ascii=False,
                    ).encode()
                )[:20],
                page=row["page"],
                bbox=row["bbox"],
                primary=row["primary"],
                witnesses=row.get("engine_texts", {}),
                tier=tier,
                lexical_choice=lexical_choice(row) if tier != "other" else None,
            )
        )
    entries.sort(
        key=lambda e: (
            {"three-peer-consensus": 0, "fresh-peer-consensus": 1, "other": 2}[
                e["tier"]
            ],
            e["page"],
            e["bbox"][1],
        )
    )
    return entries


def jev_preview(entries, limit=20):
    """Bound text-only questions; an answer can never approve an OCR edit."""
    state, questions = {}, {}
    for entry in entries:
        if entry["tier"] != "fresh-peer-consensus":
            continue  # Third-witness consensus is cheaper to inspect directly.
        choice = entry["lexical_choice"]
        if choice is None:
            continue
        if len(questions) >= limit:
            break
        name = "row_" + entry["id"]
        state[name] = dict(
            pdf_page=entry["page"],
            primary_line=entry["primary"],
            candidate_line=entry["witnesses"].get("vision", ""),
            tokens=choice,
        )
        questions[name] = dict(
            type="choice",
            instructions="Rank lexical plausibility only. You cannot see the scan. Choose uncertain if context does not distinguish the readings. This is a review proposal, never a source transcription.",
            criteria={
                "keep": "The primary token is plausible in the supplied line.",
                "candidate": "The alternate token is more plausible in the supplied line.",
                "uncertain": "The text context does not establish a safe preference.",
            },
        )
    payload = dict(model=MODEL, state=state, questions=questions)
    return dict(
        sent=False,
        request_sha256=digest(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ),
        request=payload,
        action="proposal-only; inspect the source crop before any correction",
    )


def source_crop(page, bbox):
    x, y, xx, yy = bbox
    rect = page.rect
    clip = pymupdf.Rect(
        max(0, x - 0.02) * rect.width,
        max(0, y - 0.014) * rect.height,
        min(1, xx + 0.02) * rect.width,
        min(1, yy + 0.014) * rect.height,
    )
    return page.get_pixmap(dpi=180, clip=clip).pil_tobytes(format="JPEG", quality=83)


def render(entries, doc, source_hash):
    cards = []
    for entry in entries:
        if entry["tier"] == "other":
            continue
        image = base64.b64encode(
            source_crop(doc[entry["page"] - 1], entry["bbox"])
        ).decode()
        witnesses = "".join(
            f"<li><b>{html.escape(name)}</b>: {html.escape(value)}</li>"
            for name, value in entry["witnesses"].items()
        )
        cards.append(
            f'<article id="{entry["id"]}"><h2>PDF {entry["page"]} · '
            f'{html.escape(entry["tier"])}</h2>'
            f'<img alt="Source scan crop" src="data:image/jpeg;base64,{image}">'
            f'<p><b>Current:</b> {html.escape(entry["primary"])}</p>'
            f"<details><summary>All OCR witnesses</summary><ul>{witnesses}</ul></details>"
            f'<label>Decision <select data-id="{entry["id"]}">'
            '<option value="">Unreviewed</option><option value="keep">Keep print</option>'
            '<option value="ocr-error">OCR error</option>'
            '<option value="printed-typo">Printed typo</option>'
            '<option value="uncertain">Uncertain</option></select></label>'
            f'<label>Verified text <input data-text="{entry["id"]}"></label>'
            f'<label><input type="checkbox" data-checked="{entry["id"]}"> I checked this scan crop</label></article>'
        )
    queue_hash = digest(
        json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()
    )
    data = json.dumps(
        {"source_sha256": source_hash, "queue_sha256": queue_hash, "entries": entries},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Focused OCR review</title>'
        "<style>body{font:16px/1.4 system-ui;max-width:900px;margin:auto;padding:1em;background:#f5f4ef}"
        "article{background:white;padding:1em;margin:1em 0;border:1px solid #ccc}"
        "img{max-width:100%;height:auto}input:not([type=checkbox]){width:25em;max-width:95%}label{display:block;margin:.5em 0}</style>"
        f"<h1>Focused OCR review</h1><p>{len(cards)} source crops with peer consensus. "
        "Other disagreements remain in the full review queue. OCR agreement is a search aid, "
        "not proof of the printed text. This sheet saves decisions locally and exports them for source-bound review.</p>"
        '<button id="download">Export decisions</button>'
        + "".join(cards)
        + f'<script id="data" type="application/json">{data}</script>'
        + '<script>const data=JSON.parse(document.getElementById("data").textContent);'
        'const key="focus-"+data.source_sha256+"-"+data.queue_sha256;const saved=JSON.parse(localStorage.getItem(key)||"{}");'
        'for(const select of document.querySelectorAll("select[data-id]")){const id=select.dataset.id;'
        'select.value=saved[id]?.decision||"";const input=document.querySelector(`[data-text="${id}"]`);'
        'const checked=document.querySelector(`[data-checked="${id}"]`);'
        'input.value=saved[id]?.verified_text||"";checked.checked=!!saved[id]?.source_checked;'
        'for(const el of [select,input,checked])el.addEventListener("change",()=>{'
        "saved[id]={decision:select.value,verified_text:input.value,source_checked:checked.checked};"
        "localStorage.setItem(key,JSON.stringify(saved))})}"
        'document.getElementById("download").onclick=()=>{const reviewed=data.entries.filter(e=>saved[e.id]?.decision&&saved[e.id]?.source_checked)'
        ".map(e=>({...e,...saved[e.id],source_sha256:data.source_sha256,queue_sha256:data.queue_sha256}));"
        'const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([JSON.stringify(reviewed,null,2)],'
        '{type:"application/json"}));a.download="ocr-review-decisions.json";a.click();URL.revokeObjectURL(a.href)};'
        "</script></html>"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument(
        "--input", required=True, type=Path, help="Build output directory"
    )
    parser.add_argument("--output", required=True, type=Path, help="HTML review sheet")
    parser.add_argument(
        "--jev-preview", type=Path, help="Write a bounded unsent request"
    )
    args = parser.parse_args()
    book = load_profile(args.profile)
    source_hash = file_digest(args.pdf)
    if source_hash != book["source_sha256"]:
        raise ValueError("Review source PDF mismatch")
    rows = json.loads((args.input / "review-queue.json").read_text())
    entries = triage(rows)
    counts = Counter(entry["tier"] for entry in entries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc = open_source(args.pdf)
    try:
        args.output.write_text(render(entries, doc, source_hash))
    finally:
        doc.close()
    write_json(
        args.output.with_suffix(".json"),
        dict(source_sha256=source_hash, counts=counts, entries=entries),
    )
    if args.jev_preview:
        args.jev_preview.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.jev_preview, jev_preview(entries))
    print(dict(counts))


if __name__ == "__main__":
    main()
