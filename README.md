# Docomotive

A local-first scanned-book → reflowable EPUB research pipeline. The first benchmark is Andrew Weil's *The Natural Mind*. This is a working prototype with reviewed book-specific layout, not a claim of fully automatic or fully proofread conversion.

The current reading copy is [the corrected EPUB](output/reading-edition/the-natural-mind-corrected.epub). The [source-spelling edition](output/the-natural-mind.epub) preserves six verified printed typos and one source line-wrap punctuation artifact. Both preserve the original cover, publisher emblem, back cover, verse, bibliography and index; the scanned book has no interior diagrams. The copyright text is reflowed without embedding the copyright-page scan. Continued footnotes are grouped and linked from their original pages. Index numbers link back to the text.

## Run

Use the existing `.venv`; no activation is needed. Full extraction currently requires macOS because Apple Vision is one of the witnesses. Tesseract and RapidOCR run locally on CPU. The build can run elsewhere from the cached JSON, although fresh cross-platform OCR has not been validated.

```sh
.venv/bin/pip install -r requirements.txt
brew install tesseract ocrmypdf
.venv/bin/python scripts/bootstrap.py

# The shell expands the single input PDF without interpreting its punctuation.
.venv/bin/python scripts/pipeline.py ./*.pdf

# Reading edition: additionally apply seven source-verified editorial repairs.
.venv/bin/python scripts/pipeline.py ./*.pdf --skip-extraction \
  --editorial --output output/reading-edition

.venv/bin/python -m unittest discover -s tests -v
```

Java is needed for EPUBCheck. Swift/Xcode command-line tools are needed for Vision. The bootstrap downloads six public resources with expected SHA-256 checksums: three PP-OCRv4 ONNX models, the Hunspell dictionary pair, and EPUBCheck 5.4.0. It fails on changed bytes. Package pins are in `requirements.txt`; `requirements-research.lock.txt` records the entire experiment environment, including pre-existing pdf-craft dependencies, and is not a portable minimal lockfile.

OCR caches and generated books are ignored by Git but retained locally. Do not delete `work/` if you want to reproduce the current OCR evidence without rerunning inference. Apple Vision may need execution outside the agent sandbox to contact the local macOS service. It does not upload pages.

## Stages and artifacts

| Stage | Implementation | Evidence |
|---|---|---|
| Render and recognize | `scripts/extract.py`: 300 dpi, source/engine/model fingerprints, resumable page JSON; index columns recognized separately | `work/ocr/*/provenance.json`, per-page geometry/confidence |
| Align and reconcile | `scripts/ocr.py`: geometry alignment, conservative multiple-witness token edits, source-checked exact overlays with occurrence preconditions | `output/corrected-pages.json`, `corrections-applied.json`, `ocr-comparison.json` |
| Margin inference | `scripts/layout.py`: recurring upper-margin text, OCR variant clustering, robust median/MAD position, parity, page-number offset consensus | `output/layout-analysis.json` |
| Assemble | `scripts/book_model.py`: canonical paragraphs and continued notes, shared lexical joins, character spans back to source rows; exact row-coverage check | `output/book-model.json`, `text-coverage.json` |
| Whole-book statistics | `scripts/vocabulary.py`: chapter TF-IDF, page/chapter spread, spelling forms, language-frequency enrichment; references/index counted separately | `output/vocabulary-analysis.json`, `protected-term-proposals.json` |
| Suggest corrections | Hunspell via Spylls, SymSpell, word frequency and protected vocabulary; optional KenLM/LanguageTool adapters | `output/correction-candidates.json` — proposals only |
| Proofread | Offline scan crops, OCR witnesses, proposals and whole-book evidence; export reviewed decisions | `output/proofreading-review.html`, `proofreading-review.json` |
| Render and validate | Sorted EPUB ZIP entries, fixed timestamps, XHTML/internal-link validation, external EPUBCheck | EPUB, `report.json`, `epubcheck.json`, `preview/` |

Primary body OCR is Tesseract. References/index use Vision. Paddle PP-OCRv4 through RapidOCR and the embedded OCR are witnesses. All three fresh engines were run over all 242 pages; the embedded layer is a fourth comparison source. OCRmyPDF was tested on three selected pages, not the whole book.

`review-queue.json` contains unresolved engine disagreements after recorded scan decisions. An empty queue means those detected disagreements have been handled; it does **not** mean the book is error-free. The lexical queue is separate and includes legitimate terminology and printed errors. Multiple OCR engines can agree on the same mistake.

## Reuse with another book

Create a separate profile directory containing `book.json`, `corrections.json`, `review-decisions.json`, `protected-words.txt`, `copyright.xhtml`, and `editorial-proposals.json`. ISBN enrichment is optional; copy or generate `enrichment.json` only for the matching source. Start from `config/` and replace the source SHA-256, metadata and layout decisions. Empty correction/review/editorial arrays are valid. Then use isolated job directories:

```sh
.venv/bin/python scripts/pipeline.py /path/to/book.pdf \
  --profile profiles/my-book/book.json \
  --work work/my-book --output output/my-book
```

