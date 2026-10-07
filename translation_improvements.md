# Translation improvements plan

Status (2026-10-07): phases 0, 1, 2 (notes batching, small caps, book brief, `--review-glossary`),
3, 4a, 4c and 5 implemented and measured where possible; results in `docs/research.md`
("Translation settings"). 4b skipped: prefill is 8.3% of hy-mt2 time. Open: measuring 4c
(needs Ollama restarted with `OLLAMA_NUM_PARALLEL`), 4d (needs MLX / draft model downloads),
2b name harvesting and 2c note-reference context.

Scope: `scripts/translate.py`, `config/translation-models.json`, `tests/test_translate.py`, a new
`scripts/translate_eval.py`, and the "Translation settings" section of `docs/research.md`.
Default model: `hy-mt2:latest`. Every change must keep output EPUBs valid, keep IDs and links,
and be measured with the evaluation harness (phase 0) before its defaults change.

## Phase 0 — Permanent evaluation harness (prerequisite)

Today's grid script lives in the session scratchpad. Move it into the repo so later changes can be measured.

- `scripts/translate_eval.py`
  - `--samples config/translation-eval.json`: a list of `{epub, document, start_char, length, kind}`
    windows. Kinds: prose, notes, dialogue, verse. Cover Cosmic Serpent plus one pipeline-built book.
  - `--grid '{name: overrides}'` and `--model`. Each run is written to `work/translation-eval/<model>/<name>.epub`
    and one JSONL row is appended to `work/translation-eval/results.jsonl`.
  - Shares the cache and glossary per model under `work/translation-eval/`.
- Move the metrics from the scratchpad into `translate.py` as a reusable `quality_flags(source_text,
  target_text, narrator)`, so phase 1 can use them too:
  - length ratio
  - sentence-count drop
  - leftover source-language words
  - words mixing Latin and Cyrillic letters
  - first-person gender counts
  - number of spellings of each glossary term
- Optional `--qe`: score each chunk with a local reference-free quality metric
  (`Unbabel/wmt22-cometkiwi-da` via `unbabel-comet`, on CPU or MPS).
  - CometKiwi is licensed CC BY-NC-SA: personal use only. Document this.
  - It's an optional dependency, imported lazily.
- Tests: `quality_flags` on fixed strings.
- Verify: rerun the hy-mt2 chosen config. Results should match the recorded table: 98 s, 21/21 markup,
  0 lost sentences.

## Phase 1 — Quick wins

### 1a. Runtime quality checks with retry
- After decoding each chunk or batch, run `quality_flags`. Flag a chunk when:
  - its length ratio is below 0.8 (or above 1.6);
  - it has fewer sentences than the source;
  - it contains mixed-alphabet words;
  - leftover English makes up more than 2% of its words (glossary-protected names excluded);
  - it has first-person forms of the wrong gender.
- On a flag:
  1. Retry once with half the batch size, or one paragraph at a time.
  2. If still flagged, keep the better attempt and record it in `report["flagged_blocks"]` with the reasons.
- Config: `checks: true` and `min_length_ratio` per model, because hy-mt2 compresses.
- Tests:
  - a fake translator that drops a sentence → retried, then flagged;
  - a fake translator that is correct → no retry.

### 1b. Right-size `num_ctx`
- Compute the requirement per request (~2.2 × source characters / 3.5 characters per token, plus context
  and glossary tokens) and send `min(config num_ctx, needed rounded up to 2048)`.
- Caveat: Ollama reloads the model when `num_ctx` changes, so use fixed per-model values instead:
  - hy-mt2: 8192
  - qwen3.6: 8192
  - gemma4: 12288
  - qwen3.8: 16384
- Verify: `ollama ps` shows the smaller context and swap use drops during a 30-minute run; time per
  request is unchanged or better.

### 1c. Russian typography post-pass (deterministic, target `ru` only)
- New `typeset(text, lang)`, applied to text nodes after decode, never inside placeholders:
  - straight and English curly quotes → «» with nested „“;
  - spaced dashes ` - ` and ` – ` → ` — `, with a non-breaking space before;
  - a non-breaking space between initials and surname ("В. Г. Богораз");
  - remove the space between sentence punctuation and a following note-reference link.
- Must not touch: URLs, numbers with hyphens (1980-х), or text kept in English.
- Tests for each rule, plus a no-op on kept citation blocks.

## Phase 2 — Consistency and context

### 2a. Book brief
- Before translating, one model request over the first ~6k characters of prose plus the glossary builds a
  JSON brief: genre, register, period, a narrator summary, and `people: {name: gender}` for recurring
  capitalized names (from the glossary candidates).
