"""Flag geometrically truncated OCR rows before proofreading hides omissions."""

from pathlib import Path
import difflib
import re
from common import read_json
from ocr import merge_rows
from vocabulary import TOKEN


def recover_reference_prefixes(pages, book, work, audit):
    """Restore a dropped short label only from two fresh agreeing engines."""
    if not book.get("recover_reference_prefixes"):
        return
    from common import cache_path, apply_edits

    compact = lambda text: re.sub(r"\s+", "", text)
    for n, rows in pages.items():
        if n not in book.get("vision_primary_pages", []):
            continue  # The two independent witnesses below exclude Vision.
        eligible = [
            r
            for r in rows
            if n in book.get("reference_pages", [])
            or any(
                zone["page"] == n and zone["lo"] <= r["bbox"][1] < zone["hi"]
                for zone in book.get("reference_regions", [])
            )
        ]
        if not eligible:
            continue
        peers = {
            engine: merge_rows(
                read_json(cache_path(work, engine) / f"{n:04}.json")["lines"]
            )
            for engine in ("tesseract", "rapid")
        }
        for row in eligible:
            if not re.match(r"^[—–-]\s*\S", row["text"]):
                continue
            center = sum(row["bbox"][1::2]) / 2
            aligned = (
                lambda peer: peer.get("column", 0) == row.get("column", 0)
                and abs(sum(peer["bbox"][1::2]) / 2 - center) <= 0.008
            )
            # An existing left-hand row is already represented and must not
            # acquire a second copy just because its label and body are split.
            if any(
                other is not row
                and aligned(other)
                and other["bbox"][2] <= row["bbox"][0]
                and re.search(r"\w", other["text"])
                for other in rows
            ):
                continue
            candidates = []
            for peer in peers["tesseract"]:
                label = re.fullmatch(r"([^\W\d_]{2,8})\s+([—–-].+)", peer["text"])
                # Restrict this recovery to compact uppercase reference labels
                # and an unchanged definition, not arbitrary missing prose.
                if (
                    not label
                    or not label[1].isupper()
                    or not aligned(peer)
                    or row["bbox"][0] - peer["bbox"][0] < 0.05
                    or compact(label[2]) != compact(row["text"])
                ):
                    continue
                complete = [
                    p
                    for p in peers["rapid"]
                    if aligned(p)
                    and compact(p["text"]) == compact(label[1] + " " + row["text"])
                    and abs(p["bbox"][0] - peer["bbox"][0]) <= 0.02
                    and abs(p["bbox"][2] - row["bbox"][2]) <= 0.02
                ]
                if len(complete) == 1:
                    candidates.append((label[1], peer, complete[0]))
            if len(candidates) != 1:
                continue
            prefix, peer, independent = candidates[0]
            before_box = list(row["bbox"])
            start = len(audit)
            apply_edits(
                [row],
                [
                    dict(
                        page=n,
                        before=row["text"][0],
                        after=prefix + " " + row["text"][0],
                        whole_word=False,
                        prefix="",  # Insert at the leading dash, preserving inline spans.
                        count=1,
                    )
                ],
                audit,
                "two-engine-reference-prefix",
            )
            if len(audit) == start:
                continue
            row["bbox"][0] = min(peer["bbox"][0], independent["bbox"][0])
            audit[-1].update(
                source_bbox=before_box,
                bbox=row["bbox"],
                tesseract=dict(text=peer["text"], bbox=peer["bbox"]),
                rapid=dict(text=independent["text"], bbox=independent["bbox"]),
            )


