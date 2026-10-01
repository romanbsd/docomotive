"""Small, scan-checked accuracy sample. Not a whole-book accuracy claim."""

import json
import re
import unicodedata
from pathlib import Path
import pymupdf
from common import ROOT, write_json
from ocr import embedded, merge_rows


def normalize(s):
    s = (
        unicodedata.normalize("NFKC", s)
        .replace("’", "'")
        .replace("—", "-")
        .replace("–", "-")
    )
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = re.sub(r"\s+", " ", s).strip().lstrip("*•+ ")
    return s


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        new = [i]
        for j, y in enumerate(b, 1):
            new.append(min(row[j] + 1, new[-1] + 1, row[j - 1] + (x != y)))
        row = new
    return row[-1]


def main():
    cases = json.loads((ROOT / "config/ocr-benchmark.json").read_text())
    doc = pymupdf.open(next(ROOT.glob("*.pdf")))
    results = []
    for case in cases:
        n = case["page"]
        reference = normalize(case["text"])
        entry = {"page": n, "reference_characters": len(reference), "engines": {}}
        for name in ["embedded", "vision", "rapid", "tesseract", "ocrmypdf"]:
            if name == "ocrmypdf":
                text = (ROOT / f"work/inspection/ocrmypdf-{n}.txt").read_text()
                a = text.find(case["start"])
                b = text.find(case["end"], a)
                if a < 0 or b < 0:
                    raise ValueError(f"OCRmyPDF sample anchors missing on {n}")
                text = text[a : b + len(case["end"])]
            else:
                if name == "embedded":
                    rows = embedded(doc[n - 1])
                else:
                    cache = ROOT / (ROOT / f"work/{name}-cache.txt").read_text().strip()
                    rows = merge_rows(
                        json.loads((cache / f"{n:04}.json").read_text())["lines"]
                    )
                lo, hi = case["region"]
                text = "\n".join(r["text"] for r in rows if lo <= r["bbox"][1] < hi)
            text = normalize(text)
            d = distance(reference, text)
            entry["engines"][name] = {
                "character_edits": d,
                "cer": round(d / len(reference), 5),
                "text": text,
            }
        results.append(entry)
    write_json(
        ROOT / "output/ocr-benchmark.json",
        {
            "scope": "Three visually transcribed excerpts (body, permissions, footnote); no whole-book inference",
            "cases": results,
        },
    )
    print(
        json.dumps(
            [
                {
                    **{k: v for k, v in r.items() if k != "engines"},
                    "engines": {
                        k: {kk: vv for kk, vv in v.items() if kk != "text"}
                        for k, v in r["engines"].items()
                    },
                }
                for r in results
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
