"""Evaluate shipped review rules on unrepaired cached rows, without editing books."""

import argparse
from collections import Counter
import importlib.metadata
from pathlib import Path

from spylls.hunspell import Dictionary
from symspellpy import SymSpell
import symspellpy

from candidates import exclusion_reason, lexical_suggestions, merge_suggestions
from common import ROOT, cache_path, file_digest, load_profile, read_json, write_json
from languages import dictionary_locale, language_code
from ocr import merge_rows
from review_candidates import ReviewWitnesses
from simple_ocr_research import verify_frozen
from statistical_ocr_research import load_books
from vocabulary import TOKEN


def row_model(book, page, rows):
    """Raw source-linked fixtures, with production chapter/page exclusions."""
    chapters = [
        (i, title)
        for i, (start, end, title) in enumerate(book["chapters"], 1)
        if start <= page <= end
    ]
    if not chapters:
        return []
    if len(chapters) != 1:
        raise ValueError(f"Ambiguous chapter for page {page}")
    chapter, title = chapters[0]
    return [
        dict(
            chapter=chapter,
            title=title,
            source_pages=[page, page],
            notes=[],
            blocks=[
                dict(
                    kind="text",
                    text=r["text"],
                    sources=[
                        dict(
                            r,
                            page=page,
                            row_id=f"raw-{page}-{i}",
                            start=0,
                            end=len(r["text"]),
                        )
                    ],
                )
                for i, r in enumerate(rows)
            ],
        )
    ]


def evaluate_case(case, source, bank, dictionary, protected, sym, language):
    text = source["text"]
    matches = [m for m in TOKEN.finditer(text) if m.group().lower() == case["before"]]
    if len(matches) != 1:
        raise ValueError(f"Ambiguous case token: {case['id']}")
    token = matches[0]
    original = token.group()
    source = dict(source, start=0, end=len(text))
    occurrence = dict(
        chapter=1,
        paragraph=0,
        context=text,
        offset=token.start(),
        original=original,
        block_kind="text",
        sources=[source],
    )
    exclusion = exclusion_reason(original, dictionary, protected, sym, language)
    suggestions = []
    if exclusion is None:
        suggestions = lexical_suggestions(case["before"], dictionary, sym)
        merge_suggestions(suggestions, bank.suggestions(case["before"], [occurrence]))
    fixture = [
        dict(
            chapter=1, notes=[], blocks=[dict(kind="text", text=text, sources=[source])]
        )
    ]
    for item in bank.disagreements(fixture):
        for found in item["occurrences"]:
            if found["offset"] == token.start() and found["original"] == original:
                target = found["witness_proposal"].lower()
                merge_suggestions(
                    suggestions,
                    [
                        dict(
                            term=target,
                            kind="aligned-ocr-witnesses",
                            generators=["aligned-ocr-witnesses"],
                            proposal_evidence=found["proposal_evidence"],
                        )
                    ],
                    front=True,
                )
    by_generator = {}
    for proposal in suggestions:
        for generator in proposal.get("generators", ["general-lexicon"]):
            by_generator.setdefault(generator, []).append(proposal["term"])
    detected = exclusion is None or "aligned-ocr-witnesses" in by_generator
    return dict(
        id=case["id"],
        book=case["book"],
        page=case["page"],
        before=case["before"],
        after=case["after"],
        expected=case["expected"],
        lexical_exclusion=exclusion,
        detected=detected,
        unresolved=detected and not suggestions,
        candidate_recall=case["after"] in {p["term"] for p in suggestions},
        generators=by_generator,
        suggestions=suggestions,
    )


