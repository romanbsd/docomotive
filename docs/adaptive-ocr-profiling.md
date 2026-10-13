# Adaptive document profiling and review-efficiency research

The next useful target is candidate coverage and review effort. Nine of the ten
new leads are available as full-text PDFs, including the user-supplied
`reffle2013.pdf` and `reffle2011.pdf`; only Xu & Smith remains unavailable. Their
relevant methods, evaluation sections and limitations were inspected. No complete
paper system was reproduced, no profiling service was called, and no production
repair policy, profile, EPUB or regression baseline changed.

## What the new literature changes

### Fink, Schulz and Springmann: separate precision from recall

[Profiling of OCR'ed Historical Texts Revisited](papers/fink-schulz-springmann-2017.pdf)
([2017 author preprint](https://arxiv.org/abs/1701.05377)) is the closest
architectural match. Its two channels distinguish modern lexical forms, actual
printed historical forms and observed OCR. Human feedback constrains the printed
form; an acknowledgement that the observed spelling was already correct is also
feedback. Unknown confirmed forms enter the historical lexicon. Four EM rounds
re-estimate lexical, historical-pattern and OCR-error distributions.

The paper's three interventions have different effects:

- **Adaptive feedback primarily removes false positives.** Recall stays almost
  constant, with a slight decrease. It does not demonstrate that learning more
  OCR substitutions alone discovers many additional errors.
- **Including uninterpretable tokens expands the detector.** These tokens need
  review even when the model cannot suggest an interpretation. The baseline
  ignores them; the revised decision mechanism does not.
- **Expanded historical patterns distinguish printed variants from OCR errors.**
  They can explain valid forms and also make previously unreachable candidates
  available. The patterns depend on language, period and transcription policy.

The evaluation uses two German Gothic printings, each with about 5,000 tokens.
Feedback comes from the initial 20% of each text, with evaluation on the unseen
80%. It compares full correction with correction of profiler-selected tokens;
these yielded similar adaptive gains. Baseline Table 1 for the 1557 text improves
precision from 0.456 to 0.469 and *fair* recall from 0.825 to 0.871 when enlarged
patterns and uninterpretable tokens are combined. Fair recall excludes tokens
outside the profiler's alphabetic, length-greater-than-three scope. It is not
whole-book recall. Short words, splits/merges and dictionary-valid wrong words
remain substantial limitations. Reported reachable word accuracies assume humans
correct all detected errors; they are not automatic correction results.

For our pipeline, test three independent resources: confirmed spellings, confirmed
unchanged historical/domain forms, and an explicit unresolved-token queue. Do not
infer that an unexplained word is wrong or silently modernize its spelling.

### Reffle and Ringlstetter 2013: document profiles, with explicit failure modes

The supplied [journal paper](papers/reffle2013.pdf)
([DOI](https://doi.org/10.1016/j.patcog.2012.10.002)) was read in full, including
its profile, ranking and human-study evaluations. It supersedes the abstract-only
entry. The twelve-page PDF is the published 2013 article, available online in 2012.

Its EM-style procedure alternates token interpretations with document-level
estimates of words, historical patterns and OCR errors. Every interpretation
contributes its probability share; it does not simply train on the single best
replacement. Error/pattern probabilities divide expected applications by expected
opportunities, including places where a pattern could have applied but did not.
Low-support OCR patterns are dropped unless they appear in the top interpretation
of at least two document words. Most models stabilize within six iterations.

The candidate interpretation still assumes restricted resources and bounded
errors. Exact dictionary matches are treated as correct and receive no OCR-error
trace. Approximate lookup follows only when exact lookup fails. Word segmentation,
merges and splits are excluded from the aligned token analysis. Consequently,
this method does not replace our anchored peer checks for real-word or spacing
errors; it has no calibrated abstention guarantee for correct text.

Evaluation uses two manually interpreted gold documents (about 14,400 and
4,300 tokens), plus 15 silver documents totaling about 90,000 tokens across
three centuries. Resources include over two million modern German forms,
140 historical patterns, substantial historical lexicons and separate Latin/name
lexicons. These resources are materially richer than our selected correction
pairs. Estimated/true error-distribution correlations range from 0.39 to 0.94.
One weak document misclassifies historical vowel patterns; another treats two
recurring character names as errors, creating many false positives. The authors
explicitly recommend adding those names to lexical resources. Sparse repeated
errors in short documents make estimates less stable.

Ranking is evaluated **only on correctable errors whose correct target is already
in the candidate list** (about 62 candidates per token on average). Its per-century
mean top-1 scores are 36.8→56.0%, 49.6→69.8% and 56.1→70.9% compared with the
baseline, gains of 19.2, 20.2 and 14.8 percentage points. These are conditional
ranking results, not candidate-recall or end-to-end correction measurements.
Our negative weighted-edit experiment does not reproduce or disprove this
unsupervised two-channel method; conversely, its ranking gains do not justify
promoting our existing weights.

The interactive study used seven users per book in 90-minute sessions, saving
states every ten minutes. Experience and instruction mattered strongly: full-mode
users included interface-exploration and inadequate-instruction outliers. On the
more systematic-error book, two novices averaged 43.9 corrections per ten minutes,
while two experienced full-mode users averaged 87, described as 2.7 times the
baseline. This supports our timed-review hypothesis and training requirement,
without supplying a transferable speed-up factor for our interface.

Useful next components are document-level error summaries with support and seed
provenance, explicit confirmed-name/domain lexicons, and evaluation of candidate
coverage separately from conditional ranking. Keep inferred historical/OCR
classifications as hypotheses until checked against the scan.

### Reffle 2011: virtual historical candidate generation

The supplied [journal paper](papers/reffle2011.pdf)
([DOI](https://doi.org/10.1017/S1351324911000039)) was read in full. Its nineteen-page
copy contains a publisher cover plus the eighteen-page article. This is a
candidate-generation algorithm, not a replacement decision model.

Given a modern lexicon, reviewed historical rewrite patterns and an OCR edit
radius, it generates possible **historical printed forms**, preserving distinct
historical and OCR traces. Historical patterns apply in parallel to nonoverlapping
parts of the modern form; their outputs are not recursively rewritten. The
algorithm combines a finite-state lexicon, an Aho–Corasick pattern structure and
universal Levenshtein automata. Historical transitions are virtual: it searches
plausible variants without materializing the complete hypothetical lexicon.
Forward/backward dictionaries further narrow the error-tolerant search.

This addresses a different problem from our failed learned OCR rewrite expansion:
it can admit a valid historical form outside the base lexicon. However, it requires
validated historical patterns and a suitable modern ancestor for that form. It
cannot manufacture an unattested name or determine which proposed variant was
printed. The output traces are especially useful for auditable review proposals.

Evaluation uses 2,336,165 German forms, 140 linguist-selected patterns and randomly
injected edits in corpus-derived words. It measures candidate counts and speed,
not review accuracy, false positives or recall on natural OCR errors. Unrestricted
pattern use with edit radius two averages about 626 candidates; bounding patterns
to two reduces that to about 348. Table 5's backward-filtered mixed-length queries
average about 0.915 ms at radius one and 17.247 ms at radius two with unrestricted
patterns on the stated 1.8 GHz test CPU. Those are paper-specific timings, not our
runtime measurements, and fast generation does not imply an efficient human
review shortlist.

A small resource-based experiment is appropriate only after classifying our
missing targets. First compare candidates from a validated language/period pattern
set with confirmed literal spellings, logging ancestor, historical trace, OCR trace,
candidate count and new answer coverage. Do not build the full finite-state stack
before demonstrating that historical-variant coverage is an actual local gap.
The present English misses are often names, accents and specialist terminology;
no suitable historical pattern resource has been validated for them. Neither this
algorithm nor the 2013 EM procedure was executed in this work.

### CIS OCR Profiler implementation

The [public implementation](https://github.com/cisocrgroup/Profiler) was inspected
at revision `59bd0f21141a26ea36aea24cce2b4aefa89dc02a`. The inspection covers its
README, `Profiler/AdaptiveProfiling.cxx`, `DictSearch/AdaptiveLex.cxx`,
`Profiler/Profiler.h` and the official manual; it is not a complete code audit.
[`cis-profiler-review.json`](cis-profiler-review.json) records URLs, file hashes
and scope. The manual was retrieved from its moving master branch and is bound
by its content hash, rather than asserted to belong to the Profiler revision.

The adaptive branch is activated by human correction metadata. It restricts
candidate interpretations to the correction, and can construct a candidate and
add its spelling to an adaptive lexicon when the ordinary dictionaries cannot
explain it. The adaptive lexicon subsequently matches other observed tokens
within a configured Levenshtein radius. These inspected mechanisms support
reusing confirmed rare terms as candidates; their presence does not establish
candidate correctness or full EM behavior.

The manual describes compiled modern dictionaries, historical patterns,
frequency resources and XML-oriented input/output. Its native build requires
CMake, C++, Xerces, ICU and related tooling; web-service deployment adds other
components. We did not compile it, install language resources or run its service.
A lightweight local feedback experiment is justified before adopting that stack.

## Other leads: verified relevance and boundaries

| Paper | Useful direction | Qualification |
|---|---|---|
| [Reffle & Ringlstetter, 2013](papers/reffle2013.pdf) | Global/local two-channel document profiles | Full user-supplied journal PDF reviewed. EM estimates distributions; ranking evaluates only errors already covered by candidates. Exact lexical matches bypass error interpretation, and names/short documents cause failures. |
| [Abdulkader & Casey, 2009](papers/abdulkader-casey-2009.pdf) ([Google Research](https://research.google/pubs/low-cost-correction-of-ocr-errors-using-learning-in-a-multi-engine-environment/)) | Error detection, word-image clustering, keyboard-efficient review and feedback between batches | Trains a small neural estimator on about 200,000 labeled suspects. Cost uses questions per word and measured answering speed. Clusters require equal OCR strings **and image similarity**; our character-pattern groups are a different grouping mechanism. Do not propagate a representative correction to our entire group. |
| [Reffle, 2011](papers/reffle2011.pdf) | Candidate generation across historical variants | Full user-supplied journal PDF reviewed. Virtual variant lexicon, pattern matching and Levenshtein pruning avoid exhaustive expansion. Evaluation measures speed/candidate counts on injected errors, not source-faithful correction precision. |
| [Xu & Smith, 2017](https://doi.org/10.1109/JCDL.2017.7991587) | Repeated-passage evidence | Full text unavailable; author's publication list confirms the paper but supplies no PDF link for this entry. No algorithm, evaluation or code-reuse claim is made from other papers' citations. |
| [Poncelas et al., 2020](papers/poncelas-et-al-2020.pdf) ([ACL](https://aclanthology.org/2020.lt4hala-1.7/)) | Vocabulary-based proposals with transparent interventions | Reports roughly 63% correction on one chapter, but explicitly shows wrong replacements, including Deputy-Governor to Ex-Governor. Dictionary plus language-model replacement offers little justification beyond our existing experiments; useful as a review interface reference. |
| [Suissa et al., 2020](papers/suissa-et-al-2020.pdf) ([2021 manuscript](https://arxiv.org/abs/2106.06831)) | Compare task length, source images and Find/Fix stages | 753 crowd workers; **artificially injected errors** in a 1914 text. Paragraph/image/two-stage configurations favor accuracy; longer single-stage/no-image configurations favor efficiency. The latter does not justify removing our scan requirement. Test transfer on real book OCR and source-faithful labels. |
| [Fischer & Puppe, 2026](papers/fischer-puppe-2026.pdf) ([ACL](https://aclanthology.org/2026.lrec-1.559/)) | Integrated scan/transcription view, dictionaries, PAGE structure | AdaptOCR's Latin dictionary experiment and small interface pilot are promising, but compare different page sets and are not a broad randomized trial. Its handwriting application uses Gemini; that route is outside our non-LLM correction research. |
| [Packer, 2011](papers/packer-2011.pdf) ([citation](https://doi.org/10.1145/2037342.2037354)) | Joint HMM information extraction and error detection | Two family-history books with annotated semi-structured records and domain fields. Lower priority for continuous literary prose; potential fit only when a structured-book corpus and labels exist. |
| [Springmann et al., 2014](papers/springmann-et-al-2014-latin.pdf) ([citation](https://doi.org/10.1145/2595188.2595205)) | Language-specific historical lexicons and font adaptation | Valuable for Latin printings, but our current experiment is English and lacks Latin/historical-variant labels. Do not transplant language-specific spelling rules. |

AdaptOCR Table 3 distinguishes an automatically evaluated 177,942-word corpus
from two manually corrected 12-page samples. In the latter, dictionary-selected
review took 16m25s versus 48m06s for full review, with different page sets and
initial WERs. Table 4 is a separate interface comparison, without dictionary
support for its printed-text experiment: 40.0 versus 53.5 seconds per edited
word. The handwriting comparison is 16.2 versus 20.5 seconds. These results
support testing presentation and dictionaries; they do not establish our savings.

## Offline adaptive-feedback feasibility experiment

Run:

```sh
.venv/bin/python scripts/adaptive_profile_research.py \
  --input docs/statistical-ocr-evaluation.json \
  --output work/adaptive-ocr-literature/evaluation.json
.venv/bin/python -m unittest discover -s tests -p test_adaptive_profile_research.py
```

The frozen input is the existing 91 source-reviewed error cases and 90 paired
correct-text controls across five English books. This selected corpus is not
representative of all book tokens, and these source crops were not freshly
re-reviewed. Its baseline is the original general + witnessed-book candidate
pool, whose answer coverage is 70/91. The benchmark pages were excluded from
that baseline's raw book vocabulary and context priors.

The experiment adds only same-book feedback:

1. Earlier confirmed spellings form an adaptive lexicon, queried at unit edit
   radius two, including rare forms absent from general dictionaries.
2. A confirmed correction with exactly one changed chunk of at most three
   characters supplies a literal rewrite. It is applied once per proposed
   alternative and screened through pinned English Hunspell. Insertions and
   deletions are finite; multi-chunk changes cannot masquerade as a single rule.
3. The original remains available, and every added alternative records the seed
   correction and generator. No ranker chooses a replacement; no book is edited.

The forward splits use the first 20% or 50% of **annotated error pages**, not
20% or 50% of the complete book or tokens. All reviewed errors on seed pages are
provided as feedback; held-out pages supply both errors and paired controls.
No held-out labels, other books or controls train the adaptive resources.
A leave-one-page-out diagnostic supplies all other pages as feedback, including
future pages; it is an optimistic retrospective comparison, not online behavior.
The existing baseline already uses other-book labels for its earlier research
components; this experiment adds no cross-book feedback. It cannot replace a
fully held-out-book detector/calibration study.

| Feedback split | Held-out errors | Baseline answers present | Additional correct candidates | Correct controls with an alternative / evaluated |
|---|---:|---:|---:|---:|
| First 20% of annotated error pages | 72 | 58 | 1 | 5 / 71 |
| First 50% of annotated error pages | 45 | 35 | 2 | 10 / 45 |
| Every other page, retrospective | 91 | 70 | 3 | 35 / 90 |

The forward gain at 20% is `juxibo → iuxibo`, using the earlier confirmed
`iuaibo → iuxibo` spelling. At 50%, a learned `c→e` pattern also proposes
`con → eon`. The retrospective diagnostic gains the reverse rare-word occurrence
as well. Two of its three new answers therefore come from one recurring rare
spelling, not broad error-model generalization.

Control exposure means a correct form acquired a competing proposal. It is not
an applied corruption, and does not establish a corpus-wide false-positive rate.
Nor is the presence of a correct candidate evidence that a detector would find
that occurrence or a human would choose it. The experiment does not reproduce
EM, historical-channel learning, reviewed-unchanged feedback or calibrated
error probabilities. Its full splits and per-case evidence are in
[`adaptive-profile-evaluation.json`](adaptive-profile-evaluation.json).

## Next experiments and decision

**H1 should distinguish suppression from discovery.** First test confirmed
unchanged forms as a way to avoid recurring false alarms, while retaining
occurrence-specific OCR disagreement for real-word errors. Separately test an
unexplained-token queue without requiring a replacement. Candidate-generating
patterns can remain a secondary review source; this experiment found modest
coverage gain and appreciable correct-control exposure. Do not deploy a broad
pattern fan-out or another replacement threshold from these results.

**H2 needs prospective timing.** Compare individual occurrences, grouped crops,
and grouped crops with paragraph context on balanced real OCR samples. Randomize
or counterbalance assignment and use different comparable passages to prevent
learning the answers from one condition. Measure active time, correct errors
resolved, missed errors, erroneous edits, uncertainty, group-selection overhead
and any scan-check shortcuts. Each selected occurrence must still be checked.
Offline group size and ordering scores are hypotheses, not measured time saved.
No human timing study was conducted in this work.

**H3 targets the 21 currently uncovered answers.** Many are accents,
transliterations, names or specialized terms rather than known English historical
variants. Label that distinction before expanding historical patterns. Compare
confirmed literal forms, accent-preserving lexical resources and validated
language/period-specific variants, one at a time. Reserve complete books for
transfer evaluation and independent correct-text data for queue-budget
calibration. The Reffle full texts are now reviewed; validated historical resources and
matching test labels are still needed before implementing their specific machinery.

Repeated-passage matching remains pending full-text retrieval and a corpus
redundancy check. A parallel edition can differ legitimately; another passage
is evidence for a proposal, never permission to overwrite the printed version.

## Validation and retrieval limits

The experiment replays identically, retains the original candidate and binds its
input, helpers and dictionary files by SHA-256. Four tests guard same-page,
other-book and correct-control feedback leakage, future feedback, rare confirmed
forms and multi-chunk filtering. All 475 Python tests pass. All 19 archived
paper checksums were verified. No production source changed, so no EPUB rebuild
or regression-baseline acceptance was needed.

Full text still needed:

- Xu & Smith (2017), *Retrieving and Combining Repeated Passages to Improve OCR*.

Publisher, institutional and author-publication searches did not yield accessible
a full-text copy for this entry. Metadata/abstract access is explicitly
separate from a completed paper review. PDFs supplied later should be archived
with their provenance and checked against these pending questions.
