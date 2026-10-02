"""Source-linked correction, book layout reconstruction, deterministic EPUB 3."""

import argparse
import html
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from lxml import etree
import pymupdf
from layout import infer
from common import ROOT, digest, write_json, apply_edits, cache_path, load_profile
from book_model import (
    JoinPolicy,
    join,
    page_blocks,
    reconstruct,
    coverage,
    plain_text,
    classify_row,
)
from render_text import block_html, notes_html
from ocr import center, merge_rows, embedded, correct_page
from ocr_cache import preflight_cache
from profile_validation import validate_profile
from review_decisions import match_decisions

CSS = """body {font-family:serif; line-height:1.45; margin:5%;}
h1 {font-size:1.6em; text-align:center; margin:2em 0; font-weight:normal;}
h2 {font-size:1.15em; margin:1.5em 0 .7em;}
p {margin:0; text-indent:1.25em; orphans:2; widows:2;}
h1+p, .noindent {text-indent:0;}
.title {text-align:center; margin-top:15%;} .title p {text-indent:0;margin:1em 0;}
blockquote {margin:1em 1.5em;} .verse {margin:1em 1.5em;} .verse p {text-indent:0;}
.notes {font-size:.9em; border-top:1px solid; margin-top:2em; padding-top:1em;}
.notes aside {margin:1em 0;} .notes p {text-indent:0;}
.index p, .reference {text-indent:-1.25em; margin:0 0 .4em 1.25em;}
img {max-width:100%; height:auto;} .facsimile {text-align:center; margin:0;}
.publisher-mark {width:4em;} a {text-decoration:none;} sup {font-size:.75em;}
.smallcaps {font-variant:small-caps;} .note-link {white-space:nowrap;}
.note-backlinks {font-size:.75em; text-indent:0; margin:.2em 0 .8em;}
.attribution {text-align:right; text-indent:0; margin:.3em 1.5em .8em;}
figure {margin:1.2em 0 .3em; text-align:center; break-inside:avoid;}
.narrow img {max-height:16em; width:auto;}
.narrow {float:right; width:2.5em; margin:.5em 0 .5em 1em;}
.caption {font-size:.85em; text-indent:0; margin:0 0 1.2em;}
"""


def xhtml(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">'
        f'<head><title>{html.escape(title)}</title><link rel="stylesheet" href="style.css"/></head><body>{body}</body></html>'
    ).encode()