def summarize(rows):
    errors = [r for r in rows if r["expected"] == "correct"]
    controls = [r for r in rows if r["expected"] == "retain"]
    generators = sorted({g for r in errors for g in r["generators"]})
    return dict(
        errors=len(errors),
        detected_errors=sum(r["detected"] for r in errors),
        errors_with_answer=sum(r["candidate_recall"] for r in errors),
        unresolved_errors=sum(r["unresolved"] for r in errors),
        controls=len(controls),
        flagged_controls=sum(r["detected"] for r in controls),
        controls_with_alternatives=sum(bool(r["suggestions"]) for r in controls),
        excluded_errors=dict(
            sorted(
                Counter(
                    r["lexical_exclusion"] for r in errors if not r["detected"]
                ).items()
            )
        ),
        by_generator={
            g: dict(
                answers=sum(r["after"] in r["generators"].get(g, []) for r in errors),
                unique_answers=sum(
                    r["after"] in r["generators"].get(g, [])
                    and not any(
                        r["after"] in terms
                        for other, terms in r["generators"].items()
                        if other != g
                    )
                    for r in errors
                ),
            )
            for g in generators
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=ROOT / "docs/statistical-ocr-evaluation.json"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "work/production-candidate-research/evaluation.json",
    )
    args = parser.parse_args()
    frozen = read_json(args.input)
    books, current, _ = load_books()
    verify_frozen(books, current, frozen)
    cases = frozen["cases"]
    rows, resources = [], {}
    specs = {
        s["name"]: s
        for s in read_json(ROOT / "tests/book-cases.json")
        if not s.get("editorial")
    }
    for slug, data in sorted(books.items()):
        spec = specs[slug]
        profile = ROOT / spec["profile"]
        book = load_profile(profile)
        bank = ReviewWitnesses(ROOT / spec["work"], book)
        corpus, page_sources = [], {}
        primary_path = cache_path(ROOT / spec["work"], "tesseract")
        for file in sorted(primary_path.glob("[0-9][0-9][0-9][0-9].json")):
            page = int(file.stem)
            primary = (
                "vision"
                if page in book.get("vision_primary_pages", [])
                else "tesseract"
            )
            file = cache_path(ROOT / spec["work"], primary) / file.name
            model = row_model(book, page, merge_rows(read_json(file)["lines"]))
            page_sources[page] = [
                s for e in model for b in e["blocks"] for s in b["sources"]
            ]
            if page not in data["excluded_pages"]:
                corpus.extend(model)
        bank.collect(corpus)
        dictionary = Dictionary.from_files(
            str(ROOT / "work/models" / dictionary_locale(book))
        )
        protected = {
            w.lower()
            for w in profile.with_name("protected-words.txt").read_text().splitlines()
            if w and not w.startswith("#")
        }
        protected.update(book.get("line_join_words", []))
        sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        frequency = (
            Path(symspellpy.__file__).parent / "frequency_dictionary_en_82_765.txt"
        )
        if not sym.load_dictionary(str(frequency), 0, 1):
            raise ValueError("Frequency resource failed to load")
        for word in sorted(protected):
            sym.create_dictionary_entry(word, 1)
        own = [c for c in cases if c["book"] == slug]
        originals = {c["id"]: c for c in own if c["expected"] == "correct"}
        for case in own:
            original = originals[case["id"].removesuffix("-retain")]
            sources = [
                s
                for s in page_sources[case["page"]]
                if s["bbox"] == case["bbox"]
                and s["text"].lower() == original["context"]
            ]
            if len(sources) != 1:
                raise ValueError(f"Unbound raw source row: {case['id']}")
            source = sources[0]
            if case["expected"] == "retain":
                source = dict(source, text=case["context"])
            rows.append(
                evaluate_case(
                    case, source, bank, dictionary, protected, sym, language_code(book)
                )
            )
        if any(
            r["page"] in data["excluded_pages"]
            for rs in bank.attested.values()
            for r in rs
        ):
            raise ValueError("Held-out page leaked into vocabulary")
        resources[slug] = dict(
            excluded_pages=data["excluded_pages"],
            witnesses=bank.resources,
            attested_terms=len(bank.sym.words),
            frozen_book=frozen["books"][slug],
        )
    result = dict(
        purpose="Shipped candidate rules on raw source-row fixtures, not full reconstructed-layout recall or corpus precision",
        controls="Counterfactual primary text with original peer caches; not naturally correct observations",
        ranking="Lexical contextual reranking omitted: changes order/features, not candidate membership",
        input_sha256=file_digest(args.input),
        helper_sha256=file_digest(Path(__file__)),
        helpers_sha256={
            name: file_digest(ROOT / "scripts" / name)
            for name in (
                "candidates.py",
                "review_candidates.py",
                "simple_ocr_research.py",
                "statistical_ocr_research.py",
                "ocr.py",
                "common.py",
                "languages.py",
                "vocabulary.py",
            )
        },
        package_versions={
            name: importlib.metadata.version(name)
            for name in ("symspellpy", "spylls", "wordfreq", "numpy")
        },
        books=resources,
        summary=summarize(rows),
        by_book={
            slug: summarize([r for r in rows if r["book"] == slug])
            for slug in sorted(books)
        },
        cases=rows,
    )
    write_json(args.output, result)
    print(result["summary"])


if __name__ == "__main__":
    main()
