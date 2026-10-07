"""Small shared filesystem/provenance helpers, independent of OCR and rendering."""

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    return digest(Path(path).read_bytes())


def read_json(path):
    return json.loads(Path(path).read_text())


def write_text_atomic(path, text):
    """Publish a complete file; concurrent writers never share a staging name."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temp = Path(stream.name)
            stream.write(text)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def write_json(path, value):
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_epub(target, files):
    """Write a deterministic EPUB: stored mimetype first, sorted entries, fixed timestamps."""
    with zipfile.ZipFile(target, "w") as z:
        for name, data in [("mimetype", b"application/epub+zip")] + sorted(
            files.items()
        ):
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = (
                zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            )
            info.external_attr = 0o644 << 16
            z.writestr(info, data, compresslevel=9)


# Every key a profile may contain. Unknown keys fail loudly: profiles are read
# with .get() defaults, so a misspelled key would otherwise be silently ignored.
# "comments" is free-form reviewer documentation that no code reads.
PROFILE_KEYS = frozenset("""
    approved_cover_url archive_identifier artwork_overlay_exclusions artwork_pages
    author author_sort bottom_margin_cutoffs chapter_body_starts chapter_heading
    chapters comments continuous_indented_rows contributors cover_file
    cover_imprint cover_source cross_page_continuations date description doi
    endnote_chapter endnote_marker_limit endnote_marker_min endnote_reference_mode
    endnote_sections excluded_pages figure_cleanup figure_color_mode figures frontmatter_endnotes glossary_pages
    glossary_style glossary_terms hanging_pages index_pages index_splits isbn
    language line_join_words link_symbol_footnotes metadata_record_overrides metadata_title_suffix openlibrary_edition
    native_body_size native_excluded_fonts native_font_styles native_heading_sizes
    native_heading_texts native_heading_merge native_index_indents native_list_layout native_quote_pages native_relative_font_sizes native_small_numeric_sup
    native_superscript_max_size native_typography note_continuations note_starts
    infer_verse infer_inset_verse page_labels paragraph_margins printed_page_offset publisher publisher_mark
    quote_first_line_indent recover_hanging_margins recover_ocr_regions recover_ocr_glyph_confusions recover_pdf_heading_styles recover_pdf_typography recover_pdf_numeric_superscripts
    recover_scan_font_metrics research_missing_endnotes recover_scanned_endnotes recover_reference_markers recover_variable_superscripts recover_image_only_headings reference_pages repair_lexical_confusions repair_word_wraps
    related_print_isbns rights row_heading_rules row_regions section_navigation
    scan_raster_only slug small_caps_openings source_completeness source_note
    source_prose_indents source_relative_figures source_sha256 source_version
    statistics_excluded_chapters subjects subtitle text_source title
    upper_margin_cutoff uppercase_margin_cutoff verse_regions vision_primary_pages recover_scan_italics recover_scan_inline_italics
    repair_ocr_punctuation repair_ocr_case inspect_partial_rows recover_gap_rows normalize_quotation_marks recover_figure_frames recover_body_top source_figure_anchors quote_font_scale link_numeric_footnotes dictionary_locale ocr_tesseract_language ocr_vision_language ocr_rapid_language ocr_tessdata_dir metadata_author_aliases ocr_tesseract_page_languages source_gaps
    """.split())


def load_profile(path):
    book = read_json(path)
    if not isinstance(book, dict):
        raise ValueError(f"Profile {path}: expected an object")
    unknown = sorted(set(book) - PROFILE_KEYS)
    if unknown:
        raise ValueError(f"Unknown profile keys in {path}: {', '.join(unknown)}")
    from profile_validation import validate_profile

    validate_profile(book, path)
    return book


def cache_path(work, engine):
    return ROOT / (Path(work) / (engine + "-cache.txt")).read_text().strip()


def edit_pattern(edit):
    import re

    before = edit.get("before", edit.get("printed"))
    pattern = re.escape(before)
    if edit.get("exact_row", False):
        # Standalone annotation marks must not remove matching prose punctuation.
        return r"\A" + pattern + r"\Z"
    if edit.get("whole_word", "printed" in edit):
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    if "prefix" in edit:
        pattern = r"(?<=\A" + re.escape(edit["prefix"]) + ")" + pattern
    if "suffix" in edit:
        pattern += "(?=" + re.escape(edit["suffix"]) + r"\Z)"
    return pattern


def replace_checked(text, edit):
    import re

    after = edit.get("after", edit.get("proposal"))
    return re.subn(edit_pattern(edit), lambda match: after, text)


def apply_edits(items, edits, audit, kind):
    """Apply scoped exact overlays only if all occurrence preconditions hold."""
    for edit in edits:
        targets = [item for item in items if item["page"] == edit["page"]]
        changes = [replace_checked(item["text"], edit) for item in targets]
        count = sum(n for _, n in changes)
        if count != edit.get("count", 1):
            raise ValueError(f"Correction precondition failed: {edit}, found {count}")
        for item, (text, _) in zip(targets, changes):
            import re

            after = edit.get("after", edit.get("proposal"))
            matches = list(re.finditer(edit_pattern(edit), item["text"]))
            for match in reversed(matches):
                delta = len(after) - (match.end() - match.start())
                for style in item.get("inline", []):
                    for bound in ("start", "end"):
                        if style[bound] >= match.end():
                            style[bound] += delta
                        elif style[bound] > match.start():
                            style[bound] = match.start() + (
                                len(after) if bound == "end" else 0
                            )
            item["text"] = text
        audit.append(dict(edit, kind=kind, applied=True))
