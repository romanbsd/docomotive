# Conversion research and per-book findings

## Current result

Input: 242 scanned PDF pages, SHA-256 `8d5a7f67e67a73c39d0d0b0cb8be42c3eb6a6304b07239122760e31bd757c0f2`. The filename's 2012 refers to the scan/catalog record; the source copyright page gives 1972. The EPUB metadata follows the printed book.

The profile retains 14 sections: nine chapters, acknowledgments, afterword, works cited, suggested reading, index. There are 225 original-page anchors. Blank/duplicate/divider pages are accounted for in `config/book.json`; printed contents are replaced by linked navigation. All-page contact sheets were inspected for layout roles. The book contains no interior diagrams or photographs; its original cover, back cover and publisher emblem are retained rather than adding invented illustrations.

The pipeline detects recurring headers statistically, then uses limited profile fallbacks for remaining short margin artifacts. On this book it found 12 recurring header groups and 215 consistent numbered footers. Header variant clustering is seeded by frequent text, with no transitive fuzzy chaining; robust median/MAD limits vertical outliers. Odd/even concentration selects recurrence opportunities. Beta(1,1) posterior intervals describe recurrence within an observed span; they are **not calibrated probabilities of header correctness**. Unique titles and short chapter-opening text still require separate treatment.

Detected engine disagreements were inspected in cropped scan montages. The current overlay contains 75 source-checked replacement rules, including several whole-line repairs and accented names. Two damaged glyph cases required contextual inference and are marked in their reasons. Automatic multiple-witness changes and dehyphenation have separate audit event types. Printed typos have a separate seven-item editorial overlay (six spelling repairs and one line-wrap punctuation repair). These counts are rules/events, not a character error rate or proofreading completeness score.

## Citation typography follow-up

PDF 35 (printed 27) contains superscript citations 2 and 3 after “about” and the Huxley quotation. Both digits survived OCR, but the original profile did not enable PDF typography transfer. Its hidden OCR layer also falsely flags ordinary words as superscripts, so wholesale style transfer would be unsafe. The shared opt-in `recover_pdf_numeric_superscripts` requires aligned text, a smaller source glyph and a raised baseline. Exact digits receive superscript styling; ambiguous OCR glyphs require agreement from repeated local Tesseract crop readings with no competing numeric reading. Small crop retries reduce interference from adjacent punctuation. Evidence and engine fingerprints are cached for reproducibility.

Source inspection confirms 21 recovered superscripts, including 4 on PDF 43 and 5 and 6 on PDF 44. Nine required glyph repairs; these and one duplicated footer-marker prefix repair are the only corrected-row text changes against the previous accepted edition. Other repairs, prose and source-row counts are unchanged. This does not establish complete citation recovery, and the initial typography pass did not link numeric bibliography citations to Works Cited.

The shared opt-in `link_symbol_footnotes` links a unique body symbol to a unique same-page footnote symbol and returns to that exact marker. Seventeen asterisk notes now have these links, including PDF 42, 55 and 56. PDF 56's extra OCR `°` before `*` is removed only because two aligned witnesses agree on a single asterisk. Six ambiguous or unrecognized page groups (PDF 67, 107, 112, 152, 201 and 206) retain the existing page-group navigation and are recorded in `apparatus-analysis.json`. No page, phrase or coordinate override was added for these fixes.

Both EPUB editions were rebuilt. Rendered excerpts confirm raised numeric markers, asterisk links and note backlinks at every reported location. Validation passes 176 unit tests, Black, whitespace checks and EPUBCheck for both editions with zero errors or warnings. All nine other book cases match unchanged baselines. Independent builds match both published Natural Mind editions; only their two reviewed baselines were updated. Regression paths now follow the PDFs moved to `input/`.

### Chapter-grouped Works Cited links

The reported PDF 53 citation after “normal” persons was styled but had no hyperlink: the profile had not declared Works Cited as numbered apparatus. Source pages 223–226 show nine chapter groups with respectively 2, 6, 6, 8, 4, 6, 9, 3 and 1 entries (45 total). Those source-specific facts now configure the existing shared endnote linker; no new page-specific linking code or text override is needed. All 21 recovered numeric superscripts link to the correct chapter-qualified entry, with backlinks to their exact body markers. Reused numbers in different chapters remain distinct. A generic regression covers that separation, repeated citations and unrecovered-marker chapter fallbacks.

The 24 remaining bibliography entries have chapter-level access and return links, not invented inline references. Missing recovered body markers by chapter are:

| Chapter | Numbers lacking recovered inline markers |
| --- | --- |
| 1 | 1 |
| 2 | 1 |
| 3 | 2, 4, 6 |
| 4 | 4, 5, 6, 7 |
| 5 | 1, 3, 4 |
| 6 | 1, 4, 5, 6 |
| 7 | 4, 6, 7, 8, 9 |
| 8 | 1, 2, 3 |

The apparatus report distinguishes 21 `superscript_links` from 24 `chapter_links`; `unlinked_endnotes: []` means all targets have navigation, not that every printed body marker has been recovered. Both editions preserve the corrected source rows, canonical block text and coverage counts from the previous build. Package inspection verifies PDF 53's marker targets the Cohen, Hirshhorn and Frosch chromosomal-damage entry and returns to the exact citation. EPUBCheck is clean for both; chapter-qualified links and exact backlink destinations were inspected.

### Machine research for missing inline markers

The user identified chapter 1 note 1 after “both heterosexual and homosexual”, chapter 2 note 1 after “repertory”, and chapter 3 note 2 after 1969. In this file the first passage is on PDF 18 (printed 10), despite the reported page 17. The hidden PDF layer contains the first and third digits but does not flag them as raised; line OCR retained them without typography. The “repertory” glyph is in the PDF layer but absent from the selected line OCR. These examples require looking at pixels rather than trusting source-font flags alone.

The existing variable-width pixel detector's fixed threshold of 200 converted this scan's beige paper into nearly solid black. Local experiments confirmed no components or baseline were recoverable. A separate opt-in research stage now normalizes scan contrast before reusing the shared glyph geometry, local Tesseract and source-text placement helpers. It inventories missing chapter-local labels and scans only rows between known neighboring citations. Thin serif 1s receive additional unwhitelisted tight single-character readings. Repeated modes are correlated observations, not independent engines. Consistent readings, unique placement and increasing chapter order are all required; a missing number alone is never sufficient. Source punctuation is retained, so the curly closing quote on PDF 18 is not flattened by the line witness.

The initial pass recovered 19 of the 24 missing markers without remote models. All 19 were checked against scan crops. Three already had the right digit in OCR and only gained superscript/link handling; 16 rows received marker-only character repairs or insertions. No ordinary wording or punctuation changed. At that checkpoint, both editions had 40 numeric citation links and five chapter-level fallbacks among 45 Works Cited entries, plus the existing 17 linked asterisk notes. At that checkpoint, missing inline markers were chapter 3 note 4, chapter 4 notes 5 and 7, chapter 5 note 3, and chapter 8 note 3. That initial chapter 8 search encountered both a plausible numeric glyph and a true footnote asterisk with misleading OCR readings; neither was automatically accepted. `missing-endnote-review.html` shows candidate crops and readings, including rejected observations.

Validation passes 187 tests, including synthetic beige-paper/narrow-glyph geometry, exact year anchoring, punctuation preservation, chapter resets, cache replay, missing/contradictory readings, duplicate placements, existing-link protection and detached-fragment accounting. The canonical-text rendering check now excludes generated backlink labels while still comparing source text. Both EPUBs pass EPUBCheck without errors or warnings; exact marker positions and backlink targets for the three reported examples pass package inspection. Independent cached builds match both published editions, only their baselines change, and all nine earlier book cases remain unchanged.

The five user-located remaining markers are PDF 70→4 (after “Natural History”), 101→5, 103→7, 115→3 and 201→3. Cached PP-OCRv4 correctly retained all five digits; cached Apple Vision omitted four or read symbols, so this is evidence about these frozen runs rather than an engine-wide accuracy claim. Contrast normalization closed the openings in tiny 3/5 glyphs, while a title followed by a parenthesis failed the punctuation guard. PDF 103's 7 was a closing quote in selected OCR.

Shared recovery now corroborates the exact word gap against existing extraction caches and retries the original grayscale glyph. Two agreeing raw reads suffice; one raw read additionally requires two normalized reads and an independent engine. Conflicting raw numeric reads still abstain. The title-parenthesis path masks the observed glyph and reinserts it at its measured position before matching the cached witness. A closing quote can become a digit only with this source/cross-engine evidence. Positive asterisk readings reject a competing footnote candidate without treating silence as rejection. All five source lines were visually checked; only their marker tokens change relative to the 40-link checkpoint.

Both rebuilt editions contain all 45 exact numeric citation links with backlinks and zero chapter-level citation fallbacks. Disambiguating PDF 201 also permits its real asterisk footnote to link, bringing symbol links to 18. No book-specific phrase/page/coordinate override, new OCR model or remote request was needed. The review HTML exposes raw grayscale readings and corroborating engine rows alongside source crops. Validation now passes 191 tests, Black checks 47 Python files, and both EPUBs pass EPUBCheck with zero errors or warnings. All nine earlier book cases match unchanged baselines.

### Shared evidence and chapter-wide research alignment

The initial scanned-note selector already used chapter-wide increasing paths. Its confidence scale now comes from a shared `MarkerEvidence` adapter, retaining its previous decisions. Missing-citation research uses that representation and a separate conservative application policy: consistent measured glyph reads remain mandatory, while deterministic dynamic programming jointly evaluates competing observed placements. Each proposed assignment is compared with the best path excluding it; the required margin of 3 exceeds a single adjacency bonus of 2. Missing labels remain missing unless pixels supplied a reading. Alternatives from the same glyph, overlapping source crops and overlapping text edits cannot coexist.

Anchor bounds now use only existing markers present in every longest nondecreasing anchor sequence. Tied/conflicting or out-of-range anchors remain unchanged and are explicitly quarantined; unaffected chapter regions can still be researched. Reports retain unplaced/rejected regions as well as successful candidates, with evidence rejection reasons and sequence margins. Cache fingerprints cover extraction helpers separately from selection semantics. Synthetic tests compare scores and exclusion margins with exhaustive enumeration, and cover duplicate placements, contradictory readings, missing labels, overlapping edits/crops, repeated anchors and anchor conflicts. These scores are hand-set heuristics, not calibrated probabilities; adaptive typography, unified geometric placement and held-out crop evaluation remain further work. Validation passes 203 tests and Black checks 49 Python files. Both Natural Mind editions retain exactly the previous EPUB, corrected text, canonical model and coverage, including 45 numeric citation links and 18 symbol links. All nine earlier cases match unchanged baselines; both EPUBs pass EPUBCheck without errors or warnings.

A further safety pass addresses nonadjacent glyph reuse: adjacent edge checks alone could admit alternatives from one physical glyph separated by another marker. Connected duplicate-crop groups with inconsistent text positions are now quarantined before alignment, and the cached compatibility graph is reused for exclusion checks. Rejected OCR alternatives cannot supply evidence or adjacency bonuses; unresolved numeric competitors remain blockers rather than disappearing from the audit. Repeated consistent raw readings of another digit reject normalized-only options, while mixed noisy retries preserve the existing conservative fallback. The shared adapter also retains legacy `retry_readings`. Synthetic regressions cover nonadjacent/transitive collisions, rejected sequence bridges and raw-versus-normalized contradictions. The expanded suite passes 206 tests; both Natural Mind editions and their research JSON/HTML replay exactly against independent builds, retaining all 45 numeric links. Both EPUBs pass EPUBCheck without errors or warnings.

### Retry placement caching and performance telemetry

`scanned-note-placements` now caches complete verified text placements, including empty results, separately from glyph-reading retries. Cache keys include source PDF and page, row text/box, complete retry readings/box, Python/OCR/traineddata/image-library versions and placement/render helper semantics. Errors are not cached, and mismatched cache envelopes fail closed. Build-local LRU reuse retains two grayscale pages, 128 line OCR results keyed by pixels/options/backend and eight merged engine-page row sets; mutable results are isolated from callers.

`performance.json` records total and named-stage timings plus scoped OCR/render/cache counters. It is intentionally excluded from deterministic snapshots. Times can overlap for nested calls, and OCR/render counters cover the shared scanned-note primitive and marker resources rather than every backend or exported review/figure crop. Telemetry wrappers preserve extraction-helper introspection.

| Case | Initial cache-populating marker OCR calls | Fully warm marker OCR calls | Warm placement cache hits | Initial / warm build seconds |
|---|---:|---:|---:|---:|
| Natural Mind | 289 | 0 | 0 | 99.2 / 62.5 |
| Natural Mind reading edition | 262 | 0 | 0 | 81.1 / 57.5 |
| A Feeling for the Organism | 10 | 0 | 8 | 39.2 / 39.1 |
| Organism reading edition | 10 | 0 | 8 | 39.4 / 35.6 |
| Daimonic Reality | 365 | 0 | 251 | 73.0 / 4.7 |

A final Natural Mind timing breakdown attributes 41.3 of 62.5 seconds to PDF numeric-superscript attachment, while missing-marker research takes 0.26 seconds and cache preflight 0.15 seconds. This identifies source-PDF superscript matching as a separate next optimization target.

These are observed runs, not a controlled speed benchmark; machine load and first-run cache population differ. The operation counts demonstrate the avoided work. All five final published builds have zero retry-placement cache misses. Validation passes 214 tests; all 11 initial regression cases and all five affected warm cases match unchanged book baselines. Published EPUBs, corrected rows, canonical models and source coverage match their independent warm outputs. Tests cover successful and empty cache replay, source/page/row/readings/runtime invalidation, failures and malformed envelopes, eviction, changed engine files, mutation isolation and build-scope telemetry.

### PDF superscript attachment optimization

Profiling the first 50 Natural Mind pages with assembled corrected rows took 12.75 seconds: two `get_text("dict")` calls per page consumed 6.95 seconds, while scanning every span for every matched prose line performed roughly 800,000 point-containment checks. The extraction unnecessarily serialized scanned-page images.

Attachment now performs one text-only extraction, shares its data with native row reconstruction, and skips span geometry when the matched source line has no numeric superscript candidate. The same 50-page profiling pass takes 0.78 seconds. Existing callers of native extraction retain their default behavior through an optional keyword argument. No matching, geometry or OCR acceptance threshold changed.

Published source/reading builds observed attachment times of 3.06/4.65 seconds and total times of 29.68/35.42 seconds; the prior source build measured 41.34/62.52 seconds. These timings include variable machine load and concurrent regression work, so they are observations rather than a controlled benchmark. Both published editions match their independent regression outputs, including corrected rows, models, coverage and EPUB bytes. A new embedded-image fixture verifies identical extracted text/offsets/font geometry and a single extraction per attachment call. All 215 tests and all 11 book regressions pass against unchanged baselines (`work/regression/run-vsbosnns/report.json`). Both published Natural Mind editions pass EPUBCheck with no errors or warnings.

### Scanned illustration paper cleanup and compact JPEG export

*A Feeling for the Organism* opts into the shared `figure_cleanup: "white"` pass. All 18 reviewed illustrations are monochrome (four sparse diagrams and fourteen photographs); `figure_color_mode: "grayscale"` records that source fact without book-specific pixel rules. The [figure review](../output/feeling-organism/figure-cleanup/index.html) retains originals and final exports; a refreshed eighteen-image contact sheet is in `work/feeling-organism/cleanup-final-contact.jpg`. Original cropped pixels and normalized RGB lossless PNG masters remain in that external review folder. Neither audit copy is embedded in the EPUB. Processing starts from decoded source crop pixels and performs a single final JPEG encode, without intermediate compression/decompression.

