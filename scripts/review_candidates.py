"""Source-witnessed review proposals and occurrence bindings, never automatic edits."""

from collections import defaultdict
import difflib
import re

from symspellpy import SymSpell, Verbosity

from common import cache_path, digest, file_digest, read_json
from ocr import merge_rows, nearest
from vocabulary import TOKEN


def anchored_changes(text, witness):
    own, peer = list(TOKEN.finditer(text)), list(TOKEN.finditer(witness))
    left, right = [m.group().lower() for m in own], [m.group().lower() for m in peer]
    result = {}
    for tag, i, j, k, l in difflib.SequenceMatcher(
        None, left, right, autojunk=False
    ).get_opcodes():
        if tag != "replace" or not 1 <= j - i <= 3 or not 1 <= l - k <= 3:
            continue
        before, after = (
            text[own[i].start() : own[j - 1].end()],
            witness[peer[k].start() : peer[l - 1].end()],
        )
        if before.lower() == after.lower() or not all(
            w.isalpha() for w in left[i:j] + right[k:l]
        ):
            continue
        if not all(
            re.fullmatch(r"[^\W\d_]+(?:[ \t]+[^\W\d_]+)*", s) for s in (before, after)
        ):
            continue
        anchors = sum(
            left[i - d] == right[k - d] and len(left[i - d]) >= 2
            for d in (1, 2)
            if i >= d and k >= d
        ) + sum(
            left[j + d] == right[l + d] and len(left[j + d]) >= 2
            for d in (0, 1)
            if j + d < len(left) and l + d < len(right)
        )
        if anchors >= 2:
            result[own[i].start(), own[j - 1].end(), after] = dict(
                before=before, after=after, anchors=anchors
            )
    return result


def map_span(source_text, fragment, start, end):
    """Map a complete literal token span; abstain on truncated/repeated ambiguity."""
    a, b = list(TOKEN.finditer(source_text)), list(TOKEN.finditer(fragment))
    selected = [i for i, m in enumerate(a) if m.start() >= start and m.end() <= end]
    if not selected or a[selected[0]].start() != start or a[selected[-1]].end() != end:
        return None
    left, right = [m.group() for m in a], [m.group() for m in b]
    if any(left.count(left[i]) > 1 for i in selected) and left != right:
        return None
    for match in difflib.SequenceMatcher(
        None, left, right, autojunk=False
    ).get_matching_blocks():
        if match.a <= selected[0] and selected[-1] < match.a + match.size:
            first, last = (
                match.b + selected[0] - match.a,
                match.b + selected[-1] - match.a,
            )
            return b[first].start(), b[last].end()
    return None


def occurrence_binding(occurrence):
    before = occurrence["original"]
    offset = occurrence["offset"]
    sources = [
        s
        for s in occurrence["sources"]
        if s["start"] <= offset and s["end"] >= offset + len(before)
    ]
    if len(sources) != 1:
        return None
    source = sources[0]
    fragment = occurrence["context"][source["start"] : source["end"]]
    span = map_span(
        fragment,
        source["text"],
        offset - source["start"],
        offset - source["start"] + len(before),
    )
    if span is None or source["text"][span[0] : span[1]] != before:
        return None
    return dict(
        page=source["page"],
        row_id=source["row_id"],
        bbox=source["bbox"],
        row_text=source["text"],
        row_text_sha256=digest(source["text"].encode()),
        start=span[0],
        end=span[1],
        before=before,
    )


