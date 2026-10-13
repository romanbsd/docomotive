"""OCR geometry alignment and conservative named-engine reconciliation."""

import collections
import copy
import difflib
import re
import statistics
import unicodedata
from wordfreq import zipf_frequency
from build_resources import page_text
from common import edit_pattern

TOKEN = re.compile(r"[\w]+(?:['’-][\w]+)*|[^\w\s]", re.UNICODE)


def xml_safe_ocr(text):
    """Retain unreadable OCR glyph positions without emitting invalid XML."""
    invalid = []
    result = []
    for offset, c in enumerate(text):
        n = ord(c)
        if (
            n in (9, 10, 13)
            or 0x20 <= n <= 0xD7FF
            or 0xE000 <= n <= 0xFFFD
            or 0x10000 <= n <= 0x10FFFF
        ):
            result.append(c)
        else:
            result.append("\ufffd")
            invalid.append(dict(offset=offset, codepoint=f"U+{n:04X}"))
    return "".join(result), invalid


def center(line):
    return (line["bbox"][1] + line["bbox"][3]) / 2


def merge_rows(lines):
    groups = []
    for line in sorted(
        lines, key=lambda l: (l.get("column", 0), center(l), l["bbox"][0])
    ):
        line = copy.deepcopy(line)
        if (
            groups
            and line.get("column", 0) == groups[-1][0].get("column", 0)
            and abs(center(line) - statistics.mean(center(p) for p in groups[-1]))
            < 0.008
        ):
            groups[-1].append(line)
        else:
            groups.append([line])
    rows = []
    for group in groups:
        parts = sorted(group, key=lambda l: l["bbox"][0])
        row = copy.deepcopy(parts[0])
        row["text"] = " ".join(p["text"] for p in parts)
        row["bbox"] = [
            min(p["bbox"][0] for p in parts),
            min(p["bbox"][1] for p in parts),
            max(p["bbox"][2] for p in parts),
            max(p["bbox"][3] for p in parts),
        ]
        row["confidence"] = min(p.get("confidence", 1) for p in parts)
        rows.append(row)
    return rows


def embedded(page, split=None, raw=False):
    lines = []
    for b in page_text(page)["blocks"]:
        for l in b.get("lines", []):
            box = l["bbox"]
            box = [
                box[0] / page.rect.width,
                box[1] / page.rect.height,
                box[2] / page.rect.width,
                box[3] / page.rect.height,
            ]
            lines.append(
                {
                    "text": "".join(s["text"] for s in l["spans"]),
                    "bbox": box,
                    "column": int(split is not None and box[0] > split),
                    "confidence": 0,
                }
            )
    return lines if raw else merge_rows(lines)


def nearest(line, rows):
    matches = [
        r
        for r in rows
        if r.get("column", 0) == line.get("column", 0)
        and abs(center(line) - center(r)) < 0.012
    ]
    return min(matches, key=lambda r: abs(center(line) - center(r)), default=None)


def comparable(text):
    return re.findall(r"[\w]+(?:['’-][\w]+)*", text.lower().replace("’", "'"))


def bare(word):
    return "".join(
        c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c)
    )


def same_but_unaccented(own, other, language):
    """A witness that only drops accents from rare words (Delbrück/Delbruck)
    confirms the row; a common bare word (tó/to, й/и) stays reviewable."""

    return len(own) == len(other) and all(
        a == b or (bare(a) == b and zipf_frequency(b, language) < 3)
        for a, b in zip(own, other)
    )


INDEX_REFERENCE = re.compile(
    r"(?:^|(?<=[,–-])|(?<=[,–-] ))([0-9IlO]{2,4})(?=$|[,.;–-])"
)
EXTENDED_INDEX_REFERENCE = re.compile(
    r"(?:^|(?<=[,–-])|(?<=[,–-] ))([0-9IlOg]{2,4})(n?)(?=$|[,.;–-])"
)
SPLIT_CITATION_YEAR = re.compile(
    r"(?:^|(?<=\()|(?<=, ))([12][0-9 \t]*[0-9])([a-z]?)(?=[,;)])"
)
INDEX_DIGIT_MAP = str.maketrans({"I": "1", "l": "1", "O": "0", "g": "9"})
MONTHS = "January February March April May June July August September October November December".split()
CITATION_YEAR = re.compile(
    r"(?:^|(?<=\()|(?<=, )|"
    + "|".join(rf"(?<={m} )" for m in MONTHS)
    + r")(1[5-9]\d|20[0-3])([gl])([a-z]?)(?=[,;)])"
)
BRACKET_L = re.compile(r"[^\W\d_]*\][^\W\d_]*")


