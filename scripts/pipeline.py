"""One-command local pipeline. Profiles explicitly describe source-specific layout."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("pdf", type=Path)
    p.add_argument("--profile", type=Path, default=ROOT / "config/book.json")
    p.add_argument("--work", type=Path, default=ROOT / "work")
    p.add_argument("--output", type=Path, default=ROOT / "output")
    p.add_argument("--models", type=Path, default=ROOT / "work/models")
    p.add_argument("--editorial", action="store_true")
    p.add_argument(
        "--epubcheck",
        type=Path,
        default=ROOT / "work/tools/epubcheck-5.4.0/epubcheck.jar",
    )
    p.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Require and reuse existing complete caches",
    )
    p.add_argument(
        "--skip-lexical", action="store_true", help="Skip dictionary proposals"
    )
    p.add_argument(
        "--fetch-metadata",
        action="store_true",
        help="Populate cached ISBN or DOI enrichment before building; network access required for cache misses",
    )
    a = p.parse_args()

    def run(script, *args):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
            check=True,
        )

    if a.fetch_metadata:
        run("metadata.py", "--profile", a.profile, "--cache", a.work / "metadata/cache")
    import json

    book = json.loads(a.profile.read_text())
    if not a.skip_extraction and book.get("text_source") != "native":
        for engine in ["tesseract", "rapid", "vision"]:
            run(
                "extract.py",
                a.pdf,
                "--profile",
                a.profile,
                "--work",
                a.work,
                "--models",
                a.models,
                "--engine",
                engine,
            )
    run(
        "build.py",
        a.pdf,
        "--profile",
        a.profile,
        "--work",
        a.work,
        "--output",
        a.output,
        "--models",
        a.models,
        *(["--editorial"] if a.editorial else []),
    )
    if not a.skip_lexical:
        run(
            "candidates.py",
            "--profile",
            a.profile,
            "--output",
            a.output,
            "--models",
            a.models,
        )
        run(
            "proofread.py",
            a.pdf,
            "--profile",
            a.profile,
            "--output",
            a.output,
            "--work",
            a.work,
        )
    if not a.epubcheck.exists():
        raise FileNotFoundError("Install EPUBCheck or specify --epubcheck")
    import json

    slug = json.loads(a.profile.read_text())["slug"] + (
        "-corrected" if a.editorial else ""
    )
    subprocess.run(
        [
            "java",
            "-jar",
            str(a.epubcheck),
            str(a.output / (slug + ".epub")),
            "--json",
            str(a.output / "epubcheck.json"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
