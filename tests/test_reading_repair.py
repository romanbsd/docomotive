import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lexical_repair import ContextRanker, repair
from reading_repair import source_origin
from jev_spelling import judgments
from test_lexical_repair import entry


class Dictionary:
    def lookup(self, word):
        return word in {"researching", "synchronous", "quantitative", "causal", "carl"}


class ReadingRepairTests(unittest.TestCase):
    def run_case(self, text, original, candidate, readings, judgment, protected=()):
        section, row = entry(text)
        # Context before/after is passed to the judge without book-wide leakage.
        section["blocks"].insert(
            0, dict(text="Earlier context.", kind="text", sources=[])
        )
        section["blocks"].append(dict(text="Later context.", kind="text", sources=[]))
        ranker = ContextRanker([section], {}, Dictionary(), protected)
        seen = []

        def judge(state):
            seen.append(state)
            return judgment

        audit = []
        result = repair(
            [section],
            {"1": [row]},
            {"lexical_repair_policy": "corrected-reading"},
            ranker,
            lambda *args: dict(readings=readings),
            audit,
            judge=judge,
        )
        return row, result, audit, seen

    def test_singleton_target_is_checked_and_corrected(self):
        row, result, audit, states = self.run_case(
            "In researehing this relationship.",
            "researehing",
            "researching",
            ["researehing"] * 2,
            dict(spelling_error=0.99, meaning_preserved=0.99),
        )
        self.assertEqual(row["text"], "In researching this relationship.")
        self.assertEqual(result["corrected"], 1)
        self.assertEqual(states[0]["preceding_context"], "Earlier context.")
        self.assertEqual(states[0]["following_context"], "Later context.")
        self.assertEqual(audit[0]["kind"], "reading-edition-spelling")
        self.assertEqual(
            result["decisions"][0]["source_origin"], "source-reading-supported"
        )
        # Matching OCR readings are not mislabeled as proof of a printed typo.
        self.assertNotEqual(result["decisions"][0]["source_origin"], "printed-error")

    def test_printed_spelling_can_be_corrected_and_classified(self):
        row, result, _, _ = self.run_case(
            "The synchronious movement.",
            "synchronious",
            "synchronous",
            ["synchronious"] * 2,
            dict(spelling_error=0.99, meaning_preserved=0.99),
        )
        self.assertEqual(row["text"], "The synchronous movement.")
        self.assertEqual(result["corrected"], 1)
        self.assertEqual(
            result["decisions"][0]["correction_class"], "reading-edition-spelling"
        )

    def test_meaning_changing_term_is_not_replaced(self):
        row, result, _, _ = self.run_case(
            "The acausal connection.",
            "acausal",
            "causal",
            ["causal"] * 2,
            dict(spelling_error=0.99, meaning_preserved=0.01),
        )
        self.assertEqual(row["text"], "The acausal connection.")
        self.assertEqual(result["corrected"], 0)

    def test_protected_word_and_uncertain_judgment_stay_put(self):
        row, result, _, states = self.run_case(
            "In researehing this relationship.",
            "researehing",
            "researching",
            ["researching"] * 2,
            dict(spelling_error=1.0, meaning_preserved=1.0),
            ["researehing"],
        )
        self.assertEqual(result["corrected"], 0)
        self.assertEqual(states, [])
        row, result, _, _ = self.run_case(
            "In researehing this relationship.",
            "researehing",
            "researching",
            ["researehing"] * 2,
            dict(spelling_error=0.5, meaning_preserved=1.0),
        )
        self.assertEqual(result["corrected"], 0)

    def test_missing_judge_does_not_silently_apply(self):
        section, row = entry("In researehing this relationship.")
        ranker = ContextRanker([section], {}, Dictionary())
        report = repair(
            [section],
            {"1": [row]},
            {"lexical_repair_policy": "corrected-reading"},
            ranker,
            lambda *args: dict(readings=["researehing"] * 2),
            [],
        )
        self.assertEqual(report["corrected"], 0)
        self.assertEqual(report["decisions"][0]["reason"], "context-judgment-required")

    def test_source_origin_abstains_on_conflicting_or_missing_readings(self):
        self.assertEqual(
            source_origin("wrong", "right", dict(readings=["right", "wrong"])),
            "unresolved",
        )
        self.assertEqual(
            source_origin("wrong", "right", dict(readings=[])), "unresolved"
        )
        self.assertEqual(
            source_origin("wrong", "right", dict(readings=["right"] * 2)),
            "ocr-supported",
        )

    def test_jev_validation_rejects_malformed_probabilities(self):
        from jev_spelling import MODEL

        for value in [float("nan"), float("inf"), -1, 2, True, "1"]:
            response = dict(
                model=MODEL,
                answers={
                    name: dict(type="noul", noul=value)
                    for name in ["spelling_error", "meaning_preserved"]
                },
            )
            with (
                self.subTest(value=value),
                patch("jev_spelling.post", return_value=response),
            ):
                with self.assertRaises(ValueError):
                    judgments({}, Path("/tmp/unused-jev-cache"))


