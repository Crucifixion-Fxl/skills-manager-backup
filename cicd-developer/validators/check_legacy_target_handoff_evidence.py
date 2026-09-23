#!/usr/bin/env python3
"""Validate the closed legacy-live handoff evidence state machine.

Exit codes: 0 pass, 1 contract violation, 2 usage/dependency/input failure.
Diagnostics name only fixed schema fields and never echo evidence content.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from typing import Any

from _legacy_target_common import (
    PLACEHOLDERS,
    SafeInputError,
    is_safe_actor,
    is_safe_observed_state,
    is_stable_evidence_ref,
    load_single_yaml,
)


MAX_EVIDENCE_BYTES = 256 * 1024
MAX_YAML_TOKENS = 16_384
API_VERSION = "migrations.addx.io/v1alpha1"
KIND = "LegacyTargetHandoffEvidence"
K8S_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,254}$")
CLUSTER_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
UTC_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
ROW_KEYS = {"evidence_ref", "timestamp_utc", "actor", "observed_state", "status"}
ROW_STATUSES = {
    "PENDING",
    "VERIFIED",
    "FAILED",
    "NOT_APPLICABLE",
    "NOT_INVOKED",
}
ENFORCEMENT_MODES = {"sre-policy", "ce-guarded"}
ACTIVATION_MODES = {"stop-source", "parallel"}
PARALLEL_SOURCE_ROWS = ("source_scale_authorization", "source_zero")
HANDOFF_STATUSES = {"PREPARING", "IN_PROGRESS", "VERIFIED", "ABORTED_ROLLED_BACK"}
ROLLBACK_STATUSES = {"NOT_INVOKED", "IN_PROGRESS", "VERIFIED"}
HANDOFF_STATUS_PROGRESS = {
    "PREPARING": {"PREPARING", "IN_PROGRESS", "VERIFIED", "ABORTED_ROLLED_BACK"},
    "IN_PROGRESS": {"IN_PROGRESS", "VERIFIED", "ABORTED_ROLLED_BACK"},
    "VERIFIED": {"VERIFIED"},
    "ABORTED_ROLLED_BACK": {"ABORTED_ROLLED_BACK"},
}
ROLLBACK_STATUS_PROGRESS = {
    "NOT_INVOKED": {"NOT_INVOKED", "IN_PROGRESS", "VERIFIED"},
    "IN_PROGRESS": {"IN_PROGRESS", "VERIFIED"},
    "VERIFIED": {"VERIFIED"},
}

HANDOFF_EVENTS = (
    "preparation",
    "writer_fence_authorization",
    "writer_fence_result",
    "source_scale_authorization",
    "source_zero",
    "activation_authorization",
    "activation_transition",
    "activation_result",
    "traffic_authorization",
    "traffic_result",
    "runtime_acceptance",
)
ROLLBACK_EVENTS = (
    "rollback_authorization",
    "rollback_traffic_isolation",
    "rollback_target_zero",
    "rollback_source_restore",
    "rollback_source_ready",
    "rollback_route_restore",
    "rollback_writer_policy",
)
ALL_EVENTS = HANDOFF_EVENTS + ROLLBACK_EVENTS
FAILABLE_HANDOFF_EVENTS = {
    "writer_fence_result",
    "source_zero",
    "activation_transition",
    "activation_result",
    "traffic_result",
    "runtime_acceptance",
}
PRE_ACTIVATION_REQUIRED = HANDOFF_EVENTS[:6]


class ControlledArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.exit(2, "FAIL: invalid command-line arguments\n")


def _parser() -> argparse.ArgumentParser:
    parser = ControlledArgumentParser(
        description="Validate closed legacy target handoff evidence."
    )
    parser.add_argument(
        "--phase",
        required=True,
        choices=("bootstrap", "auto", "in-progress", "activation-ready", "terminal"),
    )
    parser.add_argument("--expected-outcome", choices=("success", "rollback"))
    parser.add_argument(
        "--baseline-evidence",
        help="trusted protected-branch evidence whose history must be preserved",
    )
    parser.add_argument("evidence")
    return parser


def _exact_keys(value: Any, expected: set[str], label: str, failures: list[str]) -> bool:
    if not isinstance(value, dict):
        failures.append(f"{label} must be a mapping")
        return False
    actual = set(value)
    if actual != expected:
        failures.append(f"{label} must use the exact closed schema")
        return False
    return True


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return parsed


def _validate_controller(
    value: object, *, app: str, label: str, failures: list[str]
) -> None:
    expected = {"apiVersion", "kind", "name"}
    if not _exact_keys(value, expected, label, failures):
        return
    assert isinstance(value, dict)
    kind = value["kind"]
    api_version = value["apiVersion"]
    if kind not in {"Deployment", "Rollout"}:
        failures.append(f"{label}.kind must be Deployment or Rollout")
    expected_api = "apps/v1" if kind == "Deployment" else "argoproj.io/v1alpha1"
    if api_version != expected_api:
        failures.append(f"{label}.apiVersion does not match controller kind")
    if value["name"] != app:
        failures.append(f"{label}.name must equal spec.app")


def _validate_identity(
    value: object,
    *,
    app: str,
    label: str,
    source: bool,
    failures: list[str],
) -> None:
    expected = {"context", "cluster_fingerprint", "namespace", "controller"}
    if source:
        expected.add("rollback_replicas")
    else:
        expected.add("approved_replicas")
    if not _exact_keys(value, expected, label, failures):
        return
    assert isinstance(value, dict)
    for field in ("context", "namespace"):
        field_value = value[field]
        if not isinstance(field_value, str) or not SAFE_IDENTITY.fullmatch(field_value):
            failures.append(f"{label}.{field} must be a bounded safe identity")
        elif field_value.lower() in PLACEHOLDERS:
            failures.append(f"{label}.{field} must not be a placeholder")
    if not isinstance(value["cluster_fingerprint"], str) or not CLUSTER_FINGERPRINT.fullmatch(
        value["cluster_fingerprint"]
    ):
        failures.append(
            f"{label}.cluster_fingerprint must be sha256 plus 64 lowercase hex"
        )
    _validate_controller(value["controller"], app=app, label=f"{label}.controller", failures=failures)
    if source:
        replicas = value["rollback_replicas"]
        if not isinstance(replicas, int) or isinstance(replicas, bool) or not 1 <= replicas <= 100:
            failures.append(f"{label}.rollback_replicas must be an integer from 1 to 100")
    else:
        replicas = value["approved_replicas"]
        if not isinstance(replicas, int) or isinstance(replicas, bool) or not 1 <= replicas <= 100:
            failures.append(f"{label}.approved_replicas must be an integer from 1 to 100")


def _validate_rows(
    events: dict[str, Any], failures: list[str]
) -> tuple[dict[str, str], dict[str, datetime], dict[str, str]]:
    statuses: dict[str, str] = {}
    timestamps: dict[str, datetime] = {}
    references: dict[str, str] = {}
    for event in ALL_EVENTS:
        row = events[event]
        label = f"spec.events.{event}"
        if not _exact_keys(row, ROW_KEYS, label, failures):
            continue
        assert isinstance(row, dict)
        status = row["status"]
        if status not in ROW_STATUSES:
            failures.append(f"{label}.status is not allowed")
            continue
        statuses[event] = status
        evidence_values = (
            row["evidence_ref"],
            row["timestamp_utc"],
            row["actor"],
            row["observed_state"],
        )
        if status == "PENDING":
            if evidence_values != (None, None, None, None):
                failures.append(f"{label} PENDING row must use null evidence fields")
            continue
        if not is_stable_evidence_ref(row["evidence_ref"]):
            failures.append(f"{label}.evidence_ref is not a stable approved reference")
        elif row["evidence_ref"] in references:
            failures.append(f"{label}.evidence_ref reuses stale evidence")
        else:
            references[row["evidence_ref"]] = event
        timestamp = _parse_utc(row["timestamp_utc"])
        if timestamp is None:
            failures.append(f"{label}.timestamp_utc is not canonical UTC")
        else:
            timestamps[event] = timestamp
        if not is_safe_actor(row["actor"]):
            failures.append(f"{label}.actor is not a bounded sanitized actor")
        if not is_safe_observed_state(row["observed_state"]):
            failures.append(f"{label}.observed_state is not bounded sanitized evidence")
    return statuses, timestamps, references


def _validate_event_time_order(
    statuses: dict[str, str], timestamps: dict[str, datetime], failures: list[str]
) -> None:
    last: datetime | None = None
    for event in ALL_EVENTS:
        if statuses.get(event) == "PENDING":
            continue
        current = timestamps.get(event)
        if current is None:
            continue
        if last is not None and current < last:
            failures.append("operational evidence timestamps violate state-machine order")
            return
        last = current


def _traffic_pair_is_consistent(statuses: dict[str, str]) -> bool:
    authorization = statuses.get("traffic_authorization")
    result = statuses.get("traffic_result")
    return (authorization, result) in {
        ("PENDING", "PENDING"),
        ("VERIFIED", "PENDING"),
        ("VERIFIED", "VERIFIED"),
        ("VERIFIED", "FAILED"),
        ("NOT_APPLICABLE", "PENDING"),
        ("NOT_APPLICABLE", "NOT_APPLICABLE"),
        ("NOT_INVOKED", "PENDING"),
        ("NOT_INVOKED", "NOT_INVOKED"),
    }


def _validate_partial_prefix(
    statuses: dict[str, str], parallel: bool, failures: list[str]
) -> None:
    if not _traffic_pair_is_consistent(statuses):
        failures.append("traffic event statuses must form one consistent pair")
    blocked = False
    failed = 0
    for event in HANDOFF_EVENTS:
        status = statuses.get(event)
        if status is None:
            continue
        if parallel and event in PARALLEL_SOURCE_ROWS and status == "NOT_INVOKED":
            continue
        if event in {"traffic_authorization", "traffic_result"} and status == "NOT_APPLICABLE":
            if blocked:
                failures.append("handoff event statuses violate deterministic ordering")
            continue
        if status == "VERIFIED":
            if blocked:
                failures.append("handoff event statuses violate deterministic ordering")
        elif status == "FAILED":
            failed += 1
            if event not in FAILABLE_HANDOFF_EVENTS or blocked:
                failures.append("FAILED is allowed for only one reached operational handoff event")
            blocked = True
        elif status in {"PENDING", "NOT_INVOKED"}:
            blocked = True
        else:
            failures.append("handoff event status is not valid at this sequence position")
    if failed > 1:
        failures.append("at most one operational handoff event may be FAILED")


def _validate_rollback_partial(
    statuses: dict[str, str], parallel: bool, failures: list[str]
) -> None:
    blocked = False
    for event in ROLLBACK_EVENTS:
        status = statuses.get(event)
        if status == "VERIFIED":
            if blocked:
                failures.append("rollback event statuses violate deterministic ordering")
        elif status == "NOT_APPLICABLE" and event in {
            "rollback_traffic_isolation",
            "rollback_route_restore",
        } or (parallel and status == "NOT_APPLICABLE" and event in {
            "rollback_source_restore",
            "rollback_source_ready",
        }):
            if blocked:
                failures.append("rollback event statuses violate deterministic ordering")
        elif status in {"PENDING", "NOT_INVOKED"}:
            blocked = True
        elif status is not None:
            failures.append("rollback event status is not valid at this sequence position")


def _validate_rollback_traffic_dependency(
    statuses: dict[str, str], failures: list[str]
) -> None:
    if statuses.get("traffic_authorization") != "VERIFIED":
        return
    for event in ("rollback_traffic_isolation", "rollback_route_restore"):
        if statuses.get(event) == "NOT_APPLICABLE":
            failures.append(
                "verified traffic authorization forbids NOT_APPLICABLE rollback traffic rows"
            )
            return


def _validate_in_progress(
    handoff_status: str,
    rollback_status: str,
    statuses: dict[str, str],
    parallel: bool,
    failures: list[str],
) -> None:
    if handoff_status not in {"PREPARING", "IN_PROGRESS"}:
        failures.append("in-progress phase requires a non-terminal handoff_status")
    if rollback_status not in {"NOT_INVOKED", "IN_PROGRESS"}:
        failures.append("in-progress phase requires a non-terminal rollback_status")
    _validate_partial_prefix(statuses, parallel, failures)
    _validate_rollback_partial(statuses, parallel, failures)
    if rollback_status == "NOT_INVOKED":
        rollback_states = [statuses.get(event) for event in ROLLBACK_EVENTS]
        if any(status not in {"PENDING", "NOT_INVOKED"} for status in rollback_states):
            failures.append("rollback rows must remain PENDING or close NOT_INVOKED")
        if any(status == "NOT_INVOKED" for status in rollback_states) and any(
            statuses.get(event) == "PENDING" for event in HANDOFF_EVENTS
        ):
            failures.append("rollback NOT_INVOKED closure requires completed handoff rows")
    if rollback_status == "IN_PROGRESS" and statuses.get("rollback_authorization") != "VERIFIED":
        failures.append("rollback IN_PROGRESS requires verified rollback authorization")


def _validate_activation_ready(
    handoff_status: str,
    rollback_status: str,
    statuses: dict[str, str],
    parallel: bool,
    failures: list[str],
) -> None:
    if handoff_status != "IN_PROGRESS" or rollback_status != "NOT_INVOKED":
        failures.append("activation-ready requires IN_PROGRESS / NOT_INVOKED statuses")
    for event in PRE_ACTIVATION_REQUIRED:
        if parallel and event in PARALLEL_SOURCE_ROWS:
            if statuses.get(event) != "NOT_INVOKED":
                failures.append(
                    f"activation-ready requires {event} NOT_INVOKED in parallel"
                )
            continue
        if statuses.get(event) != "VERIFIED":
            failures.append(f"activation-ready requires {event} VERIFIED")
    for event in HANDOFF_EVENTS[len(PRE_ACTIVATION_REQUIRED) :]:
        if statuses.get(event) != "PENDING":
            failures.append(f"activation-ready requires later event {event} PENDING")
    for event in ROLLBACK_EVENTS:
        if statuses.get(event) != "PENDING":
            failures.append("activation-ready requires rollback rows PENDING")
            break


def _validate_bootstrap(
    handoff_status: str,
    rollback_status: str,
    statuses: dict[str, str],
    parallel: bool,
    failures: list[str],
) -> None:
    if (handoff_status, rollback_status) != ("PREPARING", "NOT_INVOKED"):
        failures.append("bootstrap requires PREPARING / NOT_INVOKED statuses")
    for event in ALL_EVENTS:
        expected = "NOT_INVOKED" if parallel and event in PARALLEL_SOURCE_ROWS else "PENDING"
        if statuses.get(event) != expected:
            failures.append("bootstrap requires every event row PENDING")
            break


def _validate_success(
    handoff_status: str,
    rollback_status: str,
    statuses: dict[str, str],
    parallel: bool,
    failures: list[str],
) -> None:
    if (handoff_status, rollback_status) != ("VERIFIED", "NOT_INVOKED"):
        failures.append("success terminal outcome requires VERIFIED / NOT_INVOKED")
    for event in HANDOFF_EVENTS:
        if event in {"traffic_authorization", "traffic_result"}:
            continue
        if parallel and event in PARALLEL_SOURCE_ROWS:
            if statuses.get(event) != "NOT_INVOKED":
                failures.append(
                    f"success terminal outcome requires {event} NOT_INVOKED in parallel"
                )
            continue
        if statuses.get(event) != "VERIFIED":
            failures.append(f"success terminal outcome requires {event} VERIFIED")
    traffic = (
        statuses.get("traffic_authorization"),
        statuses.get("traffic_result"),
    )
    if traffic not in {("VERIFIED", "VERIFIED"), ("NOT_APPLICABLE", "NOT_APPLICABLE")}:
        failures.append("success terminal traffic rows must be consistently closed")
    for event in ROLLBACK_EVENTS:
        if statuses.get(event) != "NOT_INVOKED":
            failures.append("success terminal rollback rows must all be NOT_INVOKED")
            break


def _validate_rollback(
    handoff_status: str,
    rollback_status: str,
    statuses: dict[str, str],
    parallel: bool,
    failures: list[str],
) -> None:
    if (handoff_status, rollback_status) != ("ABORTED_ROLLED_BACK", "VERIFIED"):
        failures.append("rollback terminal outcome requires ABORTED_ROLLED_BACK / VERIFIED")
    _validate_partial_prefix(statuses, parallel, failures)
    if statuses.get("preparation") != "VERIFIED":
        failures.append("rollback terminal outcome requires preparation VERIFIED")
    if any(statuses.get(event) == "PENDING" for event in HANDOFF_EVENTS):
        failures.append("rollback terminal outcome forbids PENDING handoff rows")
    if statuses.get("rollback_authorization") != "VERIFIED":
        failures.append("rollback terminal outcome requires rollback authorization VERIFIED")
    conditional = (
        statuses.get("rollback_traffic_isolation"),
        statuses.get("rollback_route_restore"),
    )
    if conditional not in {("VERIFIED", "VERIFIED"), ("NOT_APPLICABLE", "NOT_APPLICABLE")}:
        failures.append("rollback traffic isolation and route restore must close consistently")
    for event in (
        "rollback_target_zero",
        "rollback_source_restore",
        "rollback_source_ready",
        "rollback_writer_policy",
    ):
        expected = "VERIFIED"
        if parallel and event in {
            "rollback_source_restore",
            "rollback_source_ready",
        }:
            expected = "NOT_APPLICABLE"
        if statuses.get(event) != expected:
            failures.append(
                f"rollback terminal outcome requires {event} "
                f"{'NOT_APPLICABLE in parallel' if parallel else 'VERIFIED'}"
            )


def _validate_enforcement(
    spec: dict[str, Any], failures: list[str]
) -> None:
    """Validate optional degraded-enforcement fields closed.

    Absent `enforcement_mode` defaults to `sre-policy` for backward
    compatibility. `ce-guarded` records a migration-owner authorization for
    degraded GitLab CE enforcement and is immutable for the whole handoff.
    """
    mode = spec.get("enforcement_mode", "sre-policy")
    if mode not in ENFORCEMENT_MODES:
        failures.append("spec.enforcement_mode is not an allowed enforcement mode")
        return
    authorization = spec.get("ce_guarded_authorization")
    if mode == "ce-guarded":
        if not isinstance(authorization, dict):
            failures.append(
                "spec.enforcement_mode ce-guarded requires a closed "
                "spec.ce_guarded_authorization row"
            )
            return
        if not _exact_keys(
            authorization, ROW_KEYS, "spec.ce_guarded_authorization", failures
        ):
            return
        if authorization["status"] != "VERIFIED":
            failures.append("spec.ce_guarded_authorization.status must be VERIFIED")
        if not is_stable_evidence_ref(authorization["evidence_ref"]):
            failures.append(
                "spec.ce_guarded_authorization.evidence_ref is not a stable "
                "approved reference"
            )
        if _parse_utc(authorization["timestamp_utc"]) is None:
            failures.append(
                "spec.ce_guarded_authorization.timestamp_utc is not canonical UTC"
            )
        if not is_safe_actor(authorization["actor"]):
            failures.append(
                "spec.ce_guarded_authorization.actor is not a bounded sanitized actor"
            )
        if not is_safe_observed_state(authorization["observed_state"]):
            failures.append(
                "spec.ce_guarded_authorization.observed_state is not bounded "
                "sanitized evidence"
            )
    elif authorization is not None:
        failures.append(
            "spec.enforcement_mode sre-policy forbids spec.ce_guarded_authorization"
        )


def _validate_activation_mode(spec: dict[str, Any], failures: list[str]) -> bool:
    """Validate optional parallel-activation mode and return whether parallel.

    Absent `activation_mode` defaults to `stop-source` (source scaled to zero
    before the target starts). `parallel` is for stateless request/response
    services without consumer-group ownership: the source stays at its
    rollback replica count through activation, `source_scale_authorization`
    and `source_zero` close as `NOT_INVOKED`, and rollback source rows close
    as `NOT_APPLICABLE`. The mode is immutable for the whole handoff.
    """
    mode = spec.get("activation_mode", "stop-source")
    if mode not in ACTIVATION_MODES:
        failures.append("spec.activation_mode is not an allowed activation mode")
        return False
    events = spec.get("events")
    if not isinstance(events, dict):
        return mode == "parallel"
    if mode == "parallel":
        for event in PARALLEL_SOURCE_ROWS:
            row = events.get(event)
            if not isinstance(row, dict) or row.get("status") != "NOT_INVOKED":
                failures.append(
                    f"spec.activation_mode parallel requires {event} NOT_INVOKED"
                )
    elif any(
        isinstance(events.get(event), dict)
        and events[event].get("status") == "NOT_INVOKED"
        for event in PARALLEL_SOURCE_ROWS
    ):
        failures.append(
            "spec.activation_mode stop-source forbids NOT_INVOKED source rows"
        )
    return mode == "parallel"


def validate_evidence(
    document: Any, *, phase: str, expected_outcome: str | None
) -> list[str]:
    failures: list[str] = []
    if not _exact_keys(document, {"apiVersion", "kind", "spec"}, "document", failures):
        return failures
    assert isinstance(document, dict)
    if document["apiVersion"] != API_VERSION:
        failures.append("apiVersion is not the required evidence schema version")
    if document["kind"] != KIND:
        failures.append("kind is not LegacyTargetHandoffEvidence")
    spec = document["spec"]
    spec_keys = {
        "mode",
        "environment",
        "target_branch",
        "app",
        "source",
        "target",
        "handoff_status",
        "rollback_status",
        "events",
    }
    if not isinstance(spec, dict):
        failures.append("spec must be a mapping")
        return failures
    for optional_key in (
        "enforcement_mode",
        "ce_guarded_authorization",
        "activation_mode",
    ):
        if optional_key in spec:
            spec_keys.add(optional_key)
    if not _exact_keys(spec, spec_keys, "spec", failures):
        return failures
    _validate_enforcement(spec, failures)
    parallel = _validate_activation_mode(spec, failures)
    if spec["mode"] != "legacy-live":
        failures.append("spec.mode must be legacy-live")
    if spec["environment"] != "staging":
        failures.append("spec.environment must be staging for legacy-live")
    if spec["target_branch"] != "staging":
        failures.append("spec.target_branch must be protected staging")
    app = spec["app"]
    if not isinstance(app, str) or not K8S_NAME.fullmatch(app):
        failures.append("spec.app must be a canonical Kubernetes application name")
        return failures
    _validate_identity(spec["source"], app=app, label="spec.source", source=True, failures=failures)
    _validate_identity(spec["target"], app=app, label="spec.target", source=False, failures=failures)
    source = spec["source"]
    target = spec["target"]
    if (
        isinstance(source, dict)
        and isinstance(target, dict)
        and source.get("context") == target.get("context")
    ):
        failures.append("source and target contexts must be distinct clusters")
    if (
        isinstance(source, dict)
        and isinstance(target, dict)
        and source.get("cluster_fingerprint") == target.get("cluster_fingerprint")
    ):
        failures.append("source and target cluster_fingerprints must be distinct")
    handoff_status = spec["handoff_status"]
    rollback_status = spec["rollback_status"]
    if handoff_status not in HANDOFF_STATUSES:
        failures.append("spec.handoff_status is not allowed")
    if rollback_status not in ROLLBACK_STATUSES:
        failures.append("spec.rollback_status is not allowed")
    events = spec["events"]
    if not _exact_keys(events, set(ALL_EVENTS), "spec.events", failures):
        return failures
    assert isinstance(events, dict)
    statuses, timestamps, _ = _validate_rows(events, failures)
    _validate_event_time_order(statuses, timestamps, failures)
    _validate_rollback_traffic_dependency(statuses, failures)
    if phase == "bootstrap":
        if expected_outcome is not None:
            failures.append("expected outcome is valid only for terminal phase")
        _validate_bootstrap(handoff_status, rollback_status, statuses, parallel, failures)
    elif phase == "auto":
        if (handoff_status, rollback_status) == ("VERIFIED", "NOT_INVOKED"):
            _validate_success(handoff_status, rollback_status, statuses, parallel, failures)
        elif (handoff_status, rollback_status) == (
            "ABORTED_ROLLED_BACK",
            "VERIFIED",
        ):
            _validate_rollback(handoff_status, rollback_status, statuses, parallel, failures)
        else:
            _validate_in_progress(handoff_status, rollback_status, statuses, parallel, failures)
    elif phase == "in-progress":
        if expected_outcome is not None:
            failures.append("expected outcome is valid only for terminal phase")
        _validate_in_progress(handoff_status, rollback_status, statuses, parallel, failures)
    elif phase == "activation-ready":
        if expected_outcome is not None:
            failures.append("expected outcome is valid only for terminal phase")
        _validate_activation_ready(handoff_status, rollback_status, statuses, parallel, failures)
    else:
        if expected_outcome == "success":
            _validate_success(handoff_status, rollback_status, statuses, parallel, failures)
        elif expected_outcome == "rollback":
            _validate_rollback(handoff_status, rollback_status, statuses, parallel, failures)
        else:
            failures.append("terminal phase requires --expected-outcome")
    return failures


def validate_evidence_progression(baseline: Any, candidate: Any) -> list[str]:
    """Bind candidate history to an immutable trusted protected-branch baseline."""
    failures: list[str] = []
    baseline_failures = validate_evidence(
        baseline, phase="auto", expected_outcome=None
    )
    if baseline_failures:
        return ["trusted baseline evidence does not satisfy the closed state machine"]
    candidate_failures = validate_evidence(
        candidate, phase="auto", expected_outcome=None
    )
    if candidate_failures:
        return ["candidate evidence does not satisfy the closed state machine"]
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        return ["evidence progression requires two closed-schema documents"]

    if baseline.get("apiVersion") != candidate.get("apiVersion"):
        failures.append("candidate evidence must preserve immutable apiVersion")
    if baseline.get("kind") != candidate.get("kind"):
        failures.append("candidate evidence must preserve immutable kind")
    baseline_spec = baseline.get("spec")
    candidate_spec = candidate.get("spec")
    if not isinstance(baseline_spec, dict) or not isinstance(candidate_spec, dict):
        return ["evidence progression requires two closed-schema specs"]
    baseline_events = baseline_spec.get("events")
    candidate_events = candidate_spec.get("events")
    if not isinstance(baseline_events, dict) or not isinstance(candidate_events, dict):
        return failures + ["evidence progression requires two closed event maps"]
    parallel_conversion = (
        baseline_spec.get("activation_mode", "stop-source") == "stop-source"
        and candidate_spec.get("activation_mode", "stop-source") == "parallel"
        and all(
            baseline_events.get(event, {}).get("status") == "PENDING"
            and candidate_events.get(event, {}).get("status") == "NOT_INVOKED"
            for event in PARALLEL_SOURCE_ROWS
        )
        and all(
            baseline_events.get(event) == candidate_events.get(event)
            for event in ALL_EVENTS
            if event not in PARALLEL_SOURCE_ROWS
        )
    )
    for field in (
        "mode",
        "environment",
        "target_branch",
        "app",
        "source",
        "target",
        "enforcement_mode",
        "ce_guarded_authorization",
    ):
        if baseline_spec.get(field, "sre-policy") != candidate_spec.get(
            field, "sre-policy"
        ):
            failures.append(f"candidate evidence must preserve immutable spec.{field}")
    if baseline_spec.get("activation_mode", "stop-source") != candidate_spec.get(
        "activation_mode", "stop-source"
    ) and not parallel_conversion:
        failures.append("candidate evidence must preserve immutable spec.activation_mode")

    baseline_handoff = baseline_spec.get("handoff_status")
    candidate_handoff = candidate_spec.get("handoff_status")
    if candidate_handoff not in HANDOFF_STATUS_PROGRESS.get(baseline_handoff, set()):
        failures.append("candidate handoff_status regresses trusted history")
    baseline_rollback = baseline_spec.get("rollback_status")
    candidate_rollback = candidate_spec.get("rollback_status")
    if candidate_rollback not in ROLLBACK_STATUS_PROGRESS.get(baseline_rollback, set()):
        failures.append("candidate rollback_status regresses trusted history")

    progressed_events = 0
    for event in ALL_EVENTS:
        baseline_row = baseline_events.get(event)
        candidate_row = candidate_events.get(event)
        if not isinstance(baseline_row, dict) or not isinstance(candidate_row, dict):
            failures.append(f"candidate event {event} does not preserve closed history")
            continue
        if parallel_conversion and event in PARALLEL_SOURCE_ROWS:
            continue
        if baseline_row.get("status") != "PENDING" and candidate_row != baseline_row:
            failures.append(f"completed event {event} is immutable")
        elif baseline_row.get("status") == "PENDING" and candidate_row.get(
            "status"
        ) == "PENDING" and candidate_row != baseline_row:
            failures.append(f"pending event {event} cannot carry mutable evidence")
        elif baseline_row != candidate_row:
            progressed_events += 1
    if progressed_events > 1:
        failures.append("at most one pending event row may progress per evidence MR")
    status_only_activation_ready = (
        baseline_handoff == "PREPARING"
        and candidate_handoff == "IN_PROGRESS"
        and baseline_rollback == candidate_rollback
        and progressed_events == 0
        and baseline_events.get("activation_authorization", {}).get("status")
        == "VERIFIED"
    )
    if (
        baseline_handoff != candidate_handoff
        or baseline_rollback != candidate_rollback
    ) and progressed_events != 1 and not status_only_activation_ready:
        failures.append("top-level status may advance only with one progressed event row")
    return failures


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code)
    try:
        document = load_single_yaml(
            args.evidence,
            max_bytes=MAX_EVIDENCE_BYTES,
            max_tokens=MAX_YAML_TOKENS,
        )
    except SafeInputError:
        print("FAIL: evidence input cannot be parsed safely")
        return 2
    baseline = None
    if args.baseline_evidence is not None:
        try:
            baseline = load_single_yaml(
                args.baseline_evidence,
                max_bytes=MAX_EVIDENCE_BYTES,
                max_tokens=MAX_YAML_TOKENS,
            )
        except SafeInputError:
            print("FAIL: baseline evidence input cannot be parsed safely")
            return 2
    try:
        failures = validate_evidence(
            document, phase=args.phase, expected_outcome=args.expected_outcome
        )
        if baseline is not None:
            failures.extend(validate_evidence_progression(baseline, document))
    except (RecursionError, ValueError, OverflowError, MemoryError):
        print("FAIL: evidence validation could not complete safely")
        return 2
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        print(f"FAIL: {len(failures)} legacy handoff evidence violation(s)")
        return 1
    print("PASS: legacy handoff evidence matches the requested state-machine phase")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
