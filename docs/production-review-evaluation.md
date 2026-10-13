# Production review rules and measurement foundation

Implementation of the first steps in the [improvement plan](ocr-improvement-plan.md),
2026-10-13. The review detector and lexical suggestion functions are now shared
between the CLI and the benchmark. `ReviewWitnesses` supplies the actual aligned
peer and distinct-page vocabulary rules. The refactor preserves review outputs;
no automatic repair policy, book profile or regression baseline was changed.

## What is measured

```sh
.venv/bin/python scripts/production_candidate_research.py \
  --output work/production-candidate-research/evaluation.json
```

[Frozen results](production-candidate-evaluation.json) contain every case's
detection/exclusion reason, complete proposal evidence, generator attribution,
unique coverage, per-book results and resource fingerprints. Frozen original
evidence is verified before evaluation. Whole annotated pages are excluded from
vocabulary collection; the script also checks that no held-out attestation remains.

The benchmark uses unrepaired primary OCR rows with exact text/bbox bindings and
profile chapter ranges. It calls production lexical exclusions, the capped closest
general-lexicon/compound suggestions, `ReviewWitnesses.collect`, `suggestions` and
`disagreements`. Lexical context reranking is omitted because it changes ordering
and scores, not proposal membership. No target answer generates a proposal.

These are **raw row fixtures**, not the complete reconstructed page/paragraph
layout: header/footer exclusion, paragraph joining and all book-model inclusion
choices are not reproduced. Consequently this evaluates shipped candidate rules,
not end-to-end candidate recall in a built book. Existing reviewed protected
resources remain pinned, as in the CLI; this is retrospective resource evaluation,
not unseen-book performance. The same 91 selected errors and 90 synthetic correct
controls are reused; controls change the primary token but retain original cached
peer readings. Their flags do not establish whole-book false-positive rates.

## Results

| Book | Selected errors | Surfaced for review | Reviewed answer proposed |
|---|---:|---:|---:|
| Natural Mind | 7 | 6 | 5 |
| Feeling for the Organism | 3 | 3 | 2 |
| Otherworlds | 42 | 33 | 21 |
| Pharmako/Poeia | 14 | 14 | 13 |
| Wizard of the Upper Amazon | 25 | 23 | 21 |
| **Total** | **91** | **79** | **62** |

| Generator | Answers supplied | Answers unique to this generator |
|---|---:|---:|
| General lexicon / compound lookup | 38 | 9 |
| Aligned two-peer disagreement | 29 | 21 |
| Aligned book vocabulary on two other pages | 28 | 1 |

Generator counts overlap. Twelve errors are not surfaced: six short tokens,
five dictionary-known words and one frequent word. Examples include `con → eon`,
`eves → eyes` and `kuma → xuma`. Seventeen more are surfaced without the correct
answer among their proposals. No selected error is surfaced with an empty
proposal list, but a regression test verifies that an empty list retains an
unresolved review item. Twenty-one synthetic controls receive review items and
alternatives; this is exposure under counterfactual readings, not precision.

The earlier **70/91** vocabulary result and **74/91** with injected peers were
broad research pools. They bypassed production detection and display limits,
and used a weaker recurrent vocabulary. Their denominators match, but their
tasks differ. The new **62/91** is not a pipeline regression; it exposes the gap
between unrestricted candidate availability and proposals actually admitted by
the shipped review rules. Source review is still required for every proposal.

## Local timing for a prospective review pilot

Regenerate a proofreading sheet with the existing `scripts/proofread.py` command.
Each occurrence now has an optional **Start review timer** button. Start it when
inspecting the source and context; click again to pause. Only one occurrence can
be timed at once. The timer also pauses when another starts, the tab is hidden,
a decision is acknowledged, or decisions are exported. Visible idle time still
counts until explicitly paused; this is an active-inspection proxy, not an
automatic measure of attention.

Timing stays in local browser storage under the sheet's source/model/profile
identity. The decision export adds `review_metrics`, with cumulative active
milliseconds, tracked occurrence IDs, start counts and groups started. Reloaded
timers remain paused. Untimed occurrences have no estimated duration; batch
decisions do not inherit another occurrence's time. Normal decisions and source
acknowledgement gates are unchanged. There is no network telemetry.

Timing does not certify that an OCR-error decision was correct. The next pilot
must independently check decisions against the scan, retain source-labeled misses
and correct text, and compare matched queues in counterbalanced sessions. No human
review-time experiment was performed in this implementation. Queue ordering is
unchanged until that experiment supports a change.

## Validation and next step

All **488 Python tests** and strict JavaScript control/timer tests pass. Five new
Python tests cover detector exclusions, anchored peer bypass for valid words,
unresolved items, chapter/source binding and ambiguous-token abstention. Timer
tests cover switching, hidden time, acknowledgement, export, restoration, stale
IDs, malformed counters and untimed occurrences. These use a strict DOM double,
not a live browser timing or usability study.

Full cached candidate CLI runs for Natural Mind and Wizard of the Upper Amazon
produce byte-identical `correction-candidates.json`, `protected-term-proposals.json`
and `vocabulary-analysis.json` before and after the shared-function refactor.
The new benchmark replays byte-identically. No EPUB rebuild or baseline acceptance
was needed: the changes affect advisory review functions and sheet controls, not
book reconstruction, automatic repair or rendering of the EPUB.

The next bounded implementation is a source-bound, book-local confirmed-form
ledger, evaluated on later pages before changing proposal or priority behavior.
Representative natural controls and timed sessions remain needed before claiming
better whole-book detection or review efficiency.
