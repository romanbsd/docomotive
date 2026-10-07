"""Spelling corrections for a corrected reading edition, with source-origin audit.

Detection, semantic acceptance and source classification are independent. No
candidate is applied without exact row placement and an explicit judgment.
"""

import math
import re
from collections import Counter

from common import apply_edits
from repair_types import RepairAction, RepairReason, SourceOrigin, CorrectionClass
from vocabulary import TOKEN
from wordfreq import zipf_frequency


def spelling_case(original, candidate):
    return (
        candidate.upper()
        if original.isupper()
        else candidate.capitalize() if original.istitle() else candidate
    )


def comparison_score(ranker, original, candidate, context, offset, distance=0):
    """Compare candidates after removing this occurrence's self-training evidence."""
    low, term = original.lower(), candidate.lower()
    matches = list(TOKEN.finditer(context))
    i = next(i for i, m in enumerate(matches) if m.start() == offset)
    left = (
        matches[i - 1].group().lower()
        if i and not re.search(r"[.!?;]", context[matches[i - 1].end() : offset])
        else None
    )
    right = (
        matches[i + 1].group().lower()
        if i + 1 < len(matches)
        and not re.search(r"[.!?;]", context[matches[i].end() : matches[i + 1].start()])
        else None
    )
    held_pairs = Counter(
        pair for pair in [(left, low), (low, right)] if None not in pair
    )
    held_outgoing = Counter(a for a, b in held_pairs.elements())
    frequency = ranker.sym.words.get(
        term, 10 ** (zipf_frequency(term, ranker.language) + 3)
    )
    score = (
        math.log(max(frequency, 1))
        - 2 * distance
        + 0.5 * math.log1p(max(0, ranker.counts[term] - (term == low)))
    )

    def transition(a, b):
        prior = 10 ** (zipf_frequency(b, ranker.language) - 9)
        general = (
            0.9 * ranker.general.get((a, b), 0) / (ranker.outgoing[a] + 1) + 0.1 * prior
        )
        return (max(0, ranker.pairs[a, b] - held_pairs[a, b]) + 10 * general) / (
            max(0, ranker.book_outgoing[a] - held_outgoing[a]) + 10
        )

    if left:
        score += math.log(max(transition(left, term), 1e-15))
    if right:
        score += math.log(max(transition(term, right), 1e-15))
    return score


def source_origin(before, after, evidence):
    """OCR readings are evidence about origin, never proof of printed text."""
    readings = [TOKEN.findall(s.lower()) for s in evidence.get("readings", [])]
    if len(readings) == 2 and all(r == [after.lower()] for r in readings):
        return SourceOrigin.OCR_SUPPORTED
    if len(readings) == 2 and all(r == [before.lower()] for r in readings):
        return SourceOrigin.SOURCE_READING_SUPPORTED
    return SourceOrigin.UNRESOLVED


def meaning_sensitive_edit(before, after):
    """Removing a negative prefix can reverse the author's claim."""
    return any(
        before.lower() == prefix + after.lower()
        for prefix in ("a", "un", "non", "in", "im", "ir")
    )


