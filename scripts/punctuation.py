"""Validate assembled prose punctuation and normalize complete quotation pairs.

Lexical content is never rewritten by this pass. Scan witnesses license repairs;
quote style is a separate reversible rendering normalization.
"""

import difflib
import re
from pathlib import Path
from common import apply_edits, read_json
from book_model import row_id
from ocr import nearest, merge_rows
from vocabulary import TOKEN

QUOTES = '"„“”«»‟'
MARKS = re.compile(r"\s+[.•·](?=\s|$)|[.:;,][.:;,]+")


def punctuation_form(text, original=""):
    # OCR often reads a closing double quote as an apostrophe. Only fold
    # those lookalikes inside an anchored gap already containing a double
    # quote; lexical apostrophes and unrelated punctuation remain distinct.
    if any(c in QUOTES for c in original):
        text = text.translate(str.maketrans("’‘'", '"""'))
    return re.sub(r"\s+", " ", text.translate(str.maketrans("„“”«»", '"""""'))).strip()


def anchored_gap(text, left, right=None, fuzzy=False):
    """Find a unique punctuation-only gap between stable lexical anchors."""
    tokens = list(TOKEN.finditer(text))
    values = [t.group().lower() for t in tokens]
    hits = []
    for i in range(len(tokens) - len(left) + 1):
        actual = values[i : i + len(left) + len(right or [])]
        expected = left + (right or [])
        differences = [(a, b) for a, b in zip(actual, expected) if a != b]
        # One damaged neighboring anchor is tolerable only with two other
        # exact substantial words. No word is corrected by this comparison.
        close = (
            fuzzy
            and len(actual) == len(expected)
            and len(differences) == 1
            and min(map(len, differences[0])) >= 4
            and sum(a == b and len(a) >= 4 for a, b in zip(actual, expected)) >= 2
            and sum(
                max(i2 - i1, j2 - j1)
                for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                    None, *differences[0]
                ).get_opcodes()
                if tag != "equal"
            )
            <= 1
        )
        if values[i : i + len(left)] != left and not close:
            continue
        end = tokens[i + len(left) - 1].end()
        if right:
            j = i + len(left)
            if values[j : j + len(right)] != right and not close:
                continue
            start = tokens[j].start()
        else:
            start = len(text)
        gap = text[end:start]
        if not re.search(r"\w", gap):
            hits.append((end, start, gap))
    return hits[0] if len(hits) == 1 else None


