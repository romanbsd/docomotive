"""Research missing chapter-scoped citations using bounded local scan evidence."""

import ast
import base64
import difflib
import html
import inspect
import io
import json
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np
import PIL
import pymupdf
import scipy
from PIL import Image, ImageOps

import scanned_notes as scan
import marker_evidence
from book_model import classify_row
from common import digest, write_json
from ocr_cache import traineddata_digest
from marker_evidence import MarkerEvidence, align_markers, trusted_anchors


def citation_searches(pages, book):
    """Bound missing labels by existing source positions, never estimated page means."""
    searches, anchors = [], []
    for section in book["endnote_sections"]:
        chapter = section["source_chapter"]
        first, last, _ = book["chapters"][chapter - 1]
        known = []
        for page in range(first, last + 1):
            for index, row in enumerate(pages[page]):
                if classify_row(page, row, book, page == first)[0] != "body":
                    continue
                for style in row.get("inline", []):
                    label = row["text"][style["start"] : style["end"]]
                    if "sup" in style["tags"] and label.isascii() and label.isdigit():
                        known.append(
                            dict(
                                chapter=chapter,
                                number=int(label),
                                position=[page, index, style["start"]],
                            )
                        )
        known.sort(key=lambda a: a["position"])
        known = trusted_anchors(known, section["expected_notes"])
        anchors.extend(known)
        trusted = [a for a in known if a["trusted"]]
        for number in range(1, section["expected_notes"] + 1):
            if any(a["number"] == number for a in known):
                continue
            lower = [a for a in trusted if a["number"] < number]
            upper = [a for a in trusted if a["number"] > number]
            left = max((a["position"] for a in lower), default=[first, -1, -1])
            right = min(
                (a["position"] for a in upper), default=[last, len(pages[last]), 0]
            )
            searches.append(
                dict(
                    chapter=chapter,
                    number=number,
                    lower=left,
                    upper=right,
                    pdf_pages=[left[0], right[0]],
                    status=("searchable" if left < right else "anchor-order-conflict"),
                )
            )
    return searches, anchors


def preserve_gap(change):
    """Keep existing punctuation; only a tiny marker gap may change characters."""
    before, after = change["before"], change["after"]
    normalized = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
    matcher = difflib.SequenceMatcher(
        None, before.translate(normalized), after.translate(normalized), autojunk=False
    )
    pieces = []
    for op, a, aa, b, bb in matcher.get_opcodes():
        if op == "equal":
            pieces.append(before[a:aa])
        else:
            # Never let line OCR rewrite a quotation, word, year or whitespace.
            if any(c not in "0123456789*†‡?®°ºIl|oOa" for c in before[a:aa]) or (
                any(c not in "0123456789" for c in after[b:bb])
            ):
                return None
            pieces.append(after[b:bb])
    return dict(change, after="".join(pieces))


def placement_available(row, option):
    """A missing marker cannot reuse pixels/text already assigned to a reference."""
    return not any(
        ("sup" in style["tags"] or style.get("href"))
        and style["start"] < option["marker_end"]
        and style["end"] > option["marker_start"]
        for style in row.get("inline", [])
    )


