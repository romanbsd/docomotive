"""Canonical source-linked text, layout decisions and coverage before rendering."""

import copy
import re
import statistics
from collections import Counter
from wordfreq import zipf_frequency
from common import digest
from layout import excluded
from paragraph_layout import infer_paragraph_layout, _edge
from typography import verse_evidence, inset_verse_evidence


class JoinPolicy:
    def __init__(
        self,
        protected=(),
        observed=(),
        dictionary=None,
        observed_min_count=2,
        rare_wraps=False,
        language="en",
        noisy_wraps=False,
    ):
        self.words = {w.lower() for w in protected} | {
            w.lower() for w, n in Counter(observed).items() if n >= observed_min_count
        }
        self.dictionary = dictionary
        self.rare_wraps = rare_wraps
        self.language = language
        self.noisy_wraps = noisy_wraps

    def accepts(self, word):
        low = word.lower()
        return (
            low in self.words
            or zipf_frequency(low, self.language) >= 2
            or bool(self.dictionary and self.dictionary.lookup(low))
        )

    def boundary(self, a, b):
        if a.endswith("\u00ad"):
            return 1, "", "remove-discretionary"
        if a.endswith("-") or (self.noisy_wraps and a.endswith("-.")):
            left = re.search(r"([\w]+)(-\.?)$", a)
            right = re.match(r"([^\W\d_]+)", b)
            if left and right:
                combined = left[1] + right[1]
                hyphenated = left[1] + "-" + right[1]
                # A dot misread from scan noise after a wrap dash is removed
                # only at a source-line seam with an attested joined word.
                trim = len(left[2])
                if hyphenated.lower() in self.words:
                    # Retain a known compound's dash while discarding only the
                    # spurious dot; otherwise the noise would join into the word.
                    return (
                        (1, "", "remove-noise-retain-compound")
                        if trim == 2
                        else (0, "", "retain")
                    )
                # A rare but corpus-attested joined form can beat an unattested
                # split by a full Zipf unit (tenfold frequency). Known compounds
                # above still take precedence; this is opt-in for scan repair.
                rare_attested = (
                    self.rare_wraps
                    and zipf_frequency(combined, self.language) >= 1
                    and zipf_frequency(combined, self.language)
                    >= zipf_frequency(hyphenated, self.language) + 1
                )
                return (
                    (trim, "", "remove")
                    if self.accepts(combined) or rare_attested
                    else ((0, "", "retain") if trim == 1 else (0, " ", "space"))
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


def resolve_chapter_body_starts(pages, book, audit):
    """Retain prose crossing a title cutoff when its line geometry is continuous."""
    effective = {}
    if book.get("text_source") == "native":
        return effective
    for page, _, _ in book["chapters"]:
        cutoff = book.get("chapter_body_starts", {}).get(str(page), 0.27)
        if cutoff <= 0:
            continue
        rows = sorted(
            pages.get(page, []), key=lambda r: (r.get("column", 0), r["bbox"][1])
        )

        def prose(row):
            letters = [c for c in row["text"] if c.isalpha()]
            return (
                row.get("kind", "text") == "text"
                and len(letters) >= 40
                and len(row["text"].split()) >= 6
                and sum(c.isupper() for c in letters) / len(letters) < 0.65
                and classify_row(page, row, book, False)[0] == "body"
            )

        samples = [r for r in rows if r["bbox"][1] >= cutoff and prose(r)]
        for column in sorted({r.get("column", 0) for r in samples}):
            reference = [r for r in samples if r.get("column", 0) == column]
            # Four broad prose lines establish a local body measure; sparse
            # title/epigraph leaves retain their reviewed cutoff.
            if len(reference) < 4:
                continue
            width = statistics.median(r["bbox"][2] - r["bbox"][0] for r in reference)
            height = statistics.median(r["bbox"][3] - r["bbox"][1] for r in reference)
            margin = statistics.median(r["bbox"][0] for r in reference)
            pitches = [
                (b["bbox"][1] + b["bbox"][3] - a["bbox"][1] - a["bbox"][3]) / 2
                for a, b in zip(reference, reference[1:])
                if b["bbox"][1] > a["bbox"][1]
            ]
            if not pitches or min(width, height) <= 0:
                continue
            pitch = statistics.median(pitches)

            def consistent(row):
                return (
                    prose(row)
                    and 0.8 * width <= row["bbox"][2] - row["bbox"][0] <= 1.2 * width
                    and 0.7 * height <= row["bbox"][3] - row["bbox"][1] <= 1.3 * height
                    and abs(row["bbox"][0] - margin) <= 0.03
                )

            rr = [r for r in rows if r.get("column", 0) == column]
            anchor = next(
                (
                    i
                    for i, r in enumerate(rr)
                    if r["bbox"][1] >= cutoff and consistent(r)
                ),
                None,
            )
            if anchor is None or rr[anchor]["bbox"][1] - cutoff > 1.4 * pitch:
                continue
            index = anchor
            while index:
                before, after = rr[index - 1], rr[index]
                distance = (
                    after["bbox"][1]
                    + after["bbox"][3]
                    - before["bbox"][1]
                    - before["bbox"][3]
                ) / 2
                # Ordinary line pitch bridges a wrapped paragraph; title/quote
                # separation, larger fonts and intervening labels stop the walk.
                if (
                    not consistent(before)
                    or not 0.65 * pitch <= distance <= 1.4 * pitch
                ):
                    break
                index -= 1
            if index == anchor or rr[index]["bbox"][1] >= cutoff:
                continue
            recovered = rr[index:anchor]
            inferred = rr[index]["bbox"][1]
            effective[str(page)] = min(effective.get(str(page), cutoff), inferred)
            audit.append(
                dict(
                    page=page,
                    kind="chapter-body-boundary",
                    configured=cutoff,
                    inferred=inferred,
                    column=column,
                    pitch=pitch,
                    recovered_rows=[
                        dict(text=r["text"], bbox=r["bbox"]) for r in recovered
                    ],
                    evidence="continuous prose crossing chapter-title cutoff",
                )
            )
    return effective


def classify_row(page, row, book, first=False):
    y = row["bbox"][1]
    text = row["text"].strip()
    if row.get("kind") == "absorbed-note-marker":
        return "excluded", "represented-by-recovered-note"
    if str(page) in book.get("excluded_pages", {}):
        return "excluded", "profile-excluded-page"
    for figure in book.get("figures", []):
        x0, y0, x1, y1 = figure["rect"]
        cx = (row["bbox"][0] + row["bbox"][2]) / 2
        cy = (row["bbox"][1] + row["bbox"][3]) / 2
        if figure["page"] == page and x0 <= cx <= x1 and y0 <= cy <= y1:
            return "excluded", "preserved-figure-region"
    if first and y < book.get("_chapter_body_starts", {}).get(
        str(page), book.get("chapter_body_starts", {}).get(str(page), 0.27)
    ):
        return "excluded", "chapter-title"
    if y >= book.get("bottom_margin_cutoffs", {}).get(str(page), 2):
        return "excluded", "profile-bottom-margin"
    if y < book.get("upper_margin_cutoff", 0.035):
        return "excluded", "upper-margin-artifact"
    if excluded(page, row, book.get("_layout", {})):
        return "excluded", "recurring-margin"
    letters = [c for c in text if c.isalpha()]
    if (
        y < book.get("uppercase_margin_cutoff", 0.10)
        and letters
        and sum(c.isupper() for c in letters) / len(letters) > 0.8
    ):
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
        if n in book.get("glossary_pages", []):
            label = re.match(r"^[A-Z][A-Z /-]{2,40}\s*:", r["text"])
            if label:
                r["paragraph_start"] = True
                r.setdefault("inline", []).append(
                    dict(start=0, end=label.end(), tags=["strong"])
                )
        for region in book.get("row_regions", []):
            if region["page"] == n and region["lo"] <= r["bbox"][1] < region["hi"]:
                r["kind"] = region["kind"]
        for term in sorted(
            book.get("glossary_terms", {}).get(str(n), []), key=len, reverse=True
        ):
            if re.match(r"^" + re.escape(term) + r"(?=\s|[:;]|$)", r["text"]):
                r["paragraph_start"] = True
                r.setdefault("inline", []).append(
                    {
                        "start": 0,
                        "end": len(term),
                        "tags": [book.get("glossary_style", "strong")],
                    }
                )
                break
        r.setdefault("row_id", row_id(n, r, book.get("source_sha256", "")))
        role, reason = classify_row(n, r, book, first)
        if role == "excluded":
            continue
        (notes if role == "note" else body).append(r)
    blocks = []
    for col in sorted(set(r.get("column", 0) for r in body)):
        rr = [r for r in body if r.get("column", 0) == col]
        inferred_verse = (
            verse_evidence(rr, book.get("_body_width", 0.8))
            if book.get("text_source") != "native"
            and book.get("infer_verse", True)
            and n not in book.get("reference_pages", [])
            else set()
        )
        if book.get("infer_inset_verse") and not (
            book.get("text_source") == "native" or n in book.get("reference_pages", [])
        ):
            inferred_verse |= inset_verse_evidence(rr, book.get("_body_width", 0.8))
        if inferred_verse:
            audit.append(dict(page=n, kind="verse-layout", rows=sorted(inferred_verse)))
            for r in rr:
                if r["row_id"] in inferred_verse:
                    r["kind"] = "verse"
        margin = book.get("paragraph_margins", {}).get(
            str(n), sorted(r["bbox"][0] for r in rr)[len(rr) // 5]
        )
        geometry = {}
        hanging = n in book.get("hanging_pages", book.get("reference_pages", []))
        if hanging and book.get("recover_hanging_margins"):
            # Skew can move entry starts beyond a fixed indentation threshold.
            # Reuse the stable left envelope; hanging text cannot prove quotes.
            edge = _edge(rr, 0, 0.2) if len(rr) >= 8 else None
            if edge:
                geometry = {
                    r["row_id"]: dict(
                        left_offset=r["bbox"][0]
                        - edge["intercept"]
                        - edge["slope"] * r["bbox"][1]
                    )
                    for r in rr
                }
            audit.append(dict(page=n, kind="hanging-margin", column=col, envelope=edge))
        if (
            book.get("text_source") != "native"
            and not hanging
            and str(n) not in book.get("paragraph_margins", {})
            and str(n) not in book.get("glossary_terms", {})
        ):
            layout_rows = [
                r
                for r in rr
                if not any(
                    lo <= r["bbox"][1] <= hi
                    for lo, hi in book.get("verse_regions", {}).get(str(n), [])
                )
            ]
            geometry, evidence = infer_paragraph_layout(layout_rows)
            audit.append(dict(page=n, kind="paragraph-layout", column=col, **evidence))
        # Glyph boxes shrink on descender-only short lines. Their apparent
        # whitespace is not a paragraph gap: use local line pitch as a check.
        pitches = [
            (b["bbox"][1] + b["bbox"][3] - a["bbox"][1] - a["bbox"][3]) / 2
            for a, b in zip(rr, rr[1:])
            if a.get("kind", "text") == b.get("kind", "text") == "text"
        ]
        pitch = statistics.median(pitches) if len(pitches) >= 8 else None
        body_height = statistics.median(r["bbox"][3] - r["bbox"][1] for r in rr)
        body_width = statistics.median(r["bbox"][2] - r["bbox"][0] for r in rr)
        last = None
        for r in rr:
            text = r["text"]
            y = r["bbox"][1]
            verse = r.get("kind") == "verse" or any(
                lo <= y <= hi
                for lo, hi in book.get("verse_regions", {}).get(str(n), [])
            )
            kind = r.get("kind") or (
                "verse"
                if verse
                else "heading" if re.fullmatch(r"CHAPTER \d+", text) else "text"
            )
            geo = geometry.get(r["row_id"], {}) if kind == "text" else {}
            if geo.get("kind"):
                kind = r["kind"] = geo["kind"]
            active_margin = (
                min(
                    (row["bbox"][0] for row in rr if row.get("kind") == "quote"),
                    default=margin,
                )
                if kind == "quote"
                else margin
            )
            indented = r["bbox"][0] > active_margin + 0.018
            if geo:
                indented = geo["left_offset"] > 0.018
            gap = last is not None and y - last["bbox"][3] > 0.012
            if (
                gap
                and pitch
                and not indented
                and not hanging
                and book.get("text_source") != "native"
                and kind == "text"
                and last.get("kind", "text") == "text"
                and not r.get("paragraph_start")
                # Restrict the relaxation to short, shallow continuation boxes
                # after a full line, with at most 30% local pitch variation.
                and r["bbox"][2] - r["bbox"][0] < 0.5 * body_width
                and r["bbox"][3] - y < 0.8 * body_height
                and last["bbox"][2] - last["bbox"][0] >= 0.8 * body_width
                and 0.7 * pitch
                <= (y + r["bbox"][3] - last["bbox"][1] - last["bbox"][3]) / 2
                <= 1.3 * pitch
            ):
                gap = False
                audit.append(
                    dict(
                        page=n,
                        kind="short-line-pitch-continuation",
                        text=text,
                        bbox=r["bbox"],
                        pitch=pitch,
                    )
                )
            paragraph_indent = indented
            if book.get("continuous_indented_rows") and last is not None:
                paragraph_indent = (
                    indented and abs(r["bbox"][0] - last["bbox"][0]) > 0.012
                )
            if geo and last is not None and last.get("row_id") in geometry:
                paragraph_indent = (
                    indented
                    and abs(
                        geo["left_offset"] - geometry[last["row_id"]]["left_offset"]
                    )
                    > 0.012
                )
            numbered = bool(re.match(r"^\d+\.\s", text)) and hanging and not indented
            new = (
                not blocks
                or blocks[-1]["kind"] != kind
                or kind == "heading"
                or (not indented if hanging else paragraph_indent)
                or gap
                or numbered
                or r.get("paragraph_start", False)
            )
            if not hanging and last and r.get("column", 0) != last.get("column", 0):
                new = True
            if (
                book.get("repair_word_wraps")
                and new
                and last
                and blocks
                and kind in ("text", "quote")
                and blocks[-1]["kind"] == kind
                and r.get("column", 0) == last.get("column", 0)
                and not hanging
                and not gap
                # A normal-margin continuation can inherit a bogus style-based
                # paragraph flag. An indented new paragraph cannot override it.
                and not paragraph_indent
                and re.search(r"[^\W\d_]{2,}-\.?$", last["text"])
                and re.match(r"^[^\W\d_]{2,}", text)
                and text[:2].islower()
                and (policy or DEFAULT_POLICY).boundary(last["text"], text)[2]
                == "remove"
            ):
                new = False
                audit.append(
                    dict(
                        page=n,
                        kind="verified-word-wrap-boundary",
                        before=last["text"].split()[-1] + " " + text.split()[0],
                    )
                )
            if verse:
                new = (
                    not blocks
                    or blocks[-1]["kind"] != "verse"
                    or (last is not None and y - last["bbox"][3] > 0.035)
                )
            if kind == "list-item":
                # Item markers start blocks; measured hanging lines belong to
                # that item even when their indentation changes from line one.
                new = (
                    not blocks
                    or blocks[-1]["kind"] != "list-item"
                    or not r.get("list_continuation")
                )
            if (
                book.get("native_heading_merge")
                and kind == "heading"
                and blocks
                and last
                and last.get("native")
                and r.get("native")
                and blocks[-1]["kind"] == "heading"
                and abs(r["font_size"] - last["font_size"]) < 0.15
                # Wrapped title lines overlap or nearly touch vertically.
                # Independently spaced headings keep their own boundaries.
                and -0.015 <= y - last["bbox"][3] <= 0.005
            ):
                new = False
            if new:
                blocks.append(
                    {
                        "kind": kind,
                        "fragments": [{"page": n, "text": text}],
                        "lines": [r],
                        "continuation": (indented if hanging else not indented)
                        and not gap
                        and kind in ("text", "quote")
                        and not numbered
                        and not r.get("paragraph_start", False),
                    }
                )
                if geo.get("kind") == "quote":
                    blocks[-1]["first_line_indent"] = indented
                    if geo.get("quote_font_scale"):
                        blocks[-1]["quote_font_scale"] = geo["quote_font_scale"]
                elif kind == "quote" and r.get("quote_font_scale"):
                    blocks[-1]["quote_font_scale"] = r["quote_font_scale"]
                    blocks[-1]["first_line_indent"] = indented
                if n in book.get("index_pages", []) and book.get(
                    "native_index_indents"
                ):
                    # Map normalized source indents to a roughly 40-em text
                    # measure, preserving child-entry hierarchy at reader sizes.
                    left = min(row["bbox"][0] for row in rr)
                    blocks[-1]["index_indent_em"] = round(
                        min(4, max(0, (r["bbox"][0] - left) * 40)), 2
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
    if block["kind"] == "source-gap":
        return block
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
            gap = next((g for g in book.get("source_gaps", []) if g["page"] == n), None)
            if gap:
                # Missing leaves cannot supply a sentence continuation. This
                # notice is separate from OCR source rows and their coverage.
                blocks.append(
                    dict(
                        kind="source-gap",
                        text=gap["text"],
                        lines=[],
                        fragments=[],
                        sources=[],
                        page_breaks=[],
                    )
                )
            boundary = book.get("cross_page_continuations", {}).get(str(n))
            if boundary:
                if (
                    not blocks
                    or not bb
                    or not blocks[-1]["lines"]
                    or not blocks[-1]["lines"][-1]["text"].endswith(boundary["before"])
                    or not bb[0]["lines"][0]["text"].startswith(boundary["after"])
                ):
                    raise ValueError(
                        f"Cross-page continuation precondition changed: {n}"
                    )
                bb[0]["continuation"] = True
            if n == start and bb and bb[0]["lines"]:
                bb[0]["lines"][0]["opening"] = book.get("small_caps_openings", {}).get(
                    str(n)
                )
            if (
                bb
                and blocks
                and bb[0]["continuation"]
                and blocks[-1]["kind"] == bb[0]["kind"]
                and bb[0]["kind"] in ("text", "quote")
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
