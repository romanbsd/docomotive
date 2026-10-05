"""Bounded, build-local reuse and observational performance telemetry."""

import copy
import io
import inspect
import time
from collections import Counter, OrderedDict
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from PIL import Image
from common import digest, write_json

_ACTIVE = ContextVar("docomotive_build_resources", default=None)


class BuildResources:
    def __init__(self):
        self.counters = Counter(
            {
                name: 0
                for name in (
                    "tesseract_calls",
                    "page_renders",
                    "retry_placement_cache_hits",
                    "retry_placement_cache_misses",
                    "page_image_hits",
                    "page_image_misses",
                    "line_ocr_hits",
                    "line_ocr_misses",
                    "engine_json_reads",
                    "engine_rows_hits",
                    "engine_rows_misses",
                    "observation_cache_hits",
                    "observation_cache_misses",
                    "corroboration_cache_hits",
                    "corroboration_cache_misses",
                    "marker_observation_cache_hits",
                    "marker_observation_cache_misses",
                    "glyph_retry_cache_hits",
                    "glyph_retry_cache_misses",
                )
            }
        )
        self.seconds = Counter()
        self.pages = OrderedDict()
        self.lines = OrderedDict()
        self.engines = OrderedDict()

    def cached(self, store, key, limit, kind, compute):
        if key in store:
            self.counters[kind + "_hits"] += 1
            store.move_to_end(key)
            return store[key]
        self.counters[kind + "_misses"] += 1
        value = compute()
        store[key] = value
        while len(store) > limit:
            store.popitem(last=False)
        return value


def count(name):
    active = _ACTIVE.get()
    if active:
        active.counters[name] += 1


def measured(function):
    """Preserve inspect.getsource for helpers with semantic cache fingerprints."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        active = _ACTIVE.get()
        if not active:
            return function(*args, **kwargs)
        start = time.perf_counter()
        active.counters[function.__name__ + "_calls"] += 1
        try:
            return function(*args, **kwargs)
        finally:
            active.seconds[function.__name__] += time.perf_counter() - start

    return wrapped


def page_image(page):
    def render():
        count("page_renders")
        return Image.open(io.BytesIO(page.get_pixmap(dpi=400).tobytes("png"))).convert(
            "L"
        )

    active = _ACTIVE.get()
    if not active:
        return render()
    # Keep two 400-dpi pages, rather than retaining a whole scanned book.
    key = (page.parent, page.number)
    return active.cached(active.pages, key, 2, "page_image", render).copy()


def line_ocr(image, recognizer, **kwargs):
    active = _ACTIVE.get()
    if not active:
        return recognizer(image, **kwargs)
    key = (
        image.mode,
        image.size,
        digest(image.tobytes()),
        tuple(sorted(kwargs.items())),
        recognizer,
    )
    # Results contain mutable character boxes. Never share them with a caller
    # that may shift offsets when inserting a marker.
    return copy.deepcopy(
        active.cached(
            active.lines, key, 128, "line_ocr", lambda: recognizer(image, **kwargs)
        )
    )


def engine_rows(path, loader):
    active = _ACTIVE.get()
    if not active:
        return loader(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, loader)
    # Only a few neighboring pages are needed while resolving marker bounds.
    return copy.deepcopy(
        active.cached(active.engines, key, 8, "engine_rows", lambda: loader(path))
    )


def profiled_build(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = inspect.signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        out = Path(bound.arguments["out"])
        active = BuildResources()
        token = _ACTIVE.set(active)
        started = time.perf_counter()
        status = "failed"
        try:
            result = function(*args, **kwargs)
            status = "passed"
            return result
        finally:
            active.seconds["build_total"] = time.perf_counter() - started
            _ACTIVE.reset(token)
            # Telemetry is deliberately separate from deterministic artifacts.
            # Do not publish into a missing output directory on preflight failure.
            if out.is_dir():
                write_json(
                    out / "performance.json",
                    dict(
                        status=status,
                        scope="OCR/render counters cover scanned_notes and marker resources; other OCR and review/figure rendering excluded. Timings cover named stages; nested times overlap.",
                        counters=dict(sorted(active.counters.items())),
                        seconds=dict(sorted(active.seconds.items())),
                    ),
                )

    return wrapped