def repair_source_punctuation(model, pages, book, recognizer, witness_root, audit):
    decisions = []
    rows = {
        r.get("row_id", row_id(int(n), r, book.get("source_sha256", ""))): r
        for n, rr in pages.items()
        for r in rr
    }
    witnesses = {}
    for entry in model:
        for block in entry["blocks"] + entry["notes"]:
            if block["kind"] not in ("text", "quote", "verse", "note"):
                continue
            text = block["text"]
            tokens = list(TOKEN.finditer(text))
            candidates = list(MARKS.finditer(text))
            if block["kind"] == "verse":
                candidates += list(re.finditer(r"[?!][.]*$", text, re.M))
            # A long completed prose paragraph without terminal punctuation is
            # suspicious, not automatically a sentence needing an invented dot.
            if (
                block["kind"] in ("text", "quote")
                and len(text) > 60
                and re.search(r'[\w"”»]$', text)
            ):
                candidates.append(re.search(r"$", text))
            for match in candidates:
                lo, hi = match.span()
                left = [t for t in tokens if t.end() <= lo][-3:]
                right = [t for t in tokens if t.start() >= hi][:2]
                if not left or len(left[-1].group()) < 4:
                    continue  # initials/abbreviations
                source = next(
                    (
                        s
                        for s in block["sources"]
                        if s["start"] <= lo <= s["end"] and hi <= s["end"]
                    ),
                    None,
                )
                if not source:
                    continue
                row = rows[source["row_id"]]
                n = source["page"]
                local_lo = max(0, lo - source["start"])
                local_hi = max(0, hi - source["start"])
                # Source intervals can omit a reconstructed wrap dash. Bind the
                # actual row suffix/word gap again instead of reusing offsets.
                row_tokens = list(TOKEN.finditer(source["text"]))
                local_left = [
                    t.group().lower() for t in row_tokens if t.end() <= local_lo
                ][-3:]
                local_right = [
                    t.group().lower() for t in row_tokens if t.start() >= local_hi
                ][:2]
                original = (
                    anchored_gap(row["text"], local_left, local_right)
                    if local_left
                    else None
                )
                if not original:
                    continue
                start, end, before = original
                if any(
                    s["start"] < end and s["end"] > start
                    for s in row.get("inline", [])
                    if "sup" in s.get("tags", [])
                ):
                    continue
                if n not in witnesses:
                    witnesses[n] = merge_rows(
                        read_json(Path(witness_root) / f"{n:04}.json")["lines"]
                    )
                cached = nearest(row, witnesses[n])
                crop_row = dict(
                    row,
                    bbox=[
                        *row["bbox"][:2],
                        min(1, row["bbox"][2] + 0.025),
                        row["bbox"][3],
                    ],
                )
                fresh = recognizer.line_words(recognizer.doc[n - 1], crop_row, raw=True)
                alternatives = []
                for observation in ([cached["text"]] if cached else []) + [
                    fresh["text"]
                ]:
                    located = anchored_gap(observation, local_left, local_right)
                    if not located:
                        located = anchored_gap(
                            observation, local_left, local_right, fuzzy=True
                        )
                    alternatives.append(
                        located and punctuation_form(located[2], before)
                    )
                decision = dict(
                    page=n,
                    row_id=source["row_id"],
                    bbox=row["bbox"],
                    before=before,
                    action="review",
                    reason="punctuation-witness-conflict",
                    context=row["text"],
                    readings=alternatives,
                )
                decisions.append(decision)
                if len(alternatives) != 2 or None in alternatives:
                    continue
                if alternatives[0] == alternatives[1]:
                    after = alternatives[0]
                elif (
                    not local_right
                    and before
                    and set(before.strip()) <= set(QUOTES)
                    and all(
                        v and v.endswith(".") and not re.search(r"[^\"„“”«»’'. ]", v)
                        for v in alternatives
                    )
                ):
                    after = punctuation_form(before) + "."
                else:
                    # Very small scan specks can survive tight line OCR. A
                    # whole-page witness plus a sub-glyph-sized local component
                    # can reject that mark, never a full-size punctuation glyph.
                    after = None
                    if alternatives[0] == "" and before.strip() in {".", "•", "·"}:
                        chars = [
                            c
                            for c in fresh["characters"]
                            if not fresh["text"][c["start"] : c["end"]].isalnum()
                            and c["bbox"][0]
                            >= (fresh["words"][-1][2] if fresh["words"] else 1e9)
                        ]
                        heights = [w[3] - w[1] for w in fresh["words"]]
                        if (
                            heights
                            and chars
                            and all(
                                c["bbox"][2] - c["bbox"][0] < 0.15 * max(heights)
                                and c["bbox"][3] - c["bbox"][1] < 0.2 * max(heights)
                                for c in chars
                            )
                        ):
                            after = ""
                    elif (
                        before.strip() in {".:", ":.", ".,"}
                        and alternatives[0] == "."
                        and set(alternatives[1] or "") <= set("., ")
                    ):
                        after = "."
                if after is None:
                    continue
                if not local_right and after in {"-", "–", "—"}:
                    # A dash at a physical line end may mark a broken word.
                    # Repairing that wrap belongs to span reconstruction, not
                    # the missing sentence-punctuation check.
                    decision["reason"] = "wrap-dash-needs-span-review"
                    continue
                if not local_right:
                    # Source line end is inside a sentence only when the next
                    # assembled token starts lowercase; preserve real stops.
                    if (
                        after == ""
                        and before.strip() == "."
                        and right
                        and not right[0].group().islower()
                    ):
                        continue
                cached_gap = (
                    anchored_gap(cached["text"], local_left, local_right)
                    if cached
                    else None
                )
                if cached_gap and punctuation_form(cached_gap[2], before) == after:
                    after = cached_gap[2]
                else:
                    after = (
                        after + " "
                        if local_right and after
                        else " " if local_right else after
                    )
                # Typography is normalized separately. Keep the original
                # quote glyphs when only surrounding punctuation changes.
                old_quotes = [c for c in before if c in QUOTES]
                new_quotes = [c for c in after if c in QUOTES + "’‘"]
                if old_quotes and len(old_quotes) == len(new_quotes):
                    marks = iter(old_quotes)
                    after = "".join(
                        next(marks) if c in QUOTES + "’‘" else c for c in after
                    )
                if before == after:
                    decision.update(action="confirm", reason="punctuation-corroborated")
                    continue
                apply_edits(
                    [row],
                    [
                        dict(
                            page=n,
                            before=before,
                            after=after,
                            prefix=row["text"][:start],
                            suffix=row["text"][end:],
                            count=1,
                            whole_word=False,
                        )
                    ],
                    audit,
                    "source-punctuation-repair",
                )
                decision.update(
                    action="correct", after=after, reason="source-punctuation-witnesses"
                )
    return dict(
        corrected=sum(d["action"] == "correct" for d in decisions), decisions=decisions
    )


