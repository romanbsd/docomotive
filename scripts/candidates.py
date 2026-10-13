"""Local, suggestion-only lexical review. Never mutates the book."""

import argparse
import importlib.metadata
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit
import symspellpy
from symspellpy import SymSpell, Verbosity
from spylls.hunspell import Dictionary
from wordfreq import zipf_frequency
from common import ROOT, digest, write_json, load_profile
from vocabulary import TOKEN, analyze, review_signals
from languages import language_code, dictionary_locale
from review_candidates import ReviewWitnesses


def merge_suggestions(suggestions, proposals, *, front=False):
    """Merge evidence for a term without losing its earlier generators."""
    indexed = {c["term"]: c for c in suggestions}
    for proposal in proposals:
        old = indexed.get(proposal["term"])
        if old is not None:
            generators = set(old.get("generators", ["general-lexicon"])) | set(
                proposal["generators"]
            )
            old.update(proposal)
            old["generators"] = sorted(generators)
        else:
            candidate = dict(proposal)
            if front:
                suggestions.insert(0, candidate)
            else:
                suggestions.append(candidate)
            indexed[candidate["term"]] = candidate


def merge_review_items(results, proposals):
    indexed = {r["word"]: r for r in results}
    for item in proposals:
        existing = indexed.get(item["word"])
        if existing is None:
            results.append(item)
            indexed[item["word"]] = item
            continue

        def location(o):
            return o["chapter"], o["paragraph"], o["offset"]

        occurrences = {location(o): o for o in existing["occurrences"]}
        for occurrence in item["occurrences"]:
            old = occurrences.get(location(occurrence))
            if old is not None:
                old.update(occurrence)
            else:
                existing["occurrences"].append(occurrence)
                occurrences[location(occurrence)] = occurrence
        merge_suggestions(existing["suggestions"], item["suggestions"], front=True)


def exclusion_reason(word, dictionary, protected, sym, language):
    """Shared production lexical detector; peer disagreements bypass this filter."""
    low = word.lower()
    if len(word) < 4:
        return "short-token"
    if low in protected:
        return "protected"
    if dictionary.lookup(word) or dictionary.lookup(low):
        return "dictionary-known"
    if all(dictionary.lookup(w) or w in protected for w in low.split("-")):
        return "known-hyphen-parts"
    if zipf_frequency(low, language) >= 2:
        return "frequency-known"
    if sym.lookup(low, Verbosity.TOP, max_edit_distance=0):
        return "frequency-lexicon-known"
    return None


