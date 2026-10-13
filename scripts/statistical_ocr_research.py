"""Offline ablations of classical correction components; never edit a book."""

import argparse
from collections import Counter
import difflib
from functools import lru_cache
import math
import importlib.metadata
import hashlib
import json
from pathlib import Path
import unicodedata

import numpy as np
from spylls.hunspell import Dictionary
from symspellpy import SymSpell, Verbosity
from wordfreq import zipf_frequency

from common import ROOT, cache_path, file_digest, load_profile, read_json, write_json
from languages import dictionary_locale, language_code
from lexical_repair import ContextRanker
from ocr import merge_rows, nearest
from vocabulary import TOKEN
from text_edits import edit_chunks


class CharacterModel:
    """Add-one character trigrams from the pinned general lexicon, not test labels."""

    def __init__(self, words):
        self.counts, self.outgoing = Counter(), Counter()
        alphabet = {"$"}
        for word in sorted(words):
            if not word.isalpha():
                continue
            text = "^^" + word + "$"
            alphabet.update(word)
            for i in range(2, len(text)):
                self.counts[text[i - 2 : i + 1]] += 1
                self.outgoing[text[i - 2 : i]] += 1
        self.alphabet_size = len(alphabet) + 1  # Include unseen-character backoff.

    @lru_cache(maxsize=100000)
    def score(self, word):
        text = "^^" + word + "$"
        return sum(
            math.log(
                (self.counts[text[i - 2 : i + 1]] + 1)
                / (self.outgoing[text[i - 2 : i]] + self.alphabet_size)
            )
            for i in range(2, len(text))
        ) / (len(word) + 1)


def edit_rules(observed, target):
    """Directed candidate-to-observation chunks, excluding whole-token rewrites."""
    for a, b in edit_chunks(target, observed):
        if max(len(a), len(b)) <= 3:
            yield a, b


class ErrorModel:
    def __init__(self, pairs):
        self.counts = Counter()
        self.targets = []
        for observed, target in sorted(set(pairs)):
            self.counts.update(edit_rules(observed, target))
            self.targets.append(target)
        self.costs = {}
        for (target, observed), count in self.counts.items():
            opportunities = sum(
                word.count(target) if target else len(word) + 1 for word in self.targets
            )
            probability = min(1, (count + 0.5) / (opportunities + 1))
            self.costs[target, observed] = -math.log(probability) / math.log(1000)

    @lru_cache(maxsize=100000)
    def distance(self, target, observed):
        # Minimum weighted path, not a probability summed across all paths.
        table = [[math.inf] * (len(observed) + 1) for _ in range(len(target) + 1)]
        table[0][0] = 0
        for i in range(len(target) + 1):
            for j in range(len(observed) + 1):
                value = table[i][j]
                if i < len(target):
                    table[i + 1][j] = min(table[i + 1][j], value + 1)
                if j < len(observed):
                    table[i][j + 1] = min(table[i][j + 1], value + 1)
                if i < len(target) and j < len(observed):
                    table[i + 1][j + 1] = min(
                        table[i + 1][j + 1], value + (target[i] != observed[j])
                    )
                for (a, b), cost in self.costs.items():
                    if target.startswith(a, i) and observed.startswith(b, j):
                        ni, nj = i + len(a), j + len(b)
                        table[ni][nj] = min(table[ni][nj], value + cost)
        return table[-1][-1]

    def variants(self, observed, budget=2, beam=64):
        # Bounded inverse rewrite lattice. Limits also bound insertion/deletion loops.
        best, frontier = {observed: 0.0}, {observed: 0.0}
        rules = sorted(self.costs.items())
        for _ in range(budget):
            new = {}
            for word, score in sorted(frontier.items()):
                for (a, b), cost in rules:
                    positions = range(len(word) + 1) if not b else range(len(word))
                    for i in positions:
                        if word.startswith(b, i):
                            value = word[:i] + a + word[i + len(b) :]
                            total = score + cost
                            if total < best.get(value, math.inf):
                                new[value] = min(total, new.get(value, math.inf))
            frontier = dict(sorted(new.items(), key=lambda x: (x[1], x[0]))[:beam])
            best.update(frontier)
        return best


