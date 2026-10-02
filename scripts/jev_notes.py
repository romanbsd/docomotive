"""Optional citation-context ranking of source-located, ambiguous note references."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from common import load_profile
from jev_rank import MODEL, evaluate


def prepare(model, analysis, book):
    state, questions = {}, {}
    for section in book.get("endnote_sections", []):
        chapter = section["source_chapter"]
        missing = analysis["missing_by_chapter"].get(str(chapter), [])
        note_blocks = []
        active = False
        for ch in model:
            for block in ch["blocks"]:
                if block["kind"] == "heading":
                    active = block["text"] == section["heading"]
                elif active:
                    note_blocks.append(block)
        for number in missing:
            candidates = [
                c
                for c in analysis["candidates"]
                if c["chapter"] == chapter
                and c["status"] == "sequence-ambiguous"
                and any(o["number"] == number for o in c["options"])
            ]
            # Context can break a tie, but cannot invent a digit or source location.
            if len(candidates) < 2:
                continue
            notes = [
                b["text"] for b in note_blocks if re.match(rf"^{number}\.\s", b["text"])
            ]
            if len(notes) != 1:
                continue
            name = f"chapter_{chapter}_note_{number}"
            entries = {}
            criteria = {
                "uncertain": "The supplied citation and contexts do not establish one location."
            }
            for i, candidate in enumerate(candidates):
                blocks = [
                    b
                    for ch in model
                    if ch["chapter"] == chapter
                    for b in ch["blocks"]
                    if any(
                        s["page"] == candidate["page"]
                        and s["text"] == candidate["text"]
                        for s in b.get("sources", [])
                    )
                ]
                if len(blocks) != 1:
                    continue
                option = f"location_{i}"
                entries[option] = dict(
                    page=candidate["page"],
                    marker_text=candidate["text"],
                    paragraph=blocks[0]["text"],
                    readings=candidate["readings"],
                )
                criteria[option] = (
                    "This paragraph is the passage supported by the supplied endnote."
                )
            if len(entries) < 2:
                continue
            state[name] = dict(endnote=notes[0], locations=entries)
            questions[name] = dict(
                type="choice",
                criteria=criteria,
                instructions="Select the source-located reference whose paragraph the endnote supports. "
                "Use only supplied citation evidence. OCR digits agree but their placement is ambiguous. "
                "You cannot inspect images. Do not infer missing numbers. Choose uncertain if inconclusive.",
            )
    return dict(model=MODEL, state=state, questions=questions)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--input", type=Path, required=True, help="Book output directory")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--cache", type=Path, default=Path("work/jev"))
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    payload = prepare(
        json.loads((a.input / "book-model.json").read_text()),
        json.loads((a.input / "scanned-endnote-analysis.json").read_text()),
        load_profile(a.profile),
    )
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    result = dict(
        request_sha256=fingerprint,
        request=payload,
        sent=False,
        action="review proposal only; source and chapter-order checks remain required",
    )
    if not a.dry_run and payload["questions"]:
        _, response = evaluate(payload, a.cache)
        result.update(response=response, sent=True)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                questions=len(payload["questions"]),
                sent=result["sent"],
                choices={
                    k: v["choice"]
                    for k, v in result.get("response", {}).get("answers", {}).items()
                },
            )
        )
    )


if __name__ == "__main__":
    main()
