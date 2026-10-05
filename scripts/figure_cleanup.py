"""Conservative, deterministic paper normalization; never synthesize artwork."""

import html
import io
from pathlib import Path

import numpy as np
from PIL import Image

from common import digest, write_json
from build_resources import measured

POLICY = "paper-field-v1"


def design(x, y):
    """A quadratic models broad scan lighting, without following thin strokes."""
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=-1)


def field_strip(coefficients, basis, width, height, start, end):
    yy, xx = np.mgrid[start:end, 0:width]
    coordinates = design((xx + 0.5) * 2 / width - 1, (yy + 0.5) * 2 / height - 1)
    if basis == "plane-2d":
        coordinates = coordinates[:, :, :3]
    elif basis in ("quadratic-x", "quadratic-y"):
        t = coordinates[:, :, 2 if basis.endswith("y") else 1]
        coordinates = np.stack([np.ones_like(t), t, t * t], axis=-1)
    return np.einsum("ijk,kl->ijl", coordinates, coefficients)


def export_color(image, policy="auto"):
    """Use reviewed monochrome facts or strictly neutral pixels, never guess tint."""
    if policy not in ("auto", "rgb", "grayscale"):
        raise ValueError("Invalid figure color policy")
    rgb = image.convert("RGB")
    pixels = np.asarray(rgb).astype(np.int16)
    spread = pixels.max(axis=2) - pixels.min(axis=2)
    # Two channel levels allow rounding noise only; even tiny colored marks
    # prevent automatic grayscale. Tinted scans need a reviewed profile fact.
    neutral = image.mode == "L" or int(spread.max()) <= 2
    grayscale = policy == "grayscale" or (policy == "auto" and neutral)
    return (rgb.convert("L") if grayscale else rgb), dict(
        policy=policy,
        mode="L" if grayscale else "RGB",
        reason=(
            "reviewed-monochrome"
            if policy == "grayscale"
            else "neutral-pixels" if grayscale else "preserve-color"
        ),
        maximum_channel_spread=int(spread.max()),
    )


def jpeg_export(image, quality=95):
    """Compact EPUB asset; retain the lossless master separately for auditing."""
    output = io.BytesIO()
    # High quality and full chroma resolution protect thin colored/gray
    # lettering; optimized tables and progressive scans reduce size without
    # changing decoded pixels (verified against baseline encoding in tests).
    image.save(
        output,
        format="JPEG",
        quality=quality,
        subsampling=0,
        optimize=True,
        progressive=True,
    )
    return output.getvalue()


