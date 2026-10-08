"""Recover confused footnote symbols from aligned OCR and source-font ink."""

import difflib
import re
import statistics
from pathlib import Path

import numpy as np
from scipy.ndimage import distance_transform_edt

from common import apply_edits, cache_path, read_json
from ocr import merge_rows
from scanned_notes import components
from build_resources import page_image

CONFUSED = "®©°º"


def canonical(text):
    # Quotes and spacing vary between OCR engines; lexical content must agree.
    return re.sub(r"\s+", "", text.translate(str.maketrans("“”‘’", "\"\"''")))


def aligned(row, witness):
    a, b = row["bbox"], witness["bbox"]
    return (
        abs((a[1] + a[3] - b[1] - b[3]) / 2) <= 0.008
        and abs(a[0] - b[0]) <= 0.03
        and abs(a[2] - b[2]) <= 0.03
    )


def corroborated_symbol(row, witnesses, replacement):
    """Never reinterpret a trademark without two complete aligned readings."""
    found = []
    for engine, rows in witnesses.items():
        matches = [
            r
            for r in rows
            if aligned(row, r) and canonical(r["text"]) == canonical(replacement)
        ]
        if len(matches) == 1:
            found.append(
                dict(engine=engine, text=matches[0]["text"], bbox=matches[0]["bbox"])
            )
    return found if len(found) >= 2 else None


def prefix_glyph(page, row, recognize):
    lexical = re.search(r"[^\W\d_]+", row["text"])
    if not lexical:
        return None
    observed = recognize.line_words(page, row, raw=True)
    words = [w for w in observed["words"] if w[4].lower() == lexical[0].lower()]
    if not words:
        return None
    image = page_image(page)
    left = max(0, int(row["bbox"][0] * image.width) - 3)
    top = max(0, int(row["bbox"][1] * image.height) - 8)
    right = int(words[0][0] / page.rect.width * image.width)
    bottom = min(image.height, int(row["bbox"][3] * image.height) + 3)
    if right <= left:
        return None
    crop = image.crop((left, top, right, bottom))
    boxes = components(crop)
    if not boxes:
        return None
    # The isolated prefix is left of quotes/ellipsis and the opening word.
    box = min(boxes, key=lambda b: b[0])
    bbox = [
        (box[0] + left) / image.width,
        (box[1] + top) / image.height,
        (box[2] + left) / image.width,
        (box[3] + top) / image.height,
    ]
    return dict(glyph=crop.crop(box), bbox=bbox)


def match_symbol_font(glyph, templates):
    shape = np.asarray(glyph.resize((24, 24))) < 150
    if not shape.any():
        return None
    witnesses = []
    for template in templates:
        other = template["glyph"]
        # Physical size prevents a short quote stroke being scaled into a star.
        if not (
            0.8 <= glyph.height / other.height <= 1.25
            and 0.8 <= glyph.width / other.width <= 1.25
        ):
            continue
        mask = np.asarray(other.resize((24, 24))) < 150
        if not mask.any():
            continue
        score = (
            distance_transform_edt(~mask)[shape].mean()
            + distance_transform_edt(~shape)[mask].mean()
        ) / 48
        # Mean symmetric distance under 0.36 pixel at the 24-pixel canonical size.
        if score <= 0.015:
            witnesses.append(
                dict(
                    page=template["page"],
                    bbox=template["bbox"],
                    score=round(float(score), 6),
                    ocr_witnesses=template["witnesses"],
                )
            )
    return witnesses if len({w["page"] for w in witnesses}) >= 2 else None


LEXICAL = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)


def punctuation_gaps(text):
    words = list(LEXICAL.finditer(text))
    edges = [0] + [w.end() for w in words]
    ends = [w.start() for w in words] + [len(text)]
    return words, [(a, b) for a, b in zip(edges, ends)]


