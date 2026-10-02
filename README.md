# Docomotive

A local-first research pipeline for converting scanned and native-text PDFs into reflowable EPUBs with figures, quotations, linked notes, metadata and reviewable corrections. Source-specific facts live in profiles; detection, reconstruction and rendering fixes belong in shared code. Conversion is reproducible from pinned tools and cached evidence, but automated recognition and sampled review do not establish complete proofreading.

See [docs/research.md](docs/research.md) for per-book findings, conversion recipes, measurements, artifacts and experiments.

## Setup and usage

Python 3.14 is the tested interpreter. Use the existing `.venv` without activation:

```sh
.venv/bin/pip install .
brew install tesseract ocrmypdf
.venv/bin/python scripts/bootstrap.py
.venv/bin/pip install --group dev

.venv/bin/python scripts/pipeline.py /path/to/book.pdf \
  --profile profiles/my-book/book.json \
  --work work/my-book --output output/my-book

# Replay complete extraction caches; apply separately reviewed editorial repairs.
.venv/bin/python scripts/pipeline.py /path/to/book.pdf \
  --profile profiles/my-book/book.json --work work/my-book \
  --output output/my-book/reading-edition --skip-extraction --editorial

.venv/bin/black --check scripts tests
.venv/bin/python -m unittest discover -s tests
```

Full multi-engine extraction currently requires macOS for Apple Vision and Swift/Xcode command-line tools. Tesseract and RapidOCR run locally on CPU. Cached JSON can be built elsewhere; fresh cross-platform OCR has not been validated. Java is needed for EPUBCheck. Vision may need execution outside the agent sandbox to contact the local macOS service; it does not upload pages.

Runtime packages are pinned in `pyproject.toml`. Dependencies include PyMuPDF, Pillow, lxml, NumPy/SciPy, RapidOCR/ONNX Runtime, Spylls, SymSpell, wordfreq and Requests. Optional extras are `visual` (WeasyPrint rendering), `research` (pdf-craft), and `kenlm` (an unevaluated scoring adapter requiring a separate language model). `requirements-research.lock.txt` records the experiment environment rather than a portable minimal lockfile. The scripts run from this checkout; installation supplies dependencies.

Bootstrap downloads checksum-verified PP-OCRv4 ONNX models, a Hunspell dictionary pair and EPUBCheck 5.4.0. OCRmyPDF, pdf-craft, Marker, Docling, LanguageTool and Morfologik have differing experiment/integration status; the [research report](docs/research.md) distinguishes tested tools from planned work.

Keep `work/`: its OCR/model/metadata caches preserve the evidence needed for replay. Generated books and OCR caches are ignored by Git.

## Stages and artifacts

| Stage | Tools and behavior | Evidence |
|---|---|---|
| Recognize | 300-dpi Tesseract, Apple Vision and Paddle PP-OCRv4 through RapidOCR; native PDF extraction when configured | Per-page JSON and engine/model/source fingerprints |
| Reconcile | Geometry alignment, conservative witness agreement, checked correction overlays | `corrected-pages.json`, `ocr-comparison.json`, `corrections-applied.json` |
| Infer margins | Header/footer recurrence, OCR variant clustering, median/MAD, parity and page-number consensus | `layout-analysis.json` |
| Reconstruct | Canonical paragraphs, notes, verse and figures; source-linked spans and lexical line joins | `book-model.json`, `text-coverage.json` |
| Analyze the whole book | Chapter TF-IDF, spelling recurrence and page/chapter spread; reference/index evidence separated | `vocabulary-analysis.json`, `protected-term-proposals.json` |
| Propose corrections | Hunspell via Spylls, SymSpell, word frequency and protected vocabulary; optional KenLM/LanguageTool adapters | `correction-candidates.json` |
| Review | Offline scan crops, witnesses, text contexts and optional bounded Jev proposals | `proofreading-review.html`, `note-review.html`, JSON evidence |
| Render and validate | Deterministic EPUB ZIP, XHTML/internal-link checks and EPUBCheck | EPUB, `report.json`, `epubcheck.json`, `preview/` |

