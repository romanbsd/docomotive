# Classical OCR correction: literature review and local evaluation

The most useful next step is richer, source-linked candidate generation using
book vocabulary and OCR-specific transformations. The evaluated statistical
scores are not safe automatic replacement rules. No production correction
policy, book profile, EPUB or regression baseline changed in this work.

Ten primary-source PDFs are archived in [`papers/`](papers/README.md), with
provenance and checksums. The user supplied the PoCoTo 2014 full text after
publisher access failed; its paper and cited repository have now been reviewed. This review separates paper results from local
component experiments; none of the papers' complete systems was reproduced.

## What the papers establish

| Paper | Verified method/result | Application and qualification |
|---|---|---|
| [Evershed & Fitch, 2014](papers/evershed-fitch-2014.pdf) ([primary source](https://www.projectcomputing.com/resources/CorrectingNoisyOCR.pdf)) | Multi-character weighted channel, trie-guided search, interpolated trigrams/gap trigrams, external 5-grams; over 60% WER reduction on reviewed historical newspapers | Borrow directed edit costs and candidate search. The final decoder uses stochastic record-to-record travel, so the complete algorithm is not deterministic as described. |
| [Mei et al., 2016](papers/mei-et-al-2016.pdf) ([primary source](https://arxiv.org/abs/1611.06950)) | Levenshtein candidate radius 3, string/corpus/lexicon/context features, supervised regression; reported top-1 61.50%, top-3 71.45%, candidate bound 78% | Separate detection, candidate recall and ranking. The biology-book setup uses web-scale 5-grams, specialist lexicons and filtered detected tokens, including correct tokens flagged as errors. These are not end-to-end correction guarantees for arbitrary books. |
| [Schulz & Kuhn, 2017](papers/schulz-kuhn-2017.pdf) ([primary source](https://aclanthology.org/D17-1288/)) | Spell checking, compound/split modules, text-internal vocabulary, character/token SMT, Moses decoding and MERT module weights | Strong architectural fit for combining proposals and book terminology. Frequency alone does not certify a book word; repeated OCR artifacts can contaminate it. |
| [Drobac & Lindén, 2020](papers/drobac-linden-2020.pdf) ([primary source](https://doi.org/10.1007/s10032-020-00359-9)) | Calamari models and confidence voting, then context-sensitive weighted finite-state replacements; Swedish CER 2.9→2.7%, Finnish 1.9→1.7% in Table 5 | The 1.7/2.7% numbers are complete-pipeline CERs. The corrector tokenizes on blanks and cannot change spaces (§5.4); it mainly repairs punctuation. A Finnish lexicon made no changes in this experiment. |
| [Afli et al., 2016](papers/afli-et-al-2016.pdf) ([primary source](https://aclanthology.org/L16-1153/)) | Aligned French OCR/corrected text, word-based SMT and language models; WER 22.41→19.52%, 12.99% relative improvement | Needs substantial aligned data. Standalone language-model correction worsened WER to 24.34% when training/test periods differed: historical fidelity is a measured risk. |
| [Englmeier et al., 2022](papers/englmeier-et-al-2022.pdf) ([primary source](https://aclanthology.org/2022.jlcl-1.1/)) | A-PoCoTo uses separate logistic reranking and replacement decisions, with parallel OCR features; studies identical, same-document-disjoint and unrelated-document training | Especially useful for abstention and evaluation design. Some “optimal” experiments train and test on identical material; general-model experiments are separate. Large general training corpora can work, but some documents still deteriorate. |
| [PoCoTo, 2014](papers/PoCoTo-2014.pdf) | Interactive concordances, selectively applied error-series corrections, document profiling with separate historical/OCR channels; library user studies measure corrections per minute | Strong fit for reducing review effort. Human-reviewed batching is distinct from automatic acceptance, and productivity depends on the profile and user. |
| [Nguyen et al. survey](papers/nguyen-et-al-2021-survey.pdf) ([primary source](https://adammo12.github.io/adamjatowt/cs21.pdf)) | Taxonomy of correction, merging, resources and evaluation, with 167 references | Useful organizing bibliography. Published online in 2021; ACM volume 54(6) is dated 2022. |

Two details change the implementation priorities. Evershed's “reverse OCR”
correlates generic-font bitmaps of the candidate and observed strings, not the
original scan. It is a visual confusion estimator, not independent source
validation. The minimum-cost edit path is also explicitly not a calibrated
probability summed over all paths (§4.1). Our existing source crops provide a
more direct fidelity check.

Likewise, Drobac's voting sums character probabilities from related Calamari
models. Applying that formula to heterogeneous Vision, Tesseract and RapidOCR
confidence values would need alignment and calibration; those values cannot be
assumed comparable. Agreement and contextual anchors remain useful features.

## Mapping to the current pipeline

`ContextRanker` in `scripts/lexical_repair.py` already combines SymSpell
candidates, general bigrams, raw book counts, unigram backoff and a small
single-character confusion bonus. Its source-faithful repair path protects known
words and requires crop readings before applying an edit. This means it does not
automatically resolve real-word errors such as `modem → modern`; those can be
review candidates when source witnesses support the alternative.

The separate `word_spacing.py` pass already requires literal anchored cached
witnesses and fresh crop agreement. Its previous 23-case spacing benchmark found
that context scores and glyph gaps can favor wrong boundaries. A finite-state
implementation does not remove that uncertainty by itself.

The main gaps for the statistical architecture are directed multi-character
error estimates, broader candidate recall, independent held-out calibration and
cleaner book vocabulary. A higher-order corpus model is useful only if its
training text supports the genre and spelling of the source. Neither a dictionary
nor a deterministic decoder inherently preserves historical language.

## Reproduce the local experiment

```sh
.venv/bin/python scripts/statistical_ocr_research.py \
  --output work/ocr-literature/evaluation.json
.venv/bin/python -m unittest discover -s tests
```

Required inputs are the existing scanned-book sources, profiles, three-engine
extraction caches and pinned language resources. The script does not run new OCR,
train a neural model, call an LLM or apply corrections. The checked-in
[`evaluation report`](statistical-ocr-evaluation.json) contains case-level
contexts, labels, source boxes, overlay provenance, top-three candidates, margins,
fold rules/coefficients, source/config/resource/cache fingerprints and versions.
A second run produced the same report bytes.

### Dataset and leakage controls

The final set contains 91 distinct previously reviewed token corrections across
five English scanned books: Natural Mind 7, Feeling for the Organism 3,
Pharmako/Poeia 14, Wizard of the Upper Amazon 25 and Otherworlds 42. Labels come
from existing source-reviewed correction overlays, not guesses made by this
experiment. They retain their overlay evidence and PDF checksum; these crops
were not all re-reviewed during this task.

The experiment adds 90 paired counterfactual controls by putting each printed
correct token into its source context. One ambiguous control is omitted. This
stress-tests preservation of those correct forms, including unusual terminology
and names; it is not an unbiased sample of naturally correct prose. Headlines,
references/indexes, short/nonalphabetic changes, cross-script changes and detected
line-wrap fragments are excluded. A prior exploratory extraction admitted such
fragments; its 111-case results are superseded by the filtered final set.

Each fold holds out an entire book for error-rule and regression training. Every
benchmark page is removed from the raw book corpus before computing book counts,
trigrams, skip-grams and its vocabulary. The rest of the raw OCR corpus supplies
adaptation; corrected output and held-out answers do not train those priors.
Book vocabulary adds only words recurring on other pages and matched by both
cached non-primary witnesses. Such consensus can still contain correlated errors.
Modern general lexicon statistics are a separate prior and may contain artifacts.

### Components tested

- **Directed weighted edits:** character/chunk transformations of at most three
  letters learned from other books' reviewed pairs, with smoothed empirical
  costs. Dynamic programming chooses the minimum-cost path. Identity has zero
  cost; unseen ordinary edits cost one. This is a ranking feature, not a fully
  calibrated noisy channel.
- **Bounded rewrite search:** invert learned rules through at most two rewrites,
  keeping 64 states per step; accept final vocabulary matches. This is a small
  edit lattice, not a replication of HFST's contextual rule system or Evershed's
  large trie/A* search. Insert/delete loops are bounded.
- **Candidate pools:** general SymSpell radius 2; then recurrent witnessed book
  vocabulary; then rewrite-derived vocabulary matches. The original is always
  present. The expected answer is never injected.
- **Corpus ranking:** general/book bigram backoff, book trigram and gap-bigram
  counts; optional anchored peer agreement and a smoothed character-trigram
  prior from the general lexicon. Fixed weights are heuristics.
- **Supervised ranking:** deterministic ridge regression on candidate features
  and 0/1 target labels, fitted on other books. This tests the feature-ranking
  idea, not Mei's AdaBoost system or A-PoCoTo's logistic models.
- **Decision experiment:** choose the ridge margin giving best training coverage
  with no training wrong replacements. This in-sample margin selection is
  deliberately evaluated on held-out books; it is not independent calibration.

### Results

Candidate generation and ranking must be measured separately:

| Candidate pool | Reviewed answers present / 91 |
|---|---:|
| General lexicon, unit radius 2 | 58 |
| Add recurrent witnessed book vocabulary | 70 |
| Also add bounded learned rewrites | 70 |

All 12 additional answers came from book vocabulary in this experiment. Learned
rewrite expansion added no further answer, despite providing a more expressive
error model. Missing candidates include rare or accented terms and severely
corrupted words; no reranker can select an absent answer.

| Ranking ablation | Exact top-1 / 91 errors | Answer in top-3 / 91 | Correct controls retained / 90 |
|---|---:|---:|---:|
| Unit edit + unigram prior | 29 | 44 | 71 |
| Weighted edit + unigram prior | 28 | 40 | 68 |
| Weighted edit + bigram context | 34 | 42 | 53 |
| Add book vocabulary + trigram/skip counts | 38 | 48 | 53 |
| Also add learned rewrite proposals | 38 | 48 | 53 |
| Also add anchored peer agreement | 40 | 56 | 60 |
| Also add character trigrams | 41 | 56 | 60 |
| Cross-book ridge reranking | 19 | 54 | 82 |

These are proposal rankings without production protections. Weighted edits alone
underperformed unit costs. Context increases correction coverage while damaging
more correct controls. Character trigrams add one top-ranked answer here, too
small a selected sample to justify a general gain. Ridge is better at retaining
originals but substantially worse at top-1 recovery; a learned ranker is not
inherently an improvement over hand weights.

The training-only ridge margin accepted 11 held-out changes: four correct
repairs, five wrong replacements of already erroneous tokens and two damaged
correct controls. Training-perfect acceptance did not transfer across books.
Examples include `Nora → for` instead of the reviewed `flora`, and `nearix →
near` instead of `nearly`. That fails our source-fidelity requirement.

An additional conservative **cached-source proposal gate** requires a lowercase,
unknown original and agreement of both anchored peer readings with the selected
candidate. It yielded three correct proposals and zero wrong changes in this
selected set (`indirec → indirect`, `inkhlot → inkblot`, `suff → stuff`). It does
not include fresh crop validation and authorizes no production edit. Known-word
and case protection deliberately sacrifices coverage.

## Recommended direction

1. Improve the review queue first: union general dictionary, witnessed recurrent
   book terms, bounded OCR transformations and spacing proposals. Keep provenance
   for each generator, the original reading and the source location.
2. Use consensus and leave-source-page-out context as ranking features. Keep
   name/protected-term/historical-spelling vetoes and fresh source crops as
   acceptance requirements. Do not automatically protect a term just because an
   OCR artifact recurs often.
3. Expand representative ground truth with naturally correct lines, real-word
   errors, rare names, accents, prose and verse. Split by book and by error type,
   then reserve independent calibration data before selecting any margin.
4. Measure candidate recall@k, exact replacement precision, erroneous changes to
   correct text, CER/WER and manual review saved separately. Our selected token
   cases cannot establish whole-book CER/WER or a corpus-wide false-positive rate.
5. Revisit weighted trie search or contextual WFSTs if missing-candidate analysis
   shows that low-cost multi-character edits recover enough useful targets to
   justify the implementation. Do not install a new finite-state toolkit solely
   to re-express a rule that has not shown a gain.

SMT is deferred: local overlays are sparse, selective token edits rather than a
large aligned sentence corpus. The papers support testing it when aligned data
exists; they do not establish a benefit from training it on these few examples.
Reverse-rendered glyph similarity is also deferred because font mismatch can
produce misleading support and the pipeline already has actual source crops.

## Non-LLM neural reserve

[Lyu et al., 2021](papers/lyu-et-al-2021.pdf)
([primary source](https://aclanthology.org/2021.tacl-1.29/)) combines recurrent and
convolutional encoders, attention and a correction-weighted loss. Its more than
89% relative WER reduction from 32.3% belongs to an aligned historical German
book corpus. It is a relevant future baseline, not evidence that arbitrary
literary scans can be freely rewritten.

[Ramirez-Orta et al.](papers/ramirez-orta-et-al-2021.pdf)
([primary source](https://arxiv.org/abs/2109.06264)) handles long documents with
overlapping character windows and weighted voting of seq2seq outputs. “Large
ensemble” describes repeated window predictions, not necessarily many separately
trained networks. It reports strongest results in five of nine ICDAR languages
and provides code. Training used a V100 GPU; the local operational tradeoff has
not been benchmarked here.

Both papers are archived but no neural correction was run. Any future comparison
must use the same held-out source-faithful labels and correct-text controls as
the statistical methods.

## Validation and limits

All 453 tests pass, including directed multi-character costs, bounded rewrite
loops, original-candidate retention, unique anchored witness alignment,
character-model backoff and repeatable ridge fitting. The final evaluation was
replayed byte-for-byte. PDF identity/page counts and archive checksums were
verified. Research-only scripts are not imported by the build; production files
are unchanged, so no EPUB rebuild or baseline acceptance was needed.

The small, selected overlay dataset, manually chosen feature weights, sparse
other-book error statistics and synthetic controls limit the conclusions.
Results support better candidate recall and stronger source gates; they do not
justify deploying a trained statistical correction margin. The supplied PoCoTo paper completes the requested full-text collection; its
Java application and profiling service have not been executed.

## PoCoTo: completed paper and repository review

The supplied [five-page paper](papers/PoCoTo-2014.pdf) cites
[`thorstenv/PoCoTo`](https://github.com/thorstenv/PoCoTo) and
[`thorstenv.github.io/PoCoTo`](https://thorstenv.github.io/PoCoTo/) in its page-5
footnote. Both direct development to
[`cisocrgroup/PoCoTo`](https://github.com/cisocrgroup/PoCoTo). Selected successor
source files were inspected at commit
`737b4055ee9e38c5ea681c9a25c6d6b0944b787a`, not assumed identical to the 2014 release.

### Useful methods

The paper separates two channels: modern spelling → printed historical spelling
→ observed OCR. A proposed repair should recover the printed historical form,
not automatically emit the modern form. Document-specific expectation-maximization
profiling estimates error types and spelling-variation patterns without ground
truth, so its interpretations remain hypotheses requiring review.

For repeated errors, it presents concordances containing every occurrence's
context, image crop and proposed correction. Reviewers can exclude members and
change individual suggestions. It supports identical strings, shared error
patterns across different words, and high-confidence candidate groups. Corrected
state is retained so an occurrence need not be reviewed repeatedly.

The library experiments measured human corrections per minute over 90-minute
sessions after short training. Reported full/base rates were 7.5/3.8 and 6.4/4.0
at BSB, and 9.8/6.1 in one Alicante experiment. The first BSB full-mode average
excludes a user who explored the tool rather than correcting. The second Alicante experiment
showed no significant gain, linked to profile quality and limited batch use.
These are interactive productivity results on selected documents, not automatic
correction precision or guaranteed savings for our pipeline.

### Source confirmation

The successor implements a Java/NetBeans client. In the inspected revision:

- [`ConcordanceTopComponent.Corrector`](https://github.com/cisocrgroup/PoCoTo/blob/737b4055ee9e38c5ea681c9a25c6d6b0944b787a/Concordance/src/jav/concordance/view/ConcordanceTopComponent.java#L695)
  collects selected, uncorrected token IDs with their individual candidate strings.
  It does not blindly replace every occurrence of a character pattern.
- [`Pattern`](https://github.com/cisocrgroup/PoCoTo/blob/737b4055ee9e38c5ea681c9a25c6d6b0944b787a/CorrectionBackend/src/jav/correctionBackend/Pattern.java)
  and `PatternOccurrence` retain pattern IDs, source strings, suggestions,
  occurrence counts and corrected counts.
- [`MainController`](https://github.com/cisocrgroup/PoCoTo/blob/737b4055ee9e38c5ea681c9a25c6d6b0944b787a/MainControl/src/jav/gui/main/MainController.java)
  applies batches by token ID and records undo/event state. Profiling uses a
  configurable `ProfilerWebServiceStub`; the generated stub exposes SOAP
  operations for starting sessions and obtaining profiles.

The GUI source is therefore useful as a design reference for grouping, selection
and correction bookkeeping. Running its background statistical profiler requires
separate service/configuration and linguistic resources. The Java client was
not built, and no profiling service was called or tested. No book data was sent
through that service. Source-file hashes are recorded in
[`pocoto-repository.json`](pocoto-repository.json).

### Retrospective grouping evaluation

```sh
.venv/bin/python scripts/pocoto_research.py \
  --input docs/statistical-ocr-evaluation.json \
  --output work/ocr-literature/pocoto-groups.json
```

On the existing 91 reviewed word pairs, same-book single-error transformations
produce **12 repeated groups covering 28 pairs**. Examples are Pharmako's
`f→j` group (`fames/fean/fohn → James/Jean/John`) and Wizard's `k→x`
(`Kuma/Kumu/Kano → Xuma/Xumu/Xano`). The benchmark lowercases these forms for
analysis; actual review must retain their printed capitalization.

This is oracle grouping derived from known corrections, not a new detector's
precision or measured time saved. The other cases include singletons and
multiple-error pairs. Looking only for the same observed substring would expose
**62 of the 90 correct controls** to a naive rewrite, including epsilon-insertion
patterns. PoCoTo's reviewed member selection is precisely what a blanket rewrite
omits. [`pocoto-grouping-evaluation.json`](pocoto-grouping-evaluation.json) records
members, control exposure and input/helper fingerprints.

The evaluation supports an offline group view in the existing review sheet:
group proposals by directed error type, show all source crops, allow per-member
exclusion, and export explicit occurrence-checked edits. Historical spelling
hypotheses remain separate from OCR corrections. Group statistics prioritize
review without granting permission to rewrite all matching strings.

### Implemented review improvements

The shared pipeline now passes its source-bound OCR cache directory to
`candidates.py`. `review_candidates.py` collects alphabetic book vocabulary only
where all three cached engines agree at aligned token positions with at least
two neighboring anchors. Targets need occurrences on two distinct pages outside
all pages containing the suspect spelling. Their counts, literal forms, page
attestations and cache hashes remain in the candidate evidence. This is a
conservative proposal resource; correlated OCR errors can still recur.

Two non-primary cached engines can additionally propose the same local
character or spacing change, bound by at least two neighboring words. This can
surface valid-word errors that unknown-word spell checking misses. Missing
peers, punctuation/compound changes, case-only differences, references/indexes
and ambiguous mappings abstain. Neither stage calls a recognition service or
changes the automatic acceptance policy.

`proofreading-review.html` now displays every occurrence, grouped by the
proposed directed character/spacing pattern. Each member retains its literal
case, context, crop, OCR readings and proposal evidence. A reviewer can exclude
members, choose different replacements, keep historical spellings, or record a
printed typo separately. A group decision requires the explicit assertion that
every selected crop was checked. Replacement changes invalidate the previous
acknowledgement. Browser storage uses source/model/profile fingerprints and a
new occurrence-level key, so legacy word-group decisions cannot silently fan
out to unseen occurrences.

`review_edits.py` validates version-2 exports and emits exact proposed config
overlays. It checks source/model/profile hashes, exact row text hashes, geometry,
positions, human acknowledgements, overlap and uniqueness. Multiple edits in a
line are scoped and rebased from right to left. It runs the existing checked-edit
and inline-offset machinery on copies of the current rows. OCR and editorial
exports are separate. A missing single-row binding disables correction export
for that occurrence. No profile or EPUB is modified by an export.

The overlays must be merged in their emitted order and validated in a rebuild.
A later automatic repair may mean the reviewed row differs from the earlier
manual-overlay phase; reconstruction can also normalize a line differently at
the editorial phase. Those build preconditions fail rather than allowing a
broader replacement. This is an explicit integration limitation, not a license
to weaken source binding.

The refreshed Pharmako review contains 1,867 occurrences in 845 proposed-pattern
groups. Its vocabulary resource contains 4,840 terms; 338 word groups have
witnessed-vocabulary suggestions and 70 occurrences have aligned two-peer
proposals. These are counts of review opportunities, **not confirmed errors,
precision or time saved**. The sheet is self-contained (about 47 MB), deduplicates
crop assets and uses lazy image display. Runtime JavaScript control tests cover
member exclusions, acknowledgements and blocked unchecked exports; native PDF
fixtures cover complete occurrence rendering and shared crop assets. Actual
browser rendering/download smoke tests were blocked by Chrome's local-file URL
policy and remain unverified.

Run the workflow checks with:

```sh
.venv/bin/python -m unittest discover -s tests
node tests/test_proofread_review.js
```

The JavaScript test uses a DOM double; it does not establish actual browser
layout, image loading, download behavior or reading-device compatibility.

The final validation passed 471 Python tests and the JavaScript control tests.
Seven earlier books passed candidate-generation smoke checks, including Russian
and native-text input. All 17 cached build cases had identical complete
regression snapshots and correction audits against a fresh `4acc3b7` worktree
using the same caches. The older stored baseline was not accepted or changed.
[Review-workflow validation](review-workflow-evaluation.json) records per-case
hashes, source coverage, link/figure counts, candidate coverage and helper
fingerprints.
