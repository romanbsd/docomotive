# Dependency compatibility

The modern pipeline environment uses Python 3.14 with NumPy 2.4.6, Pillow
12.3.0, RapidOCR 3.10.0 and Black 26.10.0. The updated core passes all 419
pipeline tests and Black. Its dependency resolver and `pip check` pass.

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

`requirements-research.lock.txt` now records the 55-distribution modern
environment. The previous compatible research constraints are preserved in
`requirements-pdf-craft.lock.txt`; incompatible independent upgrades of the
legacy model/LLM stack are not mixed into the modern environment.