The current profile contract describes chapter page ranges, first-body cutoffs on chapter openings, note-region starts and continuation chains, verse regions, measured column gutters, reference/index pages, engine overrides, printed-page offset, source artwork pages, and publisher-mark page/crop. Coordinates in OCR/layout are normalized; the artwork crop uses PDF points. Page numbers are 1-based PDF pages. Fields and examples are in `config/book.json`. A profile is reviewed input, not inferred truth. Artwork names currently follow the cover/title/copyright/back-cover convention; automatic discovery of those roles is future work.

For a new book, first inspect representative scans and create transcribed evaluation regions. Run individual engines with `--pages 4,21,32` before committing to full extraction. Recheck chapter openings, footnotes, columns, quotations and rare terms. Changing the source PDF requires a new profile hash. Exact text overlays fail if their expected occurrences change.

## ISBN metadata and downloaded cover

```sh
# Free, unauthenticated Open Library ISBN/edition API + exact-source Archive cover.
.venv/bin/python scripts/metadata.py
# Reproduce enrichment with no network.
.venv/bin/python scripts/metadata.py --offline
# Or include enrichment in the coordinator (cache misses need network).
.venv/bin/python scripts/pipeline.py ./*.pdf --skip-extraction --fetch-metadata
```

`book.json` contains the source-verified ISBN, optional archive item identifier, and a visually approved cover URL. ISBN-10/13 check digits and API title/author/publisher identity are checked. Remote records and images are cached with URL, retrieval time and hash. `config/enrichment.json` is the frozen build input; the EPUB build itself never contacts the network. ISBN association alone does not approve a cover. Missing records, wrong editions, invalid images and placeholders are not treated as usable metadata.

For this scan, Open Library and the exact Internet Archive source record identify the 1972 edition, ISBN-10 **0395139368**, ISBN-13 **9780395139363**. The printed paperbound ISBN is retained as a related print identifier, rather than merging its 1973 edition record into this one. EPUB metadata includes full title, author/author sort/role, language, publisher, publication date, rights, description/note when available, 15 topical subjects, Library of Congress/Dewey classifications, LCCN, OCLC, Open Library ID, print extent/place and source links. Unsupported or absent fields are left absent; no synopsis or edition facts are invented.

The matching blue cover was downloaded from the exact source item and embedded as `downloaded-cover.jpg` with a supplementary non-linear cover page. Its 180 × 278 resolution is lower than the scan, so the 1048 × 1620 scanned cover remains the main cover. A future visually approved JPEG at least 300 pixels on its short edge replaces the main cover automatically. The original ISBN API image was rejected for main-cover use: it is a 128 × 187 title-page thumbnail. A work-level cover from a revised edition was also rejected. No copyright-page scan is included.

## Optional jev

`scripts/jev_rank.py` uses the TypeSafe Choice primitive with pinned `jev-1.13.0`. It ranks existing dictionary candidates against text context; it cannot view the scans. Responses are schema-checked and cached by exact request hash. Nothing is automatically applied. Credentials come from `TYPESAFE_API_KEY` or `.env` and are excluded from logs, cache keys and artifacts.

```sh
# Review the exact outgoing excerpts before any network request.
.venv/bin/python scripts/jev_rank.py --dry-run \
  --output output/jev-request-preview.json

# Sends those candidate contexts to TypeSafe if not already cached.
.venv/bin/python scripts/jev_rank.py
```

The five-candidate experiment was explicitly approved and executed. All five proposed the expected lexical replacements; all five were actually printed typos, established by scan inspection. This illustrates why lexical plausibility alone cannot establish an OCR error. The default source-spelling edition preserves them; `--editorial` applies the separately recorded source-verified editorial overlay. No OpenAI-compatible fallback was used.

See [the research report](docs/research.md) for measurements, tested versus untested tools, limitations and the next experiments.

## Whole-book proofreading

Vocabulary analysis runs after complete paragraph and footnote assembly, using the same canonical text that is rendered in the EPUB. Chapters are the IDF documents; references and index are counted separately so duplicated index entries do not inflate narrative support. General-language Zipf frequency supplies an approximate background prior, not a second corpus IDF. High chapter IDF alone is not a reason to protect a token: isolated OCR mistakes also have high IDF. Recurrence and page/chapter spread propose terms for protection; a more frequent nearby spelling raises review priority. Systematic OCR errors can recur too, so these passes never add words automatically to the protected dictionary.

Open [the reading-edition review sheet](output/reading-edition/proofreading-review.html) in a browser. It includes capitalized terms and footnotes, displays scan crops and all three OCR witnesses, and exports decisions as JSON. Decisions require verification and checked config overlays before application. The current reading edition has 92 lexical groups plus one repeated-word diagnostic; this is a review queue, not an error count.

Manual and editorial overlays share exact occurrence checks in `scripts/common.py`. Layout classification, within-page/across-page joins, rendering and proofreading use shared source-linked text rather than independently rebuilding words from HTML. Twelve rare line joins have scan-backed decisions in `config/line-join-decisions.json`; their vocabulary is explicitly supplied by the profile. Coverage proves that every retained reconciled OCR row is represented once, not that OCR recognized every mark in the scan.