def numeric_slot_readings(text, start, end, after, witnesses, normalize=lambda s: s):
    """Bind a numeric replacement to its slot and all other row tokens."""
    left = re.findall(r"\w+", text[:start])
    right = re.findall(r"\w+", text[end:])
    before = re.findall(r"\w+", text[start:end])
    target = re.findall(r"\w+", after)
    values = []
    for witness in witnesses:
        tokens = re.findall(r"\w+", witness)
        found = None
        for value in (before, target):
            expected = left + value + right
            if list(map(normalize, tokens)) == list(map(normalize, expected)):
                found = tokens[len(left) : len(left) + len(value)]
                break
        values.append(found)
    return values


def repair_confusions(
    row,
    fresh,
    page,
    audit,
    language,
    index_digits=False,
    page_words=None,
    index_page_labels=None,
):
    """Repair recurring glyph confusions with structural or witness evidence."""

    def change(start, end, after, kind, evidence):
        before = row["text"][start:end]
        row["text"] = row["text"][:start] + after + row["text"][end:]
        audit.append(
            dict(
                page=page,
                kind=kind,
                before=before,
                after=after,
                bbox=row["bbox"],
                evidence=evidence,
            )
        )

    witnesses = [w["text"] for w in fresh if w]
    # A primary hyphen joining two words, where both fresh engines read an em
    # dash (or its "--" transcription) between the same words. Hyphenated
    # compounds keep their hyphen unless both engines see the longer stroke.
    if len(witnesses) == 2:
        for match in reversed(list(re.finditer(r"(\w+)-(\w+)", row["text"]))):
            left, right = match.groups()
            if left.isdigit() or right.isdigit():
                continue
            pattern = (
                rf"(?<!\w){re.escape(left)}(\s?)(?:—|--)(\s?){re.escape(right)}(?!\w)"
            )
            seen = [re.search(pattern, w) for w in witnesses]
            if all(seen):
                start = match.start(2) - 1
                spacing = {s.group(1) + "—" + s.group(2) for s in seen}
                change(
                    start,
                    start + 1,
                    spacing.pop() if len(spacing) == 1 else "—",
                    "two-fresh-em-dash",
                    "both fresh engines read an em dash between the same words",
                )
    # Index page references: digits mixed with serif I/l/O, in list syntax,
    # where a fresh engine reads plain digits. Every fresh witness must keep
    # the row's words and either repeat the primary token or read those
    # digits; any other reading of the slot is a contradiction.
    digit_map = str.maketrans({"I": "1", "l": "1", "O": "0"})
    words = [t for t in re.findall(r"\w+", row["text"]) if t.isalpha() and len(t) >= 3]
    readings = [re.findall(r"\w+", w) for w in witnesses]
    for match in reversed(list(INDEX_REFERENCE.finditer(row["text"]))):
        token = match.group(1)
        after = token.translate(digit_map)
        if (
            index_digits
            and len(readings) == 2
            and after != token
            and any(c.isdigit() for c in token)
            and any(after in r for r in readings)
            and all(token in r or after in r for r in readings)
            and all(word in r for word in words for r in readings)
        ):
            change(
                match.start(1),
                match.end(1),
                after,
                "witnessed-index-digit",
                "index list syntax and a fresh engine reading plain digits",
            )

    # Additional index confusions require positional agreement and a real
    # printed page label. Keep note suffixes literal; never rewrite quantities.
    def normalized_reference(token):
        if re.fullmatch(r"[0-9IlOg]{2,4}n?", token) and any(c.isdigit() for c in token):
            return token.translate(INDEX_DIGIT_MAP)
        return token

    if index_digits and index_page_labels is not None and len(witnesses) == 2:
        for match in reversed(list(EXTENDED_INDEX_REFERENCE.finditer(row["text"]))):
            token, suffix = match.groups()
            digits = token.translate(INDEX_DIGIT_MAP)
            after = digits + suffix
            if (
                token == digits
                or not any(c.isdigit() for c in token)
                or digits not in index_page_labels
            ):
                continue
            values = numeric_slot_readings(
                row["text"],
                match.start(),
                match.end(),
                after,
                witnesses,
                normalized_reference,
            )
            if (
                all(v in ([match.group()], [after]) for v in values)
                and [after] in values
            ):
                change(
                    match.start(),
                    match.end(),
                    after,
                    "witnessed-index-digit",
                    dict(
                        rule="aligned-index-page-label",
                        readings=values,
                        witnesses=witnesses,
                        printed_page=digits,
                    ),
                )
    # Whitespace inside a four-digit citation year is a candidate, not proof.
    # Both fresh engines must read the compact year at the same lexical slot.
    for match in reversed(list(SPLIT_CITATION_YEAR.finditer(row["text"]))):
        raw, suffix = match.groups()
        year = re.sub(r"[ \t]", "", raw)
        if raw == year or len(year) != 4 or not 1500 <= int(year) <= 2099:
            continue
        if row["text"][: match.start()].endswith(", ") and not re.search(
            r"(?:^|\s|\()[A-Z][\w.&'-]*, $", row["text"][: match.start()]
        ):
            continue
        after = year + suffix
        values = numeric_slot_readings(
            row["text"],
            match.start(),
            match.end(),
            after,
            witnesses,
        )
        if len(values) == 2 and all(v == [after] for v in values):
            change(
                match.start(),
                match.end(),
                after,
                "witnessed-citation-year-spacing",
                dict(
                    rule="two-aligned-citation-years",
                    readings=values,
                    witnesses=witnesses,
                ),
            )
    # Citation years such as "(Osmond, 196la, p. 76)" or "Books, 196g), p.":
    # a fourth year glyph read as g/l, only between citation punctuation, so
    # quantities such as "150g of" are never touched.
    for match in reversed(list(CITATION_YEAR.finditer(row["text"]))):
        century, glyph, suffix = match.groups()
        # After a comma, only an author or publisher (Osmond, 196la), never a
        # list of quantities (50g, 196g, and).
        if row["text"][: match.start()].endswith(", ") and not re.search(
            r"(?:^|\s|\()[A-Z][\w.&'-]*, $", row["text"][: match.start()]
        ):
            continue
        change(
            match.start(),
            match.end(),
            century + {"g": "9", "l": "1"}[glyph] + suffix,
            "citation-year-digit",
            "four-digit year in citation punctuation",
        )
    # A closing bracket inside a word, without an opening bracket on the row,
    # is a damaged "l" when that yields a common word and the raw token is not.
    if "[" not in row["text"]:
        for match in reversed(list(BRACKET_L.finditer(row["text"]))):
            token = match.group()
            after = token.replace("]", "l")
            if (
                token != "]"
                and zipf_frequency(after.lower(), language) >= 3
                and zipf_frequency(token.replace("]", "").lower(), language) < 3
            ):
                change(
                    match.start(),
                    match.end(),
                    after,
                    "bracket-letter-l",
                    "common word only with l; no opening bracket on the row",
                )
    # A nonword where a fresh engine reads a common word one letter away, at
    # the same position, and no engine proposes another common word. Row-edge
    # tokens may be hyphenation fragments and stay as read; a token repeated on
    # the page is a name; a dropped accent may be a foreign spelling; and an
    # ending that several very common words fit (историн: истории/история) is
    # an inflection guess.
    alphabet = (
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
        if language == "ru"
        else "abcdefghijklmnopqrstuvwxyz"
    )

    def ambiguous_ending(before, after):
        if before[:-1] != after[:-1]:
            return False
        stem = before[:-1].lower()
        return sum(zipf_frequency(stem + c, language) >= 4 for c in alphabet) > 1

    spans = list(re.finditer(r"\w+", row["text"]))
    own = [m.group() for m in spans]
    proposed = {}
    for words in readings:
        for tag, i, j, k, l in difflib.SequenceMatcher(
            None, own, words, autojunk=False
        ).get_opcodes():
            if tag == "replace" and j - i == l - k:
                for n in range(j - i):
                    proposed.setdefault(i + n, set()).add(words[k + n])
    for i, words in sorted(proposed.items(), reverse=True):
        before = own[i]
        common = {w for w in words if zipf_frequency(w.lower(), language) >= 3}
        after = common.pop() if len(common) == 1 else ""
        if (
            0 < i < len(own) - 1
            and before.isalpha()
            and len(before) == len(after) >= 4
            and sum(a != b for a, b in zip(before, after)) == 1
            and zipf_frequency(before.lower(), language) == 0
            and (page_words or {}).get(before, 0) < 2
            and bare(before).lower() != after.lower()
            and not ambiguous_ending(before, after)
        ):
            change(
                spans[i].start(),
                spans[i].end(),
                after,
                "fresh-word-for-nonword",
                "a fresh engine reads a common word one letter from a nonword",
            )
    if language == "ru":
        repair_russian_letters(row, readings, change)


