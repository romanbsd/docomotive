# Dependency compatibility

The modern pipeline requires Python 3.12+ and is tested on Python 3.14.8.
The current environment uses NumPy 2.5.4, SciPy 1.18.1, Pillow 13.0.0,
RapidOCR 3.10.0, ONNX Runtime 1.31.0, headless OpenCV 5.0.0.93 and Black
26.10.1. NumPy and SciPy set the new Python 3.12 minimum. All 560 pipeline
tests and the installed extractor's 30 lightweight tests pass; dependency
checks pass in the active environment and a clean lockfile installation.

## doc-page-extractor on Python 3.14

[Upstream](https://github.com/moskize91/doc-page-extractor) 1.2.1 declares
`>=3.10,<3.14`. Its released Python modules match commit
`44b2fc8e301d406deda0347dc6d4f46d8841d1f8`; all 30 lightweight upstream tests
pass on Python 3.14.8, including parser, extraction, adapter/error handling,
import boundaries and script helpers.

The upstream-ready [patch](../patches/doc-page-extractor-python314.patch):

- Extends the package range through Python 3.14, retaining a Python 3.15 cap.
- Adds the Python 3.14 classifier and regenerates the Poetry lock metadata
  with Poetry 2.1.3; dependency versions remain unchanged.
- Adds a separate Python 3.10–3.14 PR test matrix using ordinary pip installation.

The [builder](../scripts/build_doc_page_extractor.py) archives the exact commit,
applies the patch in an isolated temporary source directory, checks its Python
range, stamps local version `1.2.1+docomotive.1`, and runs the upstream tests.
It pins Poetry Core 2.1.3 and fixes wheel timestamps to 2000-01-01.
Two builds produced the same wheel SHA-256:
`51069546065453c49b412d875c2935bf6c7a9288b3859b690be53643de6b318c`.
The generated provenance records source revision, patch, builder and wheel hashes.

```sh
.venv/bin/python scripts/build_doc_page_extractor.py
.venv/bin/pip install '.[research,visual]' --group dev \
  --find-links work/dependency-compat/dist
.venv/bin/pip check
```

The wheel also installs normally into a clean Python 3.14 environment with
Pillow 12.3.0 and Requests 2.34.2. Its installed package passes the 30 tests
and `pip check`, without `--ignore-requires-python`. The main environment
has this patched version installed. No upstream release or PR was published.
The new CI matrix is prepared, not remotely executed.

Local Hugging Face model inference requires the upstream CUDA environment.
It was not tested on this Mac. The lightweight tests use injected adapters and
mocked vendor responses; they establish runtime/package compatibility, not
OCR accuracy or live-provider conformance. No book content was sent to an API.

## Legacy pdf-craft

pdf-craft 0.2.1 pins doc-page-extractor 0.2.2 and imports its `DocExtractor`,
`Rectangle` and related API. Those exports are absent from the redesigned
1.2.1 API. Its legacy extractor also requires NumPy below 2 and Pillow below 11.
Relaxing metadata alone cannot make these two APIs compatible.

The legacy stack therefore has a separate environment and constraints snapshot.
This preserves pdf-craft without downgrading the modern pipeline or claiming
an untested API migration:

```sh
# Use a Python 3.12 interpreter, not the core Python 3.14 environment.
python3.12 -m venv work/dependency-compat/pdf-craft-venv
work/dependency-compat/pdf-craft-venv/bin/pip install \
  -c requirements-pdf-craft.lock.txt pdf-craft==0.2.1
work/dependency-compat/pdf-craft-venv/bin/pip check
```

This checkout already has that environment installed using Python 3.12.15.
Its dependency check and imports of pdf-craft, `DocExtractor` and `Rectangle`
pass. Full legacy model extraction was not run. Python 3.12 was installed under
`work/dependency-compat/python/`, without changing system Python.

`requirements-research.lock.txt` now records the 56-distribution modern
environment. The previous compatible research constraints are preserved in
`requirements-pdf-craft.lock.txt`; incompatible independent upgrades of the
legacy model/LLM stack are not mixed into the modern environment.


## Dependency refresh, 2026-10-18

RapidOCR 3.10.0 remains the [latest stable release](https://pypi.org/project/rapidocr/);
it was already installed in the modern environment. The stale legacy constraint
is updated from 3.9.2 to 3.10.0, and actual English OCR passes there with NumPy
1.26.4, Pillow 10.4.0, OpenCV 4.11 and ONNX Runtime 1.30.0. Legacy numeric/image
pins remain separate from the modern package set.

The modern project now depends directly on `opencv-python-headless`, matching
RapidOCR's dependency. No project code uses OpenCV GUI APIs. Both old OpenCV
distributions were removed before installing the headless wheel, avoiding shared
`cv2` files from overlapping installations. An unused DocLayout-YOLO duplicate
was removed from the modern environment; it remains installed in the legacy
environment. Packaging stays at 25.0 because an installed LangChain research
package requires `<26`; upgrading it independently broke dependency checks.

Other refreshed pins include FontTools, OmegaConf, Platformdirs, Pycparser,
Pydyf, Shapely and wcwidth. Brotli and Zopfli are explicitly pinned so the frozen
visual environment includes FontTools' WOFF dependencies. `requirements.txt`
now mirrors all core `pyproject.toml` pins, including Russian morphology, NumPy
and headless OpenCV. There are no prerelease dependencies or OCR-model changes.

Fresh Russian/English RapidOCR probes produce 32/56 lines with the same model
hashes. Small raw-reading differences remain: Russian `И` becomes `и`, English
`shanans` becomes `shainans`, and dash spacing changes. Both English spellings
are OCR errors for printed `shamans`; this smoke check establishes execution,
not an accuracy improvement. ONNX-version fingerprints create separate cache
keys, retaining previous observations. Existing book caches were preserved.

A fresh Potapov build before/after the runtime update has identical EPUB bytes,
text, canonical model, corrected source rows, coverage, apparatus and regression
snapshot. Its correction audit has numerically identical JSON values; a few
rounded slopes serialize as `-0.0` instead of `0.0`. No book baselines were
updated for the dependency refresh. English/Cyrillic visual rendering also
passes with the refreshed WeasyPrint dependencies. Evidence and resolver reports
are in `work/dependency-upgrade-20261018/`.
