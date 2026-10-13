"""Source-backed, review-only proposals before accepting a scanned-book profile."""

import argparse
import difflib
import hashlib
import html
import json
import re
import statistics
from pathlib import Path

from PIL import Image
import pymupdf

from common import load_profile, write_json
from figure_proposals import group_proposals, propose_figures
from note_zones import separator_proposals, small_type_proposals
from ocr_cache import read_ocr_page
from source_document import open_source

ENGINES = ("tesseract", "vision", "rapid")


def cache_dirs(work, source_hash):
    result = {}
    for engine in ENGINES:
        pointer = Path(work) / f"{engine}-cache.txt"
        if pointer.exists():
            directory = Path(pointer.read_text().strip())
            provenance = json.loads((directory / "provenance.json").read_text())
            if provenance.get("source_sha256") != source_hash:
                raise ValueError(f"{engine} OCR cache belongs to a different source")
            result[engine] = directory
    if not result:
        raise ValueError("No selected OCR caches in the work directory")
    return result


def page_kind(number, book, rows, page_count):
    if number in book.get("index_pages", []):
        return "index"
    if number in {f["page"] for f in book.get("figures", [])}:
        return "figure"
    if str(number) in book.get("note_starts", {}):
        return "note"
    if number <= min(16, page_count // 10):
        return "front"
    if any(re.search(r"\bINDEX\b", r["text"], re.I) for r in rows[:5]):
        return "index"
    return "body"


def stratified_pages(kinds):
    """First, middle and last page of each source-observed page class."""
    groups = {}
    for number, kind in kinds.items():
        groups.setdefault(kind, []).append(number)
    return sorted(
        {pages[i] for pages in groups.values() for i in {0, len(pages) // 2, -1}}
    )


def benchmark_page(number, caches):
    pages = {
        name: read_ocr_page(path / f"{number:04}.json", number)
        for name, path in caches.items()
    }
    texts = {
        name: "\n".join(
            r["text"].strip()
            for r in sorted(
                data["lines"],
                key=lambda r: (r.get("column", 0), r["bbox"][1], r["bbox"][0]),
            )
            if r["text"].strip()
        )
        for name, data in pages.items()
    }
    engines = {}
    for name, data in pages.items():
        peers = [
            difflib.SequenceMatcher(
                None, texts[name], texts[other], autojunk=False
            ).ratio()
            for other in pages
            if other != name
        ]
        engines[name] = dict(
            characters=len(texts[name]),
            lines=len(data["lines"]),
            peer_agreement=round(statistics.mean(peers), 4) if peers else None,
            mean_reported_confidence=(
                round(statistics.mean(r.get("confidence", 0) for r in data["lines"]), 4)
                if data["lines"]
                else None
            ),
            geometry_adjustments=sum(
                bool(r.get("geometry_adjustment")) for r in data["lines"]
            ),
        )
    ranked = sorted(
        (name for name in engines if engines[name]["peer_agreement"] is not None),
        key=lambda name: (-engines[name]["peer_agreement"], name),
    )
    # Peer agreement is a triage signal, not source truth. A narrow margin
    # provides no useful engine proposal; two engines can share the same error.
    proposal = (
        ranked[0]
        if len(ranked) >= 3
        and engines[ranked[0]]["peer_agreement"] - engines[ranked[1]]["peer_agreement"]
        >= 0.02
        else None
    )
    return dict(
        page=number,
        engines=engines,
        review_first=proposal,
        status="source-review-required" if proposal else "abstained",
    )


def page_label_proposals(rows):
    return [
        dict(
            text=r["text"].strip(),
            bbox=r["bbox"],
            evidence="isolated margin numeral",
        )
        for r in rows
        if (r["bbox"][1] > 0.88 or r["bbox"][3] < 0.075)
        and re.fullmatch(r"(?:\d{1,4}|[ivxlcdm]{1,8})", r["text"].strip(), re.I)
    ]


def heading_proposals(rows):
    candidates = []
    for r in rows:
        text = r["text"].strip()
        box = r["bbox"]
        if not (0.07 < box[1] < 0.55 and 3 <= len(text) <= 85):
            continue
        cue = bool(re.match(r"^(?:chapter|part|book|section)\b", text, re.I))
        cue |= bool(re.match(r"^[IVXLCDM]+[. ]", text))
        cue |= text.isupper() and sum(c.isalpha() for c in text) >= 5
        if cue and 0.25 <= (box[0] + box[2]) / 2 <= 0.75:
            candidates.append(
                dict(text=text, bbox=box, evidence="short centered heading cue")
            )
    return candidates


def index_split_proposal(rows):
    left = [r["bbox"][2] for r in rows if r["bbox"][2] < 0.49 and r["bbox"][1] < 0.9]
    right = [r["bbox"][0] for r in rows if r["bbox"][0] > 0.51 and r["bbox"][1] < 0.9]
    if len(left) < 8 or len(right) < 8:
        return None
    edge = sorted(left)[round(0.85 * (len(left) - 1))]
    start = sorted(right)[round(0.15 * (len(right) - 1))]
    if start - edge < 0.025:
        return None
    return dict(
        split=round((edge + start) / 2, 4),
        left_edge=round(edge, 4),
        right_edge=round(start, 4),
        evidence="OCR row clusters; verify gutter against scan",
    )


def chapter_ranges(pages, page_count):
    """Turn heading-like OCR rows into reviewable ranges, never profile edits."""
    starts = []
    for item in pages:
        candidates = [h for h in item["headings"] if h["bbox"][1] > 0.10]
        if not candidates:
            continue
        first = min(candidates, key=lambda h: h["bbox"][1])
        if item["kind"] == "index" or first["bbox"][1] > 0.45:
            continue
        starts.append((item["page"], first["text"], first["bbox"]))
    return [
        dict(
            start=n,
            end=starts[i + 1][0] - 1 if i + 1 < len(starts) else page_count,
            title=title,
            bbox=box,
            status="source-review-required",
        )
        for i, (n, title, box) in enumerate(starts)
    ]


def printed_offset_runs(pages):
    observed = {}
    for item in pages:
        for label in item["printed_labels"]:
            if label["text"].isdigit():
                offset = int(label["text"]) - item["page"]
                observed.setdefault(offset, []).append(item["page"])
    return [
        dict(offset=offset, pages=pages, status="source-review-required")
        for offset, pages in sorted(observed.items())
        if len(pages) >= 3
    ]


def save_preview(page, target):
    pix = page.get_pixmap(dpi=105)
    image = pix.pil_image().convert("RGB")
    image.thumbnail((900, 1300), Image.Resampling.LANCZOS)
    image.save(target, "JPEG", quality=78, optimize=True)


def save_note_crop(page, cutoff, target):
    top = max(0, cutoff - 0.035)
    clip = pymupdf.Rect(0, top * page.rect.height, page.rect.width, page.rect.height)
    image = page.get_pixmap(dpi=160, clip=clip).pil_image().convert("RGB")
    image.save(target, "JPEG", quality=82, optimize=True)


def footer_readings(number, caches, cutoff):
    readings = {}
    for name, path in caches.items():
        rows = read_ocr_page(path / f"{number:04}.json", number)["lines"]
        readings[name] = [r["text"] for r in rows if r["bbox"][1] >= cutoff][:4]
    return readings


def collect(source, work, output, profile=None, build_output=None):
    book = load_profile(profile) if profile else {}
    source_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    if book and source_hash != book["source_sha256"]:
        raise ValueError("Source PDF does not match the supplied profile")
    caches = cache_dirs(work, source_hash)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    previews = output / "pages"
    previews.mkdir(exist_ok=True)
    apparatus = {}
    if build_output:
        apparatus_path = Path(build_output) / "apparatus-analysis.json"
        if apparatus_path.exists():
            apparatus = json.loads(apparatus_path.read_text())
    unresolved_pages = {
        row["page"]
        for kind in ("footnotes", "endnotes")
        for row in apparatus.get(kind, {}).get("unresolved", [])
        if type(row.get("page")) is int
    }
    report = dict(
        source_sha256=source_hash,
        policy="review-only",
        toc=[],
        pages=[],
        benchmark=[],
        link_review=[],
        chapter_ranges=[],
        printed_page_offsets=[],
    )
    with open_source(source) as doc:
        report["toc"] = [
            dict(level=level, title=title, page=number)
            for level, title, number in doc.get_toc()
        ]
        all_rows = {}
        kinds = {}
        for number in range(1, len(doc) + 1):
            primary = "tesseract" if "tesseract" in caches else next(iter(caches))
            rows = read_ocr_page(caches[primary] / f"{number:04}.json", number)["lines"]
            all_rows[number] = rows
            kinds[number] = page_kind(number, book, rows, len(doc))
        sampled = set(stratified_pages(kinds))
        for number in range(1, len(doc) + 1):
            rows = all_rows[number]
            page = doc[number - 1]
            figure_rects = [
                f["rect"] for f in book.get("figures", []) if f["page"] == number
            ]
            heading = heading_proposals(rows)
            label = page_label_proposals(rows)
            small = small_type_proposals(rows, figure_rects)
            # Probe footer rules on every page; artwork detection uses sparse
            # pages so index text does not become false illustration evidence.
            likely_art = kinds[number] == "figure" or (
                kinds[number] != "index"
                and len([r for r in rows if len(r["text"]) > 45]) < 7
            )
            image = page.get_pixmap(dpi=105).pil_image()
            ruled = separator_proposals(image, figure_rects)
            figures = (
                group_proposals(propose_figures(image, rows), rows)
                if likely_art
                else []
            )
            index = index_split_proposal(rows) if kinds[number] == "index" else None
            cutoffs = [p["cutoff"] for p in small + ruled]
            if (
                not any((heading, label, small, ruled, figures, index))
                and number not in sampled
                and number not in unresolved_pages
            ):
                continue
            preview = f"pages/{number:04}.jpg"
            save_preview(page, output / preview)
            note_crop = None
            if cutoffs or number in unresolved_pages:
                note_crop = f"pages/{number:04}-note.jpg"
                save_note_crop(
                    page, min(cutoffs) if cutoffs else 0.66, output / note_crop
                )
            report["pages"].append(
                dict(
                    page=number,
                    kind=kinds[number],
                    preview=preview,
                    headings=heading,
                    printed_labels=label,
                    figure_regions=figures,
                    note_zones=dict(
                        small_type=small,
                        separator=ruled,
                        crop=note_crop,
                        engine_readings=(
                            footer_readings(number, caches, min(cutoffs))
                            if cutoffs
                            else {}
                        ),
                        status="source-review-required" if small or ruled else "none",
                    ),
                    index_gutter=index,
                )
            )
        report["benchmark"] = [benchmark_page(n, caches) for n in sorted(sampled)]
        report["chapter_ranges"] = chapter_ranges(report["pages"], len(doc))
        report["printed_page_offsets"] = printed_offset_runs(report["pages"])
    crops = {p["page"]: p["note_zones"]["crop"] for p in report["pages"]}
    for kind in ("footnotes", "endnotes"):
        for row in apparatus.get(kind, {}).get("unresolved", []):
            report["link_review"].append(
                dict(
                    kind=kind,
                    **row,
                    crop=crops.get(row.get("page")),
                    status="abstained",
                )
            )
    write_json(output / "preflight.json", report)
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Conversion preflight</title>",
        "<style>body{max-width:75em;margin:2em auto;font:16px/1.5 sans-serif}section{border-top:1px solid #aaa;padding:1em 0}img{max-width:100%}pre{white-space:pre-wrap}</style>",
        "<h1>Conversion preflight</h1><p>All proposals require source review. OCR agreement does not establish printed truth.</p>",
    ]
    parts.append(
        "<h2>Chapter and page-label proposals</h2><pre>"
        + html.escape(
            json.dumps(
                {
                    k: report[k]
                    for k in ("toc", "chapter_ranges", "printed_page_offsets")
                },
                indent=2,
            )
        )
        + "</pre>"
    )
    parts.append(
        "<h2>OCR engine sample</h2><pre>"
        + html.escape(json.dumps(report["benchmark"], indent=2))
        + "</pre>"
    )
    parts.append(
        "<h2>Unresolved note links</h2><pre>"
        + html.escape(json.dumps(report["link_review"], indent=2))
        + "</pre>"
    )
    for item in report["pages"]:
        details = {k: v for k, v in item.items() if k not in ("page", "preview") and v}
        parts.append(
            f"<section><h2>PDF page {item['page']}</h2><pre>{html.escape(json.dumps(details, ensure_ascii=False, indent=2))}</pre><img loading='lazy' src='{item['preview']}' alt='Source page {item['page']}'></section>"
        )
        if item["note_zones"]["crop"]:
            parts.append(
                f"<img loading='lazy' src='{item['note_zones']['crop']}' alt='Source footer crop on PDF page {item['page']}'>"
            )
    (output / "index.html").write_text("\n".join(parts) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--work",
        type=Path,
        required=True,
        help="directory with selected OCR cache pointers",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--build-output", type=Path)
    args = parser.parse_args()
    report = collect(args.pdf, args.work, args.output, args.profile, args.build_output)
    print(
        f"{len(report['pages'])} source previews; {len(report['benchmark'])} OCR sample pages; {len(report['link_review'])} unresolved links"
    )


if __name__ == "__main__":
    main()
