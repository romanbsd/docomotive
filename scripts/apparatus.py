"""Link publisher superscripts to numbered endnotes with strict sequence checks."""

import re


def link_symbol_footnotes(model, book):
    """Link unique printed symbols within their original page, preserving text."""
    if not book.get("link_symbol_footnotes", False):
        return {"mode": "not-configured"}
    symbol_pattern = re.compile(r"(?<![*†‡])([*†‡]{1,3})(?![*†‡])")
    linked = []
    unresolved = []
    for chapter in model:
        for note in chapter["notes"]:
            # OCR often joins the marker to the opening word: '*I have...'.
            marker = re.match(r"^\s*([*†‡]{1,3})(?![*†‡])(?=\s|[^\W\d_])", note["text"])
            if not marker:
                unresolved.append(
                    dict(page=note["root_page"], reason="unrecognized-note-prefix")
                )
                continue
            root = note["root_page"]
            symbol = marker[1]
            # Page-group notes can contain several entries. Without separate
            # canonical targets, one symbol must not link to the wrong entry.
            if len(list(symbol_pattern.finditer(note["text"]))) != 1:
                unresolved.append(dict(page=root, reason="multiple-note-symbols"))
                continue
            candidates = []
            for block in chapter["blocks"]:
                if block["kind"] not in ("text", "quote", "verse"):
                    continue
                for match in symbol_pattern.finditer(block["text"]):
                    if match[1] != symbol:
                        continue
                    if match.start() == 0 or (
                        block["text"][match.start() - 1].isdigit()
                        and match.end() < len(block["text"])
                        and block["text"][match.end()].isdigit()
                    ):
                        continue  # A leading bullet or multiplication is not a citation.
                    # Source offsets disambiguate a paragraph crossing pages.
                    if any(
                        s["page"] == root
                        and s["start"] <= match.start()
                        and match.end() <= s["end"]
                        for s in block["sources"]
                    ):
                        candidates.append((block, match))
            if len(candidates) != 1:
                unresolved.append(
                    dict(
                        page=root,
                        reason="nonunique-body-symbol",
                        candidates=len(candidates),
                    )
                )
                continue
            block, match = candidates[0]
            if any(
                s.get("href") and s["start"] < match.end() and s["end"] > match.start()
                for s in block.get("inline", [])
            ):
                unresolved.append(dict(page=root, reason="existing-link"))
                continue
            anchor = f"footnoteref-{root}"
            block.setdefault("inline", []).append(
                dict(
                    start=match.start(),
                    end=match.end(),
                    tags=["sup"],
                    href="#" + note["id"],
                    anchor=anchor,
                    noteref=True,
                )
            )
            note["backlink"] = "#" + anchor
            # Suppress page-group fallbacks throughout the chapter, including
            # continuation leaves. The exact printed symbol now provides access.
            chapter.setdefault("linked_note_roots", []).append(root)
            linked.append(
                dict(page=root, symbol=symbol, anchor=anchor, target=note["id"])
            )
    return dict(symbol_links=len(linked), linked=linked, unresolved=unresolved)


def link_endnotes(model, book):
    if not book.get("endnote_sections"):
        return {"mode": "not-configured"}
    notes = next(c for c in model if c["chapter"] == book["endnote_chapter"])
    sections = {s["heading"]: s for s in book["endnote_sections"]}
    targets = {}
    counts = {}
    section = None
    number = 0
    for block in notes["blocks"]:
        if block["kind"] == "heading":
            if section and number != section["expected_notes"]:
                raise ValueError(
                    "Endnote sequence is incomplete: " + section["heading"]
                )
            section = sections[block["text"]]
            number = 0
            counts[section["source_chapter"]] = 0
            continue
        if not section:
            raise ValueError("Endnote text precedes its section heading")
        block["source_chapter"] = section["source_chapter"]
        match = re.match(r"^(\d+)\.\s", block["text"])
        # Numbers at the hanging content margin are citation continuations,
        # not numbered note starts. Source geometry resolves this ambiguity.
        marker = (
            match
            and block["sources"][0]["bbox"][0] >= book.get("endnote_marker_min", 0)
            and block["sources"][0]["bbox"][0] < book.get("endnote_marker_limit", 0.17)
        )
        if marker:
            current = int(match[1])
            if current != number + 1:
                raise ValueError(
                    f'Endnote sequence error: {section["heading"]}: {number} -> {current}'
                )
            number = current
            key = (section["source_chapter"], number)
            block["id"] = f"endnote-{key[0]}-{key[1]}"
            block["endnote"] = True
            block["backlinks"] = []
            targets[key] = block
            counts[key[0]] += 1
        elif not number:
            raise ValueError("Unnumbered initial endnote")
    if section and number != section["expected_notes"]:
        raise ValueError("Final endnote sequence is incomplete")
    references = 0
    unlinked = []
    for chapter in model:
        if chapter["chapter"] not in counts:
            continue
        seen = {}
        for block in chapter["blocks"]:
            for style in block.get("inline", []):
                label = block["text"][style["start"] : style["end"]].strip()
                if "sup" not in style["tags"] or not label.isdigit():
                    continue
                key = (chapter["chapter"], int(label))
                target = targets.get(key)
                if target is None:
                    raise ValueError(f"Superscript has no endnote: {key}")
                seen[key] = seen.get(key, 0) + 1
                anchor = f"noteref-{key[0]}-{key[1]}-{seen[key]}"
                style.update(
                    href=f'chapter-{notes["chapter"]:02}.xhtml#{target["id"]}',
                    anchor=anchor,
                    noteref=True,
                )
                target["backlinks"].append(
                    f'chapter-{chapter["chapter"]:02}.xhtml#{anchor}'
                )
                references += 1
    for reference in book.get("frontmatter_endnotes", []):
        key = (reference["source_chapter"], reference["number"])
        targets[key]["backlinks"].append(reference["href"])
        references += 1
    chapter_links = 0
    if book.get("endnote_reference_mode") == "chapter":
        for key, target in targets.items():
            if not target["backlinks"]:
                target["backlinks"].append(f"chapter-{key[0]:02}.xhtml#chapter-start")
                chapter_links += 1
    unlinked = [block["id"] for block in targets.values() if not block["backlinks"]]
    if unlinked:
        raise ValueError("Endnotes lack source references: " + ", ".join(unlinked))
    return {
        "status": "passed",
        "endnotes": len(targets),
        "superscript_links": references,
        "chapter_links": chapter_links,
        "section_counts": counts,
        "unlinked_endnotes": unlinked,
    }
