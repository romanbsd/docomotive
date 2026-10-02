# Pipeline robustness review

Review date: 2026-10-02. Scope: `scripts/` at commit `2e67b48`, profiles under `config/`. Tests: 102 passing.

Strengths worth keeping: exact-occurrence overlays, the row coverage ledger, provenance fingerprints, chapter-order sequence selection for endnotes, deterministic EPUB packaging.

Main weaknesses: silent configuration failures, thresholds expressed in page units, a fixed binarization cutoff, and correlated OCR witnesses.

## 1. Silent failures

| # | Issue | Location | Suggested fix |
|---|---|---|---|
| 1.1 | Profiles are read with `book.get(...)`; misspelled or obsolete keys are ignored. `frontmatter_pages` in `config/shamanic-trance/book.json` is read by no code. | all scripts | Validate keys and basic types at load; fail on unknown keys. `pydantic` only if nested validation is wanted. |
| 1.2 | `work/<engine>-cache.txt` is rewritten by every `extract.py` run, including `--pages`/`--dpi` experiments. `build.py` follows the pointer and fails mid-build on the first missing page. | `extract.py`, `build.py` | Update the pointer only when the cache is complete; preflight all pages before building. |
| 1.3 | Review decisions match rows by bbox centre (±0.005). Re-OCR shifts geometry; stale decisions are never reported. | `build.py` | Report unmatched decisions; later store a text hash with each decision. |
| 1.4 | Engine versions in `report.json` are string literals ("Tesseract 5.5.3", "Apple Vision revision 3"). | `build.py` | Derive from cached `provenance.json`. |
| 1.5 | `platform.platform()` in the OCR fingerprint invalidates Tesseract/Rapid caches on every OS update; only Vision is OS-bound. | `extract.py` | Per-engine fingerprint fields. Changing it invalidates existing caches. |
| 1.6 | Scanned-note cache key hashes a hand-listed set of functions; unlisted helpers and `eng.traineddata` are not covered. | `scanned_notes.py` | Hash the whole module source plus traineddata. Changing it invalidates existing caches. |
| 1.7 | Crash paths: `book["index_splits"]` KeyError when absent; media-type map KeyError for `.jpeg`/`.gif`/`.webp` covers. | `extract.py`, `build.py` | Defaults / extended map. |
| 1.8 | `active-cache.txt` is written but never read. | `extract.py` | Delete. |

## 2. Page-unit thresholds

Row merge 0.008, nearest row 0.012 (`ocr.py`); indent 0.018, paragraph gap 0.012, verse gap 0.035 (`book_model.py`); margin inlier 0.008 (`paragraph_layout.py`). These are fractions of page width/height and encode one trim size and font size.

Measure a per-book type scale (median line pitch, x-height, character width from long body rows) and express thresholds as multiples of it. This is the most general robustness improvement and likely the cause of many per-page profile overrides (82 `paragraph_margins` in shamanic-trance, 54 `chapter_body_starts` in pharmako-poeia).

## 3. Image preprocessing

- Fixed ink threshold `array < 150` (`scanned_notes.py`) fails on faded, low-contrast or yellowed scans. Use Sauvola/Otsu per crop (`skimage.filters.threshold_sauvola` or OpenCV `adaptiveThreshold`).
- Deskew before OCR (projection profile or OpenCV `minAreaRect` on ink), keeping the affine transform to map boxes back to PDF coordinates. Skew is currently compensated separately in margin envelopes, baseline fits and row tolerances. `ocrmypdf --deskew --clean` is installed and allows an A/B test.

## 4. Correlated OCR witnesses

