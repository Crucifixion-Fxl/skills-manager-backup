#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""机械校验 gitlab-mr Auditor 的结构化完成证据。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1] / "lib"))

from release_drive_validation import cleanup_job_binding_matches


class AuditError(ValueError):
    """Auditor 证据不完整或与当前 state 不一致。"""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Validate structured Auditor evidence before completing an MR."
    )
    result.add_argument("--state", required=True, type=Path)
    result.add_argument("--audit-result", required=True, type=Path)
    result.add_argument("--json", action="store_true", dest="as_json")
    return result


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"{label} must be a JSON object")
    return value


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuditError(f"{label} must be a non-empty string")
    return value


def require_recent_utc(value: Any, label: str) -> None:
    text = require_text(value, label)
    try:
        observed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AuditError(f"{label} must be an ISO-8601 timestamp") from exc
    if observed.tzinfo is None:
        raise AuditError(f"{label} must include a timezone")
    now = datetime.now(UTC)
    if observed > now + timedelta(minutes=5) or observed < now - timedelta(hours=2):
        raise AuditError(f"{label} must be from the current audit window")


def validate_observation(
    observation: Any,
    *,
    expected_name: str,
    expected_url: str,
    expected_contains: list[str] | None = None,
) -> str:
    if not isinstance(observation, dict):
        raise AuditError(f"observation {expected_name!r} must be an object")
    if observation.get("name") != expected_name:
        raise AuditError(f"observation name must be {expected_name!r}")
    if observation.get("evidence_url") != expected_url:
        raise AuditError(f"observation {expected_name!r} evidence URL does not match")
    for field in ("target_environment", "observed_value", "verifier_tool"):
        require_text(observation.get(field), f"{expected_name}.{field}")
    evidence_file = Path(
        require_text(
            observation.get("evidence_file"),
            f"{expected_name}.evidence_file",
        )
    )
    if not evidence_file.is_absolute() or evidence_file.is_symlink():
        raise AuditError(
            f"{expected_name}.evidence_file must be an absolute regular file"
        )
    try:
        if not evidence_file.is_file() or evidence_file.stat().st_size > 5_000_000:
            raise AuditError(
                f"{expected_name}.evidence_file must be a regular file up to 5 MB"
            )
        content = evidence_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuditError(f"cannot read {expected_name}.evidence_file: {exc}") from exc
    observed_value = observation["observed_value"]
    if observed_value not in content:
        raise AuditError(
            f"{expected_name}.observed_value is not present in raw evidence"
        )
    for expected in expected_contains or []:
        if expected not in content:
            raise AuditError(
                f"{expected_name}.evidence_file is missing expected value {expected!r}"
            )
    require_recent_utc(
        observation.get("verified_at_utc"),
        f"{expected_name}.verified_at_utc",
    )
    return content