def aligned_readings(text, before, witnesses):
    own = [m.group().lower() for m in TOKEN.finditer(text)]
    if own.count(before.lower()) != 1:
        return []
    position = own.index(before.lower())
    readings = []
    for witness in witnesses:
        peer = [m.group().lower() for m in TOKEN.finditer(witness)]
        for tag, i, j, k, l in difflib.SequenceMatcher(
            None, own, peer, autojunk=False
        ).get_opcodes():
            if not i <= position < j:
                continue
            if tag == "equal":
                readings.append(peer[k + position - i])
            elif j - i == l - k == 1:
                anchors = sum(
                    own[i - d] == peer[k - d] and len(own[i - d]) >= 2
                    for d in (1, 2)
                    if i >= d and k >= d
                ) + sum(
                    own[j + d] == peer[l + d] and len(own[j + d]) >= 2
                    for d in (0, 1)
                    if j + d < len(own) and l + d < len(peer)
                )
                if anchors >= 2:
                    readings.append(peer[k])
    return readings


def load_books():
    books, cases, skipped = {}, [], Counter()
    for spec in read_json(ROOT / "tests/book-cases.json"):
        if spec.get("editorial"):
            continue
        book = load_profile(ROOT / spec["profile"])
        if language_code(book) != "en" or book.get("text_source") == "native":
            continue
        paths = {
            e: cache_path(ROOT / spec["work"], e)
            for e in ("tesseract", "vision", "rapid")
        }
        pages, peers, hashes = {}, {}, {}
        for path in sorted(paths["tesseract"].glob("[0-9][0-9][0-9][0-9].json")):
            n = int(path.stem)
            primary = (
                "vision" if n in book.get("vision_primary_pages", []) else "tesseract"
            )
            data = {}
            for engine, folder in paths.items():
                file = folder / path.name
                hashes[str(file.relative_to(ROOT))] = file_digest(file)
                data[engine] = merge_rows(read_json(file)["lines"])
            pages[n] = data[primary]
            peers[n] = [data[e] for e in paths if e != primary]
        profile = ROOT / spec["profile"]
        overlays = read_json(profile.with_name("corrections.json"))
        current = []
        for index, edit in enumerate(overlays):
            reason = edit.get("evidence", edit.get("reason", ""))
            if not reason or "editorial" in str(edit).lower():
                continue
            if edit["page"] in book.get("reference_pages", []) + book.get(
                "index_pages", []
            ):
                skipped["reference-or-index"] += 1
                continue
            a, b = TOKEN.findall(edit["before"]), TOKEN.findall(edit["after"])
            ops = [
                op
                for op in difflib.SequenceMatcher(
                    None, a, b, autojunk=False
                ).get_opcodes()
                if op[0] != "equal"
            ]
            if len(ops) != 1:
                skipped["not-one-token"] += 1
                continue
            tag, i, j, k, l = ops[0]
            if tag != "replace" or j - i != 1 or l - k != 1:
                skipped["not-one-token"] += 1
                continue
            before, after = a[i], b[k]
            if (
                not before.isalpha()
                or not after.isalpha()
                or min(len(before), len(after)) < 3
                or before.lower() == after.lower()
            ):
                skipped["nonalphabetic-short-or-case-only"] += 1
                continue
            if any("LATIN" not in unicodedata.name(c, "") for c in before + after):
                skipped["non-latin"] += 1
                continue
            # A line-ending prefix is not a complete lexical correction target.
            target_matches = [
                m for m in TOKEN.finditer(edit["after"]) if m.group() == after
            ]
            if len(target_matches) != 1 or edit["after"][
                target_matches[0].end() :
            ].startswith("-"):
                skipped["wrap-fragment"] += 1
                continue
            matches = [
                r
                for r in pages.get(edit["page"], [])
                if edit["before"] in r["text"]
                and TOKEN.findall(r["text"]).count(before) == 1
            ]
            if len(matches) != 1:
                skipped["absent-or-ambiguous-primary-row"] += 1
                continue
            row = matches[0]
            ordered = pages[edit["page"]]
            position = ordered.index(row)
            if (
                TOKEN.findall(row["text"])[0] == before
                and position
                and ordered[position - 1]["text"].rstrip().endswith("-")
            ):
                skipped["wrap-fragment"] += 1
                continue
            witnesses = [nearest(row, p) for p in peers[edit["page"]]]
            readings = aligned_readings(
                row["text"], before, [w["text"] for w in witnesses if w]
            )
            current.append(
                dict(
                    id=f'{spec["name"]}-{index}',
                    book=spec["name"],
                    page=edit["page"],
                    bbox=row["bbox"],
                    before=before.lower(),
                    after=after.lower(),
                    context=row["text"].lower(),
                    readings=readings,
                    expected="correct",
                    lowercase=before.islower() and after.islower(),
                    provenance=dict(
                        profile=spec["profile"], overlay_index=index, evidence=reason
                    ),
                )
            )
        if not current:
            continue
        # Remove every benchmark page before building priors; use raw OCR elsewhere.
        excluded_pages = {c["page"] for c in current}
        model, trigrams, skips = [], Counter(), Counter()
        for page, rows in pages.items():
            if page in excluded_pages or page in book.get(
                "reference_pages", []
            ) + book.get("index_pages", []):
                continue
            for row in rows:
                words = [w.lower() for w in TOKEN.findall(row["text"])]
                trigrams.update(zip(words, words[1:], words[2:]))
                skips.update(zip(words, words[2:]))
                model.append(
                    dict(
                        chapter=page,
                        source_pages=[page],
                        blocks=[dict(text=row["text"])],
                        notes=[],
                    )
                )
        dictionary = Dictionary.from_files(
            str(ROOT / "work/models" / dictionary_locale(book))
        )
        protected_file = profile.with_name("protected-words.txt")
        protected = [
            w
            for w in protected_file.read_text().splitlines()
            if w and not w.startswith("#")
        ]
        ranker = ContextRanker(
            model, book, dictionary, protected + book.get("line_join_words", [])
        )
        # Only add recurrent tokens matched by both cached peer engines.
        attested = Counter()
        for n, rows in pages.items():
            if n in excluded_pages:
                continue
            for row in rows:
                ws = [nearest(row, source) for source in peers[n]]
                if not all(ws):
                    continue
                common = set(TOKEN.findall(row["text"].lower()))
                for witness in ws:
                    common &= set(TOKEN.findall(witness["text"].lower()))
                attested.update(w for w in common if w.isalpha() and len(w) >= 3)
        extra = SymSpell(max_dictionary_edit_distance=2)
        for word, count in attested.items():
            if count >= 2:
                extra.create_dictionary_entry(word, count)
        source_hash = file_digest(ROOT / spec["pdf"])
        if source_hash != book["source_sha256"]:
            raise ValueError("Reviewed overlays no longer match the source PDF")
        books[spec["name"]] = dict(
            ranker=ranker,
            extra=extra,
            trigrams=trigrams,
            skips=skips,
            hashes=hashes,
            excluded_pages=sorted(excluded_pages),
            source_sha256=source_hash,
            profile_sha256=file_digest(profile),
            overlays_sha256=file_digest(profile.with_name("corrections.json")),
            protected_sha256=file_digest(protected_file),
            dictionary_sha256={
                suffix: file_digest(
                    ROOT / "work/models" / (dictionary_locale(book) + suffix)
                )
                for suffix in (".aff", ".dic")
            },
        )
        seen = set()
        for case in current:
            pair = case["before"], case["after"]
            if pair in seen:
                continue
            seen.add(pair)
            cases.append(case)
            # Paired counterfactual input: the printed correct token must survive.
            matches = list(TOKEN.finditer(case["context"]))
            match = next(m for m in matches if m.group() == case["before"])
            control = dict(
                case,
                id=case["id"] + "-retain",
                before=case["after"],
                expected="retain",
                context=case["context"][: match.start()]
                + case["after"]
                + case["context"][match.end() :],
            )
            if TOKEN.findall(control["context"]).count(control["before"]) == 1:
                cases.append(control)
    return books, cases, skipped


