"""Fail closed on source-linked OCR damage in rendered prose."""

import re
import statistics

from ocr import comparable


def unclassified_note_footers(pages, book):
    """Find a small-type numbered footer that a note region would absorb."""
    if not book.get("link_numeric_footnotes"):
        return []
    gaps = []
    for number, rows in pages.items():
        if int(number) in book.get("reference_pages", []) + book.get("index_pages", []):
            continue
        body_sizes = [
            r["font_size"]
            for r in rows
            if r.get("font_size") and 0.15 < r["bbox"][1] < 0.48
        ]
        if len(body_sizes) < 3:
            continue
        body_size = statistics.median(body_sizes)
        for row in rows:
            y = row["bbox"][1]
            if not (
                0.45 < y < 0.86
                and row.get("font_size", body_size) <= 0.82 * body_size
                and re.match(r"^\s*[1-9]\d?\s+[A-Za-zА-Яа-я]", row["text"])
            ):
                continue
            following = [
                r
                for r in rows
                if y < r["bbox"][1] < min(y + 0.06, 0.86)
                and r.get("font_size", body_size) <= 0.82 * body_size
            ]
            if len(following) < 1:
                continue
            if y >= book.get("note_starts", {}).get(str(number), 2):
                continue
            gaps.append(dict(page=int(number), bbox=row["bbox"], text=row["text"]))
            break
    return gaps


def opening_rows(model):
    """Source rows that begin the first rendered prose block of a chapter."""
    rows = set()
    for chapter in model:
        block = next(
            (b for b in chapter["blocks"] if b["kind"] in ("text", "quote")),
            None,
        )
        if block and block.get("sources"):
            source = block["sources"][0]
            rows.add((source["page"], tuple(source["bbox"])))
    return rows


def findings(model, review):
    """List unresolved high-risk rows; ordinary peer disagreements remain review-only."""
    openings = opening_rows(model)
    rendered = {
        (source["page"], tuple(source["bbox"])): comparable(source["text"])
        for chapter in model
        for block in chapter["blocks"] + chapter["notes"]
        for source in block.get("sources", [])
    }
    result = []
    for row in review:
        if row.get("kind") != "ocr-disagreement" or row.get("status") != "needs-review":
            continue
        primary = comparable(row.get("primary", ""))
        if len(primary) < 3:
            continue
        texts = row.get("engine_texts", {})
        fresh = [comparable(texts.get(name, "")) for name in ("vision", "rapid")]
        fresh = [tokens for tokens in fresh if len(tokens) >= 3]
        if not fresh:
            continue
        initial_conflicts = [
            tokens
            for tokens in fresh
            if tokens[0] != primary[0] and tokens[1:] == primary[1:]
        ]
        key = row["page"], tuple(row["bbox"])
        if not initial_conflicts or key not in rendered:
            continue
        if rendered[key] and rendered[key][0] != primary[0]:
            continue  # A later checked edit changed the suspected initial word.
        raw_initial = re.match(r"[‘“\"]?[^\s]+", row["primary"])
        irregular_case = bool(
            raw_initial
            and re.search(r"[a-z][A-Z]", raw_initial.group())
            and initial_conflicts
        )
        opening = key in openings
        peer_consensus = (
            len(initial_conflicts) == 2
            and initial_conflicts[0][0] == initial_conflicts[1][0]
        )
        if not (irregular_case or (opening and initial_conflicts) or peer_consensus):
            continue
        result.append(
            dict(
                page=row["page"],
                bbox=row["bbox"],
                primary=row["primary"],
                kind=(
                    "irregular-case"
                    if irregular_case
                    else "chapter-opening" if opening else "two-peer-initial-word"
                ),
                fresh_initial_words=sorted({tokens[0] for tokens in initial_conflicts}),
            )
        )
    return result
