"""Publisher PDF text with source geometry and explicit inline typography."""

import re
import statistics
from ocr import center

LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"})
LIST_MARKER = re.compile(r"^(?:[•▪●◦]|\d{1,3}[.)])\s+")


def attach_list_layout(rows):
    """Preserve visible markers; source geometry bounds hanging continuations."""
    active = None
    for row in rows:
        marker = LIST_MARKER.match(row["text"])
        if marker and row.get("list_text_left") is not None:
            row["kind"] = "list-item"
            active = row
        elif active and not marker:
            # A wrapped item aligns with its text, not its marker. Allow small
            # coordinate noise, but reject body margins, font changes and gaps.
            wraps = (
                row["column"] == active["column"]
                and abs(row["bbox"][0] - active["list_text_left"]) <= 0.008
                and abs(row["font_size"] - active["font_size"]) < 0.15
                and -0.005 <= row["bbox"][1] - active["bbox"][3] <= 0.02
                and row.get("kind") != "heading"
            )
            if wraps:
                row["kind"] = "list-item"
                row["list_continuation"] = True
                row["list_text_left"] = active["list_text_left"]
                active = row
            else:
                active = None
        else:
            active = None


def clean_text(text):
    text = (
        re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
        .replace("\t", " ")
        .translate(LIGATURES)
    )
    # A discretionary hyphen inside a line is invisible typography. At the
    # line end it explicitly marks a word continued on the following line.
    return (
        text[:-1].replace("\u00ad", "") + "\u00ad"
        if text.endswith("\u00ad")
        else text.replace("\u00ad", "")
    )


