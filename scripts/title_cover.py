"""Deterministic typographic SVG cover for sources without cover artwork."""

import html
import textwrap


def svg_cover(book):
    lines = []
    y = 260
    for value, size, width in [
        (book["title"], 45, 27),
        (book.get("subtitle", ""), 29, 38),
    ]:
        for line in textwrap.wrap(value, width):
            lines.append(
                f'<text x="80" y="{y}" font-size="{size}">{html.escape(line)}</text>'
            )
            y += size * 1.4
        y += 40
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="1200" viewBox="0 0 800 1200"><rect width="800" height="1200" fill="#f4eee1"/><path d="M80 150H720 M80 970H720" stroke="#966b34" stroke-width="3"/><g fill="#243e45" font-family="serif">'
        + "".join(lines)
        + f'<text x="80" y="900" font-size="32">{html.escape(book["author"])}</text><text x="80" y="1040" font-size="23">{html.escape(book.get("cover_imprint", book["publisher"]))}</text></g></svg>'
    ).encode()
