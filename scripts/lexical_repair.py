"""Whole-book noisy-channel ranking, gated by local word-crop recognition.

Scores are ranking evidence, not calibrated probabilities. Never repair names,
accepted spellings, protected terms, references, or an uncorroborated guess.
"""

from repair_types import RepairPolicy

import difflib
import math
import inspect
import io
import os
from functools import lru_cache
import re
import subprocess
from collections import Counter
from pathlib import Path

import pymupdf
import symspellpy
from PIL import Image, ImageOps
from symspellpy import SymSpell, Verbosity
from wordfreq import zipf_frequency

from common import apply_edits, digest, read_json, write_json
from book_model import row_id
from vocabulary import TOKEN
from languages import language_code, ocr_settings
from lxml import etree

# Common scanned serif confusions. Other edits may rank, but cannot auto-apply.
CONFUSIONS = {frozenset(pair) for pair in ("ce", "ft", "it", "il", "bh", "nr")}


def confusable(before, after):
    differences = [(a, b) for a, b in zip(before, after) if a != b]
    return (
        len(before) == len(after)
        and len(differences) == 1
        and frozenset(differences[0]) in CONFUSIONS
    )


def locate_word(text, words, word, candidate, edge_anchor=False):
    """Anchor a crop by neighboring words, not spelling similarity alone."""
    word, candidate = word.lower(), candidate.lower()
    # Tiny damaged fragments can be recognized as a digit (б -> 6). Keep
    # numbers in this geometry-only alignment; two neighbors still bind it.
    geometry_token = re.compile(r"[^\W_]+") if len(word) <= 3 else TOKEN
    tokens = [m.group().lower() for m in geometry_token.finditer(text)]
    if tokens.count(word) != 1:
        return None
    index = tokens.index(word)
    old, owners = [], []
    for owner, value in enumerate(words):
        for token in geometry_token.findall(value[4].lower()):
            old.append(token)
            owners.append(owner)
    scored = []
    for i, token in enumerate(old):
        similarity = max(
            difflib.SequenceMatcher(None, value, token).ratio()
            for value in (word, candidate)
        )
        # Two neighbors in either direction disambiguate repeated similar words
        # and prevent a correct word elsewhere in the line supplying false OCR.
        anchors = sum(
            tokens[index + d] == old[i + d]
            for d in (-2, -1, 1, 2)
            if 0 <= index + d < len(tokens) and 0 <= i + d < len(old)
        )
        if similarity < 0.75 and not (
            len(word) <= 3 and (anchors >= 2 or len(tokens) == len(old) == 1)
        ):
            continue
        edge = (
            edge_anchor
            and token == word
            and (index == i == 0 or index == len(tokens) - 1 and i == len(old) - 1)
        )
        if anchors or len(tokens) == len(old) == 1 or edge:
            scored.append(((anchors, similarity), owners[i]))
    scored.sort(reverse=True)
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return words[scored[0][1]]


