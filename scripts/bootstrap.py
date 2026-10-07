"""Download checksum-pinned public resources; never download models during OCR."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--language", choices=("en", "ru"), default="en")
    a = p.parse_args()
    resources = json.loads((ROOT / "config/resources.json").read_text())
    if a.language == "ru":
        resources += json.loads((ROOT / "config/resources-ru.json").read_text())
    for item in resources:
        target = ROOT / item["path"]
        if not target.exists():
            if a.verify_only:
                raise FileNotFoundError(target)
            r = requests.get(item["url"], timeout=120)
            r.raise_for_status()
            data = r.content
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("Resource checksum mismatch: " + item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Resource checksum mismatch: " + item["path"])
        print("Verified " + item["path"])
    jar = ROOT / "work/tools/epubcheck-5.4.0/epubcheck.jar"
    if not jar.exists():
        if a.verify_only:
            raise FileNotFoundError(jar)
        with zipfile.ZipFile(ROOT / "work/epubcheck.zip") as archive:
            archive.extractall(ROOT / "work/tools")


if __name__ == "__main__":
    main()
