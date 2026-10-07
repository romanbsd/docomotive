"""Cached judgments of a specific spelling edit, independent of source origin."""

import math
import json
from pathlib import Path

from jev_judge import MODEL, post
from common import digest

QUESTIONS = {
    "spelling_error": {
        "type": "noul",
        "instructions": "Is `original_token` a misspelling of `candidate_token` at "
        "the marked occurrence in `paragraph`, given `preceding_context` and "
        "`following_context`? A misspelling printed in the original book still "
        "counts as a spelling error. Answer no for a legitimate distinct word, "
        "technical term, historical spelling or a different person's name. "
        "Judge the specific token, not whether the sentence is understandable.",
    },
    "meaning_preserved": {
        "type": "noul",
        "instructions": "Would replacing only the marked `original_token` with "
        "`candidate_token` preserve the author's intended meaning and the identity "
        "of any named person or technical concept, given the surrounding context? "
        "Do not favor a more common word if it changes the concept or claim.",
    },
}


def judgments(state, cache):
    payload = {"model": MODEL, "state": state, "questions": QUESTIONS}
    response = post(payload, cache)
    if response.get("model") != MODEL:
        raise ValueError("Unexpected spelling judgment model")
    answers = response.get("answers", {})
    if set(answers) != set(QUESTIONS):
        raise ValueError("Missing or unexpected spelling judgment IDs")
    result = {}
    for name in QUESTIONS:
        answer = answers[name]
        value = answer.get("noul")
        if (
            answer.get("type") != "noul"
            or type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError("Invalid spelling judgment")
        result[name] = value
    return dict(
        result,
        model=MODEL,
        request_sha256=digest(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ),
        usage=response.get("usage", {}),
    )


def judge(cache):
    """Builds replay judgments only; prepare and send new requests explicitly."""

    def cached(state):
        payload = {"model": MODEL, "state": state, "questions": QUESTIONS}
        fingerprint = digest(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        )
        if not (Path(cache) / (fingerprint + ".json")).is_file():
            return None
        return judgments(state, Path(cache))

    return cached


def main():
    import argparse
    from concurrent.futures import ThreadPoolExecutor
    from common import read_json, write_json

    parser = argparse.ArgumentParser(
        description="Prepare or explicitly evaluate contextual spelling judgments."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="lexical-repair.json from a local build",
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Send uncached contextual excerpts to TypeSafe",
    )
    args = parser.parse_args()
    states = [
        d["context_state"]
        for d in read_json(args.input)["decisions"]
        if "context_state" in d
        and d.get("action") == "review"
        and not d.get("context_judgment")
    ]
    # Deduplicate exact judgments; repetitions are not independent model votes.
    unique = {json.dumps(s, ensure_ascii=False, sort_keys=True): s for s in states}
    result = dict(
        model=MODEL, questions=QUESTIONS, states=list(unique.values()), sent=False
    )
    if args.run:
        result["uncached_judgments"] = sum(
            not (
                args.cache
                / (
                    digest(
                        json.dumps(
                            {"model": MODEL, "state": state, "questions": QUESTIONS},
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode()
                    )
                    + ".json"
                )
            ).is_file()
            for state in unique.values()
        )
        with ThreadPoolExecutor(max_workers=4) as pool:
            result["judgments"] = list(
                pool.map(
                    lambda state: dict(
                        state=state, result=judgments(state, args.cache)
                    ),
                    unique.values(),
                )
            )
        result["sent"] = result["uncached_judgments"] > 0
        result["evaluated"] = True
    write_json(args.output, result)
    print(f"{len(unique)} contextual requests; API execution enabled: {args.run}")


if __name__ == "__main__":
    main()
