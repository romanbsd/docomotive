"""Literal edit chunks shared by review grouping and statistical research."""

from difflib import SequenceMatcher


def edit_chunks(before, after):
    """Yield every directed changed chunk, including insertions and deletions."""
    for tag, i, j, k, l in SequenceMatcher(
        None, before, after, autojunk=False
    ).get_opcodes():
        if tag != "equal":
            yield before[i:j], after[k:l]
