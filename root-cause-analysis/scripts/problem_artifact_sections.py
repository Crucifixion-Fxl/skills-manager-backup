"""Section validators shared by the problem Artifact CLI."""

import argparse
import json
from typing import Any


def _object(value: Any, field: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return {}
    return value


def _array(value: Any, field: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return []
    return value


def _non_empty(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a non-empty string")


def _refs(
    value: Any, field: str, evidence_ids: set[str], errors: list[str]
) -> list[str]:
    refs = _array(value, field, errors)
    if any(not isinstance(ref, str) or not ref for ref in refs):
        errors.append(f"{field} must contain non-empty strings")
        return []
    if set(refs) - evidence_ids:
        errors.append(f"{field} has unknown evidence refs")
    return refs


def validate_supporting_reviews(
    value: Any,
    evidence_ids: set[str],
    digest_validator: Any,
    errors: list[str],
) -> None:
    reviews = _array(value, "supporting_skill_reviews", errors)
    skill_ids: set[str] = set()
    for index, raw in enumerate(reviews):
        review = _object(raw, f"supporting_skill_reviews[{index}]", errors)
        skill_id = review.get("skill_id")
        _non_empty(skill_id, f"supporting_skill_reviews[{index}].skill_id", errors)
        if skill_id == "addx:root-cause-analysis":
            errors.append("root-cause-analysis self review is forbidden")
        if isinstance(skill_id, str):
            if skill_id in skill_ids:
                errors.append("duplicate supporting skill_id")
            skill_ids.add(skill_id)
        if review.get("mode") not in {"READ_ONLY", "REVIEW_ONLY"}:
            errors.append(f"supporting_skill_reviews[{index}].mode is invalid")
        result = review.get("result")
        if result not in {"EXECUTED", "BLOCKED", "SKIPPED"}:
            errors.append(f"supporting_skill_reviews[{index}].result is invalid")
        refs = _refs(
            review.get("evidence_refs"),
            f"supporting_skill_reviews[{index}].evidence_refs",
            evidence_ids,
            errors,
        )
        _array(review.get("findings"), f"supporting_skill_reviews[{index}].findings", errors)
        if result == "EXECUTED" and (
            not digest_validator(review.get("package_sha256")) or not refs
        ):
            errors.append(
                f"supporting_skill_reviews[{index}] EXECUTED requires package and refs"
            )


def validate_neighbors(
    value: Any, evidence_ids: set[str], errors: list[str]
) -> tuple[list[Any], list[str]]:
    neighbors = _array(value, "causal_neighbors", errors)
    statuses: list[str] = []
    for index, raw in enumerate(neighbors):
        neighbor = _object(raw, f"causal_neighbors[{index}]", errors)
        for field in ("neighbor_id", "relationship", "mechanism", "remaining_uncertainty"):
            _non_empty(neighbor.get(field), f"causal_neighbors[{index}].{field}", errors)
        status = neighbor.get("check_status")
        if status not in {"CHECKED", "NOT_CHECKED", "NOT_APPLICABLE"}:
            errors.append(f"causal_neighbors[{index}].check_status is invalid")
        else:
            statuses.append(status)
        refs = _refs(
            neighbor.get("evidence_ids"),
            f"causal_neighbors[{index}].evidence_ids",
            evidence_ids,
            errors,
        )
        if status == "CHECKED" and not refs:
            errors.append(f"causal_neighbors[{index}] CHECKED requires evidence")
    return neighbors, statuses


def validate_hypotheses(
    value: Any, evidence_ids: set[str], errors: list[str]
) -> tuple[list[Any], list[str], list[str]]:
    hypotheses = _array(value, "hypotheses", errors)
    states: list[str] = []
    results: list[str] = []
    for index, raw in enumerate(hypotheses):
        hypothesis = _object(raw, f"hypotheses[{index}]", errors)
        state = hypothesis.get("state")
        result = hypothesis.get("falsifier_result")
        if state not in {"OPEN", "KILLED", "CONFIRMED"}:
            errors.append(f"hypotheses[{index}].state is invalid")
        else:
            states.append(state)
        if result not in {"NOT_RUN", "SURVIVED", "FALSIFIED"}:
            errors.append(f"hypotheses[{index}].falsifier_result is invalid")
        else:
            results.append(result)
        for field in ("hypothesis_id", "statement", "falsifier", "confidence_basis"):
            _non_empty(hypothesis.get(field), f"hypotheses[{index}].{field}", errors)
        evidence_for = _refs(
            hypothesis.get("evidence_for"),
            f"hypotheses[{index}].evidence_for",
            evidence_ids,
            errors,
        )
        evidence_against = _refs(
            hypothesis.get("evidence_against"),
            f"hypotheses[{index}].evidence_against",
            evidence_ids,
            errors,
        )
        if state == "OPEN":
            _non_empty(
                hypothesis.get("next_probe"), f"hypotheses[{index}].next_probe", errors
            )
        if state == "KILLED" and (result != "FALSIFIED" or not evidence_against):
            errors.append("KILLED hypothesis requires FALSIFIED evidence")
        if state == "CONFIRMED" and (result != "SURVIVED" or not evidence_for):
            errors.append("CONFIRMED hypothesis requires surviving evidence")
    return hypotheses, states, results


def main() -> int:
    """Expose a bounded diagnostic interface for package verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        print(json.dumps({"status": "library", "valid": True}, sort_keys=True))
    except (OSError, TypeError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
