# Conversion research and per-book findings

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
