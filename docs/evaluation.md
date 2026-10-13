# Evaluating detection, correction and review

`scripts/evaluation_metrics.py` calculates offline metrics without changing books
or accepting regression baselines. The checked-in [report](evaluation-metrics.json)
is reproducible from the frozen production-candidate experiment:

```sh
.venv/bin/python scripts/evaluation_metrics.py --output docs/evaluation-metrics.json
```

## What the existing experiment establishes

| Measurement | Calculation | Result |
|---|---|---:|
| Detection recall on selected errors | 79 / 91 | 86.8% |
| Correct answer somewhere in the candidate pool | 62 / 91 | 68.1% |
| Candidate coverage among detected errors | 62 / 79 | 78.5% |
| Stored-order Recall@1 | 50 / 91 | 54.9% |
| Stored-order Recall@3 | 59 / 91 | 64.8% |
| Stored-order Recall@5 | 60 / 91 | 65.9% |
| Case-control diagnostic precision | 79 / (79 + 21) | 79.0% |
| Case-control diagnostic F1 | 158 / (158 + 21 + 12) | 82.7% |

These are 91 selected errors and 90 counterfactual correct controls. The controls
retain the original peer OCR caches. Their confusion matrix (TP=79, FP=21,
TN=69, FN=12) cannot establish precision, F1 or false-alarm rates on naturally
correct book text. Recall@k uses the stored generator ordering, which omits
deployed contextual reranking. The fixtures are source rows rather than complete
reconstructed pages. The report leaves population metrics, confidence intervals,
page CER/WER and review efficiency unavailable for this input.

## Complete-page evaluation

Copy [annotations-template.json](evaluation/annotations-template.json), then
fill it with independently source-checked annotations. The template deliberately
fails validation until real source evidence and complete text are supplied.

```sh
.venv/bin/python scripts/evaluation_metrics.py \
  --annotations work/evaluation/annotations.json \
  --output work/evaluation/report.json
```

Sample complete pages from a predeclared population, retaining naturally correct
text. Record the sampling frame, selection procedure/seed, eligible pages,
language, OCR engines and versions, profiles and pipeline commit alongside the
dataset. Do not select pages because their corrections look promising. Keep a
separate `challenge` dataset for known difficult cases. Freeze thresholds using
`calibration` books, then score different `test` books; a book cannot cross splits.
Start with a small annotation pilot, then expand the held-out books before drawing
general conclusions. Double-check the gold against page images independently of
candidate suggestions; disagreements need adjudication.

Each page supplies its book ID, one-based page number, source-file SHA-256,
`source_checked: true`, split, and full `before`, `truth` and `output` text.
Declare `output_kind` as `automatic`, `reviewed` or `pipeline` so reviewed fixes
cannot be presented as automatic performance. The transcription policy should
define page scope, reading order, whitespace and printed typos. For source-faithful
evaluation, preserve historical spelling and printed typos in the gold.

`units` are ordered source spans using zero-based Python character offsets into
`before`, with exclusive `end`. Every non-whitespace source token must be covered
exactly once. Normally use one token per unit; use one multi-token span for a
split/fused-word error. Each unit declares its gold `truth`, actual `output`,
boolean `detected` and ordered, unique `candidates`. Undetected units have no
candidates. Zero-length spans with `missing_source_text: true` represent missing
OCR text. Unit gold/output tokens must reconstruct the full page gold/output.
Keep segmentation consistent between systems; detection metrics count spans,
whereas WER counts whitespace-delimited words.

The evaluator reports pooled counts and per-book scores:

- Detection precision = TP/(TP+FP), recall = TP/(TP+FN),
  F1 = 2TP/(2TP+FP+FN), and F0.5, which weights precision more strongly.
- False-alarm rate = FP/(FP+TN), plus false-alarm **units** per 1,000 naturally
  correct reference words. Multi-token spans make these distinct denominators.
- Recall@1/3/5 and full-pool candidate recall include detector misses in the
  denominator. Conditional coverage among detected errors is reported separately.
- Exact correction precision = fully resolved erroneous spans / changed spans;
  error-resolution recall and damage to originally correct spans are separate.
  Partial improvements and erroneous edits are captured by full-page CER/WER.
- CER/WER = Levenshtein edits / reference characters or words. NFC and newline
  normalization preserve case, accents and literal glyph distinctions. Character
  scores include spaces and punctuation. Report both before/after errors and
  paired absolute and relative improvement; negative improvement means damage.
- Optional finite `review_score` on every detected unit enables queue-prefix
  precision/recall at review budgets and fixed false-alarm budgets. Scores must
  come from the system, never from gold labels. Equal scores use unit IDs.

Undefined denominators produce `null`. Declared random-page samples receive
deterministic paired hierarchical bootstrap intervals, resampling books and then
pages within books. At least two test books and 100 replicates are required;
the default is 1,000 with seed zero. Few books still give unstable intervals.
These intervals condition on the annotated collection and declared sampling;
the script cannot verify representativeness or the source-checking declaration.
Challenge sets receive no population intervals. Full-page alignment is computed
once per page and reused during resampling.

## Timed review study

Copy [review-study-template.json](evaluation/review-study-template.json). Bind
`dataset_sha256` to the exact annotation file. Each session records a reviewer,
strategy, positive **total elapsed** milliseconds and decisions keyed by unit ID.
A replacement is the complete chosen reading; `null` denotes abstention.

```sh
.venv/bin/python scripts/evaluation_metrics.py \
  --annotations work/evaluation/annotations.json \
  --review-study work/evaluation/review-study.json \
  --output work/evaluation/report.json
```

The evaluator scores decisions against independent gold and reports resolved
errors per minute, wrong decisions, abstentions and per-strategy medians. Existing
browser active timers omit navigation and other overhead and cannot substitute
for total session time. Compare matched unseen tasks, counterbalance strategy
order across reviewers, and avoid showing the same passage twice to one reviewer.
Keep source access and task difficulty comparable. Session medians alone do not
establish significant savings; the study design and reviewer-level uncertainty
need a prospective analysis once sufficient sessions exist.

For the next candidate-generation experiment, prioritize additional correct
answers at a fixed false-alarm budget. For automatic correction, require improved
held-out CER/WER and bounded damage. For review tooling, require better verified
errors per minute without increased wrong decisions. F1 supplements these goals;
it does not replace them.
