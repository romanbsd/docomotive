"""Independent decoded-pixel and package checks for opt-in figure cleanup."""

import argparse
import copy
import io
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image
from lxml import etree
from scipy.ndimage import gaussian_filter

from common import digest, load_profile, read_json, write_json


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pixels(data):
    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGB"))


def verify_cleanup(output, book, editorial=False, baseline=None):
    target = Path(output) / "figure-cleanup/verification.json"
    # Never leave an earlier passing report visible after a failed rerun.
    write_json(target, dict(status="checking", baseline_checked=False))
    try:
        return _verify_cleanup(output, book, editorial, baseline)
    except Exception as error:
        write_json(
            target, dict(status="failed", error=str(error), baseline_checked=False)
        )
        raise


def _verify_cleanup(output, book, editorial=False, baseline=None):
    output = Path(output)
    root = output / "figure-cleanup"
    rows = read_json(root / "report.json")["figures"]
    require(len(rows) == len(book.get("figures", [])), "Incomplete cleanup review")
    epub = book["slug"] + ("-corrected" if editorial else "") + ".epub"
    checks = []
    with zipfile.ZipFile(output / epub) as archive:
        for row in rows:
            evidence = row["cleanup"]
            original = (root / row["original"]).read_bytes()
            result = (root / row["result"]).read_bytes()
            master = (
                (root / row["lossless"]).read_bytes() if row.get("lossless") else result
            )
            name = row["name"]
            require(
                digest(original) == evidence["source_sha256"], name + ": original hash"
            )
            if evidence.get("source_pixel_sha256"):
                with Image.open(io.BytesIO(original)) as image:
                    require(
                        digest(image.tobytes()) == evidence["source_pixel_sha256"],
                        name + ": original crop pixels changed",
                    )
            require(digest(result) == evidence["sha256"], name + ": result hash")
            if row.get("lossless"):
                require(
                    digest(master) == evidence["lossless_sha256"],
                    name + ": master hash",
                )
            asset = "OEBPS/" + Path(row["result"]).name
            require(archive.read(asset) == result, name + ": EPUB asset mismatch")
            before, after = pixels(original), pixels(master)
            exported = pixels(result)
            require(before.shape == after.shape, name + ": dimensions changed")
            require(exported.shape == after.shape, name + ": export dimensions changed")
            check = dict(
                name=name, status=evidence["status"], kind=evidence.get("kind")
            )
            if evidence["status"] == "skipped":
                require(
                    np.array_equal(before, after),
                    name + ": skipped crop pixels changed",
                )
            elif "protected_box" in evidence:
                x0, y0, x1, y1 = evidence["protected_box"]
                require(
                    np.array_equal(before[y0:y1, x0:x1], after[y0:y1, x0:x1]),
                    name + ": photographic interior changed",
                )
                check["protected_pixels_unchanged"] = True
            else:
                source_lum, result_lum = before.mean(axis=2), exported.mean(axis=2)
                # A smoothed local reference avoids counting bright JPEG/paper
                # noise spikes as faint ink. This is a diagnostic, not proof that
                # every meaningful stroke survives; full-resolution review matters.
                source_contrast = np.empty_like(source_lum)
                result_contrast = np.empty_like(result_lum)
                np.subtract(
                    gaussian_filter(source_lum, sigma=4),
                    source_lum,
                    out=source_contrast,
                )
                np.subtract(
                    gaussian_filter(result_lum, sigma=4),
                    result_lum,
                    out=result_contrast,
                )
                ink = source_contrast >= 5
                retained = (
                    float(np.mean(result_contrast[ink] >= source_contrast[ink] * 0.9))
                    if ink.any()
                    else 1.0
                )
                require(retained >= 0.99, name + ": excessive local ink contrast loss")
                check.update(
                    ink_pixels=int(ink.sum()),
                    contrast_retained_fraction=retained,
                    median_brightness_before=float(np.median(source_lum)),
                    median_brightness_after=float(np.median(result_lum)),
                )
            # JPEG error is separate from normalization; protected photo pixels
            # remain exact in the master, not necessarily in the final encoding.
            target = after
            if evidence.get("color", {}).get("mode") == "L":
                with Image.open(io.BytesIO(result)) as image:
                    require(image.mode == "L", name + ": grayscale codec mismatch")
                gray = np.asarray(Image.fromarray(after).convert("L"))
                target = np.repeat(gray[:, :, None], 3, axis=2)
                check["grayscale_conversion_rgb_rmse"] = float(
                    np.sqrt(
                        np.mean(
                            np.square(
                                target.astype(np.float64) - after.astype(np.float64)
                            )
                        )
                    )
                )
            error = exported.astype(np.float64) - target.astype(np.float64)
            rmse = float(np.sqrt(np.mean(np.square(error))))
            require(rmse <= 3, name + ": excessive JPEG encoding error")
            check.update(
                jpeg_rmse=rmse,
                jpeg_p99_absolute_error=float(np.percentile(np.abs(error), 99)),
            )
            checks.append(check)

    verification = dict(status="passed", figures=checks, baseline_checked=False)
    if baseline is not None:
        baseline = Path(baseline)
        old_figures = {
            f["name"]: f for f in read_json(baseline / "report.json")["figures"]
        }
        mapping = {
            Path(row["result"]).name: old_figures[row["name"]]["image"] for row in rows
        }
        model = copy.deepcopy(read_json(output / "book-model.json"))
        for chapter in model:
            for block in chapter["blocks"]:
                if block["kind"] == "figure":
                    block["image"] = mapping[block["image"]]
        require(
            model == read_json(baseline / "book-model.json"),
            "Non-image canonical model change",
        )
        for name in [
            "corrected-pages.json",
            "apparatus-analysis.json",
            "text-coverage.json",
            book["slug"] + ".txt",
        ]:
            require(
                (output / name).read_bytes() == (baseline / name).read_bytes(),
                name + ": content changed",
            )
        changed = []
        with (
            zipfile.ZipFile(output / epub) as current,
            zipfile.ZipFile(baseline / epub) as old,
        ):
            removed = {"OEBPS/" + old_figures[row["name"]]["image"] for row in rows}
            added = {"OEBPS/" + Path(row["result"]).name for row in rows}
            require(
                set(current.namelist()) == (set(old.namelist()) - removed) | added,
                "Unexpected package member change",
            )
            for row in rows:
                source_asset = "OEBPS/" + old_figures[row["name"]]["image"]
                legacy = pixels(old.read(source_asset)).astype(np.float64)
                source = pixels((root / row["original"]).read_bytes()).astype(
                    np.float64
                )
                require(
                    legacy.shape == source.shape,
                    row["name"] + ": source crop dimensions changed",
                )
                # Previous exports quantized the crop before cleanup. New PNG
                # originals retain source pixels; allow only legacy JPEG error.
                require(
                    float(np.sqrt(np.mean(np.square(legacy - source)))) <= 3,
                    row["name"] + ": source crop differs beyond legacy JPEG error",
                )
            for name in set(old.namelist()) - removed:
                actual, expected = current.read(name), old.read(name)
                if actual == expected:
                    continue
                changed.append(name)
                for new, prior in mapping.items():
                    actual = actual.replace(new.encode(), prior.encode())
                if name.endswith(".opf"):
                    node = etree.fromstring(actual)
                    for item in node.iter():
                        if item.get("href") in mapping.values():
                            item.set("media-type", "image/jpeg")
                    require(
                        etree.tostring(node, method="c14n")
                        == etree.tostring(etree.fromstring(expected), method="c14n"),
                        name + ": metadata changed",
                    )
                else:
                    require(actual == expected, name + ": non-image package change")
        verification.update(
            baseline_checked=True,
            canonical_model_unchanged_except_image_filenames=True,
            text_and_notes_unchanged=True,
            changed_package_text_members=sorted(changed),
        )
    write_json(root / "verification.json", verification)
    return verification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--editorial", action="store_true")
    args = parser.parse_args()
    result = verify_cleanup(
        args.output, load_profile(args.profile), args.editorial, args.baseline
    )
    print(
        f'{result["status"]}: {len(result["figures"])} figures; baseline checked: {result["baseline_checked"]}'
    )


if __name__ == "__main__":
    main()