class ReadingPlacementTests(unittest.TestCase):
    def test_name_candidate_requires_attested_phrase_and_rejects_identity_change(self):
        section, row = entry("Cari JUNG")
        training, training_row = entry("Carl Jung. Carl Jung. Carl Jung.", page=2)
        ranker = ContextRanker([section, training], {}, Dictionary())
        report = repair(
            [section, training],
            {"1": [row], "2": [training_row]},
            {"lexical_repair_policy": "corrected-reading"},
            ranker,
            lambda *args: dict(readings=["Carl"] * 2),
            [],
            judge=lambda state: dict(spelling_error=0.99, meaning_preserved=0.99),
        )
        self.assertEqual(row["text"], "Carl JUNG")
        self.assertEqual(report["corrected"], 1)
        section, row = entry("Cari JUNG")
        report = repair(
            [section, training],
            {"1": [row], "2": [training_row]},
            {"lexical_repair_policy": "corrected-reading"},
            ranker,
            lambda *args: dict(readings=["Carl"] * 2),
            [],
            judge=lambda state: dict(spelling_error=0.99, meaning_preserved=0.01),
        )
        self.assertEqual(row["text"], "Cari JUNG")
        self.assertEqual(report["corrected"], 0)

    def test_source_edits_preserve_styles_and_frozen_statistics(self):
        section, row = entry("The synchronious movement.")
        row["inline"] = [dict(start=4, end=16, tag="em")]
        ranker = ContextRanker([section], {}, Dictionary())
        before = dict(ranker.pairs)
        repair(
            [section],
            {"1": [row]},
            {"lexical_repair_policy": "corrected-reading"},
            ranker,
            lambda *args: dict(readings=["synchronious"] * 2),
            [],
            judge=lambda state: dict(spelling_error=0.99, meaning_preserved=0.99),
        )
        self.assertEqual(row["text"], "The synchronous movement.")
        self.assertEqual(row["inline"], [dict(start=4, end=15, tag="em")])
        self.assertEqual(dict(ranker.pairs), before)

    def test_nonfinite_judgment_and_repeated_row_occurrence_cannot_apply(self):
        section, row = entry("In researehing this relationship.")
        ranker = ContextRanker([section], {}, Dictionary())
        with self.assertRaises(ValueError):
            repair(
                [section],
                {"1": [row]},
                {"lexical_repair_policy": "corrected-reading"},
                ranker,
                lambda *args: dict(readings=["researching"] * 2),
                [],
                judge=lambda state: dict(
                    spelling_error=float("nan"), meaning_preserved=1.0
                ),
            )
        self.assertEqual(row["text"], "In researehing this relationship.")


