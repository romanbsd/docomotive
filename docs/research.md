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

The fourth input is a 276-page scan, SHA-256 `59448237b057bd8b96cf9c5e33b50f6b43efc6e0123e170158984319b48f1a6a`. All pages were processed locally at 300 dpi with Tesseract, Apple Vision and RapidOCR's Paddle PP-OCRv4 English recognizer. Embedded OCR was also retained as a witness. Source inspection favored Vision for this highlighted scan; lower lexical review counts support triage but are not a word-error-rate measurement. No remote model was used.

The seven index pages exposed the cost of a guessed gutter: a 0.52 split clipped initial letters in the right column. Measuring actual text bounds yielded a reviewed 0.485 split; all three engines were rerun for those pages. Explicit blank-page exclusions prevent bleed-through entering the canonical text. Consecutive quotation lines with matching indentation now assemble into paragraphs, and a checked cross-page continuation restores `analo- / gous`. A scan mark after `ap-` required a source-checked punctuation correction before normal dehyphenation could restore `approach`.

A reusable figure module extracts 18 photographs and diagrams with normalized profile coordinates, preserves captions separately, and audits image hashes and insertion positions. Visual inspection of crops caught clipped labels in the mitosis, corn life-cycle and breakage-fusion-bridge diagrams. Figure interiors are excluded from OCR text, avoiding duplicated diagram labels. Glossary entry labels define 87 independent paragraphs and italic spans; entry boundaries also prevent a new entry merging with the previous page's definition.

All 157 notes pass per-section sequence checks. The scan does not provide reliable inline superscript recovery, so navigation explicitly links chapters to their notes and notes back to chapters. This is a documented compromise, not reconstructed inline reference evidence. The supplied 1000 × 1500 cover supersedes a matching but smaller online image. ISBN API responses contain misspelled title, publisher and conflicting dates; exact source-reviewed overrides preserve raw cached records while retaining the scan's 2003 edition identity.

Seven exact OCR corrections and eleven separately recorded printed-typo corrections were verified against source crops. Examples of actual printed mistakes include `aminc`, `physican`, `chromsomes`, and a duplicated `was` across pages. Independent OCR agreement can therefore support a wrong printed spelling: it does not justify silently treating that spelling as an OCR error. The reading edition applies the editorial overlay; the default edition preserves those printed forms. Whole-book vocabulary analysis and source-linked review run after assembly. Unreviewed suggestions remain suggestions.

Validation checks source-row coverage, canonical text against rendered XHTML, figure inventory, glossary boundaries, strict note sequences, EPUBCheck, repeat-build bytes and the 48-test regression suite. Visual QA uses decoded UTF-8 XHTML through a WeasyPrint/PyMuPDF proxy; feeding XHTML as a filename without explicit decoding caused mojibake in the proxy, while the EPUB bytes were correct. This proxy does not establish behavior in actual EPUB readers, nor does the review queue establish complete proofreading.

Final artifact checks passed for both editions: 8,124 retained body rows mapped exactly once, 502 explicitly excluded rows, 18 figures, 157 endnotes and no orphan notes. Both EPUBs have zero EPUBCheck errors/warnings and byte-identical cached repeat builds. The source-spelling review contains 182 lexical groups and ten diagnostics; the reading-edition review contains 174 groups and eight diagnostics. Black and all 48 tests pass. The native Garb book and Huss article were also rebuilt to separate regression directories to check the shared assembly changes.

Source-layout follow-up: on PDF page 20 (printed xviii), scan skew displaced the left margin enough to misclassify “Kimber Award…” as a paragraph opening. A source-checked cross-page continuation now joins it to page 19's “chosen for the”. The following Marcus Rhoades passage is explicitly classified as a quotation. The profile disables first-line indentation inside block quotations; the default renderer behavior for previous books is unchanged. A regression checks the continuation and quotation role, and another checks profile-specific quotation indentation (49 tests total).

The page-20 override was subsequently removed in favor of generic inference. `scripts/paragraph_layout.py` fits deterministic robust left/right margin envelopes from long body rows. Indentation is measured relative to the sloping margin at each line's vertical coordinate; a sustained inset run needs evidence at both edges before becoming a quotation. This recovers the unindented page continuation and quotation without matching “Kimber Award” or page 20. Quotation first-line indentation is carried by inferred block evidence, rather than a book-wide styling switch. Sparse or unstable pages fall back; native fonts and hanging reference/index layouts retain the existing path. Models, fallback reasons and inferred quote row IDs are audited. The 54-test suite includes synthetic skewed pages, ordinary paragraph indentation, rejection of one-sided insets, sparse-page fallback, and native/reference exclusions.

On this scan, 191 page-column fits are accepted and 26 fall back; 25 blocks are classified as quotations. A sample contact sheet was compared to the scan's inset text, including the reported passage. Compared with the previous assembled text, the only additional wording change is restoration of `under- / standing` to `understanding`. All retained source rows remain covered exactly once. The native book/article rebuilds are byte-identical to their earlier regression outputs. Both Natural Mind editions retain identical text and coverage; paragraph segmentation can improve under the generic scan model, so byte identity is not claimed for those OCR books. The new AGENTS.md rule requires general pipeline fixes and regression checks, with source-specific evidence retained in profiles only where needed.
