"""Pinned local lexical evidence for rare Russian source-line wraps."""

import re
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from common import file_digest


class RussianWrapLexicon:
    def __init__(self):
        import pymorphy3
        import pymorphy3_dicts_ru

        path = Path(pymorphy3_dicts_ru.get_path())
        self.analyzer = pymorphy3.MorphAnalyzer(path=str(path), lang="ru")
        self.provenance = {
            "engine": "pymorphy3",
            "version": version("pymorphy3"),
            "dictionary_version": version("pymorphy3-dicts-ru"),
            "files": {
                p.name: file_digest(p) for p in sorted(path.iterdir()) if p.is_file()
            },
            "policy": "known forms; dictionary-backed ne-prefix nouns/adjectives; no suffix or unknown-prefix guesses",
        }

    # Bound per-book memoization while retaining common repeated wrap pairs.
    @lru_cache(maxsize=8192)
    def supports(self, left, right):
        # Two letters per fragment exclude isolated particles and initials.
        if not all(re.fullmatch(r"[А-Яа-яЁё]{2,}", s) for s in (left, right)):
            return False
        word = (left + right).lower()
        if self.analyzer.word_is_known(word):
            return True
        for parse in self.analyzer.parse(word):
            methods = parse.methods_stack
            # Negated nouns/adjectives are productive dictionary-backed forms.
            # Other productive prefixes can require a real hyphen (e.g. арт-).
            if (
                parse.tag.POS in {"NOUN", "ADJF", "ADJS"}
                and len(methods) == 2
                and type(methods[0][0]).__name__ == "DictionaryAnalyzer"
                and type(methods[1][0]).__name__ == "KnownPrefixAnalyzer"
                and methods[1][1] == "не"
            ):
                return True
        return False
