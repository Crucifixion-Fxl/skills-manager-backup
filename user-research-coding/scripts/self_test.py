#!/usr/bin/env python3
"""Offline behavioral checks for coding-package validation."""
from __future__ import annotations

import argparse
import copy
import json

from validate_coding_package import validate


def fixture() -> dict:
    return {
        "package_version": 1,
        "metadata": {
            "study_id": "fixture",
            "source_snapshot": "fixture.json",
            "source_snapshot_sha256": "a" * 64,
            "question_or_task": "What happened?",
            "language": ["en"],
            "n_raw": 2,
            "n_nonempty": 2,
            "n_substantive": 2,
            "unit_of_analysis": "response",
            "counting_unit": "unique_respondent",
            "method": "inductive",
            "depth": "rapid",
            "codebook_version": "v1",
        },
        "codebook": [{
            "code_id": "friction",
            "label": "Friction",
            "level": "descriptive",
            "definition": "Participant reports difficulty.",
            "include_when": ["Explicit difficulty"],
            "exclude_when": ["No difficulty"],
            "nearby_codes": [],
        }],
        "coded_units": [
            {
                "response_ref": "r1",
                "source_text": "Setup was difficult.",
                "labels": [{
                    "code_id": "friction",
                    "evidence_spans": [{"text": "difficult"}],
                    "stance": "negative",
                }],
                "review_status": "self_rechecked",
            },
            {
                "response_ref": "r2",
                "source_text": "It was fine.",
                "labels": [],
                "review_status": "single_pass",
            },
        ],
        "theme_summary": [{
            "code_id": "friction",
            "n": 1,
            "denominator": 2,
            "counting_unit": "unique_respondent",
            "counterexamples": ["r2"],
            "signal_type": "directional",
            "interpretation": "One participant reported difficulty.",
        }],
        "quality": {
            "uncoded_n": 1,
            "ambiguous_n": 0,
            "reviewed_n": 1,
            "review_type": "same_agent_repeat",
            "agreement_metrics": [],
            "disagreements": [],
            "limitations": [],
        },
    }


def run() -> dict:
    valid = fixture()
    assert validate(valid) == []

    wrong_count = copy.deepcopy(valid)
    wrong_count["theme_summary"][0]["n"] = 2
    assert any("recomputed 1" in error for error in validate(wrong_count))

    bad_evidence = copy.deepcopy(valid)
    bad_evidence["coded_units"][0]["labels"][0]["evidence_spans"][0]["text"] = "not in source"
    assert any("not present in source_text" in error for error in validate(bad_evidence))

    duplicate = copy.deepcopy(valid)
    duplicate["coded_units"][0]["labels"].append(
        copy.deepcopy(duplicate["coded_units"][0]["labels"][0])
    )
    assert any("repeats code_id" in error for error in validate(duplicate))

    return {
        "status": "pass",
        "checks": [
            "valid package accepted",
            "fabricated theme count rejected",
            "untraceable evidence rejected",
            "duplicate unit label rejected",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline coding validator checks")
    parser.parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
