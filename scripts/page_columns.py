"""Reviewed column geometry shared by fresh OCR and PDF text witnesses."""

import pymupdf


def page_clips(page, book, number):
    key = str(number)
    if key not in book.get("index_splits", {}):
        return [page.rect]
    split = book["index_splits"][key] * page.rect.width
    top = book.get("index_column_starts", {}).get(key, 0) * page.rect.height
    columns = [
        pymupdf.Rect(0, top, split, page.rect.height),
        pymupdf.Rect(split, top, page.rect.width, page.rect.height),
    ]
    return [pymupdf.Rect(0, 0, page.rect.width, top), *columns] if top else columns


def text_column(box, split=None, start=0):
    """Column -1 precedes both columns without merging their rows."""
    if split is None:
        return 0
    if start and box[1] < start:
        return -1
    return int(box[0] > split)


def page_box(box, clip, page):
    """Map crop-normalized OCR coordinates to the complete source page."""
    return [
        (clip.x0 + box[0] * clip.width) / page.rect.width,
        (clip.y0 + box[1] * clip.height) / page.rect.height,
        (clip.x0 + box[2] * clip.width) / page.rect.width,
        (clip.y0 + box[3] * clip.height) / page.rect.height,
    ]
