"""Render canonical text with annotations; never reconstruct words in HTML."""

import html
import re


def block_html(block, book, seen, page_links, name, index_refs=None):
    marks = []
    pages = []
    for mark in block["page_breaks"]:
        n = mark["page"]
        if n in seen:
            continue
        seen.add(n)
        pages.append(n)
        label = book.get("page_labels", {}).get(
            str(n), str(n + book.get("printed_page_offset", 0))
        )
        page_links.append((name + "#page-" + str(n), label))
        anchor = f'<span epub:type="pagebreak" role="doc-pagebreak" id="page-{n}" aria-label="{label}"/>'
        marks.append((mark["offset"], anchor))
    text = block["text"]
    pieces = []
    last = 0

    def escaped(value, start=0):
        end = start + len(value)
        cuts = sorted(
            {start, end}
            | {
                max(start, min(end, style[edge]))
                for style in block.get("inline", [])
                for edge in ["start", "end"]
            }
        )
        rendered = []
        for lo, hi in zip(cuts, cuts[1:]):
            piece = html.escape(value[lo - start : hi - start])
            tags = []
            for style in block.get("inline", []):
                if style["start"] <= lo and style["end"] >= hi:
                    tags.extend(style["tags"])
            for tag in reversed(list(dict.fromkeys(tags))):
                piece = f"<{tag}>" + piece + f"</{tag}>"
            for style in block.get("inline", []):
                if style.get("href") and style["start"] <= lo and style["end"] >= hi:
                    anchor = (
                        ' id="' + html.escape(style["anchor"], quote=True) + '"'
                        if lo == style["start"]
                        else ""
                    )
                    piece = (
                        '<a epub:type="noteref" role="doc-noteref"'
                        + anchor
                        + ' href="'
                        + html.escape(style["href"], quote=True)
                        + '">'
                        + piece
                        + "</a>"
                    )
            rendered.append(piece)
        value = "".join(rendered)
        opening = block.get("small_caps")
        if start == 0 and opening and value.startswith(html.escape(opening)):
            prefix = html.escape(opening)
            value = (
                '<span class="smallcaps">' + prefix + "</span>" + value[len(prefix) :]
            )
        if index_refs:

            def link(match):
                label = match.group()
                first = match.group(1)
                note = match.group(2)
                ref = (
                    book.get("_index_note_refs", {}).get(first + ":" + note)
                    if note
                    else None
                )
                ref = ref or index_refs.get(first)
                return f'<a href="{ref}">{label}</a>' if ref else label

            value = re.sub(
                r"(?<!\w)(\d+)(?:[–-]\d+)?(?:n{1,2}(\d+)(?:[–-]\d+)?)?(?:ff)?",
                link,
                value,
            )
            if book.get("page_labels"):
                value = re.sub(
                    r"(?<!\w)(?:ix|x)(?!\w)",
                    lambda m: (
                        f'<a href="{index_refs[m.group()]}">{m.group()}</a>'
                        if m.group() in index_refs
                        else m.group()
                    ),
                    value,
                )
        return value

    for offset, anchor in sorted(marks):
        pieces.extend([escaped(text[last:offset], last), anchor])
        last = offset
    pieces.append(escaped(text[last:], last))
    content = "".join(pieces)
    # Note references are attached to the complete paragraph, never between word fragments.
    targets = book.get("_note_targets", {})
    for n in pages:
        if n in targets:
            content += f' <a class="note-link" epub:type="noteref" role="doc-noteref" href="#note-{targets[n]}" aria-label="Notes for original page {n+book.get("printed_page_offset",0)}"><sup>[note]</sup></a>'
    if block.get("endnote"):
        backlinks = " ".join(
            '<a role="doc-backlink" href="'
            + html.escape(ref, quote=True)
            + '">Return to text</a>'
            for ref in block.get("backlinks", [])
        )
        return (
            '<aside epub:type="endnote" id="'
            + html.escape(block["id"], quote=True)
            + '"><p class="reference">'
            + content
            + '</p><p class="note-backlinks">'
            + backlinks
            + "</p></aside>"
        )
    if block["kind"] == "verse":
        return '<div class="verse"><p>' + content.replace("\n", "<br/>") + "</p></div>"
    if block["kind"] == "attribution":
        return '<p class="attribution">' + content + "</p>"
    if block["kind"] == "quote":
        return "<blockquote><p>" + content + "</p></blockquote>"
    if block["kind"] == "heading":
        return "<h2>" + content + "</h2>"
    css = ' class="reference"' if book.get("_hanging") else ""
    return f"<p{css}>{content}</p>"


def notes_html(notes, book):
    if not notes:
        return ""
    body = ['<section class="notes" epub:type="endnotes"><h2>Notes</h2>']
    for note in notes:
        root = note["root_page"]
        pages = sorted({s["page"] for s in note["sources"]})
        offset = book.get("printed_page_offset", 0)
        label = "Original page " + str(root + offset)
        if len(pages) > 1:
            label += "–" + str(pages[-1] + offset)
        body.append(
            f'<aside epub:type="endnote" id="{note["id"]}"><h2>{label}</h2>'
            + block_html(
                {**note, "kind": "text", "page_breaks": []}, book, set(), [], ""
            )
            + f'<p><a href="#page-{root}" role="doc-backlink">Return to text</a></p></aside>'
        )
    return "".join(body) + "</section>"