- `vision_ocr.swift` sets `usesLanguageCorrection = true`, which pulls rare terms toward dictionary words; Tesseract's LSTM has a similar bias. Two engines can agree on the same wrong word and pass the two-vote rule. Add an uncorrected Vision pass as a witness, or supply `protected-words.txt` as `customWords`.
- Request alternatives: Vision `topCandidates(5)`, Tesseract `lstm_choice_mode=2`. A lattice is a better voting input than one string per engine.
- Token voting in `ocr.correct_page` handles only 1:1 replacements; splits and merges ("in to" / "into") are never voted. Use ROVER-style multi-alignment with confidence weights; `rapidfuzz` gives faster Levenshtein opcodes than `difflib`.
- The `J`/`1` → `I` special case belongs in a general glyph-confusion table (like `glyph_confusion_cost`).

## 5. Infer structure instead of declaring it per page

- Running-header detection only finds upper-case headers at the top and numeric footers at the bottom. Mask digits in signatures, cover both zones and mixed case, and fit a monotonic page-number sequence (Arabic and Roman) by dynamic programming. This would also infer `page_labels` (254 hand-entered in pharmako-poeia).
- `vision_primary_pages` lists 256–276 pages in two profiles. Replace with a `primary_engine` default plus page exceptions, or select per line by confidence.
- 110 hand-entered figure rectangles in pharmako-poeia. Seed them from a layout detector (`rapid-layout`, PP-DocLayout on the existing ONNX runtime, or Surya) and review.
- Derive `expected_notes` from the numbered entries in the notes section and cross-check against the profile.

## 6. Regression across books

`AGENTS.md` requires checking earlier books; `work/` contains ad-hoc baseline folders. A single script that rebuilds every profile with `--skip-extraction`, diffs `output/<slug>.txt`, coverage counts and EPUB hashes against committed baselines, and reports CER/WER on transcribed evaluation regions (`jiwer` or `rapidfuzz`) would make this a pass/fail check.

## Suggested libraries

`rapidfuzz` (alignment), `scikit-image` or `opencv-python-headless` (binarization, deskew), `rapid-layout` or `surya-ocr` (layout), `jiwer` (CER/WER), optionally `pydantic` (profile schema).

## Status

### Done (2026-10-02)

| # | Change |
|---|---|
| 1.1 | `common.load_profile` rejects unknown keys against `PROFILE_KEYS`; every script that loads a profile uses it. A free-form `comments` key holds reviewer documentation; shamanic-trance's unread `frontmatter_pages` moved under it. Test: `test_profile_rejects_unknown_keys`. |
| 1.2 | `extract.py` updates `<engine>-cache.txt` only when every page exists in the cache. `build.py` checks all pages exist in each OCR cache before building and names the missing count. Checked by a 1-page run (pointer not written) and a full run (pointer written). |
| 1.3 (partial) | `report.json` lists `unmatched_review_decisions`. Currently empty for all five books. Text-hash matching remains open. |
| 1.4 | `primary_engines` in `report.json` comes from the OCR caches' `provenance.json`. |
| 1.7 | `index_splits` defaults to empty; media-type map covers `.jpeg`, `.gif`, `.webp`. |
| 1.8 | Removed the unused `active-cache.txt` write. |

Verification: 103 tests pass, `black --check` clean. All five books rebuilt with `build.py` from the existing caches. EPUBs, `book-model.json`, text exports and other evidence files are byte-identical to the pre-change build; `report.json` differs only in `primary_engines` and the new `unmatched_review_decisions` field.

### Deferred

- 1.5 changes full-page cache keys; schedule per-engine platform fingerprint changes with the next deliberate re-OCR.
- 1.6 is partially addressed below; automatic helper-dependency coverage remains open. Whole-module hashing would also invalidate evidence on sequence-only changes and comments.
- 1.3: source/text binding is implemented below; legacy entries still need human re-review before replacement.
- Sections 2–6 are design work, not quick fixes. Recommended order: regression script (6), type-scale thresholds (2), adaptive binarization (3).

### Implemented next pass: cached regressions and input integrity

