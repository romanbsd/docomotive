"""Validate profile values without changing their representation or defaults."""

from repair_types import RepairPolicy

import math
import re

BOOL_KEYS = set(
    """chapter_heading continuous_indented_rows native_small_numeric_sup native_heading_merge native_index_indents native_list_layout native_relative_font_sizes
    repair_ocr_punctuation repair_ocr_case inspect_partial_rows recover_gap_rows normalize_quotation_marks recover_figure_frames recover_body_top source_figure_anchors link_numeric_footnotes infer_verse infer_inset_verse link_symbol_footnotes recover_hanging_margins recover_ocr_regions recover_ocr_glyph_confusions recover_pdf_heading_styles recover_pdf_typography recover_pdf_numeric_superscripts recover_scan_font_metrics recover_scan_italics recover_scan_inline_italics
    research_missing_endnotes recover_scanned_endnotes recover_reference_markers recover_variable_superscripts recover_image_only_headings repair_lexical_confusions repair_word_wraps scan_raster_only section_navigation source_prose_indents source_relative_figures""".split()
)
NUMBER_KEYS = set(
    """quote_font_scale native_body_size native_superscript_max_size endnote_marker_min
    endnote_marker_limit upper_margin_cutoff uppercase_margin_cutoff
    quote_first_line_indent""".split()
)
PAGE_LIST_KEYS = set("""glossary_pages hanging_pages index_pages
    native_quote_pages reference_pages vision_primary_pages""".split())
LIST_KEYS = PAGE_LIST_KEYS | set(
    """artwork_pages artwork_overlay_exclusions chapters contributors
    endnote_sections figures frontmatter_endnotes line_join_words
    metadata_record_overrides native_excluded_fonts native_heading_sizes
    native_heading_texts related_print_isbns row_heading_rules row_regions
    statistics_excluded_chapters subjects metadata_author_aliases source_gaps""".split()
)
DICT_KEYS = set("""bottom_margin_cutoffs chapter_body_starts comments cover_file
    cross_page_continuations excluded_pages glossary_terms index_splits
    native_font_styles native_typography note_continuations note_starts page_labels
    paragraph_margins publisher_mark small_caps_openings source_completeness
    verse_regions ocr_tesseract_page_languages""".split())
PAGE_MAP_KEYS = DICT_KEYS - set("""comments cover_file glossary_terms native_font_styles
    native_typography publisher_mark source_completeness""".split())
INT_KEYS = {"printed_page_offset", "endnote_chapter"}