ROMAN = re.compile(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})")


def repair_russian_letters(row, readings, change):
    """Replace letters outside the Russian alphabet that OCR borrowed from
    Ukrainian or Belarusian (І, є, ї, ...) and the Latin-script "Nº"."""
    for match in reversed(list(re.finditer(r"N[º°](?=\s?\d)", row["text"]))):
        change(match.start(), match.end(), "№", "russian-numero", "Nº before a number")
    foreign = "ІіЇїЄєҐґЎў"
    tokens = list(re.finditer(r"\w+", row["text"]))
    # Whole words read in Latin look-alikes (Ha, OT, co) between Russian words.
    cyrillic = str.maketrans("ABEKMHOPCTXaeopcyx", "АВЕКМНОРСТХаеорсух")
    russian = lambda m: m and re.fullmatch(r"[а-яё]+", m.group(), re.I)
    for k, match in reversed(list(enumerate(tokens))):
        token, after = match.group(), match.group().translate(cyrillic)
        # OCR case noise inside a sentence (оТ, НО) follows the previous word.
        if after not in (after.lower(), after.capitalize()) or (
            k and len(after) > 1 and tokens[k - 1].group().islower()
        ):
            after = after.lower()
        if (
            re.fullmatch(r"[ABEKMHOPCTXaeopcyx]+", token)
            and not ROMAN.fullmatch(token)
            and k
            and k + 1 < len(tokens)
            and russian(tokens[k - 1])
            and russian(tokens[k + 1])
            and not re.search(r"[-\w]", row["text"][match.end() : match.end() + 1])
            and (
                after.lower() in "аксоу"
                if len(after) == 1
                else zipf_frequency(after.lower(), "ru") >= 5
            )
        ):
            change(
                match.start(),
                match.end(),
                after,
                "russian-latin-lookalike",
                "Russian word in Latin look-alike letters between Russian words",
            )
    for k, match in reversed(list(enumerate(tokens))):
        token = match.group()
        if not any(c in foreign for c in token):
            continue
        # Roman numerals typed with Cyrillic Х and Ukrainian І (ХІХ в.).
        latin = token.translate(str.maketrans("ІХ", "IX"))
        lone = len(token) == 1 and row["text"][match.end() : match.end() + 1] == "."
        if re.fullmatch(r"[IXV]+", latin) and ROMAN.fullmatch(latin) and not lone:
            change(match.start(), match.end(), latin, "russian-roman-numeral", "")
            continue
        # Fresh engines that read Russian letters in those slots, at the same
        # word position, and agree with each other.
        pattern = re.compile(
            "".join("[а-яё]" if c in foreign else re.escape(c) for c in token),
            re.I,
        )
        slots = [r[k] for r in readings if k < len(r) and r[k] != token]
        seen = set(slots)
        if len(seen) == 1 and pattern.fullmatch(slots[0]):
            change(
                match.start(),
                match.end(),
                seen.pop(),
                "russian-letter-witness",
                "fresh engines read Russian letters in the slots",
            )
            continue
        # Inside a word: the look-alike Russian letter when that gives a word.
        after = token.translate(str.maketrans("єіїЄІЇ", "еииЕИИ"))
        if (
            not any(c in foreign for c in after)
            and len(token) > 2
            and zipf_frequency(after.lower(), "ru") >= 2.5
        ):
            change(
                match.start(),
                match.end(),
                after,
                "russian-letter-lookalike",
                "common Russian word with the look-alike letter",
            )