def parse_raw_json(content: str, label: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise AuditError(f"{label}.evidence_file must contain raw JSON") from exc


def validate_release_evidence(
    state: dict[str, Any],
    release: dict[str, Any],
) -> None:
    mode = state.get("mr_mode")
    promotion = state.get("promotion")
    if not isinstance(promotion, dict):
        raise AuditError("state.promotion must be an object")
    if mode == "production-promotion":
        if release.get("code_parity_status") != "pass":
            raise AuditError("production promotion requires code_parity_status=pass")
        if release.get("parity_report_sha256") != promotion.get("parity_report_sha256"):
            raise AuditError("Auditor parity report does not match Driver state")
        expected_gates = {
            gate["name"]: gate
            for gate in promotion.get("external_gates", [])
            if isinstance(gate, dict) and gate.get("required")
        }
        observations = release.get("external_gates")
        if not isinstance(observations, list):
            raise AuditError("release.external_gates must be a list")
        actual = {
            item.get("name"): item for item in observations if isinstance(item, dict)
        }
        if len(actual) != len(observations):
            raise AuditError(
                "Auditor external gates contain duplicates or invalid items"
            )
        if set(actual) != set(expected_gates):
            raise AuditError(
                "Auditor external gates do not match required contract gates"
            )
        for name, gate in expected_gates.items():
            validate_observation(
                actual[name],
                expected_name=name,
                expected_url=gate["evidence"],
                expected_contains=gate.get("expected_contains", []),
            )
    elif mode == "production-non-promotion":
        evidence_url = promotion.get("staging_flow_evidence")
        validate_observation(
            release.get("workflow_evidence"),
            expected_name="staging-flow-not-applicable",
            expected_url=evidence_url,
        )
    elif mode == "staging-writer-cleanup":
        cleanup = state.get("cleanup")
        if not isinstance(cleanup, dict) or cleanup.get("enabled") is not True:
            raise AuditError("staging writer cleanup is missing attested state")
        if not cleanup_job_binding_matches(cleanup):
            raise AuditError("cleanup accepted jobs do not bind the staging contract gate")
        contract = cleanup.get("contract")
        if not isinstance(contract, dict) or release.get(
            "cleanup_contract_sha256"
        ) != contract.get("sha256"):
            raise AuditError("Auditor cleanup contract does not match Driver state")
        evidence = release.get("cleanup_evidence")
        if not isinstance(evidence, list):
            raise AuditError("release.cleanup_evidence must be a list")
        actual = {
            item.get("name"): item for item in evidence if isinstance(item, dict)
        }
        expected = {
            "accepted-staging-pipeline": {
                "url": cleanup.get("accepted_pipeline_api_url"),
                "observed": cleanup.get("accepted_staging_sha"),
                "contains": [cleanup.get("accepted_staging_sha")],
            },
            "accepted-staging-jobs": {
                "url": cleanup.get("accepted_pipeline_jobs_url"),
                "observed": cleanup.get("accepted_jobs", [""])[0],
                "contains": cleanup.get("accepted_jobs", []),
            },
            "staging-branch-policy": {
                "url": cleanup.get("staging_branch_policy_url"),
                "observed": cleanup.get("staging_branch"),
                "contains": [cleanup.get("staging_branch")],
            },
        }
        if len(actual) != len(evidence) or set(actual) != set(expected):
            raise AuditError(
                "cleanup evidence does not match required observations"
            )
        for name, requirement in expected.items():
            observation = actual[name]
            if observation.get("observed_value") != requirement["observed"]:
                raise AuditError(f"{name}.observed_value is not the required value")
            content = validate_observation(
                observation,
                expected_name=name,
                expected_url=requirement["url"],
                expected_contains=requirement["contains"],
            )
            raw = parse_raw_json(content, name)
            if name == "accepted-staging-pipeline":
                if not isinstance(raw, dict) or any(
                    raw.get(field) != value
                    for field, value in (
                        ("id", cleanup.get("accepted_pipeline_id")),
                        ("ref", cleanup.get("staging_branch")),
                        ("sha", cleanup.get("accepted_staging_sha")),
                        ("status", "success"),
                        ("web_url", cleanup.get("accepted_pipeline")),
                    )
                ):
                    raise AuditError("accepted staging pipeline evidence changed")
            elif name == "accepted-staging-jobs":
                if not isinstance(raw, list) or not all(
                    isinstance(item, dict) for item in raw
                ):
                    raise AuditError("accepted staging jobs evidence must be a list")
                actual_jobs = {
                    item.get("name"): item.get("status")
                    for item in raw
                    if isinstance(item.get("name"), str)
                }
                if any(
                    sum(item.get("name") == job for item in raw) != 1
                    or actual_jobs.get(job) != "success"
                    for job in cleanup.get("accepted_jobs", [])
                ):
                    raise AuditError("accepted staging jobs evidence changed")
            elif not isinstance(raw, dict) or (
                raw.get("name") != cleanup.get("staging_branch")
                or raw.get("allow_force_push") is not False
            ):
                raise AuditError("staging branch policy evidence changed")
    elif mode == "emergency-hotfix":
        emergency = state.get("emergency")
        if not isinstance(emergency, dict) or emergency.get("approved") is not True:
            raise AuditError("emergency hotfix is missing explicit approval")


def validate(state: dict[str, Any], audit: dict[str, Any]) -> None:
    if audit.get("status") != "verified":
        raise AuditError("audit status must be verified")
    for field in ("mr_iid", "target_branch"):
        if audit.get(field) != state.get(field):
            raise AuditError(f"audit {field} does not match Driver state")
    snapshot = state.get("last_snapshot")
    if not isinstance(snapshot, dict) or audit.get("head_sha") != snapshot.get(
        "head_sha"
    ):
        raise AuditError("audit head_sha does not match latest Driver snapshot")
    if audit.get("pipeline_status") != "success":
        raise AuditError("latest pipeline is not successful")
    if audit.get("unresolved_discussions") != 0:
        raise AuditError("unresolved discussions remain")
    if audit.get("mergeable") is not True:
        raise AuditError("MR is not mergeable")
    require_recent_utc(audit.get("audited_at_utc"), "audited_at_utc")
    release = audit.get("release")
    if not isinstance(release, dict):
        raise AuditError("audit.release must be an object")
    validate_release_evidence(state, release)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        state = load_object(args.state, "state")
        audit = load_object(args.audit_result, "audit result")
        validate(state, audit)
    except AuditError as exc:
        if args.as_json:
            print(json.dumps({"status": "fail", "error": str(exc)}))
        else:
            print(f"AUDIT FAIL: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps({"status": "pass"}))
    else:
        print("AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