def disagreement_candidates(row, witnesses, symbol="*", footer=False):
    """Discover a tiny disputed gap while every lexical anchor remains intact."""
    text = row["text"]
    words, gaps = punctuation_gaps(text)
    if len(words) < 4:  # Enough anchors to reject a nearby short caption/line.
        return []
    readings = []
    for engine, rows in witnesses.items():
        matches = []
        for witness in rows:
            other_words, other_gaps = punctuation_gaps(witness["text"])
            if aligned(row, witness) and [w[0] for w in words] == [
                w[0] for w in other_words
            ]:
                matches.append((witness, other_gaps))
        if len(matches) == 1:
            readings.append((engine, *matches[0]))
    if len(readings) < 2:
        return []
    candidates = {}
    for index, (a, b) in enumerate(gaps):
        if footer != (index == 0):
            continue
        if index and words[index - 1][0].isdigit():
            continue  # Numeric expressions belong to the separate digit recovery.
        if index < len(words) and words[index][0].isdigit():
            continue
        positions = [a + i for i, c in enumerate(text[a:b]) if not c.isspace()]
        compact = "".join(text[i] for i in positions)
        if symbol in compact or len(compact) > 6:
            continue  # Already-present markers and large edits need no inference.
        for engine, witness, other_gaps in readings:
            oa, ob = other_gaps[index]
            alternative = re.sub(r"\s+", "", witness["text"][oa:ob])
            if len(alternative) > 6:
                continue
            changes = [
                v
                for v in difflib.SequenceMatcher(
                    None, compact, alternative, autojunk=False
                ).get_opcodes()
                if v[0] != "equal"
            ]
            if len(changes) != 1:
                continue
            _, start, end, os, oe = changes[0]
            # At most two disputed punctuation glyphs; no letters/digits are
            # discarded. Omission counts as disagreement, never as scan proof.
            if (
                end - start > 2
                or oe - os > 2
                or any(
                    c.isalnum() or c == "_"
                    for c in compact[start:end] + alternative[os:oe]
                )
            ):
                continue
            left = (
                positions[start]
                if start < len(positions)
                else (positions[-1] + 1 if positions else a)
            )
            right = positions[end - 1] + 1 if end > start else left
            key = (left, right, index)
            candidates.setdefault(
                key,
                dict(
                    start=left,
                    end=right,
                    gap=index,
                    witnesses=[
                        dict(engine=e, text=r["text"], bbox=r["bbox"])
                        for e, r, _ in readings
                    ],
                    disagreements=[],
                ),
            )["disagreements"].append(engine)
    return list(candidates.values())


