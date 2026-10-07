"""Source-verified short-token and physical-line word repairs.

Candidates come from assembled context; edits remain scoped to exact OCR rows.
Never join across paragraphs, columns, page turns or protected compounds.
"""

import difflib
import re
from pathlib import Path

from PIL import Image
from wordfreq import zipf_frequency

from book_model import row_id
from common import apply_edits, digest
from vocabulary import TOKEN

LETTER = r"[^\W\d_]"
LEFT = re.compile(rf"({LETTER}+)([-.·•\s]*)$")
RIGHT = re.compile(rf"^([•·\s]*)({LETTER}+)")
# Visually equivalent Latin/Cyrillic letters supply candidates, never edits.
HOMOGLYPHS = str.maketrans("aceopxyACEOPXY", "асеорхyАСЕОРХУ".replace("y", "у"))


def mixed_script(word):
    return bool(re.search(r"[A-Za-z]", word) and re.search(r"[А-Яа-яЁё]", word))


def distance(a, b):
    # Sequence alignment is enough to enumerate one-edit split positions;
    # the actual candidate edit distance comes from SymSpell.
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes()
        if tag != "equal"
    )


def targets(ranker, word, context, offset, short=False):
    folded = word.translate(HOMOGLYPHS) if ranker.language == "ru" else word
    choices = ranker.rank(folded, context, offset, include_known=True, max_distance=1)
    result = []
    for choice in choices:
        term = choice["term"]
        protected = term in ranker.protected
        exact = choice["distance"] == 0
        if protected and not exact:
            continue  # Protection licenses exact joins, not guessed rare names.
        if not (
            protected
            or ranker.dictionary.lookup(term)
            or (
                choice["book_count"] >= 3 and zipf_frequency(term, ranker.language) >= 2
            )
        ):
            continue
        # Exact seam joins preserve all letters; common dictionary forms do
        # not need recurrence. Letter repairs still need two book observations.
        if not protected and (
            zipf_frequency(term, ranker.language) < (4 if short else 2)
            or (not exact and choice["book_count"] < 2)
            or (
                exact
                and choice["book_count"] < 2
                and zipf_frequency(term, ranker.language) < 3
            )
        ):
            continue
        result.append(choice)
    return result


def corroborates(evidence, target, original=None):
    readings = evidence.get("readings", [])

    def observed(text):
        tokens = re.findall(r"[^\W_]+", text.lower())
        if len(tokens) != 1:
            return None
        value = tokens[0]
        if re.fullmatch(r"[а-яё]+", target.lower()):
            # Scanned serif б/6, о/0 and з/3 share glyph shapes. This
            # comparison does not invent extra letters or normalize prose.
            value = value.translate(HOMOGLYPHS).translate(str.maketrans("603", "боз"))
        return value

    if len(readings) == 2 and all(observed(s) == target.lower() for s in readings):
        return True
    alternate = evidence.get("alternate_crop", {})
    # Exact retained source letters need no spelling substitution. A stable
    # comparator reading can corroborate that fragment at a lower confidence;
    # changed letters continue to require the strict recognition threshold.
    return (
        original is not None
        and original.lower() == target.lower()
        and len(alternate.get("readings", [])) == 2
        and min(alternate.get("scores", [0])) >= 0.75
        and all(observed(s) == target.lower() for s in alternate["readings"])
    )


