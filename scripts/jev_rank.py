"""Optional, cached TypeSafe Choice ranking. No book edits and no secrets in artifacts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import requests

MODEL = "jev-1.13.0"


def key():
    value = os.environ.get("TYPESAFE_API_KEY")
    if not value and Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if line.strip().startswith("TYPESAFE_API_KEY="):
                value = line.strip().partition("=")[2].strip().strip("\"'")
                break
    if not value:
        raise ValueError("TYPESAFE_API_KEY is required for an uncached request")
    return value


def validate(response, questions):
    if response.get("model") != MODEL:
        raise ValueError("Unexpected model version")
    answers = response.get("answers", {})
    if set(answers) != set(questions):
        raise ValueError("Missing or unexpected answer IDs")
    for name, answer in answers.items():
        if (
            answer.get("type") != "choice"
            or answer.get("choice") not in questions[name]["criteria"]
        ):
            raise ValueError("Invalid Choice response")
        if not 0 <= answer.get("confidence", -1) <= 1:
            raise ValueError("Invalid confidence")
        probabilities = answer.get("probabilities", {})
        if set(probabilities) != set(questions[name]["criteria"]) or any(
            not 0 <= v <= 1 for v in probabilities.values()
        ):
            raise ValueError("Invalid option probabilities")
    return response


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--input", type=Path, default=Path("output/correction-candidates.json")
    )
    p.add_argument(
        "--words", default="adminster,anixous,ambivalance,experiencd,nineteeth"
    )
    p.add_argument("--cache", type=Path, default=Path("work/jev"))
    p.add_argument("--output", type=Path, default=Path("output/jev-proposals.json"))
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the exact reviewable request without contacting TypeSafe",
    )
    a = p.parse_args()
    wanted = set(a.words.split(","))
    rows = [
        r for r in json.loads(a.input.read_text())["candidates"] if r["word"] in wanted
    ]
    if not rows:
        raise ValueError("No matching candidates")
    questions = {}
    contexts = {}
    for i, row in enumerate(rows):
        name = f"word_{i}"
        contexts[name] = {
            "word": row["word"],
            "context": row["occurrences"][0]["context"],
        }
        criteria = {
            "keep": "Original token is valid in this historical book context; preserve it.",
            "uncertain": "Context does not establish a safe lexical correction.",
        }
        for j, suggestion in enumerate(row["suggestions"][:4]):
            criteria[f"candidate_{j}"] = (
                "The intended lexical form is " + suggestion["term"]
            )
        questions[name] = {
            "type": "choice",
            "instructions": "Rank lexical plausibility in the corresponding state entry. Preserve historical vocabulary and proper names. This is a proposal only: you cannot inspect the scan or determine whether an error was printed in the original. Choose uncertain if evidence is insufficient.",
            "criteria": criteria,
        }
    payload = {"model": MODEL, "state": contexts, "questions": questions}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    fingerprint = hashlib.sha256(encoded).hexdigest()
    if a.dry_run:
        a.output.write_text(
            json.dumps(
                {"request_sha256": fingerprint, "request": payload, "sent": False},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        print(f"{len(questions)} questions prepared; no API request sent")
        return
    a.cache.mkdir(parents=True, exist_ok=True)
    cached = a.cache / (fingerprint + ".json")
    if cached.exists():
        response = validate(json.loads(cached.read_text()), questions)
    else:
        r = requests.post(
            "https://api.typesafe.ai/v1/systemone",
            headers={"Authorization": "Bearer " + key()},
            json=payload,
            timeout=60,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"TypeSafe request failed with HTTP {r.status_code}; response suppressed"
            )
        response = validate(r.json(), questions)
        cached.write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n")
    result = {
        "request_sha256": fingerprint,
        "request": payload,
        "response": response,
        "action": "suggestion-only; scan evidence required; printed typos use separate editorial policy",
    }
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "model": MODEL,
                "proposals": {
                    contexts[k]["word"]: v["choice"]
                    for k, v in response["answers"].items()
                },
                "usage": response.get("usage"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