- Saved as `<output>.brief.json` and reused or editable like the glossary.
- Injection:
  - `instruct`: one line in the system prompt plus a gender hint for each person mentioned in the chunk;
  - `hy-mt`: first in the `[Background Information]` block;
  - `translategemma`: none.
- The gender hint per person extends `narrator_note`.
- Tests: the brief is filtered per chunk like the glossary.

### 2b. Glossary refinements
- Entries become `{term: rendering}` or `{term: {rendering, gender?, note?}}`; both forms stay readable.
- After the first chapter, harvest new recurring capitalized words from the outputs and append them to the
  glossary with the rendering the model chose. Recurring means ≥3 occurrences and not yet in the glossary.
- `--review-glossary`: build the glossary and brief, print them, and exit, so they can be edited before a
  long run.

### 2c. Notes
- Batch the commentary sentences of consecutive notes, as body paragraphs are batched. Citation sentences
  stay verbatim (`citation_sentence`).
- The context for a note is the body sentence that holds its reference link. Resolve it through the
  backlink href and add it as background.
- Tests: mixed notes batch correctly and citation sentences pass through byte-identical.

### 2d. Keep small caps
- When `glued()` unwraps a split word, record the element's class and style. After decode, wrap the
  translated word in a `<span>` with the same class, recased to Title case.
- Verify against the Cosmic Serpent chapter titles.

## Phase 3 — Second-opinion pass (quality, opt-in)

- `--review-model qwen3.8:latest` (or a config `review` block): after the main pass, take the blocks
  flagged in 1a plus the lowest-scoring 5–10% by quality score, if `--qe` is on.
- Send each to the reviewer as source + translation + glossary + brief, asking for a corrected translation
  that keeps the tags. Same instruct prompt, with a "post-edit" instruction.
- Accept the edit only if:
  - the tags decode;
  - `quality_flags` doesn't get worse;
  - the quality score improves (when scoring is enabled).
- Record each accepted or rejected edit in the report.
- Rationale: self-refinement inherits the model's own bias, so the reviewer is a different model.
- Verify on the eval samples: count of flagged blocks before and after, quality-score delta, and a
  side-by-side read.

## Phase 4 — Throughput

### 4a. Measure where time goes
- Log Ollama's `prompt_eval_count`, `prompt_eval_duration`, `eval_count` and `eval_duration` per request
  into the cache rows and the report.
- Decide 4b from the measured prefill share.

### 4b. Prefix-cache-friendly prompts (only if prefill is above 20% of time)
- Order prompts as stable instructions → glossary or brief subset → context → chunk.
- For `instruct`, move the per-chunk glossary from the system prompt into the user turn, so the system
  prompt is constant.
- Verify a lower `prompt_eval_count` on consecutive requests.

### 4c. Parallel chapters
- Context already resets per document, so documents are independent. Translate up to `parallel` documents
  concurrently (thread pool, one `Translator` history per document, shared glossary and cache with a
  lock on appends).
- Requires `OLLAMA_NUM_PARALLEL ≥ parallel`. Default `parallel: 1`; document the setting.
- Keep the output deterministic: documents are assembled in manifest order regardless of finish order.
- Verify: wall time on 2 chapters with parallel 2 vs 1, identical output given a warm cache.

### 4d. Backend experiments (no code unless they win)
- Ollama's MLX engine: check whether the installed Ollama version and hy-mt2 build support it; measure
  tokens/s.
- Draft model (Hy-MT2 1.8B for the 30B), if Ollama exposes `draft_model` for this architecture.
- Record the outcome in `docs/research.md`.

## Phase 5 — Review sheet

- `<output>.review.html`: an offline side-by-side of source and translation per block, highlighting flags,
  quality scores, fallback levels and glossary terms. Follows the repo's `proofreading-review.html`
  convention.
- `<output>.corrections.json`: manual per-block replacement translations keyed by document and block
  index plus a source-text hash. They are applied on rerun, before the cache lookup, and fail loudly if
  the source text changed.

## Order and acceptance

1. Phase 0 → 1a, 1b, 1c → re-measure.
2. Phase 2 → re-measure.
3. Phase 3 (opt-in) → 4a → 4b/4c as the numbers justify → 4d → 5.

Each phase:
- tests pass (`.venv/bin/python -m unittest discover -s tests`) and `black --check` is clean;
- eval results recorded in `docs/research.md`;
- a full Cosmic Serpent run passes EPUBCheck with no new errors.

Never regress the recorded baseline: markup 21/21, 0 lost sentences, a single spelling per glossary term,
narrator gender correct.