def repair_reading(model, pages, book, ranker, recognize, audit, judge, skip_rows=()):
    from book_model import row_id

    rows = {
        r.get("row_id", row_id(int(n), r, book.get("source_sha256", ""))): r
        for n, rr in pages.items()
        for r in rr
    }
    capitalized = Counter(
        m.group().lower()
        for entry in model
        for block in entry["blocks"]
        for m in TOKEN.finditer(block["text"])
        if m.group().istitle() or m.group().isupper()
    )
    decisions, edits, seen = [], [], set()
    approved_spellings = {}
    for entry in model:
        if ranker.excluded(entry, book):
            continue
        blocks = entry["blocks"]
        for index, block in enumerate(blocks):
            if block["kind"] not in ("text", "quote", "heading"):
                continue
            for match in TOKEN.finditer(block["text"]):
                word = match.group()
                low = word.lower()
                if len(word) < 4 or not word.isalpha() or low in ranker.protected:
                    continue
                # Dictionary-valid prose stays put. Known names can be checked
                # only against a repeated, capitalized, source-local phrase.
                named = not word.islower() and match.start() != 0
                if word.isupper() or word.istitle():
                    named = block["kind"] == "heading" or match.start() > 0
                    following = TOKEN.findall(block["text"][match.end() :])
                    named = named or bool(following and following[0].isupper())
                if not named and ranker.known(word):
                    continue
                sources = [
                    s
                    for s in block["sources"]
                    if s["start"] <= match.start() and s["end"] >= match.end()
                ]
                if len(sources) != 1:
                    continue
                source = sources[0]
                ident = source["row_id"]
                if ident in skip_rows or (ident, word) in seen:
                    continue
                seen.add((ident, word))
                row = rows[ident]
                choices = [
                    c
                    for c in ranker.rank(
                        word, block["text"], match.start(), include_known=True
                    )
                    if c["term"] != low
                    and c["distance"] <= 2
                    and ranker.dictionary.lookup(c["term"])
                    and zipf_frequency(c["term"], ranker.language) >= 3
                ]
                if named:
                    tokens = list(TOKEN.finditer(block["text"]))
                    k = next(
                        i for i, m in enumerate(tokens) if m.start() == match.start()
                    )
                    neighbors = [
                        (tokens[j].group().lower(), j < k)
                        for j in (k - 1, k + 1)
                        if 0 <= j < len(tokens)
                    ]
                    choices = [
                        c
                        for c in choices
                        if c["confusion_supported"]
                        and capitalized[c["term"]] >= 2
                        and any(
                            ranker.pairs[
                                (neighbor, c["term"]) if left else (c["term"], neighbor)
                            ]
                            >= 2
                            for neighbor, left in neighbors
                        )
                    ]
                if not choices:
                    continue
                for candidate in choices:
                    candidate["comparison_score"] = comparison_score(
                        ranker,
                        word,
                        candidate["term"],
                        block["text"],
                        match.start(),
                        candidate["distance"],
                    )
                choices.sort(key=lambda c: (-c["comparison_score"], c["term"]))
                best = choices[0]
                after = spelling_case(word, best["term"])
                keep = comparison_score(
                    ranker, word, word, block["text"], match.start()
                )
                decision = dict(
                    page=source["page"],
                    row_id=ident,
                    bbox=row["bbox"],
                    before=word,
                    after=after,
                    candidates=choices[:5],
                    original_score=round(keep, 6),
                    score_delta=round(best["comparison_score"] - keep, 6),
                    action=RepairAction.REVIEW,
                )
                decisions.append(decision)
                spelling_rule = (
                    approved_spellings.get((low, best["term"]))
                    if word.islower() and block["kind"] == "text"
                    else None
                )
                # Compare against keeping the original, not only other edits.
                if (
                    best["comparison_score"] <= keep and not spelling_rule
                ) or re.findall(r"\b" + re.escape(word) + r"\b", row["text"]).count(
                    word
                ) != 1:
                    decision["reason"] = RepairReason.ORIGINAL_PREFERRED
                    continue
                state = dict(
                    original_token=word,
                    candidate_token=after,
                    paragraph=block["text"],
                    occurrence_start=match.start(),
                    occurrence_end=match.end(),
                    preceding_context=blocks[index - 1]["text"][-900:] if index else "",
                    following_context=(
                        blocks[index + 1]["text"][:900]
                        if index + 1 < len(blocks)
                        else ""
                    ),
                )
                decision["context_state"] = state
                judgment = judge(state) if judge and not spelling_rule else None
                if judgment is not None:
                    if any(
                        type(judgment.get(key)) not in (int, float)
                        or not math.isfinite(judgment[key])
                        or not 0 <= judgment[key] <= 1
                        for key in ("spelling_error", "meaning_preserved")
                    ):
                        raise ValueError("Invalid contextual spelling probabilities")
                    decision["context_judgment"] = judgment
                semantic_support = bool(
                    spelling_rule
                    or (
                        judgment
                        and judgment["spelling_error"] >= 0.9
                        and judgment["meaning_preserved"] >= 0.9
                    )
                )
                character_check = getattr(recognize, "character_support", None)
                can_check_characters = (
                    callable(character_check)
                    and best["confusion_supported"]
                    and best["distance"] == 1
                )
                # OCR alternatives can establish a glyph repair independently
                # of the semantic model. Only bounded one-character confusions
                # enter this route; arbitrary substitutions cannot use it.
                evidence = recognize(dict(row, page=source["page"]), word, best["term"])
                crop_support = (
                    source_origin(word, after, evidence) == SourceOrigin.OCR_SUPPORTED
                    and not meaning_sensitive_edit(word, after)
                    and (not named or semantic_support)
                )
                character_proof = (
                    character_check(evidence, word, best["term"])
                    if can_check_characters
                    else None
                )
                if not character_proof and not crop_support and not semantic_support:
                    decision["reason"] = (
                        RepairReason.CONTEXT_REJECTED
                        if judgment
                        else RepairReason.JUDGMENT_REQUIRED
                    )
                    continue
                if character_proof:
                    evidence = dict(evidence, character_alternatives=character_proof)
                decision.update(
                    action=RepairAction.CORRECT,
                    reason=(
                        RepairReason.CHARACTER_AND_CONTEXT
                        if character_proof
                        else (
                            RepairReason.CROP_AND_CONTEXT
                            if crop_support
                            else (
                                RepairReason.CONSISTENT_SPELLING
                                if spelling_rule
                                else RepairReason.SPELLING_AND_MEANING
                            )
                        )
                    ),
                    crop=evidence,
                    source_origin=(
                        SourceOrigin.OCR_ALTERNATIVE_SUPPORTED
                        if character_proof
                        else source_origin(word, after, evidence)
                    ),
                    correction_class=CorrectionClass.READING_SPELLING,
                )
                if spelling_rule:
                    decision["spelling_rule_from"] = spelling_rule
                # A confirmed, dictionary-invalid one-edit spelling with one
                # admissible target is a lexical normalization rule. Reuse it
                # only in lowercase prose, never names, valid words or quotes.
                if (
                    word.islower()
                    and block["kind"] == "text"
                    and len(choices) == 1
                    and best["distance"] == 1
                    and not ranker.known(word)
                    and judgment
                    and judgment["spelling_error"] >= 0.9
                    and judgment["meaning_preserved"] >= 0.9
                ):
                    approved_spellings[low, best["term"]] = ident
                edits.append(
                    (
                        row,
                        dict(
                            page=source["page"],
                            before=word,
                            after=after,
                            count=1,
                            whole_word=True,
                            evidence=decision,
                        ),
                    )
                )
    for row, edit in edits:
        apply_edits([row], [edit], audit, CorrectionClass.READING_SPELLING)
    return dict(
        mode="corrected-reading; separate spelling, meaning and source-origin decisions",
        resources=ranker.resources,
        corrected=len(edits),
        decisions=decisions,
    )
