"""Stable typed policies and audit vocabulary for lexical repair."""

from enum import StrEnum


class RepairPolicy(StrEnum):
    SOURCE_FAITHFUL = "source-faithful"
    CORRECTED_READING = "corrected-reading"


class RepairAction(StrEnum):
    REVIEW = "review"
    CORRECT = "correct"


class SourceOrigin(StrEnum):
    OCR_SUPPORTED = "ocr-supported"
    OCR_ALTERNATIVE_SUPPORTED = "ocr-alternative-supported"
    SOURCE_READING_SUPPORTED = "source-reading-supported"
    UNRESOLVED = "unresolved"


class RepairReason(StrEnum):
    ORIGINAL_PREFERRED = "original-preferred-or-ambiguous-placement"
    JUDGMENT_REQUIRED = "context-judgment-required"
    CONTEXT_REJECTED = "context-rejected-or-uncertain"
    SPELLING_AND_MEANING = "spelling-and-meaning-judgment"
    CHARACTER_AND_CONTEXT = "character-alternatives-and-context"
    CROP_AND_CONTEXT = "crop-readings-and-context"
    CONSISTENT_SPELLING = "approved-invalid-spelling-rule"


class CorrectionClass(StrEnum):
    READING_SPELLING = "reading-edition-spelling"
