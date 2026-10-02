"""Offline crop benchmark; adaptive thresholds never change production defaults."""

import argparse
import html
import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pymupdf
import scipy
import PIL
from PIL import Image
from scipy.ndimage import uniform_filter

from common import ROOT, digest, file_digest, read_json, write_json
from ocr_cache import traineddata_digest
from scanned_notes import (
    components,
    prose_baseline,
    raised_regions,
    glyph_readings,
    line_crop,
)

METHODS = ("current", "fixed-150", "fixed-180", "otsu", "sauvola-61", "sauvola-91")


def otsu_threshold(array):
    hist = np.bincount(array.ravel(), minlength=256).astype(float)
    if np.count_nonzero(hist) < 2:
        return float(np.flatnonzero(hist)[0])
    weight = np.cumsum(hist)
    total = weight[-1]
    moment = np.cumsum(hist * np.arange(256))
    denominator = weight * (total - weight)
    variance = np.divide(
        (moment[-1] * weight - moment * total) ** 2,
        denominator,
        out=np.zeros(256),
        where=denominator > 0,
    )
    # Split at the half-bin boundary: a dark mode exactly at the chosen
    # integer threshold still belongs to foreground.
    return float(np.argmax(variance)) + 0.5


def threshold_image(image, method, cap_hint=None):
    array = np.asarray(image.convert("L"))
    parameters = {}
    if method == "current":
        model = prose_baseline(components(image), cap_hint)
        threshold = 180 if model and image.height > 2.4 * model[0] else 150
    elif method.startswith("fixed-"):
        threshold = int(method.split("-")[1])
    elif method == "otsu":
        threshold = otsu_threshold(array)
    elif method.startswith("sauvola-"):
        window = int(method.split("-")[1])
        values = array.astype(float)
        mean = uniform_filter(values, size=window, mode="reflect")
        variance = np.maximum(
            0,
            uniform_filter(values * values, size=window, mode="reflect") - mean * mean,
        )
        # Documented Sauvola formula, k=.2 and R=127.5 for 8-bit grayscale.
        # The two fixed odd windows span roughly one/two cap heights at 400 dpi;
        # they are comparison settings, not a tuned production policy.
        threshold = mean * (1 + 0.2 * (np.sqrt(variance) / 127.5 - 1))
        parameters = dict(window=window, k=0.2, r=127.5, border="reflect")
    else:
        raise ValueError(f"Unknown method {method}")
    if np.isscalar(threshold):
        parameters["threshold"] = float(threshold)
    return (
        Image.fromarray(np.where(array < threshold, 0, 255).astype(np.uint8)),
        parameters,
    )


def condition(image, name):
    if name == "original":
        return image
    if name != "faded-uneven":
        raise ValueError(name)
    array = np.asarray(image).astype(float)
    # Fixed affine contrast reduction plus a smooth left-to-right brightness
    # gradient is a stress test, not a claim about another physical scan.
    array = 125 + 0.45 * array + 20 * np.linspace(0, 1, image.width)[None, :]
    return Image.fromarray(np.clip(np.rint(array), 0, 255).astype(np.uint8))


def overlaps(box, target):
    area = max(0, min(box[2], target[2]) - max(box[0], target[0])) * max(
        0, min(box[3], target[3]) - max(box[1], target[1])
    )
    return area / max(1, (target[2] - target[0]) * (target[3] - target[1])) >= 0.6


def observe(image, method, target, expected, cap_hint):
    binary, parameters = threshold_image(image, method, cap_hint)
    # Production keeps grayscale for recognition unless its tall/faint crop
    # fallback fires. Preserve that distinction in the baseline comparison.
    recognition = (
        image if method == "current" and parameters["threshold"] == 150 else binary
    )
    regions = raised_regions(binary, cap_hint)
    observations = []
    for box in regions:
        readings = glyph_readings(recognition, box, 999)
        observations.append(
            dict(
                box=list(box),
                readings=readings,
                target=bool(target and overlaps(box, target)),
                numeric=any(
                    v.isascii()
                    and v.isdigit()
                    and not v.startswith("0")
                    and 1 <= int(v) <= 999
                    for v in readings
                ),
            )
        )
    return (
        dict(
            parameters=parameters,
            regions=observations,
            detected=any(r["target"] for r in observations),
            expected_read=any(
                r["target"] and str(expected) in r["readings"] for r in observations
            ),
            oracle_readings=glyph_readings(recognition, target, 999) if target else [],
            extra_numeric_regions=sum(
                r["numeric"] and not r["target"] for r in observations
            ),
        ),
        binary,
    )