- `scripts/regression.py` rebuilds the five books through seven source/reading edition cases from `tests/book-cases.json`, using cached full-page extraction and isolated output directories. Compact pre-change baselines in `tests/baselines/books.json` record canonical artifacts, text, coverage, apparatus, note/backlink counts, figure inventory and EPUB/member hashes. Logs and structured differences remain under `work/regression/`. Missing inputs, failed builds and unexpected differences fail the command. `--accept-baseline` is explicit, and a failed batch cannot update the baseline. No CER/WER or new dependency is introduced.
- `common.load_profile` now validates required fields and core value types/ranges via `profile_validation.py`, including booleans, ordered non-overlapping chapters, page lists/maps, normalized rectangles and endnote chapter references. Opening the source checks page references against actual PDF bounds. Free-form reviewer comments remain allowed; this is deliberately basic validation rather than a complete nested schema.
- `ocr_cache.py` checks source provenance, complete page coverage, readable JSON, page identity, dimensions, text rows, normalized boxes and confidences before building or selecting a cache. A partial run retains the selected cache; malformed complete caches fail explicitly. Extraction validates its profile and requested pages before initializing OCR engines.
- JSON/cache-pointer publication uses unique temporary files and atomic replacement; concurrent writers no longer share a `.tmp` filename. Failed publication preserves the previous file and cleans the staging file.
- Scanned-note detection and retry fingerprints now include the English traineddata checksum from Tesseract's actual search directory, honoring `TESSDATA_PREFIX`. Full-page Tesseract extraction uses the same checksum helper. This deliberately caused one fresh Organism note pass while preserving full-page caches. The hand-listed detector-function fingerprint remains unchanged.
- The Organism cover had moved into `input/`; its profile path was repaired after verifying the existing checksum. The image content and EPUB cover are unchanged.

Type-scale thresholds, adaptive binarization and broader OCR/layout redesign remain deferred. The regression script above supersedes the regression portion of the earlier deferred list.

Verification: all 114 tests pass; Black and `git diff --check` pass. The full regression run passes all seven cases with unchanged EPUBs, canonical artifacts, coverage, figures and note statistics against independently captured pre-change builds (`work/regression/run-xonkoyzz/report.json`). The fresh Organism note pass still recovers 157/157 references. A deliberately altered baseline produces exit status 1 and names `artifacts.book-model.json` without changing the baseline. A real one-page Tesseract run writes its page cache but does not publish a cache pointer. Tests also exercise corrupt JSON, wrong page identity, invalid geometry, concurrent atomic publication, failed-publication cleanup, and refusal to accept any baseline after a failed batch.

### Source-bound scan-review decisions

`review_decisions.py` centralizes matching and generates an offline scan-backed acknowledgement sheet. New format-1 decisions bind PDF source SHA-256, exact reviewed text and OCR witness/glyph evidence. Confidence, cache paths and status are excluded from evidence hashes. A unique same-page text/evidence match survives a geometry shift; duplicate evidence requires unique historical geometric disambiguation. Changed source, text or witnesses, incomplete bindings, ambiguous locations and uncertain decisions cannot acknowledge rows and receive explicit report reasons.

The 74 legacy Natural Mind decisions retain their existing effect, with `legacy-geometry-only` row labels, console diagnostics and `legacy_review_decisions` report entries. No config decisions were silently upgraded. The generated [legacy review sheet](../output/scan-review.html) contains all 74 locations with crops and witnesses. Records start pending; only explicit selections are exported, and Uncertain selections remain unacknowledged. Merge reviewed exports into the profile array, replacing the corresponding legacy entries. The lexical proofreading workflow and exact correction overlays remain separate.

Verification: 122 tests pass; Black and diff checks pass. All seven edition regressions pass without accepting new baselines (`work/regression/run-q5n57skp/report.json`). New tests cover changed source/text/witnesses, shifted geometry, duplicate ambiguity, incomplete bindings, legacy diagnostics, uncertain decisions, pending sheet records and safe HTML embedding. The generated export JavaScript passes Node syntax checks and a controlled DOM/storage stub verifies empty exports before selection and preservation of explicit choices and bindings; this is not a full browser rendering test.