class ContextRanker:
    """Smoothed bigram Markov model with fixed general and book priors."""

    def __init__(self, model, book, dictionary, protected=()):
        self.language = language_code(book)
        self.dictionary = dictionary
        self.protected = {w.lower() for w in protected}
        root = Path(symspellpy.__file__).parent
        unigram = root / "frequency_dictionary_en_82_765.txt"
        bigram = root / "frequency_bigramdictionary_en_243_342.txt"
        self.sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        self.general = {}
        self.outgoing = Counter()
        if self.language == "en":
            self.resources = {p.name: digest(p.read_bytes()) for p in (unigram, bigram)}
            if not self.sym.load_dictionary(str(unigram), 0, 1):
                raise ValueError("Cannot load pinned frequency dictionary")
            for line in bigram.read_text().splitlines():
                left, right, count = line.split()
                count = int(count)
                self.general[left, right] = count
                self.outgoing[left] += count
        else:
            from wordfreq import top_n_list
            import wordfreq

            resource = (
                Path(wordfreq.__file__).parent
                / "data"
                / f"large_{self.language}.msgpack.gz"
            )
            self.resources = {resource.name: digest(resource.read_bytes())}
            # Bound candidate construction; inflected specialist forms are
            # added below only when the dictionary accepts book observations.
            for word in top_n_list(self.language, 50000):
                if word.isalpha():
                    self.sym.create_dictionary_entry(
                        word, max(1, round(10 ** zipf_frequency(word, self.language)))
                    )
        self.counts = Counter()
        self.pairs = Counter()
        self.book_outgoing = Counter()
        for entry in model:
            if self.excluded(entry, book):
                continue
            for block in entry["blocks"] + entry["notes"]:
                # Do not train across paragraph or sentence boundaries.
                for sentence in re.split(r"[.!?;]\s+", block["text"]):
                    words = [m.group().lower() for m in TOKEN.finditer(sentence)]
                    self.counts.update(words)
                    self.pairs.update(zip(words, words[1:]))
                    self.book_outgoing.update(words[:-1])

        if self.language != "en":
            for word in sorted(self.protected):
                self.sym.create_dictionary_entry(
                    word, max(1, round(10 ** zipf_frequency(word, self.language)))
                )
            for word in sorted(self.counts):
                if word not in self.sym.words and (
                    self.dictionary.lookup(word)
                    or (
                        self.counts[word] >= 3
                        and zipf_frequency(word, self.language) >= 2
                    )
                ):
                    self.sym.create_dictionary_entry(
                        word, max(1, round(10 ** zipf_frequency(word, self.language)))
                    )

    @staticmethod
    def excluded(entry, book):
        return entry["chapter"] in book.get("statistics_excluded_chapters", []) or (
            entry["source_pages"][0]
            in book.get("reference_pages", []) + book.get("index_pages", [])
        )

    @lru_cache(maxsize=None)
    def known(self, word):
        low = word.lower()
        return (
            low in self.protected
            or low in self.sym.words
            or zipf_frequency(low, self.language) >= 2
            or self.dictionary.lookup(word)
            or self.dictionary.lookup(low)
        )

    def transition(self, left, right):
        # Ten book observations' worth of general-language prior keeps sparse
        # book bigrams from dominating. Missing general pairs back off to Zipf.
        prior = 10 ** (zipf_frequency(right, self.language) - 9)
        # Reserve 10% for unigram backoff: an absent pair in a finite corpus
        # must not make a common word less likely than an unrelated rare word.
        general = (
            0.9 * self.general.get((left, right), 0) / (self.outgoing[left] + 1)
            + 0.1 * prior
        )
        return (self.pairs[left, right] + 10 * general) / (
            self.book_outgoing[left] + 10
        )

    def rank(self, word, context, offset, include_known=False, max_distance=2):
        matches = list(TOKEN.finditer(context))
        index = next((i for i, m in enumerate(matches) if m.start() == offset), None)
        left = matches[index - 1].group().lower() if index and index > 0 else None
        right = (
            matches[index + 1].group().lower()
            if index is not None and index + 1 < len(matches)
            else None
        )
        if index is not None:
            if left and re.search(
                r"[.!?;]", context[matches[index - 1].end() : offset]
            ):
                left = None
            if right and re.search(
                r"[.!?;]", context[matches[index].end() : matches[index + 1].start()]
            ):
                right = None
        choices = []
        for candidate in self.sym.lookup(
            word.lower(),
            Verbosity.ALL if include_known else Verbosity.CLOSEST,
            max_distance,
        ):
            term = candidate.term
            score = math.log(max(candidate.count, 1)) - (
                2 * candidate.distance if include_known else 0
            )
            score += 0.5 * math.log1p(self.counts[term])
            if left:
                score += math.log(max(self.transition(left, term), 1e-15))
            if right:
                score += math.log(max(self.transition(term, right), 1e-15))
            # A known glyph substitution gets a modest likelihood advantage;
            # crop OCR still has to read the winning word, in both modes.
            supported = confusable(word.lower(), term) or (
                self.language != "en" and candidate.distance == 1
            )
            score += 2 if supported else 0
            choices.append(
                dict(
                    term=term,
                    distance=candidate.distance,
                    frequency=candidate.count,
                    book_count=self.counts[term],
                    confusion_supported=supported,
                    context_score=round(score, 6),
                )
            )
        return sorted(choices, key=lambda c: (-c["context_score"], c["term"]))


