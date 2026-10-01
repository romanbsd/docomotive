"""Canonical source-linked text, layout decisions and coverage before rendering."""

import copy
import re
from collections import Counter
from wordfreq import zipf_frequency
from common import digest
from layout import excluded


class JoinPolicy:
    def __init__(
        self, protected=(), observed=(), dictionary=None, observed_min_count=2
    ):
        self.words = {w.lower() for w in protected} | {
            w.lower() for w, n in Counter(observed).items() if n >= observed_min_count
        }
        self.dictionary = dictionary

    def accepts(self, word):
        low = word.lower()
        return (
            low in self.words
            or zipf_frequency(low, "en") >= 2
            or bool(self.dictionary and self.dictionary.lookup(low))
        )

    def boundary(self, a, b):
        if a.endswith("\u00ad"):
            return 1, "", "remove-discretionary"
        if a.endswith("-"):
            left = re.search(r"([\w]+)-$", a)
            right = re.match(r"([^\W\d_]+)", b)
            if left and right:
                combined = left[1] + right[1]
                hyphenated = left[1] + "-" + right[1]
                if hyphenated.lower() in self.words:
                    return 0, "", "retain"
                return (
                    (1, "", "remove") if self.accepts(combined) else (0, "", "retain")
                )
        return 0, (" " if a else ""), "space"


DEFAULT_POLICY = JoinPolicy()


def join(a, b, audit=None, page=None, policy=None):
    trim, separator, action = (policy or DEFAULT_POLICY).boundary(a, b)
    if action != "space" and audit is not None:
        audit.append(
            {
                "page": page,
                "kind": "line-hyphen",
                "before": a.split()[-1] + " " + b.split()[0],
                "after": (a[:-trim] if trim else a).split()[-1] + b.split()[0],
                "action": action,
            }
        )
    return (a[:-trim] if trim else a) + separator + b


def row_id(page, row, source=""):
    identity = [source, page, row.get("column", 0), [round(v, 6) for v in row["bbox"]]]
    return digest(repr(identity).encode())[:20]


def classify_row(page, row, book, first=False):
    y = row["bbox"][1]
    text = row["text"].strip()
    if first and y < book.get("chapter_body_starts", {}).get(str(page), 0.27):
        return "excluded", "chapter-title"
    if y < book.get("upper_margin_cutoff", 0.035):
        return "excluded", "upper-margin-artifact"
    if excluded(page, row, book.get("_layout", {})):
        return "excluded", "recurring-margin"
    letters = [c for c in text if c.isalpha()]
    if y < 0.10 and letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return "excluded", "uppercase-margin-fallback"
    if len(text) <= 4 and y > 0.84:
        return "excluded", "short-footer-fallback"
    if y >= book.get("note_starts", {}).get(str(page), 2):
        return "note", "profile-note-region"
    return "body", "retained"