def lexical_suggestions(word, dictionary, sym):
    choices = sym.lookup(word, Verbosity.CLOSEST, max_edit_distance=2)
    suggestions = [
        dict(
            term=s.term,
            distance=s.distance,
            frequency=s.count,
            hunspell_accepts=dictionary.lookup(s.term),
        )
        for s in choices[:5]
    ]
    compound = sym.lookup_compound(word, max_edit_distance=2)
    if compound and compound[0].term != word and " " in compound[0].term:
        suggestions.append(
            dict(
                term=compound[0].term,
                distance=compound[0].distance,
                kind="split-or-join",
            )
        )
    return suggestions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kenlm-model",
        type=Path,
        help="Optional pinned local binary/ARPA model; suggestion ranking only",
    )
    parser.add_argument(
        "--languagetool-url", help="Optional local LanguageTool server URL"
    )
    parser.add_argument("--profile", type=Path, default=ROOT / "config/book.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    parser.add_argument("--models", type=Path, default=ROOT / "work/models")
    parser.add_argument(
        "--feedback",
        type=Path,
        help="Latest source-checked decision export; advisory feedback on later pages only",
    )
    parser.add_argument(
        "--work",
        type=Path,
        help="Source-bound OCR caches for witnessed review proposals",
    )
    args = parser.parse_args()
    book = load_profile(args.profile)
    config = args.profile.parent
    language = language_code(book)
    locale = dictionary_locale(book)
    dictionary = Dictionary.from_files(str(args.models / locale))
    protected = {
        s.lower()
        for s in (config / "protected-words.txt").read_text().splitlines()
        if s and not s.startswith("#")
    }
    protected.update(book.get("line_join_words", []))
    frequency = Path(symspellpy.__file__).parent / "frequency_dictionary_en_82_765.txt"
    sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    if language == "en":
        if not sym.load_dictionary(str(frequency), 0, 1):
            raise ValueError("SymSpell dictionary failed to load")
    else:
        from wordfreq import top_n_list

        # A pinned language-frequency resource supplies suggestions; book
        # recurrence can prioritize review but cannot prove correct spelling.
        for word in top_n_list(language, 50000):
            if word.isalpha():
                sym.create_dictionary_entry(
                    word, max(1, round(10 ** zipf_frequency(word, language)))
                )
        frequency = (
            Path(__import__("wordfreq").__file__).parent
            / "data"
            / f"large_{language}.msgpack.gz"
        )
    for word in sorted(protected):
        sym.create_dictionary_entry(word, 1)
    lm = None
    if args.kenlm_model:
        import kenlm

        lm = kenlm.Model(str(args.kenlm_model))
    if args.languagetool_url and urlsplit(args.languagetool_url).hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("LanguageTool adapter requires a local server")
    unknown = defaultdict(list)
    grammar = []
    model = json.loads((args.output / "book-model.json").read_text())
    witnesses = ReviewWitnesses(args.work, book) if args.work else None
    if witnesses:
        witnesses.collect(model)
    statistics = analyze(model, book)
    ranker = None
    if book.get("repair_lexical_confusions"):
        from lexical_repair import ContextRanker

        ranker = ContextRanker(model, book, dictionary, protected)
    write_json(args.output / "vocabulary-analysis.json", statistics)
    for entry in model:
        chapter = entry["chapter"]
        for index, node in enumerate(entry["blocks"] + entry["notes"]):
            if node["kind"] == "source-gap":
                continue  # Editorial source notices are not OCR observations.
            if (
                entry["source_pages"][0] in book.get("reference_pages", [])
                and "source_chapter" not in node
            ):
                continue
            chapter = node.get("source_chapter", entry["chapter"])
            text = node["text"]
            if args.languagetool_url:
                import requests

                response = requests.post(
                    args.languagetool_url.rstrip("/") + "/v2/check",
                    data={"language": book["language"], "text": text},
                    timeout=60,
                )
                response.raise_for_status()
                for match in response.json()["matches"]:
                    grammar.append(
                        {
                            "chapter": chapter,
                            "paragraph": index,
                            "context": text,
                            "match": match,
                        }
                    )
            for match in TOKEN.finditer(text):
                word = match.group()
                low = word.lower()
                if exclusion_reason(word, dictionary, protected, sym, language):
                    continue
                unknown[low].append(
                    {
                        "chapter": chapter,
                        "paragraph": index,
                        "context": text,
                        "offset": match.start(),
                        "original": word,
                        "block_kind": node["kind"],
                        "sources": [
                            source
                            for source in node["sources"]
                            if source["end"] > match.start()
                            and source["start"] < match.end()
                        ],
                    }
                )
    results = []
    for word, occurrences in sorted(unknown.items()):
        suggestions = lexical_suggestions(word, dictionary, sym)
        if lm:
            context = occurrences[0]["context"]
            for candidate in suggestions:
                candidate["kenlm_log10_delta"] = lm.score(
                    context.replace(word, candidate["term"]), bos=True, eos=True
                ) - lm.score(context, bos=True, eos=True)
            suggestions.sort(
                key=lambda c: (-c.get("kenlm_log10_delta", 0), c["distance"], c["term"])
            )
        if ranker and not lm:
            occurrence = occurrences[0]
            ranked = {
                c["term"]: c
                for c in ranker.rank(word, occurrence["context"], occurrence["offset"])
            }
            for candidate in suggestions:
                if candidate["term"] in ranked:
                    candidate.update(ranked[candidate["term"]])
            suggestions.sort(key=lambda c: (-c.get("context_score", -1e30), c["term"]))
        if witnesses:
            merge_suggestions(suggestions, witnesses.suggestions(word, occurrences))
        results.append(
            {
                "word": word,
                "occurrences": occurrences,
                "suggestions": suggestions,
                "category": (
                    "capitalized-term"
                    if all(o["original"][0].isupper() for o in occurrences)
                    else "lexical"
                ),
                "action": "scan-review-required",
                **review_signals(word, statistics),
            }
        )
    if witnesses:
        merge_review_items(results, witnesses.disagreements(model))
    if args.feedback:
        from review_feedback import FeedbackLedger

        ledger = FeedbackLedger(
            json.loads(args.feedback.read_text()),
            json.loads((args.output / "corrected-pages.json").read_text()),
            book["source_sha256"],
            digest((args.output / "book-model.json").read_bytes()),
            digest(args.profile.read_bytes()),
        )
        for item in results:
            ledger.annotate(item)
    results.sort(key=lambda item: (item["review_priority"], item["word"]))
    write_json(
        args.output / "protected-term-proposals.json",
        {
            "mode": "scan-review-required; no automatic protection",
            "terms": [
                r for r in results if r["recommendation"] == "consider-protecting"
            ],
        },
    )
    manifest = {
        "mode": "suggestion-only",
        "symspellpy": importlib.metadata.version("symspellpy"),
        "spylls": importlib.metadata.version("spylls"),
        "dictionary_sha256": {
            p.name: digest(p.read_bytes())
            for p in [
                frequency,
                args.models / (locale + ".aff"),
                args.models / (locale + ".dic"),
                config / "protected-words.txt",
            ]
        },
        "kenlm_model_sha256": (
            digest(args.kenlm_model.read_bytes()) if args.kenlm_model else None
        ),
        "languagetool": args.languagetool_url,
        "context_ranking": ranker.resources if ranker else None,
        "review_witnesses": witnesses.resources if witnesses else None,
        "witnessed_book_vocabulary": len(witnesses.sym.words) if witnesses else 0,
        "unknown_words": len(unknown),
        "review_items": len(results),
        "candidates": results,
        "grammar": grammar,
    }
    if args.feedback:
        manifest["feedback"] = dict(
            export_sha256=digest(args.feedback.read_bytes()),
            mode="advisory; earlier pages only; original items retained",
            confirmed_records=len(ledger.records),
        )
    write_json(args.output / "correction-candidates.json", manifest)
    print(f"{len(results)} distinct words for review; book unchanged")


if __name__ == "__main__":
    main()
