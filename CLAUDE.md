# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Docomotive: local-first pipeline converting scanned/native-text PDFs into reflowable EPUBs (figures, quotations, linked notes, metadata, reviewable corrections). `README.md` covers heuristics in depth; `docs/research.md` holds per-book recipes, measurements and experiment status.

## Core rule (from AGENTS.md)

This is a **generic** book-conversion pipeline. Fix defects in shared detection, reconstruction, correction or rendering code so the fix applies to other books. Do not add book-specific page/phrase/coordinate overrides when the problem can be inferred from source evidence. Genuinely source-specific facts and reviewed exceptions go in book profiles, with evidence. Add regression tests for the general behavior and check earlier books (all profiles under `config/`) for unintended changes.

## Commands

Use `.venv/bin/python` directly (no activation). Python 3.14 tested.

```sh
# Tests (stdlib unittest; tests add scripts/ to sys.path)
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m unittest tests.test_scanned_notes
.venv/bin/python -m unittest tests.test_pipeline.PipelineTests.test_isbn_check_digits

# Format
.venv/bin/black --check scripts tests

# Full run for one book (OCR all engines, build, lexical proposals, proofread, EPUBCheck)
.venv/bin/python scripts/pipeline.py 'A feeling for the Organism.pdf' \
  --profile config/feeling-organism/book.json \
  --work work/feeling-organism --output output/feeling-organism

# Replay cached extraction (fast iteration); add --editorial for the corrected edition
.venv/bin/python scripts/pipeline.py <pdf> --profile <profile> --work <work> \
  --output <output> --skip-extraction [--skip-lexical] [--editorial]

# One-time setup: models, Hunspell dict, EPUBCheck into work/
.venv/bin/python scripts/bootstrap.py
```

External tools: Tesseract, OCRmyPDF (brew), Java (EPUBCheck), Swift/Xcode for Apple Vision (`scripts/vision_ocr.swift`, macOS only; may need to run outside the sandbox). Exact per-book commands are in `docs/research.md`.

## Layout

- `scripts/` — flat modules run as scripts and imported by each other by bare name (no package). `pipeline.py` is the coordinator; it shells out to `extract.py` (per engine: tesseract, rapid, vision) → `build.py` → `candidates.py` → `proofread.py` → EPUBCheck.
- `config/` — book profiles. `config/book.json` (root) is the default profile (*The Natural Mind*); subdirs `feeling-organism/`, `letter-kills/`, `pharmako-poeia/`, `shamanic-trance/` are other books. Each profile dir: `book.json`, `corrections.json`, `review-decisions.json`, `protected-words.txt`, `copyright.xhtml`, `editorial-proposals.json`, optional `line-join-decisions.json`, `enrichment.json`. Profiles load through `common.load_profile`, which rejects keys missing from `common.PROFILE_KEYS`; register any new profile key there. Free-form notes go under `comments`.
- `work/` — OCR/model/metadata/tool caches keyed by source+tool+algorithm hashes. **Do not delete**; it is the evidence needed for replay. Gitignored, as are `output/` and PDFs.
- `output/<book>/` — EPUB plus JSON evidence (`book-model.json`, `report.json`, `scanned-endnote-analysis.json`, `proofreading-review.html`, …).

## Architecture

Stages (all inside `build.py` after extraction unless noted):

1. **Recognize** (`extract.py`, `ocr.py`, `native_pdf.py`) — 300-dpi multi-engine OCR, per-page JSON cached with fingerprints. Profile `text_source: "native"` skips OCR and uses embedded text. Primary engine is profile-selected; others are witnesses.
2. **Reconcile** (`ocr.py`) — geometry alignment of rows, conservative witness agreement, exact-occurrence correction overlays (`common.apply_edits`; overlays fail loudly if preconditions change).
3. **Margins** (`layout.py`) — header/footer recurrence clustering, page-number consensus.
4. **Reconstruct** (`book_model.py`, `paragraph_layout.py`) — canonical paragraph/verse/quote/note model with source-linked spans; Theil–Sen margin envelopes for quotations; `JoinPolicy` for line joins. Every retained source row must map exactly once (`coverage`).
5. **Typography / notes / figures** (`typography.py`, `scanned_notes.py`, `figures.py`, `apparatus.py`) — font-size transfer from hidden text, raised-glyph endnote marker detection with per-chapter sequence scoring and crop retries, figure extraction.
6. **Render** (`render_text.py`, `build.py`) — deterministic EPUB ZIP (sorted entries, fixed timestamps), XHTML/link validation.
7. **Proofreading** (`vocabulary.py`, `candidates.py`, `proofread.py`, `note_review.py`) — whole-book TF-IDF/spelling proposals and offline HTML review sheets. Proposals never auto-edit; reviewed decisions go into profile overlays.

Optional: `metadata.py` (Open Library/Crossref, cached; build itself is offline), `jev_rank.py`/`jev_notes.py` (TypeSafe Jev ranking; always `--dry-run` first, needs `TYPESAFE_API_KEY`).

Conventions: PDF page numbers are one-based; OCR coordinates normalized, publisher-mark crops in PDF points. OCR corrections and printed-typo editorial changes are separate editions (`--editorial`). Heuristic scores are documented heuristics, not probabilities; uncertain readings are reported, not invented.