def page_blocks(n, rows, book, audit, first, policy=None):
    notes = []
    body = []
    for original in rows:
        r = copy.deepcopy(original)
        r["page"] = n
        r.setdefault("row_id", row_id(n, r, book.get("source_sha256", "")))
        role, reason = classify_row(n, r, book, first)
        if role == "excluded":
            continue
        (notes if role == "note" else body).append(r)
    blocks = []
    for col in sorted(set(r.get("column", 0) for r in body)):
        rr = [r for r in body if r.get("column", 0) == col]
        margin = book.get("paragraph_margins", {}).get(
            str(n), sorted(r["bbox"][0] for r in rr)[len(rr) // 5]
        )
        last = None
        for r in rr:
            text = r["text"]
            y = r["bbox"][1]
            verse = any(
                lo <= y <= hi
                for lo, hi in book.get("verse_regions", {}).get(str(n), [])
            )
            kind = r.get("kind") or (
                "verse"
                if verse
                else "heading" if re.fullmatch(r"CHAPTER \d+", text) else "text"
            )
            active_margin = (
                min(
                    (row["bbox"][0] for row in rr if row.get("kind") == "quote"),
                    default=margin,
                )
                if kind == "quote"
                else margin
            )
            indented = r["bbox"][0] > active_margin + 0.018
            gap = last is not None and y - last["bbox"][3] > 0.012
            hanging = n in book.get("reference_pages", [])
            numbered = bool(re.match(r"^\d+\.\s", text)) and hanging and not indented
            new = (
                not blocks
                or blocks[-1]["kind"] != kind
                or kind == "heading"
                or (not indented if hanging else indented)
                or gap
                or numbered
            )
            if not hanging and last and r.get("column", 0) != last.get("column", 0):
                new = True
            if verse:
                new = not blocks or blocks[-1]["kind"] != "verse"
            if new:
                blocks.append(
                    {
                        "kind": kind,
                        "fragments": [{"page": n, "text": text}],
                        "lines": [r],
                        "continuation": (indented if hanging else not indented)
                        and not gap
                        and kind == "text"
                        and not numbered,
                    }
                )
            else:
                block = blocks[-1]
                frag = block["fragments"][-1]
                frag["text"] = (
                    frag["text"] + "\n" + text
                    if verse
                    else join(frag["text"], text, policy=policy)
                )
                block["lines"].append(r)
            last = r
    return blocks, notes


def normalize_block(block, policy, audit):
    text = ""
    sources = []
    breaks = []
    seen = set()
    inline = []
    for line in block["lines"]:
        previous = text
        trim, separator, action = (
            policy.boundary(text, line["text"])
            if block["kind"] != "verse"
            else (0, "\n" if text else "", "space")
        )
        if trim:
            text = text[:-trim]
            for style in inline:
                style["end"] = min(style["end"], len(text))
            for source in sources:
                source["end"] = min(source["end"], len(text))
        start = len(text) + len(separator)
        text = (
            join(previous, line["text"], audit, line["page"], policy)
            if block["kind"] != "verse"
            else text + separator + line["text"]
        )
        inline.extend(
            {**style, "start": start + style["start"], "end": start + style["end"]}
            for style in line.get("inline", [])
        )
        if line["page"] not in seen:
            breaks.append({"page": line["page"], "offset": start})
            seen.add(line["page"])
        sources.append(
            {
                k: line[k]
                for k in ["page", "row_id", "bbox", "text", "column"]
                if k in line
            }
            | {"start": start, "end": len(text)}
        )
    block["text"] = text
    if inline:
        block["inline"] = inline
    block["sources"] = sources
    block["page_breaks"] = breaks
    opening = next(iter(block.get("lines", [])), {}).get("opening")
    if opening and text.lower().startswith(opening.lower()):
        block["text"] = opening + text[len(opening) :]
        block["small_caps"] = opening
        if text[: len(opening)] != opening:
            audit.append(
                {
                    "kind": "opening-case-normalization",
                    "page": block["lines"][0]["page"],
                    "before": text[: len(opening)],
                    "after": opening,
                    "evidence": "Profile small-caps opening",
                }
            )
    # Keep legacy fragments as source evidence, never as another text-export path.
    return block


def reconstruct(pages, book, policy, audit, editorial_edits=()):
    model = []
    for index, (start, end, title) in enumerate(book["chapters"], 1):
        blocks = []
        note_rows = []
        for n in range(start, end + 1):
            bb, nn = page_blocks(n, pages[n], book, audit, n == start, policy)
            if n == start and bb and bb[0]["lines"]:
                bb[0]["lines"][0]["opening"] = book.get("small_caps_openings", {}).get(
                    str(n)
                )
            if (
                bb
                and blocks
                and bb[0]["continuation"]
                and blocks[-1]["kind"] == bb[0]["kind"] == "text"
            ):
                continuation = bb.pop(0)
                blocks[-1]["fragments"].extend(continuation["fragments"])
                blocks[-1]["lines"].extend(continuation["lines"])
            blocks.extend(bb)
            note_rows.extend(nn)
        notes = {}
        targets = {}
        for r in note_rows:
            root = r["page"]
            visited = set()
            while str(root) in book.get("note_continuations", {}):
                if root in visited:
                    raise ValueError("Cyclic note continuation")
                visited.add(root)
                root = book["note_continuations"][str(root)]
            targets[r["page"]] = root
            notes.setdefault(
                root,
                {"kind": "note", "id": f"note-{root}", "root_page": root, "lines": []},
            )["lines"].append(r)
        for block in blocks + list(notes.values()):
            normalize_block(block, policy, audit)
        model.append(
            {
                "title": title,
                "chapter": index,
                "source_pages": [start, end],
                "blocks": blocks,
                "notes": list(notes.values()),
                "note_targets": targets,
            }
        )
    if editorial_edits:
        # Same exact-overlay mechanism; offsets are rebuilt after replacements.
        from common import apply_edits, edit_pattern

        for edit in editorial_edits:
            proxies = []
            for chapter in model:
                for block in chapter["blocks"] + chapter["notes"]:
                    matches = list(re.finditer(edit_pattern(edit), block["text"]))
                    if any(
                        s["page"] == edit["page"]
                        and s["end"] > m.start()
                        and s["start"] < m.end()
                        for s in block["sources"]
                        for m in matches
                    ):
                        proxies.append(
                            {
                                "page": edit["page"],
                                "text": block["text"],
                                "block": block,
                            }
                        )
            apply_edits(proxies, [edit], audit, "editorial-printed-typo")
            for proxy in proxies:
                replace_block(proxy["block"], edit)
                if proxy["block"]["text"] != proxy["text"]:
                    raise ValueError(
                        "Editorial offset update changed text unexpectedly"
                    )
    return model


def replace_block(block, edit):
    """Adjust source/page offsets when an exact editorial overlay changes length."""
    before = edit.get("before", edit.get("printed"))
    after = edit.get("after", edit.get("proposal"))
    from common import edit_pattern

    pattern = edit_pattern(edit)
    for match in reversed(list(re.finditer(pattern, block["text"]))):
        start, end = match.span()
        delta = len(after) - (end - start)
        block["text"] = block["text"][:start] + after + block["text"][end:]

        def adjust(offset):
            return (
                offset
                if offset <= start
                else (
                    offset + delta
                    if offset >= end
                    else start + min(offset - start, len(after))
                )
            )

        for source in block["sources"]:
            source["start"] = adjust(source["start"])
            source["end"] = adjust(source["end"])
        for style in block.get("inline", []):
            style["start"] = adjust(style["start"])
            style["end"] = adjust(style["end"])
        for mark in block["page_breaks"]:
            mark["offset"] = adjust(mark["offset"])


def coverage(pages, book, model):
    mapped = Counter(
        s["row_id"]
        for chapter in model
        for b in chapter["blocks"] + chapter["notes"]
        for s in b["sources"]
    )
    starts = {a for a, _, _ in book["chapters"]}
    selected = {n for a, b, _ in book["chapters"] for n in range(a, b + 1)}
    ledger = []
    errors = []
    for n, rows in pages.items():
        for row in rows:
            ident = row_id(n, row, book.get("source_sha256", ""))
            role, reason = (
                classify_row(n, row, book, n in starts)
                if n in selected
                else ("excluded", "outside-reflow-sections")
            )
            count = mapped[ident]
            if count != (0 if role == "excluded" else 1):
                errors.append(
                    {"row_id": ident, "page": n, "role": role, "mapped": count}
                )
            ledger.append(
                {
                    "row_id": ident,
                    "page": n,
                    "bbox": row["bbox"],
                    "text": row["text"],
                    "role": role,
                    "reason": reason,
                    "mapped": count,
                }
            )
    unknown = set(mapped) - {r["row_id"] for r in ledger}
    if unknown:
        raise ValueError("Text coverage contains unknown source rows")
    if errors:
        raise ValueError("Text coverage failed: " + repr(errors[:5]))
    return {
        "status": "passed",
        "scope": "Reconciled OCR rows, not proof of OCR completeness",
        "counts": dict(Counter(r["role"] for r in ledger)),
        "rows": ledger,
    }


def plain_text(model):
    return (
        "\n\n".join(
            part
            for chapter in model
            for part in [chapter["title"]]
            + [b["text"] for b in chapter["blocks"]]
            + [
                f"Notes, original PDF page {n['root_page']}: {n['text']}"
                for n in chapter["notes"]
            ]
        )
        + "\n"
    )