def opf_metadata(book, enrichment, uid):
    opf = "http://www.idpf.org/2007/opf"
    dc = "http://purl.org/dc/elements/1.1/"
    root = etree.Element("{" + opf + "}metadata", nsmap={None: opf, "dc": dc})

    def field(name, value, **attrs):
        node = etree.SubElement(root, "{" + dc + "}" + name, **attrs)
        node.text = str(value)
        return node

    def meta(prop, value, **attrs):
        node = etree.SubElement(root, "{" + opf + "}meta", property=prop, **attrs)
        node.text = str(value)

    field("identifier", uid, id="book-id")
    field(
        "title",
        book["title"] + (": " + book["subtitle"] if book.get("subtitle") else ""),
        id="title",
    )
    meta("title-type", "main", refines="#title")
    field("creator", book["author"], id="author")
    meta("role", "aut", refines="#author", scheme="marc:relators")
    if book.get("author_sort"):
        meta("file-as", book["author_sort"], refines="#author")
    for i, contributor in enumerate(book.get("contributors", []), 1):
        identifier = f"contributor-{i}"
        field("contributor", contributor["name"], id=identifier)
        meta(
            "role",
            contributor["role"],
            refines="#" + identifier,
            scheme="marc:relators",
        )
        if contributor.get("file_as"):
            meta("file-as", contributor["file_as"], refines="#" + identifier)
    field("language", book["language"])
    field("date", enrichment.get("publication_date", book["date"]))
    field("publisher", book["publisher"])
    field("type", "Text")
    field("format", "application/epub+zip")
    if book.get("rights"):
        field("rights", book["rights"])
    if enrichment.get("description"):
        field("description", enrichment["description"])
    if book.get("source_note"):
        field("description", book["source_note"])
    for subject in enrichment.get("subjects", []):
        field("subject", subject)
    if enrichment.get("isbn_13"):
        field("identifier", "urn:isbn:" + enrichment["isbn_13"], id="source-isbn")
        meta("identifier-type", "15", refines="#source-isbn", scheme="onix:codelist5")
        field(
            "source",
            "Print source ISBN "
            + enrichment["isbn"]
            + "; ISBN-13 "
            + enrichment["isbn_13"],
        )
    if book.get("doi"):
        field("identifier", "https://doi.org/" + book["doi"])
        field("source", book.get("source_version", "Article"))
    if enrichment.get("journal"):
        field(
            "source",
            enrichment["journal"]
            + "; volume "
            + enrichment.get("volume", "")
            + ", issue "
            + enrichment.get("issue", "")
            + "; pages "
            + enrichment.get("page", "")
            + "; final publication "
            + enrichment.get("final_publication_date", ""),
        )
    for kind, values in enrichment.get("identifiers", {}).items():
        if kind.startswith("isbn"):
            continue
        for value in values:
            field("source", kind.upper() + ": " + value)
    for kind, values in enrichment.get("classifications", {}).items():
        for value in values:
            field("subject", kind + ": " + value)
    for url in enrichment.get("links", []):
        field("relation", url)
    if book.get("archive_identifier"):
        field("source", "https://archive.org/details/" + book["archive_identifier"])
    if enrichment.get("publication_places"):
        field(
            "source",
            "Print publication place: " + ", ".join(enrichment["publication_places"]),
        )
    if enrichment.get("print_pages"):
        field("source", f"Print edition extent: {enrichment['print_pages']} pages")
    for related in book.get("related_print_isbns", []):
        field("source", "Related " + related["binding"] + " ISBN: " + related["isbn"])
    meta("dcterms:modified", "2000-01-01T00:00:00Z")
    return etree.tostring(root, encoding="unicode")


def validate_epub(path):
    errors = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        ids = {}
        if (
            z.infolist()[0].filename != "mimetype"
            or z.infolist()[0].compress_type != zipfile.ZIP_STORED
        ):
            errors.append("mimetype must be first and stored")
        for name in names:
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                root = etree.fromstring(z.read(name))
                found = root.xpath("//@id")
                ids[name] = set(found)
                if len(found) != len(ids[name]):
                    errors.append(f"{name}: duplicate IDs")
        for name in names:
            if not name.endswith((".xhtml", ".opf", ".ncx")):
                continue
            root = etree.fromstring(z.read(name))
            for ref in root.xpath("//@href|//@src"):
                u = urlsplit(ref)
                if u.scheme:
                    continue
                target = str(Path(name).parent / unquote(u.path)) if u.path else name
                if target not in names:
                    errors.append(f"{name}: missing {target}")
                elif u.fragment and u.fragment not in ids.get(target, set()):
                    errors.append(f"{name}: missing fragment {ref}")
    if errors:
        raise ValueError("\n".join(errors))
    return {"xml_and_internal_links": "passed", "zip_mimetype": "passed"}