def normalized_observations(page, rows, maximum):
    """Reuse raised-glyph geometry and OCR placement after normalizing tinted paper."""
    image = Image.open(io.BytesIO(page.get_pixmap(dpi=400).tobytes("png"))).convert("L")
    # Clip 1% at each histogram tail: dust/shadows must not set the ink-paper
    # range. This changes detection pixels, never the source PDF or book images.
    image = ImageOps.autocontrast(image, cutoff=1)
    width, height = image.size
    records = []
    for index, row in enumerate(rows):
        if len(row["text"]) < 3 or row.get("kind", "text") != "text":
            continue
        crop, left, top = scan.line_crop(image, row, variable=True)
        cap = scan.nearby_cap(row, rows, image) if len(row["text"]) < 40 else None
        regions = scan.raised_regions(crop, cap, variable=True)
        if not regions:
            continue
        witnesses = {}
        for box in regions:
            readings = scan.glyph_readings(crop, box, maximum)
            numeric = {v for v in readings if v.isascii() and v.isdigit()}
            if not numeric:
                continue
            if max(readings.count(v) for v in numeric) < 2:
                # Thin serif 1s become L in padded line modes. Tight, unwhitelisted
                # single-character reads add observations, not the expected digit.
                glyph = crop.crop(box)
                for scale in (2, 4):
                    enlarged = glyph.resize((glyph.width * scale, glyph.height * scale))
                    readings.append(
                        scan.tesseract(
                            ImageOps.expand(enlarged, border=30, fill=255), 10
                        )
                        .decode()
                        .strip()
                    )
            options = []
            for value in sorted(set(readings)):
                if (
                    not value.isascii()
                    or not value.isdigit()
                    or value.startswith("0")
                    or not 1 <= int(value) <= maximum
                ):
                    continue
                change = None
                witness = ""
                for variant in (
                    (True, False),
                    (False, False),
                    (True, True),
                    (False, True),
                ):
                    if variant not in witnesses:
                        witnesses[variant] = scan.character_line(
                            crop, clean=variant[0], deskew=variant[1]
                        )
                    witness, chars = witnesses[variant]
                    change = scan.replacement(row["text"], witness, chars, box, value)
                    if change:
                        break
                if not change:
                    witness, chars = scan.marker_context(crop, box, value)
                    change = scan.replacement(row["text"], witness, chars, box, value)
                if change:
                    change = preserve_gap(change)
                if change:
                    options.append(
                        dict(
                            number=int(value),
                            votes=readings.count(value),
                            witness=witness,
                            **change,
                        )
                    )
            x, y, xx, yy = box
            records.append(
                dict(
                    row=index,
                    text=row["text"],
                    readings=readings,
                    options=options,
                    bbox=[
                        (left + x) / width,
                        (top + y) / height,
                        (left + xx) / width,
                        (top + yy) / height,
                    ],
                )
            )
    return records


def observation_provenance(source_hash):
    """Observation caches exclude selection/report code, so policy retries stay cheap."""
    return dict(
        source_sha256=source_hash,
        dpi=400,
        normalization="autocontrast-1-percent",
        tesseract=subprocess.run(
            ["tesseract", "--version"], capture_output=True, check=True
        )
        .stdout.decode()
        .splitlines()[0],
        eng_traineddata_sha256=traineddata_digest(),
        pymupdf=pymupdf.VersionBind,
        pillow=PIL.__version__,
        numpy=np.__version__,
        scipy=scipy.__version__,
        helpers_sha256=digest(
            "".join(
                ast.dump(ast.parse(inspect.getsource(f)), include_attributes=False)
                for f in (
                    scan.components,
                    scan.prose_baseline,
                    scan.raised_regions,
                    scan.tesseract,
                    scan.character_line,
                    scan.replacement,
                    scan.marker_context,
                    scan.line_crop,
                    scan.nearby_cap,
                    scan.glyph_readings,
                    scan.complete_marker_crop,
                    scan.retry_readings,
                )
            ).encode()
        ),
        observation_sha256=digest(
            "".join(
                ast.dump(ast.parse(inspect.getsource(f)), include_attributes=False)
                for f in (normalized_observations, preserve_gap)
            ).encode()
        ),
    )


def cached_row_witnesses(work, number, row):
    """Use fingerprint-validated extraction caches; omissions are not negative votes."""
    from common import cache_path
    from ocr import merge_rows, nearest

    witnesses = []
    for engine in ("vision", "rapid"):
        if not (Path(work) / (engine + "-cache.txt")).exists():
            continue
        path = cache_path(work, engine) / f"{number:04}.json"
        if not path.exists():
            continue
        source = nearest(row, merge_rows(json.loads(path.read_text())["lines"]))
        if (
            source
            and difflib.SequenceMatcher(
                None, row["text"], source["text"], autojunk=False
            ).ratio()
            >= 0.85
        ):
            witnesses.append(
                dict(engine=engine, text=source["text"], bbox=source["bbox"])
            )
    return witnesses