def recover_footer_bounds(pages, book, work, audit):
    """Keep a raised footer label from stranding its first line in body prose."""
    if not book.get("recover_footer_bounds"):
        return
    from common import cache_path

    for number, cutoff in list(book.get("note_starts", {}).items()):
        n = int(number)
        rows = pages.get(n, [])
        primary = "vision" if n in book.get("vision_primary_pages", []) else "tesseract"
        peer_engine = "tesseract" if primary == "vision" else "vision"
        peers = merge_rows(
            read_json(cache_path(work, peer_engine) / f"{n:04}.json")["lines"]
        )
        supported = []
        for row in rows:
            box = row["bbox"]
            # A crossing of less than half a typical line can come from a
            # raised label. The majority of this compact box must be in footer.
            if not (
                0 < cutoff - box[1] <= 0.008
                and (box[1] + box[3]) / 2 >= cutoff
                and box[3] - box[1] <= 0.04
            ):
                continue
            own = [w.lower() for w in re.findall(r"[^\W\d_]+", row["text"])]
            anchors = {w for w in own if len(w) >= 4}
            matches = []
            for peer in peers:
                pb = peer["bbox"]
                other = [w.lower() for w in re.findall(r"[^\W\d_]+", peer["text"])]
                if (
                    peer.get("column", 0) == row.get("column", 0)
                    and pb[1] >= cutoff
                    and pb[3] <= box[3] + 0.005
                    and pb[3] - pb[1] <= 0.025
                    and abs(pb[0] - box[0]) <= 0.04
                    and len(anchors & set(other)) >= 3
                    and difflib.SequenceMatcher(None, own, other).ratio() >= 0.9
                ):
                    matches.append(peer)
            if len(matches) == 1:
                supported.append((row, matches[0]))
        if not supported:
            continue
        floor = min(row["bbox"][1] for row, _ in supported)
        # Moving a page boundary must not reclassify an unverified neighbor.
        if any(
            floor <= row["bbox"][1] < cutoff
            and not any(row is own for own, _ in supported)
            for row in rows
        ):
            continue
        book["note_starts"][number] = floor
        audit.append(
            dict(
                page=n,
                kind="footer-boundary-recovery",
                source_cutoff=cutoff,
                effective_cutoff=floor,
                primary_engine=primary,
                peer_engine=peer_engine,
                rows=[
                    dict(text=row["text"], bbox=row["bbox"], peer_bbox=peer["bbox"])
                    for row, peer in supported
                ],
                applied=True,
            )
        )


def truncated_rows(primary, witnesses):
    findings = []
    for row in primary:
        if len(TOKEN.findall(row["text"])) < 3:
            continue
        width = row["bbox"][2] - row["bbox"][0]
        for peer in witnesses:
            if row.get("column", 0) != peer.get("column", 0):
                continue
            if abs(sum(row["bbox"][1::2]) - sum(peer["bbox"][1::2])) / 2 > 0.012:
                continue
            extra = max(
                row["bbox"][0] - peer["bbox"][0], peer["bbox"][2] - row["bbox"][2]
            )
            # Ten percent of page width cannot be a quotation mark or an
            # ordinary OCR-box discrepancy. Require two lexical anchors too.
            own = [w.lower() for w in TOKEN.findall(row["text"])]
            other = [w.lower() for w in TOKEN.findall(peer["text"])]
            anchors = set(w for w in own if len(w) >= 3) & set(other)
            long_anchor = any(
                len(a) >= 6 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8
                for a in own
                for b in other
            )
            if (
                extra > 0.1
                and width < 0.75 * (peer["bbox"][2] - peer["bbox"][0])
                and (
                    len(anchors) >= 2
                    or (
                        long_anchor
                        and width < 0.5 * (peer["bbox"][2] - peer["bbox"][0])
                    )
                )
            ):
                findings.append(
                    dict(
                        context=row["text"],
                        bbox=row["bbox"],
                        witness=peer["text"],
                        witness_bbox=peer["bbox"],
                        reason="partial-source-row",
                        action="review",
                    )
                )
                break
    return findings


def inspect_partial_rows(pages, book, cache):
    findings = []
    for n, rows in pages.items():
        other = merge_rows(read_json(Path(cache) / f"{n:04}.json")["lines"])
        findings.extend(dict(page=n, **r) for r in truncated_rows(rows, other))
    return dict(unresolved=findings)


def restore_reviewed_bounds(pages, report, audit):
    """Expand a reviewed full-row transcription to its observed line extent."""
    import statistics

    for finding in report["unresolved"]:
        rows = pages[finding["page"]]
        row = next((r for r in rows if r["bbox"] == finding["bbox"]), None)
        if (
            not row
            or len(TOKEN.findall(row["text"]))
            < len(TOKEN.findall(finding["context"])) + 2
        ):
            continue
        own = set(TOKEN.findall(row["text"].lower()))
        peer = set(TOKEN.findall(finding["witness"].lower()))
        if len({w for w in own & peer if len(w) >= 3}) < 2:
            continue
        wide = [
            r["bbox"][0]
            for r in rows
            if len(r["text"]) >= 30 and r["bbox"][2] - r["bbox"][0] > 0.65
        ]
        left = min(finding["witness_bbox"][0], statistics.median(wide) if wide else 1)
        old = list(row["bbox"])
        row["bbox"] = [left, old[1], max(old[2], finding["witness_bbox"][2]), old[3]]
        finding["action"] = "reviewed"
        finding["after"] = row["text"]
        finding["restored_bbox"] = row["bbox"]
        audit.append(
            dict(
                page=finding["page"],
                kind="reviewed-row-extent",
                before=old,
                after=row["bbox"],
            )
        )


