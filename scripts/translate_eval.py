#!/usr/bin/env python3
"""Measure translation settings on fixed sample windows.

Builds one sample EPUB from the windows in config/translation-eval.json, then
translates it once per named configuration with the same code path as
translate.py (glossary, checks, typography). Each run appends one JSON row of
metrics to work/translation-eval/results.jsonl. Cache and glossary are kept per
model under work/translation-eval/<model>/, so reruns are replays.

    translate_eval.py --model hy-mt2:latest --grid '{"chosen": {}, "big": {"chunk_chars": 6000}}'
"""

import argparse
import copy
import json
import re
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

import translate as T
from common import ROOT, write_epub

SPEC = ROOT / "config/translation-eval.json"
WORK = ROOT / "work/translation-eval"
XHTML = "http://www.w3.org/1999/xhtml"
CONTAINER = (
    b'<?xml version="1.0"?><container version="1.0" '
    b'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    b'<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
    b"</rootfiles></container>"
)


def units(root):
    """Top-level content of the body, below single-child wrappers (Calibre divs)."""
    node = root.find(f"{{{XHTML}}}body")
    while len(node) == 1 and not (node.text or "").strip():
        node = node[0]
    return list(node)


def window(epub, document, start, length):
    with zipfile.ZipFile(ROOT / epub) as z:
        root = etree.fromstring(z.read(document))
    chosen, offset = [], 0
    for unit in units(root):
        size = len("".join(unit.itertext()))
        if offset + size > start and offset < start + length:
            chosen.append(copy.deepcopy(unit))
        offset += size
    return chosen


