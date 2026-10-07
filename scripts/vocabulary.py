"""Deterministic whole-book statistics over assembled text; never changes spelling."""

import argparse
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from wordfreq import zipf_frequency
from common import ROOT, read_json, write_json, load_profile
from languages import language_code

TOKEN = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)


def analyze(model, book):
    terms = defaultdict(
        lambda: {
            "forms": Counter(),
            "chapters": Counter(),
            "pages": set(),
            "blocks": set(),
            "reference_count": 0,
        }
    )
    documents = []
    total = 0
    for entry in model:
        chapter = entry["chapter"]
        reference = entry["source_pages"][0] in book.get("reference_pages", [])
        if not reference and chapter not in book.get(
            "statistics_excluded_chapters", []
        ):
            documents.append(chapter)
        for index, block in enumerate(entry["blocks"] + entry["notes"]):
            if block.get("kind") == "source-gap":
                continue
            effective_chapter = block.get("source_chapter", chapter)
            effective_reference = reference and "source_chapter" not in block
            for match in TOKEN.finditer(block["text"]):
                value = match.group()
                term = terms[value.lower()]
                if effective_reference or effective_chapter in book.get(
                    "statistics_excluded_chapters", []
                ):
                    term["reference_count"] += 1
                    continue
                total += 1
                term["forms"][value] += 1
                term["chapters"][effective_chapter] += 1
                term["blocks"].add((chapter, index))
                term["pages"].update(
                    s["page"]
                    for s in block["sources"]
                    if s["end"] > match.start() and s["start"] < match.end()
                )
    result = {}
    n = len(documents)
    for word, term in sorted(terms.items()):
        count = sum(term["forms"].values())
        df = len(term["chapters"])
        idf = math.log((n + 1) / (df + 1)) + 1
        prior = zipf_frequency(word, language_code(book))
        # Zipf zero is a censored/unknown prior, not proof of corpus absence.
        expected = total * 10 ** (prior - 9)
        result[word] = {
            "count": count,
            "chapter_df": df,
            "page_df": len(term["pages"]),
            "block_df": len(term["blocks"]),
            "pages": sorted(term["pages"]),
            "chapter_counts": dict(sorted(term["chapters"].items())),
            "forms": dict(sorted(term["forms"].items())),
            "reference_count": term["reference_count"],
            "chapter_idf": round(idf, 6),
            "max_chapter_tfidf": round(
                max(
                    ((1 + math.log(tf)) * idf for tf in term["chapters"].values()),
                    default=0,
                ),
                6,
            ),
            "general_zipf": prior,
            "general_prior_censored": prior == 0,
            "log10_book_enrichment": round(
                math.log10((count + 0.5) / (expected + 0.5)), 6
            ),
        }
    return {
        "stage": "after-book-assembly",
        "mode": "evidence-only; no automatic protection or correction",
        "documents": n,
        "token_count": total,
        "idf_definition": "ln((narrative_chapters+1)/(chapter_df+1))+1",
        "reference_policy": "references/index counted separately; footnotes belong to their narrative chapter",
        "enrichment_definition": "log10((book_count+0.5)/(tokens*10^(general_zipf-9)+0.5)); approximate language prior",
        "terms": result,
    }


def edit_distance_one(a, b):
    """Insertion, deletion, substitution or adjacent transposition; bounded lookup."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    if len(a) == len(b):
        differences = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(differences) == 1 or (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and a[differences[0]] == b[differences[1]]
            and a[differences[1]] == b[differences[0]]
        )
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), len(a))
    return a[i:] == b[i + 1 :]


def review_signals(word, stats):
    term = stats["terms"][word]
    variants = [
        {"term": other, "count": data["count"]}
        for other, data in stats["terms"].items()
        if len(word) >= 4
        and data["count"] >= max(3, term["count"] * 3)
        and edit_distance_one(word, other)
    ]
    variants.sort(key=lambda v: (-v["count"], v["term"]))
    dispersed = (
        term["count"] >= 3
        and term["page_df"] >= 2
        and (term["chapter_df"] >= 2 or term["page_df"] >= 3)
    )
    reasons = []
    if dispersed:
        reasons.append(
            "repeated on multiple pages and chapters or at least three pages"
        )
    if term["reference_count"]:
        reasons.append("also occurs in references or index")
    if variants:
        reasons.append(
            "a more frequent nearby spelling occurs in this book; inspect the scan"
        )
    proposal = dispersed and not variants
    return {
        "statistics": term,
        "in_book_variants": variants[:5],
        "recommendation": "consider-protecting" if proposal else "inspect-spelling",
        "automatic_protection": False,
        "reasons": reasons,
        "review_priority": 0 if variants else (2 if proposal else 1),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", type=Path, default=ROOT / "config/book.json")
    p.add_argument("--output", type=Path, default=ROOT / "output")
    a = p.parse_args()
    result = analyze(read_json(a.output / "book-model.json"), load_profile(a.profile))
    write_json(a.output / "vocabulary-analysis.json", result)
    print(
        f"Whole-book vocabulary: {result['token_count']} tokens across {result['documents']} narrative chapters"
    )


if __name__ == "__main__":
    main()