def candidate_pool(word, ranker, extra, error, expanded):
    pool = {word}
    pool.update(c.term for c in ranker.sym.lookup(word, Verbosity.ALL, 2))
    if expanded:
        pool.update(c.term for c in extra.lookup(word, Verbosity.ALL, 2))
        for variant in error.variants(word):
            pool.update(c.term for c in ranker.sym.lookup(variant, Verbosity.ALL, 0))
            pool.update(c.term for c in extra.lookup(variant, Verbosity.ALL, 0))
    return sorted(pool)


def features(case, candidate, data, error):
    ranker = data["ranker"]
    words = TOKEN.findall(case["context"])
    i = words.index(case["before"])
    left, right = (words[i - 1] if i else None), (
        words[i + 1] if i + 1 < len(words) else None
    )
    bigram = sum(
        math.log(max(ranker.transition(a, b), 1e-30))
        for a, b in ((left, candidate), (candidate, right))
        if a and b
    )
    tri = math.log1p(data["trigrams"][left, candidate, right]) if left and right else 0
    skip = sum(
        math.log1p(data["skips"][a, b])
        for a, b in (
            (words[i - 2] if i >= 2 else None, candidate),
            (candidate, words[i + 2] if i + 2 < len(words) else None),
        )
        if a and b
    )
    return [
        -error.distance(candidate, case["before"]),
        zipf_frequency(candidate, "en"),
        bigram,
        tri,
        skip,
        case["readings"].count(candidate),
        math.log1p(ranker.counts[candidate]),
        float(candidate == case["before"]),
        data["character_model"].score(candidate),
    ]