def gap_candidates(rows, witnesses, cutoff=1):
    """One missing prose row must fit measured pitch between existing rows."""
    import statistics
    from ocr import center, comparable

    body = sorted(
        [
            r
            for r in rows
            if r["bbox"][1] < cutoff and len(TOKEN.findall(r["text"])) >= 5
        ],
        key=center,
    )
    pitches = [
        center(b) - center(a)
        for a, b in zip(body, body[1:])
        if a.get("column", 0) == b.get("column", 0) and 0 < center(b) - center(a) < 0.05
    ]
    # A few independent row advances keep paragraph spacing from becoming
    # the font's estimated pitch on a sparse page.
    if len(pitches) < 4:
        return []
    pitch = statistics.median(pitches)
    found = []
    for left, right in zip(body, body[1:]):
        advance = center(right) - center(left)
        # About two ordinary advances indicate one missing baseline; broad
        # chapter gaps and transitions between columns are not this defect.
        if (
            left.get("column", 0) != right.get("column", 0)
            or not 1.6 * pitch <= advance <= 2.6 * pitch
        ):
            continue
        for peer in witnesses:
            if (
                peer.get("column", 0) != left.get("column", 0)
                or not center(left) + 0.6 * pitch
                < center(peer)
                < center(right) - 0.6 * pitch
            ):
                continue
            if (
                len(TOKEN.findall(peer["text"])) < 8
                or peer["bbox"][2] - peer["bbox"][0] < 0.3
            ):
                continue
            if abs(peer["bbox"][0] - left["bbox"][0]) > 0.04:
                continue
            # Tall primary boxes can overlap a missing row. Lexical coverage,
            # rather than box overlap alone, establishes representation.
            if any(
                abs(center(r) - center(peer)) < pitch
                and difflib.SequenceMatcher(
                    None, comparable(r["text"]), comparable(peer["text"])
                ).ratio()
                >= 0.6
                for r in body
            ):
                continue
            # Horizontal clearance retains omitted leading/trailing glyphs.
            # Keep four-fifths of the two-pitch vertical gap: skewed letters
            # remain visible while neighboring baselines stay outside it.
            rect = [
                max(0, min(left["bbox"][0], peer["bbox"][0]) - 0.015),
                center(left) + 0.2 * pitch,
                min(1, peer["bbox"][2] + 0.015),
                center(right) - 0.2 * pitch,
            ]
            anchors = set(w for w in comparable(peer["text"]) if len(w) >= 4)
            represented = any(
                len(anchors & set(comparable(r["text"]))) >= 3 for r in (left, right)
            )
            found.append(
                dict(
                    left=left,
                    peer=peer,
                    rect=rect,
                    pitch=pitch,
                    represented_maybe=represented,
                )
            )
    return found


def corroborated_gap_text(candidate, cached, previous, dictionary):
    """A lost prefix additionally needs a dictionary-supported boundary join."""
    from ocr import comparable

    own, other = comparable(candidate), comparable(cached)
    if own == other:
        return True
    for omitted in (1, 2):
        if len(own) > omitted and own[omitted:] == other:
            fragment = re.search(r"([^\W\d_]+)-$", previous)
            return bool(
                omitted == 1
                and fragment
                and dictionary
                and dictionary.lookup(fragment[1].lower() + own[0])
            )
    return False


def uncertain_gap_supported(cached, readings, independent):
    """Support whole-line presence without resolving its disputed glyphs."""

    def tokens(text):
        return re.findall(r"[^\W_]+", text.lower())

    own = tokens(cached)
    # Eight words and three long anchors exclude labels and incidental overlap;
    # 97% character agreement limits the evidence to nearly identical lines.
    if len(own) < 8 or len(readings) != 2:
        return False
    anchors = {w for w in own if len(w) >= 4}
    normalized = " ".join(own)

    def supported(text):
        words = tokens(text)
        comparison = difflib.SequenceMatcher(None, normalized, " ".join(words))
        # Two character discrepancies cover an isolated glyph plus a space.
        # This establishes line presence only; the original reading is retained.
        changes = sum(
            max(b - a, d - c)
            for op, a, b, c, d in comparison.get_opcodes()
            if op != "equal"
        )
        return (
            len(anchors & set(words)) >= 3
            and comparison.ratio() >= 0.97
            and changes <= 2
        )

    if not all(supported(text) for text in readings):
        return False
    # A second engine is mandatory; two Tesseract scales are not independent.
    # Its score is a recognition guard, not a correctness probability.
    return any(
        score >= 0.8 and supported(text)
        for text, score in zip(
            independent.get("readings", []), independent.get("scores", [])
        )
    )


