"""Validate cached OCR before selecting it or reconstructing a book."""

import math
import re
import subprocess
from pathlib import Path

from common import file_digest, read_json, write_text_atomic


def read_ocr_page(path, number):
    try:
        data = read_json(path)
        if (
            not isinstance(data, dict)
            or type(data.get("page")) is not int
            or data["page"] != number
        ):
            raise ValueError(f"expected page {number}")
        for key in ("width", "height"):
            value = data.get(key)
            if (
                type(value) not in (float, int)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"invalid {key}")
        if not isinstance(data.get("lines"), list):
            raise ValueError("lines must be a list")
        for i, row in enumerate(data["lines"]):
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                raise ValueError(f"line {i}: expected text")
            box = row.get("bbox")
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(
                    type(v) not in (float, int)
                    or not math.isfinite(v)
                    or not 0 <= v <= 1
                    for v in box
                )
                or box[0] >= box[2]
                or box[1] >= box[3]
            ):
                raise ValueError(f"line {i}: invalid normalized bbox")
            if "confidence" in row and (
                type(row["confidence"]) not in (float, int)
                or not math.isfinite(row["confidence"])
                or not 0 <= row["confidence"] <= 1
            ):
                raise ValueError(f"line {i}: invalid confidence")
        return data
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError(f"Invalid OCR cache page {path}: {error}") from error


def preflight_cache(cache, page_count, source_hash):
    cache = Path(cache)
    try:
        provenance = read_json(cache / "provenance.json")
        if (
            not isinstance(provenance, dict)
            or provenance.get("source_sha256") != source_hash
        ):
            raise ValueError("belongs to a different PDF")
    except (OSError, ValueError) as error:
        raise ValueError(f"Invalid OCR cache provenance {cache}: {error}") from error
    missing = [
        n for n in range(1, page_count + 1) if not (cache / f"{n:04}.json").is_file()
    ]
    if missing:
        raise ValueError(
            f"OCR cache {cache} lacks {len(missing)} pages (first {missing[0]}); rerun extraction"
        )
    for number in range(1, page_count + 1):
        read_ocr_page(cache / f"{number:04}.json", number)
    return provenance


def publish_cache(work, engine, cache, page_count, source_hash):
    """A partial or corrupt experiment never replaces the selected cache."""
    cache = Path(cache)
    if any(not (cache / f"{n:04}.json").is_file() for n in range(1, page_count + 1)):
        print(f"Cache incomplete; {engine}-cache.txt not updated", flush=True)
        return False
    preflight_cache(cache, page_count, source_hash)
    write_text_atomic(
        Path(work) / f"{engine}-cache.txt", str(Path(cache).resolve()) + "\n"
    )
    return True


def traineddata_digest(language="eng"):
    # Query the actual Tesseract search directory, honoring TESSDATA_PREFIX;
    # a package-manager prefix may point at a different installed model.
    output = subprocess.check_output(
        ["tesseract", "--list-langs"], stderr=subprocess.STDOUT, text=True
    )
    match = re.search(r'List of available languages in "([^"]+)"', output)
    if not match:
        raise ValueError("Cannot determine Tesseract traineddata directory")
    return file_digest(Path(match[1]) / f"{language}.traineddata")
