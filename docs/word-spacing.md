# Within-line OCR word spacing

Enable `repair_ocr_word_spacing: true` in a scanned-book profile to repair a
small, source-supported class of fused words. The default is off. Pharmako/Poeia
currently enables it; native-text inputs bypass it. The shared implementation
is [`scripts/word_spacing.py`](../scripts/word_spacing.py).

## Automatic repair

The pass runs before reviewed manual overlays and changes one unknown alphabetic
token into two or three known words. Every letter and its case must remain
identical. Two other cached OCR engines must propose exactly the same split,
with at least two matching neighboring words of two or more letters. Fresh
Tesseract line crops at both 600 and 300 dpi must independently reproduce the
same anchored spacing. Those scales test stability within one engine, not
independent recognition. Missing or conflicting readings abstain.

Dictionary membership, protected vocabulary, or wordfreq Zipf frequency of at
least 2 veto splitting an accepted original. Targets must be accepted by one of
those resources. Lowercase text and an exact initial `I` pronoun split are
eligible; other capitalization is protected. Numbers, hyphenated compounds,
apostrophes, punctuation changes and joining separate words are excluded.
The frequency threshold is a conservative guard, not a confidence estimate.

Edits use the common checked occurrence/inline-offset machinery. The pass
includes verse as well as prose without changing verse reconstruction.
`word-spacing-repair.json` records decisions, engine readings, crop scales,
anchors, page/row/bbox and lexical resource hashes. Accepted edits also appear
as `source-word-spacing` in `corrections-applied.json`. Regression snapshots
require and hash the diagnostic when the pass is enabled.

Seven newly discovered errors were corrected in Pharmako/Poeia. PDF page
numbers and per-change source reasoning are recorded in
[`research.md`](research.md#source-supported-word-spacing-2026-10-13).
The previously reviewed `Ilearned → I learned` remains a manual overlay because
fresh crop recognition loses the initial pronoun. No manual overlay was retired.

## Reproduce the research

The research helper never modifies book text. It evaluates the source-checksummed
[`23-case manifest`](../tests/spacing-cases.json), renders a review sheet, measures
character-box gaps and compares dictionary segmentation with book-context scores.
Use a baseline output tree built against the same source caches:

```sh
.venv/bin/python scripts/spacing_research.py \
  --book-output-root work/spacing-full-baseline/run-i2bn4il_ \
  --output work/spacing-research/benchmark
```

The resulting `report.json` fingerprints the manifest, helper, baseline models,
OCR runtime/models and lexical resources. `review.html` contains all source
crops. The required output-root argument avoids silently evaluating against a
stale published book. The helper currently covers English scanned PDFs.

## Findings and next heuristic

The initial survey examined 61,714 rows across seven English scanned books.
Two cached witnesses produced 55 shared spacing proposals; requiring neighboring
anchors left 49 (34 joins and 15 splits). The conservative lexical gate left
eight splits, and the fresh crop gate accepted exactly the seven fixes above.
Agreement is useful evidence, but the same OCR systems can agree on a wrong
boundary: source crops retain `if you`, `of a` and `psilocybin` even when multiple
readings propose a join or a split.

The reviewed benchmark contains 18 positive cases and five negative controls,
including existing manual edits and newly found examples. These deliberately
selected cases measure behavior, not corpus-wide precision or recall:

| Experiment | Observed result | Implication |
|---|---|---|
| SymSpell zero-edit segmentation | Exact answer for 14/18 positives; retains all 5 controls | Useful proposal generator; insufficient as an edit rule |
| Book context with the source row excluded | Positive score for all 18 positives, but also 2/5 controls | Rank candidates; never authorize from score alone |
| Character gap / median character height | True splits include negative or zero gaps; false `psilocy bin` has a gap around 0.36 | A universal physical-gap threshold is unsafe |
| Dictionary acceptance instead of a frequency-only original veto | Two additional `ofthe → of the` cases survive both crop scales | Worth evaluating as a controlled expansion |

Ordinary whole-book counts reinforce the input error: all seven newly corrected
tokens were initially favored over their correct splits. The benchmark removes
the observed source row's bigram contributions before scoring, including an
existing manual answer where present. This prevents a candidate from training
its own comparison. Other rows can still contain correlated OCR errors.

Frequency alone also mistakes OCR artifacts for vocabulary: `ofthe` has Zipf
frequency 3.33 but is absent from the pinned English Hunspell and SymSpell
dictionaries. Replacing the original veto with dictionary recognition while
retaining case/protected-word checks yielded 11 anchored proposals. Nine survived
fresh crops: the seven fixes and two further `ofthe → of the` errors on Pharmako
PDF pages 94 and 132, both visually confirmed. `ina → in a` and `Ilearned → I
learned` failed the crop gate. This alternative remains research-only.

A true `sy nesthetes → synesthetes` join on Otherworlds PDF page 256 illustrates
the opposite coverage problem: rare legitimate terms may be absent from every
dictionary. One fresh crop also damages the letters. It remains a reviewed
candidate, with no deployed joining rule. Likewise, `half myth → halfmyth` and
`of rum from → ofrumfrom` receive tempting context scores while contradicting
the print. Protected terms and valid original words need explicit vetoes.

The next useful expansion is therefore bounded split proposals, dictionary and
protected-word vetoes, leave-source-row-out context ranking and unchanged literal
witness/crop gates. Geometry can corroborate proposals after calibration within
the same line/font; character boxes have overhang, kerning and recognition error.
Joining and rare-term recovery should first grow the review queue and held-out
source controls. The deployed rule still makes only the seven validated edits.

## Primary-source research

[SymSpell's documentation](https://github.com/wolfgarbe/SymSpell) describes
segmentation and compound lookup. Its broader normalization can remove hyphens
or normalize ligatures, so any proposal must pass the non-space-character
invariant rather than replacing a whole generated sentence.

[Tesseract's quality guide](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)
distinguishes single-line and raw-line segmentation. Its
[command documentation](https://github.com/tesseract-ocr/tesseract/blob/main/doc/tesseract.1.asc)
describes character-box outputs and interword-space preservation; preserving
recognized spaces does not establish where an omitted boundary belongs.

[Nastase and Hitschler's LREC 2018 study](https://www.cl.uni-heidelberg.de/~nastase/publications/Papers/lrec2018_correction-ocr-word.pdf)
reports strong segmentation correction on synthetic ACL-domain examples but
weaker results for unseen tokens, with erroneous merges among the examples.
That supports studying proposals, not assuming transfer to literary scans.
[OCR-D's evaluation specification](https://ocr-d.de/en/spec/ocrd_eval.html)
provides CER/WER evaluation against ground truth. Future corpus evaluation
should measure boundary precision/recall alongside CER/WER on held-out,
representative source lines, separating prose, verse and unusual vocabulary.

## Validation boundary

All 17 cached regression cases were rebuilt on both the changed tree and detached
baseline `c73c730`, sharing the same caches. Sixteen snapshots are identical;
Pharmako differs by exactly seven source-reviewed word splits. Coverage, figures,
noterefs, backlinks and character formatting are retained. The published EPUB
matches the validated build and passes EPUBCheck with zero fatals, errors,
warnings or infos. The 445-test suite passes. Stored regression baselines remain
unchanged. These checks validate this bounded change, not complete proofreading
or a general accuracy claim for the research proposals.
