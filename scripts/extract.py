"""Cache local OCR with source, renderer, engine and platform provenance."""

import argparse
import hashlib
import json
import platform
import subprocess
import csv
import io
from pathlib import Path

import pymupdf

from common import ROOT, file_digest as sha


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
    fingerprint = {
        "source_sha256": sha(args.pdf),
        "dpi": args.dpi,
        "pymupdf": pymupdf.VersionBind,
        "platform": platform.platform(),
        "engine": "Apple Vision accurate en-US revision 3 language correction",
        "engine_source_sha256": sha(ROOT / "scripts/vision_ocr.swift"),
    }
    engine = None
    if args.engine == "tesseract":
        fingerprint["engine"] = (
            "Tesseract English OEM 1 PSM 3 (PSM 6 for index columns)"
        )
        fingerprint["tesseract"] = subprocess.check_output(
            ["tesseract", "--version"], text=True
        ).splitlines()[0]
        prefix = Path(
            subprocess.check_output(
                ["brew", "--prefix", "tesseract"], text=True
            ).strip()
        )
        fingerprint["eng_traineddata_sha256"] = sha(
            prefix / "share/tessdata/eng.traineddata"
        )
    if args.engine == "rapid":
        from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType
        import importlib.metadata

        models = args.models.resolve()
        fingerprint["engine"] = "RapidOCR PP-OCRv4 mobile English CPU"
        fingerprint["rapidocr"] = importlib.metadata.version("rapidocr")
        fingerprint["onnxruntime"] = importlib.metadata.version("onnxruntime")
        fingerprint["models"] = {p.name: sha(p) for p in sorted(models.glob("*.onnx"))}
        engine = RapidOCR(
            params={
                "Rec.lang_type": LangRec.EN,
                "Rec.ocr_version": OCRVersion.PPOCRV4,
                "Rec.model_type": ModelType.MOBILE,
                "Det.ocr_version": OCRVersion.PPOCRV4,
                "Det.model_type": ModelType.MOBILE,
                "Global.model_root_dir": str(models),
                "Rec.model_path": str(models / "en_PP-OCRv4_rec_mobile.onnx"),
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
    (cache / "provenance.json").write_text(json.dumps(fingerprint, indent=2) + "\n")
    (work / f"{args.engine}-cache.txt").write_text(str(cache) + "\n")
    if args.engine == "vision":
        (work / "active-cache.txt").write_text(str(cache) + "\n")
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
    doc = pymupdf.open(args.pdf)
    book = json.loads(args.profile.read_text())
    if book["source_sha256"] != sha(args.pdf):
        raise ValueError("Profile source hash mismatch")
    numbers = (
        list(map(int, args.pages.split(",")))
        if args.pages
        else list(range(1, len(doc) + 1))
    )
    for n in numbers:
        if not 1 <= n <= len(doc):
            raise ValueError(f"Invalid page {n}")
        target = cache / f"{n:04}.json"
        page = doc[n - 1]
        # Two-column index must be recognized separately to avoid line interleaving.
        split = book["index_splits"].get(str(n), 0.5) * page.rect.width
        clips = (
            [
                pymupdf.Rect(0, 0, split, page.rect.height),
                pymupdf.Rect(split, 0, page.rect.width, page.rect.height),
            ]
            if str(n) in book["index_splits"]
            else [page.rect]
        )
        if target.exists():
            cached = json.loads(target.read_text())
            if cached.get("clips") == [list(c) for c in clips] or (
                len(clips) == 1 and "clips" not in cached
            ):
                continue
        lines = []
        for col, clip in enumerate(clips):
            image = cache / f"{n:04}-{col}.png"
            page.get_pixmap(dpi=args.dpi, clip=clip).save(image)
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
                        "eng",
                        "--oem",
                        "1",
                        "--psm",
                        "6" if len(clips) > 1 else "3",
                        "tsv",
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
                raw = subprocess.check_output([str(binary), str(image)], text=True)
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
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        temp.replace(target)
        print(f"OCR {n}/{len(doc)}: {len(lines)} lines", flush=True)


if __name__ == "__main__":
    main()
