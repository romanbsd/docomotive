# OCR improvement priorities: simple methods first

Implementation follow-through: [production-rule benchmark and local review timing](production-review-evaluation.md)
now provide the foundation for the first two priorities. The 62/91 admitted-answer
result measures stricter rules than the broad pools below; queue ordering remains
unchanged pending prospective review data. Later stages remain conditional.

[The confirmed-form ledger](review-feedback.md) is implemented behind an explicit
option. Its forward-page test adds zero answers to the production proposal pool
and lowers no priorities; it remains experimental and is not enabled by default.

Research and offline experiments, 2026-10-13. **Prioritize measuring the existing
review paths, then document-specific feedback and review ordering.** Broader fuzzy
matching offers little unique coverage in the current sample. No production
repair, book profile, output EPUB or regression baseline changed in this work.

## What we researched

The [initial review](classical-ocr-correction.md) and
[adaptive follow-up](adaptive-ocr-profiling.md) cover the original shortlist and
the supplied Reffle papers. This round adds four full texts to the
[23-paper archive](papers/README.md). Relevant methods and evaluation sections
were inspected; individual components were tested, not complete paper systems.

| Source | Finding and qualification | Application here |
|---|---|---|
| [Furrer & Volk 2011](https://aclanthology.org/W11-4115/) | Corpus-derived lexicon, common substitutions, then shared-trigram retrieval. Recurrent errors can outnumber correct rare forms. Their specialized German administrative corpus improves word accuracy 96.72%→98.36% on about 35,000 manually corrected words; not evidence for any one component alone. | Test bounded shared-trigram retrieval and raw recurrent vocabulary separately; recurrence is a proposal signal, not spelling proof. |
| [Reynaert 2016](https://aclanthology.org/L16-1154/) | TICCL's earlier single-book scores (about 71% recall, 99.8% precision) fall to about 35% recall and 84% precision in Table 4 on 1,020 sampled proposed word-type corrections from a 10,333-book collection. These are different evaluation populations, not a controlled model regression. Splits/merges and foreign passages account for many failures. | Audit candidate recall and sample representativeness before improving ranking. Keep spacing errors and foreign vocabulary in separate strata. The paper's acceptance of contemporary normalizations is broader than our source-faithful policy. |
| [Smith 2011](https://research.google/pubs/limits-on-the-application-of-frequency-based-language-models-to-ocr/) | A simplified classifier simulation using Google Books n-grams shows the trade-off between correcting errors and corrupting valid rare words. Not a benchmark of a deployable post-corrector. | Preserve originals, separate detection from ranking, and demand classifier/source evidence; do not promote a more frequent alternative merely because it wins a score. |
| [Neudecker et al. 2021](https://primaresearch.org/publications/HIP21_CNeudecker_OcrEvalSurvey) | CER/WER implementations differ through alignment, reading order and Unicode normalization; OCR confidence is not reliable accuracy by itself. | Pin normalization and tokenization, retain evidence hashes, report candidate availability separately from whole-page quality and review time. |

The existing literature remains relevant: PoCoTo motivates concordances and
explicit grouped decisions; Abdulkader & Casey motivates review selection and
clustering; Fink et al. motivates confirmed-unchanged feedback and unresolved
tokens; Reffle 2013 separates historical forms from OCR traces and estimates
document profiles; Reffle 2011 supplies a resource-dependent historical candidate
search. None establishes that unrestricted automatic replacement is safe here.

[Unicode UAX #15](https://www.unicode.org/reports/tr15/) distinguishes canonical
normalization from compatibility normalization. The tested accent key decomposes
characters and removes marks **only for retrieval**. This is lossy, unlike NFC;
every suggestion remains a literal vocabulary spelling. Do not change book text,
offsets, source matching or review fingerprints using that key.

## Reproducible experiment

```sh
.venv/bin/python scripts/simple_ocr_research.py \
  --output work/simple-ocr-research/evaluation.json
.venv/bin/python -m unittest discover -s tests -p test_simple_ocr_research.py
```

[Frozen results](simple-ocr-evaluation.json) include literal alternatives, gained
case IDs, per-book queues, residual diagnostics and evidence fingerprints. The
script fails if frozen cases, source/profile/overlay/protected-word/dictionary
hashes, OCR caches, language resources, baseline helpers or package versions drift.
It performs no book edits and no network calls.

The data are **91 previously source-reviewed single-token errors and 90 paired
counterfactual correct controls across five English books**. All annotated pages
are removed before building book vocabulary and corpus counts. Test targets are
used only for scoring and explicitly labeled residual diagnostics, not retrieval
or queue ordering. These are selected errors, not random book tokens. Existing
spacing rules are outside this sample.

Important boundaries:

- The baseline research pool has 70/91 answers. Its recurrent book bank uses
  tokens shared by three roughly matched OCR rows and at least two occurrences.
  Production `ReviewWitnesses.collect` demands aligned tokens and distinct-page
  evidence. This bank is **not** the stronger production bank; the results do not
  establish production candidate recall.
- Controls replace the error with its correct text but retain the original cached
  peer readings. Their disagreements are counterfactual; alternatives on controls
  measure exposure, not detector precision or expected false alarms in a book.
- The prior pool did not explicitly inject peer strings. Production
  `ReviewWitnesses.disagreements` already does so, and `scripts/candidates.py`
  merges that path when `--work` is supplied. Four answers added by this ablation
  are a **benchmark coverage gap**, not four demonstrated new pipeline fixes.
- Candidate recall does not mean correct ranking, source authorization or edits.
  We did not measure reader speed or conduct a prospective user study.

## Candidate results

Every method is added to the frozen 70/91 pool for the coverage comparison.
Alternatives are method outputs, including candidates that may already exist in
the baseline; they are not counts of strictly incremental suggestions. The
original remains available. No confidence threshold was fitted.

| Method | Additional answers | Combined coverage | Controls with alternatives /90 | Largest method pool |
|---|---:|---:|---:|---:|
| Two equal cached peer readings | 4 | 74/91 | 17 | 1 |
| Raw book forms occurring at least twice, radius 2 | 1 | 71/91 | 71 | 114 |
| Witnessed shared trigrams, literal edit distance ≤4, top 10, length ≥6 | 1 | 71/91 | 39 | 10 |
| Raw recurrent shared trigrams, same bounds | 1 | 71/91 | 39 | 10 |
| Witnessed radius 3 | 0 | 70/91 | 83 | 579 |
| Accent-key witnessed lookup, radius 1 | 0 | 70/91 | 41 | 11 |
| Accent lookup + peers | 4 | 74/91 | 50 | 12 |
| Raw recurrent radius 2 + peers | 5 | 75/91 | 75 | 115 |

The peer additions are `yetyersity → university`, `nizpo → nixpo`,
`iuaibo → iuxibo`, and `juxibo → iuxibo`. Of 30 actual error cases with an equal
non-original peer suggestion, 29 match the reviewed answer and one does not.
This selected-case result is not whole-book precision; correlated engine errors
remain a reason for scan review.

Trigrams retrieve `university`, at a distance beyond radius 3, but contribute no
answer beyond peers. Raw recurrence contributes `mapia → mapiá`; both peers at
that location read `mapia`. It adds one answer at a large exposure cost and still
requires independent scan evidence. Accent lookup contributes none because the
missing literal target forms are mostly absent from the witnessed bank.

Of the 21 baseline-uncovered answers, just **one** occurs in the witnessed bank,
**two** in the raw recurrent bank, and **nine** have diacritics. Only three are
recognized by the English dictionary, while one erroneous original is itself a
dictionary word. Wider search of the same bank cannot recover absent spellings.
These overlapping diagnostic categories are not automatic language labels.

## Review and feedback results

Two label-free deterministic orders use dictionary unknownness and equal-peer
disagreement, with source book/page/ID tie breaks. They do not learn weights or
inspect the correction target.

| Review slots | Unknown first, peers second: errors found | Peers first, unknown second: errors found |
|---|---:|---:|
| 10 | 8 | 8 |
| 25 | 20 | 20 |
| 50 | 37 | 32 |
| 100 | 70 | 68 |

The selected queue is roughly half errors and half synthetic controls. The first
50 result favors unknown-first here, not a globally optimal review order. It does
not compare the current production group queue, account for group sizes or
source-crop inspection time, or establish errors per minute. Alphabetical book
tie breaks also affect the aggregate result; per-book orders are saved for audit.

Simulating confirmed-unchanged forms from the first 20% of **annotated pages**
suppresses two later synthetic controls and no known error among 72 held-out
errors and 71 controls. Two-peer disagreement vetoes suppression. This tiny,
optimistic result only justifies a prospective deprioritization experiment.
Never silently remove all occurrences of a reviewed spelling: a valid word can
be the wrong word in another passage.

The [earlier forward-feedback test](adaptive-ocr-profiling.md) adds one answer
among 72 later errors using confirmed spellings/short transformations, with five
control exposures. That does not establish an EM profiler's utility; the complete
EM and historical finite-state methods remain untested.

## Implementation order and gates

Estimates below are relative engineering scope, excluding annotation time.
Implement one stage, freeze its inputs, then decide from held-out evidence.

| Priority | Work | Scope | Concrete evaluation / promotion gate |
|---|---|---|---|
| 0 | Bring the benchmark into agreement with existing production proposals | Small | Exercise existing `ReviewWitnesses` on unrepaired, source-bound models; exclude held-out pages from vocabulary; use the production alignment and distinct-page rules. Record per-generator recall, unique gains, abstentions and full candidate lists. Do not add a second peer generator. |
| 1 | Measure existing review queues and expose unresolved items clearly | Small | Audit that missing suggestions do not hide unknown/disputed tokens. Instrument open/decision times and group/occurrence counts locally. Compare current ordering, unknown-first, peer-first and group-yield ordering using fresh source-labeled samples. Preserve all items and individual decisions. |
| 2 | Book-local confirmed spelling and unchanged-form ledger | Small–medium | Reuse explicit review decisions with source fingerprints; corrected literal forms may seed suggestions on later pages. Unchanged forms may only lower priority; fresh conflicting OCR/source evidence restores priority. Test cross-book/page leakage, revoked decisions, stale sources, valid-word errors and accents. Require unique held-out candidate gains or measured noise reduction without lost known errors. |
| 3 | Paragraph context and group-yield ordering | Medium | Render source/context using current review evidence. Prioritize by audited support and unresolved occurrence count, without auto-accepting a group. Conduct timed counterbalanced sessions; score source-correct decisions per minute, errors per reviewed occurrence and incorrect decisions. Group counts alone are insufficient. |
| 4 | Targeted multilingual/domain lexical resources | Medium | Annotate which uncovered answers are names, foreign words, diacritics or technical terms. Add reviewed literal forms or pinned suitable resources only for the relevant language/period. Test candidate coverage on new books and correct rare-word controls; preserve the printed spelling. No automatic English correction of foreign passages. |
| 5 | Repeated passage evidence | Medium–large | First measure near-duplicate passage frequency. If sufficient, use local shingles to retrieve passages, then anchored alignment to suggest disagreements. Require context and page provenance, unique candidate gain and no conflation of different quotations/editions. Obtain Xu & Smith full text before claiming its method was reviewed. |
| 6 | Full two-channel profiling / historical finite-state search | Large | Proceed only after gathering sufficient literal/historical labels, supported language resources and learning curves. Keep historical variants separate from OCR corruptions. Evaluate detection recall, candidate recall, conditional ranking and preservation independently; begin as advisory proposals. |

For a prospective pilot, choose at least three **previously unused books**, with
whole pages assigned to calibration or held-out evaluation. Include randomly
sampled ordinary text and separately sampled names, foreign passages, diacritics,
real-word errors and spacing cases; retain each stratum's denominator. Source-
label all selected occurrences, including correct text and detector misses.
Do not build the vocabulary from held-out reviewed answers. Freeze decisions on
calibration books; report held-out per-book results, not just a pooled best score.

Pre-register a small candidate display cap and manual review budget on calibration
data. Compare recall at that cap and budget, unique additional answers, candidate
counts, correct-text exposures and abstentions. Record runtime separately from
review time. For a queue pilot, use equally sized, matched groups in randomized,
counterbalanced sessions and report session-level variability. Adopt an ordering
only if held-out source-correct decisions per minute improve without a material
increase in wrong decisions; report the confidence interval rather than infer
speed from retrospective grouping.

## What to defer

- **Radius 3 and generic accent expansion:** no new answer here; do not expand
  the production pool on this evidence.
- **Shared-trigram fallback:** cheap and bounded but its sole gain is redundant
  with existing peer evidence. Keep the experiment; revisit on genuinely
  uncovered long tokens with available targets.
- **Unfiltered raw recurrence:** one unique answer does not justify hundreds of
  unverified suggestions. A selective, scan-backed rare-form review may be useful.
- **More regression ranking, SMT or neural correction:** no new evidence that
  these address absent target forms or reduce review cost. Aligned training data
  and representative controls are prerequisites, not optional polish.
- **Packer's structured-record HMM and Latin-specific resources:** useful for their
  documented layouts/languages, but not general defaults for these English prose
  books. AdaptOCR's handwriting LLM route is outside the deterministic scope.

Automatic replacement remains a separate future decision. Any production repair
change must honor existing scan-evidence gates and the AGENTS.md regression
procedure: isolated comparison, fresh baseline worktree, full audited output diffs
and source-image verification before accepting changed baselines.

Validation: all **483 Python tests** pass, including eight new tests guarding
literal accent output, retrieval bounds, peer agreement, answer-independent
ordering, feedback isolation and frozen-case drift. Black checks all 108 Python
files; the experiment replays byte-identically, all 23 archived PDF hashes and
sizes match the manifest, research navigation links resolve, and `git diff --check`
passes. No EPUB rebuild was run because production code and profiles did not change.
