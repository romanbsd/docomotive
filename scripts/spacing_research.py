"""Evaluate spacing hypotheses against reviewed scans; never edit book text."""

import argparse
from collections import Counter
import html
import math
from pathlib import Path
import re
import statistics

import pymupdf
from spylls.hunspell import Dictionary
from wordfreq import zipf_frequency

from common import ROOT, file_digest, load_profile, read_json, write_json
from lexical_repair import ContextRanker, CropRecognizer
from languages import dictionary_locale, language_code
from ocr import nearest
from vocabulary import TOKEN


def boundaries(text):
    compact = re.sub(r"\s+", "", text)
    positions = set()
    for match in re.finditer(r"\s+", text):
        if (
            match.start()
            and match.end() < len(text)
            and text[match.start() - 1].isalpha()
            and text[match.end()].isalpha()
        ):
            positions.add(len(re.sub(r"\s+", "", text[: match.start()])))
    return compact, positions


def gap_evidence(observation, before, after):
    compact, original = boundaries(before)
    target, revised = boundaries(after)
    if compact != target:
        raise ValueError("Spacing benchmark must preserve every non-space character")
    observed, _ = boundaries(observation["text"])
    hits = [m.start() for m in re.finditer(re.escape(compact), observed)]
    if len(hits) != 1:
        return dict(reason="missing-or-ambiguous-literal-glyphs", gaps=[])
    offsets = [i for i, c in enumerate(observation["text"]) if not c.isspace()]
    chars = {c["start"]: c for c in observation["characters"]}
    heights = [c["bbox"][3] - c["bbox"][1] for c in chars.values()]
    height = statistics.median(heights) if heights else 0
    gaps = []
    for boundary in sorted(original ^ revised):
        position = hits[0] + boundary
        a, b = chars.get(offsets[position - 1]), chars.get(offsets[position])
        if not a or not b or height <= 0:
            continue
        gap = b["bbox"][0] - a["bbox"][2]
        gaps.append(
            dict(
                operation="insert-space" if boundary in revised else "remove-space",
                compact_offset=boundary,
                glyphs=[
                    observation["text"][offsets[position - 1]],
                    observation["text"][offsets[position]],
                ],
                gap_points=gap,
                gap_over_height=gap / height,
            )
        )
    return dict(reason="aligned" if gaps else "missing-character-boxes", gaps=gaps)


def sequence_score(ranker, words, exclusions):
    score = math.log(10 ** (zipf_frequency(words[0], ranker.language) - 9))
    outgoing_removed = Counter()
    for (left, _), count in exclusions.items():
        outgoing_removed[left] += count
    for left, right in zip(words, words[1:]):
        prior = 10 ** (zipf_frequency(right, ranker.language) - 9)
        general = (
            0.9 * ranker.general.get((left, right), 0) / (ranker.outgoing[left] + 1)
            + 0.1 * prior
        )
        count = max(0, ranker.pairs[left, right] - exclusions.get((left, right), 0))
        outgoing = max(0, ranker.book_outgoing[left] - outgoing_removed[left])
        score += math.log(max((count + 10 * general) / (outgoing + 10), 1e-30))
    return score


def context_evidence(ranker, case, corrected_row):
    text, before, after = case["row_text"], case["before"], case["after"]
    if text.count(before) != 1:
        return dict(reason="ambiguous-source-occurrence")
    revised = text.replace(before, after, 1)
    original_words = [w.lower() for w in TOKEN.findall(text)]
    revised_words = [w.lower() for w in TOKEN.findall(revised)]
    # Exclude the observed source row, including already-reviewed overlays.
    # Neither the erroneous input nor a manual answer should train its own test.
    observed_words = [w.lower() for w in TOKEN.findall(corrected_row)]
    exclusions = {
        pair: min(count, ranker.pairs[pair])
        for pair, count in Counter(zip(observed_words, observed_words[1:])).items()
    }
    return dict(
        reason="scored",
        original_words=original_words,
        revised_words=revised_words,
        original_delta=sequence_score(ranker, revised_words, {})
        - sequence_score(ranker, original_words, {}),
        leave_source_row_out_delta=sequence_score(ranker, revised_words, exclusions)
        - sequence_score(ranker, original_words, exclusions),
        excluded_pairs=[
            dict(words=list(pair), count=n) for pair, n in exclusions.items() if n
        ],
    )