The primary OCR engine is profile-selected. Embedded text can be another witness; it is not assumed correct. Multiple modes of one engine are correlated observations, and even independent engines can agree on a mistake. Empty queues mean detected items were handled, not that the book is error-free.

## Profiles and source evidence

Create a profile directory with `book.json`, `corrections.json`, `review-decisions.json`, `protected-words.txt`, `copyright.xhtml` and `editorial-proposals.json`. Optional files hold reviewed line-join decisions and frozen metadata enrichment. Start from `config/book.json`, replace its source SHA-256 and all source-specific metadata/layout facts, and use isolated work/output directories. Empty correction and review arrays are valid.

Profiles describe chapter ranges, opening cutoffs, note regions, verse, columns, engine choices, printed pagination, artwork and reviewed exceptions. PDF page numbers are one-based; OCR coordinates are normalized, while publisher-mark crops use PDF points. A profile is reviewed input, not inferred truth. Artwork roles and bounds still need source inspection.

Inspect representative pages and create transcribed evaluation regions before full OCR. Test individual engines with selected `--pages`; inspect chapter openings, notes, columns, quotations and rare terms. Changing the source requires a new profile hash. Exact overlays fail when occurrence preconditions change. OCR corrections and printed-typo editorial changes remain separate editions.

## Layout, typography and note heuristics

Recurring margin text is clustered conservatively, with robust positions, parity and page-number consensus. Statistical recurrence is supporting evidence rather than a calibrated probability that text is a header.

Scanned prose uses deterministic Theil–Sen left/right margin envelopes to compensate for skew. Sustained symmetric insets support quotations; occasional deeper first lines remain paragraph indents. Unstable, sparse, hanging and native-text layouts retain conservative fallback paths. Margin models, source rows and fallback reasons are audited.

`recover_scan_font_metrics` aligns hidden PDF text with fresh OCR and transfers glyph sizes only. Smaller source-backed quote measurements yield relative EPUB font sizes; hidden OCR font names do not establish the original family. `recover_pdf_typography` separately transfers inline styles where text and geometry agree. Both require usable source evidence. Verse detection preserves short ragged runs and stanza gaps while avoiding ordinary wrapped prose.

`recover_scanned_endnotes` detects raised glyphs at 400 dpi, estimates prose baselines and recognizes isolated crops locally. Character boxes anchor replacements without losing neighboring punctuation. A chapter-wide increasing sequence resets at each chapter, permits missing references, rewards consecutive numbers and rejects ambiguous assignments. Ties and order conflicts trigger tight-crop retries with single-character segmentation, multiple scales and resampling variants. Adjacent broken raised components are included before trimming the complete marker. Common glyph confusions such as 6/8 receive a smaller relative penalty than unrelated substitutions. Scores are documented heuristics, not calibrated probabilities; sequence alone does not supply unread digits. Subscripts, ordinary numbers and mathematical notation have geometric/punctuation guards.

Recognition evidence and retries are cached by source, OCR/tool versions, algorithm and geometry. Sequence-score changes reuse crop evidence. Unlocated or unresolved references retain chapter-level endnote navigation and remain visible in `scanned-endnote-analysis.json`.

The marker search extends beyond OCR's right edge because tiny trailing references and quotes may be absent from its text box. Short lines borrow an estimated letter height from up to three nearby long lines in the same column, while retaining their own measured baseline. This avoids treating a raised digit as body text when only two or three ordinary letters are present. The final OCR crop remains tight around the detected marker.

Raised regions with no valid initial numeric reading get a tight single-character retry before being discarded: digits such as 9 and 11 may initially appear as letters or punctuation. Only numbers actually read from the crop enter sequence selection. Wider search clearance is trimmed before whole-line recognition, so extra whitespace does not disturb existing character placement.

