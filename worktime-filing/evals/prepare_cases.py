#!/usr/bin/env python3
"""Validate and normalize worktime-filing eval inputs for an agent runner."""

import argparse
import json
from pathlib import Path


ALLOWED_ROLES = {"assistant", "tool", "user"}


def prepare_case(evaluation):
    has_prompt = isinstance(evaluation.get("prompt"), str)
    has_messages = isinstance(evaluation.get("messages"), list)
    if has_prompt == has_messages:
        raise ValueError(f"eval {evaluation.get('id')} must define exactly one input form")
    if has_messages:
        messages = evaluation["messages"]
        if not messages or messages[-1].get("role") != "user":
            raise ValueError(f"eval {evaluation.get('id')} must end with a user turn")
        if any(message.get("role") not in ALLOWED_ROLES for message in messages):
            raise ValueError(f"eval {evaluation.get('id')} has an unsupported role")
    else:
        messages = [{"role": "user", "content": evaluation["prompt"]}]
    return {
        "id": evaluation["id"],
        "messages": messages,
        "expected_output": evaluation["expected_output"],
        "assertions": evaluation["assertions"],
    }


def load_cases(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [prepare_case(evaluation) for evaluation in payload["evals"]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("evals.json"),
    )
    args = parser.parse_args()
    for evaluation in load_cases(args.path):
        print(json.dumps(evaluation, ensure_ascii=False))


if __name__ == "__main__":
    main()
