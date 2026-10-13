"""Easy candidate/queue ablations on frozen cases; never apply a correction."""

import argparse
from collections import defaultdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import unicodedata

from spylls.hunspell import Dictionary
from symspellpy import SymSpell, Verbosity
from symspellpy.editdistance import DistanceAlgorithm, EditDistance

from common import ROOT, file_digest, read_json, write_json
from statistical_ocr_research import load_books


def accent_key(word):
    """Lossy retrieval key only: callers must return original literal spellings."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", word.casefold())
        if unicodedata.category(c) != "Mn"
    )


class VocabularyIndex:
    def __init__(self, words, radius=2, folded=False):
        self.radius, self.folded = radius, folded
        self.forms = defaultdict(set)
        self.sym = SymSpell(max_dictionary_edit_distance=radius)
        for word, count in sorted(words.items()):
            if not word.isalpha() or len(word) < 3:
                continue
            key = accent_key(word) if folded else word
            self.forms[key].add(word)
            self.sym.create_dictionary_entry(key, count)

    def lookup(self, word):
        key = accent_key(word) if self.folded else word
        return {
            form
            for candidate in self.sym.lookup(key, Verbosity.ALL, self.radius)
            for form in self.forms[candidate.term]
            if form != word
        }


def peer_candidate(case):
    """Existing frozen readings are already anchored; consensus is advisory."""
    readings = case["readings"]
    if len(readings) == 2 and readings[0] == readings[1]:
        target = readings[0]
        if target != case["before"] and target.isalpha():
            return target
    return None


class TrigramIndex:
    """Bounded shared-trigram fallback; literal forms, no answer-based ranking."""

    def __init__(self, words):
        self.words = {w: n for w, n in words.items() if w.isalpha() and len(w) >= 6}
        self.grams = defaultdict(set)
        for word in self.words:
            for gram in self.parts(word):
                self.grams[gram].add(word)
        self.distance = EditDistance(DistanceAlgorithm.LEVENSHTEIN)

    @staticmethod
    def parts(word):
        return {word[i : i + 3] for i in range(len(word) - 2)}

    def lookup(self, word):
        if len(word) < 6:
            return set()
        grams = self.parts(word)
        pool = set().union(*(self.grams[g] for g in grams))
        candidates = []
        for target in sorted(pool - {word}):
            distance = self.distance.compare(word, target, 4)
            if distance < 0:
                continue
            other = self.parts(target)
            overlap = len(grams & other) / len(grams | other)
            candidates.append((-overlap, distance, -self.words[target], target))
        return {row[-1] for row in sorted(candidates)[:10]}


def priority(case, known, peer_first):
    peer = peer_candidate(case)
    unknown = not known(case["before"])
    # Deterministic ties use source page/id, never answer labels or target words.
    flags = (
        (not bool(peer), not unknown) if peer_first else (not unknown, not bool(peer))
    )
    return (*flags, case["book"], case["page"], case["id"])


def queue_report(cases, known, peer_first, budgets=(10, 25, 50, 100)):
    ordered = sorted(cases, key=lambda c: priority(c, known, peer_first))
    return {
        "order": [c["id"] for c in ordered],
        "budgets": {
            str(n): {
                "reviewed": len(ordered[:n]),
                "errors": sum(c["expected"] == "correct" for c in ordered[:n]),
                "correct_controls": sum(c["expected"] == "retain" for c in ordered[:n]),
            }
            for n in budgets
        },
    }


def suppression_report(cases, known):
    """Simulated confirmed-unchanged feedback, whole-page forward split."""
    held, suppressed, vetoed = [], [], []
    for book in sorted({c["book"] for c in cases}):
        own = [c for c in cases if c["book"] == book]
        pages = sorted({c["page"] for c in own})
        seed_pages = set(pages[: max(1, (len(pages) + 4) // 5)])
        # Synthetic correct controls supply an optimistic unchanged-form resource.
        confirmed = {
            c["before"]
            for c in own
            if c["page"] in seed_pages and c["expected"] == "retain"
        }
        for case in own:
            if case["page"] in seed_pages:
                continue
            held.append(case)
            if case["before"] in confirmed and not known(case["before"]):
                if peer_candidate(case):
                    vetoed.append(case["id"])
                else:
                    suppressed.append(case)
    return {
        "held_out_errors": sum(c["expected"] == "correct" for c in held),
        "held_out_controls": sum(c["expected"] == "retain" for c in held),
        "suppressed_error_ids": [
            c["id"] for c in suppressed if c["expected"] == "correct"
        ],
        "suppressed_control_ids": [
            c["id"] for c in suppressed if c["expected"] == "retain"
        ],
        "disagreement_veto_ids": vetoed,
        "warning": "Counterfactual correct controls as feedback; not measured live review savings",
    }


def measure(cases, added):
    errors = [c for c in cases if c["expected"] == "correct"]
    controls = [c for c in cases if c["expected"] == "retain"]
    gained = [
        c["id"]
        for c in errors
        if not c["book_only_candidate_recall"] and c["after"] in added[c["id"]]
    ]
    return dict(
        errors=len(errors),
        baseline_answers=sum(c["book_only_candidate_recall"] for c in errors),
        method_answers=sum(c["after"] in added[c["id"]] for c in errors),
        additional_answers=len(gained),
        gained_ids=gained,
        controls_with_alternatives=sum(bool(added[c["id"]]) for c in controls),
        total_error_alternatives=sum(len(added[c["id"]]) for c in errors),
        total_control_alternatives=sum(len(added[c["id"]]) for c in controls),
        max_alternatives=max(map(len, added.values()), default=0),
    )


def verify_frozen(books, cases, frozen):
    """Fail on changed evidence before using frozen baseline recall labels."""
    expected = {c["id"]: c for c in frozen["cases"]}
    if set(expected) != {c["id"] for c in cases}:
        raise ValueError("Frozen case inventory changed")
    for case in cases:
        for key, value in case.items():
            if expected[case["id"]].get(key) != value:
                raise ValueError(f"Frozen case evidence changed: {case['id']} {key}")
    for book, data in books.items():
        old = frozen["books"][book]
        cache_hash = hashlib.sha256(
            json.dumps(data["hashes"], sort_keys=True).encode()
        ).hexdigest()
        if cache_hash != old["cache_manifest_sha256"]:
            raise ValueError(f"Frozen OCR cache changed: {book}")
        if data["ranker"].resources != old["language_resources"]:
            raise ValueError(f"Frozen language resources changed: {book}")
        for key in (
            "excluded_pages",
            "source_sha256",
            "profile_sha256",
            "overlays_sha256",
            "protected_sha256",
            "dictionary_sha256",
        ):
            if data[key] != old[key]:
                raise ValueError(f"Frozen book evidence changed: {book} {key}")
    for name, expected_hash in frozen["imported_helpers_sha256"].items():
        if file_digest(ROOT / "scripts" / name) != expected_hash:
            raise ValueError(f"Frozen helper changed: {name}")
    if (
        file_digest(ROOT / "scripts/statistical_ocr_research.py")
        != frozen["helper_sha256"]
    ):
        raise ValueError("Frozen baseline research helper changed")
    for name, version in frozen["package_versions"].items():
        if importlib.metadata.version(name) != version:
            raise ValueError(f"Frozen package version changed: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=ROOT / "docs/statistical-ocr-evaluation.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "work/simple-ocr-research/evaluation.json"
    )
    args = parser.parse_args()
    frozen = read_json(args.input)
    books, current, _ = load_books()
    verify_frozen(books, current, frozen)
    cases = frozen["cases"]
    dictionary = Dictionary.from_files(str(ROOT / "work/models/en_US"))
    methods = {
        name: {}
        for name in (
            "book_radius3",
            "book_accent1",
            "raw_recurrent2",
            "two_peers",
            "book_trigram",
            "raw_trigram",
        )
    }
    resource_counts = {}
    diagnostics = []
    for book, data in sorted(books.items()):
        bank = data["extra"].words
        raw = {w: n for w, n in data["ranker"].counts.items() if n >= 2}
        indices = {
            "book_radius3": VocabularyIndex(bank, radius=3),
            "book_accent1": VocabularyIndex(bank, radius=1, folded=True),
            "raw_recurrent2": VocabularyIndex(raw),
            "book_trigram": TrigramIndex(bank),
            "raw_trigram": TrigramIndex(raw),
        }
        resource_counts[book] = dict(
            witnessed_terms=len(bank), raw_recurrent_terms=len(raw)
        )
        for case in [c for c in cases if c["book"] == book]:
            for name, index in indices.items():
                methods[name][case["id"]] = sorted(index.lookup(case["before"]))
            peer = peer_candidate(case)
            methods["two_peers"][case["id"]] = [peer] if peer else []
            if case["expected"] == "correct" and not case["book_only_candidate_recall"]:
                diagnostics.append(
                    dict(
                        id=case["id"],
                        book=book,
                        page=case["page"],
                        before=case["before"],
                        after=case["after"],
                        target_in_witnessed_bank=case["after"] in bank,
                        target_in_raw_recurrent_bank=case["after"] in raw,
                        target_has_diacritics=accent_key(case["after"])
                        != case["after"].casefold(),
                        original_dictionary_known=dictionary.lookup(case["before"]),
                        target_dictionary_known=dictionary.lookup(case["after"]),
                        peer_supplies_target=peer == case["after"],
                    )
                )
    union = {
        c["id"]: sorted(
            set(methods["book_accent1"][c["id"]]) | set(methods["two_peers"][c["id"]])
        )
        for c in cases
    }
    methods["accent_and_peers"] = union
    methods["raw_and_peers"] = {
        c["id"]: sorted(
            set(methods["raw_recurrent2"][c["id"]]) | set(methods["two_peers"][c["id"]])
        )
        for c in cases
    }
    result = dict(
        purpose="Candidate availability and selected-case review budgets; no edits, corpus precision or timed savings",
        input_sha256=file_digest(args.input),
        helper_sha256=file_digest(Path(__file__)),
        imported_helpers_sha256={
            name: file_digest(ROOT / "scripts" / name)
            for name in (
                "statistical_ocr_research.py",
                "lexical_repair.py",
                "vocabulary.py",
                "ocr.py",
                "common.py",
                "languages.py",
                "text_edits.py",
            )
        },
        package_versions={
            name: importlib.metadata.version(name)
            for name in ("symspellpy", "spylls", "wordfreq", "numpy")
        },
        resources=resource_counts,
        missing_candidate_diagnostics=diagnostics,
        methods={
            name: {"summary": measure(cases, values), "alternatives": values}
            for name, values in methods.items()
        },
        queue={
            "unknown_first": queue_report(cases, dictionary.lookup, False),
            "peers_first": queue_report(cases, dictionary.lookup, True),
        },
        queue_by_book={
            book: {
                name: queue_report(
                    [c for c in cases if c["book"] == book],
                    dictionary.lookup,
                    peer_first,
                )
                for name, peer_first in (
                    ("unknown_first", False),
                    ("peers_first", True),
                )
            }
            for book in sorted(books)
        },
        unchanged_feedback=suppression_report(cases, dictionary.lookup),
    )
    write_json(args.output, result)
    for name, data in result["methods"].items():
        print(name, data["summary"])
    print("queue", {k: v["budgets"] for k, v in result["queue"].items()})
    print("unchanged_feedback", result["unchanged_feedback"])


if __name__ == "__main__":
    main()
