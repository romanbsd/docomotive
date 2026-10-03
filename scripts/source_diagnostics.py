"""Deterministic diagnostics for reconstructed text painted over page scans."""

import argparse
from pathlib import Path

import numpy as np
import pymupdf

from common import file_digest, write_json
from scan_pixels import isolated_scan


def inspect_page(page):
    try:
        _, scan = isolated_scan(page, page.rect)
    except ValueError:
        return {"page": page.number + 1, "status": "not-single-page-scan"}
    traces = [
        s for s in page.get_texttrace() if s["type"] != 3 and s.get("opacity", 1) > 0
    ]
    result = {"page": page.number + 1, "painted_text_spans": len(traces)}
    glyphs = sum(len(span["chars"]) for span in traces)
    result["painted_text_glyphs"] = glyphs
    # Accept both word-based and line-based reconstruction. Twelve spans and
    # 300 glyphs establish substantial text, rather than a few short labels.
    if len(traces) < 12 or glyphs < 300:
        return dict(result, status="no-dense-painted-text")
    image = next(
        im for im in page.get_image_info(xrefs=True) if im["xref"] == scan["xref"]
    )
    with pymupdf.open() as raster:
        clean = raster.new_page(width=page.rect.width, height=page.rect.height)
        clean.insert_image(
            image["bbox"], stream=page.parent.extract_image(scan["xref"])["image"]
        )
        # Render both placements identically: resizing the raw bitmap would
        # confound overlay ink with resampling and subpixel placement differences.
        original = clean.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
        composed = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
        a = np.frombuffer(original.samples, dtype=np.uint8)
        b = np.frombuffer(composed.samples, dtype=np.uint8)
        # Wide luminance separation avoids counting antialiasing at ink edges.
        ink = int(np.count_nonzero(a < 180))
        added = int(np.count_nonzero((a > 230) & (b < 180)))
    ratio = added / max(ink, 1)
    # Require substantial absolute and relative added ink; a tiny registration
    # discrepancy on an otherwise empty page should not trigger the warning.
    candidate = added >= 500 and ratio >= 0.01
    return dict(
        result,
        status=(
            "possible-reconstructed-overlay"
            if candidate
            else "no-substantial-added-ink"
        ),
        source_ink_pixels=ink,
        added_ink_pixels=added,
        added_ink_ratio=round(ratio, 6),
    )


def diagnose(doc, source_hash):
    # Include the opening leaves and nine evenly spaced samples from the book.
    pages = sorted(
        {0, min(1, len(doc) - 1), min(2, len(doc) - 1)}
        | {round(i * (len(doc) - 1) / 8) for i in range(9)}
    )
    results = [inspect_page(doc[n]) for n in pages]
    candidates = [
        r["page"] for r in results if r["status"] == "possible-reconstructed-overlay"
    ]
    return {
        "source_sha256": source_hash,
        "sampled_pages": [n + 1 for n in pages],
        "candidate_pages": candidates,
        "status": (
            "review-scan-pixel-mode" if candidates else "no-overlay-evidence-in-sample"
        ),
        "recommendation": (
            "Compare original scan pixels with the rendered PDF; use scan_raster_only only after confirming overlays are reconstructed OCR."
            if candidates
            else "Retain the current extraction path."
        ),
        "scope": "Deterministic sample diagnostic, not proof that every page is free of overlays or genuine annotations.",
        "pages": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with pymupdf.open(args.pdf) as doc:
        result = diagnose(doc, file_digest(args.pdf))
    write_json(args.output, result)
    print("Source diagnostic: " + result["status"])
    if result["candidate_pages"]:
        print(
            "Possible reconstructed overlays on PDF pages: "
            + ", ".join(map(str, result["candidate_pages"]))
        )
        print(result["recommendation"])


if __name__ == "__main__":
    main()