def repair_spaced_initial_word(row, embedded_row, fresh, page, audit):
    """Recover a damaged initial small-cap word from spaced PDF text and OCR."""
    match = re.match(r"([‘“\"]?)([A-Za-z]{3,}(?:-[A-Za-z]{3,})*)(?=\s)", row["text"])
    if not match or not embedded_row:
        return
    tail = comparable(row["text"])[1:]
    if len(tail) < 3:
        return
    embedded_text = embedded_row["text"]
    # Locate the unchanged prose after the spaced small-cap word. The whole
    # remaining row must agree, so a repeated anchor word cannot supply a
    # false prefix.
    for anchor in re.finditer(rf"\b{re.escape(tail[0])}\b", embedded_text, re.I):
        if comparable(embedded_text[anchor.start() :]) != tail:
            continue
        prefix = embedded_text[: anchor.start()].strip()
        if not re.search(r"[A-Za-z]\s+[A-Za-z]", prefix):
            continue
        candidate = re.sub(r"\s+", "", prefix)
        if not re.fullmatch(r"[A-Za-z]{3,}(?:-[A-Za-z]{3,})*", candidate):
            continue
        before = match.group(2)
        if (
            before.casefold() == candidate.casefold()
            or before[0].casefold() != candidate[0].casefold()
            or abs(len(before) - len(candidate)) > 2
            or difflib.SequenceMatcher(
                None, before.casefold(), candidate.casefold()
            ).ratio()
            < (0.75 if "-" in candidate else 0.5)
        ):
            continue
        corroborating = [
            witness["text"]
            for witness in fresh
            if witness
            and (tokens := comparable(witness["text"]))
            and tokens[0] == candidate.casefold()
            and tokens[1:] == tail
        ]
        # Removing an opening quote also changes punctuation, so require both
        # independent fresh engines in addition to the embedded text.
        if len(corroborating) < (2 if match.group(1) else 1):
            continue
        row["text"] = candidate + row["text"][match.end() :]
        audit.append(
            dict(
                page=page,
                kind=(
                    "spaced-initial-compound"
                    if "-" in candidate
                    else "spaced-initial-word"
                ),
                before=match.group(),
                after=candidate,
                bbox=row["bbox"],
                evidence=dict(embedded=embedded_text, fresh=corroborating[0]),
            )
        )
        return


