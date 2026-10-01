# First-book research report

## Current result

Input: 242 scanned PDF pages, SHA-256 `8d5a7f67e67a73c39d0d0b0cb8be42c3eb6a6304b07239122760e31bd757c0f2`. The filename's 2012 refers to the scan/catalog record; the source copyright page gives 1972. The EPUB metadata follows the printed book.

The profile retains 14 sections: nine chapters, acknowledgments, afterword, works cited, suggested reading, index. There are 225 original-page anchors. Blank/duplicate/divider pages are accounted for in `config/book.json`; printed contents are replaced by linked navigation. All-page contact sheets were inspected for layout roles. The book contains no interior diagrams or photographs; its original cover, back cover and publisher emblem are retained rather than adding invented illustrations.

The pipeline detects recurring headers statistically, then uses limited profile fallbacks for remaining short margin artifacts. On this book it found 12 recurring header groups and 215 consistent numbered footers. Header variant clustering is seeded by frequent text, with no transitive fuzzy chaining; robust median/MAD limits vertical outliers. Odd/even concentration selects recurrence opportunities. Beta(1,1) posterior intervals describe recurrence within an observed span; they are **not calibrated probabilities of header correctness**. Unique titles and short chapter-opening text still require separate treatment.

Detected engine disagreements were inspected in cropped scan montages. The current overlay contains 75 source-checked replacement rules, including several whole-line repairs and accented names. Two damaged glyph cases required contextual inference and are marked in their reasons. Automatic multiple-witness changes and dehyphenation have separate audit event types. Printed typos have a separate seven-item editorial overlay (six spelling repairs and one line-wrap punctuation repair). These counts are rules/events, not a character error rate or proofreading completeness score.

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