The paper estimator samples light exposed borders, rejects strongly colored/dark backgrounds, and robustly fits a coarse quadratic illumination field. Dense regions are conservatively protected as photographs; low-contrast dense artwork does not become blank paper. Narrow exposed margins can use lower-dimensional fits. Normalization preserves gray strokes continuously instead of binarizing them. Independent checks assert exact protected photo pixels in lossless masters; final grayscale/JPEG export is evaluated separately. Automatic grayscale selection requires neutral pixels everywhere (at most two channel levels of spread), so even a small colored mark prevents it. Tinted scans require a reviewed monochrome profile fact; this classifier does not infer artwork semantics.

Lossless PNG exports initially produced roughly 26 MiB EPUBs. JPEG quality 95 slightly reduced faint-stroke contrast on two diagrams; quality 98 for line art and 95 for photographs passed the independent contrast gate. Optimized progressive encoding reduced the eighteen RGB assets from 9,587,801 to 9,166,844 bytes (4.39%), with identical decoded pixels for every image. Grayscale further reduced those assets to 7,917,984 bytes (13.62%); both EPUB editions are about 8.28 MiB. Color removal is intentional and distinct from JPEG error. Maximum measured JPEG luminance RMSE is 1.742 gray levels; diagram local-contrast retention is 99.82–99.92% under the documented diagnostic. These metrics complement source/image review rather than proving complete semantic preservation.

Both edition verifiers check all eighteen packaged assets against their review exports and compare text, canonical structure, notes, coverage and non-image ZIP members with the pre-cleanup builds. No package text members changed. Both final EPUBs pass EPUBCheck without errors or warnings. Synthetic checks cover faint strokes, protected halftones, narrow margins, colored marks, low-contrast dense regions, unsafe fits, source immutability, deterministic encoding, retained originals and verifier failure reports. All 226 unit tests pass and Black is clean. The previous nine unaffected book cases retain unchanged accepted baselines (`work/regression/run-8jyd4a_1/report.json`); only the two reviewed Organism image baselines change. Both isolated Organism rebuilds match the delivered snapshots exactly and pass another byte-identical replay (`work/regression/run-qw2wanfr/report.json`).

### Pixel-backed italic verse recovery

The Einstein epigraph on PDF 139 (printed 107) was preserved as a two-line verse block, but fresh OCR had no emphasis spans and the hidden PDF font labels were regular. The source scan visibly italicizes the quotation and keeps its credit upright. A shared opt-in `recover_scan_italics` pass now measures glyph slant directly in existing verse regions. It does not contain an author, phrase, page, or coordinate whitelist; the book's existing verse-region facts determine the bounded research scope.

The detector crops at 400 dpi, separates connected glyphs, and tries 33 horizontal shears from -0.4 to +0.4. Vertical projection concentration estimates stem direction. At least eight usable glyphs and two upright surrounding prose lines are required; a page-reference lean over 0.1 is uncertain. At least 80% of glyphs must support a relative shear of 0.14 and a concentration gain of 1.12. Comments explain these empirical conservative guards. Mixed/short/low-contrast lines abstain; whole-line emphasis is appended without replacing existing spans or changing wording. Measurements and source crop methods are retained in `corrections-applied.json` as `scan-italic-evidence`.

The Einstein line passes with 88% supporting glyphs, while its upright credit has no supporting votes. The same general pass recovers both italic Pascal lines on PDF 229 (printed 197), with approximately 97% and 93% support; its short credit abstains. Both scans were visually checked. Exactly three OCR rows gain `em` spans, producing changes in two XHTML chapter members per edition. Removing those emphasis tags reproduces the earlier XHTML bytes exactly. Text, note apparatus, coverage, image assets, metadata and model structure remain unchanged. Exact publisher font-family recovery for author credits is not attempted.

Validation passes 231 tests, including roman/bold/sans negative controls, mixed styles, insufficient glyphs/contrast, uncertain page reference, opt-in scope and preservation of existing spans and credits. Both delivered EPUBs pass EPUBCheck with zero errors/warnings; all eighteen figure verifications still pass. Nine earlier book cases pass unchanged (`work/regression/run-kctq9um5/report.json`); both reviewed Organism snapshots match independent rebuilds byte-for-byte (`work/regression/run-72d8afrz/report.json`). Only those two typography baselines were updated. Browser visual preview was unavailable because the browser URL policy disallows local file URLs; validation used source scan images, synthetic raster fixtures and exact generated XHTML comparisons instead.

### Conditional chapter-end note navigation

The renderer previously inserted “Notes for this chapter” immediately after every configured chapter heading under chapter-reference mode, even when every inline reference had been recovered. Navigation now follows the linker's actual chapter-start fallback backlinks. Fully linked chapters have no extra shortcut; chapters with missing markers retain a shortcut after their body and footnotes. Stable chapter-start anchors and every inline/backlink target remain unchanged.

Both Organism editions remove exactly thirteen redundant links while preserving all 157 inline references. Package comparison against the pre-change builds finds only those removed paragraphs; canonical models, corrected rows, notes, coverage, images and metadata are byte-identical. The Natural Mind also has complete inline references. Daimonic Reality still has 73 unlocated references across 21 chapters, so those chapters need end-of-chapter navigation. Regression coverage checks repeated note numbers, repeated citations, missing markers in one chapter, fully linked neighboring chapters, and disabled fallback mode.

All five affected published EPUBs pass EPUBCheck with no errors/warnings. Both Organism editions retain all eighteen passing figure checks, and the 231-test suite passes. All eleven cached book snapshots match the reviewed baselines after exactly five navigation-only baseline updates. Six unaffected cases pass unchanged; the affected cases preserve all non-navigation package bytes and canonical content (`work/regression/run-rc7i3dm1/report.json`, with expected pre-acceptance navigation differences; final review in `work/chapter-navigation-verification.json`). Both delivered Organism packages match their independent replay snapshots exactly. The other affected published editions were refreshed from those reviewed isolated outputs.

## OCR experiment

Ground truth comprises three visually transcribed excerpts, 1,854 normalized characters in total. Evaluation normalizes whitespace, typographic apostrophes/dashes and line-end hyphenation; it does not score italics, reading-order fidelity beyond these regions, page structure or semantic typography. Sample selection is exploratory and too small for a whole-book estimate.

| Sample (PDF page) | Embedded OCR CER | Vision CER | PP-OCRv4 mobile CER | Tesseract CER | OCRmyPDF CER |
|---|---:|---:|---:|---:|---:|
| Body paragraph (21), 460 chars | 0% | 0% | 0% | 0% | 0% |
| Permissions (4), 514 chars | 0.195% | 22.763% | 2.140% | 0.195% | 0.195% |
| Small footnote (32), 880 chars | 0% | 0.114% | 0.568% | 0% | 0.227% |

Vision's permissions failure included omitted lines. Tesseract was chosen for body text after these samples and disagreement inspection. Vision handled separated bibliography numbers and index typography better in inspected examples. These are book-specific observations, not a global ranking of engines. Deskew/clean through OCRmyPDF did not improve the three samples sufficiently to replace direct Tesseract. The embedded layer is surprisingly useful and should always be evaluated before discarding it.

Raw evidence: `output/ocr-benchmark.json`, `config/ocr-benchmark.json`, and cached per-page OCR. `scripts/benchmark.py` is deliberately a benchmark-specific harness; it is not the generic pipeline entry point.

## Correction experiment and policy

The implemented lexical stage combines the LibreOffice US-English Hunspell dictionary, Spylls affix lookup, SymSpell candidate generation/split suggestions, and frequency filtering. A protected lexicon preserves historical vocabulary such as “marihuana,” culturally specific names and technical terms. It currently yields 99 source-edition / 92 reading-edition distinct rare-word proposals, many legitimate words or compounds. Dictionaries do not establish that a token is wrong.

Scan inspection found literal printed typos including “adminster,” “anixous,” “ambivalance,” “experiencd,” and “nineteeth.” It also found a printed `experi-` / `ience` split. Preserve these in a diplomatic transcription; apply an explicit editorial overlay in a reading edition. Both outputs are generated and all seven editorial substitutions have occurrence preconditions and audit records. Accented “epená” misread as “epend” is an actual OCR correction. Whole-word overlays prevent corrupting “dependence.”

