#!/usr/bin/env python3
"""Validate structure, traceability, and recomputed counts in a coding package."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


LEVELS = {"descriptive", "interpretive"}
UNITS = {"response", "segment"}
COUNTING_UNITS = {"unique_respondent", "segment", "mention"}
METHODS = {"inductive", "deductive", "hybrid"}
DEPTHS = {"rapid", "standard", "high_assurance"}
STANCES = {"positive", "negative", "mixed", "neutral", "conditional", "not_applicable"}
REVIEW_STATUSES = {"single_pass", "self_rechecked", "independently_reviewed", "adjudicated"}
REVIEW_TYPES = {"none", "same_agent_repeat", "independent_model", "independent_human"}
SIGNAL_TYPES = {"common", "directional", "rare_important", "isolated"}


def _emit(status: str, errors: list[str], data: dict | None = None) -> None:
    payload = {"status": status, "errors": errors}
    if data:
        payload.update(data)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("package_version") != 1:
        errors.append("package_version must be 1")
    for key in ("metadata", "codebook", "coded_units", "theme_summary", "quality"):
        if key not in data:
            errors.append(f"missing top-level field: {key}")
    if errors:
        return errors

    metadata = data["metadata"]
    required_metadata = (
        "study_id", "source_snapshot", "source_snapshot_sha256", "question_or_task",
        "n_raw", "n_nonempty", "n_substantive", "unit_of_analysis",
        "counting_unit", "method", "depth", "codebook_version",
    )
    for key in required_metadata:
        if metadata.get(key) in (None, ""):
            errors.append(f"metadata.{key} is required")
    digest = str(metadata.get("source_snapshot_sha256") or "")
    if digest and not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        errors.append("metadata.source_snapshot_sha256 must be a 64-character SHA-256")
    counts = [metadata.get(key) for key in ("n_raw", "n_nonempty", "n_substantive")]
    if not all(_integer(value) for value in counts):
        errors.append("metadata n_raw/n_nonempty/n_substantive must be non-negative integers")
    elif not counts[0] >= counts[1] >= counts[2]:
        errors.append("metadata counts must satisfy n_raw >= n_nonempty >= n_substantive")
    if metadata.get("unit_of_analysis") not in UNITS:
        errors.append(f"metadata.unit_of_analysis must be one of {sorted(UNITS)}")
    if metadata.get("counting_unit") not in COUNTING_UNITS:
        errors.append(f"metadata.counting_unit must be one of {sorted(COUNTING_UNITS)}")
    if metadata.get("method") not in METHODS:
        errors.append(f"metadata.method must be one of {sorted(METHODS)}")
    if metadata.get("depth") not in DEPTHS:
        errors.append(f"metadata.depth must be one of {sorted(DEPTHS)}")

    codebook = data["codebook"]
    if not isinstance(codebook, list):
        return errors + ["codebook must be a list"]
    codes: dict[str, dict] = {}
    for index, code in enumerate(codebook):
        if not isinstance(code, dict):
            errors.append(f"codebook[{index}] must be an object")
            continue
        code_id = str(code.get("code_id") or "")
        if not code_id:
            errors.append(f"codebook[{index}] missing code_id")
            continue
        if code_id in codes:
            errors.append(f"duplicate code_id: {code_id}")
        codes[code_id] = code
        for key in ("label", "definition", "include_when", "exclude_when"):
            if code.get(key) in (None, ""):
                errors.append(f"codebook[{index}] missing {key}")
        if code.get("level") not in LEVELS:
            errors.append(f"codebook[{index}].level must be descriptive or interpretive")
        for neighbor in code.get("nearby_codes") or []:
            if neighbor == code_id:
                errors.append(f"codebook[{index}] lists itself as nearby code")

    units = data["coded_units"]
    if not isinstance(units, list):
        return errors + ["coded_units must be a list"]
    seen_refs: set[str] = set()
    seen_unit_keys: set[tuple[str, str]] = set()
    respondents_by_code: dict[str, set[str]] = defaultdict(set)
    segments_by_code: Counter = Counter()
    mentions_by_code: Counter = Counter()
    uncoded_n = 0
    ambiguous_n = 0
    reviewed_n = 0
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            errors.append(f"coded_units[{index}] must be an object")
            continue
        ref = str(unit.get("response_ref") or "")
        if not ref:
            errors.append(f"coded_units[{index}] missing response_ref")
        seen_refs.add(ref)
        segment_id = str(unit.get("segment_id") or "")
        unit_key = (ref, segment_id if metadata.get("unit_of_analysis") == "segment" else "")
        if unit_key in seen_unit_keys:
            errors.append(f"duplicate coded unit key at coded_units[{index}]: {unit_key}")
        seen_unit_keys.add(unit_key)
        labels = unit.get("labels")
        if not isinstance(labels, list):
            errors.append(f"coded_units[{index}].labels must be a list")
            continue
        if not labels:
            uncoded_n += 1
        if unit.get("review_status") not in REVIEW_STATUSES:
            errors.append(f"coded_units[{index}] has invalid review_status")
        elif unit["review_status"] != "single_pass":
            reviewed_n += 1
        seen_codes_in_unit: set[str] = set()
        unit_is_ambiguous = False
        source_text = unit.get("source_text")
        for label_index, label in enumerate(labels):
            code_id = str(label.get("code_id") or "")
            if code_id not in codes:
                errors.append(f"coded_units[{index}].labels[{label_index}] uses unknown code_id: {code_id}")
            if code_id in seen_codes_in_unit:
                errors.append(f"coded_units[{index}] repeats code_id {code_id}")
            seen_codes_in_unit.add(code_id)
            if label.get("stance") not in STANCES:
                errors.append(f"coded_units[{index}] label {code_id} has invalid stance")
            spans = label.get("evidence_spans")
            if not isinstance(spans, list) or not spans:
                errors.append(f"coded_units[{index}] label {code_id} lacks evidence span")
            else:
                for span in spans:
                    text = str((span or {}).get("text") or "")
                    if not text:
                        errors.append(f"coded_units[{index}] label {code_id} has blank evidence span")
                    elif isinstance(source_text, str) and text not in source_text:
                        errors.append(
                            f"coded_units[{index}] label {code_id} evidence is not present in source_text"
                        )
            unit_is_ambiguous = unit_is_ambiguous or bool(label.get("ambiguity"))
            respondents_by_code[code_id].add(ref)
            mentions_by_code[code_id] += 1
        for code_id in seen_codes_in_unit:
            segments_by_code[code_id] += 1
        ambiguous_n += int(unit_is_ambiguous)

    if _integer(metadata.get("n_substantive")) and metadata["n_substantive"] != len(seen_refs):
        errors.append(
            "metadata.n_substantive must equal the unique response_ref count in coded_units"
        )
    for code_id, code in codes.items():
        for neighbor in code.get("nearby_codes") or []:
            if neighbor not in codes:
                errors.append(f"code {code_id} references unknown nearby code: {neighbor}")

    theme_ids: set[str] = set()
    for index, theme in enumerate(data["theme_summary"]):
        code_id = str(theme.get("code_id") or "")
        if code_id not in codes:
            errors.append(f"theme_summary[{index}] uses unknown code_id: {code_id}")
        if code_id in theme_ids:
            errors.append(f"duplicate theme_summary code_id: {code_id}")
        theme_ids.add(code_id)
        counting_unit = theme.get("counting_unit")
        if counting_unit not in COUNTING_UNITS:
            errors.append(f"theme_summary[{index}] has invalid counting_unit")
            continue
        expected_n = {
            "unique_respondent": len(respondents_by_code[code_id]),
            "segment": segments_by_code[code_id],
            "mention": mentions_by_code[code_id],
        }[counting_unit]
        expected_denominator = {
            "unique_respondent": len(seen_refs),
            "segment": len(units),
            "mention": sum(mentions_by_code.values()),
        }[counting_unit]
        if theme.get("n") != expected_n:
            errors.append(
                f"theme_summary[{index}].n={theme.get('n')} but recomputed {expected_n}"
            )
        if theme.get("denominator") != expected_denominator:
            errors.append(
                f"theme_summary[{index}].denominator={theme.get('denominator')} "
                f"but recomputed {expected_denominator}"
            )
        if theme.get("signal_type") not in SIGNAL_TYPES:
            errors.append(f"theme_summary[{index}] has invalid signal_type")
        for ref in theme.get("counterexamples") or []:
            if ref not in seen_refs:
                errors.append(f"theme_summary[{index}] references unknown counterexample: {ref}")

    quality = data["quality"]
    for key, expected in (
        ("uncoded_n", uncoded_n), ("ambiguous_n", ambiguous_n), ("reviewed_n", reviewed_n)
    ):
        if quality.get(key) != expected:
            errors.append(f"quality.{key}={quality.get(key)} but recomputed {expected}")
    if quality.get("review_type") not in REVIEW_TYPES:
        errors.append(f"quality.review_type must be one of {sorted(REVIEW_TYPES)}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a user research coding package")
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.package.read_text(encoding="utf-8"))
    except Exception as exc:
        _emit("fail", [f"cannot read JSON: {exc}"])
        raise SystemExit(1)
    errors = validate(data)
    if errors:
        _emit("fail", errors)
        raise SystemExit(1)
    _emit("pass", [], {
        "codes": len(data["codebook"]),
        "coded_units": len(data["coded_units"]),
        "themes": len(data["theme_summary"]),
    })


if __name__ == "__main__":
    main()