def marker_witness_matches(primary, change, value, witness):
    """A digit elsewhere in a line cannot corroborate this marker's word gap."""
    import re

    digits = list(re.finditer(r"(?<!\d)" + re.escape(value) + r"(?!\d)", witness))
    if len(digits) != 1:
        return False
    token = digits[0]
    left = list(
        re.finditer(
            r"[A-Za-z]+|(?<!\d)(?:1\d{3}|20\d{2})(?!\d)", witness[: token.start()]
        )
    )
    if not left:
        return False
    word = left[-1]
    matcher = difflib.SequenceMatcher(None, witness, primary, autojunk=False)
    return any(
        a <= word.start()
        and word.end() <= a + n
        and b + word.end() - a == change["start"]
        for a, b, n in matcher.get_matching_blocks()
    )


def title_marker_change(primary, witness, value):
    """Allow a capitalized publication/name before a parenthetical continuation."""
    import re

    source = re.search(
        r"\b([A-Z][A-Za-z]{3,})(" + re.escape(value) + r")\s*\(([A-Za-z]+)", witness
    )
    if not source:
        return None
    target = re.search(
        r"\b"
        + re.escape(source[1])
        + r"([*†‡?®°ºIl|oOa0-9]*)\s*\("
        + re.escape(source[3]),
        primary,
    )
    if not target:
        return None
    start, end = target.span(1)
    # Source ink and a second OCR engine are required by the caller; this only
    # aligns a narrow marker slot, without adding punctuation to a title.
    return dict(
        start=start,
        end=end,
        before=target[1],
        after=value,
        marker_start=start,
        marker_end=start + len(value),
    )


