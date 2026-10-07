"""Translation quality judgments from TypeSafe Jev, cached and version-pinned.

Each translated paragraph is one request whose state holds the source, the
translation and the glossary entries it uses. Four Nouls ask for specific
error kinds and one Score rates fluency; code combines them. Responses are
cached by request hash, so reruns are offline replays. Sending a request
sends that paragraph and its translation to TypeSafe's API.
"""

import hashlib
import json
import time
from pathlib import Path

import requests

from jev_rank import key

MODEL = "jev-1.13.0"
URL = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {
    "omission": {
        "type": "noul",
        "instructions": "Does `translation` leave out any statement, detail, "
        "qualifier, name or number that `source` contains?",
        "criteria": {
            "true": "At least one piece of content in the source has no "
            "counterpart in the translation.",
            "false": "Everything in the source is rendered, even if reworded, "
            "merged or reordered.",
        },
    },
    "meaning_error": {
        "type": "noul",
        "instructions": "Does any sentence of `translation` state something "
        "different from what `source` says (wrong meaning, negation, "
        "relationship, number or name)?",
    },
    "addition": {
        "type": "noul",
        "instructions": "Does `translation` add any statement or detail that "
        "is not in `source`?",
    },
    "terminology": {
        "type": "noul",
        "instructions": "Is any term from `glossary` that occurs in `source` "
        "rendered in `translation` with a different word or spelling from its "
        "glossary rendering (ignoring grammatical inflection)?",
    },
    "fluency": {
        "type": "score",
        "instructions": "How natural is `translation` as prose in the target "
        "language, judged as a reader of a published book would?",
        "criteria": [
            "Ungrammatical or garbled in places; hard to follow.",
            "Grammatical but stiff: word-by-word structure from the source "
            "is obvious.",
            "Natural on the whole, with an occasional awkward or calqued phrase.",
            "Reads like natural, published prose written in the target language.",
        ],
    },
}
NOULS = ("omission", "meaning_error", "addition", "terminology")


def validate(response):
    if response.get("model") != MODEL:
        raise ValueError("Unexpected model version")
    answers = response.get("answers", {})
    if set(answers) != set(QUESTIONS):
        raise ValueError("Missing or unexpected answer IDs")
    for name in NOULS:
        if not 0 <= answers[name].get("noul", -1) <= 1:
            raise ValueError(f"Invalid noul for {name}")
    if (
        not 0
        <= answers["fluency"].get("score", -1)
        <= len(QUESTIONS["fluency"]["criteria"]) - 1
    ):
        raise ValueError("Invalid fluency score")
    return response


def post(payload, cache, attempts=5):
    """Replay a cached response for this exact payload, or request and cache
    it; checks only the pinned model version."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    path = Path(cache) / (hashlib.sha256(encoded).hexdigest() + ".json")
    if path.exists():
        return json.loads(path.read_text())
    for attempt in range(attempts):
        response = requests.post(
            URL,
            headers={"Authorization": "Bearer " + key()},
            json=payload,
            timeout=120,
        )
        if response.status_code in (429, 529) and attempt < attempts - 1:
            time.sleep(2**attempt)
            continue
        response.raise_for_status()
        break
    result = response.json()
    if result.get("model") != MODEL:
        raise ValueError("Unexpected model version")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def evaluate(payload, cache):
    """A validated judgment response, cached."""
    return validate(post(payload, cache))


def judgments(source, translation, glossary, cache):
    """{omission, meaning_error, addition, terminology: P(yes), fluency: 0-1}."""
    terms = {
        term: rendering
        for term, rendering in glossary.items()
        if term.lower() in source.lower()
    }
    payload = {
        "model": MODEL,
        "state": {"source": source, "translation": translation, "glossary": terms},
        "questions": QUESTIONS,
    }
    answers = evaluate(payload, cache)["answers"]
    top = len(QUESTIONS["fluency"]["criteria"]) - 1
    result = {name: answers[name]["noul"] for name in NOULS}
    result["fluency"] = answers["fluency"]["score"] / top
    return result


def score(result, weights=None):
    """0-100 quality from judgments. Error probabilities under 0.5 count as no
    error and rise linearly to full weight at 1.0; the most likely error
    dominates and fluency adjusts the rest. On deliberately damaged paragraphs
    every damaged version had an error probability of 0.85 or more and every
    original 0.56 or less."""
    weights = weights or {
        "omission": 1.0,
        "meaning_error": 1.0,
        "addition": 0.7,
        "terminology": 0.5,
    }
    error = max(max(0.0, (result[n] - 0.5) / 0.5) * w for n, w in weights.items())
    return round(100 * (1 - error) * (0.7 + 0.3 * result["fluency"]))


def labels(result):
    return [f"{name} {result[name]:.2f}" for name in NOULS if result[name] > 0.5]


def judge_pairs(pairs, glossary, cache, workers=4):
    """(score, labels) per (source, translation) pair, several requests at once."""
    from concurrent.futures import ThreadPoolExecutor

    def one(pair):
        result = judgments(pair[0], pair[1], glossary, cache)
        return score(result), labels(result)

    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, pairs))
