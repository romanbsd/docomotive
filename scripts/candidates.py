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
    args = parser.parse_args()
    book = load_profile(args.profile)
    config = args.profile.parent
    dictionary = Dictionary.from_files(str(args.models / "en_US"))
    protected = {
        s.lower()
        for s in (config / "protected-words.txt").read_text().splitlines()
        if s and not s.startswith("#")
    }
    protected.update(book.get("line_join_words", []))
    frequency = Path(symspellpy.__file__).parent / "frequency_dictionary_en_82_765.txt"
    sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    if not sym.load_dictionary(str(frequency), 0, 1):
        raise ValueError("SymSpell dictionary failed to load")
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
    statistics = analyze(model, book)
    write_json(args.output / "vocabulary-analysis.json", statistics)
    for entry in model:
        chapter = entry["chapter"]
        for index, node in enumerate(entry["blocks"] + entry["notes"]):
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
                    data={"language": "en-US", "text": text},
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
                if (
                    len(word) < 4
                    or low in protected
                    or dictionary.lookup(word)
                    or dictionary.lookup(low)
                ):
                    continue
                if all(dictionary.lookup(w) or w in protected for w in low.split("-")):
                    continue
                if zipf_frequency(low, "en") >= 2:
                    continue
                exact = sym.lookup(low, Verbosity.TOP, max_edit_distance=0)
                if exact:
                    continue  # already recognized by the pinned frequency lexicon
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
        choices = sym.lookup(word, Verbosity.CLOSEST, max_edit_distance=2)
        suggestions = [
            {
                "term": s.term,
                "distance": s.distance,
                "frequency": s.count,
                "hunspell_accepts": dictionary.lookup(s.term),
            }
            for s in choices[:5]
        ]
        compound = sym.lookup_compound(word, max_edit_distance=2)
        if compound and compound[0].term != word and " " in compound[0].term:
            suggestions.append(
                {
                    "term": compound[0].term,
                    "distance": compound[0].distance,
                    "kind": "split-or-join",
                }
            )
        if lm:
            context = occurrences[0]["context"]
            for candidate in suggestions:
                candidate["kenlm_log10_delta"] = lm.score(
                    context.replace(word, candidate["term"]), bos=True, eos=True
                ) - lm.score(context, bos=True, eos=True)
            suggestions.sort(
                key=lambda c: (-c.get("kenlm_log10_delta", 0), c["distance"], c["term"])
            )
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
                args.models / "en_US.aff",
                args.models / "en_US.dic",
                config / "protected-words.txt",
            ]
        },
        "kenlm_model_sha256": (
            digest(args.kenlm_model.read_bytes()) if args.kenlm_model else None
        ),
        "languagetool": args.languagetool_url,
        "unknown_words": len(results),
        "candidates": results,
        "grammar": grammar,
    }
    write_json(args.output / "correction-candidates.json", manifest)
    print(f"{len(results)} distinct words for review; book unchanged")


if __name__ == "__main__":
    main()
