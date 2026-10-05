"""Memoization changes work performed, never OCR evidence or caller-owned data."""

import inspect
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_resources as resources
import scanned_notes as scan


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.active = resources.BuildResources()
        self.token = resources._ACTIVE.set(self.active)

    def tearDown(self):
        resources._ACTIVE.reset(self.token)

    def test_page_reuse_eviction_and_mutation_isolation(self):
        stream = io.BytesIO()
        Image.new("L", (20, 20), 255).save(stream, format="PNG")
        render = Mock(
            return_value=SimpleNamespace(tobytes=lambda fmt: stream.getvalue())
        )
        parent = object()
        pages = [
            SimpleNamespace(parent=parent, number=n, get_pixmap=render)
            for n in range(3)
        ]
        image = resources.page_image(pages[0])
        image.putpixel((0, 0), 0)
        self.assertEqual(resources.page_image(pages[0]).getpixel((0, 0)), 255)
        for page in pages[1:]:
            resources.page_image(page)
        resources.page_image(pages[0])
        self.assertEqual(render.call_count, 4)
        self.assertEqual(len(self.active.pages), 2)

    def test_line_cache_pixels_options_and_mutation_isolation(self):
        recognize = Mock(return_value=("word", [dict(start=0, bbox=[0, 0, 2, 2])]))
        image = Image.new("L", (20, 20), 255)
        result = resources.line_ocr(image, recognize, clean=True)
        result[1][0]["start"] = 99
        self.assertEqual(
            resources.line_ocr(image, recognize, clean=True)[1][0]["start"], 0
        )
        resources.line_ocr(image, recognize, clean=False)
        image.putpixel((0, 0), 0)
        resources.line_ocr(image, recognize, clean=True)
        self.assertEqual(recognize.call_count, 3)

    def test_engine_file_change_and_returned_rows(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "rows.json"
            path.write_text('[{"text":"old"}]')
            load = Mock(side_effect=lambda p: json.loads(p.read_text()))
            resources.engine_rows(path, load)[0]["text"] = "modified"
            self.assertEqual(resources.engine_rows(path, load)[0]["text"], "old")
            path.write_text('[{"text":"changed"}]')
            self.assertEqual(resources.engine_rows(path, load)[0]["text"], "changed")
            self.assertEqual(load.call_count, 2)

    def test_measurement_preserves_helper_source_and_records_failures(self):
        def sample():
            raise RuntimeError("failed OCR")

        wrapped = resources.measured(sample)
        self.assertEqual(inspect.getsource(wrapped), inspect.getsource(sample))
        with self.assertRaisesRegex(RuntimeError, "failed OCR"):
            wrapped()
        self.assertEqual(self.active.counters["sample_calls"], 1)
        self.assertIn("sample", self.active.seconds)

    def test_build_scope_isolation_and_separate_telemetry(self):
        @resources.profiled_build
        def build(out):
            resources.count("tesseract_calls")
            return "done"

        with tempfile.TemporaryDirectory() as work:
            self.assertEqual(build(Path(work)), "done")
            report = json.loads(Path(work, "performance.json").read_text())
            self.assertEqual(report["counters"]["tesseract_calls"], 1)
            self.assertEqual(report["status"], "passed")
            self.assertIs(resources._ACTIVE.get(), self.active)
            self.assertEqual(self.active.counters["tesseract_calls"], 0)

    def test_retry_cache_includes_empty_results_and_complete_inputs(self):
        with (
            tempfile.TemporaryDirectory() as work,
            patch.object(scan, "retry_placements", return_value={}) as compute,
        ):
            row = dict(text="Statement.", bbox=[0.1, 0.2, 0.8, 0.3])
            retry = dict(readings=["2"], bbox=[0.7, 0.2, 0.72, 0.22])
            args = (
                SimpleNamespace(number=0),
                row,
                retry,
                Path(work),
                dict(source="first", runtime="v1"),
            )
            self.assertEqual(scan.cached_retry_placements(*args), {})
            self.assertEqual(scan.cached_retry_placements(*args), {})
            self.assertEqual(compute.call_count, 1)
            scan.cached_retry_placements(args[0], dict(row, text="Changed."), *args[2:])
            scan.cached_retry_placements(*args[:4], dict(source="second", runtime="v1"))
            scan.cached_retry_placements(*args[:4], dict(source="first", runtime="v2"))
            scan.cached_retry_placements(
                *args[:2], dict(retry, readings=["3"]), *args[3:]
            )
            scan.cached_retry_placements(SimpleNamespace(number=1), *args[1:])
            self.assertEqual(compute.call_count, 6)

    def test_positive_placement_replay_does_not_share_mutable_results(self):
        changes = {
            "2": dict(
                start=4, end=5, before=".", after=".2", marker_start=5, marker_end=6
            )
        }
        with (
            tempfile.TemporaryDirectory() as work,
            patch.object(scan, "retry_placements", return_value=changes) as compute,
        ):
            args = (
                SimpleNamespace(number=0),
                dict(text="Word.", bbox=[0.1, 0.2, 0.8, 0.3]),
                dict(readings=["2"], bbox=[0.7, 0.2, 0.72, 0.22]),
                Path(work),
                dict(source="first"),
            )
            result = scan.cached_retry_placements(*args)
            result["2"]["after"] = "modified"
            self.assertEqual(scan.cached_retry_placements(*args)["2"]["after"], ".2")
            self.assertEqual(compute.call_count, 1)

    def test_failed_ocr_is_not_cached_and_cache_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as work:
            args = (
                SimpleNamespace(number=0),
                dict(text="Statement.", bbox=[0.1, 0.2, 0.8, 0.3]),
                dict(readings=["2"], bbox=[0.7, 0.2, 0.72, 0.22]),
                Path(work),
                dict(source="first"),
            )
            with patch.object(
                scan, "retry_placements", side_effect=RuntimeError("failed OCR")
            ):
                with self.assertRaises(RuntimeError):
                    scan.cached_retry_placements(*args)
            self.assertEqual(list(Path(work).rglob("*.json")), [])
            with patch.object(scan, "retry_placements", return_value={}) as compute:
                scan.cached_retry_placements(*args)
                path = next(Path(work).rglob("*.json"))
                path.write_text('{"key":"wrong","changes":{}}')
                with self.assertRaisesRegex(
                    ValueError, "Invalid retry placement cache"
                ):
                    scan.cached_retry_placements(*args)
                self.assertEqual(compute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