def run(manifest, book_output_root, output, models):
    cases = read_json(manifest)
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids) or not all(
        re.fullmatch(r"[a-z0-9-]+", i) for i in ids
    ):
        raise ValueError("Spacing case IDs must be unique safe filenames")
    for case in cases:
        if case["expected"] not in {"correct", "retain"}:
            raise ValueError("Unknown spacing ground truth outcome")
        if boundaries(case["before"])[0] != boundaries(case["after"])[0]:
            raise ValueError(
                "Spacing benchmark must preserve every non-space character"
            )
    books = {c["name"]: c for c in read_json(ROOT / "tests/book-cases.json")}
    prepared, results, review = {}, [], []
    for case in cases:
        if case["book"] not in prepared:
            spec = books[case["book"]]
            book = load_profile(ROOT / spec["profile"])
            if language_code(book) != "en" or book.get("text_source") == "native":
                raise ValueError("This spacing benchmark covers English scanned PDFs")
            source = ROOT / spec["pdf"]
            source_hash = file_digest(source)
            if source_hash != case["source_sha256"]:
                raise ValueError("Spacing ground truth no longer matches source")
            folder = book_output_root / case["book"]
            model = read_json(folder / "book-model.json")
            pages = read_json(folder / "corrected-pages.json")
            dictionary = Dictionary.from_files(str(models / dictionary_locale(book)))
            ranker = ContextRanker(model, book, dictionary)
            recognizer = CropRecognizer(
                pymupdf.open(source), output / case["book"], book, models
            )
            prepared[case["book"]] = ranker, recognizer, pages, source_hash, folder
        ranker, recognize, pages, source_hash, folder = prepared[case["book"]]
        if source_hash != case["source_sha256"]:
            raise ValueError("Inconsistent source hash within spacing ground truth")
        row = dict(text=case["row_text"], bbox=case["bbox"], page=case["page"])
        page = recognize.doc[case["page"] - 1]
        box = case["bbox"]
        rect = pymupdf.Rect(
            box[0] * page.rect.width,
            box[1] * page.rect.height,
            box[2] * page.rect.width,
            box[3] * page.rect.height,
        )
        image_name = case["id"] + ".png"
        output.mkdir(parents=True, exist_ok=True)
        page.get_pixmap(dpi=300, clip=(rect + (-2, -2, 2, 2)) & page.rect).save(
            output / image_name
        )
        review.append(
            "<section><h2>"
            + html.escape(
                case["id"] + ": " + case["book"] + " / source page " + str(case["page"])
            )
            + "</h2><p>"
            + html.escape(
                case["expected"] + ": " + case["before"] + " → " + case["after"]
            )
            + '</p><img src="'
            + image_name
            + '" alt="Source line"/></section>'
        )
        corrected = nearest(row, pages[str(case["page"])])
        if not corrected:
            raise ValueError("Ground truth source row is absent from baseline output")
        crops = []
        for dpi in (600, 300):
            observation = recognize.line_words(
                recognize.doc[case["page"] - 1], row, raw=True, dpi=dpi
            )
            crops.append(
                dict(
                    dpi=dpi,
                    text=observation["text"],
                    **gap_evidence(observation, case["before"], case["after"]),
                )
            )
        segmentation = ranker.sym.word_segmentation(case["before"], max_edit_distance=0)
        compound = ranker.sym.lookup_compound(
            case["before"], max_edit_distance=0, transfer_casing=True
        )
        results.append(
            dict(
                **case,
                context=context_evidence(ranker, case, corrected["text"]),
                crops=crops,
                symspell_segmentation=segmentation.segmented_string,
                symspell_compound=compound[0].term if compound else None,
                baseline_model_sha256=file_digest(folder / "book-model.json"),
                crop_engine_version=recognize.version,
                crop_model_sha256=recognize.model_hash,
                language_resources=ranker.resources,
            )
        )
    positives = [r for r in results if r["expected"] == "correct"]
    negatives = [r for r in results if r["expected"] == "retain"]
    summary = {
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "segmentation_exact_positive_matches": sum(
            r["symspell_segmentation"] == r["after"] for r in positives
        ),
        "segmentation_exact_negative_retained": sum(
            r["symspell_segmentation"] == r["before"] for r in negatives
        ),
        "positive_leave_out_context_scores": sum(
            r["context"].get("leave_source_row_out_delta", 0) > 0 for r in positives
        ),
        "negative_leave_out_context_scores_above_zero": sum(
            r["context"].get("leave_source_row_out_delta", 0) > 0 for r in negatives
        ),
    }
    write_json(
        output / "report.json",
        dict(
            manifest_sha256=file_digest(manifest),
            helper_sha256=file_digest(Path(__file__)),
            results=results,
            summary=summary,
            purpose="research-only; scores and gaps do not authorize edits",
        ),
    )
    (output / "review.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Spacing ground truth</title>'
        "<style>body{max-width:1000px;margin:auto;font-family:system-ui}"
        "img{max-width:100%}section{border-bottom:1px solid #ccc;padding:1em}</style>"
        "<h1>Reviewed spacing cases</h1>" + "".join(review),
        encoding="utf-8",
    )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "tests/spacing-cases.json"
    )
    parser.add_argument("--book-output-root", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "work/spacing-research/benchmark"
    )
    parser.add_argument("--models", type=Path, default=ROOT / "work/models")
    args = parser.parse_args()
    results = run(args.manifest, args.book_output_root, args.output, args.models)
    print(
        f"Evaluated {len(results)} reviewed cases; report: {args.output / 'report.json'}"
    )


if __name__ == "__main__":
    main()
