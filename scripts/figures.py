"""Profile-reviewed source crops placed among canonical text blocks."""

import pymupdf
import io
import re
import copy
import statistics
from PIL import Image
from common import digest
from figure_cleanup import EBOOK_PHOTO_PPI, EBOOK_TARGET_PPI


def crop_artwork(
    page,
    rect,
    dpi=EBOOK_TARGET_PPI,
    excluded_overlays=(),
    raster_only=False,
    effective_ppi=EBOOK_TARGET_PPI,
):
    """JPEG export with a bounded embedded size for ebook readers."""
    result, evidence = crop_artwork_pixels(
        page, rect, dpi, excluded_overlays, raster_only
    )
    from figure_cleanup import ebook_size

    result, evidence["ebook_resize"] = ebook_size(result, effective_ppi)
    out = io.BytesIO()
    options = dict(
        format="JPEG", quality=90 if evidence["ebook_resize"]["resized"] else 95
    )
    if evidence["method"] == "rendered-page":
        options["dpi"] = result.info["dpi"]
    result.save(out, **options)
    return out.getvalue(), evidence


def crop_artwork_pixels(
    page, rect, dpi=EBOOK_TARGET_PPI, excluded_overlays=(), raster_only=False
):
    """Use isolated scan pixels only when no visible labels would be lost."""
    if raster_only:
        # Explicit source-reviewed mode: discard reconstructed PDF text overlays.
        from scan_pixels import isolated_scan

        result, evidence = isolated_scan(page, rect)
        return result, evidence
    from scan_pixels import image_placements

    images = image_placements(page)
    # When a reviewed figure crop exactly matches one upright embedded image,
    # export that source object directly. Rendering the page rectangle can pull
    # separate OCR/text overlays into the artwork and duplicate those words in
    # the reflowed text. Text that is part of the image pixels remains intact.
    exact = [
        im
        for im in images
        if im.get("xref")
        and abs(im["transform"][1]) < 1e-6
        and abs(im["transform"][2]) < 1e-6
        and im["transform"][0] > 0
        and im["transform"][3] > 0
        and all(abs(a - b) <= 0.5 for a, b in zip(im["bbox"], rect))
    ]
    if len(exact) == 1 and not exact[0].get("has-mask"):
        raw = page.parent.extract_image(exact[0]["xref"])
        result = Image.open(io.BytesIO(raw["image"])).convert("RGB")
        return result, dict(
            method="embedded-figure-image",
            xref=exact[0]["xref"],
            pixel_size=list(result.size),
        )
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
    # Hashing fully decodes the page images into MuPDF's store; the render then
    # reuses those full-resolution decodes rather than subsampled ones. Reviewed
    # figure pixels depend on this, so rendered crops keep the hashed lookup.
    page.get_image_info(xrefs=True)
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


def figure_position(blocks, page, y, split=False, join_words=()):
    join_words = {word.lower() for word in join_words}

    def wrapped_word(previous, following):
        left = re.search(r"([\w]+)-\.?$", previous.get("text", "").rstrip())
        right = re.match(r"^([^\W\d_]+)", following.get("text", "").lstrip())
        known_compound = bool(
            left and right and f"{left[1]}-{right[1]}".lower() in join_words
        )
        return bool(
            left
            and right
            and previous["page"] == following["page"] == page
            and (right[1][:1].islower() or known_compound)
        )

    for index, block in enumerate(blocks):
        sources = block.get("sources", [])
        after = [
            i
            for i, s in enumerate(sources)
            if s["page"] > page or (s["page"] == page and s["bbox"][1] >= y)
        ]
        if not after:
            continue
        split_at = after[0]
        if split and 0 < split_at < len(sources):
            previous, following = sources[split_at - 1], sources[split_at]
            if wrapped_word(previous, following):
                if split_at > 1 and wrapped_word(
                    sources[split_at - 2], previous
                ):
                    # The preceding row is itself the suffix of another
                    # wrapped word, so moving the figure before it would cut
                    # that word. Keep this text block intact and anchor after.
                    return index + 1
                # A source-positioned image must not land between the two
                # printed rows of a wrapped word. Move it before that word.
                split_at -= 1
        if split_at == 0:
            if split and index > 0 and block["kind"] in ("text", "quote"):
                previous_block = blocks[index - 1]
                previous_sources = previous_block.get("sources", [])
                following_sources = block.get("sources", [])
                if (
                    previous_block.get("kind") in ("text", "quote")
                    and previous_sources
                    and following_sources
                    and wrapped_word(previous_sources[-1], following_sources[0])
                ):
                    parts = split_source_block(
                        previous_block, len(previous_sources) - 1
                    )
                    if parts:
                        blocks[index - 1 : index] = parts
                        return index
                    # The preceding block cannot be split because it contains
                    # another joined word at an earlier source seam. Keep the
                    # current wrapped word intact and place the image after it.
                    return index + 1
            return index
        if split and block["kind"] in ("text", "quote"):
            parts = split_source_block(block, split_at)
            if parts:
                blocks[index : index + 1] = parts
                return index + 1
    return len(blocks)


