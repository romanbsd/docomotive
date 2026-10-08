"""Spelling corrections for a corrected reading edition, with source-origin audit.

Detection, semantic acceptance and source classification are independent. No
candidate is applied without exact row placement and an explicit judgment.
"""

import math
import re
from collections import Counter

from common import apply_edits
from lexical_repair import neighbors
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
    left, right = neighbors(context, offset)
    held_pairs = Counter(
        pair for pair in [(left, low), (low, right)] if None not in pair
    )
    held_outgoing = Counter(a for a, b in held_pairs.elements())
    # Match the SymSpell count scale: English corpus counts, else 10**zipf.
    frequency = ranker.sym.words.get(
        term,
        10
        ** (
            zipf_frequency(term, ranker.language)
            + (3 if ranker.language == "en" else 0)
        ),
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


def correction(row, decision):
    return (
        row,
        dict(
            page=decision["page"],
            before=decision["before"],
            after=decision["after"],
            count=1,
            whole_word=True,
            evidence=decision,
        ),
    )


def repair_reading(
    model, pages, book, ranker, recognize, audit, judge, skip_rows=(), editorial=False
):
    from book_model import row_id

    rows = {
        r.get("row_id", row_id(int(n), r, book.get("source_sha256", ""))): r
        for n, rr in pages.items()
        for r in rr
    }
    capitalized = Counter(
        m.group().lower()
        for entry in model
        for block in entry["blocks"] + entry["notes"]
        for m in TOKEN.finditer(block["text"])
        if m.group().istitle() or m.group().isupper()
    )
    decisions, edits, seen = [], [], set()
    approved_spellings, pending = {}, []
    for entry in model:
        if ranker.excluded(entry, book):
            continue
        # Notes get the same repair; context stays within their own sequence.
        for blocks, index, block in (
            (blocks, i, b)
            for blocks in (entry["blocks"], entry["notes"])
            for i, b in enumerate(blocks)
        ):
            if block["kind"] not in ("text", "quote", "heading", "note"):
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
                    left, right = neighbors(block["text"], match.start())
                    choices = [
                        c
                        for c in choices
                        if c["confusion_supported"]
                        and capitalized[c["term"]] >= 2
                        and (
                            (left and ranker.pairs[left, c["term"]] >= 2)
                            or (right and ranker.pairs[c["term"], right] >= 2)
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
                margin = (
                    best["comparison_score"] - choices[1]["comparison_score"]
                    if len(choices) > 1
                    else None
                )
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
                rule_eligible = word.islower() and block["kind"] == "text"
                spelling_rule = (
                    approved_spellings.get((low, best["term"]))
                    if rule_eligible
                    else None
                )
                placed = (
                    re.findall(r"\b" + re.escape(word) + r"\b", row["text"]).count(word)
                    == 1
                )
                # Compare against keeping the original, not only other edits.
                if not placed or (
                    best["comparison_score"] <= keep and not spelling_rule
                ):
                    decision["reason"] = RepairReason.ORIGINAL_PREFERRED
                    if placed and rule_eligible:
                        pending.append((row, decision, best["term"]))
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
                # Same bounds as the source-faithful route: an attested target,
                # and weak margins need the embedded OCR word to agree too.
                crop_support = (
                    source_origin(word, after, evidence) == SourceOrigin.OCR_SUPPORTED
                    and not meaning_sensitive_edit(word, after)
                    and (not named or semantic_support)
                    and best["book_count"] >= 2
                    and (
                        margin is None
                        or margin >= 1
                        or TOKEN.findall(evidence.get("embedded_word", "").lower())
                        == [best["term"]]
                    )
                )
                # Names always need a meaning judgment, whatever the glyph evidence.
                character_proof = (
                    character_check(evidence, word, best["term"])
                    if can_check_characters and (not named or semantic_support)
                    else None
                )
                if not character_proof and not crop_support and not semantic_support:
                    decision["reason"] = (
                        RepairReason.CONTEXT_REJECTED
                        if judgment
                        else RepairReason.JUDGMENT_REQUIRED
                    )
                    if rule_eligible:
                        pending.append((row, decision, best["term"]))
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
                edits.append(correction(row, decision))
    # A rule approved later in the book also covers earlier occurrences.
    for row, decision, term in pending:
        rule = approved_spellings.get((decision["before"].lower(), term))
        if not rule:
            continue
        word, after = decision["before"], decision["after"]
        evidence = recognize(dict(row, page=decision["page"]), word, term)
        decision.update(
            action=RepairAction.CORRECT,
            reason=RepairReason.CONSISTENT_SPELLING,
            crop=evidence,
            source_origin=source_origin(word, after, evidence),
            correction_class=CorrectionClass.READING_SPELLING,
            spelling_rule_from=rule,
        )
        edits.append(correction(row, decision))
    # The default edition fixes OCR errors only. Printed or unresolved spellings
    # are corrected only in the separate editorial edition.
    if not editorial:
        for _, edit in edits:
            decision = edit["evidence"]
            if decision["source_origin"] not in (
                SourceOrigin.OCR_SUPPORTED,
                SourceOrigin.OCR_ALTERNATIVE_SUPPORTED,
            ):
                decision.pop("correction_class")
                decision.update(
                    action=RepairAction.REVIEW, reason=RepairReason.EDITORIAL_ONLY
                )
        edits = [e for e in edits if e[1]["evidence"]["action"] == RepairAction.CORRECT]
    for row, edit in edits:
        apply_edits([row], [edit], audit, CorrectionClass.READING_SPELLING)
    return dict(
        mode="corrected-reading; separate spelling, meaning and source-origin decisions",
        resources=ranker.resources,
        corrected=len(edits),
        decisions=decisions,
    )