def seam_candidates(block, ranker):
    sources = block["sources"]
    for a, b in zip(sources, sources[1:]):
        if a["page"] != b["page"] or a.get("column", 0) != b.get("column", 0):
            continue
        height = max(a["bbox"][3] - a["bbox"][1], b["bbox"][3] - b["bbox"][1])
        # Adjacent source lines can overlap OCR boxes, but must advance and
        # remain within ordinary line pitch, not a separated inset or caption.
        advance = (b["bbox"][1] + b["bbox"][3] - a["bbox"][1] - a["bbox"][3]) / 2
        if not 0 < advance <= 2.5 * height:
            continue
        left = LEFT.search(block["text"][a["start"] : a["end"]])
        right = RIGHT.match(block["text"][b["start"] : b["end"]])
        original_left = LEFT.search(a["text"])
        if not left or not right or not original_left:
            continue
        lo = a["start"] + left.start(1)
        hi = b["start"] + right.end(2)
        first, second = left[1], right[2]
        literal = block["text"][lo:hi]
        mixed = mixed_script(first + second)
        # A lowercase prefix with an all-caps continuation is suspicious OCR
        # case, unlike ordinary title case, all-caps words or branded camelCase.
        case_mismatch = (
            len(first) >= 3
            and first.islower()
            and len(second) >= 2
            and second.isupper()
        )
        if literal == first + second and not (mixed or case_mismatch):
            continue  # Already reconstructed correctly.
        if first.lower() in ranker.protected or second.lower() in ranker.protected:
            continue
        compound = (first + "-" + second).lower()
        if compound in ranker.protected or (
            (first + second).lower() not in ranker.protected
            and ranker.dictionary.lookup(compound)
            and (ranker.counts[compound] >= 2)
        ):
            # Hunspell can accept arbitrary pairs around a hyphen. Require
            # corpus/language evidence before treating one as a real compound.
            continue
        suffix = original_left[2].strip()
        if (
            "." in suffix
            and ranker.dictionary.lookup(first.lower())
            and (first + second).lower() not in ranker.protected
        ):
            # A wrap fragment can itself be a valid inflection. Do not let
            # that hide an exact, repeated dictionary word whose continuation
            # is invalid alone. Both physical fragments still require OCR proof.
            exact_wrap = (
                not ranker.known(second)
                and ranker.dictionary.lookup((first + second).lower())
                and ranker.counts[(first + second).lower()] >= 2
            )
            if not exact_wrap:
                continue  # Ordinary sentence punctuation after an accepted word.
        if not suffix and (
            ranker.known(second)
            or len(first) > 3
            or ranker.known(first)
            and len(first) > 1
        ):
            continue  # Never fuse ordinary accepted adjacent words.
        # A truncated final fragment belongs near the local right edge; its
        # continuation begins at the local left edge, including wrapped prose.
        # Wrapped prose can use a narrow measure beside an illustration;
        # estimate margins from nearby rows in this page and column only.
        local = [
            s
            for s in sources
            if s["page"] == a["page"]
            and s.get("column", 0) == a.get("column", 0)
            and abs(s["bbox"][1] - a["bbox"][1]) <= 3 * height
        ]
        margin_left = min(s["bbox"][0] for s in local)
        margin_right = max(s["bbox"][2] for s in local)
        if a["bbox"][2] < margin_right - 0.05 or b["bbox"][0] > margin_left + 0.04:
            continue
        combined = first + second
        context = block["text"][:lo] + combined + block["text"][hi:]
        yield dict(
            left=a,
            right=b,
            first=first,
            second=second,
            before=literal,
            context=context,
            offset=lo,
            combined=combined,
            mixed=mixed,
            case_mismatch=case_mismatch,
        )


def composite_crop(recognize, parts, target):
    root = Path(recognize.work)
    images = [
        Image.open(root / (p["cache_key"] + ".png")).convert("RGB") for p in parts
    ]
    # Keep both literal source fragments and a visible gutter for audit; this
    # composite is never fed back into OCR as synthetic recognition evidence.
    canvas = Image.new(
        "RGB",
        (sum(i.width for i in images) + 12, max(i.height for i in images)),
        "white",
    )
    x = 0
    for image in images:
        canvas.paste(image, (x, 0))
        x += image.width + 12
    key = digest(
        ("fragment-crop-v1" + "".join(p["crop_sha256"] for p in parts)).encode()
    )
    path = root / (key + ".png")
    canvas.save(path)
    return dict(
        readings=[target, target],
        engine="verified-fragment-crops",
        fragments=parts,
        cache_key=key,
        crop_sha256=digest(path.read_bytes()),
    )


