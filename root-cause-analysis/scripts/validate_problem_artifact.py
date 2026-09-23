#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Validate the portable core of Buzz problem-analysis/2.0 artifacts."""
import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, List, Optional
from problem_artifact_sections import (
    validate_hypotheses,
    validate_neighbors,
    validate_supporting_reviews,
)
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
ARTIFACT_ID_RE = re.compile(r"problem-analysis:([0-9a-f]{64})\Z")
STATUSES = {
    "TRIAGED",
    "ANALYZED_NO_ROOT_CAUSE",
    "ROOT_CAUSE_CONFIRMED",
    "BLOCKED",
}
DEPTHS = {"SOURCE_ONLY", "REPOSITORY_READ", "RUNTIME_READ"}
MODES = {"ISSUE_TRIAGE", "ROOT_CAUSE_INVESTIGATION"}
TOP_FIELDS = {
    "schema_version", "artifact_id", "content_sha256", "identity", "status",
    "evidence_depth", "source_baseline", "context_resolution", "problem_frame",
    "impact", "boundary_matrix", "evidence_ledger", "methodology_proof",
    "supporting_skill_reviews", "causal_neighbors", "hypotheses", "code_findings",
    "recommended_actions", "verification_matrix", "open_questions", "quality_gate",
}
QUALITY_BOOLEANS = {
    "source_verified", "context_claims_bounded", "evidence_traceable",
    "competing_hypotheses_present", "falsifiers_present",
    "causal_neighbors_reconciled", "magnitude_direction_reconciled",
    "no_unexplained_symptoms", "methodology_proof_verified",
    "root_cause_gate_passed",
}
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


def _digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


def _canonical_digest(artifact: dict[str, Any]) -> str:
    payload = copy.deepcopy(artifact)
    payload.pop("artifact_id", None)
    payload.pop("content_sha256", None)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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