@measured
def normalize_paper(image):
    """Operate on cropped pixel images, with no compression between stages."""
    evidence = dict(
        policy=POLICY, source_pixel_sha256=digest(image.tobytes()), status="skipped"
    )

    def skip(reason):
        return image.copy(), dict(evidence, reason=reason)

    if image.mode not in ("RGB", "L"):
        return skip("unsupported-color-or-alpha-mode")
    source = np.asarray(image.convert("RGB")).copy()
    source.setflags(write=False)
    rgb = source.astype(np.float64)
    h, w = rgb.shape[:2]
    if min(h, w) < 64:
        return skip("insufficient-pixels")
    lum = rgb.mean(axis=2)
    # A narrow edge band usually includes some exposed paper even around photos.
    edge = max(2, round(min(h, w) * 0.05))
    border = np.concatenate(
        [
            rgb[:edge].reshape(-1, 3),
            rgb[-edge:].reshape(-1, 3),
            rgb[:, :edge].reshape(-1, 3),
            rgb[:, -edge:].reshape(-1, 3),
        ]
    )
    light = border.mean(axis=1)
    seed = np.median(border[light >= np.percentile(light, 80)], axis=0)
    evidence["paper_rgb"] = np.round(seed, 3).tolist()
    # Reject dark or strongly tinted backgrounds rather than guessing paper.
    if seed.min() < 180 or seed.max() - seed.min() > 40:
        return skip("no-light-neutral-paper")
    # Strongly colored artwork is outside this monochrome cleanup policy.
    chroma = rgb - lum[:, :, None]
    seed_chroma = seed - seed.mean()
    if np.mean(np.max(np.abs(chroma - seed_chroma), axis=2) > 25) > 0.02:
        return skip("colored-artwork")

    # At most 20x20 cells bound fitting work and keep the field much coarser
    # than letters and diagram strokes. Dark area density distinguishes photos
    # from sparse line art; ambiguous densities receive the safer photo treatment.
    xs = np.linspace(0, w, min(20, w // 16) + 1, dtype=int)
    ys = np.linspace(0, h, min(20, h // 16) + 1, dtype=int)
    cells = []
    dense = []
    for y0, y1 in zip(ys[:-1], ys[1:]):
        for x0, x1 in zip(xs[:-1], xs[1:]):
            values = rgb[y0:y1, x0:x1].reshape(-1, 3)
            levels = values.mean(axis=1)
            # Dense low-contrast gray areas may be photographs too. Treat them
            # conservatively even when they contain few clearly dark pixels.
            dense.append(
                np.mean(levels < seed.mean() - 20) >= 0.2
                or np.mean(levels < seed.mean() - 8) >= 0.6
            )
            cells.append((x0, y0, x1, y1, values, levels))
    density = float(np.mean(dense))
    kind = "line-art" if density < 0.15 else "photograph-or-dense-artwork"
    evidence.update(kind=kind, dense_cell_fraction=round(density, 6))
    protected = None
    if kind != "line-art":
        # A rectangular envelope protects every photo highlight and halftone,
        # including light patches connected to the paper at its edges.
        dark = lum < seed.mean() - 20
        columns = np.flatnonzero(dark.mean(axis=0) >= 0.05)
        rows = np.flatnonzero(dark.mean(axis=1) >= 0.05)
        if not len(columns) or not len(rows):
            return skip("uncertain-photo-bounds")
        protected = (
            max(0, columns[0] - 2),
            max(0, rows[0] - 2),
            min(w, columns[-1] + 3),
            min(h, rows[-1] + 3),
        )
        evidence["protected_box"] = [int(v) for v in protected]

    sample_cells = cells
    if protected:
        a, b, c, d = protected
        sample_cells = []
        # Margins can be narrower than a fitting cell. Sample the actual exposed
        # strips, rather than rejecting every cell that overlaps the photo.
        for ax, ay, bx, by in [(0, 0, a, h), (c, 0, w, h), (a, 0, c, b), (a, d, c, h)]:
            if min(bx - ax, by - ay) < 4:
                continue
            sx = np.linspace(ax, bx, max(1, round((bx - ax) / (w / 20))) + 1, dtype=int)
            sy = np.linspace(ay, by, max(1, round((by - ay) / (h / 20))) + 1, dtype=int)
            for y0, y1 in zip(sy[:-1], sy[1:]):
                for x0, x1 in zip(sx[:-1], sx[1:]):
                    values = rgb[y0:y1, x0:x1].reshape(-1, 3)
                    sample_cells.append((x0, y0, x1, y1, values, values.mean(axis=1)))
    samples = []
    for x0, y0, x1, y1, values, levels in sample_cells:
        low, mid, high = np.percentile(levels, [10, 50, 90])
        # Select broad, light, quiet paper, rather than fitting photo highlights
        # or the ink inside a densely labeled diagram cell.
        if abs(mid - seed.mean()) > 18 or high - low > 18:
            continue
        paper = np.percentile(values, 90, axis=0)
        samples.append(((x0 + x1) / w - 1, (y0 + y1) / h - 1, paper))
    evidence["paper_cells"] = len(samples)
    # Twelve cells overdetermine the six quadratic coefficients.
    if len(samples) < 12:
        return skip("insufficient-exposed-paper")
    points = np.array([(x, y) for x, y, _ in samples])
    values = np.array([v for _, _, v in samples])
    matrix = design(points[:, 0], points[:, 1])
    basis = "quadratic-2d"
    if protected and np.linalg.matrix_rank(matrix) < 6:
        matrix = matrix[:, :3]
        basis = "plane-2d"
        if np.linalg.matrix_rank(matrix) < 3:
            axis = int(np.argmax(np.ptp(points, axis=0)))
            t = points[:, axis]
            matrix = np.stack([np.ones_like(t), t, t * t], axis=-1)
            basis = "quadratic-y" if axis else "quadratic-x"
    columns = matrix.shape[1]
    evidence["field_basis"] = basis
    # Trim outlier cells (bleed-through, isolated highlights) without fitting
    # progressively smaller features. Rank checks prevent extrapolated planes.
    keep = np.ones(len(samples), dtype=bool)
    for _ in range(3):
        if keep.sum() < 12 or np.linalg.matrix_rank(matrix[keep]) < columns:
            return skip("ill-conditioned-paper-field")
        coefficients = np.linalg.lstsq(matrix[keep], values[keep], rcond=None)[0]
        residual = np.max(
            np.abs(np.einsum("ij,jk->ik", matrix, coefficients) - values), axis=1
        )
        keep = residual <= 6  # Six gray levels tolerate scan noise, not dense ink.
    if keep.sum() < 12 or np.linalg.matrix_rank(matrix[keep]) < columns:
        return skip("inconsistent-paper-field")
    coefficients = np.linalg.lstsq(matrix[keep], values[keep], rcond=None)[0]
    cleaned = np.empty_like(source)
    field_min = np.full(3, np.inf)
    field_max = np.full(3, -np.inf)
    # Bound polynomial/normalization temporaries to 256 rows instead of keeping
    # six full-resolution coordinate planes for a large scan.
    for start in range(0, h, 256):
        end = min(h, start + 256)
        field = field_strip(coefficients, basis, w, h, start, end)
        if not np.isfinite(field).all() or field.min() < 175 or field.max() > 260:
            return skip("unsafe-paper-field-range")
        np.minimum(field_min, field.min(axis=(0, 1)), out=field_min)
        np.maximum(field_max, field.max(axis=(0, 1)), out=field_max)
        # Continuous division preserves gray strokes; no ink threshold or hard
        # binarization. Explicit output buffers keep the source immutable.
        normalized = np.empty_like(field)
        np.multiply(rgb[start:end], 255, out=normalized)
        np.divide(normalized, field, out=normalized)
        np.clip(normalized, 0, 255, out=normalized)
        np.rint(normalized, out=normalized)
        cleaned[start:end] = normalized.astype(np.uint8)
    if (field_max - field_min).max() > 35:
        return skip("unsafe-paper-field-range")
    if field_min.min() >= 254:
        return skip("already-white")
    if protected:
        a, b, c, d = protected
        cleaned[b:d, a:c] = source[b:d, a:c]
        if not np.array_equal(cleaned[b:d, a:c], source[b:d, a:c]):
            raise ValueError("Photographic interior changed during paper cleanup")
        evidence["protected_pixels_unchanged"] = True
    changed = np.any(cleaned != source, axis=2)
    if not changed.any():
        return skip("no-change")
    evidence.update(
        status="applied",
        changed_fraction=round(float(changed.mean()), 6),
        field_min_rgb=np.round(field_min, 3).tolist(),
        field_max_rgb=np.round(field_max, 3).tolist(),
        fitted_cells=int(keep.sum()),
    )
    return Image.fromarray(cleaned), evidence


def png_bytes(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def clean_figure(data):
    """Byte-oriented audit adapter; pipeline normalization uses pixels directly."""
    with Image.open(io.BytesIO(data)) as image:
        result, evidence = normalize_paper(image)
    output = png_bytes(result) if evidence["status"] == "applied" else data
    evidence.update(source_sha256=digest(data), sha256=digest(output))
    if evidence["status"] == "applied":
        evidence["format"] = "PNG"
    return output, evidence


def write_review(directory, rows):
    """Original bytes and preview assets live outside the distributed EPUB."""
    directory = Path(directory)
    write_json(directory / "report.json", dict(policy=POLICY, figures=rows))
    cards = []
    for row in rows:
        name = html.escape(row["name"])
        before = html.escape(row["original"], quote=True)
        after = html.escape(row["result"], quote=True)
        summary = html.escape(str(row["cleanup"]))
        master = (
            f'<p><a href="{html.escape(row["lossless"], quote=True)}">Lossless normalized master</a></p>'
            if row.get("lossless")
            else ""
        )
        cards.append(
            f'<section><h2>{name} · PDF {row["page"]}</h2>'
            f'<div class="pair"><figure><figcaption>Original</figcaption>'
            f'<a href="{before}"><img src="{before}" alt="Original {name}"></a></figure>'
            f'<figure><figcaption>EPUB JPEG</figcaption><a href="{after}">'
            f'<img src="{after}" alt="Cleaned {name}"></a></figure></div>'
            f"{master}<details><summary>Cleanup evidence</summary><pre>{summary}</pre></details></section>"
        )
    (directory / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        "<title>Figure paper cleanup review</title><style>"
        "body{font-family:system-ui;margin:2em}section{margin-bottom:3em}"
        ".pair{display:flex;gap:1em}.pair figure{margin:0;width:50%}"
        "img{max-width:100%;height:auto}pre{white-space:pre-wrap}"
        "</style><h1>Figure paper cleanup review</h1>"
        "<p>Click either image for full resolution. Original crops are retained byte for byte. "
        "Dense artwork interiors are protected in lossless masters; EPUB JPEGs add small encoding differences. "
        "Uncertain crops remain unchanged.</p>" + "".join(cards) + "</html>",
        encoding="utf-8",
    )