The approved five-question [TypeSafe Choice](https://docs.typesafe.ai/primitives/choice) experiment used `jev-1.13.0`, 2,226 input tokens and 245 output tokens. It chose administer, ambivalence, anxious, experienced and nineteenth with reported confidence 0.69–0.80. It is useful evidence for lexical ranking, not proof of source transcription; no reliability or calibration estimate can be drawn from five examples. Exact request and response are in `output/jev-proposals.json`. The [API contract](https://docs.typesafe.ai/api) is integrated without placing credentials in artifacts. No general generative model rewrote book prose.

Recommended correction cascade:

1. Normalize Unicode and OCR geometry; retain original text, boxes, source hashes and engine outputs.
2. Recover paragraph/line boundaries before spell checking; distinguish end-of-line hyphens from lexical compounds.
3. Propose token replacements from witnesses, confusion classes and lexicons; protect rare names and historical spelling.
4. Rank candidates with context and optional local language models; abstain on small margins or conflicting evidence.
5. Review against cropped scans. Record printed typos separately from OCR errors, and store exact preconditions.
6. Use remote classification or an OpenAI-compatible model only for unresolved proposals, with pinned model, cached requests, bounded options, provenance and no direct mutation.

## Tested versus future work

| Tool / method | Status here | Next useful experiment |
|---|---|---|
| Tesseract 5.5.3 English | All 242 pages, direct TSV; selected body primary | Alternative traineddata, PSM by region, confidence calibration |
| Apple Vision revision 3 | All 242 pages, accurate local OCR | Independent platform/version rerun; layout-aware crop sensitivity |
| Paddle PP-OCRv4 through RapidOCR 3.9.2 / ONNX | All 242 pages, mobile English CPU | PP-OCRv5/server recognition on held-out difficult regions; not equivalent to testing the full PaddleOCR document stack |
| OCRmyPDF 17.13.0 | Three pages, deskew + clean, sidecar-only | Skewed/noisy scans; ablate deskew and cleaning independently |
| pdf-craft 0.2.1 | Installed implementation inspected, not run end to end | Isolated compatible environment and small comparative fixture; do not confuse installed version with current GitHub code |
| Marker / Docling | Not installed or tested | Isolated environments; benchmark layout/footnotes/figure extraction, not just Markdown appearance |
| Hunspell / Spylls + SymSpell | Implemented proposal stage and dictionaries | OCR confusion costs, names/domain lexicons, frequency threshold ablation |
| KenLM | Optional adapter implemented; no model installed or evaluated | Pin a licensed independent corpus/model; compare context ranking against frequency baseline; prevent training/test leakage |
| LanguageTool | Local-server adapter implemented; not exercised | Rule allowlist for OCR errors; measure overcorrection on historical prose |
| Morfologik | Not integrated or tested | Use through LanguageTool where appropriate; evaluate inflection support on a multilingual corpus |
| SciPy statistics | Recurrence Beta intervals and robust geometry implemented | Annotated header/footer precision/recall; clustering with page-dependent translations; held-out-book validation |
| jev / TypeSafe Choice | One approved cached five-question experiment | Blinded mixed set of valid rare terms, OCR errors and printed typos; abstention calibration |
| OpenAI-compatible LLM | Not used | Final fallback only, candidate-bound edits and replayable responses |

Relevant primary projects: [pdf-craft](https://github.com/oomol-lab/pdf-craft), [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR), [RapidOCR](https://github.com/RapidAI/RapidOCR), [Tesseract](https://tesseract-ocr.github.io/), [OCRmyPDF](https://ocrmypdf.readthedocs.io/), [Marker](https://github.com/datalab-to/marker), [Docling](https://github.com/docling-project/docling), [SymSpell](https://github.com/wolfgarbe/SymSpell), [LanguageTool](https://github.com/languagetool-org/languagetool), [KenLM](https://github.com/kpu/kenlm), [Morfologik](https://github.com/morfologik/morfologik-stemming), [EPUBCheck](https://github.com/w3c/epubcheck).

## Validation and remaining limits

EPUBCheck and internal XML/link checks are quality gates, not content correctness proofs. Twenty-seven regression tests cover lost high body text, chapter-opening cutoffs, horizontal OCR ordering, hanging index entries, compound hyphens, recurring margins, note continuation, generated links, malformed jev choices, ISBN checksums, wrong-edition rejection, XML-safe metadata and copyright-scan exclusion, canonical cross-page text, source coverage, overlay preconditions, diagnostic geometry and whole-book vocabulary signals. The EPUB ZIP uses stable ordering, compression and timestamps. Repeat builds from the same cached OCR/profile/runtime are byte-compared; fresh inference or different rendering/library/runtime versions are outside that guarantee.

Offline HTML/CSS renders were inspected at a narrow reading width for title, chapters, bibliography and index. This caught merged index entries and a clipped emblem, both repaired. WeasyPrint is a layout proxy, not an EPUB reading-system compatibility test. Browser preview was unavailable because localhost navigation was blocked. Actual Apple Books/Kobo/Kindle rendering and accessibility review remain unverified.

The prototype does not yet fully recover inline italics, superscript bibliography citations, all quotation block styles or exact individual footnote-symbol links. Notes link by original page group. Chapter/page roles, note regions and exceptional cutoffs are curated. The figure extraction path is source-preserving but has not been evaluated on a diagram-rich book. A zero disagreement queue is not full proofreading; lexical proposals and unanimous OCR mistakes can remain. End-of-line dehyphenation now uses one shared lexical policy, including twelve scan-verified rare joins; ambiguous joins can still require review.

Next milestone: a stratified, independently transcribed evaluation set with pages containing footnotes, columns, figures, italics, skew, damage and rare vocabulary from several books. Measure character/word error rates, paragraph and reading-order errors, header/footer false removals, note/figure completeness, and correction precision/recall separately. Compare a frozen baseline against each preprocessing, OCR and correction change, with source-review workload and false-edit counts. Only then promote learned/Bayesian layout classifiers or language-model rankers into automatic decisions.

## ISBN and cover enrichment

Free [Open Library edition-by-ISBN endpoints](https://openlibrary.org/dev/docs/api/books) and the [Covers API](https://openlibrary.org/dev/docs/api/covers) were exercised. The legacy batch Books endpoint returned 404 in this environment; edition JSON succeeded. A keyless Google Books fallback probe returned HTTP 429/quota unavailable, so Google is not a working integration here. The implementation uses the successful Open Library endpoint and caches exact response bytes.

The [1972 edition record](https://openlibrary.org/books/OL4919777M) matches the ISBNs in the [exact scan's source metadata](https://archive.org/metadata/naturalmindneww00weil). The paperbound ISBN resolves to a distinct April 1973 record, retained separately. Source-verified metadata remains authoritative for title and author. API enrichment supplies subjects, classifications and library identifiers without silently merging editions.

Cover checks were useful: ISBN 0395139368 returned a tiny title page rather than the cover; the paperback cover returned 404; the work-level cover depicted a revised edition. The [exact-source cover download](https://archive.org/services/img/naturalmindneww00weil) matches the blue scan cover, but is only 180 × 278. It is embedded as a supplementary source-cover reference, while the higher-resolution scanned image is the main cover. Image dimensions, format, checksum, URL, acceptance reason and source records are retained. The copyright scan and its facsimile page/link were removed at the user's request; only checked reflow text remains.

## Assembly and statistical review

The canonical book model now holds assembled paragraph/note text and character spans linked to source row IDs and bounding boxes. Rendering and lexical review consume that model. Page-break spans are annotations at character offsets; note references are attached to complete paragraphs, preventing links from splitting joined words. A coverage ledger rejects dropped or duplicated retained OCR rows.

Whole-book statistics run only after assembly. Smoothed chapter IDF is `ln((N+1)/(chapter_df+1))+1`, with body footnotes counted in their chapter and references/index counted separately. The report records token counts, block/page/chapter spread, case forms, maximum chapter TF-IDF and approximate enrichment against wordfreq's language prior. A zero background Zipf value is marked censored, not treated as proven absence. One-edit competing book spellings (including transpositions) raise review priority. Dispersed rare terms are proposed for scan-backed protection; neither high IDF nor repetition automatically changes the book or its dictionary.

The offline proofreading sheet combines these statistics with source crops and OCR witnesses. Browser decisions can be exported; they are not silently applied. This cheaply consolidates review evidence, but does not detect every real-word substitution or establish a full-book error rate. Optional KenLM and LanguageTool integrations remain unevaluated.

## Second benchmark: Shamanic Trance in Modern Kabbalah

Source: Jonathan Garb, University of Chicago Press, 2011; ISBN-13 9780226282077. Input: 288-page publisher PDF, SHA-256 `41b6ceef0f3eb6ad5cac685fc6dc1881ed9c9d35a83f6d9fcc3921c5bfd04cc4`. Source metadata and selectable font spans establish a native-text input, so no OCR or model inference is needed. Only the two cover pages contain images; no drawing objects or interior diagrams occur in this PDF.

The separate `config/shamanic-trance` profile maps dedication, preface, six chapters, epilogue, appendix, notes, bibliography and index. Native extraction preserves italic/bold/superscript ranges, normalizes nonprinting control characters and ligatures, and treats encoded discretionary hyphens explicitly. Source-rendered review confirmed ten rare intraword wraps and nine tracked headings. The shared lexical join policy was corrected after discovering that Hunspell accepts artificial hyphen compounds such as `friend-ship`; dictionary acceptance alone no longer proves a lexical hyphen.

The retained-row ledger passes for 12,163 body rows and four on-page footnote rows; 324 selected rows are explicitly excluded as margin/title material. All 745 numbered endnotes appear in complete per-section sequences, match 745 superscript references and have backlinks. Citation numbers at hanging content margins are kept inside their note, rather than incorrectly becoming new note entries. Index page/note references route to unique individual notes; ambiguous source references retain a page target. Full-model rendering checks verify every canonical block's text, excluding navigation annotations.

Whole-book analysis counts 103,487 narrative and attributed-endnote tokens across nine sections; bibliography, index and dedication are separate from narrative statistics. The review queue contains 1,609 rare lexical groups and four repeated-word diagnostics, mostly reflecting multilingual scholarly names/titles rather than demonstrated errors. There are 415 evidence-based proposals for vocabulary protection, never automatic dictionary additions. No whole-book proofreading accuracy estimate is claimed. Native typography support does not establish full italics recovery for scanned PDFs, and visual QA uses an offline rendering proxy rather than a physical EPUB reader.

Validation commands are the profile-specific pipeline command in README, `python -m unittest discover -s tests -v`, and `python -m black --check .`. The suite now has 36 tests, including native encoding and style spans, endnote sequencing, bidirectional links, transliteration tokenization, index note/range targets, source-chapter statistical attribution and native corpus rendering. Both original benchmark editions were also rebuilt and passed EPUBCheck after the shared changes.

## Native scholarly article: Letter Kills

The third input is `For_the_Letter_Kills_but_the_Spirit_Giv.pdf`, SHA-256 `d6b2a44e168adde1670c4d776f43007538369a0528beea71098eca38cde084a9`. It is a 24-page publisher PDF, with no interior raster images. Native extraction avoids OCR. The profile is `config/letter-kills/book.json`; artifacts live in `output/letter-kills`.

This case exposed opaque font names whose PDF flags omit italics, a small note marker without the superscript flag, rotated download watermarks, a heading with mixed-size punctuation, normal first-line-indented endnotes, a funding note attached to the title, and a quotation continued across pages. Reusable profile options resolve these cases without title-specific code branches. Exact correction overlays now update inline offsets, preventing a diacritic replacement from moving later note anchors. `Zość żydowska` and German umlauts were checked against rendered source pages; printed spelling is preserved even when it appears mistaken. The German line wrap `reli-giösen` uses reviewed join vocabulary. A profile-declared typography normalization restores 86 printed dashes extracted as double hyphens, retaining the original strings in the audit. Source-internal `re-form` supplies evidence for retaining that lexical hyphen.

The article has 89 sequential endnotes and 89 linked references, including the title reference; every note has a return link. Source coverage maps all 977 retained rows once and classifies 30 rows as excluded. The 24 rotated watermarks are separately audited before row grouping. A deterministic SVG title cover supports sources without cover artwork; chapter section anchors provide a nested EPUB 3 contents list. Crossref enrichment accepts DOI-only sources, verifies author/title identity including a profile-declared title footnote suffix, and retains source and final-journal dates separately. Cached public metadata is the only remote input; no remote inference is used.

Whole-article analysis runs after assembly, including notes attributed to the article. There is only one IDF document: all occurring narrative terms have the same chapter IDF, so it offers no discrimination for this input. Page spread, recurrence, edit-distance alternatives and the approximate general-language prior remain useful review signals. The offline review contains 136 lexical groups and zero additional structural diagnostics; neither figure is a proofreading error count. Printed errors remain separate from extraction corrections.

Validation includes exact rendered XHTML/canonical text comparisons, strict note sequences and target checks, source-row coverage, regression tests, a byte-identical repeat build, Black, and EPUBCheck. Cover, title, opening text and endnotes were visually inspected with a WeasyPrint/PyMuPDF rendering proxy; this does not replace testing on actual EPUB reading devices.

## Scanned illustrated biography: A Feeling for the Organism

The fourth input is a 276-page scan, SHA-256 `59448237b057bd8b96cf9c5e33b50f6b43efc6e0123e170158984319b48f1a6a`. All pages were processed locally at 300 dpi with Tesseract, Apple Vision and RapidOCR's Paddle PP-OCRv4 English recognizer. Embedded OCR was also retained as a witness. Source inspection favored Vision for this highlighted scan; lower lexical review counts support triage but are not a word-error-rate measurement. No remote model was used for the initial conversion; a later approved Jev citation-context experiment returned uncertain without editing the book.

The seven index pages exposed the cost of a guessed gutter: a 0.52 split clipped initial letters in the right column. Measuring actual text bounds yielded a reviewed 0.485 split; all three engines were rerun for those pages. Explicit blank-page exclusions prevent bleed-through entering the canonical text. Consecutive quotation lines with matching indentation now assemble into paragraphs, and a checked cross-page continuation restores `analo- / gous`. A scan mark after `ap-` required a source-checked punctuation correction before normal dehyphenation could restore `approach`.

A reusable figure module extracts 18 photographs and diagrams with normalized profile coordinates, preserves captions separately, and audits image hashes and insertion positions. Visual inspection of crops caught clipped labels in the mitosis, corn life-cycle and breakage-fusion-bridge diagrams. Figure interiors are excluded from OCR text, avoiding duplicated diagram labels. Glossary entry labels define 87 independent paragraphs and italic spans; entry boundaries also prevent a new entry merging with the previous page's definition.

All 157 notes pass per-section sequence checks. The scan does not provide reliable inline superscript recovery, so navigation explicitly links chapters to their notes and notes back to chapters. This is a documented compromise, not reconstructed inline reference evidence. The supplied 1000 × 1500 cover supersedes a matching but smaller online image. ISBN API responses contain misspelled title, publisher and conflicting dates; exact source-reviewed overrides preserve raw cached records while retaining the scan's 2003 edition identity.

Seven exact OCR corrections and eleven separately recorded printed-typo corrections were verified against source crops. Examples of actual printed mistakes include `aminc`, `physican`, `chromsomes`, and a duplicated `was` across pages. Independent OCR agreement can therefore support a wrong printed spelling: it does not justify silently treating that spelling as an OCR error. The reading edition applies the editorial overlay; the default edition preserves those printed forms. Whole-book vocabulary analysis and source-linked review run after assembly. Unreviewed suggestions remain suggestions.

Validation checks source-row coverage, canonical text against rendered XHTML, figure inventory, glossary boundaries, strict note sequences, EPUBCheck, repeat-build bytes and the 48-test regression suite. Visual QA uses decoded UTF-8 XHTML through a WeasyPrint/PyMuPDF proxy; feeding XHTML as a filename without explicit decoding caused mojibake in the proxy, while the EPUB bytes were correct. This proxy does not establish behavior in actual EPUB readers, nor does the review queue establish complete proofreading.

Final artifact checks passed for both editions: 8,124 retained body rows mapped exactly once, 502 explicitly excluded rows, 18 figures, 157 endnotes and no orphan notes. Both EPUBs have zero EPUBCheck errors/warnings and byte-identical cached repeat builds. The source-spelling review contains 182 lexical groups and ten diagnostics; the reading-edition review contains 174 groups and eight diagnostics. Black and all 48 tests pass. The native Garb book and Huss article were also rebuilt to separate regression directories to check the shared assembly changes.

Source-layout follow-up: on PDF page 20 (printed xviii), scan skew displaced the left margin enough to misclassify “Kimber Award…” as a paragraph opening. A source-checked cross-page continuation now joins it to page 19's “chosen for the”. The following Marcus Rhoades passage is explicitly classified as a quotation. The profile disables first-line indentation inside block quotations; the default renderer behavior for previous books is unchanged. A regression checks the continuation and quotation role, and another checks profile-specific quotation indentation (49 tests total).

The page-20 override was subsequently removed in favor of generic inference. `scripts/paragraph_layout.py` fits deterministic robust left/right margin envelopes from long body rows. Indentation is measured relative to the sloping margin at each line's vertical coordinate; a sustained inset run needs evidence at both edges before becoming a quotation. This recovers the unindented page continuation and quotation without matching “Kimber Award” or page 20. Quotation first-line indentation is carried by inferred block evidence, rather than a book-wide styling switch. Sparse or unstable pages fall back; native fonts and hanging reference/index layouts retain the existing path. Models, fallback reasons and inferred quote row IDs are audited. The 54-test suite includes synthetic skewed pages, ordinary paragraph indentation, rejection of one-sided insets, sparse-page fallback, and native/reference exclusions.

On this scan, 191 page-column fits are accepted and 26 fall back; 25 blocks are classified as quotations. A sample contact sheet was compared to the scan's inset text, including the reported passage. Compared with the previous assembled text, the only additional wording change is restoration of `under- / standing` to `understanding`. All retained source rows remain covered exactly once. The native book/article rebuilds are byte-identical to their earlier regression outputs. Both Natural Mind editions retain identical text and coverage; paragraph segmentation can improve under the generic scan model, so byte identity is not claimed for those OCR books. The new AGENTS.md rule requires general pipeline fixes and regression checks, with source-specific evidence retained in profiles only where needed.


## Per-book build recipes and follow-up evidence

### The Natural Mind

The current reading copy is [the corrected EPUB](../output/reading-edition/the-natural-mind-corrected.epub). The [source-spelling edition](../output/the-natural-mind.epub) preserves six verified printed typos and one source line-wrap punctuation artifact. Both preserve the original cover, publisher emblem, back cover, verse, bibliography and index; the scanned book has no interior diagrams. The copyright text is reflowed without embedding the copyright-page scan. Continued footnotes are grouped and linked from their original pages. Index numbers link back to the text.

Primary body OCR is Tesseract. References/index use Vision. Paddle PP-OCRv4 through RapidOCR and the embedded OCR are witnesses. All three fresh engines were run over all 242 pages; the embedded layer is a fourth comparison source. OCRmyPDF was tested on three selected pages, not the whole book.

For this scan, Open Library and the exact Internet Archive source record identify the 1972 edition, ISBN-10 **0395139368**, ISBN-13 **9780395139363**. The printed paperbound ISBN is retained as a related print identifier, rather than merging its 1973 edition record into this one. EPUB metadata includes full title, author/author sort/role, language, publisher, publication date, rights, description/note when available, 15 topical subjects, Library of Congress/Dewey classifications, LCCN, OCLC, Open Library ID, print extent/place and source links. Unsupported or absent fields are left absent; no synopsis or edition facts are invented.

The matching blue cover was downloaded from the exact source item and embedded as `downloaded-cover.jpg` with a supplementary non-linear cover page. Its 180 × 278 resolution is lower than the scan, so the 1048 × 1620 scanned cover remains the main cover. A future visually approved JPEG at least 300 pixels on its short edge replaces the main cover automatically. The original ISBN API image was rejected for main-cover use: it is a 128 × 187 title-page thumbnail. A work-level cover from a revised edition was also rejected. No copyright-page scan is included.

The five-candidate experiment was explicitly approved and executed. All five proposed the expected lexical replacements; all five were actually printed typos, established by scan inspection. This illustrates why lexical plausibility alone cannot establish an OCR error. The default source-spelling edition preserves them; `--editorial` applies the separately recorded source-verified editorial overlay. No OpenAI-compatible fallback was used.

## Shamanic Trance in Modern Kabbalah

[The EPUB](../output/shamanic-trance/shamanic-trance.epub) is built from `Shamanic trance.pdf` with its own profile and output directory:

```sh
.venv/bin/python scripts/pipeline.py 'Shamanic trance.pdf' \
  --profile config/shamanic-trance/book.json \
  --work work/shamanic-trance --output output/shamanic-trance
```

This input is a publisher PDF with native text. Its profile selects `text_source: native`, so the pipeline extracts text, geometry and font spans locally without OCR. It preserves italics, bold headings, superscript references, quotations and the two-column index. The book has no interior figures; its original cover and back cover are retained. The downloaded matching ISBN cover is embedded as a source reference. Open Library supplies edition metadata for ISBN-13 9780226282077, with checked author/title/publisher identity and cached provenance.

All 745 numbered endnotes have individual links and backlinks. Endnote sequence checks fail on gaps, duplicates or references without targets. Index references link to individual notes where the source page/number identifies one unambiguously, otherwise to the printed page. Notes contribute vocabulary evidence to their source chapter; bibliography and index counts remain separate. Unicode modifier letters in transliterations such as `Baʿal` and `Peʿamim` stay within their words.

Ten rare line-wrap joins and nine tracked-heading normalizations were inspected against the source. Hunspell's ability to accept arbitrary hyphen compounds no longer overrides a supported joined spelling. Native publisher text can supply a single complete in-book spelling as join evidence; OCR still requires recurrence. All transformations retain source-row mappings and audit records. The [proofreading sheet](../output/shamanic-trance/proofreading-review.html) is offline and uses lazy image loading; it is large because of the scholarly vocabulary and source crops. Proposals remain unverified until reviewed, and no remote model was used for this book.

## For the Letter Kills, but the Spirit Gives Life

[The EPUB](../output/letter-kills/letter-kills.epub) converts the 24-page Boaz Huss advance article locally:

```sh
.venv/bin/python scripts/pipeline.py For_the_Letter_Kills_but_the_Spirit_Giv.pdf \
  --profile config/letter-kills/book.json \
  --work work/letter-kills --output output/letter-kills
```

The source has native text and no interior images or cover artwork. A deterministic typographic SVG cover replaces the absent cover. The EPUB retains the abstract, section navigation, quotations, inline italics, original page anchors and all 89 numbered endnotes, with backlinks including the title's funding note. Copyright is reflowed; publisher download watermarks are excluded before geometric row merging.

Free Crossref DOI metadata is checked against the profile's author/title and cached with checksums. Run `scripts/metadata.py --profile config/letter-kills/book.json --cache work/letter-kills/metadata/cache --offline` to replay enrichment, or omit `--offline` to fetch an uncached response. Metadata distinguishes the 2020 advance article from the final journal publication and pagination. No ISBN is invented. The title's printed spelling “Mysticim” and other printed mistakes are retained; seven source-checked diacritic corrections restore extraction errors. German `reli-giösen` is joined using reviewed vocabulary; source-supported `re-form` remains hyphenated.

The [offline proofreading sheet](../output/letter-kills/proofreading-review.html) contains 136 lexical review groups. This single article is one IDF document, so chapter IDF cannot distinguish terms here; recurrence, page spread and the language-frequency prior still supply review evidence. A successful source-row coverage check and EPUBCheck do not establish complete proofreading or reader-device compatibility.

## A Feeling for the Organism

[The reading edition](../output/feeling-organism/reading-edition/feeling-organism-corrected.epub) converts the 276-page scanned Evelyn Fox Keller book. [The source-spelling edition](../output/feeling-organism/feeling-organism.epub) retains verified printed mistakes; both correct verified OCR errors.

```sh
.venv/bin/python scripts/pipeline.py 'A feeling for the Organism.pdf' \
  --profile config/feeling-organism/book.json \
  --work work/feeling-organism --output output/feeling-organism
# Reuse complete OCR caches and apply the reviewed printed-typo overlay.
.venv/bin/python scripts/pipeline.py 'A feeling for the Organism.pdf' \
  --profile config/feeling-organism/book.json \
  --work work/feeling-organism --output output/feeling-organism/reading-edition \
  --skip-extraction --editorial
```

Local Apple Vision is the selected OCR source, with Tesseract and RapidOCR/Paddle witnesses. The book includes 18 source-cropped photographs and diagrams, reflowed captions, 87 italicized glossary labels, a two-column source index reflowed into reading order, and 157 sequential endnotes. Scan-based recovery supplies individual superscript links and return links for confidently located references. Unresolved references retain chapter-level navigation; `scanned-endnote-analysis.json` lists the missing numbers and uncertain candidates.

The supplied 1000 × 1500 JPEG is the cover. ISBN-13 **9780805074581** corresponds to ISBN-10 **0805074589**. Free ISBN APIs provide cached metadata, but the scan identifies the 2003 Owl Books edition; conflicting API dates and title/publisher errors remain in provenance, with explicit source-reviewed overrides. No copyright-page image is included.

Reusable profile additions include checked local covers, normalized figure crops, blank-page exclusions, glossary entry boundaries, continuous indented quotation lines, explicit cross-page continuation preconditions, and chapter-level endnote navigation. The [offline proofreading sheet](../output/feeling-organism/reading-edition/proofreading-review.html) retains uncertain lexical and structural proposals for review. Complete inline italics recovery and complete proofreading remain limitations. A separately approved Jev citation-context experiment returned `uncertain`; it made no book edits.

`recover_scanned_endnotes` enables a shared local recovery pass for configured endnote chapters. It detects small raised glyphs in 400-dpi scan pixels, estimates the skewed prose baseline, and recognizes isolated crops with three Tesseract segmentation modes. Character boxes and matched neighboring words locate the reference in the corrected text, including markers confused with quotes or letters. An increasing-sequence model spans each complete chapter and resets at its boundary; it ranks alternative readings against the expected endnotes, rejects tied or conflicting assignments, and never supplies digits without a crop reading. Subscripts and ordinary numbers are excluded geometrically, and punctuation checks protect mathematical superscripts. Detection evidence is cached by source, OCR version, algorithm and corrected rows; changing heuristic comments or sequence scores does not require OCR again. Thresholds and fallback rules are explained in `scripts/scanned_notes.py` and covered by synthetic and artifact regressions. The feature is opt-in, so earlier profiles keep their existing extraction behavior.

Scanned prose now uses a shared deterministic margin model: robust Theil–Sen slopes compensate for scan skew, and sustained symmetric insets supply block-quotation evidence. It runs on ordinary OCR prose columns after exclusions; native typography, hanging references, verse and reviewed paragraph margins retain their existing paths. Insufficient or unstable geometry falls back conservatively. `corrections-applied.json` records margin models, fallback reasons and source row IDs for inferred quotation runs. No title, page number or phrase is part of the detector.

The margin model distinguishes occasional paragraph-first-line indents from a quotation's stable inset. `recover_scan_font_metrics` optionally aligns hidden PDF text with fresh OCR and transfers glyph sizes only; it does not infer the original font family. Sustained smaller quote measurements yield relative EPUB font sizes. This requires a source with usable hidden-text geometry; absent or inconsistent evidence leaves the default size.

`scripts/jev_notes.py` prepares bounded citation-location questions for references whose crop digits agree but whose chapter sequence is ambiguous. It offers only existing OCR-backed locations plus `uncertain`; order conflicts and unlocated numbers are excluded. Responses are pinned to `jev-1.13.0`, validated and cached through the same client as lexical ranking. Results remain review proposals; AI confidence alone cannot establish scan fidelity or authorize a text edit. Jev accepts text rather than images, so digit recognition still requires local OCR or visual review.

The [visual note-review sheet](../output/feeling-organism/note-review.html) shows source scan crops, enlarged markers, paragraph context, matching Jev results and missing numbers by chapter. Regenerate it with `scripts/note_review.py PDF --profile PROFILE --input OUTPUT_DIRECTORY --output REVIEW.html`; it is self-contained and offline.

Chapter-order conflicts and ties now trigger a cached tight-crop retry using single-character segmentation, two scales, and nearest/smooth resampling. Newly read alternatives are ranked with the chapter sequence; common glyph confusions such as 6/8 carry a smaller relative penalty than unrelated substitutions. These are documented heuristic scores, not calibrated probabilities. A counter never supplies a digit without a new crop reading. This resolved PDF pages 84→6, 91→8, 128→9 and 178→5 locally, raising inline recovery to 136/157 after the complete-marker crop repair also resolved PDF 220→12. All five source-located review candidates are now resolved; the remaining 21 references lack a detected candidate. The earlier Jev duplicate-8 experiment is historical evidence of an OCR failure, not an outstanding ambiguity after retry.

```sh
.venv/bin/python scripts/jev_notes.py --profile config/feeling-organism/book.json \
  --input output/feeling-organism --output output/feeling-organism/jev-note-request-preview.json --dry-run
# Once the exact excerpt payload is approved, omit --dry-run to obtain cached proposals.
```

## Pharmako/Poeia

[Corrected reading edition](../output/pharmako-poeia/reading-edition/pharmako-poeia-corrected.epub) and [source-spelling edition](../output/pharmako-poeia/pharmako-poeia.epub) convert the supplied 256-page Dale Pendell scan. The scanned copyright page identifies the **1995 first edition**, despite the filename's 1994. ISBN-10 **1562790692**, ISBN-13 **9781562790691**. Gary Snyder is credited for the foreword using the EPUB contributor role, rather than as a coauthor. Free Open Library metadata and the matching downloaded cover are cached for offline rebuilding. Copyright is checked reflow text, with no copyright-page image.

```sh
.venv/bin/python scripts/pipeline.py \
  'input/Dale Pendell, Gary Snyder - Pharmako_Poeia_ Plant Powers, Poisons, and Herbcraft-Mercury House (1994).pdf' \
  --profile config/pharmako-poeia/book.json --work work/pharmako-poeia \
  --output output/pharmako-poeia --skip-extraction
# Reading edition additionally corrects five scan-verified printed mistakes.
.venv/bin/python scripts/pipeline.py \
  'input/Dale Pendell, Gary Snyder - Pharmako_Poeia_ Plant Powers, Poisons, and Herbcraft-Mercury House (1994).pdf' \
  --profile config/pharmako-poeia/book.json --work work/pharmako-poeia \
  --output output/pharmako-poeia/reading-edition --skip-extraction --editorial
```

All pages have local Apple Vision, Tesseract and RapidOCR/Paddle caches. Vision supplies the primary text. The book has 54 navigable sections, 110 reviewed artwork crops (including original contents, marginal symbols, woodcuts and separate Chinese ideograms), glossary entry boundaries, and an explicit map of irregular printed pagination. The **source is incomplete**: it ends at glossary page 247 and lacks the references and credits listed in its own contents. Printed numbers also jump over pages 28, 98, 116, 144, 154, 210 and 240; their contents are not inferred. An edition note and metadata record these limitations.

Generic improvements mask raw artwork OCR before horizontal line merging; preserve image-only page anchors; transfer inline styles only where fresh OCR agrees with PDF text and geometry; recognize short ragged verse and alternating indents; and preserve stanza gaps. Small-cap label/value rows and hanging italic field labels retain their entry boundaries. `recover_pdf_typography` is opt-in after verifying the hidden text's font evidence. `recover_ocr_regions` is also opt-in: tall garbled rows are replaced only when two fresh engines independently agree on each separate line, geometry matches, and adjacent retained rows would not be duplicated. Earlier profiles retain their extraction settings. `source_relative_figures` scales artwork from reviewed source proportions, with narrow ornaments floating beside the text.

Artwork extraction prefers the original raster when exactly one full-page scan covers the crop with a verified unrotated transform and no visible text overlaps it. Visible reconstructed captions and labels require page rendering. Profile-reviewed font/size/pattern exclusions suppress only verified spurious overlay glyphs, without modifying source images; removal evidence is recorded. Ambiguous placements and vector pages also fall back to rendering. Candidate image bounds still require scan review: disconnected components can be separate illustrations with intervening prose, and OCR text masks can erase parts of drawings.

The offline [proofreading sheet](../output/pharmako-poeia/reading-edition/proofreading-review.html) and JSON retain uncertain spellings, scientific terms and OCR disagreements. Source-checked OCR overlays and reviewed vocabulary are scoped to the profile; editorial spelling changes remain a separate edition. Automated recovery and sampled visual checks are not complete proofreading. Reproduction uses pinned tools, source/config/code hashes, checksum-verified cached metadata and complete OCR caches; fresh OCR across different tool or OS versions is not guaranteed byte-identical.


## Quotation and superscript follow-up

The quotation on PDF pages 196–197 beginning “As the summer passed” contains two paragraphs with first-line indentation and smaller serif text. The shared margin detector had rejected its deeper first lines, splitting the passage into four ordinary paragraphs. Stable quote margins now allow occasional paragraph indents; aligned hidden-text glyph measurements yield 90% and 92% relative type sizes for the two paragraphs. No original font-family identity is inferred. Both editions preserve the note-22 link at the quotation end. Earlier-book comparison against the same profiles/caches and HEAD shared modules produced identical EPUB bytes for all four prior books after guarding against an overly broad labeled-prose match.

A separately approved Jev request compared two candidate locations for note 8 and returned uncertain. Subsequent source inspection showed that the earlier location was actually 6: the purported duplicate was an OCR error. Chapter sequence initially considered only digits already recognized and could not request additional observations. Sequence conflicts now trigger tight-crop local retries. A documented visual-confusion prior favors similar digits (for example, 6/8) over unrelated substitutions, without supplying an unread digit. At PDF 220, the initial connected-component detector captured only the 1 of 12; the 2 was split into smaller fragments. A bounded search for adjacent raised components followed by tight trimming recovered the complete number, and fresh character placement removed the phantom asterisk from the earlier OCR witness.

At the tight-crop checkpoint, verified locations were PDF 84→6, 91→8, 128→9, 178→5, and 220→12. Inline recovery was 136/157; all five located candidates are resolved and 21 expected references remain unlocated, with chapter-level navigation retained. The [visual marker review sheet](../output/feeling-organism/note-review.html) retains the original OCR readings, retry readings and scan crops so these decisions remain inspectable. Jev is unnecessary for these five cases after local recovery.


## Short-line and omitted-marker follow-up

Source review identified Preface note 3 after “as yet only dimly understood” on PDF 21 and note 6 after “on” on PDF 23. The first lay outside the OCR right edge. The second followed only two body letters; the local height estimate mistook the note digit for body text, and the minimum line-length filter excluded the row. Detection now searches beyond the OCR right edge and borrows a cap-height estimate from nearby longer lines in the same column for short rows, keeping the current row’s baseline. Search clearance is trimmed before whole-line OCR: leaving it wide had changed recognition enough to lose two previously recovered markers.

Chapter 1 note 9 on PDF 40 and note 11 on PDF 42 were geometrically detected but discarded because the initial modes read them as letters/punctuation. Tight single-character retries supply actual numeric alternatives before sequence selection. The sequence resolves the weak 8/9 readings at note 9 using its neighbors, and the source-supported 11 is linked within the existing smaller block quotation. No book/page/phrase override was added.

This pass recovers 150/157 inline references: all six Preface and all sixteen Chapter 1 notes are linked. It adds fourteen verified locations relative to the 136-reference checkpoint. Scan crops were inspected for the added markers, and existing note links were retained. Seven expected references remain unlocated: Chapter 3 note 7, Chapter 4 notes 8 and 11, Chapter 5 note 1, Chapter 8 note 3, Chapter 10 note 28 and Chapter 11 note 3. One additional weak pixel candidate on PDF 20 remains rejected by chapter order; it is not a recovered reference. Updated inclusive search ranges are in [the review sheet](../output/feeling-organism/note-review.html) and [JSON](../output/feeling-organism/missing-reference-ranges.json). Earlier profiles do not enable scan-note recovery.

### Final seven source-located references: 157/157

The user located Chapter 3 note 7 (PDF 85), Chapter 4 notes 8 and 11 (PDF 106 and 107), Chapter 5 note 1 (PDF 111), Chapter 8 note 3 (PDF 161), Chapter 10 note 28 (PDF 199), and Chapter 11 note 3 (PDF 206). All seven now recover through shared logic with local OCR; no page/phrase/coordinate marker override or Jev request was needed.

The references after 1920 and 1953 needed four-digit year anchors: the prior placement guard treated those year digits as unrelated numeric material inside the edit gap. The unpunctuated Hopkins reference needs a matched capitalized name and following word, with no original prose letters in the replacement gap. The tiny final lines `it.` and `lished.` were recognized more accurately after masking the observed raised glyph, recognizing the prose alone and reinserting only the crop-read number. Baseline-relative vertical masking reduces neighboring-line noise.

PDF 111's blurry digit was fragmented by the original dark-ink threshold. Tall overlapping crops now receive a bounded contrast pass; a tight single-character mode reads 1 and chapter order supports it. PDF 161 had a confident, garbled two-line row duplicating the preceding quotation line and absorbing the line ending at note 3. The existing optional region recovery now retains matching neighbors once and restores the missing line using independent OCR evidence. Where one fresh engine adds a single digit-confusion token, exact embedded/fresh lexical agreement corroborates the other reading; prose substitutions are rejected. Enabling this shared region-recovery option made the old `itseld` manual correction obsolete, so its stale precondition was removed from the profile.

Both EPUB editions now contain all 157 inline references and backlinks. The missing-reference table is empty. Two extra pixel candidates remain rejected by chapter order and visible in the review sheet; they are not missing notes. Source-row coverage and canonical/rendered text checks pass. EPUBCheck reports zero errors and warnings for both editions, and all 102 tests and Black checks pass. Same-profile/cache comparisons using the previous OCR module confirm byte-identical EPUBs and unchanged canonical models, corrected rows and coverage for The Natural Mind, Shamanic Trance, For the Letter Kills, and Pharmako/Poeia; comparison evidence is in `work/feeling-organism/seven-backward-compatibility.json`.

A repeat build of the source edition is byte-identical; its hashes are recorded in `work/feeling-organism/seven-repeat-verification.json`.

## Note-crop binarization experiment

After committing input-integrity and review-binding work, a local benchmark compared six threshold paths on eleven difficult Organism markers and eleven adjacent prose controls. The manifest in `tests/binarization-cases.json` fixes the source checksum, row geometry, complete marker boxes and neighboring cap-height hints. Original inputs and a deterministic faded/uneven derivative are separate conditions. The 22 original crops were visually inspected; some crops retain neighboring-line fragments, deliberately exposing the detector to realistic noise. All OCR ran locally with the existing Tesseract model; no new dependency or external request was required.

Run `.venv/bin/python scripts/binarization_experiment.py`. The [visual comparison](../work/binarization-experiment/review.html) and [raw report](../work/binarization-experiment/report.json) retain 264 method/sample observations, crop hashes, model/tool/code provenance and reusable recognition caches. The current path preserves grayscale recognition except for its existing tall/faint binary fallback. Otsu uses a histogram threshold; Sauvola uses local mean/variance with k=0.2, R=127.5 and fixed 61/91-pixel windows. These settings follow the documented [threshold formulas](https://scikit-image.org/docs/stable/api/skimage.filters.html#skimage.filters.threshold_sauvola); they are experimental settings, not a fitted production policy.

Each cell below is original / synthetically faded. “Expected read” means the expected number occurs among raw OCR alternatives at a detected region covering at least 60% of the annotated full marker. It is an optimistic observation measure, not a selected or accepted reference. Controls contain no annotated marker on their selected prose row. Extra numeric regions count raw candidates outside the target, including noise in positive crops.

| Method | Full marker detected / 11 | Expected read / 11 | Controls with numeric candidates / 11 | Extra numeric regions |
|---|---:|---:|---:|---:|
| Current path | 10 / 0 | 7 / 0 | 0 / 0 | 2 / 0 |
| Fixed 150 | 9 / 0 | 9 / 0 | 0 / 0 | 2 / 0 |
| Fixed 180 | 11 / 1 | 9 / 1 | 0 / 2 | 1 / 3 |
| Otsu | 11 / 10 | 10 / 9 | 0 / 0 | 1 / 1 |
| Sauvola 61 | 3 / 3 | 3 / 2 | 0 / 0 | 0 / 0 |
| Sauvola 91 | 3 / 3 | 2 / 3 | 0 / 0 | 0 / 0 |

Otsu supplied the correct alternatives for the original 9 on PDF 128 and 5 on PDF 178, and found the complete 12 on PDF 220 where the baseline region covers only its first digit. It still read the original PDF 84 marker as 8 rather than 6. Under synthetic fading it failed to read the blurry PDF 111 digit 1 and fragmented PDF 220's 12. The original PDF 111 crop still produced an extra numeric noise region. Sauvola's tested settings lost many real markers, so it is not a useful default on this sample.

The existing production pipeline already recovers all 157 references through tight retries, placement and chapter sequence; this benchmark deliberately omits those steps. It does not demonstrate a further production coverage gain. One selected book and eleven negative controls are insufficient to estimate false acceptance reliably, and synthetic fading is not evidence from another scan. Keep production defaults unchanged. The next useful experiment is a larger independently annotated set across books/type scales, followed by a bounded Otsu retry comparison through full placement/sequence checks and the seven-case regression gate.

Verification: 128 tests pass, Black is clean and all eleven complete marker boxes lie within their source crops. Cached replay reproduces the report and visual sheet byte-for-byte. This research-only change does not rebuild or alter published EPUBs; the seven-case production regression passed before commit `3ec72d2`.

## Native textbook: The Skilled Helper

The supplied `input/Gerard Egan Robert J. Reese - The Skilled Helper (2019, Cengage Learning).pdf` is a 452-page publisher-native PDF, SHA-256 `b0895556f39a537c409e5006554b7dc134389e0d33fcb123470ac4ccf321ac1d`. Its title/copyright pages identify Gerard Egan and Robert J. Reese, the eleventh edition, copyright 2019, print year 2018 and ISBN 9781305865716. The [publisher record](https://www.cengage.com/c/new-edition/9781305865716/) gives publication 2018-03-20; that publication date is kept distinct from the copyright year. [Open Library's exact ISBN edition](https://openlibrary.org/books/OL30358841M) supplies both authors, identifiers, classification, subjects and a downloaded cover. Its 480-page print count conflicts with the supplied PDF's 452 pages ending at printed 432; the discrepancy is recorded without inventing missing material. The source itself warns of potentially suppressed third-party/media content.

```sh
.venv/bin/python scripts/metadata.py --profile config/skilled-helper/book.json \
  --cache work/skilled-helper/metadata/cache --offline
.venv/bin/python scripts/pipeline.py \
  'input/Gerard Egan Robert J. Reese - The Skilled Helper (2019, Cengage Learning).pdf' \
  --profile config/skilled-helper/book.json --work work/skilled-helper \
  --output output/skilled-helper
.venv/bin/python scripts/regression.py --book skilled-helper
```

The [EPUB](../output/skilled-helper/skilled-helper.epub) contains twenty navigable divisions: the introductory model, preface/dedication, guide, three part divisions, eleven chapters, references and two indexes. Native PDF bookmarks seed the main chapter boundaries after removing control padding. Original contents are replaced by EPUB navigation. The MindTap advertisement and repeated publisher footers are omitted; title and copyright information are reflowable. Roman front matter and the body offset (PDF 21 = printed 1) retain original page links. The unnumbered introductory model has an explicit nonnumeric page label. No copyright-page image is included.

Ten diagrams are preserved as visually reviewed, 300-dpi source crops: the front model and Figures 1.1, 2.1–2.4, 8.1, 9.1, 10.1 and 11.1. Caption text remains reflowable. Crops were enlarged after visual review found that a six-point caption clearance clipped lower arrows/bullets; the final boundary leaves one point before the caption's text box. Masking native labels before row merging prevents artwork labels from absorbing nearby prose. A checked 956×1368 source cover remains primary; the downloaded, visually matching 345×500 ISBN cover is included as a source reference. Both authors appear as EPUB creators and on the title page. The source LCCN 2017962286 supplements the otherwise missing API field through an explicit checked override.

Generic opt-in layout improvements preserve multi-span numbered/bulleted items and their hanging wraps, tightly wrapped native headings, smaller example text and child-index indentation. Mixed-size bold headings with positioned learning-objective labels remain headings even when synthetic separator spaces lack font tags. Lists preserve their actual source markers; current rendering uses hanging paragraphs, with semantic nested list markup and uncertain cross-page continuations left for further work. Examples become smaller indented block quotations; their original shaded panels and font-family identity are not reproduced.

Whole-book proofreading exposed a substantial configuration error: a single 0.53 gutter split interleaved bibliography columns on even pages, producing false repeated-word candidates such as “New New” and “and and.” Source geometry confirmed mirrored gutters, about 0.45 on even PDF pages and 0.53 on odd pages. Correcting those source facts restores independent references and reduces structural diagnostics from seventeen to fifteen. This demonstrates why post-assembly checks are useful even for native text. The remaining diagnostics include grammatical repeats, run-in labels followed by the same word, and the source brand Time2Track; none is silently rewritten. The [offline proofreading sheet](../output/skilled-helper/proofreading-review.html) retains 534 lexical review groups and fifteen diagnostics. References and indexes are excluded from chapter-IDF statistics. No OCR, Jev or LLM upload was needed.

Validation covers retained-row mapping, canonical/rendered text, internal links, repeatable EPUB packaging, focused layout tests and EPUBCheck. Sampled rendering reviewed learning objectives, wrapped headings, examples, figures, bibliography columns and the actual linked subject index. Automated checks and these samples do not establish complete proofreading or compatibility with every EPUB reader. The book now has its own regression case and independently replayed baseline; earlier-book baselines remain unchanged.

Verification: 134 tests pass, Black and `git diff --check` are clean, and EPUBCheck reports zero errors/warnings. All seven earlier edition cases pass unchanged (`work/regression/run-j7ks3upm/report.json`); the new case passes an independent replay (`work/regression/run-6sg01bwu/report.json`). A final focused Letter Kills replay also checks the existing DOI metadata branch. The final published package matches the isolated replay byte-for-byte.
## Scanned narrative: Wizard of the Upper Amazon

The supplied 215-page scan has a 203-page main text/appendix and a poor reconstructed text layer. PDF 1 is the jacket cover; PDF 11 and 12 are distinct South America overview and travel-route maps. A publisher title page is absent. The contents on PDF 10 agree with all 16 narrative chapter openings and the appendix. Printed body page numbers are PDF page minus ten. Foreword, acknowledgments, both maps and author biographies are retained as reading sections; the barely readable front and back jacket flaps were later omitted at user request; source contents are replaced by EPUB navigation. Copyright is checked reflowable text, with no copyright scan.

The scan states copyright 1971 and First Edition. The matching public Open Library edition is [OL2561783M](https://openlibrary.org/books/OL2561783M), published by Atheneum with 203 numbered pages and no ISBN. Kingsport Press is the printer, not the publisher. Later editions have different publishers, introductory material and ISBNs. The checked source copyright page prints LCCN 73–139314, which differs from the catalog record; the profile records this discrepancy and retains the source value. The edition-specific cover endpoint returned no cover. The user later explicitly selected `input/wizard of the upper amazon.jpg` as replacement reading-cover artwork (its checksum is frozen in the profile). This replacement does not establish the source edition or change its metadata; no later edition's ISBN is assigned.

A source defect emerged during OCR disagreement review: visible reconstructed PDF words overlap the original scan, producing doubled letters. Comparing PDF 78's rendered final word with its embedded image confirms the original reads “above” clearly. Every source page places one upright dominant scan (1291 × 2243 pixels). The generic `scan_raster_only` mode therefore supplies original pixels to Tesseract, Apple Vision and English PP-OCRv4/RapidOCR, and crops illustrations from those pixels. The first rendered-page OCR experiment remains in its separate cache. Scan pixel mode and extraction helper hashes bind the new caches, so they cannot silently be mixed with the original experiment.

The new `source_diagnostics.py` samples opening leaves and evenly spaced pages. It requires substantial painted text over a page scan and compares scan-only versus composed renders at identical placement, resolution and luminance thresholds. Added dark ink is evidence of visible reconstruction; hidden OCR or text occluded by the scan alone is insufficient. The Wizard sample flags eight pages. A native-text Kabbalah comparison returns no sampled overlay evidence, and synthetic tests cover damaged overlays, hidden/occluded text, genuine captions and ambiguous multiple scans. This diagnostic warns and records evidence; it does not silently discard possible real annotations.

The appendix's indole ring and amine side-chain diagrams, chemical subscripts and surrounding explanatory paragraph are preserved as one checked image; prose before and after it remains reflowable. Both maps use checked source-image regions. The former jacket-flap facsimiles are omitted at user request. Three asterisk footnotes occur on PDF 33, 42 and 47. Chapter-body cutoffs exclude decorative branch frames, and the foreword library stamp is excluded. Book facts and source-checked overlays live in `config/wizard-upper-amazon`; all new detection and metadata behavior is shared code.

All three OCR engines were rerun locally on original scan pixels. The hybrid selection uses Vision for front matter, chapter openings and the author biography, with Tesseract elsewhere; RapidOCR and the old PDF layer remain comparison witnesses. Source inspection resolved 86 initially disputed lines: 75 required corrections, eight retained the primary reading and three were running headers. Two additional source-checked corrections repair a line-end v/y confusion and the author's birth year, printed as 1913 but read as 1918 by both Vision and Tesseract. Agreement between two engines is therefore not proof of accuracy. The original source crop and the other two witnesses corroborate 1913.

Whole-book proofreading preserves printed errors such as “approachd,” “omnious” and “subleties” in this faithful edition, while retaining optional editorial proposals. Indigenous names and historical botanical spellings are source-checked protected terms. The offline review retains 135 lexical groups and eight structural diagnostics; a resolved OCR disagreement queue does not establish complete proofreading. Original inline italic typography is not fully recovered from this reconstructed, uniformly tagged PDF layer.

```sh
.venv/bin/python scripts/pipeline.py \
  'input/Manuel Córdova-Ríos_ F. Bruce Lamb - Wizard of the Upper Amazon-Kingsport Press (1971).pdf' \
  --profile config/wizard-upper-amazon/book.json \
  --work work/wizard-upper-amazon --output output/wizard-upper-amazon \
  --skip-extraction
.venv/bin/python scripts/regression.py --book wizard-upper-amazon
```

Omit `--skip-extraction` to regenerate OCR caches. Replaying a complete cache requires the same source, raster mode and extraction fingerprints. The initial conversion had 23 sections, four source illustrations/facsimiles, three linked footnotes and 211 original page anchors; reviewed changes below describe the current [EPUB](../output/wizard-upper-amazon/wizard-upper-amazon.epub). The [proofreading sheet](../output/wizard-upper-amazon/proofreading-review.html) retains remaining candidates, and [source analysis](../output/wizard-upper-amazon/source-analysis.json) records the sampled overlay evidence. Validation includes 140 passing tests, all nine book regression cases, checked source-row coverage, sampled visual rendering and EPUBCheck with zero errors or warnings. Earlier accepted book baselines remain unchanged.

The initial conversion EPUB SHA-256 was `476b74d1d055b99399f47304f5caf061274b78a8b8f22cf9538207fdd255b937`; subsequent reviewed rebuilds are tracked by the regression baseline. The profile contains 77 source-checked correction rows and 83 bound review decisions. The author biography retains the original printed birth year 1913.

### Maps, exact footnote markers and inset chants

Source inspection corrected an erroneous profile exclusion: PDF 11 is the South America overview map, not a duplicate cover. The maps now share one reading section, preserving PDF 11 before PDF 12 without renumbering later chapters. The prior completeness statement has been corrected. The existing generic `link_symbol_footnotes` pass is enabled for this profile so the unique page-scoped asterisks on PDF 33, 42 and 47 can provide exact references and return links instead of fallback page links.

A shared, opt-in `infer_inset_verse` rule recognizes colon-introduced runs of at least four aligned, deeply inset, ragged lines without relying on capitalization or font flags. It preserves both chants on PDF 42 and the chants on PDF 98 and 100 as indented blocks with source line breaks. Sustained prose, drifting margins, hyphenated continuations and other columns are rejected.

The source PDF has uniformly reconstructed font tags. The optional `recover_scan_inline_italics` pass therefore measures stem lean in original pixels, relative to nearby prose controls, and aligns complete word groups using local character OCR. Each word must independently support the style; groups need at least six components, 75% support and a relative median lean of .14. Detached dots and punctuation do not vote. These thresholds are conservative heuristics, not calibrated probabilities. Rows with weak OCR alignment, missing controls or unstable skew abstain. Page scans are decoded once and each glyph's trial shears are computed once per line, using batched projections equivalent to the former whole-line calculation. A contiguous pixel-stem prefilter avoids unnecessary character OCR. Positive and negative page results bind source pixels, classified rows, helpers and OCR/runtime versions; cover and metadata changes retain these observations. No book phrases, page coordinates or term lists drive the detector. Inline typography remains incomplete; this pass is not a guarantee that every italic word is recognized.

The revised book retains both maps, links all three asterisks with exact backlinks, and preserves four chants (PDF 42: 12 and 4 lines; PDF 98: 11 lines; PDF 100: 6 lines). All 45 recovered italic spans were compared with source crops; the three user-reported phrases are fully italicized. Remaining conservative omissions include short words and parts of longer italic titles (for example, “Mind” and “Review” on PDF 213). Source prose rows and all three footnote rows are unchanged; 28 garbled OCR rows inside the restored map are removed by the existing figure mask. The supplied JPEG is embedded byte-for-byte, and Calibre bookmark metadata is preserved.

Validation passes 239 tests and EPUBCheck without errors or warnings. Cold and cached builds match in every deterministic artifact and EPUB member. The cold build took 346 seconds, including 329 seconds of inline analysis; the cached build took 17 seconds, with 0.09 seconds of inline analysis. Ten earlier books match their accepted baselines; the interrupted exploratory Wizard build is not counted as passing. Only the reviewed Wizard baseline is updated. Evidence and raw regression paths are recorded in `work/wizard-upper-amazon/final-verification.json`.

Further simplification opportunities are a single registry for profile keys and types (currently split between `common.py` and `profile_validation.py`), and separating cached glyph/character observations from style acceptance. The latter would permit threshold experiments without repeating extraction; current page caches deliberately invalidate on helper changes. These are follow-up proposals, not implemented behavior.

### Quote-mark repair, short continuations and omitted flaps

PDF 117's opening quote before “Well-made blocks” was read as an asterisk by Tesseract, while Apple Vision and PP-OCR both read a quotation mark and otherwise agree on the complete line. The lexical reconciliation pass previously ignored that punctuation disagreement. Shared OCR reconciliation now repairs a leading asterisk only with this exact two-fresh-engine evidence, allowing straight/curly double quotes but no other textual differences. Embedded OCR cannot authorize the repair; genuine apparatus stars remain unchanged. This is an audited inference, not a phrase override.

PDF 161's “you.” was incorrectly split into a separate paragraph because the fixed 0.012-page gap threshold measured the whitespace between glyph boxes. A short lowercase line has a shallower box, which inflates that apparent gap. The shared assembler checks local center-to-center pitch before interpreting short, shallow, unindented continuations as paragraph starts. It requires at least eight prose pitches, a preceding full line and spacing within 30% of the local median; explicit starts, indentation, real gaps, verse, hanging entries and native text retain their boundaries. The same rule repairs source-checked continuations on PDF 104 (“after-” / “noon.”) and PDF 131 (“any more.”). No page or wording override is used by the pipeline.

The user requested omission of the barely readable front and back jacket flaps (PDF 2 and 215). Their reading sections and figure specifications were removed from the profile, with explicit omission reasons. The EPUB now has 21 sections and retains both maps, the chemical diagram, the supplied cover, three linked footnotes and four chant blocks. The remaining image bytes are unchanged. Size falls from 2,779,806 to 1,803,887 bytes before adding preserved reader bookmarks. The proofreading sheet is regenerated with the new chapter order.

Validation passes 241 tests, Black and source-row coverage; EPUBCheck reports zero errors and warnings. All ten earlier edition cases remain byte-for-byte consistent with their accepted snapshots (`work/regression/run-m4etux_b/report.json`). Only the reviewed Wizard baseline changes. Source-row comparison permits exactly the quote correction and omitted flap pages; all other OCR rows remain identical. The reviewed semantic changes affect Apprenticeship, Indian Caucho, Legends and Assassin. Independent Wizard replay passes against the reviewed baseline (`work/regression/run-7bflilml/report.json`). Published EPUB members match the rebuild, and existing Calibre bookmark bytes are preserved. The actual XHTML paragraphs contain the corrected quotation mark and uninterrupted “another chief for you.” Evidence is recorded in `work/wizard-upper-amazon/reported-fixes-verification.json`.

## Otherworlds: annotated scan, chemical notation and skewed index

The supplied `input/David Luke - Otherworlds-Aeon Academic (2019).pdf` contains 299 scanned pages with an ABBYY FineReader 12 hidden OCR layer and no bookmarks. The source copyright page (PDF 2) identifies **David Luke, Otherworlds: Psychedelics and Exceptional Human Experience, Muswell Hill Press, London, 2017**, ISBN **9781908995148**. Its edition is corroborated by the [author's university repository](https://gala.gre.ac.uk/id/eprint/19335/). The filename's year/publisher are not reliable bibliographic evidence. Exact-ISBN Open Library enrichment supplies identifiers, subjects, classification and description, but its publisher field conflicts with the scan. A recorded source override preserves Muswell Hill Press.

The PDF omits the exterior cover. A visually checked exact-ISBN Open Library cover is frozen in `config/otherworlds/isbn-cover.jpg` (331 × 500 pixels, SHA-256 `6179c74dd7633d11dbe593f8811995b119eb65e0b26cd8c6b80a599facdbbdf5`). Copyright is transcribed into XHTML; no copyright scan is included. Original illustrations and attributions are retained, rather than generated substitutes.

All pages were recognized locally at 300 dpi using Tesseract 5.5.3, Apple Vision accurate English revision 3 and RapidOCR/Paddle PP-OCRv4 English on CPU. ABBYY is another witness. Vision produced fewer review disagreements than Tesseract in the sampled body, so it is the primary engine. The scan has no sampled reconstructed-text overlay evidence. Its multiple full-page image layers require composed-page rendering; extracting one raw layer would lose content.

The source is heavily annotated: underlining, brackets, arrows, marginal words and question marks can be mistaken for author prose. Numbered offline crop sheets in `work/otherworlds/` were compared with the print to remove verified annotation text and repair letters, citations, names and Greek beta glyphs. Printed mistakes remain in the faithful edition, including repeated words where the scan actually prints them. Hunspell/SymSpell suggestions and whole-book diagnostics remain proposals. Domain vocabulary and source-checked names have a protected-word list; recurring unknown words are not automatically trusted. No book excerpts were uploaded to Jev or an LLM.

Three optional generic behaviors were added: strict three-witness recovery of a single digit confused with a letter in a common word, restoration of fully bold aligned heading rows, and robust left-margin fitting for hanging columns. The last reuses the existing Theil–Sen estimator with its support/spread/error guards; it fixes index entries that had merged because skew moved their starts past a fixed indentation threshold. Sparse or unstable columns retain the existing fallback. `exact_row` correction preconditions also distinguish a standalone annotation mark from identical prose punctuation. Earlier profiles retain their prior behavior by default.

Page-top review caught dropped opening lines: body text begins around normalized y=.095 on ordinary pages, so the reviewed cutoff is .085. Lowering it restores the dedication opening and cross-page continuations without retaining recurrent headers. Index OCR was rerun with a .49 gutter, except PDF 295 (.525) where the left column is wider. Stale corrections and acknowledgements on changed index readings were removed before review. Reusing caches with old column geometry correctly fails preflight.

The reading order has 23 sections: front matter, two part openings, 14 chapters, afterword, references and index. Chapter 3 retains its Devin Terhune credit. There are 18 numbered illustrations, five landscape sheets of Table 1 and both sheets of Table 2. Table captions and attribution are included in the crops. Four source paragraphs (PDF 112, 167, 169 and 188) preserve receptor subtype subscripts as facsimiles because every text layer corrupts them. These paragraphs and table/figure captions do not reflow or offer searchable text; their alt text describes the role, not a complete accessible transcription. Other prose remains reflowable. This is an explicit fidelity fallback, not a solved chemical-notation recognizer.

```sh
.venv/bin/python scripts/pipeline.py \
  'input/David Luke - Otherworlds-Aeon Academic (2019).pdf' \
  --profile config/otherworlds/book.json --work work/otherworlds \
  --output output/otherworlds --skip-extraction
.venv/bin/python scripts/regression.py --book otherworlds
```

Omit `--skip-extraction` to regenerate the local OCR evidence. The source SHA-256 is `d78f1b9fb631bd72f65a6e49cd3eaea554e4b9e7a79c5077484e6c50f670e697`. The [EPUB](../output/otherworlds/otherworlds.epub), [proofreading sheet](../output/otherworlds/proofreading-review.html), [OCR disagreements](../output/otherworlds/review-queue.json) and [source coverage](../output/otherworlds/text-coverage.json) are separate deliverables. Hundreds of disagreements remain, chiefly bibliographic punctuation/names, and the spelling sheet includes legitimate technical terms. Sampled source checks and row coverage do not establish complete proofreading or guarantee that every handwritten mark was excluded.

Initial conversion validation: 147 unit tests passed; Black and whitespace checks passed. All nine earlier regression cases passed against unchanged baselines. The initial Otherworlds baseline was accepted after source/layout review, and its independent cached rebuild matched the published EPUB, canonical model, corrected rows and covered regression artifacts exactly. EPUBCheck reported zero errors or warnings. The initial EPUB SHA-256 was `40fa3f14a377095ea4bf5b28df2c06954525a024f85f616bbc04c66ea333ea4c`; the proofreading repair below supersedes that artifact.

The profile has 255 checked correction rows and 490 evidence-bound review decisions, with no unmatched decisions. Coverage accounts for 10,527 retained body rows and 469 excluded rows, and the EPUB retains 279 original-page anchors. Of 618 remaining OCR disagreements, 615 are in the references; three are an elongated quoted utterance and two index lines. The initial lexical sheet had 949 groups plus nine whole-book diagnostics. An offline 20-page rendering sample in `work/otherworlds/layout-qa.pdf` checks the initial conversion's cover, openings, verse, an inset quotation, chemical notation, references and index. It is sampled layout evidence, not a full text proofread. Additional handwritten punctuation can survive agreement among engines; the review artifacts should remain part of the delivery.

Automatic verse inference pulled the short final line of prose before Bourdillon's poem into the verse block. For this source, `infer_verse: false` uses the three checked verse ranges instead, preserving the prose tail and both poem stanzas. Other profiles retain their existing automatic inference default.

User proofreading identified five straightforward spelling errors and an unjoined `asso- / ciators`. The lexical suggestions already contained most correct targets, but the suggestion-only stage could not edit text. Full-page engines can share errors; moreover, punctuation-sensitive reconciliation treats `atter . / after ,` as a two-token replacement and misses the otherwise corroborated spelling. These are distinct recognition, reconciliation and reconstruction failures.

The shared opt-in whole-book repair now combines a noisy-character channel, bundled SymSpell unigram/bigram counts, smoothed general/book Markov transitions, and targeted 600-dpi word OCR. Book statistics are frozen before edits; missing corpus bigrams reserve unigram backoff rather than penalizing common words for absent observations. Crop positions require matching neighboring words, so a correct word elsewhere in the row cannot supply false corroboration. Local Tesseract reads `after`, `beliefs`, `amnesia`, `earlier`, and `each` in both segmentation modes. The weak amnesia/amnesty ranking additionally has matching embedded OCR. Only those five spelling edits are accepted; a `centrai → central` proposal abstains because crop geometry is ambiguous. Names, domain vocabulary and the remaining proposals stay review-only. No new dependency or external API was needed.

The generic wrap repair joins corpus-attested rare forms and overrides a paragraph flag only for a normal-margin, same-kind, same-column continuation with verified word evidence. `associators` was below the old Zipf 2 threshold and absent from Hunspell; its frequency advantage over `asso-ciators` now supports the join. The false paragraph flag arose from recovered italic typography. Its italics remain intact after reconstruction. Additional repairs remove discretionary breaks in rare technical words such as `Neoplatonism`, `visuospatial`, `automaticity` and chemical names, without changing their letters. Known hyphenated compounds still take precedence. Existing profiles keep their prior defaults.

The regenerated [review sheet](../output/otherworlds/proofreading-review.html) has 933 lexical groups and nine diagnostics; its opening disclosure shows the five accepted spelling repairs and their exact source word crops. [Lexical repair evidence](../output/otherworlds/lexical-repair.json) records all accepted/rejected eligible decisions, ranks, crop readings and resource hashes. The remaining sheet contains legitimate technical terms as well as unresolved OCR; the reduced count does not establish complete proofreading.

Repair validation: 158 unit tests pass, Black reports 44 files unchanged, and whitespace checks pass. All nine earlier books match unchanged baselines. The independently rebuilt Otherworlds EPUB, canonical model, corrected rows, source coverage and lexical audit match the published output exactly; the reviewed Otherworlds baseline now includes the lexical audit. The updated EPUB SHA-256 is `7e9afd017e3c19511a952cc6299e628c321c666e64b2e2ec45ccad244e960788`. Source-row coverage still accounts for every retained row, with 29 figures and 279 page anchors. EPUBCheck reports zero errors or warnings. The offline review HTML contains all 942 grouped records and five checksum-checked repair crops.


### Preface opening recovery

The first two body lines on PDF 17 were present in fresh OCR but excluded by the profile's chapter-opening cutoff of 0.255. The actual prose begins at approximately 0.228, below the short centered Preface heading. This omission passed the previous coverage gate because those rows had been explicitly classified as title material; coverage alone cannot prove exclusion rules are correct.

The shared boundary resolver now detects a continuous body-prose run crossing a configured title cutoff and walks upward through matching adjacent lines. At least four retained prose lines establish column-local width, height, margin and line pitch. Short headings, uppercase banners, font-size changes, separating gaps and column changes stop recovery. Sparse openings and native PDFs retain their profile boundary. The effective cutoff stays in build state and feeds assembly, lexical context and coverage consistently; no Otherworlds-specific phrase, coordinate or profile override was added. `chapter-body-boundary` audit entries record recovered source rows and measurements.

The rule recovers exactly the two missing lines, joining `prohibi- / tion` as `prohibition`. Only the first Preface paragraph and `OEBPS/chapter-04.xhtml` change; removing the restored prefix reproduces all previous package bytes. Corrected OCR rows and apparatus are byte-identical. Retained coverage rises from 10,527 to 10,529 rows, while exclusions fall from 469 to 467. All 234 tests pass, including boundary continuity and heading/font/gap/column/sparse/native negative cases. A direct boundary analysis of all eleven cached editions finds this single adjustment and no others. All ten earlier cases replay against unchanged baselines. The reviewed Otherworlds baseline alone was updated, and every generated member of its independent output matches the delivered package (`work/regression/run-tq1gb7na/report.json`, which records the expected pre-acceptance content change; final review in `work/otherworlds/chapter-opening-verification.json`). Calibre added `META-INF/calibre_bookmarks.txt` during reader review; that external reading-position metadata is preserved and excluded from the content-equivalence assertion. EPUBCheck reports zero errors or warnings.

## Daimonic Reality (Patrick Harpur)

The supplied `Patrick Harpur - Daimonic Reality_ A Field Guide to the Otherworld (2003).pdf` has 319 image-only pages and no usable text layer. Its SHA-256 is `4670a8cc961b42034a19060b95fd3de216badf63c39762d4c00e96024f080525`. The [publisher catalogue](https://idyllarbor.com/product/daimonic-reality-a-field-guide-to-the-otherworld/) and [exact-ISBN Open Library edition](https://openlibrary.org/books/OL3577953M/Daimonic_reality) identify Pine Winds Press, 2003, ISBN 9780937663097 / 0937663093. Free ISBN enrichment is frozen in `config/daimonic-reality/enrichment.json`; the downloaded matching cover is frozen as `cover.jpg`, SHA-256 `dc1351042c959a85550ab03c9368b343dd1b0b91b0f10fec4e437edf61adca75`. The higher-resolution source cover stays primary, with the downloaded cover incorporated as a reference.

The scan is incomplete. PDF 318 explicitly says blank leaves were not scanned and the index was removed; the copyright leaf is also absent between title and dedication. The EPUB includes an explicit source-edition note rather than invented copyright text or reconstructed index entries. Eighteen missing even-numbered positions are consistent with the declared blank-leaf removal. Consequently a constant PDF-to-print offset is incorrect: the profile records explicit folios and inferred unprinted part/chapter openings, with descriptive labels for unnumbered front matter. Source contents pages establish the reading order but are replaced by EPUB navigation.

All pages were recognized locally at 300 dpi with Tesseract 5.5.3, Apple Vision accurate English revision 3 and RapidOCR/Paddle PP-OCRv4 English on CPU. An identical-mask comparison in `work/daimonic-reality/engine-choice.json` reports 662 retained-region disagreements with Tesseract primary versus 366 with Vision primary (738 versus 410 before region exclusions). These are disagreement counts, not accuracy measurements; checked body lines and severe Tesseract vertical-number errors in the references support choosing Vision. No book excerpts were sent to an external AI API.

The profile has 32 sections: front matter, introduction, three part openings, twenty chapters, epilogue, references, bibliography and the retained publisher page. Eleven numbered figures are source crops with their original captions and attributions, including the diagram relating proposed causes of crop circles. Captions inside those images are facsimiles and do not independently reflow or provide searchable caption text.

This image-only source exposed a lexical-repair geometry gap. Cached local Tesseract character boxes now locate word crops when embedded word boxes are absent, with neighboring-word anchors still required. This is geometry evidence, not an independent text vote. The whole-book repair corrects source-corroborated `sces → sees`, `bypotheses → hypotheses` and `carth → earth`; other candidates remain proposals. Source-checked domain vocabulary stays protected.

Generic opt-in image-only layout recovery uses normalized margins, median line pitch, neighboring prose and preceding sentence/spacing evidence to recover section headings. Center pitch tolerates overlapping Vision boxes. Right-aligned parenthesized credits following quoted text become attributions even on sparse leaves. Source-measured first-line indentation is rendered after headings and added navigation links. Original raster font families and all inline italics have not been recovered; this is a readable reflow edition, not exact typography reproduction.

The reference apparatus contains 477 entries in 22 sections. Missing/damaged hanging entry starts are reread locally, with text agreement, fresh digits and section order required; the strict complete reference-sequence check is retained. Dropped prefixes restore observed marker coordinates, instead of broadening the apparatus limit to accept citation continuation numbers. The crop detector optionally admits narrow serif ones and larger superscripts, reconnects small vertical breaks in light scans, and excludes nonbody regions before chapter sequencing. A detached OCR numeral is excluded only where it overlaps a marker already represented by a recovered reference.

A recognition-only Paddle experiment on a narrow printed `11` succeeds at two padding sizes with scores above 0.80 where Tesseract reads letters or bars. The opt-in recovery uses that local model alongside Tesseract crop readings, filters Paddle scores below 0.75 and records model/runtime fingerprints. Padding modes are correlated observations, and the model score is not a correctness probability. Placement and chapter order still gate acceptance; the apparatus retains chapter-level links for unlocated markers.

```sh
.venv/bin/python scripts/pipeline.py \
  'input/Patrick Harpur - Daimonic Reality_ A Field Guide to the Otherworld (2003).pdf' \
  --profile config/daimonic-reality/book.json --work work/daimonic-reality \
  --output output/daimonic-reality --skip-extraction
.venv/bin/python scripts/note_review.py \
  'input/Patrick Harpur - Daimonic Reality_ A Field Guide to the Otherworld (2003).pdf' \
  --profile config/daimonic-reality/book.json --input output/daimonic-reality \
  --output output/daimonic-reality/note-review.html
```

Omit `--skip-extraction` to regenerate the full local OCR caches. The [EPUB](../output/daimonic-reality/daimonic-reality.epub), [proofreading sheet](../output/daimonic-reality/proofreading-review.html) and [note-marker sheet](../output/daimonic-reality/note-review.html) separate the reading edition from uncertain evidence. The note sheet includes source crops and chapter-relative missing-reference search ranges. The [source coverage](../output/daimonic-reality/text-coverage.json), [apparatus checks](../output/daimonic-reality/apparatus-analysis.json) and [lexical audit](../output/daimonic-reality/lexical-repair.json) are independent validation artifacts. A rendered layout sample checks covers, openings, headings, figures and references; it is not a whole-book text proofread.

The delivered EPUB has 32 sections, 11 numbered figures and 311 original-page navigation anchors, including the figure-only PDF 158. All 477 reference entries pass strict section sequence checks: 404 have linked superscripts and 73 use chapter-level fallback links. The note review retains 246 uncertain candidate readings across 334 review locations; these candidates are distinct from the 73 unlocated reference numbers. The proofreading sheet contains 539 lexical groups and three diagnostics. Ten source-checked correction rows and ten evidence-bound review acknowledgements leave 569 unresolved OCR disagreement locations; the scan sheet also displays the acknowledged locations for traceability. Remaining candidates include legitimate vocabulary and unresolved OCR, so these counts do not establish complete proofreading.

Validation accounts for 10,043 retained body rows and 509 excluded rows, passes 168 unit tests, Black and whitespace checks, and reports zero EPUBCheck errors or warnings. All ten earlier book regression cases match their unchanged baselines. An independent rebuild of this book also matches its reviewed baseline. The 28-page rendered layout sample was refreshed from the delivered EPUB; epigraph credits, chapter indentation and superscripts, a diagram and the reference layout were rechecked. The final EPUB SHA-256 is `3d39d3c52614945fde120c89714b0495e18c081d7bd4d85b21a3ff1e0c810e14`.

## Translation settings (scripts/translate.py)

Per-model settings live in `config/translation-models.json` (exact name, then untagged name, then `default`; an untagged name means `:latest`). `qwen3.8:latest` (27.3B dense, Q4_K_M) was tuned on 2026-10-06 against 21 consecutive paragraphs (~9,500 characters, 37 first-person "I", 16 inline tags) from two chapters of the Calibre EPUB 3 conversion of *The Cosmic Serpent*, English to Russian, male narrator. Measures: wall time, paragraphs decoded with full markup, mixed Latin/Cyrillic words, masculine versus feminine first-person past forms, number of spellings of the book's core term, and reading of aligned paragraphs.

| Setting | Time | Markup kept | Mixed-script | Core-term spellings |
|---|---|---|---|---|
| one paragraph per request, no context | 686 s | 21/21 | — | — |
| 4k-character batches, 2k context, T=0 | 328 s | 21/21 | 6 | 2 |
| 8k batches, 3k context, T=0 | 288–302 s | 21/21 | 4–6 | 1–2 |
| 4k batches, T=0.3 | 375 s | 20/21 | 5 | 2 |
| 4k batches, Qwen non-thinking recommendation (T=0.7, top_p 0.8, presence 1.5) | 333 s | 21/21 | 1 | 2 |
| 4k batches, thinking `low` | 971 s | 21/21 | 1 | 2 |
| **8k batches, 3k context, T=0, glossary** | **273 s** | **21/21** | **0** | **1** |

All runs with the narrator note produced 25–28 masculine and 0 feminine first-person forms; a run whose note was accidentally omitted drifted to 18 feminine forms at T=0.3, so the note is required. Sampling and thinking did not improve markup or consistency, matching published Qwen3 MT results that greedy decoding is best and thinking adds little. Batched paragraphs with preceding translated context read as connected prose; per-paragraph requests without context lost markup on 15 of 21 paragraphs until a stray `<seg1>` wrapper was unwrapped. The glossary (143 recurring names and Hunspell-unknown words, translated once in JSON batches with an example sentence each, entries filtered per request) removed the remaining mixed-script loanwords and gave inflected, consistent renderings. Glossaries are saved beside the output as `<output>.glossary.json`; review its spellings before a full run, because they are applied book-wide.

Throughput at the chosen setting is about 35 source characters per second on this machine, roughly 2.5 hours for a 300 KB book. The `default` entry (4k batches, 2k context, glossary, T=0) is a conservative starting point for untuned instruct models; `translategemma` keeps its published single-paragraph prompt without batching, context or glossary.

`hy-mt2:latest` (Tencent Hy-MT2 30B-A3B MoE, Q4_K_M) has no system prompt; its [model card](https://huggingface.co/tencent/Hy-MT2-30B-A3B) publishes single-turn templates for background information, "X translates to Y" terminology and delimiter retention, which the `hy-mt` prompt style combines. Same sample and measures:

| Setting | Time | Markup kept | Lost sentences | Core-term spellings |
|---|---|---|---|---|
| one paragraph per request | 80 s | 21/21 | 0 | 3 |
| 4k batches, no context | 130 s | 21/21 | 0 | 1 |
| 4k batches, 2k background | 54 s | 21/21 | 0 | 1 |
| 4k batches, 2k background, own glossary | 107 s | 21/21 | 1 | 1 |
| 8k batches, 3k background, glossary | 99 s | 21/21 | 1 (truncated paragraph end) | 1 |
| 4k batches, glossary, T=0.7 (card recommendation) | 101 s | 21/21 | 0 | 1 |
| **3k batches, 2k background, own glossary, T=0** | **98 s** | **21/21** | **0** | **1** |

Hy-MT2 is about 2.8 times faster than qwen3.8 at the chosen settings and, read side by side, renders more idiomatically (fewer calques), with the narrator note obeyed (23–29 masculine, 0 feminine). It compresses as batches grow: at 8k it merged sentences and dropped the end of one paragraph, and paragraphs ran up to 18% shorter than the source, so batches stay at 3k. It builds the full 143-term glossary in JSON in 53 s. The glossary is model-specific (Hy-MT2 chose "айяуаска", qwen3.8 "айауаска"); because `<output>.glossary.json` is reused when present, delete or rename it when switching models for the same output.

`qwen3.6:35b-a3b-nvfp4` (35B MoE, 3B active) ships with Ollama defaults including `presence_penalty 1.5`, which penalizes the tags and glossary terms a translation must repeat; the `default` options now pin `presence_penalty` to 0 for every model. Same sample: 2k batches with 1.5k context and glossary took 73 s with 21/21 markup, no lost sentences and one core-term spelling; 4k and 8k batches left one English word untranslated and 8k lost markup on one paragraph; without the glossary the core term had three spellings; the card's non-thinking sampling (T=0.7, presence 1.5) shortened a paragraph; thinking took 1,034 s for no measurable gain. Its Russian is correct but follows English syntax closely.

`gemma4:26b-a4b` (26B MoE, 4B active) uses the `instruct` style; with thinking off it emitted no thought channel. 4k batches with 2k context and glossary took 65 s with 21/21 markup, all-masculine narrator and one core-term spelling, and it kept that spelling even without the glossary. Every non-thinking configuration produced one paragraph about 17% shorter than the source and an occasional dropped preposition. The card's T=1.0 sampling and thinking were far slower (runs above 20 minutes, under concurrent memory pressure) and were not pursued.

Comparison at each model's chosen settings, same 21 paragraphs:

| Model | Settings | Time | Reading |
|---|---|---|---|
| qwen3.8:latest | 8k batches, 3k context, glossary | 273 s | correct, literal |
| hy-mt2:latest | 3k batches, 2k background, glossary | 98 s | most idiomatic; compresses at large batches |
| qwen3.6:35b-a3b-nvfp4 | 2k batches, 1.5k context, glossary | 73 s | correct, closest to English syntax |
| gemma4:26b-a4b | 4k batches, 2k context, glossary | 65 s | fluent; occasional dropped word |
| translategemma:latest | one paragraph, published prompt, no context or glossary | 121 s | readable; markup lost on 4/21 paragraphs, core term spelled four ways |

All four obey the narrator note and keep markup with batching and glossary. On this sample Hy-MT2 gives the best prose at moderate speed; the scores are from one book's narrative prose and do not cover notes, verse or long-range drift.


## Russian ethnography: Материальная культура чукчей (1991)

Source: `input/Владимир Тан-Богораз - Материальная культура чукчей.pdf`, 263 image-only PDF pages, SHA-256 `bbcc7799b2bf2e4a334f8cb51f58e7ad80e4bd1f62c67f19cf4b48d448b97fcf`. Body scans are mainly 768 × 1181 pixels inside a larger PDF page, so enlargement cannot restore lost glyph detail. Profile: `config/chukchi-material-culture/`; evidence and full extraction caches: `work/chukchi-material-culture/`; EPUB and offline review: `output/chukchi-material-culture/`.

The printed title/copyright pages establish В. Г. Богораз, *Материальная культура чукчей*, authorized translation from English, Москва: Наука, Главная редакция восточной литературы, 1991; ISBN 5-02-016757-6 (9785020167575). Exact-ISBN Open Library metadata is frozen in `enrichment.json`. Its romanized title and Waldemar Bogoras authority name required explicit, source-evidenced record/author aliases; independent edition identity checks remain mandatory. The printed page count is 224; catalog text pagination differs. No eligible online cover was returned, so the original green cover is retained.

### Local OCR experiments and limits

Compared checksum-pinned best/fast Russian Tesseract, mixed `rus+eng`, Apple Vision Russian, and RapidOCR Cyrillic PP-OCRv5 recognition with PP-OCRv4 detection. Full selected caches: Tesseract best Russian at 200 dpi, with 31 Latin-heavy pages using `rus+eng`; Vision at 300 dpi; RapidOCR at 200 dpi. Russian-only Tesseract avoided many Latin intrusions into prose, whereas mixed recognition helped citations and indigenous transliterations. Vision helped comparison and the bibliographic/reference sections. RapidOCR frequently lost or corrupted dense small-font lines in this scan, so it is a comparison witness rather than the primary transcription. These are source-sample findings, not a measured whole-book CER/WER benchmark.

A small PDF 6 conditioning experiment (`ocr-conditioning/`) compared grayscale, Otsu, fixed threshold 180 and Sauvola-61 at 300 dpi. Thresholding produced worse text or no text; none of these experimental outputs was promoted. Recognition remains noisy. The ordinary shared two-witness pass applied 177 token corrections; 13 exact scan-verified opening-page corrections are recorded separately. Russian Hunspell/wordfreq/SymSpell produce proposals, not automatic normalization of historical or Chukchi words. At this conversion stage, English bigram/crop lexical repair was not yet generalized to Russian. The offline proofreading sheet contains 1,769 lexical groups and six additional diagnostics; the OCR disagreement queue is separate. Neither is an error count or proof of full proofreading.

### Layout and completeness

Fourteen navigable sections cover the eight numbered chapters, front editorial text, the inserted photographic plates, notes, bibliography and afterword. All 40 photographic plate leaves are retained, along with 114 reviewed illustration crops/leaves. Some plates are sideways in the supplied source and retain that orientation. Complex side-wrapped prose may remain inside source facsimiles instead of editable text. Suggestion-only connected-component grouping helped locate artwork; visual review rejected ordinary prose and footer false positives, consolidated thin-tool drawings, and checked the full plate inventory.

Short lower-left separator proposals supplied reviewed footer zones. Forty-eight page-group note blocks are accessible; seven symbol references have exact links, other groups use page-level navigation. Numeric inline note linkage and italics are not fully recovered. PDF 8's bibliographic footer continues onto PDF 9 and is retained as one page-group note. This should not be described as complete per-note apparatus recovery.

Printed pagination is not a constant PDF offset: PDF 6–144 maps to printed 5–143, PDF 145–191 to 146–192, followed by 40 plate leaves, then PDF 232–263 maps to printed 193–224. **Printed pages 144–145 are absent from this source**; `pagination-gap-check.png` records the inspected transition. A source-gap notice before PDF 145 prevents accidental sentence joining across the absent leaves. Missing text has not been invented.

Illustrations use original embedded scan pixels, monochrome JPEG export, and one encode from uncompressed crops. The larger PDF white frame originally forced 300-dpi rendering; accepting contained crops of a dominant 80%-area upright scan avoids that enlargement while retaining overlay safety. Color policy can now be used independently of paper normalization. The green cover remains RGB. Resource downloads are reproducible with `bootstrap.py --language ru`, whose additional manifest includes actual checksums; cache language and per-page language selection are validated before build.

### Replay

```sh
.venv/bin/python scripts/bootstrap.py --language ru --verify-only
.venv/bin/python scripts/pipeline.py \
  'input/Владимир Тан-Богораз - Материальная культура чукчей.pdf' \
  --profile config/chukchi-material-culture/book.json \
  --work work/chukchi-material-culture \
  --output output/chukchi-material-culture --skip-extraction
```

For fresh extraction, invoke `extract.py` with this profile and work directory separately: `--engine tesseract --dpi 200`, `--engine rapid --dpi 200`, `--engine vision --dpi 300`. The last command may require access to the local macOS Vision service outside an agent sandbox. No book text was uploaded to an LLM service during this conversion.

Validation: 270 unit tests passed; Black and `git diff --check` passed. EPUBCheck 5.4.0 reported no errors or warnings, and reconciled OCR-row coverage passed (not a claim of missing-line or spelling completeness). All 154 exported monochrome crops retain independent source crop dimensions; maximum mean JPEG pixel error was 1.154/255 and minimum PSNR 43.52 dB. All XHTML documents declare Russian, all 40 plate leaves and the source-gap notice are present. A UTF-8-aware rendered chapter-opening check displayed Cyrillic and paragraph layout correctly. Cached replay matches the new reproducibility baseline; eleven earlier edition snapshots remain unchanged. Reports: `work/chukchi-material-culture/artifact-verification.json`, `validation-summary.json`, `unit-tests.log`, `epubcheck-final.log`, `new-book-replay.log`.

Installing an unused Cyrillic ONNX model invalidated the existing English marker cache because that helper fingerprints all model files. The final Daimonic Reality regression used the original English model/dictionary set through the new `regression.py --models` option and passed. Two redundant global-model batches were stopped after their completed cases and this separate case covered all prior editions. Full-page extraction now fingerprints only used models; the marker helper's broader cache fingerprint remains an optimization opportunity. The separate English regression directory contains symlinks, not copied models.


## Избранники духов — В. Н. Басилов (Политиздат, 1984)

Source: `input/В. Басилов - Избранники духов-Политиздат (1984).pdf`, 207 image-only PDF pages, SHA-256 `0699f8ffd9b000050ab8660f6d6cd3220c9fa7cf1b6e28df7597ece6942ff540`. Native body scans are roughly 660 × 940 one-bit pixels. Profile: `config/chosen-by-spirits/`; OCR, source thumbnails and reviews: `work/chosen-by-spirits/`; EPUB and proofreading: `output/chosen-by-spirits/`.

Printed title, copyright and colophon establish Владимир Николаевич Басилов, *Избранники духов*, Москва: Политиздат, 1984, 208 с.: ил. The colophon credits editor Ю. В. Степанов and artist А. А. Брантман. No ISBN is printed; classification, stock and production codes were not misused as ISBNs. Metadata comes from the scanned edition, and the original color cover is retained. Ten navigable sections follow the printed contents. Body PDF 4–206 corresponds to printed 5–207; the last PDF leaf contains the printed contents and colophon. No body pagination gap was found in the page inventory; this does not establish that every source glyph was recognized.

### OCR, footers and illustrations

Full local caches were produced with Tesseract best Russian, Apple Vision `ru-RU`, and RapidOCR Cyrillic recognition, all at 300 dpi. Source samples clearly favored Vision for complete Russian prose; Tesseract dropped or corrupted portions of lines, and RapidOCR often missed dense lines. All pages therefore use Vision as primary, with the others as comparison witnesses. No book text was sent to an LLM service. Five source-verified name forms are protected; dictionary proposals do not automatically modernize terminology.

Vision emitted several boxes less than one pixel outside the raster. The shared extraction fix clips only this bounded overshoot, records raw coordinates, rejects larger/empty geometry, and fingerprints the normalization semantics in new caches. Earlier caches remain untouched. One Vision glyph was U+FFFE, illegal in XML. Reconciliation now substitutes a visible U+FFFD at the same character position and records code-point/offset review evidence. The particular winter-word hyphen was then checked against the scan and corrected through an exact overlay. Optional subtitles no longer cause title-page generation to fail.

All 30 illustration crops are preserved, including nine illustrated chapter-opening leaves. Crop review adjusted caption bounds and narrow side-wrapped diagrams; export uses native scan pixels, grayscale JPEG and one encoding, with the color cover kept separately. Twenty-three footnote regions were source-reviewed. Thin separator rules, relative footer type, markers and bibliographic initials/year evidence supply proposals; underlined body text and printer signatures were rejected. A source-checked bibliographic footer on PDF 123 was transcribed explicitly after primary OCR merged its two tiny lines into unreadable text. Seven exact OCR overlays are kept in `corrections.json`.

The verse detector now rejects Cyrillic hyphenated prose just as it rejects Latin wraps. A long, deeply inset, ragged run in smaller type can follow a completed sentence without a colon; eight lines, a block gap and type contrast are required. Limited OCR box overlap is accepted when line centers advance, since descenders can cross neighboring boxes. This recovers the ten-line prayer on PDF 119 without a page/phrase detector override. Short chants still use the established introduced-verse rule. Inline italics and exact numeric footnote backlinks remain incomplete; page-group note navigation is available. Automated correction and sampled layout review are not complete proofreading.

### Replay

```sh
.venv/bin/python scripts/pipeline.py \
  'input/В. Басилов - Избранники духов-Политиздат (1984).pdf' \
  --profile config/chosen-by-spirits/book.json \
  --work work/chosen-by-spirits \
  --output output/chosen-by-spirits --skip-extraction
```

For fresh extraction, use `extract.py` with this profile/work and `--dpi 300` for each of `--engine tesseract`, `--engine rapid`, and `--engine vision`. The Vision command requires the local macOS service. Source checks include the full-page contact sheets, figure-review sheets, note proposals, `footer-rule-check.jpg`, `footer-123.png`, `colophon.png` and opening-page source images.

Validation: 274 unit tests passed; EPUBCheck 5.4.0 reported no errors or warnings; reconciled OCR-row coverage passed. All 30 grayscale figure exports retain independent original-crop dimensions (minimum JPEG PSNR 48.91 dB; maximum mean pixel error 0.335/255). UTF-8 rendering checked the complete opening and ten-line prayer; five verse blocks are retained. The offline review has 820 lexical groups and three additional diagnostics; these are proposals, not measured error counts. All twelve earlier edition snapshots, including the deferred Chukchi book, passed; Wizard was checked again after the final verse changes. The old corrected-row artifacts contain no XML-invalid characters, so glyph sanitation does not change them. A new reproducibility baseline is recorded for this book. Reports: `artifact-verification.json`, `regression.log`, `verse-regression.log`, `new-book-regression.log`, `new-book-replay.log`, `unit-tests.log`, and `publish-final.log`.

### Russian whole-book correction follow-up

The first Basilov review exposed two shared gaps: noisy wrap dashes (`уме-.` / `ния`, `на-.` / `столько`) escaped source-line joining, and automatic lexical correction still used English resources. The source confirms `Алтайцы`, `шаманского` and `более` on PDF 85, 71 and 106. No book-specific spelling substitutions were added.

The shared ranker now selects language-specific frequency resources and dictionary-accepted observed inflections, then builds a smoothed whole-book bigram Markov model with unigram backoff. Russian one-edit insertions/deletions/substitutions and title-case words are eligible with dictionary acceptance, recurrence (at least two book observations), a Zipf floor of 2, and local crop verification. Protected forms, accepted words and references retain their guards. A regression example uses the same malformed token with two equally close targets (`коды` / `козы`); changing adjacent words changes the winner.

Russian line crops use the primary ink bounds rather than a two-point expansion that admitted neighboring scan lines. Word anchors are case insensitive. Conflicting Tesseract crops can use the pinned Cyrillic RapidOCR recognizer at two fixed scales; both readings must match the ranked target and score at least 0.90. These are correlated readings, and the score is not calibrated confidence. All readings, runtime/model hashes, source rectangles and rejected decisions remain auditable. Dictionary membership is cached within each immutable ranker to avoid repeated morphological lookups.

Noisy-dash joining is enabled by `repair_word_wraps`, preserving the default diplomatic behavior. The initial broad check caught an existing Natural Mind editorial overlay that deliberately keeps printed `func-. tioning` in its diplomatic edition; the opt-in guard preserves that source-specific edition choice.

The rebuilt Basilov edition has 63 locally verified lexical repairs (60 Tesseract crop pairs, three RapidOCR crop pairs), plus seven wrap joins (the two reported noisy seams and five additional rare-form joins). All accepted crops were visually checked. The scan-backed proofreading sheet now has 766 lexical groups and one diagnostic; these are review candidates, not confirmed errors. EPUBCheck reports zero errors/warnings, and retained-row coverage passes. Tests cover Russian Markov disambiguation, missing letters, title-case anchors, OCR conflict/low-score abstention, model-cache invalidation and noisy-dash scope.

Final verification: 286 tests pass; Black and whitespace checks pass. Every changed canonical-text token is accounted for by the 63 lexical edits or seven audited wrap joins; the 30 figures, five verse blocks and 23 note groups remain. All twelve earlier editions match their existing baselines after the Natural Mind scope fix. The new Russian output and lexical audit match an independent warm replay using the complete Russian model directory. An attempted mixed-case replay used the English-only model folder for Basilov and failed its missing Cyrillic-model dependency; the correctly configured Russian replay passes. Final evidence: `work/chosen-by-spirits/russian-repair-verification.json`, `russian-final-tests.log`, `russian-black.log`, `russian-book-replay.log`, `russian-earlier-regression.log`, and `russian-final-replay.log`.

### Evaluation harness and runtime checks (2026-10-07)

`scripts/translate_eval.py` replaces the session-only grid script. It cuts the windows listed in `config/translation-eval.json` (two *Cosmic Serpent* prose windows, an 8,000-character window of its unmarked notes — 4,000 characters was too few blocks for notes detection — and *Wizard of the Upper Amazon* verse/dialogue and endnotes; 74 blocks, ~27,000 characters) into one sample EPUB and runs it through the same code path as `translate.py`: glossary built from both whole books, runtime checks, typography. Metrics per run: time, model calls, fallback levels, blocks still flagged after retry, `quality_flags` counts over non-note blocks (notes keep citation sentences in English by design), output/source length ratio, glossary terms rendered under more than one spelling, and masculine/feminine first-person past forms. Spelling variants are matched by similarity only for renderings of six or more letters; shorter ones (Перу, Луна) otherwise matched ordinary words.

hy-mt2 baseline at its chosen settings: all 72 translated blocks decoded with full markup at the first attempt (the other two of 74 are citations kept in English), no quality flags, no spelling variants, 35 masculine and 0 feminine first-person forms, length ratio 1.00. The runtime checks retried nothing on this sample (a checks-off run replayed entirely from the checks-on cache), so they cost nothing when output is clean. Reducing `num_ctx` from 16,384 to 8,192 changed neither speed (286 s including glossary construction versus 273 s) nor swap use, which rose from 14.1 to 15.1 GB during both runs; the 8,192 setting is kept as the smaller reservation but is not a measured improvement.

### Notes batching, small caps and book brief (2026-10-07)

Notes made only of commentary (no citation sentence) now batch like prose; notes that mix citations and commentary keep the sentence-level path so citations stay byte-identical. On the evaluation sample, requests for the same 74 blocks fell from about 45 (Phase 1, excluding glossary construction) to 20 and the translation took 95 s, with all 72 translated blocks still decoded with full markup, no quality flags, no spelling variants and 35 masculine / 0 feminine first-person forms. On whole books the effect is larger where commentary notes dominate: 882 of 1,490 notes in *Shamanic Trance* and 51 of 228 in *Cosmic Serpent* qualify.

Faux small-caps headings (a capital outside a smaller-type uppercase span, as in Calibre's *Cosmic Serpent* chapter titles) are detected before translation and re-set around the translated words; 27 headings qualify in *Cosmic Serpent* and none in the pipeline-built books.

The book brief (genre, register, period, and the gender of each recurring capitalized name, built in batches of 30 names so requests fit an 8,192-token context) cost 18 extra requests on this sample and changed none of the automatic measures, which were already clean; its intended effect, gender agreement for named third persons, is not measured by them. Its people list was mostly right (e.g. the ethnographer Gebhart-Sayer as female) but labelled the Huni Kui people as a man, so it should be reviewed with `--review-glossary` before a run.

### Second-opinion review, timing, parallel documents, review sheet (2026-10-07)

`--review-model` post-edits prose blocks still flagged after the retry or that lost inline markup. The reviewer receives the source with its placeholder tags, the draft as plain text, and the glossary and brief entries for the passage; an edit is kept only if it decodes with the source's markup and the checks do not get worse. With translategemma as the primary model (6 of 72 blocks fell back to dropping emphasis) and qwen3.8 reviewing, 5 of those 6 regained full markup and one block flagged for a lost sentence was replaced; the sixth was a note, which the reviewer does not handle. The six reviews took 101 s. hy-mt2 produced no flagged or fallback blocks on the sample, so with it the reviewer would not run.

Ollama's per-request counters are now logged (`timing` in the run report). At hy-mt2's chosen settings prompt processing is 8.3% of model time (26,191 prompt tokens in 23.6 s versus 11,126 output tokens in 260 s, about 43 tokens/s); translategemma's single-paragraph prompts give 6.9%. Prompt-prefix reuse cannot save more than that, so it was not implemented; speed has to come from decoding.

Documents can be translated concurrently (`parallel` in the model config), each with its own context and a shared, locked cache; with a deterministic model the output is identical to a serial run. It needs Ollama started with `OLLAMA_NUM_PARALLEL` at least as high and has not been measured, because that requires restarting the user's Ollama service. The machine is an M5 Pro with 48 GB running Ollama 0.35.1; hy-mt2 is a GGUF build on the llama.cpp engine. Ollama's MLX engine and a Hy-MT2 1.8B draft model were not tried: both need new multi-gigabyte model downloads.

Every run now writes `<output>.review.html` (side-by-side source with placeholder tags and translation, flagged and fallback blocks highlighted) and `<output>.pairs.json` (block keys and source hashes), and applies `<output>.corrections.json` before the model; a correction whose source hash no longer matches stops the run.