def repair_spans(model, pages, book, ranker, recognize, audit):
    rows = {
        r.get("row_id", row_id(int(n), r, book.get("source_sha256", ""))): r
        for n, rr in pages.items()
        for r in rr
    }
    decisions, edits, claimed = [], [], set()
    recognize_fragment = getattr(recognize, "fragment", recognize)
    for entry in model:
        if ranker.excluded(entry, book):
            continue
        for block in entry["blocks"] + entry["notes"]:
            if block["kind"] not in ("text", "quote", "note"):
                continue
            for seam in seam_candidates(block, ranker):
                ids = [seam[k]["row_id"] for k in ("left", "right")]
                if any(i in claimed for i in ids):
                    continue
                choices = targets(
                    ranker, seam["combined"], seam["context"], seam["offset"]
                )
                if not choices:
                    continue
                exact = next((c for c in choices if c["distance"] == 0), None)
                # Repair a broken wrap before considering spelling changes.
                # The language model can prefer a neighboring inflection, but
                # that must not veto an intact, dictionary-valid source join.
                if exact:
                    choices = [exact] + [c for c in choices if c is not exact]
                choice = choices[0]
                target = choice["term"]
                # Preserve ordinary title case. A mixed-script glyph's case is
                # unreliable; the verified Cyrillic reading supplies the letters.
                if seam["first"].istitle() and not seam["mixed"]:
                    target = target.capitalize()
                decision = dict(
                    page=seam["left"]["page"],
                    row_id=ids[0],
                    row_ids=ids,
                    before=seam["before"],
                    after=target,
                    bbox=seam["left"]["bbox"],
                    candidates=choices[:5],
                    action="review",
                    kind="line-seam",
                    margin=None,
                )
                decisions.append(decision)
                if (
                    not exact
                    and len(choices) > 1
                    and choices[0]["context_score"] - choices[1]["context_score"] < 1
                ):
                    decision["reason"] = "ambiguous-seam-context"
                    continue
                verified = None
                attempts = []
                decision["fragment_attempts"] = attempts
                for cut in range(1, len(target)):
                    first, second = target[:cut], target[cut:]
                    folded_first = (
                        seam["first"].translate(HOMOGLYPHS)
                        if ranker.language == "ru"
                        else seam["first"]
                    )
                    if (
                        distance(folded_first.lower(), first.lower())
                        + distance(seam["second"].lower(), second.lower())
                        > 1
                    ):
                        continue
                    left = recognize_fragment(rows[ids[0]], seam["first"], first)
                    right = recognize_fragment(rows[ids[1]], seam["second"], second)
                    attempts.append(dict(targets=[first, second], crops=[left, right]))
                    case_witness = None
                    if seam.get("case_mismatch") and hasattr(recognize, "line_words"):
                        observation = recognize.line_words(
                            recognize.doc[seam["right"]["page"] - 1],
                            rows[ids[1]],
                            raw=True,
                        )
                        observed = TOKEN.findall(observation["text"])
                        original = TOKEN.findall(rows[ids[1]]["text"])
                        # Case-only repair requires an exact lowercase fresh
                        # prefix and two following lexical anchors. No letters
                        # are inferred from context or from an OCR confidence.
                        if (
                            observed
                            and observed[0] == second
                            and len(observed) >= 3
                            and [w.lower() for w in observed[1:3]]
                            == [w.lower() for w in original[1:3]]
                        ):
                            case_witness = observation["text"]
                            right["case_witness"] = case_witness
                    case_only = (
                        case_witness
                        and first == seam["first"]
                        and second == seam["second"].lower()
                    )
                    if case_only or (
                        corroborates(left, first, seam["first"])
                        and (
                            corroborates(right, second, seam["second"]) or case_witness
                        )
                    ):
                        verified = first, second, [left, right]
                        break
                if (
                    verified
                    and seam.get("case_mismatch")
                    and not verified[2][1].get("case_witness")
                    and not all(
                        TOKEN.findall(reading) == [verified[1]]
                        for reading in verified[2][1].get("readings", [])
                    )
                ):
                    verified = None  # Letter agreement alone cannot establish case.
                if not verified:
                    decision["reason"] = "fragment-crops-do-not-corroborate"
                    continue
                first, second, parts = verified
                decision["crop"] = composite_crop(recognize, parts, target)
                decision.update(
                    action="correct", reason="context-and-two-source-fragments"
                )
                for ident, old, new, terminal in [
                    (ids[0], seam["first"], first, True),
                    (ids[1], seam["second"], second, False),
                ]:
                    row = rows[ident]
                    pattern = (
                        LEFT.search(row["text"])
                        if terminal
                        else RIGHT.match(row["text"])
                    )
                    old_text = pattern.group()  # Includes only seam noise.
                    replacement = new
                    # Preserve the space before the left fragment; its regex
                    # starts at the word, whereas RIGHT includes leading noise.
                    edits.append(
                        (
                            row,
                            dict(
                                page=row["page"],
                                before=old_text,
                                after=replacement,
                                count=1,
                                whole_word=False,
                                prefix=row["text"][: pattern.start()],
                                suffix=row["text"][pattern.end() :],
                                evidence=decision,
                            ),
                        )
                    )
                rows[ids[1]]["word_continuation_from"] = ids[0]
                claimed.update(ids)
            for match in TOKEN.finditer(block["text"]):
                word = match.group()
                # A colon at a short token's physical line end can be a damaged
                # final letter. Do not revise accepted short words generally.
                if not 1 <= len(word) <= 3 or word.lower() in ranker.protected:
                    continue
                source = next(
                    (
                        s
                        for s in block["sources"]
                        if s["start"] <= match.start()
                        and match.end() < s["end"]
                        and block["text"][match.end() : s["end"]].strip() == ":"
                    ),
                    None,
                )
                if not source or source["row_id"] in claimed:
                    continue
                choices = targets(
                    ranker, word, block["text"], match.start(), short=True
                )
                baseline = next(
                    (c["context_score"] for c in choices if c["term"] == word.lower()),
                    None,
                )
                # One-letter dictionary entries also represent initials. A
                # damaged trailing colon is not evidence that such an entry
                # is a real competing word in prose.
                if len(word) == 1:
                    baseline = None
                alternatives = [c for c in choices if c["term"] != word.lower()]
                if len(word) == 1:
                    # The trailing blob stands for a lost final letter, not a
                    # license to replace the retained initial with another word.
                    alternatives = [
                        c
                        for c in alternatives
                        if len(c["term"]) == 2 and c["term"].startswith(word.lower())
                    ]
                if not alternatives:
                    continue
                row = rows[source["row_id"]]
                for best in alternatives[:3]:
                    # A 20-fold score advantage bounds real-word correction;
                    # scores rank hypotheses, not calibrated probabilities.
                    if baseline is not None and best["context_score"] < baseline + 3:
                        continue
                    target = (
                        best["term"].capitalize() if word.istitle() else best["term"]
                    )
                    decision = dict(
                        page=row["page"],
                        row_id=source["row_id"],
                        before=word + ":",
                        after=target,
                        bbox=row["bbox"],
                        candidates=alternatives[:5],
                        margin=None,
                        action="review",
                        kind="short-damaged-token",
                    )
                    decisions.append(decision)
                    crop = recognize(row, word, target)
                    decision["crop"] = crop
                    if not corroborates(crop, target):
                        decision["reason"] = "crop-does-not-corroborate"
                        continue
                    decision.update(
                        action="correct", reason="context-and-damaged-short-token-crop"
                    )
                    edits.append(
                        (
                            row,
                            dict(
                                page=row["page"],
                                before=word + ":",
                                after=target,
                                count=1,
                                whole_word=True,
                                evidence=decision,
                            ),
                        )
                    )
                    claimed.add(source["row_id"])
                    break
    for row, edit in edits:
        apply_edits([row], [edit], audit, "whole-book-span-repair")
    return dict(
        corrected=sum(d["action"] == "correct" for d in decisions),
        decisions=decisions,
        corrected_rows=sorted(claimed),
    )