def build_sample(spec, path):
    files = {"META-INF/container.xml": CONTAINER}
    names = []
    for i, sample in enumerate(spec["samples"], 1):
        html = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML})
        html.set("lang", "en")
        head = etree.SubElement(html, f"{{{XHTML}}}head")
        etree.SubElement(head, f"{{{XHTML}}}title").text = sample["name"]
        body = etree.SubElement(html, f"{{{XHTML}}}body")
        for unit in window(
            sample["epub"], sample["document"], sample["start"], sample["length"]
        ):
            body.append(unit)
        name = f"s{i}.xhtml"
        files[name] = etree.tostring(html, xml_declaration=True, encoding="utf-8")
        names.append(name)
    manifest = "".join(
        f'<item id="i{k}" href="{n}" media-type="application/xhtml+xml"/>'
        for k, n in enumerate(names)
    )
    spine = "".join(f'<itemref idref="i{k}"/>' for k in range(len(names)))
    files["content.opf"] = (
        '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">translation-eval</dc:identifier><dc:title>Translation evaluation'
        f"</dc:title><dc:language>en</dc:language></metadata><manifest>{manifest}</manifest>"
        f"<spine>{spine}</spine></package>"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_epub(path, files)


def book_texts(spec):
    """Prose of every source book, as a real run would see it for the glossary."""
    texts = []
    for epub in sorted({s["epub"] for s in spec["samples"]}):
        with zipfile.ZipFile(ROOT / epub) as z:
            for name in sorted(z.namelist()):
                if not name.endswith((".html", ".xhtml")):
                    continue
                blocks = T.leaf_blocks(etree.fromstring(z.read(name)))
                if T.apparatus_document(blocks):
                    continue
                texts += [T.encode(b)[0] for b in blocks if not T.apparatus(b)]
    return texts


def aligned(sample, output, spec):
    """(sample name, kind, source text, target text) per leaf block."""
    rows = []
    with zipfile.ZipFile(sample) as a, zipfile.ZipFile(output) as b:
        for i, s in enumerate(spec["samples"], 1):
            name = f"s{i}.xhtml"
            pick = lambda z: [
                "".join(x.itertext())
                for x in T.leaf_blocks(etree.fromstring(z.read(name)))
                if T.local(x) != "title"
            ]
            rows += [
                (s["name"], s["kind"], src, tgt) for src, tgt in zip(pick(a), pick(b))
            ]
    return rows


def term_variants(glossary, rows):
    """Glossary terms that occur twice or more in the sources and appear in the
    output under more than one spelling. Output words are matched to the
    rendering by similarity (difflib ratio 0.7 separates аяуаска/айяуаска, 0.77
    and up, from unrelated words, 0.6 and below) and compared by a prefix short
    enough to ignore inflected endings."""
    from difflib import SequenceMatcher

    source = " ".join(r[2] for r in rows)
    target_words = [w.lower() for r in rows for w in re.findall(r"\w+", r[3])]
    variants = {}
    for term, rendering in glossary.items():
        rendering = rendering if isinstance(rendering, str) else rendering["rendering"]
        stem = rendering.split()[0].lower()
        hits = len(re.findall(rf"(?<!\w){re.escape(term)}", source, re.I))
        if hits < 2 or len(stem) < 4:
            continue
        k = max(4, min(6, len(stem) - 1))
        # Short renderings (Перу, Луна) fuzzily match ordinary words (первый),
        # so only renderings of 6+ letters are matched by similarity.
        forms = {
            w[:k]
            for w in target_words
            if (
                SequenceMatcher(None, w[: len(stem)], stem).ratio() >= 0.7
                if len(stem) >= 6
                else w.startswith(stem[:k])
            )
        }
        if len(forms) > 1:
            variants[term] = sorted(forms)
    return variants


def first_person(text):
    count = lambda ending: len(
        re.findall(rf"(?i)\bя\s+(?:не\s+)?(?:\w+\s+)?\w+{ending}\b", text)
    )
    return {"masculine": count(r"(?:л|лся)"), "feminine": count(r"(?:ла|лась)")}


def qe_scores(rows):
    """Mean CometKiwi score per sample (reference-free). The model is licensed
    CC BY-NC-SA 4.0: non-commercial use only."""
    try:
        from comet import download_model, load_from_checkpoint
    except ImportError:
        raise SystemExit("--qe needs: .venv/bin/pip install unbabel-comet")
    model = load_from_checkpoint(download_model("Unbabel/wmt22-cometkiwi-da"))
    data = [{"src": r[2], "mt": r[3]} for r in rows if r[2].strip()]
    scores = model.predict(data, batch_size=8, gpus=0, progress_bar=False).scores
    by_sample = {}
    for row, score in zip([r for r in rows if r[2].strip()], scores):
        by_sample.setdefault(row[0], []).append(score)
    return {k: round(sum(v) / len(v), 4) for k, v in by_sample.items()}


def run(model, name, overrides, spec, language, qe=False, review_model=None):
    folder = WORK / re.sub(r"\W", "_", model)
    sample = WORK / "sample.epub"
    if not sample.exists():
        build_sample(spec, sample)
    config = T.model_config(model)
    config = {
        **config,
        **{k: v for k, v in overrides.items() if k != "options"},
        "options": {**config["options"], **overrides.get("options", {})},
    }
    narrator = spec.get("narrator")
    translator = T.Translator(
        model, folder / "cache.jsonl", config, note=T.narrator_note(narrator)
    )
    glossary_path = folder / f"glossary-{language}.json"
    if config.get("glossary"):
        if not glossary_path.exists():
            terms = T.glossary_terms(book_texts(spec), T.spelling_dictionary("en"))
            glossary = translator.translate_terms(terms, "en", language)
            glossary_path.write_text(json.dumps(glossary, ensure_ascii=False, indent=1))
        translator.glossary = json.loads(glossary_path.read_text())
    brief_path = folder / f"brief-{language}.json"
    if config.get("glossary") and config.get("brief"):
        if not brief_path.exists():
            excerpt, names = T.brief_inputs(book_texts(spec), translator.glossary)
            brief = translator.make_brief(excerpt, names, "en")
            brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=1))
        translator.brief = json.loads(brief_path.read_text())
    checks = None
    if config.get("checks", True):
        ratio = config.get("min_length_ratio", 0.8)
        checks = lambda s, t: T.quality_flags(
            s, t, language, narrator, ratio
        ) + T.term_flags(s, t, translator.glossary)
    review = reviewer = None
    if review_model:
        reviewer = T.Translator(
            review_model,
            WORK / re.sub(r"\W", "_", review_model) / "review-cache.jsonl",
            T.model_config(review_model),
            note=T.narrator_note(narrator),
        )
        review = lambda src, draft, s, t: reviewer.post_edit(
            src, draft, s, t, translator.glossary, translator.brief
        )
    output = folder / f"{name}.epub"
    started = time.time()
    report = T.translate_epub(
        sample,
        output,
        translator,
        language,
        False,
        checks=checks,
        validate=False,
        review=review,
    )
    seconds = round(time.time() - started)
    rows = aligned(sample, output, spec)
    flags = Counter()
    for _, kind, src, tgt in rows:
        # Notes keep their citation sentences in the source language by design.
        if kind != "notes":
            flags.update(T.quality_flags(src, tgt, language, narrator))
    result = {
        "model": model,
        "name": name,
        "overrides": overrides,
        "language": language,
        "seconds": seconds,
        "model_calls": translator.calls,
        "timing": T.timing_summary(translator.timings),
        "review_model": review_model,
        "reviewed": [
            {k: r[k] for k in ("flags", "level", "accepted")}
            for r in report["reviewed_blocks"]
        ],
        "blocks": len(rows),
        "fallback_levels": report["fallback_levels"],
        "flagged_after_retry": len(report["flagged_blocks"]),
        "flags": dict(flags),
        "length_ratio": round(
            sum(len(r[3]) for r in rows) / max(1, sum(len(r[2]) for r in rows)), 3
        ),
        "term_variants": term_variants(translator.glossary, rows),
        "first_person": first_person(" ".join(r[3] for r in rows)),
    }
    if qe:
        result["cometkiwi"] = qe_scores(rows)
    with (WORK / "results.jsonl").open("a") as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="hy-mt2:latest")
    parser.add_argument("--lang", default="ru", choices=sorted(T.LANGS))
    parser.add_argument(
        "--grid",
        default='{"chosen": {}}',
        help="JSON object: run name -> config overrides (options merge)",
    )
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--qe", action="store_true", help="add CometKiwi scores")
    parser.add_argument("--review-model", help="second model for post-editing")
    parser.add_argument(
        "--rebuild-sample", action="store_true", help="re-cut the sample windows"
    )
    a = parser.parse_args()
    spec = json.loads(a.spec.read_text())
    if a.rebuild_sample:
        build_sample(spec, WORK / "sample.epub")
    T.keep_awake()
    for name, overrides in json.loads(a.grid).items():
        result = run(a.model, name, overrides, spec, a.lang, a.qe, a.review_model)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
