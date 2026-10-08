<!-- codebase-memory-mcp:start -->
For structural codebase exploration, use the installed `codebase-memory` skill.
<!-- codebase-memory-mcp:end -->

## Generic pipeline fixes

This is a generic book-conversion pipeline. Fix defects in shared detection,
reconstruction, correction, or rendering logic so the improvement applies to
other books. Do not substitute a book-specific page, phrase, or coordinate
override for a general fix when the underlying problem can be inferred from
source evidence. Keep genuinely source-specific facts and reviewed exceptions
in book profiles, with evidence. Add regression coverage for the general
behavior and check earlier books for unintended changes.

## Reviewing regression changes

`scripts/regression.py` detects change, not quality: it compares output hashes
and reports `changed`. Never use `--accept-baseline` just to make a run pass.
Run it on a tree that holds only the change under review, so every difference
can be attributed. For each changed case:

1. Build the baseline commit in a `git worktree` against the same `work/`
   caches and diff the full outputs. The baseline stores only hashes, and
   `output/<book>/` may be older than the baseline.
2. Diff `<slug>.txt` word by word. Trace each change to its audit entry in
   `corrections-applied.json` or `lexical-repair.json` (rule, page, bbox,
   evidence).
3. Check each change against the source page image at that bbox. It is an
   improvement if the new text matches the print or fixes a real OCR error. It
   is breakage if it departs from the print, undoes a correct fix, or corrects a
   printed typo outside an `--editorial` build.
4. Treat lost coverage, or fewer noterefs, backlinks or figures, as breakage
   unless the reason is shown.
5. Check the direction: narrowing a rule should only remove edits, and widening
   it should only add them. Changes in the other direction mean side effects.
6. Accept the baseline only when every difference is explained. Record the
   per-difference reasoning in the commit message or `docs/research.md`. Fix
   breakage in the general rule, not with a profile override.
