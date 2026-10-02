"""Profile-reviewed source crops placed among canonical text blocks."""

import pymupdf
from common import digest


def incorporate_figures(model, book, doc, files):
    report = []
    for figure in book.get("figures", []):
        page = figure["page"]
        chapter = next(
            (c for c in model if c["source_pages"][0] <= page <= c["source_pages"][1]),
            None,
        )
        if chapter is None:
            raise ValueError(f"Figure page is outside reflow chapters: {page}")
        source = doc[page - 1]
        x0, y0, x1, y1 = figure["rect"]
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("Invalid normalized figure rectangle")
        rect = pymupdf.Rect(
            x0 * source.rect.width,
            y0 * source.rect.height,
            x1 * source.rect.width,
            y1 * source.rect.height,
        )
        image = figure["name"] + ".jpg"
        data = source.get_pixmap(dpi=300, clip=rect).pil_tobytes(
            format="JPEG", quality=95
        )
        path = "OEBPS/" + image
        if path in files:
            raise ValueError("Duplicate figure asset: " + image)
        files[path] = data
        block = {
            "kind": "figure",
            "image": image,
            "alt": figure["alt"],
            "text": "",
            "sources": [],
            "page_breaks": [],
            "source_page": page,
            "rect": figure["rect"],
        }
        blocks = chapter["blocks"]
        position = len(blocks)
        for i, candidate in enumerate(blocks):
            if candidate.get("sources"):
                first = candidate["sources"][0]
                if first["page"] > page or (
                    first["page"] == page and first["bbox"][1] >= y0
                ):
                    position = i
                    break
        blocks.insert(position, block)
        report.append(
            {
                **figure,
                "image": image,
                "sha256": digest(data),
                "chapter": chapter["chapter"],
            }
        )
    return report
