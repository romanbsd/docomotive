"""Force shared-cache readers to overlap image publication and scratch cleanup."""

import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extract
from common import digest, write_bytes_atomic, write_json
from lexical_repair import CropRecognizer


class OCRConcurrencyTests(unittest.TestCase):
    def recognizer(self, root):
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=100)
        page.insert_text((20, 50), "extreme beliefs", fontsize=16)
        words = page.get_text("words")
        row = dict(
            page=1,
            text="extreme beliets",
            bbox=[
                min(w[0] for w in words) / 300,
                min(w[1] for w in words) / 100,
                max(w[2] for w in words) / 300,
                max(w[3] for w in words) / 100,
            ],
        )
        rec = CropRecognizer.__new__(CropRecognizer)
        rec.doc, rec.work = doc, root / "lexical-crops"
        rec.version, rec.model_hash = "test-version", "test-model"
        rec.language, rec.models, rec.ocr_args = "eng", None, []
        return rec, row

    def test_crop_reader_never_sees_another_writers_partial_png(self):
        ready, partial, checked = (threading.Event() for _ in range(3))
        role = threading.local()
        original_save = Image.Image.save

        def slow_save(image, target, *args, **kwargs):
            if getattr(role, "name", "") != "writer":
                return original_save(image, target, *args, **kwargs)
            encoded = io.BytesIO()
            original_save(image, encoded, format="PNG")
            data = encoded.getvalue()
            # Emulate a writer suspended halfway through encoding. With the
            # old implementation this is the shared final file, not a buffer.
            stream = target if hasattr(target, "write") else open(target, "wb")
            try:
                middle = len(data) // 2
                stream.write(data[:middle])
                stream.flush()
                partial.set()
                self.assertTrue(checked.wait(10), "reader never checked the crop")
                stream.write(data[middle:])
            finally:
                if stream is not target:
                    stream.close()

        def ocr(args, **kwargs):
            if role.name == "reader" and args[4] == "8":
                ready.set()
                self.assertTrue(partial.wait(10), "writer never reached partial PNG")
                try:
                    with Image.open(args[1]) as image:
                        image.load()
                finally:
                    checked.set()
            else:
                with Image.open(args[1]) as image:
                    image.load()
            return SimpleNamespace(stdout="beliefs\n")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reader, row = self.recognizer(root)
            writer, other_row = self.recognizer(root)

            def recognize(rec, source, name):
                role.name = name
                return rec.recognize_crop(source, "beliets", "beliefs")

            try:
                with (
                    patch.object(Image.Image, "save", slow_save),
                    patch("lexical_repair.subprocess.run", side_effect=ocr),
                    ThreadPoolExecutor(max_workers=2) as pool,
                ):
                    first = pool.submit(recognize, reader, row, "reader")
                    self.assertTrue(ready.wait(10))
                    second = pool.submit(recognize, writer, other_row, "writer")
                    a, b = first.result(timeout=15), second.result(timeout=15)
                self.assertEqual(a, b)
                crop = reader.work / (a["cache_key"] + ".png")
                self.assertEqual(digest(crop.read_bytes()), a["crop_sha256"])
                self.assertEqual(json.loads(crop.with_suffix(".json").read_text()), a)
                self.assertEqual(list(reader.work.glob(".*")), [])
            finally:
                checked.set()
                reader.doc.close()
                writer.doc.close()

    def test_damaged_or_missing_cached_crop_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as folder:
            rec, row = self.recognizer(Path(folder))
            try:
                with patch(
                    "lexical_repair.subprocess.run",
                    return_value=SimpleNamespace(stdout="beliefs\n"),
                ) as ocr:
                    evidence = rec.recognize_crop(row, "beliets", "beliefs")
                    crop = rec.work / (evidence["cache_key"] + ".png")
                    crop.write_bytes(b"\x89PNG incomplete old cache")
                    self.assertEqual(
                        rec.recognize_crop(row, "beliets", "beliefs"), evidence
                    )
                    crop.unlink()
                    self.assertEqual(
                        rec.recognize_crop(row, "beliets", "beliefs"), evidence
                    )
                    self.assertEqual(ocr.call_count, 6)
                    self.assertEqual(digest(crop.read_bytes()), evidence["crop_sha256"])
            finally:
                rec.doc.close()

    def extraction_fixture(self, root):
        source = root / "source.pdf"
        with pymupdf.open() as doc:
            doc.new_page(width=100, height=100)
            doc.save(source)
        profile = root / "book.json"
        write_json(
            profile,
            dict(
                title="Book",
                author="Author",
                language="en",
                slug="book",
                source_sha256=digest(source.read_bytes()),
                chapters=[[1, 1, "Chapter"]],
            ),
        )
        work = root / "work"
        argv = [
            "extract.py",
            str(source),
            "--profile",
            str(profile),
            "--work",
            str(work),
            "--engine",
            "tesseract",
        ]
        return work, argv

    def test_overlapping_extractions_do_not_delete_each_others_input(self):
        both = threading.Barrier(2)
        first_finished = threading.Event()
        role = threading.local()
        paths = []
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t5\t5\t20\t10\t99\tWord\n"
        )

        def ocr(args, **kwargs):
            if args == ["tesseract", "--version"]:
                return "tesseract test\n"
            path = Path(args[1])
            paths.append(path)
            both.wait(timeout=10)
            if role.name == "second":
                self.assertTrue(first_finished.wait(10))
            with Image.open(path) as image:
                image.load()
            return tsv

        def run(name):
            role.name = name
            try:
                extract.main()
            finally:
                if name == "first":
                    first_finished.set()

        with tempfile.TemporaryDirectory() as folder:
            work, argv = self.extraction_fixture(Path(folder))
            with (
                patch("sys.argv", argv),
                patch("extract.platform.platform", return_value="test-platform"),
                patch("extract.traineddata_digest", return_value="test-model"),
                patch("extract.subprocess.check_output", side_effect=ocr),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                first, second = pool.submit(run, "first"), pool.submit(run, "second")
                first.result(timeout=15)
                second.result(timeout=15)
            self.assertEqual(len(set(paths)), 2)
            self.assertTrue(all(not p.exists() for p in paths))
            cache = Path((work / "tesseract-cache.txt").read_text().strip())
            self.assertEqual(
                json.loads((cache / "0001.json").read_text())["lines"][0]["text"],
                "Word",
            )
            self.assertEqual(list(cache.glob(".page-*")), [])

    def test_failed_extraction_removes_private_scratch_and_publishes_no_page(self):
        def ocr(args, **kwargs):
            if args == ["tesseract", "--version"]:
                return "tesseract test\n"
            raise subprocess.CalledProcessError(1, args)

        with tempfile.TemporaryDirectory() as folder:
            work, argv = self.extraction_fixture(Path(folder))
            with (
                patch("sys.argv", argv),
                patch("extract.platform.platform", return_value="test-platform"),
                patch("extract.traineddata_digest", return_value="test-model"),
                patch("extract.subprocess.check_output", side_effect=ocr),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                extract.main()
            self.assertFalse((work / "tesseract-cache.txt").exists())
            self.assertEqual(list(work.glob("ocr/*/.page-*")), [])
            self.assertEqual(list(work.glob("ocr/*/0001.json")), [])

    def test_failed_atomic_publication_preserves_previous_file_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "crop.png"
            target.write_bytes(b"previous complete image")
            with patch("common.Path.replace", side_effect=OSError("publish failed")):
                with self.assertRaises(OSError):
                    write_bytes_atomic(target, b"new complete image")
            self.assertEqual(target.read_bytes(), b"previous complete image")
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_concurrent_vision_compiles_publish_separate_source_bound_executables(self):
        both = threading.Barrier(2)

        def compile(args, **kwargs):
            staged = Path(args[-1])
            staged.write_bytes(Path(args[-3]).read_bytes())
            staged.chmod(0o755)
            both.wait(timeout=10)

        for same_source in (True, False):
            with self.subTest(same_source=same_source):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    a, b = root / "a.swift", root / "b.swift"
                    a.write_bytes(b"first source")
                    b.write_bytes(a.read_bytes() if same_source else b"second source")
                    with patch(
                        "extract.subprocess.run", side_effect=compile
                    ) as compiler:
                        with ThreadPoolExecutor(max_workers=2) as pool:
                            first = pool.submit(extract.vision_binary, root, a)
                            second = pool.submit(extract.vision_binary, root, b)
                            one, two = first.result(timeout=15), second.result(
                                timeout=15
                            )
                        self.assertEqual(one == two, same_source)
                        self.assertEqual(one.read_bytes(), a.read_bytes())
                        self.assertEqual(two.read_bytes(), b.read_bytes())
                        self.assertTrue(one.stat().st_mode & 0o111)
                        self.assertEqual(extract.vision_binary(root, a), one)
                        self.assertEqual(compiler.call_count, 2)
                    self.assertEqual(list(root.glob(".vision-build-*")), [])


if __name__ == "__main__":
    unittest.main()
