"""Open PDFs and DjVu scans through a shared, fingerprinted raster adapter."""

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from common import ROOT, file_digest, read_json, write_json

DJVU_SUFFIXES = {".djvu", ".djv"}
CACHE_ERRORS = (OSError, ValueError, KeyError, TypeError, pymupdf.FileDataError)


def open_source(source, cache_root=None, *, native=False):
    """Keep source identity at the original file; DjVu text is re-recognized."""
    source = Path(source).resolve()
    if source.suffix.lower() not in DJVU_SUFFIXES:
        return pymupdf.open(source)
    if native:
        raise ValueError("DjVu requires scanned OCR; text_source=native is PDF-only")
    decoder = shutil.which("ddjvu")
    inspector = shutil.which("djvused")
    if not decoder or not inspector:
        raise FileNotFoundError(
            "DjVu input requires DjVuLibre (ddjvu and djvused on PATH)"
        )
    fingerprint = {
        "format": "djvu",
        "source_sha256": file_digest(source),
        "ddjvu_sha256": file_digest(decoder),
        "djvused_sha256": file_digest(inspector),
        "adapter_sha256": file_digest(Path(__file__)),
        "options": ["-format=pdf", "-quality=deflate"],
    }
    key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    directory = Path(cache_root or ROOT / "work/sources") / key
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "raster.pdf"
    stamp = directory / "provenance.json"
    record = None
    try:
        candidate = read_json(stamp)
        if candidate["fingerprint"] == fingerprint and candidate[
            "pdf_sha256"
        ] == file_digest(target):
            with pymupdf.open(target) as doc:
                if len(doc) == candidate["page_count"] and len(doc) > 0:
                    record = candidate
    except CACHE_ERRORS:
        pass
    if record is None:
        count = int(
            subprocess.check_output(
                [inspector, str(source), "-e", "n"], text=True
            ).strip()
        )
        if count < 1:
            raise ValueError("DjVu source has no pages")
        with tempfile.TemporaryDirectory(prefix=".render-", dir=directory) as staging:
            rendered = Path(staging) / "raster.pdf"
            subprocess.run(
                [decoder, *fingerprint["options"], str(source), str(rendered)],
                check=True,
                capture_output=True,
            )
            with pymupdf.open(rendered) as doc:
                if len(doc) != count or any(page.rect.is_empty for page in doc):
                    raise ValueError(
                        "DjVu rendering changed the page count or produced empty pages"
                    )
            record = {
                "fingerprint": fingerprint,
                "page_count": count,
                "pdf_sha256": file_digest(rendered),
            }
            rendered.replace(target)
            write_json(stamp, record)
    doc = pymupdf.open(target)
    doc.source_rendering = record
    return doc