class CharacterEvidenceTests(unittest.TestCase):
    def test_casefolded_alternatives_keep_maximum_score_and_parse_xml_declaration(self):
        import tempfile
        from types import SimpleNamespace
        from lexical_repair import CropRecognizer

        xml = '<?xml version="1.0" encoding="UTF-8"?><html><span class="ocrx_word">'
        for values in [
            [("b", 80)],
            [("e", 90), ("c", 42)],
            [("r", 85)],
            [("e", 90), ("E", 0)],
        ]:
            xml += (
                '<span id="lstm_choices_'
                + str(len(xml))
                + '">'
                + "".join(
                    '<span title="x_confs ' + str(score) + '">' + letter + "</span>"
                    for letter, score in values
                )
                + "</span>"
            )
        xml += "</span></html>"
        with tempfile.TemporaryDirectory() as directory:
            rec = CropRecognizer.__new__(CropRecognizer)
            rec.language = "eng"
            rec.work = Path(directory)
            rec.model_hash = "model"
            rec.version = "version"
            rec.ocr_args = []
            (rec.work / "word.png").write_bytes(b"fixture")
            with patch(
                "lexical_repair.subprocess.run",
                return_value=SimpleNamespace(stdout=xml),
            ):
                result = rec.character_support({"cache_key": "word"}, "bere", "bcre")
            self.assertIsNotNone(result)
            self.assertEqual(result["alternatives"][3]["e"], 90)

    def test_local_character_evidence_works_without_jev(self):
        from lexical_repair import CropRecognizer

        section, row = entry("In researehing this relationship.")
        ranker = ContextRanker([section], {}, Dictionary())

        class Recognizer:
            def __call__(self, *args):
                return dict(readings=["researehing"] * 2)

            def character_support(self, *args):
                return dict(changed_index=6, raw_score_floor=20)

        report = repair(
            [section],
            {"1": [row]},
            {"lexical_repair_policy": "corrected-reading"},
            ranker,
            Recognizer(),
            [],
        )
        self.assertEqual(row["text"], "In researching this relationship.")
        self.assertEqual(
            report["decisions"][0]["reason"], "character-alternatives-and-context"
        )

    def test_approved_unique_invalid_spelling_is_consistent_across_prose(self):
        first, row1 = entry("The synchronious movement.", page=1)
        second, row2 = entry("A synchronious event.", page=2)
        ranker = ContextRanker([first, second], {}, Dictionary())
        calls = []

        def judge(state):
            calls.append(state)
            return dict(spelling_error=0.99, meaning_preserved=0.99)

        report = repair(
            [first, second],
            {"1": [row1], "2": [row2]},
            {"lexical_repair_policy": "corrected-reading"},
            ranker,
            lambda *args: dict(readings=["synchronious"] * 2),
            [],
            judge=judge,
        )
        self.assertEqual(report["corrected"], 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            report["decisions"][1]["reason"], "approved-invalid-spelling-rule"
        )


class CachedJudgmentTests(unittest.TestCase):
    def test_build_judge_replays_locally_and_never_sends_uncached_state(self):
        import tempfile
        from common import digest
        from jev_spelling import judge, MODEL, QUESTIONS

        state = {"original_token": "broken", "candidate_token": "correct"}
        payload = {"model": MODEL, "state": state, "questions": QUESTIONS}
        fingerprint = digest(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        )
        with tempfile.TemporaryDirectory() as directory:
            cached = judge(Path(directory))
            with patch(
                "jev_judge.requests.post",
                side_effect=AssertionError("Unexpected network"),
            ):
                self.assertIsNone(cached(state))
                response = {
                    "model": MODEL,
                    "answers": {
                        name: {"type": "noul", "noul": 0.99} for name in QUESTIONS
                    },
                }
                Path(directory, fingerprint + ".json").write_text(json.dumps(response))
                self.assertEqual(cached(state)["spelling_error"], 0.99)


if __name__ == "__main__":
    unittest.main()