def corroborate_observation(page, row, observed, witnesses, work, provenance):
    """Raw pixels break normalization ties; independent OCR verifies the same slot."""
    import re

    semantics = "".join(
        ast.dump(ast.parse(inspect.getsource(f)), include_attributes=False)
        for f in (marker_witness_matches, title_marker_change, corroborate_observation)
    )
    key = digest(
        json.dumps(
            dict(
                provenance=provenance,
                bbox=observed["bbox"],
                row=row["text"],
                witnesses=witnesses,
                semantics=semantics,
            ),
            sort_keys=True,
        ).encode()
    )
    cache = Path(work) / "citation-research-corroboration" / (key + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    raw = scan.retry_readings(page, observed["bbox"])
    options = []
    values = sorted(
        {v for w in witnesses for v in re.findall(r"(?<!\d)\d{1,3}(?!\d)", w["text"])}
    )
    # Preserve grayscale openings that thresholding can merge (3/8, 5/1).
    image = Image.open(io.BytesIO(page.get_pixmap(dpi=400).tobytes("png"))).convert("L")
    crop, left, top = scan.line_crop(image, row, variable=False)
    x, y, xx, yy = observed["bbox"]
    box = (
        x * image.width - left,
        y * image.height - top,
        xx * image.width - left,
        yy * image.height - top,
    )
    for value in values:
        numeric = {v for v in raw["readings"] if v.isascii() and v.isdigit()}
        # A thin glyph may vanish in grayscale resampling. One raw read
        # suffices only when two normalized reads and a second engine agree.
        raw_votes = raw["readings"].count(value)
        normalized_votes = observed["readings"].count(value)
        if (
            raw_votes < 2 and not (raw_votes >= 1 and normalized_votes >= 2)
        ) or numeric != {value}:
            continue
        change = None
        used = ""
        for clean, deskew in ((False, False), (True, False), (False, True)):
            used, chars = scan.character_line(crop, clean=clean, deskew=deskew)
            proposed = scan.replacement(row["text"], used, chars, box, value)
            if not proposed:
                continue
            agreeing = [
                w
                for w in witnesses
                if marker_witness_matches(row["text"], proposed, value, w["text"])
            ]
            if not agreeing:
                continue
            change = preserve_gap(proposed)
            if (
                not change
                and proposed["before"].endswith(("”", "’", '"', "'"))
                and proposed["after"] == proposed["before"][:-1] + value
            ):
                # A closing-quote OCR token can itself be the printed digit.
                # Only raw digit agreement plus a second engine permits removal.
                change = proposed
                change["quote_glyph_confusion"] = True
            if change:
                break
        if not change:
            # Mask only the observed glyph and reinsert it at its measured x
            # position. The title fallback must have a fresh geometric anchor.
            context, _ = scan.marker_context(crop, tuple(map(round, box)), value)
            proposed = title_marker_change(row["text"], context, value)
            if proposed:
                agreeing = [
                    w
                    for w in witnesses
                    if marker_witness_matches(row["text"], proposed, value, w["text"])
                ]
                if agreeing:
                    change = proposed
                    used = context
        if change:
            options.append(
                dict(
                    number=int(value),
                    votes=max(raw_votes, normalized_votes),
                    witness=used,
                    corroborating_engines=agreeing,
                    raw_corroborated=True,
                    **change,
                )
            )
    # Repeated punctuation reads plus a literal asterisk in line OCR provide
    # positive symbol evidence, rather than treating OCR silence as a rejection.
    symbol_evidence = (
        "*" in row["text"]
        and "*" in raw["readings"]
        and sum(v in ("*", "†", "‡", ":", ",") for v in raw["readings"]) >= 2
    )
    result = dict(
        options=options,
        raw_readings=raw["readings"],
        raw_symbol_evidence=symbol_evidence,
        corroboration_sha256=key,
    )
    write_json(cache, result)
    return result


def research_missing_markers(pages, doc, book, work, source_hash, audit, review):
    searches, anchors = citation_searches(pages, book)
    for anchor in anchors:
        if not anchor["trusted"]:
            page, index, _ = anchor["position"]
            review.append(
                dict(
                    anchor,
                    page=page,
                    row=index,
                    text=pages[page][index]["text"],
                    bbox=pages[page][index]["bbox"],
                    kind="citation-anchor-review",
                    status="needs-review",
                )
            )
    provenance = observation_provenance(source_hash)
    all_candidates = []
    plans = defaultdict(list)
    for search in searches:
        if search["status"] == "searchable":
            plans[search["chapter"]].append(search)
    for chapter, missing in plans.items():
        first, last, _ = book["chapters"][chapter - 1]
        maximum = next(
            s["expected_notes"]
            for s in book["endnote_sections"]
            if s["source_chapter"] == chapter
        )
        for number in range(first, last + 1):
            indices = [
                i
                for i, row in enumerate(pages[number])
                if classify_row(number, row, book, number == first)[0] == "body"
                and any(
                    s["lower"][:2] <= [number, i] <= s["upper"][:2] for s in missing
                )
            ]
            if not indices:
                continue
            rows = [pages[number][i] for i in indices]
            key = digest(
                json.dumps(
                    dict(
                        provenance=provenance,
                        page=number,
                        maximum=maximum,
                        rows=[
                            {
                                k: r[k]
                                for k in ("text", "bbox", "kind", "column")
                                if k in r
                            }
                            for r in rows
                        ],
                    ),
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            )
            cache = Path(work) / "citation-research" / (key + ".json")
            if cache.exists():
                observations = json.loads(cache.read_text())
            else:
                observations = normalized_observations(doc[number - 1], rows, maximum)
                write_json(cache, observations)
            for observed in observations:
                record = dict(
                    observed,
                    page=number,
                    chapter=chapter,
                    row=indices[observed["row"]],
                    evidence_sha256=key,
                )
                row = pages[number][record["row"]]
                witnesses = cached_row_witnesses(work, number, row)
                if witnesses:
                    extra = corroborate_observation(
                        doc[number - 1], row, observed, witnesses, work, provenance
                    )
                    record.update({k: v for k, v in extra.items() if k != "options"})
                    # Keep one option per number/slot; retain the stronger raw-pixel
                    # observation when it corroborates the normalized candidate.
                    options = {
                        (o["number"], o["marker_start"]): o for o in observed["options"]
                    }
                    options.update(
                        {(o["number"], o["marker_start"]): o for o in extra["options"]}
                    )
                else:
                    options = {
                        (o["number"], o["marker_start"]): o for o in observed["options"]
                    }
                record["options"] = [
                    o
                    for o in options.values()
                    if placement_available(pages[number][record["row"]], o)
                    and any(
                        o["number"] == s["number"]
                        and s["lower"]
                        < [number, record["row"], o["marker_start"]]
                        < s["upper"]
                        for s in missing
                    )
                ]
                # Retain rejected/unplaced regions for audit; silence is not a
                # negative vote and must remain distinguishable from a symbol.
                all_candidates.append(record)
        print(
            f"Citation research: chapter {chapter}: searched {len(missing)} missing labels",
            flush=True,
        )
    accepted, alignments = [], []
    for chapter in plans:
        evidence = []
        for index, candidate in enumerate(all_candidates):
            if candidate["chapter"] != chapter:
                continue
            candidate["marker_evidence"] = []
            for option_index, option in enumerate(candidate["options"]):
                observed = MarkerEvidence.from_candidate(
                    candidate, option, index, option_index
                )
                candidate["marker_evidence"].append(observed.report())
                if not observed.rejected_symbol:
                    evidence.append(observed)
        # Trusted anchors participate in scoring, but search bounds ensure they
        # remain compatible with every proposed marker. No existing text edits.
        for anchor in anchors:
            if anchor["chapter"] == chapter and anchor["trusted"]:
                page, row, start = anchor["position"]
                evidence.append(
                    MarkerEvidence.from_candidate(
                        dict(page=page, row=row),
                        dict(
                            number=anchor["number"],
                            votes=0,
                            marker_start=start,
                            start=start,
                            end=start + len(str(anchor["number"])),
                            anchor=True,
                        ),
                        -1,
                        len(evidence),
                    )
                )
        decisions = align_markers(evidence)
        alignments.append(dict(chapter=chapter, decisions=decisions))
        for decision in decisions:
            if decision["candidate"] < 0 or decision["status"] != "selected":
                continue
            candidate = all_candidates[decision["candidate"]]
            option = candidate["options"][decision["option_index"]]
            accepted.append(
                dict(candidate, **option, sequence_margin=decision["margin"])
            )
        for search in (s for s in searches if s["chapter"] == chapter):
            observed = [
                e for e in evidence if e.candidate >= 0 and e.number == search["number"]
            ]
            search["candidate_count"] = len(observed)
            matching = [
                d
                for d in decisions
                if d["candidate"] >= 0 and d["number"] == search["number"]
            ]
            search["status"] = (
                "selected"
                if any(d["status"] == "selected" for d in matching)
                else (
                    "ambiguous"
                    if any(d["status"] == "sequence-ambiguous" for d in matching)
                    else "insufficient-evidence" if observed else "no-candidate"
                )
            )
    for p in sorted(
        accepted, key=lambda p: (p["page"], p["row"], p["start"]), reverse=True
    ):
        row = pages[p["page"]][p["row"]]
        if row["text"][p["start"] : p["end"]] != p["before"]:
            raise ValueError("Citation research placement precondition failed")
        delta = len(p["after"]) - len(p["before"])
        for style in row.get("inline", []):
            for bound in ("start", "end"):
                if style[bound] >= p["end"]:
                    style[bound] += delta
                elif style[bound] > p["start"]:
                    style[bound] = p["start"] + (
                        len(p["after"]) if bound == "end" else 0
                    )
        row["text"] = row["text"][: p["start"]] + p["after"] + row["text"][p["end"] :]
        row.setdefault("inline", []).append(
            dict(start=p["marker_start"], end=p["marker_end"], tags=["sup"])
        )
        audit.append(dict(p, kind="targeted-citation-recovery", status="applied"))
        next(
            s
            for s in searches
            if s["chapter"] == p["chapter"] and s["number"] == p["number"]
        )["status"] = "applied"
    # A detached OCR digit must not remain as duplicate prose after recovery.
    scan.absorb_marker_fragments(
        pages, [dict(p, status="applied") for p in accepted], audit
    )
    for candidate in all_candidates:
        if not any(
            p["evidence_sha256"] == candidate["evidence_sha256"]
            and p["bbox"] == candidate["bbox"]
            for p in accepted
        ):
            review.append(
                dict(candidate, kind="targeted-citation-review", status="needs-review")
            )
    return dict(
        provenance=provenance,
        selection_policy=dict(
            method="chapter-wide-exclusion-margin",
            minimum_margin=3,
            evidence_sha256=digest(Path(marker_evidence.__file__).read_bytes()),
        ),
        searches=searches,
        anchors=anchors,
        alignments=alignments,
        candidates=all_candidates,
        applied=len(accepted),
        remaining=sum(s["status"] != "applied" for s in searches),
    )


def research_html(report, book, doc=None):
    """Expose remaining bounds and machine observations without claiming certainty."""
    rows = []
    offset = book.get("printed_page_offset", 0)
    for search in report["searches"]:
        if search["status"] == "applied":
            continue
        a, b = search["pdf_pages"]
        rows.append(
            f'<tr><td>{search["chapter"]}</td><td>{search["number"]}</td><td>{a}–{b}</td><td>{a+offset}–{b+offset}</td><td>{html.escape(search["status"])}</td></tr>'
        )
    quarantined = [a for a in report.get("anchors", []) if not a["trusted"]]
    anchor_notice = (
        "<p>Quarantined existing anchors (retained unchanged): "
        + html.escape(repr(quarantined))
        + "</p>"
        if quarantined
        else ""
    )
    details = []
    for candidate in report["candidates"]:
        labels = sorted({o["number"] for o in candidate["options"]})
        states = ", ".join(
            f'{s["number"]}: {s["status"]}'
            for s in report["searches"]
            if s["chapter"] == candidate["chapter"] and s["number"] in labels
        )
        crop = ""
        if doc is not None:
            page = doc[candidate["page"] - 1]
            x, y, xx, yy = candidate["bbox"]
            rect = pymupdf.Rect(
                x * page.rect.width,
                y * page.rect.height,
                xx * page.rect.width,
                yy * page.rect.height,
            )
            # Small source crops retain the glyph and adjacent punctuation for review.
            rect += (-8, -4, 8, 4)
            encoded = base64.b64encode(
                page.get_pixmap(dpi=400, clip=rect).tobytes("png")
            ).decode()
            crop = (
                f'<img alt="Source glyph crop" src="data:image/png;base64,{encoded}">'
            )
        details.append(
            f'<details><summary>Chapter {candidate["chapter"]}, PDF {candidate["page"]}: '
            + html.escape(states)
            + "</summary><p>"
            + html.escape(candidate["text"])
            + "</p><p>Local readings: "
            + html.escape(repr(candidate["readings"]))
            + "</p><p>Sequence evidence: "
            + html.escape(
                repr(
                    [
                        d
                        for a in report.get("alignments", [])
                        for d in a["decisions"]
                        if d["position"][:2] == [candidate["page"], candidate["row"]]
                    ]
                )
            )
            + "</p><p>Raw grayscale readings: "
            + html.escape(repr(candidate.get("raw_readings", [])))
            + "</p><p>Corroborating OCR: "
            + html.escape(
                repr([o.get("corroborating_engines", []) for o in candidate["options"]])
            )
            + "</p>"
            + crop
            + "</details>"
        )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Missing citation research</title><style>body{font:16px sans-serif;margin:2em}td,th{padding:.5em;border:1px solid #ccc}table{border-collapse:collapse}</style><h1>Missing citation research</h1><p>Page ranges are search bounds, not confirmed marker locations. Applied recoveries and crop readings are recorded in missing-endnote-research.json.</p><table><tr><th>Chapter</th><th>Number</th><th>PDF pages</th><th>Printed pages</th><th>Search result</th></tr>'
        + "".join(rows)
        + "</table>"
        + anchor_notice
        + "<h2>Machine observations</h2>"
        + "".join(details)
        + "</html>"
    )