def normalize_quotes(model, audit):
    """Normalize paired marks only; retain orphan marks as explicit review."""
    changes = []
    uncertain = []
    for chapter in model:
        pairs = []
        for block in chapter["blocks"] + chapter["notes"]:
            stack = []
            text = block["text"]
            for i, char in enumerate(text):
                if char not in QUOTES:
                    continue
                before = text[i - 1] if i else ""
                after = text[i + 1] if i + 1 < len(text) else ""
                opening = bool(
                    after
                    and not after.isspace()
                    and (not before or before.isspace() or before in "([{—-:")
                )
                closing = bool(
                    before
                    and not before.isspace()
                    and (
                        not after
                        or after.isspace()
                        or after in ".,;:!?)]}" + QUOTES
                        or after.isdigit()
                    )
                )
                if opening and not closing:
                    stack.append((block, i, len(stack)))
                elif closing and stack:
                    opener, at, depth = stack.pop()
                    pairs.append((opener, at, block, i, depth))
                else:
                    uncertain.append(
                        location(chapter, block, i, "unpaired-or-ambiguous-quote")
                    )
            for block, i, depth in stack:
                uncertain.append(location(chapter, block, i, "unclosed-quote"))
        for a, i, b, j, depth in pairs:
            for block, offset, value in [
                (a, i, "«" if depth % 2 == 0 else "„"),
                (b, j, "»" if depth % 2 == 0 else "“"),
            ]:
                old = block["text"][offset]
                if old != value:
                    block["text"] = (
                        block["text"][:offset] + value + block["text"][offset + 1 :]
                    )
                    record = location(
                        chapter, block, offset, "paired-quote-normalization"
                    )
                    record.update(before=old, after=value)
                    changes.append(record)
                    audit.append(dict(record, kind="quote-normalization", applied=True))
    return dict(changed=len(changes), changes=changes, unresolved=uncertain)


def location(chapter, block, offset, reason):
    source = next(
        (s for s in block.get("sources", []) if s["start"] <= offset < s["end"]), None
    )
    return dict(
        chapter=chapter["chapter"],
        page=source and source["page"],
        bbox=source and source["bbox"],
        offset=offset,
        reason=reason,
        context=block["text"][max(0, offset - 70) : offset + 100],
    )


def write_review(out, pdf, reports):
    """One readable audit for accepted edits and all unresolved source locations."""
    import html
    import os
    from urllib.parse import quote

    pieces = [
        '<!doctype html><meta charset="utf-8"><title>Punctuation and layout review</title><style>body{max-width:1100px;margin:2em auto;font:16px system-ui}td{padding:.6em;border-bottom:1px solid #ddd;vertical-align:top}pre{white-space:pre-wrap;font:inherit}.review{background:#fff4d9}.correct{background:#e9f6ec}</style><h1>Punctuation and layout review</h1><p>Review entries retain uncertain text. Corrections show literal OCR witnesses. PDF links identify source pages.</p><table><tr><th>Pass / action</th><th>Source</th><th>Context and evidence</th></tr>'
    ]
    source = quote(os.path.relpath(pdf, out))
    for name, report in reports.items():
        records = report.get("decisions", []) + report.get("unresolved", [])
        for r in records:
            action = r.get("action", "review")
            if action == "confirm":
                continue
            page = r.get("page")
            link = (
                f'<a href="{source}#page={page}">PDF {page}</a>'
                if page
                else "No source page"
            )
            evidence = {
                k: r[k]
                for k in (
                    "before",
                    "after",
                    "readings",
                    "witness",
                    "reason",
                    "bbox",
                    "label",
                    "candidates",
                )
                if k in r
            }
            import json

            pieces.append(
                f'<tr class="{action}"><td>{html.escape(name)}<br>{action}</td><td>{link}</td><td><pre>{html.escape(r.get("context", ""))}</pre><pre>{html.escape(json.dumps(evidence,ensure_ascii=False,indent=2))}</pre></td></tr>'
            )
    pieces.append("</table>")
    (Path(out) / "punctuation-review.html").write_text("".join(pieces))