def recover_gap_rows(pages, book, cache, recognizer, dictionary, audit):
    import pymupdf

    decisions = []
    for n, rows in pages.items():
        if str(n) in book.get("excluded_pages", {}):
            continue
        witnesses = merge_rows(read_json(Path(cache) / f"{n:04}.json")["lines"])
        for proposal in gap_candidates(
            rows, witnesses, book.get("note_starts", {}).get(str(n), 1)
        ):
            peer = proposal["peer"]
            rect = proposal["rect"]
            row = dict(peer, page=n, bbox=rect)
            page = recognizer.doc[n - 1]
            fresh = recognizer.line_words(page, row, raw=True)
            alternate = recognizer.line_words(page, row, raw=True, dpi=300)
            readings = [fresh["text"], alternate["text"]]
            record = dict(
                page=n,
                bbox=rect,
                context=peer["text"],
                previous=proposal["left"]["text"],
                readings=readings,
                action="review",
                reason="missing-row-witness-conflict",
            )
            decisions.append(record)
            target = None
            evidence = {}
            if readings[0] == readings[1] and corroborated_gap_text(
                readings[0], peer["text"], proposal["left"]["text"], dictionary
            ):
                target = readings[0]
            elif recognizer.models is not None:
                from common import digest

                crop = recognizer.work / (
                    "gap-" + digest(repr((n, rect)).encode()) + ".png"
                )
                crop.parent.mkdir(parents=True, exist_ok=True)
                page.get_pixmap(
                    dpi=600,
                    clip=pymupdf.Rect(
                        rect[0] * page.rect.width,
                        rect[1] * page.rect.height,
                        rect[2] * page.rect.width,
                        rect[3] * page.rect.height,
                    ),
                    colorspace=pymupdf.csGRAY,
                ).save(crop)
                evidence = recognizer.with_fallback(
                    dict(readings=readings), crop, "UNSPECIFIED"
                ).get("alternate_crop", {})
                record["recognition_only"] = evidence
                observed = evidence.get("readings", [])
                # Fixed-scale agreement is an empirical stability guard. A
                # cached witness still corroborates the complete lexical tail.
                if (
                    len(observed) == 2
                    and observed[0] == observed[1]
                    and min(evidence.get("scores", [0])) >= 0.9
                    and corroborated_gap_text(
                        observed[0], peer["text"], proposal["left"]["text"], dictionary
                    )
                ):
                    target = observed[0]
            if proposal.get("represented_maybe"):
                # A badly recognized neighboring row may describe this same
                # physical line. Quarantine it instead of duplicating its text.
                target = None
                record["reason"] = "possibly-represented-by-damaged-neighbor"
            if target:
                row.update(text=target, kind="text", recovered_source_row=True)
                rows.append(row)
                rows.sort(key=lambda r: sum(r["bbox"][1::2]))
                record.update(
                    action="correct", after=target, reason="source-verified-missing-row"
                )
                audit.append(dict(record, kind="recovered-gap-row", applied=True))
            elif (
                book.get("retain_uncertain_gap_rows")
                and not proposal.get("represented_maybe")
                and uncertain_gap_supported(peer["text"], readings, evidence)
            ):
                # Completeness and correction are different decisions: keep
                # the source OCR verbatim and expose the uncertainty to review.
                row.update(
                    text=peer["text"],
                    kind="text",
                    recovered_source_row=True,
                    uncertain_source_row=True,
                )
                rows.append(row)
                rows.sort(key=lambda r: sum(r["bbox"][1::2]))
                record.update(
                    after=peer["text"],
                    retained_for_review=True,
                    reason="source-supported-gap-retained-for-review",
                )
                audit.append(dict(record, kind="recovered-gap-row", applied=True))
    return dict(
        corrected=sum(d["action"] == "correct" for d in decisions),
        decisions=decisions,
        **(
            {
                "retained_for_review": sum(
                    bool(d.get("retained_for_review")) for d in decisions
                )
            }
            if book.get("retain_uncertain_gap_rows")
            else {}
        ),
    )
