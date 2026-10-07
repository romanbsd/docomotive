"""Cache local OCR with source, renderer, engine and platform provenance."""

import argparse
import hashlib
import json
import platform
import subprocess
import csv
import io
import shutil
import ast
import inspect
from pathlib import Path

import pymupdf

from common import ROOT, file_digest as sha, load_profile, write_json
from ocr_cache import publish_cache, traineddata_digest, read_ocr_page
from profile_validation import validate_profile
from languages import ocr_settings, language_code


def clipped_observation(box, width, height):
    """Clip subpixel OCR edge overshoot; reject larger geometry errors."""
    # Some recognizers extend edge glyph boxes a fraction of one raster pixel.
    # Preserve the raw box as evidence; this is not permission to clip text.
    tolerance = [1 / width, 1 / height, 1 / width, 1 / height]
    if any(not -eps <= value <= 1 + eps for value, eps in zip(box, tolerance)):
        raise ValueError("OCR bounding box exceeds the raster by more than one pixel")
    result = [max(0, min(1, value)) for value in box]
    if result[0] >= result[2] or result[1] >= result[3]:
        raise ValueError("OCR bounding box is empty after clipping")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--pages", help="Comma-separated 1-based pages; default all")
    parser.add_argument(
        "--engine", choices=["vision", "rapid", "tesseract"], default="vision"
    )
    parser.add_argument("--profile", type=Path, default=ROOT / "config/book.json")
    parser.add_argument("--work", type=Path, default=ROOT / "work")
    parser.add_argument("--models", type=Path, default=ROOT / "work/models")
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(exist_ok=True)
    book = load_profile(args.profile)
    settings = ocr_settings(book)
    page_languages = (
        book.get("ocr_tesseract_page_languages", {})
        if args.engine == "tesseract"
        else {}
    )
    doc = pymupdf.open(args.pdf)
    validate_profile(book, args.profile, page_count=len(doc))
    source_hash = sha(args.pdf)
    if book["source_sha256"] != source_hash:
        raise ValueError("Profile source hash mismatch")
    numbers = (
        list(map(int, args.pages.split(",")))
        if args.pages
        else list(range(1, len(doc) + 1))
    )
    if any(not 1 <= n <= len(doc) for n in numbers):
        raise ValueError("Requested OCR page is outside the source PDF")
    fingerprint = {
        "source_sha256": source_hash,
        "dpi": args.dpi,
        "pymupdf": pymupdf.VersionBind,
        "platform": platform.platform(),
        "engine": "Apple Vision accurate en-US revision 3 language correction",
        "engine_source_sha256": sha(ROOT / "scripts/vision_ocr.swift"),
        "geometry_normalizer_sha256": hashlib.sha256(
            ast.dump(
                ast.parse(inspect.getsource(clipped_observation)),
                include_attributes=False,
            ).encode()
        ).hexdigest(),
    }
    if book.get("scan_raster_only"):
        fingerprint["page_pixels"] = "isolated-scan"
        fingerprint["scan_pixels_sha256"] = sha(ROOT / "scripts/scan_pixels.py")
    if language_code(book) != "en":
        fingerprint["language"] = book["language"]
        fingerprint["engine"] = (
            f"Apple Vision accurate {settings['vision']} revision 3 language correction"
        )
    engine = None
    if args.engine == "tesseract":
        fingerprint["engine"] = (
            "Tesseract English OEM 1 PSM 3 (PSM 6 for index columns)"
        )
        fingerprint["tesseract"] = subprocess.check_output(
            ["tesseract", "--version"], text=True
        ).splitlines()[0]
        if settings["tesseract"] == "eng" and settings["tessdata"] is None:
            fingerprint["eng_traineddata_sha256"] = traineddata_digest()
        else:
            fingerprint["engine"] = (
                f"Tesseract {settings['tesseract']} OEM 1 PSM 3 (PSM 6 for index columns)"
            )
            fingerprint["traineddata_sha256"] = {
                lang: (
                    sha(settings["tessdata"] / f"{lang}.traineddata")
                    if settings["tessdata"]
                    else traineddata_digest(lang)
                )
                for lang in sorted(
                    set(
                        "+".join(
                            [settings["tesseract"]] + list(page_languages.values())
                        ).split("+")
                    )
                )
            }
        if page_languages:
            fingerprint["page_languages"] = page_languages
    if args.engine == "rapid":
        from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType
        import importlib.metadata

        models = args.models.resolve()
        cyrillic = settings["rapid"] == "cyrillic"
        if settings["rapid"] not in ("en", "cyrillic"):
            raise ValueError("RapidOCR requires a configured supported script model")
        rec_model = (
            "cyrillic_PP-OCRv5_rec_mobile.onnx"
            if cyrillic
            else "en_PP-OCRv4_rec_mobile.onnx"
        )
        fingerprint["engine"] = "RapidOCR PP-OCRv4 mobile English CPU"
        if cyrillic:
            fingerprint["engine"] = (
                "RapidOCR PP-OCRv5 mobile Cyrillic CPU; PP-OCRv4 detection"
            )
        fingerprint["rapidocr"] = importlib.metadata.version("rapidocr")
        fingerprint["onnxruntime"] = importlib.metadata.version("onnxruntime")
        # Unused script models must not invalidate another language's cache.
        fingerprint["models"] = {
            name: sha(models / name)
            for name in sorted(
                (
                    rec_model,
                    "ch_PP-OCRv4_det_mobile.onnx",
                    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                )
            )
        }
        engine = RapidOCR(
            params={
                "Rec.lang_type": LangRec.CYRILLIC if cyrillic else LangRec.EN,
                "Rec.ocr_version": (
                    OCRVersion.PPOCRV5 if cyrillic else OCRVersion.PPOCRV4
                ),
                "Rec.model_type": ModelType.MOBILE,
                "Det.ocr_version": OCRVersion.PPOCRV4,
                "Det.model_type": ModelType.MOBILE,
                "Global.model_root_dir": str(models),
                "Rec.model_path": str(models / rec_model),
                "Det.model_path": str(models / "ch_PP-OCRv4_det_mobile.onnx"),
                "Cls.model_path": str(models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
                "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
    cache = (
        work
        / "ocr"
        / hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[
            :16
        ]
    )
    cache.mkdir(parents=True, exist_ok=True)
    write_json(cache / "provenance.json", fingerprint)
    # Changing a page's language set need not repeat unchanged observations.
    # Reuse only exact source/render/runtime/model fingerprints, never a cache
    # whose OCR language merely happens to match the book's main language.
    peers = []
    if page_languages:
        for other in cache.parent.glob("*/provenance.json"):
            if other.parent != cache:
                peers.append((other.parent, json.loads(other.read_text())))
    binary = work / "vision-ocr"
    stamp = work / "swift-source.sha256"
    if args.engine == "vision" and (
        not binary.exists()
        or not stamp.exists()
        or sha(ROOT / "scripts/vision_ocr.swift") != stamp.read_text().strip()
    ):
        subprocess.run(
            [
                "swiftc",
                "-module-cache-path",
                str(work / "swift-cache"),
                str(ROOT / "scripts/vision_ocr.swift"),
                "-o",
                str(binary),
            ],
            check=True,
        )
        (work / "swift-source.sha256").write_text(
            sha(ROOT / "scripts/vision_ocr.swift")
        )
    for n in numbers:
        target = cache / f"{n:04}.json"
        page = doc[n - 1]
        # Two-column index must be recognized separately to avoid line interleaving.
        split = book.get("index_splits", {}).get(str(n), 0.5) * page.rect.width
        clips = (
            [
                pymupdf.Rect(0, 0, split, page.rect.height),
                pymupdf.Rect(split, 0, page.rect.width, page.rect.height),
            ]
            if str(n) in book.get("index_splits", {})
            else [page.rect]
        )
        if target.exists():
            cached = read_ocr_page(target, n)
            if cached.get("clips") == [list(c) for c in clips] or (
                len(clips) == 1 and "clips" not in cached
            ):
                continue
        page_language = page_languages.get(str(n), settings["tesseract"])
        if peers:
            core = {
                k: v
                for k, v in fingerprint.items()
                if k not in ("page_languages", "traineddata_sha256")
            }
            models_used = {
                lang: fingerprint["traineddata_sha256"][lang]
                for lang in page_language.split("+")
            }
            reused = False
            for directory, provenance in peers:
                peer_core = {
                    k: v
                    for k, v in provenance.items()
                    if k not in ("page_languages", "traineddata_sha256")
                }
                peer_language = provenance.get("page_languages", {}).get(
                    str(n), settings["tesseract"]
                )
                candidate = directory / target.name
                if (
                    core == peer_core
                    and peer_language == page_language
                    and all(
                        provenance.get("traineddata_sha256", {}).get(k) == v
                        for k, v in models_used.items()
                    )
                    and candidate.exists()
                ):
                    data = read_ocr_page(candidate, n)
                    if data.get("clips") == [list(c) for c in clips]:
                        shutil.copyfile(candidate, target)
                        reused = True
                        break
            if reused:
                print(
                    f"Reused OCR {n}/{len(doc)}: unchanged page-language evidence",
                    flush=True,
                )
                continue
        lines = []
        for col, clip in enumerate(clips):
            image = cache / f"{n:04}-{col}.png"
            if book.get("scan_raster_only"):
                from scan_pixels import isolated_scan

                pixels, _ = isolated_scan(page, clip)
                # Preserve the original sampling: increasing DPI cannot add detail.
                pixels.save(image)
            else:
                page.get_pixmap(dpi=args.dpi, clip=clip).save(image)
            from PIL import Image

            with Image.open(image) as raster:
                raster_width, raster_height = raster.size
            if args.engine == "tesseract":
                from PIL import Image

                with Image.open(image) as im:
                    width, height = im.size
                raw = subprocess.check_output(
                    [
                        "tesseract",
                        str(image),
                        "stdout",
                        "-l",
                        page_language,
                        *(
                            ["--tessdata-dir", str(settings["tessdata"])]
                            if settings["tessdata"]
                            else []
                        ),
                        "--oem",
                        "1",
                        "--psm",
                        "6" if len(clips) > 1 else "3",
                        # Explicit output mode also works with model-only
                        # tessdata directories lacking the packaged configs/.
                        "-c",
                        "tessedit_create_tsv=1",
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                grouped = {}
                for word in csv.DictReader(
                    io.StringIO(raw), delimiter="\t", quoting=csv.QUOTE_NONE
                ):
                    if word["level"] != "5" or not word["text"].strip():
                        continue
                    key = tuple(word[k] for k in ["block_num", "par_num", "line_num"])
                    x, y, w, h = map(
                        int, [word[k] for k in ["left", "top", "width", "height"]]
                    )
                    grouped.setdefault(key, []).append(
                        {
                            "text": word["text"],
                            "confidence": float(word["conf"]) / 100,
                            "bbox": [
                                x / width,
                                y / height,
                                (x + w) / width,
                                (y + h) / height,
                            ],
                        }
                    )
                result = {"lines": []}
                for words in grouped.values():
                    result["lines"].append(
                        {
                            "text": " ".join(w["text"] for w in words),
                            "confidence": sum(w["confidence"] for w in words)
                            / len(words),
                            "bbox": [
                                min(w["bbox"][0] for w in words),
                                min(w["bbox"][1] for w in words),
                                max(w["bbox"][2] for w in words),
                                max(w["bbox"][3] for w in words),
                            ],
                        }
                    )
            elif engine is None:
                raw = subprocess.check_output(
                    [str(binary), "--language", settings["vision"], str(image)],
                    text=True,
                )
                result = json.loads(raw)
            else:
                from PIL import Image

                with Image.open(image) as im:
                    width, height = im.size
                r = engine(str(image))
                result = {
                    "lines": (
                        []
                        if r.txts is None
                        else [
                            {
                                "text": text,
                                "confidence": float(score),
                                "bbox": [
                                    float(box[:, 0].min() / width),
                                    float(box[:, 1].min() / height),
                                    float(box[:, 0].max() / width),
                                    float(box[:, 1].max() / height),
                                ],
                            }
                            for text, score, box in zip(r.txts, r.scores, r.boxes)
                        ]
                    )
                }
            for line in result["lines"]:
                box = line["bbox"]
                clipped = clipped_observation(box, raster_width, raster_height)
                if clipped != box:
                    line["raw_bbox"] = box
                    line["geometry_adjustment"] = "subpixel-raster-edge-clipping"
                box = clipped
                line["bbox"] = [
                    (clip.x0 + box[0] * clip.width) / page.rect.width,
                    box[1],
                    (clip.x0 + box[2] * clip.width) / page.rect.width,
                    box[3],
                ]
                line["column"] = col
                lines.append(line)
            image.unlink()
        data = {
            "page": n,
            "width": page.rect.width,
            "height": page.rect.height,
            "clips": [list(c) for c in clips],
            "lines": lines,
        }
        write_json(target, data)
        print(f"OCR {n}/{len(doc)}: {len(lines)} lines", flush=True)
    # Builds follow this pointer, so a partial or experimental run (--pages,
    # another --dpi) must not replace a complete cache.
    publish_cache(work, args.engine, cache, len(doc), fingerprint["source_sha256"])


if __name__ == "__main__":
    main()
