"""Repair Cyrillic OCR case from aligned source witnesses, preserving brands."""

import difflib
import re
from pathlib import Path
from common import apply_edits, read_json
from book_model import row_id
from vocabulary import TOKEN
from span_repair import distance
from ocr import nearest, merge_rows


def aligned_word(text, witness, match):
    own = list(TOKEN.finditer(text))
    other = list(TOKEN.finditer(witness))
    index = next(i for i, m in enumerate(own) if m.start() == match.start())
    found = []
    for j, word in enumerate(other):
        if (
            difflib.SequenceMatcher(
                None, match.group().lower(), word.group().lower()
            ).ratio()
            < 0.8
        ):
            continue
        anchors = sum(
            own[index + d].group().lower() == other[j + d].group().lower()
            for d in (-2, -1, 1, 2)
            if 0 <= index + d < len(own) and 0 <= j + d < len(other)
        )
        if anchors >= 2:
            found.append((anchors, word.group()))
    found.sort(reverse=True)
    return (
        found[0][1]
        if found and (len(found) == 1 or found[0][0] > found[1][0])
        else None
    )


def repair_case_words(
    model, pages, book, dictionary, recognize, cache, audit, protected=()
):
    rows = {
        r.get("row_id", row_id(int(n), r, book["source_sha256"])): r
        for n, rr in pages.items()
        for r in rr
    }
    protected = {w.lower() for w in protected}
    observations = {}
    decisions = []
    for entry in model:
        for block in entry["blocks"] + entry["notes"]:
            if block["kind"] not in ("text", "quote", "note"):
                continue
            for source in block["sources"]:
                row = rows[source["row_id"]]
                text = row["text"]
                edits = []
                for match in TOKEN.finditer(text):
                    word = match.group()
                    # Cyrillic uppercase suffixes after lowercase letters are
                    # unlike Latin brands, acronyms and capitalized proper names.
                    mixed = word[0].islower() and any(c.isupper() for c in word)
                    interior_title = (
                        word.istitle()
                        and match.start() > 0
                        and re.search(r"\w\s+$", text[: match.start()])
                    )
                    if (
                        len(word) < 4
                        or word.lower() in protected
                        or not re.fullmatch(r"[А-Яа-яЁё]+", word)
                        or not (mixed or interior_title)
                        or (
                            interior_title
                            and not mixed
                            and not dictionary.lookup(word.lower())
                        )
                    ):
                        continue
                    n = row["page"]
                    if n not in observations:
                        observations[n] = merge_rows(
                            read_json(Path(cache) / f"{n:04}.json")["lines"]
                        )
                    peers = [
                        p
                        for p in observations[n]
                        if abs(
                            (
                                p["bbox"][1]
                                + p["bbox"][3]
                                - row["bbox"][1]
                                - row["bbox"][3]
                            )
                            / 2
                        )
                        < 0.04
                    ]
                    # Overlapping primary boxes can point nearest() at an
                    # adjacent line. Lexical anchors identify the actual row.
                    peer_values = [aligned_word(text, p["text"], match) for p in peers]
                    peer_values = [v for v in peer_values if v is not None]
                    peer_value = peer_values[0] if len(set(peer_values)) == 1 else None
                    fresh = recognize.line_words(recognize.doc[n - 1], row, raw=True)
                    fresh_value = aligned_word(text, fresh["text"], match)
                    values = [peer_value, fresh_value]
                    if peer_value is None and fresh_value and fresh_value.islower():
                        # Whole-page OCR sometimes loses the entire row. Two
                        # fixed crop scales test a literal, anchored reading;
                        # this is stability evidence from one engine.
                        alternate = recognize.line_words(
                            recognize.doc[n - 1], row, raw=True, dpi=300
                        )
                        values[0] = aligned_word(text, alternate["text"], match)
                    record = dict(
                        page=n,
                        row_id=source["row_id"],
                        before=word,
                        bbox=row["bbox"],
                        readings=values,
                        action="review",
                        reason="case-witness-conflict",
                    )
                    decisions.append(record)
                    if (
                        None in values
                        or values[0] != values[1]
                        or not values[0].islower()
                        or not dictionary.lookup(values[0])
                    ):
                        continue
                    target = values[0]
                    # Case changes can expose one lost OCR stroke; require both
                    # aligned literal witnesses and dictionary acceptance for it.
                    if distance(word.lower(), target) > 1:
                        continue
                    edits.append(
                        dict(
                            page=n,
                            before=word,
                            after=target,
                            prefix=text[: match.start()],
                            suffix=text[match.end() :],
                            count=1,
                            whole_word=True,
                        )
                    )
                    record.update(
                        action="correct", after=target, reason="aligned-source-case"
                    )
                # Scope positions against the frozen row and apply from the
                # right so a repaired earlier word cannot shift later overlays.
                for edit in reversed(edits):
                    # Prefix/suffix constraints are strict; recompute suffix only
                    # from the unchanged lexical anchor after earlier right edits.
                    start = len(edit["prefix"])
                    edit["suffix"] = row["text"][start + len(edit["before"]) :]
                    apply_edits([row], [edit], audit, "source-case-repair")
    return dict(
        corrected=sum(d["action"] == "correct" for d in decisions), decisions=decisions
    )