class CropRecognizer:
    def __init__(self, doc, work, book=None, models=None):
        settings = ocr_settings(book or {"language": "en"})
        self.language = settings["tesseract"]
        self.tessdata = settings["tessdata"]
        self.ocr_args = ["-l", self.language]
        if self.tessdata:
            self.ocr_args += ["--tessdata-dir", str(self.tessdata)]
        self.models = Path(models) if models and self.language == "rus" else None
        self.rapid = None
        self.doc = doc
        self.work = Path(work) / "lexical-crops"
        self.version = subprocess.run(
            ["tesseract", "--version"], capture_output=True, text=True, check=True
        ).stdout
        languages = subprocess.run(
            ["tesseract", "--list-langs"]
            + (["--tessdata-dir", str(self.tessdata)] if self.tessdata else []),
            capture_output=True,
            text=True,
            check=True,
        )
        match = re.search(r'"([^"]+)"', languages.stdout + languages.stderr)
        if (
            not match
            or not (Path(match[1]) / (self.language + ".traineddata")).is_file()
        ):
            raise ValueError("Cannot fingerprint local Tesseract language model")
        self.model_hash = digest(
            (Path(match[1]) / (self.language + ".traineddata")).read_bytes()
        )

    def line_words(self, page, row, raw=False, clean=True, dpi=600):
        """Image-only fallback: fresh line OCR supplies geometry, not old votes."""
        from scanned_notes import character_line

        # Low-resolution Russian scans have tight line pitch. Their primary
        # boxes already enclose the ink; a two-point expansion admits neighbors.
        padding = 2 if self.language == "eng" else 0
        rect = (
            pymupdf.Rect(
                row["bbox"][0] * page.rect.width - padding,
                row["bbox"][1] * page.rect.height - padding,
                row["bbox"][2] * page.rect.width + padding,
                row["bbox"][3] * page.rect.height + padding,
            )
            & page.rect
        )
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(dpi / 72, dpi / 72),
            clip=rect,
            colorspace=pymupdf.csGRAY,
        )
        key = digest(
            pix.samples
            + self.version.encode()
            + self.model_hash.encode()
            + Path(inspect.getsourcefile(character_line)).read_bytes()
            + b"lexical-line-geometry-v1"
            + (str(dpi).encode() if dpi != 600 else b"")
            + (
                inspect.getsource(self.line_words).encode() + str(clean).encode()
                if self.language != "eng"
                else b""
            )
        )
        path = self.work / ("line-" + key + ".json")
        if path.exists():
            data = read_json(path)
            text, chars = data["text"], data["characters"]
        else:
            image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            if self.language == "eng" and not self.tessdata:
                text, chars = character_line(image)
            else:
                from scanned_notes import components, prose_baseline
                import numpy as np

                # Reuse the baseline estimator to mask adjacent lines; oversized
                # primary OCR boxes can otherwise anchor to a neighbor's glyph.
                model = prose_baseline(components(image))
                if model and clean:
                    cap, baseline, slope = model
                    array = np.array(image)
                    yy, xx = np.indices(array.shape)
                    array[
                        (yy < baseline + slope * xx - 1.1 * cap)
                        | (yy > baseline + slope * xx + 0.25 * cap)
                    ] = 255
                    image = Image.fromarray(array)
                # Character boxes retain neighboring-word anchors even when
                # OCR has dropped a letter in the target word.
                stream = io.BytesIO()
                image.save(stream, format="PNG")
                result = subprocess.run(
                    ["tesseract", "stdin", "stdout", "--psm", "7"]
                    + self.ocr_args
                    + ["-c", "hocr_char_boxes=1", "-c", "tessedit_create_hocr=1"],
                    input=stream.getvalue(),
                    capture_output=True,
                    check=True,
                    timeout=60,
                    env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                )
                root = etree.fromstring(result.stdout)
                text, chars = "", []
                for word_node in root.xpath('//*[@class="ocrx_word"]'):
                    if text:
                        text += " "
                    for char in word_node.xpath('.//*[@class="ocrx_cinfo"]'):
                        box = list(
                            map(
                                int,
                                re.search(r"x_bboxes ([\d ]+)", char.get("title"))[
                                    1
                                ].split(),
                            )
                        )
                        chars.append(
                            dict(
                                start=len(text),
                                end=len(text) + len(char.text or ""),
                                bbox=box,
                            )
                        )
                        text += char.text or ""
            write_json(path, dict(text=text, characters=chars))
        words = []
        geometry_token = re.compile(r"[^\W_]+") if self.language != "eng" else TOKEN
        for match in geometry_token.finditer(text):
            boxes = [
                c["bbox"]
                for c in chars
                if c["end"] > match.start() and c["start"] < match.end()
            ]
            if boxes:
                # Pixel edges convert back to PDF points before word cropping.
                words.append(
                    (
                        rect.x0 + min(b[0] for b in boxes) * rect.width / pix.width,
                        rect.y0 + min(b[1] for b in boxes) * rect.height / pix.height,
                        rect.x0 + max(b[2] for b in boxes) * rect.width / pix.width,
                        rect.y0 + max(b[3] for b in boxes) * rect.height / pix.height,
                        match.group(),
                    )
                )
        if raw:
            characters = [
                {
                    **c,
                    "bbox": [
                        rect.x0 + c["bbox"][0] * rect.width / pix.width,
                        rect.y0 + c["bbox"][1] * rect.height / pix.height,
                        rect.x0 + c["bbox"][2] * rect.width / pix.width,
                        rect.y0 + c["bbox"][3] * rect.height / pix.height,
                    ],
                }
                for c in chars
            ]
            return dict(text=text, characters=characters, words=words)
        return words

    def fragment(self, row, word, candidate):
        return self.recognize_crop(row, word, candidate, fragment=True)

    def character_support(self, evidence, word, candidate):
        """Retain a source LSTM alternative for one confusable character.

        x_confs are raw OCR scores, not probabilities or independent votes.
        The 20-point floor bounds admission and is an empirical guard.
        """
        if self.language != "eng" or not confusable(word, candidate):
            return None
        crop = self.work / (evidence.get("cache_key", "") + ".png")
        if not crop.is_file():
            return None
        fingerprint = digest(
            crop.read_bytes()
            + self.model_hash.encode()
            + self.version.encode()
            + inspect.getsource(self.character_support).encode()
        )
        cache = self.work / ("choices-" + fingerprint + ".json")
        if cache.exists():
            data = read_json(cache)
        else:
            result = subprocess.run(
                ["tesseract", str(crop), "stdout", "--psm", "8"]
                + self.ocr_args
                + ["-c", "lstm_choice_mode=2", "hocr"],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
            root = etree.HTML(result.stdout.encode())
            nodes = root.xpath('//*[@class="ocrx_word"]')
            alternatives = []
            if len(nodes) == 1:
                for choices in nodes[0].xpath('.//*[starts-with(@id,"lstm_choices_")]'):
                    letters = {}
                    for c in choices:
                        letter = (c.text or "").lower()
                        score = float(c.get("title").split()[-1])
                        letters[letter] = max(letters.get(letter, 0), score)
                    alternatives.append(letters)
            data = dict(
                alternatives=alternatives,
                crop_sha256=digest(crop.read_bytes()),
                engine="tesseract-lstm",
                raw_score_floor=20,
                traineddata_sha256=self.model_hash,
                version=self.version.splitlines()[0],
            )
            write_json(cache, data)
        changed = [
            i for i, (a, b) in enumerate(zip(word.lower(), candidate.lower())) if a != b
        ]
        if len(data["alternatives"]) != len(word) or len(changed) != 1:
            return None
        i = changed[0]
        if data["alternatives"][i].get(candidate[i].lower(), 0) < 20:
            return None
        # Every unchanged character must also have source support.
        if any(
            data["alternatives"][j].get(c.lower(), 0) <= 0
            for j, c in enumerate(candidate)
            if j != i
        ):
            return None
        return dict(data, changed_index=i, cache_key=fingerprint)

    def __call__(self, row, word, candidate):
        evidence = self.recognize_crop(row, word, candidate)
        if self.language == "eng":
            return evidence

        def supported(value):
            return len(value.get("readings", [])) == 2 and all(
                TOKEN.findall(r.lower()) == [candidate.lower()]
                for r in value["readings"]
            )

        if supported(evidence):
            return evidence
        attempts = [evidence]
        # Fixed crop variants test clearance sensitivity, not arbitrary search
        # windows: normal word, tight word, then original unmasked line geometry.
        for variant in (1, 2):
            alternate = self.recognize_crop(row, word, candidate, variant=variant)
            attempts.append(alternate)
            if supported(alternate):
                return dict(
                    alternate, crop_variants=attempts[:-1], selected_variant=variant
                )
        return dict(evidence, crop_variants=attempts[1:])

    def source_choice(self, row, word, choices):
        """Corroborate a bounded shortlist in whole-page and two local contexts."""
        from common import cache_path
        from ocr import merge_rows, nearest

        if not hasattr(self, "page_witnesses"):
            self.page_witnesses = {}
        n = row["page"]
        if n not in self.page_witnesses:
            path = cache_path(self.work.parent, "tesseract") / f"{n:04}.json"
            self.page_witnesses[n] = (
                merge_rows(read_json(path)["lines"]) if path.exists() else []
            )
        cached = nearest(row, self.page_witnesses[n])
        if not cached:
            return None
        observations = [cached["text"]] + [
            self.line_words(self.doc[n - 1], row, raw=True, dpi=dpi)["text"]
            for dpi in (600, 300)
        ]
        proofs = []
        for choice in choices:
            readings = [
                anchored_reading(row["text"], text, word, choice["term"])
                for text in observations
            ]
            if all(value == choice["term"] for value in readings):
                proofs.append(
                    dict(
                        term=choice["term"],
                        readings=readings,
                        observations=observations,
                        engine="tesseract-page-and-two-local-scales",
                        scales=[600, 300],
                    )
                )
        if len(proofs) != 1:
            return None
        proof = proofs[0]
        page = self.doc[n - 1]
        x, y, xx, yy = row["bbox"]
        rect = (
            pymupdf.Rect(
                x * page.rect.width,
                y * page.rect.height,
                xx * page.rect.width,
                yy * page.rect.height,
            )
            & page.rect
        )
        data = page.get_pixmap(dpi=600, clip=rect, colorspace=pymupdf.csGRAY).tobytes(
            "png"
        )
        key = digest(
            data
            + self.model_hash.encode()
            + self.version.encode()
            + b"lexical-context-evidence-v1"
        )
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / (key + ".png")).write_bytes(data)
        return dict(
            proof,
            cache_key=key,
            crop_sha256=digest(data),
            traineddata_sha256=self.model_hash,
            version=self.version,
            rect=list(rect),
            dpi=600,
            kind="context-line",
        )

    def recognize_crop(self, row, word, candidate, fragment=False, variant=0):
        page = self.doc[row["page"] - 1]
        cy = (row["bbox"][1] + row["bbox"][3]) / 2
        # Embedded words locate the crop. Their old OCR reading is retained
        # separately; it can resolve a weak context margin only with fresh OCR.
        words = [
            w
            for w in page.get_text("words")
            if abs((w[1] + w[3]) / 2 / page.rect.height - cy) < 0.012
            and row["bbox"][0] - 0.02 <= w[0] / page.rect.width <= row["bbox"][2]
        ]

        image_only = not words
        if image_only:
            words = self.line_words(page, row, clean=variant != 2)
        if not words:
            return dict(readings=[], reason="no-word-geometry")
        located = locate_word(
            row["text"],
            sorted(words, key=lambda w: w[0]),
            word,
            candidate,
            edge_anchor=self.language != "eng",
        )
        if located is None:
            return dict(readings=[], reason="ambiguous-word-geometry")
        rect = pymupdf.Rect(located[:4])
        # One PDF point surrounds the glyph box; a white 20-pixel frame lets
        # single-word segmentation work without neighboring annotation noise.
        padding = 0.2 if (fragment or variant) and self.language != "eng" else 1
        # Faint terminal glyphs can extend beyond the OCR word box. Expand
        # horizontally by one measured glyph, retaining tight vertical bounds.
        horizontal = (
            padding
            if self.language == "eng" or not fragment
            else max(1.2, rect.width / max(1, len(located[4])))
        )
        if self.language != "eng" and variant != 2:
            following = [w[0] for w in words if w[0] >= rect.x1 and w != located]
            if following:
                # Stop halfway across the next word space; faint glyph clearance
                # must never include the next word's first letter.
                horizontal = min(horizontal, max(0.2, (min(following) - rect.x1) / 2))
        rect = pymupdf.Rect(
            rect.x0 - padding,
            rect.y0 - padding,
            rect.x1 + horizontal,
            rect.y1 + padding,
        )
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(600 / 72, 600 / 72),
            clip=rect,
            colorspace=pymupdf.csGRAY,
        )
        image = ImageOps.expand(
            Image.frombytes("L", (pix.width, pix.height), pix.samples),
            border=20,
            fill=255,
        )
        key = digest(
            pix.samples
            + self.version.encode()
            + repr(list(rect)).encode()
            + self.model_hash.encode()
            + b"word-crop-v3-600dpi-psm8-13"
        )
        cache = self.work / (key + ".json")
        if cache.exists():
            return self.with_fallback(
                read_json(cache), self.work / (key + ".png"), candidate
            )
        self.work.mkdir(parents=True, exist_ok=True)
        image.save(self.work / (key + ".png"))
        readings = []
        for psm in (8, 13):
            result = subprocess.run(
                [
                    "tesseract",
                    str(self.work / (key + ".png")),
                    "stdout",
                    "--psm",
                    str(psm),
                ]
                + self.ocr_args,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
            readings.append(result.stdout.strip())
        evidence = dict(
            readings=readings,
            embedded_word="" if image_only else located[4],
            cache_key=key,
            crop_sha256=digest((self.work / (key + ".png")).read_bytes()),
            traineddata_sha256=self.model_hash,
            rect=list(rect),
            engine="tesseract",
            version=self.version.splitlines()[0],
            dpi=600,
            segmentation_modes=[8, 13],
        )
        if image_only:
            evidence["geometry_source"] = "local-line-ocr"
        write_json(cache, evidence)
        return self.with_fallback(evidence, self.work / (key + ".png"), candidate)

    def with_fallback(self, evidence, path, candidate):
        """Bound Cyrillic fallback to crop conflicts; never download models."""
        if self.models is None or all(
            TOKEN.findall(r.lower()) == [candidate.lower()]
            for r in evidence["readings"]
        ):
            return evidence
        import importlib.metadata
        import numpy as np
        from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType

        names = (
            "cyrillic_PP-OCRv5_rec_mobile.onnx",
            "ch_PP-OCRv4_det_mobile.onnx",
            "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        )
        resources = {name: digest((self.models / name).read_bytes()) for name in names}
        runtime = {
            name: importlib.metadata.version(name)
            for name in ("rapidocr", "onnxruntime")
        }
        key = digest(
            path.read_bytes()
            + repr(sorted(resources.items())).encode()
            + repr(sorted(runtime.items())).encode()
            + b"rapid-word-600-300-v1"
        )
        cache = self.work / ("rapid-" + key + ".json")
        if cache.exists():
            alternate = read_json(cache)
        else:
            if self.rapid is None:
                self.rapid = RapidOCR(
                    params={
                        "Rec.lang_type": LangRec.CYRILLIC,
                        "Rec.ocr_version": OCRVersion.PPOCRV5,
                        "Rec.model_type": ModelType.MOBILE,
                        "Det.ocr_version": OCRVersion.PPOCRV4,
                        "Det.model_type": ModelType.MOBILE,
                        "Rec.model_path": str(self.models / names[0]),
                        "Det.model_path": str(self.models / names[1]),
                        "Cls.model_path": str(self.models / names[2]),
                        "EngineConfig.onnxruntime.intra_op_num_threads": 1,
                        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    }
                )
            image = Image.open(path).convert("RGB")
            readings, scores = [], []
            # Two fixed raster scales check stability without a candidate-aware
            # prompt. They remain two readings from one engine, not two votes.
            for variant in (
                image,
                image.resize((max(1, image.width // 2), max(1, image.height // 2))),
            ):
                result = self.rapid(np.array(variant), use_det=False, use_cls=False)
                readings.append(" ".join(result.txts or ()))
                scores.append(min(result.scores) if result.scores else 0)
            alternate = dict(
                readings=readings,
                scores=scores,
                resources=resources,
                runtime=runtime,
                scales=[1, 0.5],
                cache_key=key,
            )
            write_json(cache, alternate)
        evidence = dict(evidence, alternate_crop=alternate)
        # 0.90 is an empirical recognition floor, not a calibrated probability.
        # Context, dictionary, recurrence and exact placement are still required.
        if (
            len(alternate["readings"]) == 2
            and min(alternate["scores"]) >= 0.9
            and all(
                TOKEN.findall(r.lower()) == [candidate.lower()]
                for r in alternate["readings"]
            )
        ):
            evidence.update(
                tesseract_readings=evidence["readings"],
                readings=alternate["readings"],
                engine="rapidocr",
            )
        return evidence


def anchored_reading(original, observation, word, candidate):
    """Require two exact neighbors and a unique location for a lexical vote."""
    words = [(0, 0, 0, 0, t) for t in TOKEN.findall(observation.lower())]
    located = locate_word(original, words, word, candidate)
    if not located:
        return None
    own = TOKEN.findall(original.lower())
    other = [w[4] for w in words]
    if own.count(word.lower()) != 1:
        return None
    i, j = own.index(word.lower()), words.index(located)
    anchors = sum(
        own[i + d] == other[j + d]
        for d in (-2, -1, 1, 2)
        if 0 <= i + d < len(own) and 0 <= j + d < len(other)
    )
    return located[4] if anchors >= 2 else None


def repair(model, pages, book, ranker, recognize, audit, skip_rows=(), judge=None):
    """Decide from one immutable assembled book; apply exact source-row edits."""
    if book.get("lexical_repair_policy") == RepairPolicy.CORRECTED_READING:
        from reading_repair import repair_reading

        return repair_reading(
            model, pages, book, ranker, recognize, audit, judge, skip_rows
        )
    rows = {
        r.get("row_id", row_id(int(n), r, book.get("source_sha256", ""))): r
        for n, rr in pages.items()
        for r in rr
    }
    decisions, seen = [], set()
    edits = []
    for entry in model:
        if ranker.excluded(entry, book):
            continue
        for block in entry["blocks"] + entry["notes"]:
            if block["kind"] not in ("text", "quote", "note"):
                continue
            for match in TOKEN.finditer(block["text"]):
                word = match.group()
                if (
                    len(word) < 4
                    or not word.isalpha()
                    or not (
                        word.islower() or (ranker.language != "en" and word.istitle())
                    )
                    or ranker.known(word)
                ):
                    continue
                sources = [
                    s
                    for s in block["sources"]
                    if s["start"] <= match.start() and s["end"] >= match.end()
                ]
                if len(sources) != 1:
                    continue  # Never patch a reconstructed word across source rows.
                row = rows[sources[0]["row_id"]]
                ident = sources[0]["row_id"]
                if ident in skip_rows:
                    continue
                identity = (ident, word)
                if identity in seen:
                    continue
                seen.add(identity)
                choices = ranker.rank(word, block["text"], match.start())
                if not choices:
                    continue
                best = choices[0]
                margin = (
                    best["context_score"] - choices[1]["context_score"]
                    if len(choices) > 1
                    else None
                )
                decision = dict(
                    page=row["page"],
                    row_id=ident,
                    before=word,
                    after=(
                        best["term"].capitalize() if word.istitle() else best["term"]
                    ),
                    bbox=row["bbox"],
                    candidates=choices[:5],
                    margin=round(margin, 6) if margin is not None else None,
                    action="review",
                )
                decisions.append(decision)
                # Common, already-attested targets bound automatic edits;
                # rare terminology remains review-only.
                if (
                    not best["confusion_supported"]
                    or (
                        ranker.language != "en"
                        and not ranker.dictionary.lookup(best["term"])
                    )
                    or best["book_count"] < 2
                    or zipf_frequency(best["term"], ranker.language)
                    < (3 if ranker.language == "en" else 2)
                ):
                    decision["reason"] = "insufficient-lexical-evidence"
                    continue
                if len(re.findall(r"\b" + re.escape(word) + r"\b", row["text"])) != 1:
                    decision["reason"] = "ambiguous-row-occurrence"
                    continue
                evidence = recognize(row, word, best["term"])
                decision["crop"] = evidence
                readings = [TOKEN.findall(s.lower()) for s in evidence["readings"]]
                if len(readings) != 2 or any(
                    words != [best["term"]] for words in readings
                ):
                    decision["reason"] = "crop-does-not-corroborate"
                    continue
                # An e-fold margin is a ranking guard, not a confidence value.
                # Weak margins additionally require the independent embedded
                # OCR word to agree with both fresh crop segmentation modes.
                if (
                    margin is not None
                    and margin < 1
                    and TOKEN.findall(evidence.get("embedded_word", "").lower())
                    != [best["term"]]
                ):
                    decision["reason"] = "weak-context-without-embedded-corroboration"
                    continue
                decision["action"] = "correct"
                decision["reason"] = (
                    "context-and-two-rapid-crop-readings"
                    if evidence.get("engine") == "rapidocr"
                    else "context-and-two-segmentation-crop-readings"
                )
                edits.append(
                    (
                        row,
                        dict(
                            page=row["page"],
                            before=word,
                            after=(
                                best["term"].capitalize()
                                if word.istitle()
                                else best["term"]
                            ),
                            count=1,
                            whole_word=True,
                            evidence=decision,
                        ),
                    )
                )
    # A sparse book Markov model may favor a common but wrong inflection.
    # For non-English scans, source agreement can select another of five
    # one-edit dictionary candidates without requiring it twice in the book.
    # Whole-page OCR and two local scales must agree with two exact neighbors;
    # low-scoring isolated-word fallback readings alone never license a fix.
    if ranker.language != "en" and callable(getattr(recognize, "source_choice", None)):
        for decision in decisions:
            if decision["action"] != "review":
                continue
            row = rows[decision["row_id"]]
            word = decision["before"]
            if len(re.findall(r"\b" + re.escape(word) + r"\b", row["text"])) != 1:
                continue
            shortlist = [
                c
                for c in decision["candidates"]
                if c["distance"] == 1
                and c["term"] not in ranker.protected
                and ranker.dictionary.lookup(c["term"])
                and zipf_frequency(c["term"], ranker.language) >= 3
            ]
            proof = recognize.source_choice(row, word, shortlist) if shortlist else None
            if not proof:
                continue
            after = proof["term"].capitalize() if word.istitle() else proof["term"]
            decision.update(
                initial_proposal=decision["after"],
                after=after,
                action="correct",
                reason="three-context-source-witnesses",
                crop=proof,
            )
            edits.append(
                (
                    row,
                    dict(
                        page=row["page"],
                        before=word,
                        after=after,
                        count=1,
                        whole_word=True,
                        evidence=decision,
                    ),
                )
            )
    for row, edit in edits:
        apply_edits([row], [edit], audit, "whole-book-lexical-repair")
    return dict(
        mode="local-crop-corroborated; immutable whole-book context",
        resources=ranker.resources,
        corrected=len(edits),
        decisions=decisions,
    )