def build(
    pdf,
    profile=ROOT / "config/book.json",
    out=ROOT / "output",
    work=ROOT / "work",
    editorial=False,
    models=ROOT / "work/models",
):
    book = load_profile(profile)
    config = profile.parent
    source_hash = digest(pdf.read_bytes())
    if source_hash != book["source_sha256"]:
        raise ValueError("This book profile does not match the source PDF")
    enrichment = (
        json.loads((config / "enrichment.json").read_text())
        if (config / "enrichment.json").exists()
        else {}
    )
    if enrichment and enrichment["source_sha256"] != source_hash:
        raise ValueError("Metadata enrichment belongs to a different PDF")
    if enrichment and enrichment.get("isbn") != book.get("isbn"):
        raise ValueError("ISBN profile changed; rerun metadata enrichment")
    if enrichment.get("doi") and enrichment["doi"] != book.get("doi"):
        raise ValueError("DOI profile changed; rerun metadata enrichment")
    out.mkdir(parents=True, exist_ok=True)
    files = {"OEBPS/style.css": CSS.encode()}
    doc = pymupdf.open(pdf)
    validate_profile(book, profile, page_count=len(doc))
    audit = []
    review = []
    pages = {}
    native = book.get("text_source") == "native"
    provenance_records = []
    if native:
        from native_pdf import extract_page

        pages = {
            n: extract_page(doc[n - 1], book, n, audit) for n in range(1, len(doc) + 1)
        }
        provenance_records = [
            {
                "source_sha256": source_hash,
                "engine": "native-pdf",
                "pymupdf": pymupdf.VersionBind,
            }
        ]
    else:
        cache = cache_path(work, "tesseract")
        vision = cache_path(work, "vision")
        secondary = cache_path(work, "rapid")
        for p in [cache, vision, secondary]:
            preflight_cache(p, len(doc), source_hash)
        for n in range(1, len(doc) + 1):
            v = json.loads((cache / f"{n:04}.json").read_text())
            r = json.loads((secondary / f"{n:04}.json").read_text())
            vv = json.loads((vision / f"{n:04}.json").read_text())
            if str(n) in book.get("index_splits", {}):
                rect = doc[n - 1].rect
                split = book["index_splits"][str(n)] * rect.width
                expected = [
                    [0, 0, split, rect.height],
                    [split, 0, rect.width, rect.height],
                ]
                for data in [v, r, vv]:
                    if data.get("clips") != expected:
                        raise ValueError(
                            f"OCR column geometry changed on page {n}; rerun extraction"
                        )
            # Vision handles separate reference numbers and index entries more reliably.
            primary_name = (
                "vision" if n in book.get("vision_primary_pages", []) else "tesseract"
            )
            if primary_name == "vision":
                v, vv = vv, v
            from figures import outside_figures

            pages[n] = correct_page(
                outside_figures(v["lines"], book, n, audit),
                merge_rows(
                    outside_figures(
                        embedded(
                            doc[n - 1],
                            book.get("index_splits", {}).get(str(n)),
                            raw=True,
                        ),
                        book,
                        n,
                    )
                ),
                merge_rows(outside_figures(vv["lines"], book, n)),
                merge_rows(outside_figures(r["lines"], book, n)),
                audit,
                review,
                n,
                primary_name,
                "tesseract" if primary_name == "vision" else "vision",
                recover_regions=book.get("recover_ocr_regions", False),
            )
        provenance_records = [
            json.loads((p / "provenance.json").read_text())
            for p in [cache, vision, secondary]
        ]
        # Report the engines actually fingerprinted, not remembered versions.
        engine_names = {
            name: record.get(name, record["engine"])
            for name, record in zip(["tesseract", "vision"], provenance_records)
        }
    # Add page scope directly to the source rows before checked overlays.
    for n, rows in pages.items():
        for row in rows:
            row["page"] = n
    apply_edits(
        [row for rows in pages.values() for row in rows],
        json.loads((config / "corrections.json").read_text()),
        audit,
        "scan-verified-manual",
    )
    for n, rows in pages.items():
        if not native and book.get("recover_pdf_typography", False):
            from typography import attach_typography

            attach_typography(rows, doc[n - 1], n, audit)
        for row in rows:
            for rule in book.get("row_heading_rules", []):
                if n in rule["pages"] and re.fullmatch(rule["pattern"], row["text"]):
                    row["kind"] = "heading"
    if not native and book.get("recover_scanned_endnotes", False):
        from scanned_notes import recover_endnotes

        recovered = recover_endnotes(pages, doc, book, work, source_hash, audit, review)
        write_json(out / "scanned-endnote-analysis.json", recovered)
    if not native and book.get("recover_scan_font_metrics", False):
        from typography import attach_scan_font_metrics

        for n, rows in pages.items():
            attach_scan_font_metrics(rows, doc[n - 1], n, audit)
    write_json(out / "corrected-pages.json", pages)
    layout = infer(pages)
    write_json(out / "layout-analysis.json", layout)
    book["_layout"] = {
        "excluded_rows": [r for g in layout["headers"] for r in g["rows"]]
        + layout["footers"]
    }
    # Keep original artwork, not invented illustrations.
    for n, name in book["artwork_pages"]:
        files[f"OEBPS/{name}.jpg"] = (
            doc[n - 1].get_pixmap(dpi=200).pil_tobytes(format="JPEG", quality=90)
        )
    downloaded_cover = None
    if book.get("cover_file"):
        from metadata import cover_info

        cover = book["cover_file"]
        data = (ROOT / cover["path"]).read_bytes()
        info = cover_info(data)
        if info["sha256"] != cover["sha256"] or info["format"] != "JPEG":
            raise ValueError("Supplied cover checksum or format changed")
        files["OEBPS/cover.jpg"] = data
    for candidate in enrichment.get("covers", []):
        if not candidate.get("accepted"):
            continue
        if candidate["url"] != book.get("approved_cover_url"):
            raise ValueError("Cover approval changed; rerun metadata enrichment")
        asset = ROOT / candidate["path"]
        data = asset.read_bytes()
        if digest(data) != candidate["sha256"]:
            raise ValueError("Downloaded cover checksum mismatch")
        suffix = asset.suffix
        downloaded_cover = "downloaded-cover" + suffix
        files["OEBPS/" + downloaded_cover] = data
        if candidate["suitable_main_cover"] and suffix == ".jpg":
            files["OEBPS/cover.jpg"] = data
        break
    publisher_image = ""
    if book.get("publisher_mark"):
        files["OEBPS/publisher-mark.png"] = (
            doc[book["publisher_mark"]["page"] - 1]
            .get_pixmap(dpi=300, clip=pymupdf.Rect(book["publisher_mark"]["rect"]))
            .tobytes("png")
        )
        publisher_image = '<img class="publisher-mark" src="publisher-mark.png" alt="Publisher mark"/>'
    spine = []
    navigation = []
    section_navigation = {}
    page_links = []
    model = []

    def add(name, title, body, nav=True):
        files["OEBPS/" + name] = xhtml(title, body)
        spine.append(name)
        if nav:
            navigation.append((name, title))

    cover_name = "cover.jpg"
    if book.get("cover_source") == "typographic":
        from title_cover import svg_cover

        cover_name = "cover.svg"
        files["OEBPS/" + cover_name] = svg_cover(book)
    title_reference = "".join(
        f'<sup><a epub:type="noteref" id="{r["href"].split("#")[1]}" href="chapter-{book["endnote_chapter"]:02}.xhtml#endnote-{r["source_chapter"]}-{r["number"]}">{r["number"]}</a></sup>'
        for r in book.get("frontmatter_endnotes", [])
    )
    add(
        "cover.xhtml",
        "Cover",
        f'<div class="facsimile"><img src="{cover_name}" alt="Cover"/></div>',
    )
    add(
        "title.xhtml",
        book["title"],
        f'<div class="title"><h1>{html.escape(book["title"])}</h1><p>{html.escape(book["subtitle"])}{title_reference}</p><p>{html.escape(book["author"])}</p>{publisher_image}<p>{html.escape(book["publisher"])}</p></div>',
    )
    add(
        "copyright.xhtml",
        "Copyright and permissions",
        "<h1>Copyright and permissions</h1>" + (config / "copyright.xhtml").read_text(),
    )
    if book.get("source_note"):
        add(
            "edition-note.xhtml",
            "About this edition",
            "<h1>About this edition</h1><p>"
            + html.escape(book["source_note"])
            + "</p>",
        )
    if downloaded_cover:
        files["OEBPS/downloaded-cover.xhtml"] = xhtml(
            "Online source cover",
            f'<div class="facsimile"><img src="{downloaded_cover}" alt="Downloaded cover of the matching source edition"/></div>',
        )
        files["OEBPS/cover.xhtml"] = xhtml(
            "Cover",
            '<div class="facsimile"><img src="cover.jpg" alt="Original book cover"/></div><p class="noindent" style="text-align:center;font-size:.75em;margin-top:1em"><a href="downloaded-cover.xhtml">Source cover reference</a></p>',
        )
    from spylls.hunspell import Dictionary

    protected = [
        w
        for w in (config / "protected-words.txt").read_text().splitlines()
        if w and not w.startswith("#")
    ]
    observed = [
        word.lower()
        for rows in pages.values()
        for row in rows
        for word in re.findall(r"[\w]+(?:['’-][\w]+)*", row["text"])
    ]
    dictionary = (
        Dictionary.from_files(str(models / "en_US"))
        if (models / "en_US.aff").exists()
        else None
    )
    policy = JoinPolicy(
        protected + book.get("line_join_words", []),
        observed,
        dictionary,
        observed_min_count=1 if native else 2,
    )
    editorial_edits = (
        json.loads((config / "editorial-proposals.json").read_text())
        if editorial
        else []
    )
    model = reconstruct(pages, book, policy, audit, editorial_edits)
    from figures import incorporate_figures

    figure_report = incorporate_figures(model, book, doc, files)
    from apparatus import link_endnotes

    apparatus = link_endnotes(model, book)
    write_json(out / "apparatus-analysis.json", apparatus)
    text_coverage = coverage(pages, book, model)
    write_json(out / "text-coverage.json", text_coverage)
    note_page_refs = {}
    ambiguous_note_refs = set()
    for chapter in model:
        for block in chapter["blocks"]:
            if block.get("endnote"):
                number = block["id"].rsplit("-", 1)[1]
                for source in block["sources"]:
                    key = (
                        str(source["page"] + book.get("printed_page_offset", 0))
                        + ":"
                        + number
                    )
                    ref = f'chapter-{chapter["chapter"]:02}.xhtml#{block["id"]}'
                    if key in note_page_refs and note_page_refs[key] != ref:
                        ambiguous_note_refs.add(key)
                    note_page_refs[key] = ref
    for key in ambiguous_note_refs:
        note_page_refs.pop(key, None)
    for chapter in model:
        start, end = chapter["source_pages"]
        name = f'chapter-{chapter["chapter"]:02}.xhtml'
        seen = set()
        render_book = dict(
            book,
            _note_targets=chapter["note_targets"],
            _index_note_refs=note_page_refs,
            _hanging=start
            in book.get("hanging_pages", book.get("reference_pages", [])),
        )
        body = (
            ["<h1>" + html.escape(chapter["title"]) + "</h1>"]
            if book.get("chapter_heading", True)
            else []
        )
        if book.get("endnote_reference_mode") == "chapter":
            if body:
                body[0] = body[0].replace("<h1>", '<h1 id="chapter-start">', 1)
            if any(
                s["source_chapter"] == chapter["chapter"]
                for s in book["endnote_sections"]
            ):
                body.append(
                    f'<p class="noindent"><a href="chapter-{book["endnote_chapter"]:02}.xhtml#endnote-{chapter["chapter"]}-1">Notes for this chapter</a></p>'
                )
        refs = (
            {label: ref for ref, label in page_links}
            if start in book.get("index_pages", [])
            else None
        )
        for index, block in enumerate(chapter["blocks"]):
            if book.get("section_navigation") and block["kind"] == "heading":
                block["heading_id"] = f"section-{chapter['chapter']}-{index}"
                section_navigation.setdefault(name, []).append(
                    (name + "#" + block["heading_id"], block["text"])
                )
            body.append(block_html(block, render_book, seen, page_links, name, refs))
        body.append(notes_html(chapter["notes"], book))
        if start in book.get("index_pages", []):
            body = ['<div class="index">'] + body + ["</div>"]
        add(name, chapter["title"], "".join(body))
    if any(name == "back-cover" for _, name in book["artwork_pages"]):
        add(
            "back-cover.xhtml",
            "Back cover",
            '<h1>Back cover</h1><div class="facsimile"><img src="back-cover.jpg" alt="Original back cover with reader endorsements"/></div>',
        )
    nav = (
        '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>'
        + "".join(
            f'<li><a href="{name}">{html.escape(title)}</a>'
            + (
                "<ol>"
                + "".join(
                    f'<li><a href="{ref}">{html.escape(label)}</a></li>'
                    for ref, label in section_navigation[name]
                )
                + "</ol>"
                if name in section_navigation
                else ""
            )
            + "</li>"
            for name, title in navigation
        )
        + "</ol></nav>"
    )
    nav += (
        '<nav epub:type="page-list" hidden="hidden"><h2>Original pages</h2><ol>'
        + "".join(f'<li><a href="{ref}">{label}</a></li>' for ref, label in page_links)
        + "</ol></nav>"
    )
    nav += '<nav epub:type="landmarks" hidden="hidden"><h2>Landmarks</h2><ol><li><a epub:type="bodymatter" href="chapter-01.xhtml">Start of text</a></li></ol></nav>'
    files["OEBPS/nav.xhtml"] = xhtml("Contents", nav)
    spine.insert(3, "nav.xhtml")
    if downloaded_cover:
        spine.append("downloaded-cover.xhtml")
    uid = "urn:sha256:" + source_hash
    ncx = f'<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="{uid}"/></head><docTitle><text>{html.escape(book["title"])}</text></docTitle><navMap>'
    ncx += (
        "".join(
            f'<navPoint id="nav-{i}" playOrder="{i}"><navLabel><text>{html.escape(t)}</text></navLabel><content src="{n}"/></navPoint>'
            for i, (n, t) in enumerate(navigation, 1)
        )
        + "</navMap></ncx>"
    )
    files["OEBPS/toc.ncx"] = ncx.encode()
    manifest = []
    idmap = {}
    for i, path in enumerate(sorted(files)):
        name = Path(path).name
        idmap[name] = f"item-{i}"
        media = {
            ".xhtml": "application/xhtml+xml",
            ".css": "text/css",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".ncx": "application/x-dtbncx+xml",
        }[Path(path).suffix]
        properties = (
            ' properties="nav"'
            if name == "nav.xhtml"
            else ' properties="cover-image"' if name == cover_name else ""
        )
        manifest.append(
            f'<item id="item-{i}" href="{name}" media-type="{media}"{properties}/>'
        )
    metadata = opf_metadata(book, enrichment, uid)
    files["OEBPS/content.opf"] = (
        f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
        + metadata
        + "<manifest>"
        + "".join(manifest)
        + f'</manifest><spine toc="{idmap["toc.ncx"]}">'
        + "".join(
            f'<itemref idref="{idmap[n]}"'
            + (' linear="no"' if n == "downloaded-cover.xhtml" else "")
            + "/>"
            for n in spine
        )
        + "</spine></package>"
    ).encode()
    files["META-INF/container.xml"] = (
        b'<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    target = out / (book["slug"] + ("-corrected" if editorial else "") + ".epub")
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
    write_json(out / "book-model.json", model)
    write_json(out / "corrections-applied.json", audit)
    # Remove exact duplicate review rows without hiding distinct evidence.
    review = list({json.dumps(r, sort_keys=True): r for r in review}.values())
    decisions = json.loads((config / "review-decisions.json").read_text())
    rendered = {n for a, b, _ in book["chapters"] for n in range(a, b + 1)}
    decision_bindings = match_decisions(review, decisions, source_hash)
    if decision_bindings["legacy"] or decision_bindings["issues"]:
        print(
            f"Review decisions: {len(decision_bindings['legacy'])} legacy geometry-only; "
            f"{len(decision_bindings['issues'])} unmatched, changed or uncertain",
            flush=True,
        )
    matched_decisions = decision_bindings["matched"]
    for index, row in enumerate(review):
        matches = decision_bindings["matches"].get(index, [])
        verified = bool(matches)
        if verified:
            row["review_binding"] = (
                "source-and-text"
                if any(i not in decision_bindings["legacy"] for i in matches)
                else "legacy-geometry-only"
            )
        row["status"] = (
            "scan-reviewed"
            if verified
            else (
                "not-rendered"
                if row["page"] not in rendered
                or classify_row(
                    row["page"],
                    {
                        "text": row.get("primary", row.get("embedded", "")),
                        "bbox": row["bbox"],
                    },
                    book,
                    row["page"] in {a for a, _, _ in book["chapters"]},
                )[0]
                == "excluded"
                else "needs-review"
            )
        )
    write_json(out / "ocr-comparison.json", review)
    review = [r for r in review if r["status"] == "needs-review"]
    write_json(out / "review-queue.json", review)
    (out / (book["slug"] + ".txt")).write_text(plain_text(model))
    preview = out / "preview"
    preview.mkdir(exist_ok=True)
    for obsolete in [
        "copyright-scan.jpg",
        "copyright-facsimile.xhtml",
        "title-scan.jpg",
    ]:
        (preview / obsolete).unlink(missing_ok=True)
    for name, data in files.items():
        if name.startswith("OEBPS/"):
            (preview / Path(name).name).write_bytes(data)
    result = {
        "source_sha256": source_hash,
        "epub_sha256": digest(target.read_bytes()),
        "source_pages": len(doc),
        "figures": figure_report,
        "apparatus": apparatus,
        "ambiguous_index_note_references": sorted(ambiguous_note_refs),
        "chapter_count": len(book["chapters"]),
        "text_coverage": {
            "status": text_coverage["status"],
            "counts": text_coverage["counts"],
        },
        "original_page_anchors": len(page_links),
        "correction_events": len(audit),
        "review_items": len(review),
        # Geometry-matched decisions go stale when OCR is rerun; list them so
        # reviewed rows cannot silently drop back into, or out of, the queue.
        "unmatched_review_decisions": [
            {k: d[k] for k in ("page", "bbox") if k in d}
            for i, d in enumerate(decisions)
            if i not in matched_decisions
        ],
        "review_decision_issues": decision_bindings["issues"],
        "legacy_review_decisions": [
            dict(
                index=i,
                page=decisions[i]["page"],
                reason="geometry-only; source/text not bound",
            )
            for i in decision_bindings["legacy"]
        ],
        "editorial_corrections": editorial,
        "metadata_enrichment": enrichment,
        "validation": validate_epub(target),
        "excluded_pages": book["excluded_pages"],
        "ocr_provenance": provenance_records,
        "limitations": [
            "OCR disagreements require review; automated correction is not full proofreading.",
            "Original inline italics and superscript reference typography are not fully recovered.",
            "Back cover is preserved as a facsimile; copyright has checked reflow text.",
        ],
        "build_inputs_sha256": {
            str(p): digest(p.read_bytes())
            for p in sorted(
                list((ROOT / "scripts").glob("*.py")) + list(config.glob("*"))
            )
            if p.is_file()
        },
        "primary_engines": (
            {
                "body": "Publisher PDF native text",
                "bibliography_and_index": "Publisher PDF native text",
            }
            if native
            else {
                "body": (
                    engine_names["vision"]
                    if all(
                        n in book.get("vision_primary_pages", [])
                        for a, b, _ in book["chapters"]
                        for n in range(a, b + 1)
                    )
                    else engine_names["tesseract"]
                ),
                "bibliography_and_index": engine_names["vision"],
            }
        ),
    }
    if native:
        result["limitations"] = [
            "Native extraction and format normalization are not full proofreading.",
            "Quoted and heading roles use reviewed profile/font geometry; original pagination changes with reflow.",
        ]
    if book.get("source_completeness"):
        result["source_completeness"] = book["source_completeness"]
    write_json(out / "report.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--profile", type=Path, default=ROOT / "config/book.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    parser.add_argument("--work", type=Path, default=ROOT / "work")
    parser.add_argument("--models", type=Path, default=ROOT / "work/models")
    parser.add_argument(
        "--editorial",
        action="store_true",
        help="Apply scan-verified printed typo corrections as a separate edition",
    )
    args = parser.parse_args()
    build(args.pdf, args.profile, args.output, args.work, args.editorial, args.models)