def validate_profile(book, path="profile", page_count=None):
    def fail(key, reason):
        raise ValueError(f"Profile {path}: {key}: {reason}")

    def integer(value):
        return type(value) is int

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    def page(value, key):
        if not integer(value) or value < 1 or (page_count and value > page_count):
            fail(key, f"expected a PDF page in 1..{page_count or 'end'}")

    def unit(value, key):
        if not number(value) or not 0 <= value <= 1:
            fail(key, "expected a finite normalized coordinate in 0..1")

    for key in ("title", "author", "language", "slug", "source_sha256", "chapters"):
        if key not in book:
            fail(key, "required field is missing")
    for key, value in book.items():
        expected = (
            bool
            if key in BOOL_KEYS
            else (
                list
                if key in LIST_KEYS
                else dict if key in DICT_KEYS else int if key in INT_KEYS else str
            )
        )
        if key in NUMBER_KEYS:
            if not number(value):
                fail(key, "expected a finite number")
        elif type(value) is not expected:
            fail(key, f"expected {expected.__name__}, got {type(value).__name__}")
    if "quote_font_scale" in book and not 0.5 <= book["quote_font_scale"] <= 1.5:
        fail("quote_font_scale", "expected a readable scale in 0.5..1.5")
    if book.get("figure_cleanup", "none") not in ("none", "white"):
        fail("figure_cleanup", "expected none or white")
    if book.get("figure_color_mode", "auto") not in ("auto", "rgb", "grayscale"):
        fail("figure_color_mode", "expected auto, rgb or grayscale")
    if not re.fullmatch(r"[0-9a-f]{64}", book["source_sha256"]):
        fail("source_sha256", "expected a lowercase SHA-256 digest")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", book["slug"]):
        fail("slug", "expected a filename-safe identifier")
    for key in ("title", "author", "language"):
        if not book[key].strip():
            fail(key, "must not be empty")
    if book.get("text_source", "ocr") not in ("ocr", "native"):
        fail("text_source", "expected ocr or native")
    if book.get("lexical_repair_policy", RepairPolicy.SOURCE_FAITHFUL) not in (
        RepairPolicy.SOURCE_FAITHFUL,
        RepairPolicy.CORRECTED_READING,
    ):
        fail("lexical_repair_policy", "expected source-faithful or corrected-reading")
    if book.get(
        "lexical_repair_policy"
    ) == RepairPolicy.CORRECTED_READING and not book.get("repair_lexical_confusions"):
        fail(
            "lexical_repair_policy",
            "corrected-reading requires repair_lexical_confusions",
        )
    chapters = book["chapters"]
    if not chapters:
        fail("chapters", "must not be empty")
    previous = 0
    for i, chapter in enumerate(chapters):
        key = f"chapters[{i}]"
        if not isinstance(chapter, list) or len(chapter) != 3:
            fail(key, "expected [first_page, last_page, title]")
        start, end, title = chapter
        page(start, key)
        page(end, key)
        if start > end or start <= previous:
            fail(key, "ranges must be ordered and non-overlapping")
        if not isinstance(title, str) or not title.strip():
            fail(key, "title must be a nonempty string")
        previous = end
    for key in PAGE_LIST_KEYS:
        for value in book.get(key, []):
            page(value, key)
    seen_gaps = set()
    for gap in book.get("source_gaps", []):
        if not isinstance(gap, dict) or not {"page", "text", "evidence"} <= gap.keys():
            fail("source_gaps", "expected page, text and source evidence")
        page(gap["page"], "source_gaps")
        if gap["page"] in seen_gaps:
            fail("source_gaps", "duplicate notice page")
        seen_gaps.add(gap["page"])
        for field in ("text", "evidence"):
            if not isinstance(gap[field], str) or not gap[field].strip():
                fail("source_gaps", f"{field} must be a nonempty string")
    for value in book.get("ocr_tesseract_page_languages", {}).values():
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", value
        ):
            fail(
                "ocr_tesseract_page_languages",
                "expected Tesseract language identifiers",
            )
    for value in book.get("artwork_pages", []):
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not isinstance(value[1], str)
        ):
            fail("artwork_pages", "expected [page, name] pairs")
        page(value[0], "artwork_pages")
    for key in PAGE_MAP_KEYS:
        for value in book.get(key, {}):
            if not re.fullmatch(r"[1-9]\d*", value):
                fail(key, f"invalid page key {value!r}")
            page(int(value), key)
    for key in (
        "chapter_body_starts",
        "paragraph_margins",
        "note_starts",
        "bottom_margin_cutoffs",
        "index_splits",
    ):
        for value in book.get(key, {}).values():
            unit(value, key)
    for key in ("upper_margin_cutoff", "endnote_marker_min", "endnote_marker_limit"):
        if key in book:
            unit(book[key], key)
    for key in ("native_body_size", "native_superscript_max_size"):
        if key in book and book[key] <= 0:
            fail(key, "must be positive")
    if book.get("endnote_marker_min", 0) > book.get("endnote_marker_limit", 1):
        fail("endnote_marker_min", "must not exceed endnote_marker_limit")

    def chapter_ref(value, key):
        if not integer(value) or not 1 <= value <= len(chapters):
            fail(key, "chapter reference is out of range")

    if "endnote_chapter" in book:
        chapter_ref(book["endnote_chapter"], "endnote_chapter")
    for value in book.get("statistics_excluded_chapters", []):
        chapter_ref(value, "statistics_excluded_chapters")
    seen = set()
    for section in book.get("endnote_sections", []):
        if (
            not isinstance(section, dict)
            or not {"source_chapter", "heading", "expected_notes"} <= section.keys()
        ):
            fail(
                "endnote_sections",
                "expected source_chapter, heading and expected_notes",
            )
        chapter_ref(section["source_chapter"], "endnote_sections.source_chapter")
        if section["source_chapter"] in seen:
            fail("endnote_sections", "duplicate source chapter")
        seen.add(section["source_chapter"])
        if not integer(section["expected_notes"]) or section["expected_notes"] < 1:
            fail("endnote_sections.expected_notes", "expected a positive integer")
        if not isinstance(section["heading"], str) or not section["heading"].strip():
            fail("endnote_sections.heading", "expected a nonempty string")
    if book.get("endnote_sections") and "endnote_chapter" not in book:
        fail("endnote_chapter", "required when endnote_sections is provided")
    if book.get("research_missing_endnotes") and not book.get("endnote_sections"):
        fail("research_missing_endnotes", "requires configured endnote_sections")
    for rule in book.get("row_heading_rules", []):
        if not isinstance(rule, dict) or not isinstance(rule.get("pages"), list):
            fail("row_heading_rules", "expected objects with pages")
        for value in rule["pages"]:
            page(value, "row_heading_rules")
    for key in ("figures", "row_regions"):
        for item in book.get(key, []):
            if not isinstance(item, dict) or "page" not in item:
                fail(key, "expected objects with a page")
            page(item["page"], key)
            if key == "figures" and "rect" not in item:
                fail(key, "rect is required")
            if "rect" in item:
                rect = item["rect"]
                if not isinstance(rect, list) or len(rect) != 4:
                    fail(key, "rect must contain four coordinates")
                for value in rect:
                    unit(value, key)
                if rect[0] >= rect[2] or rect[1] >= rect[3]:
                    fail(key, "rect must have positive width and height")
            if "lo" in item or "hi" in item:
                unit(item.get("lo"), key)
                unit(item.get("hi"), key)
                if item["lo"] >= item["hi"]:
                    fail(key, "lo must be less than hi")
