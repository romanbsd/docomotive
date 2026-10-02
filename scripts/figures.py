"""Profile-reviewed source crops placed among canonical text blocks."""

import pymupdf
import io
import re
from PIL import Image
from common import digest


def crop_artwork(page, rect, dpi=300, excluded_overlays=()):
    """Use isolated scan pixels only when no visible labels would be lost."""
    images = page.get_image_info(xrefs=True)
    scans = [
        im
        for im in images
        if im.get("xref")
        and pymupdf.Rect(im["bbox"]).contains(rect)
        and pymupdf.Rect(im["bbox"]).get_area() >= 0.9 * page.rect.get_area()
        and abs(im["transform"][1]) < 1e-6
        and abs(im["transform"][2]) < 1e-6
        and im["transform"][0] > 0
        and im["transform"][3] > 0
    ]
    visible = any(
        span["type"] != 3 and pymupdf.Rect(span["bbox"]).intersects(rect)
        for span in page.get_texttrace()
    )
    if len(scans) == 1 and not visible:
        scan = scans[0]
        bounds = pymupdf.Rect(scan["bbox"])
        raw = page.parent.extract_image(scan["xref"])
        im = Image.open(io.BytesIO(raw["image"])).convert("RGB")
        box = (
            round((rect.x0 - bounds.x0) / bounds.width * im.width),
            round((rect.y0 - bounds.y0) / bounds.height * im.height),
            round((rect.x1 - bounds.x0) / bounds.width * im.width),
            round((rect.y1 - bounds.y0) / bounds.height * im.height),
        )
        result = im.crop(box)
        out = io.BytesIO()
        result.save(out, format="JPEG", quality=95)
        return out.getvalue(), dict(
            method="embedded-page-scan", xref=scan["xref"], pixel_size=list(result.size)
        )
    removed = []
    temporary = None
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                box = pymupdf.Rect(span["bbox"])
                if box.intersects(rect) and any(
                    span["font"] == rule["font"]
                    and span["size"] >= rule.get("min_size", 0)
                    and re.fullmatch(rule["pattern"], span["text"].strip())
                    for rule in excluded_overlays
                ):
                    removed.append(span)
    if removed:
        temporary = pymupdf.open()
        temporary.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
        page = temporary[0]
        for span in removed:
            page.add_redact_annot(
                pymupdf.Rect(span["bbox"]), fill=False, cross_out=False
            )
        page.apply_redactions(images=0, graphics=0, text=0)
    data = page.get_pixmap(dpi=dpi, clip=rect).pil_tobytes(format="JPEG", quality=95)
    if temporary:
        temporary.close()
    return data, dict(method="rendered-page", dpi=dpi, excluded_overlays=removed)


def outside_figures(lines, book, page, audit=None):
    """Mask artwork before row merging, so image glyphs cannot join nearby prose."""
    retained = []
    for line in lines:
        box = line["bbox"]
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        figure = next(
            (
                f
                for f in book.get("figures", [])
                if f["page"] == page
                and f["rect"][0] <= cx <= f["rect"][2]
                and f["rect"][1] <= cy <= f["rect"][3]
            ),
            None,
        )
        if figure is None:
            retained.append(line)
        elif audit is not None:
            audit.append(
                dict(
                    page=page,
                    kind="excluded-figure-ocr",
                    text=line["text"],
                    bbox=box,
                    figure=figure["name"],
                )
            )
    return retained


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
        data, crop_evidence = crop_artwork(
            source, rect, excluded_overlays=book.get("artwork_overlay_exclusions", [])
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
            "page_breaks": [dict(page=page, offset=0)],
            "source_page": page,
            "rect": figure["rect"],
            "narrow": rect.width / rect.height < 0.22,
            "display_width": (
                round(min(100, max(10, (x1 - x0) / 0.8 * 100)), 1)
                if book.get("source_relative_figures")
                else None
            ),
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
                "crop_source": crop_evidence,
            }
        )
    return report
