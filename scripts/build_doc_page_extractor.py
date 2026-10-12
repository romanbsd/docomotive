"""Build a pinned, tested Python 3.14-compatible extractor without site patches."""

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/moskize91/doc-page-extractor.git"
REVISION = "44b2fc8e301d406deda0347dc6d4f46d8841d1f8"
VERSION = "1.2.1+docomotive.1"


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def stamp_version(source):
    files = {
        source / "pyproject.toml": ('version = "1.2.1"', f'version = "{VERSION}"'),
        source
        / "doc_page_extractor/__init__.py": (
            '__version__ = "1.2.1"',
            f'__version__ = "{VERSION}"',
        ),
    }
    # Check both inputs before changing either: a newer checkout is not this
    # reviewed compatibility build, even if the patch happens to apply.
    originals = {path: path.read_text() for path in files}
    for path, (before, _) in files.items():
        if originals[path].count(before) != 1:
            raise ValueError(f"Unexpected upstream version in {path.name}")
    for path, (before, after) in files.items():
        path.write_text(originals[path].replace(before, after))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout", type=Path, default=ROOT / "work/dependency-compat/upstream"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "work/dependency-compat/dist"
    )
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 14):
        parser.error("Run this compatibility validation with Python 3.14")
    checkout, output = args.checkout.resolve(), args.output.resolve()
    patch = ROOT / "patches/doc-page-extractor-python314.patch"
    if not checkout.exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--no-checkout", UPSTREAM, checkout)
    # Archive the exact commit, ignoring local edits in the supplied checkout.
    archive = run(
        "git", "-C", checkout, "archive", REVISION, capture_output=True
    ).stdout
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        source = Path(directory)
        with tarfile.open(fileobj=BytesIO(archive)) as contents:
            contents.extractall(source, filter="data")
        # Stop Git discovering Docomotive above this archive: otherwise apply
        # can silently skip every upstream path as outside its current prefix.
        patch_env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(source.parent)}
        run("git", "apply", "--check", patch, cwd=source, env=patch_env)
        run("git", "apply", patch, cwd=source, env=patch_env)
        project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
        if project["requires-python"] != ">=3.10,<3.15":
            raise ValueError("Python 3.14 compatibility patch was not applied")
        stamp_version(source)
        # Pin the build backend as well as the library source.
        project_file = source / "pyproject.toml"
        project_file.write_text(
            project_file.read_text().replace(
                "poetry-core>=2.0.0,<3.0.0", "poetry-core==2.1.3"
            )
        )
        run(
            sys.executable,
            "test.py",
            cwd=source,
            env={**os.environ, "PYTHONPATH": str(source)},
        )
        run(
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            output,
            source,
            env={**os.environ, "SOURCE_DATE_EPOCH": "946684800"},
        )
    wheel = output / f"doc_page_extractor-{VERSION}-py3-none-any.whl"
    manifest = {
        "upstream": UPSTREAM,
        "revision": REVISION,
        "version": VERSION,
        "python": sys.version,
        "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "validation": "upstream lightweight tests; local CUDA inference not validated",
    }
    (output / "build-provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(wheel)


if __name__ == "__main__":
    main()
