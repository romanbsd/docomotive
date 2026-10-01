"""Publisher PDF text with source geometry and explicit inline typography."""

import re
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
            text = ""
            inline = []
            sizes = []
            for span in line["spans"]:
                value = clean_text(span["text"])
                start = len(text)
                text += value
                tags = []
                if span["flags"] & 2 or "italic" in span["font"].lower():
                    tags.append("em")
                if span["flags"] & 16:
                    tags.append("strong")
                if span["flags"] & 1:
                    tags.append("sup")
                if tags and value:
                    inline.append({"start": start, "end": len(text), "tags": tags})
                if value.strip():
                    sizes.append(span["size"])
                if value != span["text"]:
                    audit.append(
                        {
                            "page": number,
                            "kind": "native-encoding-normalization",
                            "before": span["text"],
                            "after": value,
                            "evidence": "Nonprinting control, Unicode ligature or discretionary hyphen",
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
        }
        if sizes and all(
            any(abs(size - h) < 0.05 for h in book.get("native_heading_sizes", []))
            for size in sizes
        ):
            row["kind"] = "heading"
        elif (
            number in book.get("native_quote_pages", [])
            and sizes
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