def repair_three_witness_initial_word(row, witnesses, page, audit):
    """Replace one initial OCR word when three other readings match the line."""
    if any(w is None for w in witnesses):
        return
    own = comparable(row["text"])
    peers = [comparable(w["text"]) for w in witnesses]
    if len(own) < 4 or not all(len(p) == len(own) for p in peers):
        return
    if not all(p[0] == peers[0][0] and p[1:] == own[1:] for p in peers):
        return
    match = re.match(r"[‘“\"]?([A-Za-z]+)(?=\b)", row["text"])
    if not match or not peers[0][0].isascii() or not peers[0][0].isalpha():
        return
    before, after = match.group(1), peers[0][0]
    if before.casefold() == after or abs(len(before) - len(after)) > 2:
        return
    witness_words = [
        re.match(r"[‘“\"]?([A-Za-z]+)(?=\b)", w["text"]) for w in witnesses
    ]
    witness_spellings = [
        m.group(1) if m and m.group(1).casefold() == after else None
        for m in witness_words
    ]
    corrected = (
        witness_spellings[0]
        if witness_spellings[0]
        and all(word == witness_spellings[0] for word in witness_spellings)
        else (
            after.upper()
            if before.isupper()
            else after.capitalize() if before.istitle() else after
        )
    )
    row["text"] = (
        row["text"][: match.start(1)] + corrected + row["text"][match.end(1) :]
    )
    audit.append(
        dict(
            page=page,
            kind="three-witness-initial-word",
            before=before,
            after=corrected,
            bbox=row["bbox"],
            evidence=[w["text"] for w in witnesses],
        )
    )


