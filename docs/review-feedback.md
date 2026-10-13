# Source-bound book feedback

An **opt-in experimental** confirmed-form ledger now complements the review
workflow. It derives evidence from the latest occurrence decision export; it
never applies edits or removes review items. The forward evaluation found no
unique coverage or priority benefit, so it is not enabled by default.

## Use

After checking source occurrences and exporting decisions from the current sheet:

```sh
.venv/bin/python scripts/candidates.py \
  --profile config/wizard-upper-amazon/book.json \
  --work work/wizard-upper-amazon \
  --output output/wizard-upper-amazon \
  --feedback /path/to/proofreading-decisions.json
```

The output directory must contain the matching `book-model.json` and
`corrected-pages.json`. Regenerate its proofreading sheet with the existing
`scripts/proofread.py` command to see new suggestions and their evidence.

The export is the ledger's complete snapshot. Removing a decision or changing it
to uncertain revokes its feedback on the next run. Do not append old decisions
from a different model to a new export. Source, model or profile changes require
new source checking; stale exports are rejected. Omitting `--feedback` preserves
the existing candidate workflow.

## Rules

- Accept only explicitly source-acknowledged **OCR error** and **Keep source**
  decisions with exact page/row identity, text hash, bbox and token span.
- Use shared correction validation for OCR decisions and shared row validation
  for keep decisions. Reject conflicting/duplicate occurrences and partial-token
  keep bindings. Printed typos and uncertain decisions do not supply vocabulary.
- Only alphabetic single-word forms of at least three characters qualify.
  Literal source forms, including accents, remain in the evidence. Lookup uses
  lowercase keys, as in existing review generators; this is not historical
  normalization or a universal Unicode tokenizer.
- Corrected forms can add at most five radius-two suggestions to already surfaced
  items. They do not bypass lexical detection. Every seed page must precede the
  earliest page of **all occurrences** in the item; same-page and future feedback
  are excluded. Originals and source review remain available.
- A confirmed unchanged form may lower priority to 3. It cannot protect a word,
  suppress an occurrence, or authorize a replacement. Any current aligned peer
  disagreement vetoes this priority change. Feedback evidence and that veto appear
  in the review item's proposal/statistics details.

The manifest fingerprints the export and records the qualifying seed count.
Proposals retain seed IDs, literal forms, pages and source bindings. The ledger
is rebuilt each run; no unreviewed global dictionary or cross-book cache is added.
The pipeline does not automatically consume the feedback option.

## Forward-page evaluation

```sh
.venv/bin/python scripts/feedback_research.py \
  --output work/feedback-research/evaluation.json
```

[Frozen results](feedback-evaluation.json) reuse the production-rule benchmark.
The first 20% of **annotated pages** seed each book; later whole pages are held
out. Previously reviewed labels simulate source-acknowledged exports. Keep
feedback is simulated separately using synthetic correct controls, not natural
human-confirmed occurrences. No held-out target seeds retrieval. Source, case and
resource fingerprints are checked; this is retrospective, not an unseen-book test.

| Measure | Result |
|---|---:|
| Later selected errors | 72 |
| Baseline answers already proposed | 51 |
| Additional correct answers from feedback | **0** |
| Errors receiving feedback suggestions | 3 |
| Later synthetic controls | 71 |
| Controls receiving feedback suggestions | 0 |
| Errors / controls deprioritized | **0 / 0** |

The three suggestion-bearing errors already had correct proposals. The weaker
earlier rewrite experiment and the broad unchanged-form diagnostic measured
different operations before production detection: their reported gains do not
transfer to this narrower ledger. Existing peer/vocabulary paths already cover
the repeated forms useful in this sample.

**Decision:** keep the option experimental. Do not widen detection, learn global
rewrites or enable unchanged-form priority automatically from this result. The
next useful evaluation requires representative natural correct text and further
source-reviewed rare/domain forms, alongside prospective timed review. Automatic
repair, reconstruction, profiles and regression baselines remain unchanged.

Validation covers stale source/model/profile bindings, exact kept-row evidence,
cross-page exclusion, literal accents, revocation, printed-typo exclusion,
conflicting decisions, partial words and peer vetoes. Full default candidate CLI
outputs and the selected production-rule cases are checked for preservation.
All **497 Python tests** and strict JavaScript tests pass; Black checks all 113
Python files. The forward evaluation replays byte-identically. Full default
Natural Mind and Wizard candidate outputs remain identical to baseline, and an
opt-in Wizard CLI run accepts an exactly bound simulated keep export without
losing review items. That fixture is a validation case, not a human review session.
No EPUB rebuild or regression baseline acceptance was performed.