def gap_glyphs(page, row, index, recognize, candidate=None):
    """Locate a disputed punctuation gap using fresh character OCR anchors."""
    observed = recognize.line_words(page, row, raw=True)
    words, _ = punctuation_gaps(row["text"])
    fresh, fresh_gaps = punctuation_gaps(observed["text"])
    if [w[0] for w in fresh] != [w[0] for w in words]:
        return []
    chars = observed["characters"]

    def boxes(word):
        return [
            c["bbox"]
            for c in chars
            if c["start"] < word.end() and c["end"] > word.start()
        ]

    before = boxes(fresh[index - 1]) if index else []
    after = boxes(fresh[index]) if index < len(fresh) else []
    if (index and not before) or (index < len(fresh) and not after):
        return []
    if candidate is not None:
        _, original_gaps = punctuation_gaps(row["text"])
        a, b = original_gaps[index]
        prefix = canonical(row["text"][a : candidate["start"]])
        suffix = canonical(row["text"][candidate["end"] : b])
        fa, fb = fresh_gaps[index]
        positions = [i for i in range(fa, fb) if not observed["text"][i].isspace()]
        actual = canonical(observed["text"][fa:fb])
        if (
            not actual.startswith(prefix)
            or not actual.endswith(suffix)
            or len(actual) < len(prefix) + len(suffix)
        ):
            return []
        # Keep unchanged punctuation outside the pixel search. Otherwise a real
        # star next to a disputed quote could wrongly authorize deleting it.
        if prefix:
            before = [
                c["bbox"]
                for c in chars
                if c["start"] <= positions[len(prefix) - 1] < c["end"]
            ]
        if suffix:
            after = [
                c["bbox"]
                for c in chars
                if c["start"] <= positions[len(actual) - len(suffix)] < c["end"]
            ]
        if (prefix and not before) or (suffix and not after):
            return []
    image = page_image(page)
    left = max(b[2] for b in before) / page.rect.width if before else row["bbox"][0]
    right = (
        min(b[0] for b in after) / page.rect.width if after else row["bbox"][2] + 0.006
    )
    # Two raster pixels keep serifs; vertical clearance retains raised markers.
    x0, x1 = max(0, int(left * image.width) - 2), min(
        image.width, int(right * image.width) + 2
    )
    y0, y1 = max(0, int(row["bbox"][1] * image.height) - 8), min(
        image.height, int(row["bbox"][3] * image.height) + 3
    )
    if x1 <= x0 or y1 <= y0:
        return []
    crop = image.crop((x0, y0, x1, y1))
    result = []
    letters = [
        c["bbox"] for c in chars if observed["text"][c["start"] : c["end"]].isalpha()
    ]
    if index and not letters:
        return []
    # The 90th percentile estimates capital height, as in prose_baseline;
    # median x-height would reject legitimate stars above short lowercase text.
    cap = float(np.percentile([b[3] - b[1] for b in letters], 90)) if letters else None
    baseline = statistics.median(b[3] for b in letters) if letters else None
    for box in components(crop):
        bbox = [
            (box[0] + x0) / image.width,
            (box[1] + y0) / image.height,
            (box[2] + x0) / image.width,
            (box[3] + y0) / image.height,
        ]
        if index:
            top, bottom = bbox[1] * page.rect.height, bbox[3] * page.rect.height
            # Inline stars are smaller and raised. A modest 12% rise accepts
            # slightly raised type; the upper bound excludes neighboring lines.
            if (
                cap <= 0
                or bottom - top > 0.9 * cap
                or bottom > baseline - 0.12 * cap
                or top < baseline - 1.4 * cap
            ):
                continue
        result.append(dict(glyph=crop.crop(box), bbox=bbox))
    return result


def recover_disputed_marker(page, row, candidate, templates, recognize, number):
    evidence = []
    for item in gap_glyphs(page, row, candidate["gap"], recognize, candidate):
        matches = match_symbol_font(
            item["glyph"], [t for t in templates if t["page"] != number]
        )
        if matches:
            evidence.append(dict(bbox=item["bbox"], font_witnesses=matches))
    return evidence[0] if len(evidence) == 1 else None