Marker placement can use a four-digit historical year as an anchor. Unpunctuated citations after capitalized names require matched surrounding prose and a gap containing no prose letters. If whole-line OCR merges a numeral into punctuation, a fallback masks the observed glyph, recognizes the remaining prose and reinserts only the crop-read number at its measured position. Tall overlapping crops receive a bounded contrast pass; chapter sequence still rejects unsupported readings.

Figure extraction prefers the original raster when its transform and overlays are verified; otherwise it renders the PDF crop. Captions remain reflowable. `source_relative_figures` preserves reviewed artwork proportions. `recover_ocr_regions` replaces garbled rows where fresh engines agree on separate lines and geometry avoids duplication. An existing matching neighbor is retained once. A single extra digit-confusion token in one fresh engine requires exact lexical corroboration from embedded OCR; substitutions of prose words remain rejected.

## Whole-book proofreading

Vocabulary analysis runs after complete paragraph and note assembly, using the same canonical text as EPUB rendering. Chapters are IDF documents; notes contribute to their source chapters, while references/index remain separate. General-language Zipf frequency is an approximate background prior. High IDF alone cannot protect a token: OCR mistakes can also be rare. Recurrence, spread and competing spellings propose review priorities, without automatically extending the protected dictionary.

Open `OUTPUT/proofreading-review.html` in a browser to compare scan crops, witnesses and suggestions and export decisions. Reviewed config overlays are required for application. Manual/editorial overlays share exact occurrence checks, and every retained source row must map once into the canonical model. Coverage cannot prove that OCR recognized every mark in the scan.

For note-marker review:

```sh
.venv/bin/python scripts/note_review.py /path/to/book.pdf \
  --profile profiles/my-book/book.json --input output/my-book \
  --output output/my-book/note-review.html
```

The self-contained offline sheet shows unresolved and retried locations, marker close-ups, paragraph context, recognition evidence and missing numbers by chapter.

## Metadata and covers

`scripts/metadata.py` uses free, unauthenticated Open Library ISBN/edition APIs, Crossref for DOI-only sources and reviewed source cover URLs. ISBN check digits and title/author/publisher identity are checked. Records/images are cached with provenance and hashes; `--offline` replays them. `--fetch-metadata` in the coordinator allows cache misses to fetch remotely. The EPUB build itself uses frozen enrichment and does not contact the network.

ISBN association does not approve a cover. Wrong editions, placeholders, conflicting records and low-resolution images require review. A supplied checked local cover or deterministic typographic cover can be used. Copyright text is reflowed without a copyright-page scan. Unsupported metadata is left absent rather than invented.

## Optional Jev review

`scripts/jev_rank.py` ranks supplied lexical candidates against context. `scripts/jev_notes.py` ranks existing OCR-backed ambiguous citation locations. Both use pinned `jev-1.13.0`, validated Choice responses and content-addressed caches. Jev accepts text rather than images; its confidence cannot establish scan fidelity. Results are review proposals, not automatic book edits.

```sh
.venv/bin/python scripts/jev_rank.py --dry-run --output output/jev-request-preview.json
.venv/bin/python scripts/jev_notes.py --profile profiles/my-book/book.json \
  --input output/my-book --output output/my-book/jev-note-request-preview.json --dry-run
```

Review and authorize the exact outgoing excerpts before omitting `--dry-run`. Credentials come from `TYPESAFE_API_KEY` or `.env` and are excluded from artifacts. A running-number conflict should first trigger better local crop recognition; semantic ranking cannot recover a digit from an image it cannot see.

## Reproducibility and limits

Pinned tools, source/config/code hashes, checksum-verified resources, cached responses, sorted ZIP entries and fixed timestamps support deterministic replay. Fresh OCR across OS/tool versions is not guaranteed byte-identical. EPUBCheck and source-row coverage complement lexical and visual review; neither establishes full proofreading or behavior on every reading device. Missing source pages and uncertain readings are reported rather than reconstructed without evidence.
