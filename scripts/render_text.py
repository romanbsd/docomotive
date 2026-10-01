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
        label = str(n + book.get("printed_page_offset", 0))
        page_links.append((name + "#page-" + str(n), label))
        anchor = f'<span epub:type="pagebreak" role="doc-pagebreak" id="page-{n}" aria-label="{label}"/>'
        marks.append((mark["offset"], anchor))
    text = block["text"]
    pieces = []
    last = 0

    def escaped(value, start=0):
        value = html.escape(value)
        opening = block.get("small_caps")
        if start == 0 and opening and value.startswith(html.escape(opening)):
            prefix = html.escape(opening)
            value = (
                '<span class="smallcaps">' + prefix + "</span>" + value[len(prefix) :]
            )
        if index_refs:

            def link(match):
                ref = index_refs.get(match.group().rstrip("n"))
                return f'<a href="{ref}">{match.group()}</a>' if ref else match.group()

            value = re.sub(r"(?<!\w)\d+n?(?!\w)", link, value)
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
    if block["kind"] == "verse":
        return '<div class="verse"><p>' + content.replace("\n", "<br/>") + "</p></div>"
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
            f'<aside epub:type="endnote" id="{note["id"]}"><h2>{label}</h2><p>{html.escape(note["text"])}</p><p><a href="#page-{root}" role="doc-backlink">Return to text</a></p></aside>'
        )
    return "".join(body) + "</section>"