def repair_symbol_markers(pages, doc, book, work, audit, recognize):
    banks = {}
    footers = {}
    templates = []
    for key, cutoff in book.get("note_starts", {}).items():
        if key in book.get("note_continuations", {}):
            continue
        number = int(key)
        notes = [r for r in pages[number] if r["bbox"][1] >= cutoff]
        if not notes:
            continue
        first = min(notes, key=lambda r: r["bbox"][1])
        footers[number] = first
        banks[number] = {
            engine: merge_rows(
                read_json(cache_path(work, engine) / f"{number:04}.json")["lines"]
            )
            for engine in ("vision", "rapid")
            if (Path(work) / (engine + "-cache.txt")).exists()
        }
        # Templates need literal agreement by two independent cached engines;
        # inferred stars never become further templates.
        if re.match(r"^\s*\*(?!\*)", first["text"]):
            witnesses = corroborated_symbol(first, banks[number], first["text"])
            if witnesses:
                evidence = prefix_glyph(doc[number - 1], first, recognize)
                if evidence:
                    templates.append(dict(page=number, witnesses=witnesses, **evidence))
    for number, first in footers.items():
        if first["text"][:1] in CONFUSED:
            evidence = prefix_glyph(doc[number - 1], first, recognize)
            matching = evidence and match_symbol_font(
                evidence["glyph"], [t for t in templates if t["page"] != number]
            )
            if matching:
                apply_edits(
                    [first],
                    [
                        dict(
                            page=number,
                            before=first["text"][0],
                            after="*",
                            prefix="",
                            suffix=first["text"][1:],
                            whole_word=False,
                        )
                    ],
                    audit,
                    "source-font-footnote-symbol",
                )
                audit.append(
                    dict(
                        page=number,
                        kind="footnote-symbol-font-evidence",
                        bbox=evidence["bbox"],
                        witnesses=matching,
                    )
                )
        if not re.match(r"^\s*\*(?!\*)", first["text"]):
            proposals = []
            for candidate in disagreement_candidates(first, banks[number], footer=True):
                evidence = recover_disputed_marker(
                    doc[number - 1], first, candidate, templates, recognize, number
                )
                audit.append(
                    dict(
                        page=number,
                        kind="disputed-footer-symbol-candidate",
                        row_bbox=first["bbox"],
                        **candidate,
                        evidence=evidence,
                        status="verified" if evidence else "abstained",
                    )
                )
                if evidence:
                    proposals.append((candidate, evidence))
            if len(proposals) == 1:
                candidate, evidence = proposals[0]
                a, b = candidate["start"], candidate["end"]
                apply_edits(
                    [first],
                    [
                        dict(
                            page=number,
                            before=first["text"][a:b],
                            after="*",
                            prefix=first["text"][:a],
                            suffix=first["text"][b:],
                            whole_word=False,
                            witnesses=candidate["witnesses"],
                            **evidence,
                        )
                    ],
                    audit,
                    "source-font-disputed-footer-symbol",
                )
        if not re.match(r"^\s*\*(?!\*)", first["text"]):
            continue
        cutoff = book["note_starts"][str(number)]
        for row in pages[number]:
            if row["bbox"][1] >= cutoff or row.get("kind", "text") != "text":
                continue
            for match in list(re.finditer("[" + CONFUSED + "]", row["text"]))[::-1]:
                start = match.start()
                replacement = row["text"][:start] + "*" + row["text"][start + 1 :]
                witnesses = corroborated_symbol(row, banks[number], replacement)
                if witnesses:
                    apply_edits(
                        [row],
                        [
                            dict(
                                page=number,
                                before=match[0],
                                after="*",
                                prefix=row["text"][:start],
                                suffix=row["text"][start + 1 :],
                                whole_word=False,
                                witnesses=witnesses,
                            )
                        ],
                        audit,
                        "corroborated-body-footnote-symbol",
                    )

    # Search only single-entry pages with no literal body star. Existing calls
    # and multi-entry page groups are left to the apparatus linker/review.
    for number, first in footers.items():
        cutoff = book["note_starts"][str(number)]
        notes = [r for r in pages[number] if r["bbox"][1] >= cutoff]
        body = [
            r
            for r in pages[number]
            if r["bbox"][1] < cutoff
            and r.get("kind", "text") in ("text", "quote", "verse")
        ]
        if sum(r["text"].count("*") for r in notes) != 1 or not re.match(
            r"^\s*\*(?!\*)", first["text"]
        ):
            continue
        if any("*" in r["text"] for r in body):
            continue
        proposals = []
        for row in body:
            for candidate in disagreement_candidates(row, banks[number]):
                evidence = recover_disputed_marker(
                    doc[number - 1], row, candidate, templates, recognize, number
                )
                audit.append(
                    dict(
                        page=number,
                        kind="disputed-footnote-symbol-candidate",
                        row_bbox=row["bbox"],
                        **candidate,
                        evidence=evidence,
                        status="verified" if evidence else "abstained",
                    )
                )
                if evidence:
                    proposals.append((row, candidate, evidence))
        # More than one supported location is ambiguous; do not pick by score.
        if len(proposals) != 1:
            continue
        row, candidate, evidence = proposals[0]
        a, b = candidate["start"], candidate["end"]
        apply_edits(
            [row],
            [
                dict(
                    page=number,
                    before=row["text"][a:b],
                    after="*",
                    prefix=row["text"][:a],
                    suffix=row["text"][b:],
                    whole_word=False,
                    witnesses=candidate["witnesses"],
                    **evidence,
                )
            ],
            audit,
            "source-font-disputed-footnote-symbol",
        )
