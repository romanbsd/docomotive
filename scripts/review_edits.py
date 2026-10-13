"""Validate occurrence-reviewed exports and create exact correction overlays."""

import argparse
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from common import apply_edits, digest, file_digest, load_profile, read_json, write_json
from book_model import row_id


def checked_overlays(payload, pages, source_hash, model_hash, profile_hash, kind="ocr"):
    if (
        not isinstance(payload, dict)
        or payload.get("format_version") != 2
        or payload.get("source_sha256") != source_hash
        or payload.get("model_sha256") != model_hash
        or payload.get("profile_sha256") != profile_hash
    ):
        raise ValueError(
            "Review source/model/profile binding is stale or unsupported; regenerate the sheet"
        )
    if kind not in {"ocr", "editorial"}:
        raise ValueError("Unknown correction kind")
    # IDs are assigned during reconstruction and need not be serialized in pages.
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("Review export needs a decisions array")
    rows = [dict(r) for rs in pages.values() for r in rs]
    row_index = defaultdict(list)
    for row in rows:
        row.setdefault("row_id", row_id(row["page"], row, source_hash))
        row_index[row["page"], row["row_id"]].append(row)
    selected = {}
    decision = "ocr-error" if kind == "ocr" else "printed-typo"
    for record in decisions:
        if not isinstance(record, dict) or record.get("decision") not in {
            "keep",
            "ocr-error",
            "printed-typo",
            "uncertain",
        }:
            raise ValueError("Unsupported review decision")
        if record.get("decision") != decision:
            continue
        if record.get("reviewed") is not True:
            raise ValueError(
                "Each correction needs an explicit checked-source acknowledgement"
            )
        binding = record.get("binding")
        replacement = record.get("replacement_or_note")
        if (
            not isinstance(replacement, str)
            or not isinstance(record.get("id"), str)
            or not record["id"]
        ):
            raise ValueError("Correction needs an occurrence ID and a text replacement")
        after = replacement.strip()
        required = {
            "row_id",
            "page",
            "start",
            "end",
            "row_text_sha256",
            "before",
            "bbox",
        }
        if not isinstance(binding, dict) or not required <= binding.keys():
            raise ValueError("Correction needs a complete source-row binding")
        if not after or after == binding["before"]:
            raise ValueError(
                "A correction needs an exact row binding and a changed replacement"
            )
        if type(binding["page"]) != int or not isinstance(binding["row_id"], str):
            raise ValueError("Invalid source-row identity")
        matches = row_index.get((binding["page"], binding["row_id"]), [])
        if len(matches) != 1:
            raise ValueError("Reviewed source row is missing or ambiguous")
        row = matches[0]
        start, end = binding["start"], binding["end"]
        if (
            type(start) != int
            or type(end) != int
            or not 0 <= start < end <= len(row["text"])
        ):
            raise ValueError("Invalid reviewed occurrence offsets")
        if (
            digest(row["text"].encode()) != binding["row_text_sha256"]
            or row["text"][start:end] != binding["before"]
            or row["bbox"] != binding["bbox"]
        ):
            raise ValueError("Reviewed source row or occurrence changed")
        key = row["page"], row["row_id"], start, end
        if key in selected and selected[key][1] != after:
            raise ValueError("Conflicting replacements for one occurrence")
        selected[key] = (record, after, row)
    ranges = {}
    for page, ident, start, end in selected:
        for other_start, other_end in ranges.setdefault((page, ident), []):
            if start < other_end and end > other_start:
                raise ValueError("Selected corrections overlap")
        ranges[page, ident].append((start, end))
    if not selected:
        return []
    mutable = deepcopy(rows)
    by_id = {(r["page"], r["row_id"]): r for r in mutable}
    result = []
    for (page, ident, start, end), (record, after, row) in sorted(
        selected.items(), key=lambda pair: (pair[1][2]["page"], pair[0][1], -pair[0][2])
    ):
        current = by_id[page, ident]
        edit = dict(
            page=row["page"],
            before=row["text"][start:end],
            after=after,
            whole_word=True,
            prefix=current["text"][:start],
            suffix=current["text"][end:],
            count=1,
            bbox=row["bbox"],
            evidence=dict(
                review_occurrence_id=record["id"],
                source_sha256=source_hash,
                model_sha256=model_hash,
                profile_sha256=profile_hash,
                candidate_signals=record.get("candidate_signals"),
                row_id=ident,
                row_text_sha256=digest(row["text"].encode()),
                decision=decision,
                reviewed=True,
                proposal_evidence=record.get("proposal_evidence"),
            ),
        )
        # Run the same checked-edit and inline-offset machinery as the build.
        apply_edits(
            mutable, [edit], [], "reviewed-ocr" if kind == "ocr" else "editorial"
        )
        if kind == "editorial":
            edit["printed"] = edit.pop("before")
            edit["proposal"] = edit.pop("after")
        result.append(edit)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--book-output", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=["ocr", "editorial"], default="ocr")
    args = parser.parse_args()
    book = load_profile(args.profile)
    source_hash = file_digest(args.source)
    if source_hash != book["source_sha256"]:
        raise ValueError("Review source file does not match profile")
    edits = checked_overlays(
        read_json(args.input),
        read_json(args.book_output / "corrected-pages.json"),
        source_hash,
        file_digest(args.book_output / "book-model.json"),
        file_digest(args.profile),
        args.kind,
    )
    write_json(args.output, edits)
    print(
        f"{len(edits)} checked {args.kind} overlays written; merge after existing profile edits, in this order"
    )


if __name__ == "__main__":
    main()
