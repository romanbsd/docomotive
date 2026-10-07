"""Jev translation judgments: validated, cached, and combined into a score."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jev_judge as J


def response(omission=0.1, meaning=0.1, addition=0.1, terminology=0.1, fluency=3.0):
    answers = {
        "omission": {"type": "noul", "noul": omission},
        "meaning_error": {"type": "noul", "noul": meaning},
        "addition": {"type": "noul", "noul": addition},
        "terminology": {"type": "noul", "noul": terminology},
        "fluency": {"type": "score", "score": fluency},
    }
    return {"model": J.MODEL, "answers": answers}


class JevJudgeTests(unittest.TestCase):
    def test_validation_rejects_other_models_and_missing_answers(self):
        J.validate(response())
        with self.assertRaises(ValueError):
            J.validate({**response(), "model": "jev-0.1"})
        broken = response()
        del broken["answers"]["addition"]
        with self.assertRaises(ValueError):
            J.validate(broken)

    def test_cached_responses_replay_without_network(self):
        cache = Path(tempfile.mkdtemp())
        payload = {
            "model": J.MODEL,
            "state": {"source": "A", "translation": "Б", "glossary": {}},
            "questions": J.QUESTIONS,
        }
        name = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        (cache / f"{name}.json").write_text(json.dumps(response(omission=0.9)))
        with patch.object(J.requests, "post", side_effect=AssertionError("network")):
            result = J.judgments("A", "Б", {"tea": "чай"}, cache)
        self.assertEqual(result["omission"], 0.9)
        self.assertEqual(result["fluency"], 1.0)

    def test_score_ignores_low_error_probabilities_and_drops_on_errors(self):
        clean = {"omission": 0.4, "meaning_error": 0.3, "addition": 0.2}
        clean |= {"terminology": 0.1, "fluency": 1.0}
        self.assertEqual(J.score(clean), 100)
        self.assertEqual(J.labels(clean), [])
        cut = clean | {"omission": 0.95}
        self.assertLess(J.score(cut), 15)
        self.assertEqual(J.labels(cut), ["omission 0.95"])
        stiff = clean | {"fluency": 1 / 3}
        self.assertEqual(J.score(stiff), 80)


if __name__ == "__main__":
    unittest.main()
