"""Publisher PDF text with source geometry and explicit inline typography."""

import re
import statistics
from ocr import center

LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"})


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


def extract_page(page, book, number, audit):
    parts = []
    for block in page.get_text("dict")["blocks"]:
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
            and all("strong" in style["tags"] for style in inline)
            and sum(style["end"] - style["start"] for style in inline)
            >= len(text.strip())
        ):
            row["kind"] = "heading"
        if (
            number in book.get("native_quote_pages", [])
            and text.startswith("—")
            and len(text) < 160
        ):
            row["kind"] = "attribution"
        rows.append(row)
    return rows