def figure_flow(blocks, page, rect):
    """Infer a side float only when source rows occupy the opposite side."""
    x0, y0, x1, y1 = rect
    if x1 - x0 > 0.62:
        return None
    left_text = right_text = 0
    tolerance = 0.06  # OCR line boxes include some whitespace around glyphs.
    for block in blocks:
        if block.get("kind") not in ("text", "quote"):
            continue
        for source in block.get("sources", []):
            if source.get("page") != page:
                continue
            _, top, _, bottom = source["bbox"]
            if bottom <= y0 or top >= y1:
                continue
            sx0, _, sx1, _ = source["bbox"]
            right_text += sx0 >= x1 - tolerance
            left_text += sx1 <= x0 + tolerance
    if max(left_text, right_text) < 3:
        return None
    return "float-left" if right_text > left_text else "float-right"


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
        image_type = figure.get("image_type", "photograph")
        effective_ppi = figure.get("effective_ppi", EBOOK_TARGET_PPI)
        if image_type not in ("photograph", "line-art"):
            raise ValueError("Unknown figure image_type")
        if effective_ppi not in (EBOOK_PHOTO_PPI, EBOOK_TARGET_PPI) or (
            effective_ppi == EBOOK_PHOTO_PPI and image_type != "photograph"
        ):
            raise ValueError("150 PPI is available only for reviewed photographs")
        image = figure["name"] + (".png" if image_type == "line-art" else ".jpg")
        color_export = "figure_color_mode" in book
        cropper = (
            crop_artwork_pixels
            if cleanup == "white"
            or color_export
            or "color_mode" in figure
            or image_type == "line-art"
            else crop_artwork
        )
        crop, crop_evidence = cropper(
            source,
            rect,
            excluded_overlays=book.get("artwork_overlay_exclusions", []),
            raster_only=book.get("scan_raster_only", False),
            **({"effective_ppi": effective_ppi} if cropper is crop_artwork else {}),
        )
        cleanup_evidence = None
        data = crop
        if cleanup != "white" and (
            color_export
            or "color_mode" in figure
            or image_type == "line-art"
            or effective_ppi == EBOOK_PHOTO_PPI
        ):
            from figure_cleanup import export_color, jpeg_export, ebook_size, png_bytes

            # An explicit source-reviewed color policy also works without
            # paper normalization; encode once from uncompressed crop pixels.
            export, color_evidence = export_color(
                crop, figure.get("color_mode", book.get("figure_color_mode", "auto"))
            )
            export, crop_evidence["ebook_resize"] = ebook_size(export, effective_ppi)
            data = (
                png_bytes(export)
                if image_type == "line-art"
                else jpeg_export(
                    export,
                    quality=90 if crop_evidence["ebook_resize"]["resized"] else 95,
                )
            )
            crop_evidence["color"] = color_evidence
        if cleanup == "white":
            from pathlib import Path
            from figure_cleanup import (
                normalize_paper,
                png_bytes,
                jpeg_export,
                export_color,
                ebook_size,
            )

            # Normalize the cropped source pixels before the sole JPEG encode.
            normalized, cleanup_evidence = normalize_paper(crop)
            original = png_bytes(crop)
            master = png_bytes(normalized)
            # Extra JPEG precision costs little on sparse diagrams and retains
            # faint strokes better; dense photographs use compact quality 95.
            quality = 98 if cleanup_evidence.get("kind") == "line-art" else 95
            export, color_evidence = export_color(
                normalized,
                figure.get("color_mode", book.get("figure_color_mode", "auto")),
            )
            export, crop_evidence["ebook_resize"] = ebook_size(export, effective_ppi)
            if crop_evidence["ebook_resize"]["resized"]:
                quality = min(quality, 90)
            cleanup_evidence["ebook_resize"] = crop_evidence["ebook_resize"]
            data = (
                png_bytes(export)
                if image_type == "line-art"
                else jpeg_export(export, quality=quality)
            )
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
                format="PNG" if image_type == "line-art" else "JPEG",
                source_sha256=digest(original),
                sha256=digest(data),
                lossless_sha256=digest(master),
                color=color_evidence,
                jpeg=(
                    None
                    if image_type == "line-art"
                    else dict(
                        quality=quality, subsampling=0, optimize=True, progressive=True
                    )
                ),
            )
            result_path = "results/" + image
            (directory / original_path).write_bytes(original)
            (directory / result_path).write_bytes(data)
            # Remove the obsolete PNG export from this generated review folder.
            (
                directory
                / "results"
                / (figure["name"] + (".jpg" if image_type == "line-art" else ".png"))
            ).unlink(missing_ok=True)
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
            "flow": figure.get("flow")
            or (
                figure_flow(chapter["blocks"], page, figure["rect"])
                if book.get("source_figure_anchors")
                else None
            ),
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
            blocks,
            page,
            y0,
            book.get("source_figure_anchors", False),
            book.get("line_join_words", []),
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