def correct_page(
    primary,
    baseline,
    secondary,
    tertiary,
    audit,
    review,
    page,
    primary_name="tesseract",
    secondary_name="vision",
    recover_regions=False,
    recover_glyphs=False,
    language="en",
    recover_index_digits=False,
    index_page_labels=None,
    protected_edits=None,
):
    rows = merge_rows(primary)
    for row in rows:
        row["text"], invalid = xml_safe_ocr(row["text"])
        if invalid:
            event = dict(
                page=page,
                kind="invalid-xml-ocr-character",
                bbox=row["bbox"],
                primary=row["text"],
                replacements=invalid,
            )
            audit.append(event)
            review.append(dict(event, action="scan-review-required"))
    # Some engines collapse two printed lines into a confident but garbled row.
    # Recover the region only when two fresh engines independently agree on
    # every line, their geometry covers the tall row, and its text disagrees.
    recovered = []
    for row in rows:
        peers = [
            r
            for r in secondary
            if recover_regions
            and r.get("column", 0) == row.get("column", 0)
            and row["bbox"][1] <= center(r) <= row["bbox"][3] + 0.002
            and abs(r["bbox"][0] - row["bbox"][0]) < 0.035
            and abs(r["bbox"][2] - row["bbox"][2]) < 0.05
        ]
        agreement = [nearest(r, tertiary) for r in peers]

        # Old embedded OCR can corroborate a fresh line when the other fresh
        # engine disagrees only at a tiny apparatus glyph. Prose must match
        # exactly in two witnesses; the third may add only one digit-confusion
        # token (a common note glyph error), never substitute prose words.
        def agrees(r, other):
            if not other:
                return False
            own, alternate = comparable(r["text"]), comparable(other["text"])
            old = nearest(other, baseline)
            changes = difflib.SequenceMatcher(None, own, alternate).get_opcodes()
            return own == alternate or (
                old
                and comparable(old["text"]) in (own, alternate)
                and sum(
                    (j - i) + (l - k) for tag, i, j, k, l in changes if tag != "equal"
                )
                == 1
                and all(
                    tag == "equal"
                    or (
                        tag in ("insert", "delete")
                        and (j - i) + (l - k) == 1
                        and all(
                            len(token) <= 3
                            and all(c in "0123456789silobz" for c in token)
                            for token in own[i:j] + alternate[k:l]
                        )
                    )
                    for tag, i, j, k, l in changes
                )
            )

        tall = peers and row["bbox"][3] - row["bbox"][1] > 1.5 * statistics.median(
            r["bbox"][3] - r["bbox"][1] for r in peers
        )
        if (
            len(peers) >= 2
            and tall
            and all(
                other
                and agrees(r, other)
                and r.get("confidence", 0) >= 0.85
                and other.get("confidence", 0) >= 0.85
                for r, other in zip(peers, agreement)
            )
            and difflib.SequenceMatcher(
                None,
                comparable(row["text"]),
                comparable(" ".join(r["text"] for r in peers)),
            ).ratio()
            < 0.6
            and all(
                not (
                    existing := nearest(
                        r, [other for other in rows if other is not row]
                    )
                )
                or comparable(existing["text"]) == comparable(other["text"])
                for r, other in zip(peers, agreement)
            )
        ):
            recovered.extend(
                copy.deepcopy(
                    r
                    if comparable(r["text"]) == comparable(other["text"])
                    or (
                        (old := nearest(r, baseline))
                        and comparable(r["text"]) == comparable(old["text"])
                    )
                    else other
                )
                for r, other in zip(peers, agreement)
                if not nearest(
                    r, [existing for existing in rows if existing is not row]
                )
            )
            audit.append(
                dict(
                    page=page,
                    kind="two-witness-region-recovery",
                    before=row["text"],
                    bbox=row["bbox"],
                    after=[r["text"] for r in peers],
                    evidence=f"{secondary_name}/rapid line agreement, with embedded corroboration for small discrepancies",
                )
            )
        else:
            recovered.append(row)
    rows = recovered
    for row in baseline:
        if len(row["text"]) < 8 or nearest(row, rows):
            continue
        alt = nearest(row, secondary)
        other = nearest(row, tertiary)
        if (
            alt
            and other
            and difflib.SequenceMatcher(
                None, comparable(alt["text"]), comparable(other["text"])
            ).ratio()
            > 0.8
        ):
            new = copy.deepcopy(alt)
            new["bbox"] = row["bbox"]
            new["recovered"] = True
            rows.append(new)
            audit.append(
                {
                    "page": page,
                    "kind": "missing-line-recovery",
                    "bbox": row["bbox"],
                    "after": new["text"],
                    "evidence": f"{secondary_name} and rapid agree at embedded OCR geometry",
                }
            )
        else:
            review.append(
                {
                    "page": page,
                    "kind": "possible-missing-line",
                    "bbox": row["bbox"],
                    "embedded": row["text"],
                }
            )
    rows = merge_rows(rows)
    page_words = collections.Counter(
        w for r in rows for w in re.findall(r"\w+", r["text"])
    )
    for row in rows:
        raw_primary = row["text"]
        witnesses = [nearest(row, source) for source in [baseline, secondary, tertiary]]
        # A leading star can be a damaged opening quote, but can also be a
        # real apparatus marker. Require both fresh engines to read a quote
        # and otherwise match the entire line; embedded OCR is not a vote.
        normalize_quotes = lambda text: text.translate(str.maketrans("“”", '""'))
        fresh = witnesses[1:]
        manual_overlay = any(
            re.search(edit_pattern(edit), row["text"])
            for edit in (protected_edits or [])
        )
        if not manual_overlay:
            repair_spaced_initial_word(row, witnesses[0], fresh, page, audit)
            repair_three_witness_initial_word(row, witnesses, page, audit)
        if recover_index_digits and all(fresh):
            # Numeric index references often confuse serif 1/I/l and 0/O.
            # Both fresh witnesses must agree on every token; unchanged words
            # anchor the row. Mixed digit tokens only, to preserve Roman folios.
            own_tokens = list(TOKEN.finditer(row["text"]))
            alternate = [TOKEN.findall(w["text"]) for w in fresh]
            digit_map = str.maketrans({"I": "1", "l": "1", "O": "0"})
            own = [m.group() for m in own_tokens]
            if (
                alternate[0] == alternate[1]
                and len(own) == len(alternate[0])
                and any(
                    a == b and a.isalpha() and len(a) >= 3
                    for a, b in zip(own, alternate[0])
                )
                and all(
                    a == b
                    or (
                        2 <= len(a) <= 4
                        and len(a) == len(b)
                        and any(c.isdigit() for c in a)
                        and b.isascii()
                        and b.isdigit()
                        and a.translate(digit_map) == b
                    )
                    for a, b in zip(own, alternate[0])
                )
            ):
                for match, after in reversed(list(zip(own_tokens, alternate[0]))):
                    if match.group() != after:
                        row["text"] = (
                            row["text"][: match.start()]
                            + after
                            + row["text"][match.end() :]
                        )
                        audit.append(
                            dict(
                                page=page,
                                kind="two-fresh-index-digit",
                                before=match.group(),
                                after=after,
                                bbox=row["bbox"],
                                evidence=f"{secondary_name}/rapid complete-token-sequence agreement",
                                witnesses=[w["text"] for w in fresh],
                            )
                        )
        if row["text"].startswith("*") and all(fresh):
            candidate = '"' + normalize_quotes(row["text"][1:])
            if '"' in candidate[1:] and all(
                normalize_quotes(w["text"]) == candidate for w in fresh
            ):
                row["text"] = "“" + row["text"][1:]
                audit.append(
                    dict(
                        page=page,
                        kind="two-fresh-quote-mark",
                        before=raw_primary,
                        after=row["text"],
                        bbox=row["bbox"],
                        evidence=f"{secondary_name}/rapid complete-line agreement",
                    )
                )
        spans = list(TOKEN.finditer(row["text"]))
        vt = [m.group() for m in spans]
        # Small-cap headings can be read as mixed case by one engine. A
        # dictionary-known misreading is still eligible when both fresh engines
        # agree on the whole heading, with an unchanged alphabetic anchor.
        # An all-caps primary row is not that misreading and keeps its text.
        fresh_tokens = [TOKEN.findall(w["text"]) for w in fresh if w]
        heading_consensus = (
            not all(t.isupper() for t in vt if t.isalpha())
            and len(fresh_tokens) == 2
            and fresh_tokens[0] == fresh_tokens[1]
            and len(fresh_tokens[0]) == len(vt)
            and sum(t.isalpha() for t in fresh_tokens[0]) >= 2
            and all(t.isupper() for t in fresh_tokens[0] if t.isalpha())
            and sum(a.casefold() != b.casefold() for a, b in zip(vt, fresh_tokens[0]))
            == 1
            and any(
                a.isalpha() and len(a) >= 3 and a.casefold() == b.casefold()
                for a, b in zip(vt, fresh_tokens[0])
            )
        )
        proposals = {}
        for witness in witnesses:
            if witness is None:
                continue
            wt = TOKEN.findall(witness["text"])
            for tag, i, j, k, l in difflib.SequenceMatcher(
                None, vt, wt, autojunk=False
            ).get_opcodes():
                if tag == "replace" and j - i == l - k == 1:
                    proposals.setdefault((i, wt[k]), 0)
                    proposals[(i, wt[k])] += 1
        edits = []
        for (i, after), votes in proposals.items():
            before = vt[i]
            if votes < 2 or before == after:
                continue
            plausible = (
                before.isalpha()
                and after.isalpha()
                and len(after) > 2
                and difflib.SequenceMatcher(None, before, after).ratio() >= 0.65
                and zipf_frequency(before, language) < 2
                and (zipf_frequency(after, language) >= 2 or votes == 3)
            )
            plausible = plausible or (
                before in {"J", "1"} and after == "I" and votes >= 2
            )
            differences = [
                (a, b) for a, b in zip(before.casefold(), after.casefold()) if a != b
            ]
            plausible = plausible or (
                heading_consensus
                and after == fresh_tokens[0][i]
                and before.isalpha()
                and after.isalpha()
                and len(before) == len(after) >= 4
                and len(differences) == 1
                and set(differences[0]) == {"i", "l"}
                and zipf_frequency(after, language) >= 2
            )
            if recover_glyphs and votes == 3:
                # Three witnesses must agree on a common alphabetic word.
                # A single confusable digit is eligible; dates, chemical names,
                # quantities and arbitrary alphanumeric rewrites are not.
                confusions = {"0": "o", "1": "ilt", "5": "s", "8": "b"}
                plausible = plausible or (
                    2 <= len(before) <= 10
                    and len(before) == len(after)
                    and sum(c.isdigit() for c in before) == 1
                    and after.isalpha()
                    and zipf_frequency(after, language) >= 4
                    and all(
                        a.lower() == b.lower() or b.lower() in confusions.get(a, "")
                        for a, b in zip(before, after)
                    )
                )
            if plausible:
                edits.append((spans[i].start(), spans[i].end(), after))
                audit.append(
                    {
                        "page": page,
                        "kind": "two-witness-token",
                        "before": before,
                        "after": after,
                        "votes": votes,
                        "bbox": row["bbox"],
                    }
                )
        for start, end, text in sorted(edits, reverse=True):
            row["text"] = row["text"][:start] + text + row["text"][end:]
        repair_confusions(
            row,
            fresh,
            page,
            audit,
            language,
            recover_index_digits,
            page_words,
            index_page_labels,
        )
        own = comparable(row["text"])
        best = [comparable(w["text"]) for w in witnesses[1:] if w]
        # Agreement on lexical content resolves harmless OCR typography differences.
        if (
            best
            and not any(same_but_unaccented(own, other, language) for other in best)
            and not (witnesses[0] and own == comparable(witnesses[0]["text"]))
        ):
            texts = {
                primary_name: raw_primary,
                "embedded": witnesses[0]["text"] if witnesses[0] else "",
                secondary_name: witnesses[1]["text"] if witnesses[1] else "",
                "rapid": witnesses[2]["text"] if witnesses[2] else "",
            }
            review.append(
                {
                    "page": page,
                    "kind": "ocr-disagreement",
                    "primary": row["text"],
                    "primary_engine": primary_name,
                    "engine_texts": texts,
                    "column": row.get("column", 0),
                    "embedded": texts["embedded"],
                    "vision": texts.get("vision", ""),
                    "tesseract": texts.get("tesseract", ""),
                    "paddle": texts["rapid"],
                    "bbox": row["bbox"],
                }
            )
    return rows
