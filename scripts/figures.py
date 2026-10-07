"""Profile-reviewed source crops placed among canonical text blocks."""

import pymupdf
import io
import re
import copy
import statistics
from PIL import Image
from common import digest


def crop_artwork(page, rect, dpi=300, excluded_overlays=(), raster_only=False):
    """Legacy JPEG export; its encoding remains unchanged for existing books."""
    result, evidence = crop_artwork_pixels(
        page, rect, dpi, excluded_overlays, raster_only
    )
    out = io.BytesIO()
    options = dict(format="JPEG", quality=95)
    if evidence["method"] == "rendered-page":
        options["dpi"] = result.info["dpi"]
    result.save(out, **options)
    return out.getvalue(), evidence


def crop_artwork_pixels(page, rect, dpi=300, excluded_overlays=(), raster_only=False):
    """Use isolated scan pixels only when no visible labels would be lost."""
    if raster_only:
        # Explicit source-reviewed mode: discard reconstructed PDF text overlays.
        from scan_pixels import isolated_scan

        result, evidence = isolated_scan(page, rect)
        return result, evidence
    images = page.get_image_info(xrefs=True)
    scans = [
        im
        for im in images
        if im.get("xref") and pymupdf.Rect(im["bbox"]).contains(rect)
        # Framed scans commonly occupy about 82% of the PDF page. Only use
        # their pixels for contained crops without visible text overlays.
        and pymupdf.Rect(im["bbox"]).get_area() >= 0.8 * page.rect.get_area()
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
        return result, dict(
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
    pixmap = page.get_pixmap(dpi=dpi, clip=rect)
    result = pixmap.pil_image()
    result.info["dpi"] = (pixmap.xres, pixmap.yres)
    if temporary:
        temporary.close()
    return result, dict(method="rendered-page", dpi=dpi, excluded_overlays=removed)


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


def detect_figure_captions(pages, book, audit):
    """Identify small, adjacent caption runs with an explicit figure/photo cue."""
    if not book.get("source_figure_anchors"):
        return
    cue = re.compile(r"(?i)(?:^|\s)(?:фото|рис\.|fig\.|figure|photo)(?:\s|$)")
    for figure in book.get("figures", []):
        number = figure["page"]
        rows = sorted(
            [
                r
                for r in pages[number]
                if r["bbox"][1] < book.get("note_starts", {}).get(str(number), 2)
            ],
            key=lambda r: r["bbox"][1],
        )
        broad = [r["bbox"][3] - r["bbox"][1] for r in rows if len(r["text"]) >= 15]
        if len(broad) < 8:
            continue
        body = statistics.median(broad)
        bottom = figure["rect"][3]
        eligible = [
            r
            for r in rows
            if bottom - 0.15 <= r["bbox"][1] <= bottom + 0.1
            and r["bbox"][3] - r["bbox"][1] <= 0.86 * body
        ]
        groups = []
        for row in eligible:
            if (
                groups
                and row["bbox"][1] - groups[-1][-1]["bbox"][3] <= body
                and abs(row["bbox"][0] - groups[-1][-1]["bbox"][0]) < 0.04
            ):
                groups[-1].append(row)
            else:
                groups.append([row])
        for group in groups:
            # A short small-type run plus an explicit credit/figure label is
            # stronger than font size alone, which also describes footnotes.
            if not 1 <= len(group) <= 4 or not cue.search(
                " ".join(r["text"] for r in group)
            ):
                continue
            for row in group:
                row["kind"] = "caption"
                row["figure_caption_for"] = figure["name"]
            audit.append(
                dict(
                    page=number,
                    kind="source-figure-caption",
                    figure=figure["name"],
                    rows=[r["bbox"] for r in group],
                )
            )


def split_source_block(block, source_index):
    """Split at a source-row boundary, retaining text and annotation offsets."""
    sources = block["sources"]
    # Avoid splitting a word joined across a line/page seam. Keep the complete
    # next source line before the figure instead of breaking that word in two.
    while source_index < len(sources):
        cut = sources[source_index]["start"]
        if cut == 0 or not (
            block["text"][cut - 1].isalpha() and block["text"][cut].isalpha()
        ):
            break
        source_index += 1
    if source_index == len(sources):
        return None
    cut = sources[source_index]["start"]
    parts = []
    for start, end, selected in [
        (0, cut, sources[:source_index]),
        (cut, len(block["text"]), sources[source_index:]),
    ]:
        part = copy.deepcopy(block)
        # Spaces separating source rows are not paragraph content.
        while start < end and block["text"][start].isspace():
            start += 1
        while end > start and block["text"][end - 1].isspace():
            end -= 1
        part["text"] = block["text"][start:end]
        part["sources"] = [
            {
                **s,
                "start": max(0, s["start"] - start),
                "end": min(end, s["end"]) - start,
            }
            for s in selected
        ]
        lines = block.get("lines", [])
        part["lines"] = copy.deepcopy(
            lines[:source_index] if start == 0 else lines[source_index:]
        )
        part["fragments"] = (
            []
        )  # Canonical sources own text and offsets after splitting.
        part["inline"] = [
            {
                **style,
                "start": max(start, style["start"]) - start,
                "end": min(end, style["end"]) - start,
            }
            for style in block.get("inline", [])
            if style["start"] < end and style["end"] > start
        ]
        part["page_breaks"] = []
        seen = set()
        for source in part["sources"]:
            if source["page"] not in seen:
                seen.add(source["page"])
                part["page_breaks"].append(
                    dict(page=source["page"], offset=source["start"])
                )
        parts.append(part)
    parts[1]["first_line_indent"] = False
    parts[1]["paragraph_indent"] = False
    parts[1]["source_continuation"] = True
    parts[1]["continuation"] = True
    parts[1].pop("small_caps", None)
    return parts


def figure_position(blocks, page, y, split=False):
    for index, block in enumerate(blocks):
        sources = block.get("sources", [])
        after = [
            i
            for i, s in enumerate(sources)
            if s["page"] > page or (s["page"] == page and s["bbox"][1] >= y)
        ]
        if not after:
            continue
        if after[0] == 0:
            return index
        if split and block["kind"] in ("text", "quote"):
            parts = split_source_block(block, after[0])
            if parts:
                blocks[index : index + 1] = parts
                return index + 1
    return len(blocks)


def incorporate_figures(model, book, doc, files, *, cleanup_dir=None):
    report = []
    cleanup_rows = []
    cleanup = book.get("figure_cleanup", "none")
    if cleanup not in ("none", "white"):
        raise ValueError("Unknown figure_cleanup policy")
    if cleanup == "white" and cleanup_dir is None:
        raise ValueError("Figure cleanup requires an original-crop review directory")
    if cleanup == "white":
        from pathlib import Path

        directory = Path(cleanup_dir)
        directory.mkdir(parents=True, exist_ok=True)
        # A new build invalidates pixel checks from the previous exported assets.
        (directory / "verification.json").unlink(missing_ok=True)
    for figure in book.get("figures", []):
        if cleanup == "white" and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*", figure["name"]
        ):
            raise ValueError("Unsafe figure name")
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
        color_export = "figure_color_mode" in book
        cropper = (
            crop_artwork_pixels if cleanup == "white" or color_export else crop_artwork
        )
        crop, crop_evidence = cropper(
            source,
            rect,
            excluded_overlays=book.get("artwork_overlay_exclusions", []),
            raster_only=book.get("scan_raster_only", False),
        )
        cleanup_evidence = None
        data = crop
        if cleanup != "white" and color_export:
            from figure_cleanup import export_color, jpeg_export

            # An explicit source-reviewed color policy also works without
            # paper normalization; encode once from uncompressed crop pixels.
            export, color_evidence = export_color(crop, book["figure_color_mode"])
            data = jpeg_export(export, quality=95)
            crop_evidence["color"] = color_evidence
        if cleanup == "white":
            from pathlib import Path
            from figure_cleanup import (
                normalize_paper,
                png_bytes,
                jpeg_export,
                export_color,
            )

            # Normalize the cropped source pixels before the sole JPEG encode.
            normalized, cleanup_evidence = normalize_paper(crop)
            original = png_bytes(crop)
            master = png_bytes(normalized)
            # Extra JPEG precision costs little on sparse diagrams and retains
            # faint strokes better; dense photographs use compact quality 95.
            quality = 98 if cleanup_evidence.get("kind") == "line-art" else 95
            export, color_evidence = export_color(
                normalized, book.get("figure_color_mode", "auto")
            )
            data = jpeg_export(export, quality=quality)
            directory = Path(cleanup_dir)
            (directory / "originals").mkdir(parents=True, exist_ok=True)
            (directory / "results").mkdir(exist_ok=True)
            lossless_path = None
            if cleanup_evidence["status"] == "applied":
                lossless_path = "lossless/" + figure["name"] + ".png"
                (directory / "lossless").mkdir(exist_ok=True)
                (directory / lossless_path).write_bytes(master)
            original_path = "originals/" + figure["name"] + ".png"
            if lossless_path is None:
                lossless_path = original_path
            cleanup_evidence.update(
                format="JPEG",
                source_sha256=digest(original),
                sha256=digest(data),
                lossless_sha256=digest(master),
                color=color_evidence,
                jpeg=dict(
                    quality=quality, subsampling=0, optimize=True, progressive=True
                ),
            )
            result_path = "results/" + image
            (directory / original_path).write_bytes(original)
            (directory / result_path).write_bytes(data)
            # Remove the obsolete PNG export from this generated review folder.
            (directory / "results" / (figure["name"] + ".png")).unlink(missing_ok=True)
            cleanup_rows.append(
                dict(
                    name=figure["name"],
                    page=page,
                    original=original_path,
                    result=result_path,
                    cleanup=cleanup_evidence,
                    lossless=lossless_path,
                )
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
        captions = [b for b in blocks if b.get("figure_caption_for") == figure["name"]]
        if captions:
            blocks[:] = [b for b in blocks if b not in captions]
        position = figure_position(
            blocks, page, y0, book.get("source_figure_anchors", False)
        )
        blocks[position:position] = [block] + captions
        report.append(
            {
                **figure,
                "image": image,
                "sha256": digest(data),
                "chapter": chapter["chapter"],
                "crop_source": crop_evidence,
            }
        )
        if cleanup_evidence is not None:
            report[-1]["cleanup"] = cleanup_evidence
    if cleanup == "white":
        from figure_cleanup import write_review

        write_review(cleanup_dir, cleanup_rows)
    return report