def summarize(results):
    summary = []
    for variant in ("original", "faded-uneven"):
        for method in METHODS:
            positives = [
                r
                for r in results
                if r["condition"] == variant and r["method"] == method and r["expected"]
            ]
            negatives = [
                r
                for r in results
                if r["condition"] == variant
                and r["method"] == method
                and not r["expected"]
            ]
            summary.append(
                dict(
                    condition=variant,
                    method=method,
                    positives=len(positives),
                    detected=sum(r["detected"] for r in positives),
                    expected_read=sum(r["expected_read"] for r in positives),
                    oracle_expected_read=sum(
                        str(r["expected"]) in r["oracle_readings"] for r in positives
                    ),
                    controls=len(negatives),
                    controls_with_numeric=sum(
                        r["extra_numeric_regions"] > 0 for r in negatives
                    ),
                    extra_numeric_regions=sum(
                        r["extra_numeric_regions"] for r in positives + negatives
                    ),
                )
            )
    return summary


def run(manifest, output):
    spec = read_json(manifest)
    pdf = ROOT / spec["pdf"]
    if file_digest(pdf) != spec["source_sha256"]:
        raise ValueError("Benchmark source mismatch")
    provenance = dict(
        source_sha256=spec["source_sha256"],
        manifest_sha256=file_digest(manifest),
        script_sha256=file_digest(Path(__file__)),
        detector_sha256=file_digest(ROOT / "scripts/scanned_notes.py"),
        numpy=np.__version__,
        scipy=scipy.__version__,
        pillow=PIL.__version__,
        pymupdf=pymupdf.VersionBind,
        tesseract=subprocess.check_output(
            ["tesseract", "--version"], text=True
        ).splitlines()[0],
        eng_traineddata_sha256=traineddata_digest(),
        dpi=400,
        algorithm_reference="https://scikit-image.org/docs/stable/api/skimage.filters.html#skimage.filters.threshold_sauvola",
    )
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "cache"
    images = output / "images"
    images.mkdir(exist_ok=True)
    results = []
    cards = []
    with pymupdf.open(pdf) as doc:
        for case in spec["cases"]:
            page = Image.open(
                io.BytesIO(doc[case["page"] - 1].get_pixmap(dpi=400).tobytes("png"))
            ).convert("L")
            for positive in (True, False):
                row = case["row"] if positive else case["control_row"]
                crop, left, top = line_crop(page, row)
                # Fix the short-line height hint across methods, so the A/B
                # comparison changes thresholding rather than this input prior.
                cap = case.get("cap_hint" if positive else "control_cap_hint")
                target = (
                    tuple(
                        round(v * page.size[i % 2] - (left if i % 2 == 0 else top))
                        for i, v in enumerate(case["marker_bbox"])
                    )
                    if positive
                    else None
                )
                name = case["name"] + ("" if positive else "-control")
                for variant in ("original", "faded-uneven"):
                    sample = condition(crop, variant)
                    source_name = f"{name}-{variant}.png"
                    sample.save(images / source_name)
                    items = []
                    for method in METHODS:
                        key = digest(
                            json.dumps(
                                [provenance, name, variant, method, target, cap],
                                sort_keys=True,
                            ).encode()
                        )
                        path = cache / (key + ".json")
                        if path.exists():
                            observation = read_json(path)
                            binary, _ = threshold_image(sample, method, cap)
                        else:
                            observation, binary = observe(
                                sample,
                                method,
                                target,
                                case["number"] if positive else None,
                                cap,
                            )
                            write_json(path, observation)
                        binary_name = f"{name}-{variant}-{method}.png"
                        binary.save(images / binary_name)
                        results.append(
                            dict(
                                case=name,
                                page=case["page"],
                                condition=variant,
                                method=method,
                                expected=case["number"] if positive else None,
                                crop_sha256=digest(sample.tobytes()),
                                **observation,
                            )
                        )
                        items.append(
                            f'<td>{method}<br/>detected {observation["detected"]}; read {observation["expected_read"]}<br/><img src="images/{binary_name}"/></td>'
                        )
                    cards.append(
                        f'<h2>{html.escape(name)} · {variant}</h2><img src="images/{source_name}"/><table><tr>'
                        + "".join(items)
                        + "</tr></table>"
                    )
                    print(name, variant, flush=True)
    summary = summarize(results)
    write_json(
        output / "report.json",
        dict(
            provenance=provenance,
            summary=summary,
            results=results,
            scope=f"{len(spec['cases'])} annotated markers and {len(spec['cases'])} adjacent controls from the manifest source; synthetic fading is not independent evidence; no placement or chapter sequence applied",
        ),
    )
    (output / "review.html").write_text(
        '<!doctype html><html><meta charset="utf-8"/><title>Binarization A/B</title><style>body{font:16px system-ui}img{max-width:900px}td img{max-width:450px}td{vertical-align:top;border:1px solid #ccc}table{border-collapse:collapse}</style><h1>Binarization A/B</h1><p>Raw local glyph detection and readings; these are not accepted book references.</p>'
        + "".join(cards)
        + "</html>"
    )
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "tests/binarization-cases.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "work/binarization-experiment"
    )
    args = parser.parse_args()
    run(args.manifest, args.output)


if __name__ == "__main__":
    main()
