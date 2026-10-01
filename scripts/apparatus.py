"""Link publisher superscripts to numbered endnotes with strict sequence checks."""

import re


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
    unlinked = [block["id"] for block in targets.values() if not block["backlinks"]]
    if unlinked:
        raise ValueError("Endnotes lack source references: " + ", ".join(unlinked))
    return {
        "status": "passed",
        "endnotes": len(targets),
        "superscript_links": references,
        "section_counts": counts,
        "unlinked_endnotes": unlinked,
    }
