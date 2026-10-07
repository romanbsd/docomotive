"""Rebuild cached book cases and compare reviewable, explicitly accepted baselines."""

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from common import ROOT, digest, file_digest, load_profile, read_json, write_json


def json_digest(value):
    return digest(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    )


def snapshot(output, book, editorial=False):
    """Exclude volatile reports; include text, geometry, links and all EPUB media."""
    output = Path(output)
    model = read_json(output / "book-model.json")
    coverage = read_json(output / "text-coverage.json")
    if coverage.get("status") != "passed":
        raise ValueError("Source-row coverage did not pass")
    stem = book["slug"] + ("-corrected" if editorial else "")
    epub = output / f"{stem}.epub"
    with zipfile.ZipFile(epub) as archive:
        members = {
            name: digest(archive.read(name)) for name in sorted(archive.namelist())
        }
    artifacts = {
        name: json_digest(read_json(output / name))
        for name in (
            "book-model.json",
            "corrected-pages.json",
            "text-coverage.json",
            "apparatus-analysis.json",
        )
    }
    artifacts[f'{book["slug"]}.txt'] = file_digest(output / f'{book["slug"]}.txt')
    if book.get("repair_lexical_confusions"):
        artifacts["lexical-repair.json"] = json_digest(
            read_json(output / "lexical-repair.json")
        )
    return dict(
        source_sha256=book["source_sha256"],
        slug=book["slug"],
        editorial=editorial,
        artifacts=artifacts,
        epub_sha256=file_digest(epub),
        epub_members=members,
        coverage=coverage["counts"],
        chapters=[
            dict(title=c["title"], sha256=json_digest(c), blocks=len(c["blocks"]))
            for c in model
        ],
        noterefs=sum(
            bool(s.get("noteref"))
            for c in model
            for b in c["blocks"]
            for s in b.get("inline", [])
        ),
        backlinks=sum(len(b.get("backlinks", [])) for c in model for b in c["blocks"]),
        figures=sorted(
            {b["image"] for c in model for b in c["blocks"] if b.get("image")}
        ),
    )


def differences(before, after, path=""):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            location = f"{path}.{key}" if path else key
            if key not in before or key not in after:
                result.append(
                    dict(path=location, before=before.get(key), after=after.get(key))
                )
            else:
                result.extend(differences(before[key], after[key], location))
        return result
    return [] if before == after else [dict(path=path, before=before, after=after)]


def load_cases(path):
    cases = read_json(path)
    if not isinstance(cases, list) or not cases:
        raise ValueError("Regression manifest must be a nonempty list")
    names = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "name",
            "pdf",
            "profile",
            "work",
            "editorial",
        }:
            raise ValueError("Each case needs name, pdf, profile, work, editorial")
        if (
            not isinstance(case["name"], str)
            or not case["name"]
            or any(
                c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in case["name"]
            )
        ):
            raise ValueError("Case names must be filename-safe lowercase identifiers")
        if case["name"] in names:
            raise ValueError(f'Duplicate case: {case["name"]}')
        names.add(case["name"])
        if type(case["editorial"]) is not bool:
            raise ValueError("editorial must be boolean")
        for key in ("pdf", "profile", "work"):
            if not isinstance(case[key], str) or not case[key]:
                raise ValueError(f"{key} must be a nonempty path")
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/book-cases.json")
    parser.add_argument(
        "--baseline", type=Path, default=ROOT / "tests/baselines/books.json"
    )
    parser.add_argument("--run-root", type=Path, default=ROOT / "work/regression")
    parser.add_argument("--models", type=Path, default=ROOT / "work/models")
    parser.add_argument(
        "--book", action="append", help="Case name; repeat to select cases"
    )
    parser.add_argument(
        "--accept-baseline",
        action="store_true",
        help="Explicitly replace selected baseline entries after all builds pass",
    )
    args = parser.parse_args()
    args.run_root = args.run_root.resolve()
    args.baseline = args.baseline.resolve()
    cases = load_cases(args.manifest)
    if args.book:
        unknown = set(args.book) - {c["name"] for c in cases}
        if unknown:
            parser.error(f"Unknown cases: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["name"] in args.book]
    baseline = (
        read_json(args.baseline)
        if args.baseline.exists()
        else {"format": 1, "cases": {}}
    )
    if baseline.get("format") != 1 or not isinstance(baseline.get("cases"), dict):
        parser.error("Unsupported baseline format")
    if not args.accept_baseline and any(
        c["name"] not in baseline["cases"] for c in cases
    ):
        parser.error(
            "Missing baseline entries; use --accept-baseline only after reviewing the intended output"
        )
    args.run_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=args.run_root))
    results = []
    observations = {}
    for case in cases:
        name = case["name"]
        output = run / name
        log = run / f"{name}.log"
        row = dict(name=name, log=str(log), output=str(output))
        try:
            book = load_profile(ROOT / case["profile"])
            command = [
                sys.executable,
                str(ROOT / "scripts/build.py"),
                str(ROOT / case["pdf"]),
                "--profile",
                str(ROOT / case["profile"]),
                "--work",
                str(ROOT / case["work"]),
                "--output",
                str(output),
                "--models",
                str(args.models.resolve()),
            ]
            if case["editorial"]:
                command.append("--editorial")
            # build.py consumes existing full-page caches; this runner never
            # calls extraction, downloads metadata or changes published books.
            with log.open("w") as stream:
                subprocess.run(
                    command,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=True,
                    cwd=ROOT,
                )
            actual = snapshot(output, book, case["editorial"])
            observations[name] = actual
            row["differences"] = differences(baseline["cases"].get(name, {}), actual)
            row["status"] = (
                "built"
                if args.accept_baseline
                else "changed" if row["differences"] else "passed"
            )
        except (
            OSError,
            ValueError,
            KeyError,
            subprocess.CalledProcessError,
            zipfile.BadZipFile,
        ) as error:
            row.update(status="failed", error=str(error))
        results.append(row)
        print(f'{name}: {row["status"]}', flush=True)
    failed = any(r["status"] == "failed" for r in results)
    accepted = args.accept_baseline and not failed
    if accepted:
        baseline["cases"].update(observations)
        write_json(args.baseline, baseline)
    passed = not failed and (accepted or all(r["status"] == "passed" for r in results))
    write_json(
        run / "report.json",
        dict(
            passed=passed,
            baseline_accepted=accepted,
            baseline=str(args.baseline),
            cases=results,
        ),
    )
    print(f'Report: {run / "report.json"}', flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