class ReviewWitnesses:
    def __init__(self, work, book):
        self.book = book
        self.paths, self.loaded, self.resources = {}, {}, {}
        self.attested = defaultdict(list)
        self.sym = SymSpell(max_dictionary_edit_distance=2)
        if book.get("text_source") == "native":
            return
        for engine in ("tesseract", "vision", "rapid"):
            try:
                folder = cache_path(work, engine)
                provenance = folder / "provenance.json"
                meta = read_json(provenance)
            except FileNotFoundError:
                continue
            if meta["source_sha256"] != book["source_sha256"]:
                raise ValueError(f"Review {engine} cache source mismatch")
            self.paths[engine] = folder
            self.resources[engine] = dict(
                provenance_sha256=file_digest(provenance), pages={}
            )

    def peers(self, source, all_engines=False):
        page = source["page"]
        primary = (
            "vision"
            if page in self.book.get("vision_primary_pages", [])
            else "tesseract"
        )
        result = {}
        for engine, folder in self.paths.items():
            if not all_engines and engine == primary:
                continue
            key = engine, page
            if key not in self.loaded:
                file = folder / f"{page:04}.json"
                if not file.exists():
                    self.loaded[key] = []
                else:
                    self.loaded[key] = merge_rows(read_json(file)["lines"])
                    self.resources[engine]["pages"][str(page)] = file_digest(file)
            hit = nearest(source, self.loaded[key])
            if hit is not None:
                result[engine] = hit
        return result

    def collect(self, model):
        seen = set()
        for entry in model:
            if entry["chapter"] in self.book.get("statistics_excluded_chapters", []):
                continue
            for block in entry["blocks"] + entry["notes"]:
                if block["kind"] == "source-gap":
                    continue
                for source in block["sources"]:
                    if source["row_id"] in seen or source["page"] in self.book.get(
                        "reference_pages", []
                    ) + self.book.get("index_pages", []):
                        continue
                    seen.add(source["row_id"])
                    peers = self.peers(source, all_engines=True)
                    if len(peers) != 3:
                        continue
                    own = [m.group().lower() for m in TOKEN.finditer(source["text"])]
                    # Token positions match exactly through SequenceMatcher equal runs;
                    # a shared token elsewhere in a damaged row is not evidence.
                    positions = set(range(len(own)))
                    for peer in peers.values():
                        words = TOKEN.findall(peer["text"].lower())
                        positions &= {
                            i
                            for m in difflib.SequenceMatcher(
                                None, own, words, autojunk=False
                            ).get_matching_blocks()
                            for i in range(m.a, m.a + m.size)
                            if sum(
                                len(own[j]) >= 2
                                for j in range(
                                    max(m.a, i - 2), min(m.a + m.size, i + 3)
                                )
                                if j != i
                            )
                            >= 2
                        }
                    for i, m in enumerate(TOKEN.finditer(source["text"])):
                        word = m.group()
                        if i in positions and word.isalpha() and len(word) >= 3:
                            self.attested[word.lower()].append(
                                dict(
                                    page=source["page"],
                                    row_id=source["row_id"],
                                    bbox=source["bbox"],
                                    form=word,
                                )
                            )
        for word, records in sorted(self.attested.items()):
            if len({r["page"] for r in records}) >= 2:
                self.sym.create_dictionary_entry(word, len(records))

    def suggestions(self, word, occurrences):
        excluded = {s["page"] for o in occurrences for s in o["sources"]}
        result = []
        for candidate in self.sym.lookup(word, Verbosity.ALL, 2):
            records = [
                r for r in self.attested[candidate.term] if r["page"] not in excluded
            ]
            if candidate.term == word or len({r["page"] for r in records}) < 2:
                continue
            result.append(
                dict(
                    term=candidate.term,
                    distance=candidate.distance,
                    kind="witnessed-book-vocabulary",
                    generators=["witnessed-book-vocabulary"],
                    attested_count=len(records),
                    attested_pages=sorted({r["page"] for r in records}),
                    attested_forms=sorted({r["form"] for r in records}),
                    attestations=records,
                )
            )
        return sorted(
            result, key=lambda c: (c["distance"], -c["attested_count"], c["term"])
        )[:5]

    def disagreements(self, model):
        items = {}
        for entry in model:
            for index, block in enumerate(entry["blocks"] + entry["notes"]):
                if block["kind"] == "source-gap":
                    continue
                for source in block["sources"]:
                    if source["page"] in self.book.get(
                        "index_pages", []
                    ) + self.book.get("reference_pages", []):
                        continue
                    peers = self.peers(source)
                    if len(peers) != 2:
                        continue
                    proposals = [
                        anchored_changes(source["text"], p["text"])
                        for p in peers.values()
                    ]
                    for start, end, target in sorted(
                        proposals[0].keys() & proposals[1].keys()
                    ):
                        fragment = block["text"][source["start"] : source["end"]]
                        mapped = map_span(source["text"], fragment, start, end)
                        if mapped is None:
                            continue
                        offset = source["start"] + mapped[0]
                        before = block["text"][offset : source["start"] + mapped[1]]
                        key = before.lower()
                        item = items.setdefault(
                            key,
                            dict(
                                word=key,
                                occurrences=[],
                                suggestions=[],
                                category="ocr-witness-disagreement",
                                action="scan-review-required",
                                review_priority=0,
                                recommendation="compare-source-witnesses",
                                reasons=[
                                    "Two non-primary cached engines propose the same anchored change; scan review is still required."
                                ],
                            ),
                        )
                        if not any(
                            c["term"] == target.lower() for c in item["suggestions"]
                        ):
                            item["suggestions"].append(
                                dict(
                                    term=target.lower(),
                                    kind="aligned-ocr-witnesses",
                                    generators=["aligned-ocr-witnesses"],
                                )
                            )
                        ident = (source["row_id"], offset, target)
                        if any(
                            o.get("witness_identity") == list(ident)
                            for o in item["occurrences"]
                        ):
                            continue
                        item["occurrences"].append(
                            dict(
                                chapter=entry["chapter"],
                                paragraph=index,
                                context=block["text"],
                                offset=offset,
                                original=before,
                                block_kind=block["kind"],
                                sources=[source],
                                witness_proposal=target,
                                witness_identity=list(ident),
                                proposal_evidence=dict(
                                    engines=list(peers),
                                    readings=[p["text"] for p in peers.values()],
                                    anchors=[
                                        p[start, end, target]["anchors"]
                                        for p in proposals
                                    ],
                                ),
                            )
                        )
        return list(items.values())
