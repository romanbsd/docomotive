"""Occurrence-based offline review rendering; proposals never edit book text."""

import html
import json
from pathlib import Path

from common import digest
from review_candidates import occurrence_binding
from text_edits import edit_chunks


def pattern(before, after):
    if not after:
        return "Source diagnostics / no proposal"
    return (
        " · ".join(
            f"{a or '∅'} → {b or '∅'}"
            for a, b in edit_chunks(before.lower(), after.lower())
        )
        or "No change / retain source"
    )


def prepare(items, source):
    records, groups, seen = [], {}, set()
    for item in items:
        for occurrence in item["occurrences"]:
            before = occurrence.get("original", item["word"])
            suggestions = [s["term"] for s in item.get("suggestions", [])]
            if before.isupper():
                suggestions = [s.upper() for s in suggestions]
            elif before.istitle():
                suggestions = [s[:1].upper() + s[1:] for s in suggestions]
            proposal = occurrence.get("witness_proposal") or next(iter(suggestions), "")
            suggestions = (
                list(dict.fromkeys([proposal] + suggestions))
                if proposal
                else suggestions
            )
            occurrence = {**occurrence, "original": before}
            binding = occurrence_binding(occurrence)
            ident = digest(
                json.dumps(
                    [
                        source,
                        occurrence["chapter"],
                        occurrence["paragraph"],
                        occurrence["offset"],
                        before,
                        binding,
                        [r["row_id"] for r in occurrence["sources"]],
                    ],
                    sort_keys=True,
                ).encode()
            )[:24]
            if ident in seen:
                continue
            seen.add(ident)
            group = pattern(before, proposal)
            record = dict(
                id=ident,
                word=before,
                proposal=proposal,
                suggestions=suggestions,
                group=group,
                binding=binding,
                occurrence=occurrence,
                category=item.get("kind", item.get("category", "lexical")),
                proposal_evidence=occurrence.get("proposal_evidence"),
                candidate_signals={
                    k: item[k]
                    for k in ("suggestions", "statistics", "reasons", "recommendation")
                    if k in item
                },
                evidence=[],
            )
            records.append(record)
            groups.setdefault(group, []).append(ident)
    return records, groups


def compact(payload):
    """Share large vocabulary attestations across occurrences of the same item."""
    evidence = dict(payload.get("candidate_evidence", {}))
    items = []
    for original in payload["items"]:
        record = dict(original)
        if "candidate_signals" in record:
            signals = record.pop("candidate_signals")
            key = digest(json.dumps(signals, sort_keys=True).encode())[:24]
            evidence[key] = signals
            record["candidate_signals_key"] = key
        items.append(record)
    return {**payload, "items": items, "candidate_evidence": evidence}


def render(payload, summary_html=""):
    """Render the sheet; summary_html is trusted markup built by proofread.py.

    Its source/user values must already be HTML-escaped by that caller.
    All review data below is escaped here, including embedded JSON.
    """
    if any("candidate_signals" in r for r in payload["items"]):
        payload = compact(payload)
    records = {r["id"]: r for r in payload["items"]}
    cards = []
    escape = html.escape
    for group, members in payload["groups"].items():
        sections = []
        for ident in members:
            r = records[ident]
            o = r["occurrence"]
            context, start = o["context"], o["offset"]
            end = start + len(r["word"])
            snippet = (
                escape(context[max(0, start - 180) : start])
                + "<mark>"
                + escape(context[start:end])
                + "</mark>"
                + escape(context[end : end + 180])
            )
            images = "".join(
                f'<figure><figcaption>PDF {e["page"]} · printed page {escape(str(e.get("printed_page", e["page"])))}</figcaption><img loading="lazy" data-image="{e["image_key"]}" alt="Source line"/></figure><details><summary>OCR readings</summary><pre>{escape(json.dumps(e["engines"], ensure_ascii=False, indent=2))}</pre></details>'
                for e in r["evidence"]
            )
            radios = "".join(
                f'<label><input type="radio" name="{ident}" value="{value}"> {label}</label>'
                for value, label in [
                    ("keep", "Keep source"),
                    ("ocr-error", "OCR error"),
                    ("printed-typo", "Printed typo"),
                    ("uncertain", "Uncertain"),
                ]
            )
            options = '<option value="">Choose proposal</option>' + "".join(
                f'<option value="{escape(s, quote=True)}">{escape(s)}</option>'
                for s in r["suggestions"]
            )
            comparisons = (
                '<details class="proposal-evidence"><summary>Proposal evidence and book statistics</summary><pre data-signals="'
                + r["candidate_signals_key"]
                + '"></pre></details>'
            )
            binding_note = (
                ""
                if r["binding"]
                else "<p>Exact row binding unavailable; retain this item for manual review. It cannot export a correction overlay.</p>"
            )
            sections.append(
                f'<article id="{ident}"><h3><label><input class="selected" type="checkbox" {"disabled" if not r["binding"] else ""}> {escape(r["word"])}</label></h3><p>{escape(r["category"])} · chapter {escape(str(o["chapter"]))}</p><p>{snippet}</p>{images}{comparisons}{binding_note}<div>{radios}</div><label>Proposal <select class="proposal">{options}</select></label><label>Verified replacement / note <input class="replacement"></label><label><input class="reviewed" type="checkbox"> I checked this source occurrence</label></article>'
            )
        cards.append(
            f'<section class="group"><h2>{escape(group)} · {len(members)} occurrences</h2><button class="select-all">Select eligible occurrences</button> <button class="clear">Clear selection</button><label><input class="batch-reviewed" type="checkbox"> I checked every selected source crop</label><button class="batch">Mark selected as reviewed OCR errors</button>{"".join(sections)}</section>'
        )
    data = json.dumps(payload, ensure_ascii=False).replace("<", r"\u003c")
    script = (Path(__file__).with_name("proofread_review.js")).read_text()
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Scan-backed proofreading</title><style>body{font:17px/1.5 system-ui;max-width:1000px;margin:2em auto;padding:0 1em;background:#f5f4ef}article{background:white;padding:1.3em;margin:1em 0;border:1px solid #ddd}img{max-width:100%;height:auto}figure{margin:1em 0}label{display:inline-block;margin:.5em}pre{white-space:pre-wrap}input.replacement{width:25em;max-width:90%}button{padding:.6em}.group{border-top:2px solid #aaa}</style><h1>Scan-backed proofreading</h1>'
        f'<p>{len(records)} occurrences in {len(cards)} proposed error-pattern groups. Select individual occurrences for batch decisions. Deselection retains prior decisions; choose Keep source to exclude a previous correction. Grouping is a review aid, not evidence of correctness. Decisions stay in this browser; export to retain them. OCR confidence is engine-specific.</p><p id="storage-status" role="status"></p><button id="download">Export reviewed decisions</button>'
        + summary_html
        + "".join(cards)
        + '<script id="review-data" type="application/json">'
        + data
        + "</script><script>"
        + script
        + "</script></html>"
    )