def fit_ridge(groups):
    xs, ys, weights = [], [], []
    for case, values in groups:
        for candidate, vector in values.items():
            xs.append(vector)
            ys.append(float(candidate == case["after"]))
            weights.append(1 / len(values))
    x, y, w = np.asarray(xs), np.asarray(ys), np.asarray(weights)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1
    design = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    regularizer = np.eye(design.shape[1])
    regularizer[0, 0] = 0
    coefficients = np.linalg.solve(
        design.T @ (design * w[:, None]) + regularizer, design.T @ (y * w)
    )
    return mean, scale, coefficients


def ridge_score(vector, fit):
    mean, scale, coefficients = fit
    return float(np.r_[1, (np.asarray(vector) - mean) / scale] @ coefficients)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "work/ocr-literature/evaluation.json"
    )
    args = parser.parse_args()
    books, cases, skipped = load_books()
    if len(books) < 2 or not cases:
        raise ValueError("Evaluation requires reviewed cases from multiple books")
    character_model = CharacterModel(next(iter(books.values()))["ranker"].sym.words)
    for data in books.values():
        data["character_model"] = character_model
        data["language_resources"] = data["ranker"].resources
    results, folds = [], []
    for held in sorted(books):
        train = [c for c in cases if c["book"] != held]
        error = ErrorModel(
            (c["before"], c["after"]) for c in train if c["expected"] == "correct"
        )
        groups = []
        for case in train:
            pool = candidate_pool(
                case["before"],
                books[case["book"]]["ranker"],
                books[case["book"]]["extra"],
                error,
                True,
            )
            values = {c: features(case, c, books[case["book"]], error) for c in pool}
            groups.append((case, values))
        fit = fit_ridge(groups)
        thresholds = []
        for case, values in groups:
            scores = {c: ridge_score(v, fit) for c, v in values.items()}
            choice = min(scores, key=lambda c: (-scores[c], c))
            thresholds.append(
                (
                    scores[choice] - scores[case["before"]],
                    choice == case["after"],
                    choice != case["before"],
                )
            )
        # Training-only gate: best coverage with no observed wrong replacements.
        viable = []
        for threshold in sorted(
            {0.0} | {m + 1e-9 for m, _, changed in thresholds if changed}
        ):
            accepted = [
                (correct, changed)
                for margin, correct, changed in thresholds
                if margin > threshold and changed
            ]
            if not any(not correct for correct, _ in accepted):
                viable.append((sum(correct for correct, _ in accepted), threshold))
        gate = min(viable, key=lambda x: (-x[0], x[1]))[1]
        folds.append(
            dict(
                held_out_book=held,
                training_pairs=len(error.targets),
                rules=[
                    dict(target=a, observed=b, count=error.counts[a, b], cost=cost)
                    for (a, b), cost in sorted(error.costs.items())
                ],
                gate=gate,
                coefficients=fit[2].tolist(),
            )
        )
        for case in [c for c in cases if c["book"] == held]:
            data = books[held]
            ranker = data["ranker"]
            base = candidate_pool(case["before"], ranker, data["extra"], error, False)
            book_only = candidate_pool(
                case["before"], ranker, data["extra"], ErrorModel([]), True
            )
            pool = candidate_pool(case["before"], ranker, data["extra"], error, True)
            values = {c: features(case, c, data, error) for c in pool}
            scoring = {
                "unit_edit": {
                    c: -2 * ErrorModel([]).distance(c, case["before"])
                    + zipf_frequency(c, "en")
                    for c in base
                },
                "weighted_edit": {c: 2 * values[c][0] + values[c][1] for c in base},
                "weighted_context": {
                    c: 2 * values[c][0] + values[c][1] + values[c][2] for c in base
                },
                "expanded_context": {
                    c: 2 * v[0] + v[1] + v[2] + v[3] + 0.5 * v[4]
                    for c, v in values.items()
                },
                "book_context": {
                    c: 2 * values[c][0]
                    + values[c][1]
                    + values[c][2]
                    + values[c][3]
                    + 0.5 * values[c][4]
                    for c in book_only
                },
                "consensus_context": {
                    c: 2 * v[0] + v[1] + v[2] + v[3] + 0.5 * v[4] + 2 * v[5]
                    for c, v in values.items()
                },
                "character_context": {
                    c: 2 * v[0]
                    + v[1]
                    + v[2]
                    + v[3]
                    + 0.5 * v[4]
                    + 2 * v[5]
                    + 0.2 * v[8]
                    for c, v in values.items()
                },
                "ridge": {c: ridge_score(v, fit) for c, v in values.items()},
            }
            rankings = {
                method: sorted(scores, key=lambda c: (-scores[c], c))
                for method, scores in scoring.items()
            }
            best = rankings["ridge"][0]
            margin = scoring["ridge"][best] - scoring["ridge"][case["before"]]
            results.append(
                dict(
                    **case,
                    baseline_candidate_recall=case["after"] in base,
                    expanded_candidate_recall=case["after"] in pool,
                    book_only_candidate_recall=case["after"] in book_only,
                    pool_size=len(pool),
                    rankings={m: cs[:3] for m, cs in rankings.items()},
                    ridge_gate_output=best if margin > gate else case["before"],
                    ridge_margin=margin,
                    source_gate_output=(
                        rankings["consensus_context"][0]
                        if case["lowercase"]
                        and not ranker.known(case["before"])
                        and case["readings"].count(rankings["consensus_context"][0])
                        == 2
                        else case["before"]
                    ),
                )
            )
        print(f"Evaluated {held}", flush=True)
    summary = {}
    for method in results[0]["rankings"]:
        summary[method] = {
            kind: dict(
                total=sum(r["expected"] == kind for r in results),
                top1=sum(
                    r["expected"] == kind and r["rankings"][method][0] == r["after"]
                    for r in results
                ),
                top3=sum(
                    r["expected"] == kind and r["after"] in r["rankings"][method]
                    for r in results
                ),
            )
            for kind in ("correct", "retain")
        }
    summary["ridge_gate"] = {
        kind: dict(
            total=sum(r["expected"] == kind for r in results),
            exact=sum(
                r["expected"] == kind and r["ridge_gate_output"] == r["after"]
                for r in results
            ),
            changed=sum(
                r["expected"] == kind and r["ridge_gate_output"] != r["before"]
                for r in results
            ),
            wrong_changes=sum(
                r["expected"] == kind
                and r["ridge_gate_output"] not in {r["after"], r["before"]}
                for r in results
            ),
        )
        for kind in ("correct", "retain")
    }
    summary["source_gate"] = {
        kind: dict(
            total=sum(r["expected"] == kind for r in results),
            exact=sum(
                r["expected"] == kind and r["source_gate_output"] == r["after"]
                for r in results
            ),
            changed=sum(
                r["expected"] == kind and r["source_gate_output"] != r["before"]
                for r in results
            ),
            wrong_changes=sum(
                r["expected"] == kind
                and r["source_gate_output"] not in {r["after"], r["before"]}
                for r in results
            ),
        )
        for kind in ("correct", "retain")
    }
    write_json(
        args.output,
        dict(
            purpose="research-only component ablation, not paper replication or calibrated probabilities",
            feature_names=[
                "negative_weighted_edit",
                "zipf_frequency",
                "bigram_log_score",
                "book_trigram_log_count",
                "book_skipgram_log_count",
                "peer_agreement",
                "book_log_count",
                "original_candidate",
                "mean_character_trigram_log_score",
            ],
            helper_sha256=file_digest(Path(__file__)),
            imported_helpers_sha256={
                name: file_digest(ROOT / "scripts" / name)
                for name in (
                    "common.py",
                    "languages.py",
                    "lexical_repair.py",
                    "ocr.py",
                    "vocabulary.py",
                    "text_edits.py",
                )
            },
            book_cases_sha256=file_digest(ROOT / "tests/book-cases.json"),
            package_versions={
                name: importlib.metadata.version(name)
                for name in ("numpy", "symspellpy", "wordfreq", "spylls")
            },
            cases=results,
            folds=folds,
            summary=summary,
            skipped=dict(skipped),
            books={
                name: {
                    "cache_manifest_sha256": hashlib.sha256(
                        json.dumps(data["hashes"], sort_keys=True).encode()
                    ).hexdigest(),
                    "cache_files": len(data["hashes"]),
                    **{
                        k: v
                        for k, v in data.items()
                        if k
                        not in {
                            "ranker",
                            "extra",
                            "trigrams",
                            "skips",
                            "character_model",
                            "hashes",
                        }
                    },
                }
                for name, data in books.items()
            },
        ),
    )
    print(summary)


if __name__ == "__main__":
    main()
