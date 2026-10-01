"""Small shared filesystem/provenance helpers, independent of OCR and rendering."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    return digest(Path(path).read_bytes())


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def cache_path(work, engine):
    return ROOT / (Path(work) / (engine + "-cache.txt")).read_text().strip()


def edit_pattern(edit):
    import re

    before = edit.get("before", edit.get("printed"))
    pattern = re.escape(before)
    if edit.get("whole_word", "printed" in edit):
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
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
            item["text"] = text
        audit.append(dict(edit, kind=kind, applied=True))
