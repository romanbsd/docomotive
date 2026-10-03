"""Read original page scans after checking their actual placement."""

import io
import pymupdf
from PIL import Image


def isolated_scan(page, rect):
    scans = [
        image
        for image in page.get_image_info(xrefs=True)
        if image.get("xref")
        # PDF placement rounding can miss a page edge by a fraction of a pixel.
        and (pymupdf.Rect(image["bbox"]) + (-0.1, -0.1, 0.1, 0.1)).contains(rect)
        # A dominant page scan, rather than a photograph embedded in prose.
        and pymupdf.Rect(image["bbox"]).get_area() >= 0.9 * page.rect.get_area()
        # Cropping assumes upright, unskewed image placement.
        and abs(image["transform"][1]) < 1e-6
        and abs(image["transform"][2]) < 1e-6
        and image["transform"][0] > 0
        and image["transform"][3] > 0
    ]
    if len(scans) != 1:
        raise ValueError("Original scan pixels require exactly one upright page scan")
    scan = scans[0]
    bounds = pymupdf.Rect(scan["bbox"])
    raw = page.parent.extract_image(scan["xref"])
    image = Image.open(io.BytesIO(raw["image"])).convert("RGB")
    box = (
        round((rect.x0 - bounds.x0) / bounds.width * image.width),
        round((rect.y0 - bounds.y0) / bounds.height * image.height),
        round((rect.x1 - bounds.x0) / bounds.width * image.width),
        round((rect.y1 - bounds.y0) / bounds.height * image.height),
    )
    result = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), "white")
    result.paste(image, (-box[0], -box[1]))
    return result, dict(
        method="embedded-page-scan", xref=scan["xref"], pixel_size=list(result.size)
    )