def validate_artifact(value: Any) -> list[str]:
    errors: list[str] = []
    artifact = _object(value, "artifact", errors)
    if not artifact:
        if not errors:
            errors.append("artifact must not be empty")
        return errors
    if set(artifact) != TOP_FIELDS:
        errors.append("problem-analysis/2.0 top-level fields are invalid")
    if artifact.get("schema_version") != "problem-analysis/2.0":
        errors.append("schema_version must be problem-analysis/2.0")
    status = artifact.get("status")
    if status not in STATUSES:
        errors.append("status is invalid")
    depth = artifact.get("evidence_depth")
    if depth not in DEPTHS:
        errors.append("evidence_depth is invalid")

    identity = _object(artifact.get("identity"), "identity", errors)
    for field in (
        "workflow_id", "node_id", "attempt_id", "producer_agent", "produced_at",
        "source_revision",
    ):
        _non_empty(identity.get(field), f"identity.{field}", errors)

    source = _object(artifact.get("source_baseline"), "source_baseline", errors)
    if source.get("kind") != "GITLAB_ISSUE":
        errors.append("source_baseline.kind must be GITLAB_ISSUE")
    for field in ("stable_ref", "revision", "retrieved_at", "retrieval_status"):
        _non_empty(source.get(field), f"source_baseline.{field}", errors)
    if source.get("retrieval_status") not in {"VERIFIED", "FAILED"}:
        errors.append("source_baseline.retrieval_status is invalid")
    if not _digest(source.get("content_sha256")):
        errors.append("source_baseline.content_sha256 must be sha256")

    context = _object(
        artifact.get("context_resolution"), "context_resolution", errors
    )
    if context.get("status") not in {"UNIQUE", "AMBIGUOUS", "UNMAPPED"}:
        errors.append("context_resolution.status is invalid")
    if depth in {"REPOSITORY_READ", "RUNTIME_READ"} and (
        context.get("status") != "UNIQUE" or not context.get("repository")
    ):
        errors.append(f"{depth} requires UNIQUE repository context")

    ledger = _array(artifact.get("evidence_ledger"), "evidence_ledger", errors)
    evidence_ids: set[str] = set()
    evidence_classes: set[str] = set()
    for index, raw in enumerate(ledger):
        record = _object(raw, f"evidence_ledger[{index}]", errors)
        evidence_id = record.get("evidence_id")
        _non_empty(evidence_id, f"evidence_ledger[{index}].evidence_id", errors)
        if isinstance(evidence_id, str):
            if evidence_id in evidence_ids:
                errors.append(f"duplicate evidence_id: {evidence_id}")
            evidence_ids.add(evidence_id)
        evidence_class = record.get("evidence_class")
        if evidence_class not in {"SOURCE", "REPOSITORY", "RUNTIME"}:
            errors.append(f"evidence_ledger[{index}].evidence_class is invalid")
        elif isinstance(evidence_class, str):
            evidence_classes.add(evidence_class)
        for field in ("claim", "result", "locator", "observed_at", "relation"):
            _non_empty(record.get(field), f"evidence_ledger[{index}].{field}", errors)
        if not _digest(record.get("content_sha256")):
            errors.append(f"evidence_ledger[{index}].content_sha256 is invalid")
        _array(record.get("limitations"), f"evidence_ledger[{index}].limitations", errors)
    if "SOURCE" not in evidence_classes:
        errors.append("all evidence depths require SOURCE evidence")
    if depth == "SOURCE_ONLY" and evidence_classes - {"SOURCE"}:
        errors.append("SOURCE_ONLY cannot contain REPOSITORY or RUNTIME evidence")
    if depth in {"REPOSITORY_READ", "RUNTIME_READ"} and "REPOSITORY" not in evidence_classes:
        errors.append(f"{depth} requires REPOSITORY evidence")
    if depth == "RUNTIME_READ" and "RUNTIME" not in evidence_classes:
        errors.append("RUNTIME_READ requires RUNTIME evidence")

    proof = _object(artifact.get("methodology_proof"), "methodology_proof", errors)
    if proof.get("skill_id") != "addx:root-cause-analysis":
        errors.append("methodology_proof.skill_id must be addx:root-cause-analysis")
    mode = proof.get("mode")
    if mode not in MODES:
        errors.append("methodology_proof.mode is invalid")
    if status == "TRIAGED" and mode != "ISSUE_TRIAGE":
        errors.append("TRIAGED requires ISSUE_TRIAGE methodology mode")
    proof_verified = proof.get("proof_verified")
    if not isinstance(proof_verified, bool):
        errors.append("methodology_proof.proof_verified must be boolean")
    proof_refs = _refs(
        proof.get("evidence_refs"), "methodology_proof.evidence_refs",
        evidence_ids, errors,
    )
    if not proof_refs:
        errors.append("methodology_proof.evidence_refs must not be empty")
    if proof_verified is True and not _digest(proof.get("package_sha256")):
        errors.append("verified methodology proof requires package_sha256")
    if status != "BLOCKED" and proof_verified is not True:
        errors.append("non-BLOCKED artifact requires verified methodology proof")
    if proof_verified is False:
        if status != "BLOCKED":
            errors.append("unverified methodology proof requires BLOCKED")
        for field in ("failure_code", "failure_reason"):
            _non_empty(proof.get(field), f"methodology_proof.{field}", errors)

    validate_supporting_reviews(
        artifact.get("supporting_skill_reviews"), evidence_ids, _digest, errors
    )
    neighbors, neighbor_statuses = validate_neighbors(
        artifact.get("causal_neighbors"), evidence_ids, errors
    )
    hypotheses, states, falsifier_results = validate_hypotheses(
        artifact.get("hypotheses"), evidence_ids, errors
    )
    if status in {"ANALYZED_NO_ROOT_CAUSE", "ROOT_CAUSE_CONFIRMED"} and len(hypotheses) < 2:
        errors.append(f"{status} requires at least two competing hypotheses")

    code = _object(artifact.get("code_findings"), "code_findings", errors)
    findings = _array(code.get("findings"), "code_findings.findings", errors)
    if depth == "SOURCE_ONLY" and (
        code.get("inspection_status") != "NOT_INSPECTED" or findings
    ):
        errors.append("SOURCE_ONLY requires NOT_INSPECTED empty code findings")
    for field in (
        "boundary_matrix", "recommended_actions", "verification_matrix", "open_questions"
    ):
        _array(artifact.get(field), field, errors)

    quality = _object(artifact.get("quality_gate"), "quality_gate", errors)
    for field in QUALITY_BOOLEANS:
        if not isinstance(quality.get(field), bool):
            errors.append(f"quality_gate.{field} must be boolean")
    gate_refs = _refs(
        quality.get("gate_evidence_ids"), "quality_gate.gate_evidence_ids",
        evidence_ids, errors,
    )
    _array(quality.get("missing_requirements"), "quality_gate.missing_requirements", errors)
    if quality.get("methodology_proof_verified") is not proof_verified:
        errors.append("quality_gate.methodology_proof_verified does not match proof")
    if status != "ROOT_CAUSE_CONFIRMED" and quality.get("root_cause_gate_passed") is True:
        errors.append("root_cause_gate_passed must be false before ROOT_CAUSE_CONFIRMED")
    if status == "ROOT_CAUSE_CONFIRMED":
        if mode != "ROOT_CAUSE_INVESTIGATION":
            errors.append("ROOT_CAUSE_CONFIRMED requires ROOT_CAUSE_INVESTIGATION")
        if "KILLED" not in states or "CONFIRMED" not in states:
            errors.append("ROOT_CAUSE_CONFIRMED requires KILLED and CONFIRMED hypotheses")
        if "OPEN" in states or "NOT_RUN" in falsifier_results:
            errors.append("ROOT_CAUSE_CONFIRMED has unresolved hypotheses")
        if not neighbors or any(item == "NOT_CHECKED" for item in neighbor_statuses):
            errors.append("ROOT_CAUSE_CONFIRMED has an unchecked causal neighbor")
        if not proof_refs or not set(proof_refs).issubset(set(gate_refs)):
            errors.append("ROOT_CAUSE_CONFIRMED methodology proof is not gate-bound")
        for field in QUALITY_BOOLEANS:
            if quality.get(field) is not True:
                errors.append(f"ROOT_CAUSE_CONFIRMED requires quality_gate.{field}=true")

    actual_digest = artifact.get("content_sha256")
    expected_digest = _canonical_digest(artifact)
    match = ARTIFACT_ID_RE.fullmatch(str(artifact.get("artifact_id")))
    if not _digest(actual_digest) or actual_digest != expected_digest:
        errors.append("content_sha256 does not match canonical artifact content")
    if match is None or actual_digest != f"sha256:{match.group(1)}":
        errors.append("artifact_id does not match content_sha256")
    return errors


def validate_control_outcome(value: Any) -> list[str]:
    errors: list[str] = []
    control = _object(value, "control outcome", errors)
    if not control:
        if not errors:
            errors.append("control outcome must not be empty")
        return errors
    expected = {
        "schema_version": "problem-analysis-control/1.0",
        "outcome_kind": "NEEDS_INPUT",
        "write_policy": "ZERO_WRITE",
    }
    for field, required in expected.items():
        if control.get(field) != required:
            errors.append(f"{field} must be {required}")
    for field in ("question", "reason"):
        _non_empty(control.get(field), field, errors)
    for forbidden in ("artifact_id", "content_sha256", "status"):
        if forbidden in control:
            errors.append(f"{forbidden} is forbidden in NEEDS_INPUT")
    return errors


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--kind", choices=("auto", "artifact", "control"), default="auto")
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    kind = args.kind
    if kind == "auto":
        kind = (
            "control"
            if isinstance(value, dict) and value.get("outcome_kind") == "NEEDS_INPUT"
            else "artifact"
        )
    errors = validate_control_outcome(value) if kind == "control" else validate_artifact(value)
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
