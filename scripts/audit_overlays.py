"""Report possible redundancy and recurrence in checked correction overlays.

This tool is read-only with respect to book profiles. It compares profile
overlays with the build's correction audit and reports repeated exact changes
as candidates for human review; it never proposes or applies a shared rule.
"""

import argparse
from collections import defaultdict
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _key(record):
    return (
        record.get("page"),
        json.dumps(record.get("before"), ensure_ascii=False, sort_keys=True),
        json.dumps(record.get("after"), ensure_ascii=False, sort_keys=True),
    )


def audit_profiles(config_root=ROOT / "config", output_root=ROOT / "output"):
    occurrences = defaultdict(list)
    books = []
    for profile_path in sorted(config_root.glob("*/corrections.json")):
        slug = profile_path.parent.name
        overlays = json.loads(profile_path.read_text())
        audit_path = output_root / slug / "corrections-applied.json"
        if not audit_path.exists():
            books.append({"book": slug, "status": "missing-build-audit", "overlays": len(overlays)})
            continue
        events = json.loads(audit_path.read_text())
        automatic = defaultdict(list)
        for event in events:
            if event.get("kind") != "scan-verified-manual" and event.get("before") is not None and event.get("after") is not None:
                automatic[_key(event)].append(event)
        reviewed = []
        for overlay in overlays:
            key = _key(overlay)
            shared = automatic.get(key, [])
            status = "reproduced-by-shared-event" if shared else "not-reproduced"
            row = {
                "page": overlay.get("page"),
                "before": overlay.get("before"),
                "after": overlay.get("after"),
                "bbox": overlay.get("bbox"),
                "status": status,
                "shared_events": [e.get("kind") for e in shared],
            }
            reviewed.append(row)
            occurrences[(overlay.get("before"), overlay.get("after"))].append(
                {"book": slug, "page": overlay.get("page"), "bbox": overlay.get("bbox")}
            )
        books.append({"book": slug, "status": "audited", "overlays": reviewed})

    recurring = [
        {"before": before, "after": after, "occurrences": places,
         "candidate_only": True, "distinct_books": len({p["book"] for p in places})}
        for (before, after), places in sorted(occurrences.items())
        if len(places) > 1 and (len({p["book"] for p in places}) > 1 or len(places) >= 3)
    ]
    return {
        "format_version": 1,
        "purpose": "read-only review aid; no correction or profile is changed",
        "books": books,
        "recurring_exact_changes": recurring,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", type=Path, default=ROOT / "config")
    parser.add_argument("--output-root", type=Path, default=ROOT / "output")
    parser.add_argument("--report", type=Path, required=True, help="JSON report destination")
    args = parser.parse_args()
    report = audit_profiles(args.config_root, args.output_root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