def extract_page(page, book, number, audit, *, text_data=None):
    parts = []
    # Callers inspecting the same page can share its extracted text and spans.
    data = text_data if text_data is not None else page.get_text("dict")
    for block in data["blocks"]:
        for line in block.get("lines", []):
            if any(
                span["font"] in book.get("native_excluded_fonts", [])
                for span in line["spans"]
            ):
                audit.append(
                    {
                        "page": number,
                        "kind": "excluded-native-annotation",
                        "before": "".join(s["text"] for s in line["spans"]),
                        "evidence": "Profile-verified publisher download watermark",
                    }
                )
                continue
            text = ""
            inline = []
            sizes = []
            for span in line["spans"]:
                value = clean_text(span["text"])
                for before, after in book.get("native_typography", {}).items():
                    value = value.replace(before, after)
                start = len(text)
                text += value
                tags = list(book.get("native_font_styles", {}).get(span["font"], []))
                if span["flags"] & 2 or "italic" in span["font"].lower():
                    tags.append("em")
                if span["flags"] & 16:
                    tags.append("strong")
                if (
                    span["flags"] & 1
                    or (
                        book.get("native_small_numeric_sup") and value.strip().isdigit()
                    )
                ) and span["size"] <= book.get(
                    "native_superscript_max_size", float("inf")
                ):
                    tags.append("sup")
                if tags and value:
                    inline.append(
                        {
                            "start": start,
                            "end": len(text),
                            "tags": list(dict.fromkeys(tags)),
                        }
                    )
                if value.strip():
                    sizes.extend([span["size"]] * len(value.strip()))
                if value != span["text"]:
                    audit.append(
                        {
                            "page": number,
                            "kind": "native-encoding-normalization",
                            "before": span["text"],
                            "after": value,
                            "evidence": "Nonprinting control, Unicode ligature, discretionary hyphen or profile-verified typography",
                        }
                    )
            if not text.strip():
                continue
            leading = len(text) - len(text.lstrip())
            text = text.strip()
            for style in inline:
                style.update(
                    start=max(0, style["start"] - leading),
                    end=min(len(text), style["end"] - leading),
                )
            box = list(line["bbox"])
            split = book.get("index_splits", {}).get(str(number))
            parts.append(
                {
                    "text": text,
                    "bbox": [
                        box[0] / page.rect.width,
                        box[1] / page.rect.height,
                        box[2] / page.rect.width,
                        box[3] / page.rect.height,
                    ],
                    "column": int(
                        split is not None and box[0] / page.rect.width > split
                    ),
                    "inline": inline,
                    "sizes": sizes,
                }
            )
            spans = line["spans"]
            if (
                len(spans) > 1
                and spans[0]["text"].strip().isupper()
                and 3 <= len(spans[0]["text"].strip()) <= 32
                and spans[0]["size"] < 0.85 * spans[1]["size"]
            ):
                parts[-1]["label_end"] = len(clean_text(spans[0]["text"]).strip())
            if book.get("native_list_layout"):
                marker = LIST_MARKER.match(text)
                if marker:
                    # Separately positioned marker/body spans give a measured
                    # hanging indent. Single-span markers need character boxes.
                    prefix = marker.end()
                    consumed = 0
                    text_left = None
                    for span in spans:
                        value = clean_text(span["text"])
                        if consumed >= prefix and value.strip():
                            text_left = span["bbox"][0] / page.rect.width
                            break
                        consumed += len(value)
                    if text_left is not None:
                        parts[-1]["list_text_left"] = text_left
    # Mask diagram labels before horizontal merging: otherwise a label can
    # widen a nearby prose row and make the whole row look like artwork.
    from figures import outside_figures

    parts = outside_figures(parts, book, number, audit)
    groups = []
    for part in sorted(parts, key=lambda p: (p["column"], center(p), p["bbox"][0])):
        if (
            groups
            and part["column"] == groups[-1][0]["column"]
            and abs(center(part) - center(groups[-1][0])) < 0.008
        ):
            groups[-1].append(part)
        else:
            groups.append([part])
    rows = []
    for group in groups:
        text = ""
        inline = []
        sizes = []
        previous = None
        for part in sorted(group, key=lambda p: p["bbox"][0]):
            separator = (
                " "
                if previous
                and (part["bbox"][0] - previous["bbox"][2]) * page.rect.width > 0.5
                else ""
            )
            start = len(text) + len(separator)
            text += separator + part["text"]
            inline.extend(
                {**style, "start": start + style["start"], "end": start + style["end"]}
                for style in part["inline"]
                if style["end"] > style["start"]
            )
            sizes.extend(part["sizes"])
            previous = part
        row = {
            "text": text,
            "bbox": [
                min(p["bbox"][0] for p in group),
                min(p["bbox"][1] for p in group),
                max(p["bbox"][2] for p in group),
                max(p["bbox"][3] for p in group),
            ],
            "column": group[0]["column"],
            "inline": inline,
            "native": True,
            "font_size": statistics.median(sizes) if sizes else None,
        }
        ordered = sorted(group, key=lambda p: p["bbox"][0])
        if ordered[0].get("list_text_left") is not None:
            row["list_text_left"] = ordered[0]["list_text_left"]
        if ordered[0].get("label_end"):
            row["label_end"] = ordered[0]["label_end"]
        if (
            len(ordered) > 1
            and (
                ordered[0]["text"].isupper()
                or any(
                    s["start"] == 0
                    and s["end"] == len(ordered[0]["text"])
                    and "em" in s["tags"]
                    for s in ordered[0]["inline"]
                )
            )
            and ordered[1]["bbox"][0] - ordered[0]["bbox"][2] > 0.01
        ):
            row["label_end"] = len(ordered[0]["text"])
        if text in book.get("native_heading_texts", []):
            row["kind"] = "heading"
        elif sizes and all(
            any(abs(size - h) < 0.05 for h in book.get("native_heading_sizes", []))
            for size in sizes
        ):
            row["kind"] = "heading"
        elif (
            number in book.get("native_quote_pages", [])
            and sizes
            and not text.isdigit()
            and max(sizes) < book.get("native_body_size", 9.5) - 0.5
        ):
            row["kind"] = "quote"
        if (
            text
            and len(text) < 150
            and inline
            # Spaces introduced between positioned spans need no font style;
            # every actual glyph must still carry bold evidence.
            and all(
                c.isspace()
                or any(
                    "strong" in style["tags"] and style["start"] <= i < style["end"]
                    for style in inline
                )
                for i, c in enumerate(text)
            )
        ):
            row["kind"] = "heading"
        if (
            number in book.get("native_quote_pages", [])
            and text.startswith("—")
            and len(text) < 160
        ):
            row["kind"] = "attribution"
        if row.get("kind") == "quote" and book.get("native_relative_font_sizes"):
            # Relative native size respects the reader's chosen base font;
            # clamp extreme annotation sizes instead of shrinking prose away.
            row["quote_font_scale"] = min(
                1, max(0.65, row["font_size"] / book.get("native_body_size", 9.5))
            )
        if re.fullmatch(r"FIGURE\s+\d+(?:\.\d+)?", text, re.I) and any(
            f["page"] == number and 0 <= row["bbox"][1] - f["rect"][3] <= 0.025
            for f in book.get("figures", [])
        ):
            row["kind"] = "caption"
        elif (
            rows
            and rows[-1].get("kind") == "caption"
            and (
                row["column"] == rows[-1]["column"]
                and abs(row["bbox"][0] - rows[-1]["bbox"][0]) < 0.01
                and 0 <= row["bbox"][1] - rows[-1]["bbox"][3] < 0.01
                and row["font_size"] <= rows[-1]["font_size"] + 0.1
            )
        ):
            # A small, aligned line directly under a figure label is its
            # caption continuation; larger or separated prose ends the run.
            row["kind"] = "caption"
        rows.append(row)
    if book.get("native_list_layout"):
        # Only measured multi-span markers participate; prose numbers in one
        # font span do not provide enough evidence for an item boundary.
        candidates = [r for r in rows if r.get("list_text_left") is not None]
        if candidates:
            attach_list_layout(rows)
    return rows
