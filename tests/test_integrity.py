"""Input failures and concurrent cache publication must be explicit and safe."""

import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, load_profile, read_json, write_json, write_text_atomic
from profile_validation import validate_profile
from ocr_cache import preflight_cache, publish_cache, traineddata_digest
from regression import differences, load_cases, main as regression_main


class IntegrityTests(unittest.TestCase):
    def profile(self):
        return dict(
            title="Book",
            author="Author",
            language="en",
            slug="book",
            source_sha256="a" * 64,
            chapters=[[1, 3, "Chapter"], [4, 5, "Notes"]],
        )

    def test_existing_profiles_are_valid(self):
        for path in [
            ROOT / "config/book.json",
            *sorted((ROOT / "config").glob("*/book.json")),
        ]:
            with self.subTest(path=path):
                load_profile(path)

    def test_profiles_reject_wrong_types_and_structure(self):
        cases = [
            ("recover_scanned_endnotes", "false"),
            ("chapters", [[3, 1, "Bad"]]),
            ("chapters", [[1, 3, "A"], [3, 5, "B"]]),
            ("source_sha256", "short"),
            ("slug", "../escape"),
            ("index_splits", {"2": 1.2}),
            ("figures", [{"page": 2, "rect": [0.8, 0.1, 0.2, 0.7]}]),
            ("endnote_chapter", 3),
            ("native_body_size", float("nan")),
            ("hanging_pages", [True]),
            ("figure_cleanup", "transparent"),
            ("figure_cleanup", True),
            ("figure_color_mode", "sepia"),
            ("figure_color_mode", True),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_profile(dict(self.profile(), **{key: value}))
        with self.assertRaisesRegex(ValueError, "required"):
            validate_profile({})
        with self.assertRaisesRegex(ValueError, "PDF page"):
            validate_profile(self.profile(), page_count=4)
        with self.assertRaisesRegex(ValueError, "chapter reference"):
            validate_profile(
                dict(
                    self.profile(),
                    endnote_chapter=2,
                    endnote_sections=[
                        dict(heading="Bad", source_chapter=9, expected_notes=1)
                    ],
                )
            )

    def make_cache(self, root):
        cache = root / "ocr"
        write_json(cache / "provenance.json", dict(source_sha256="a" * 64))
        page = dict(
            page=1,
            width=400,
            height=600,
            lines=[dict(text="Line", bbox=[0.1, 0.2, 0.8, 0.3], confidence=0.9)],
        )
        write_json(cache / "0001.json", page)
        return cache, page

    def test_cache_rejects_corruption_wrong_pages_and_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            cache, page = self.make_cache(Path(folder))
            preflight_cache(cache, 1, "a" * 64)
            with self.assertRaisesRegex(ValueError, "lacks 1 pages"):
                preflight_cache(cache, 2, "a" * 64)
            with self.assertRaisesRegex(ValueError, "different PDF"):
                preflight_cache(cache, 1, "b" * 64)
            for bad in [
                dict(page, page=2),
                dict(page, width=-1),
                dict(page, lines=[dict(text="Line", bbox=[0.8, 0.2, 0.1, 0.3])]),
            ]:
                write_json(cache / "0001.json", bad)
                with self.assertRaisesRegex(ValueError, "0001.json"):
                    preflight_cache(cache, 1, "a" * 64)
            (cache / "0001.json").write_text('{"page":')
            with self.assertRaisesRegex(ValueError, "Invalid OCR cache"):
                preflight_cache(cache, 1, "a" * 64)

    def test_incomplete_experiment_cannot_replace_selected_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache, _ = self.make_cache(root)
            pointer = root / "vision-cache.txt"
            pointer.write_text("old cache\n")
            with patch("builtins.print"):
                self.assertFalse(publish_cache(root, "vision", cache, 2, "a" * 64))
            self.assertEqual(pointer.read_text(), "old cache\n")
            self.assertTrue(publish_cache(root, "vision", cache, 1, "a" * 64))
            self.assertEqual(pointer.read_text(), str(cache.resolve()) + "\n")
            (cache / "0001.json").write_text("broken")
            with self.assertRaisesRegex(ValueError, "Invalid OCR cache"):
                publish_cache(root, "vision", cache, 1, "a" * 64)
            self.assertEqual(pointer.read_text(), str(cache.resolve()) + "\n")

    def test_concurrent_atomic_writers_leave_one_complete_document(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shared.json"
            documents = [dict(writer=n, payload=str(n) * 20000) for n in range(16)]
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda value: write_json(path, value), documents))
            self.assertIn(read_json(path), documents)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_failed_atomic_publish_preserves_old_file_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "selected"
            path.write_text("old")
            with patch("pathlib.Path.replace", side_effect=OSError("failed")):
                with self.assertRaises(OSError):
                    write_text_atomic(path, "new")
            self.assertEqual(path.read_text(), "old")
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_model_digest_uses_actual_tesseract_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "eng.traineddata"
            path.write_bytes(b"first model")
            output = f'List of available languages in "{folder}" (1):\neng\n'
            with patch("ocr_cache.subprocess.check_output", return_value=output):
                first = traineddata_digest()
                path.write_bytes(b"second model")
                self.assertNotEqual(first, traineddata_digest())

    def test_regression_diff_names_changed_artifacts_and_missing_entries(self):
        self.assertEqual(differences({"a": 1}, {"a": 1}), [])
        self.assertEqual(
            [
                r["path"]
                for r in differences(
                    {"artifacts": {"text": "old"}},
                    {"artifacts": {"text": "new"}, "extra": 2},
                )
            ],
            ["artifacts.text", "extra"],
        )

    def test_regression_manifest_rejects_duplicate_cases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cases.json"
            case = dict(
                name="book",
                pdf="book.pdf",
                profile="book.json",
                work="work",
                editorial=False,
            )
            write_json(path, [case, case])
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_cases(path)

    def test_regression_never_accepts_a_failed_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "cases.json"
            baseline = root / "baseline.json"
            case = dict(
                name="book",
                pdf="book.pdf",
                profile="book.json",
                work="work",
                editorial=False,
            )
            write_json(manifest, [case, dict(case, name="failed")])
            write_json(baseline, dict(format=1, cases={"book": {"text": "old"}}))
            before = baseline.read_bytes()
            argv = [
                "regression.py",
                "--manifest",
                str(manifest),
                "--baseline",
                str(baseline),
                "--run-root",
                str(root / "runs"),
                "--accept-baseline",
            ]
            import subprocess

            with (
                patch("sys.argv", argv),
                patch("regression.load_profile", return_value=self.profile()),
                patch("regression.snapshot", return_value={"text": "new"}),
                patch(
                    "regression.subprocess.run",
                    side_effect=[None, subprocess.CalledProcessError(1, "build")],
                ),
                patch("builtins.print"),
            ):
                self.assertEqual(regression_main(), 1)
            self.assertEqual(baseline.read_bytes(), before)
            report = read_json(next((root / "runs").glob("*/report.json")))
            self.assertFalse(report["passed"])
            self.assertFalse(report["baseline_accepted"])

    def test_changed_output_fails_until_baseline_explicitly_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "cases.json"
            baseline = root / "baseline.json"
            write_json(
                manifest,
                [
                    dict(
                        name="book",
                        pdf="book.pdf",
                        profile="book.json",
                        work="work",
                        editorial=False,
                    )
                ],
            )
            write_json(baseline, dict(format=1, cases={"book": {"text": "old"}}))
            before = baseline.read_bytes()
            argv = [
                "regression.py",
                "--manifest",
                str(manifest),
                "--baseline",
                str(baseline),
                "--run-root",
                str(root / "runs"),
            ]
            with (
                patch("regression.load_profile", return_value=self.profile()),
                patch("regression.snapshot", return_value={"text": "new"}),
                patch("regression.subprocess.run"),
                patch("builtins.print"),
            ):
                with patch("sys.argv", argv):
                    self.assertEqual(regression_main(), 1)
                self.assertEqual(baseline.read_bytes(), before)
                with patch("sys.argv", argv + ["--accept-baseline"]):
                    self.assertEqual(regression_main(), 0)
            self.assertEqual(read_json(baseline)["cases"]["book"], {"text": "new"})


if __name__ == "__main__":
    unittest.main()
