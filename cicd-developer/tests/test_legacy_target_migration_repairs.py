from __future__ import annotations

import argparse
import builtins
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_ROOT = SKILL_ROOT / "validators"


def load_validator(name: str) -> ModuleType:
    path = VALIDATOR_ROOT / name
    validators_dir = str(VALIDATOR_ROOT)
    if validators_dir not in sys.path:
        sys.path.insert(0, validators_dir)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


EVIDENCE = load_validator("check_legacy_target_handoff_evidence.py")
DORMANT = load_validator("check_dormant_target.py")
APPLICATION = load_validator("check_legacy_target_application.py")
LIVE_EMPTY = load_validator("check_legacy_target_live_empty.py")
PROVENANCE = load_validator("check_legacy_reference_provenance.py")


def legacy_live_workflow_text() -> str:
    return (
        SKILL_ROOT / "workflows/supplements/legacy-live-target-migration.md"
    ).read_text(encoding="utf-8")
ROUTES = load_validator("check_routes.py")


def evidence_ref(index: int) -> str:
    return f"record:{index:064x}"


def evidence_row(status: str, index: int) -> dict[str, object]:
    if status == "PENDING":
        return {
            "evidence_ref": None,
            "timestamp_utc": None,
            "actor": None,
            "observed_state": None,
            "status": status,
        }
    timestamp = datetime(2026, 8, 12, tzinfo=timezone.utc) + timedelta(
        seconds=index
    )
    return {
        "evidence_ref": evidence_ref(index),
        "timestamp_utc": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": "sre-owner",
        "observed_state": f"sanitized-state-{index}",
        "status": status,
    }


def base_evidence() -> dict[str, object]:
    return {
        "apiVersion": EVIDENCE.API_VERSION,
        "kind": EVIDENCE.KIND,
        "spec": {
            "mode": "legacy-live",
            "environment": "staging",
            "target_branch": "staging",
            "app": "webhook",
            "source": {
                "context": "eu-eks-prod-admin",
                "cluster_fingerprint": "sha256:" + "1" * 64,
                "namespace": "staging-eu",
                "controller": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "webhook",
                },
                "rollback_replicas": 1,
            },
            "target": {
                "context": "eu-eks-staging",
                "cluster_fingerprint": "sha256:" + "2" * 64,
                "namespace": "staging-webhook",
                "controller": {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Rollout",
                    "name": "webhook",
                },
                "approved_replicas": 1,
            },
            "handoff_status": "PREPARING",
            "rollback_status": "NOT_INVOKED",
            "events": {
                event: evidence_row("PENDING", index + 1)
                for index, event in enumerate(EVIDENCE.ALL_EVENTS)
            },
        },
    }


def set_event(document: dict[str, object], event: str, status: str) -> None:
    index = EVIDENCE.ALL_EVENTS.index(event) + 1
    document["spec"]["events"][event] = evidence_row(status, index)  # type: ignore[index]


def activation_ready_evidence() -> dict[str, object]:
    document = base_evidence()
    document["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    for event in EVIDENCE.PRE_ACTIVATION_REQUIRED:
        set_event(document, event, "VERIFIED")
    return document


def progressed_evidence(
    baseline: dict[str, object], event: str, status: str
) -> dict[str, object]:
    candidate = copy.deepcopy(baseline)
    set_event(candidate, event, status)
    return candidate


def success_evidence(*, traffic: bool) -> dict[str, object]:
    document = base_evidence()
    spec = document["spec"]  # type: ignore[index]
    spec["handoff_status"] = "VERIFIED"
    spec["rollback_status"] = "NOT_INVOKED"
    for event in EVIDENCE.HANDOFF_EVENTS:
        status = "VERIFIED"
        if event in {"traffic_authorization", "traffic_result"} and not traffic:
            status = "NOT_APPLICABLE"
        set_event(document, event, status)
    for event in EVIDENCE.ROLLBACK_EVENTS:
        set_event(document, event, "NOT_INVOKED")
    return document


def rollback_evidence(*, post_traffic: bool, in_progress: bool = False) -> dict[str, object]:
    document = base_evidence()
    spec = document["spec"]  # type: ignore[index]
    spec["handoff_status"] = "IN_PROGRESS" if in_progress else "ABORTED_ROLLED_BACK"
    spec["rollback_status"] = "IN_PROGRESS" if in_progress else "VERIFIED"
    failure_event = "runtime_acceptance" if post_traffic else "activation_result"
    blocked = False
    for event in EVIDENCE.HANDOFF_EVENTS:
        if event == failure_event:
            set_event(document, event, "FAILED")
            blocked = True
        elif blocked:
            set_event(document, event, "NOT_INVOKED")
        else:
            set_event(document, event, "VERIFIED")
    set_event(document, "rollback_authorization", "VERIFIED")
    conditional = "VERIFIED" if post_traffic else "NOT_APPLICABLE"
    set_event(document, "rollback_traffic_isolation", conditional)
    if in_progress:
        return document
    for event in (
        "rollback_target_zero",
        "rollback_source_restore",
        "rollback_source_ready",
        "rollback_writer_policy",
    ):
        set_event(document, event, "VERIFIED")
    set_event(document, "rollback_route_restore", conditional)
    return document


@pytest.mark.parametrize("traffic", [False, True])
def test_evidence_validator_accepts_success_terminal_matrix(traffic: bool) -> None:
    assert EVIDENCE.validate_evidence(
        success_evidence(traffic=traffic),
        phase="terminal",
        expected_outcome="success",
    ) == []


@pytest.mark.parametrize("post_traffic", [False, True])
def test_evidence_validator_accepts_pre_and_post_traffic_rollback(
    post_traffic: bool,
) -> None:
    assert EVIDENCE.validate_evidence(
        rollback_evidence(post_traffic=post_traffic),
        phase="terminal",
        expected_outcome="rollback",
    ) == []


def test_evidence_validator_accepts_preparation_and_activation_ready_states() -> None:
    assert EVIDENCE.validate_evidence(
        base_evidence(), phase="bootstrap", expected_outcome=None
    ) == []
    preparation = base_evidence()
    set_event(preparation, "preparation", "VERIFIED")
    preparation["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    assert EVIDENCE.validate_evidence(
        preparation, phase="auto", expected_outcome=None
    ) == []
    assert EVIDENCE.validate_evidence(
        activation_ready_evidence(), phase="activation-ready", expected_outcome=None
    ) == []


def test_evidence_bootstrap_rejects_pre_authorized_state() -> None:
    failures = EVIDENCE.validate_evidence(
        activation_ready_evidence(), phase="bootstrap", expected_outcome=None
    )
    assert "bootstrap requires" in "\n".join(failures)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-field",
        "query-ref",
        "noncanonical-time",
        "secret-state",
        "todo-state",
        "embedded-todo-state",
        "duplicate-ref",
        "impossible-order",
        "two-failures",
        "bad-traffic-pair",
        "route-before-source-ready",
        "query-state",
        "raw-output-state",
        "unsafe-actor",
    ],
)
def test_evidence_validator_rejects_closed_schema_secret_stale_and_order_errors(
    mutation: str,
) -> None:
    document = success_evidence(traffic=True)
    events = document["spec"]["events"]  # type: ignore[index]
    if mutation == "unknown-field":
        events["preparation"]["raw"] = "forbidden"
    elif mutation == "query-ref":
        events["preparation"]["evidence_ref"] = (
            "https://gitlab.addx.ai/PAAS/webhook/-/issues/1?token=value"
        )
    elif mutation == "noncanonical-time":
        events["preparation"]["timestamp_utc"] = "2026-08-12T00:00:01+00:00"
    elif mutation == "secret-state":
        events["preparation"]["observed_state"] = "password=raw-value"
    elif mutation == "todo-state":
        events["preparation"]["observed_state"] = "TODO"
    elif mutation == "embedded-todo-state":
        events["preparation"]["observed_state"] = "ready but stale evidence"
    elif mutation == "query-state":
        events["preparation"]["observed_state"] = (
            "artifact=https://example.invalid/object?download=1"
        )
    elif mutation == "raw-output-state":
        events["preparation"]["observed_state"] = "kubectl output READY 1/1"
    elif mutation == "unsafe-actor":
        events["preparation"]["actor"] = "sre owner with free-form text"
    elif mutation == "duplicate-ref":
        events["writer_fence_authorization"]["evidence_ref"] = events[
            "preparation"
        ]["evidence_ref"]
    elif mutation == "impossible-order":
        events["writer_fence_result"]["timestamp_utc"] = "2026-08-11T23:59:59Z"
    elif mutation == "two-failures":
        document = rollback_evidence(post_traffic=True)
        set_event(document, "activation_result", "FAILED")
    elif mutation == "bad-traffic-pair":
        events["traffic_result"] = evidence_row("NOT_APPLICABLE", 10)
    else:
        document = rollback_evidence(post_traffic=True)
        events = document["spec"]["events"]  # type: ignore[index]
        events["rollback_route_restore"]["timestamp_utc"] = "2026-08-12T00:00:14Z"
        events["rollback_source_ready"]["timestamp_utc"] = "2026-08-12T00:00:16Z"
    assert EVIDENCE.validate_evidence(
        document,
        phase="terminal",
        expected_outcome=(
            "rollback"
            if mutation in {"two-failures", "route-before-source-ready"}
            else "success"
        ),
    )


def test_evidence_validator_rejects_same_source_and_target_identity() -> None:
    document = base_evidence()
    document["spec"]["target"]["context"] = document["spec"]["source"]["context"]  # type: ignore[index]
    document["spec"]["target"]["namespace"] = document["spec"]["source"]["namespace"]  # type: ignore[index]
    assert "must be distinct" in "\n".join(
        EVIDENCE.validate_evidence(
            document,
            phase="auto",
            expected_outcome=None,
        )
    )


def test_evidence_validator_orders_not_invoked_terminal_rows() -> None:
    document = success_evidence(traffic=True)
    events = document["spec"]["events"]  # type: ignore[index]
    events["rollback_authorization"]["timestamp_utc"] = "2026-08-11T23:59:59Z"
    assert "timestamps violate" in "\n".join(
        EVIDENCE.validate_evidence(
            document,
            phase="terminal",
            expected_outcome="success",
        )
    )


def test_evidence_progression_preserves_identity_and_completed_history() -> None:
    baseline = base_evidence()
    baseline["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    set_event(baseline, "preparation", "VERIFIED")
    candidate = progressed_evidence(baseline, "writer_fence_authorization", "VERIFIED")
    assert EVIDENCE.validate_evidence_progression(baseline, candidate) == []

    identity_change = copy.deepcopy(candidate)
    identity_change["spec"]["source"]["namespace"] = "other-source"  # type: ignore[index]
    assert "immutable spec.source" in "\n".join(
        EVIDENCE.validate_evidence_progression(baseline, identity_change)
    )

    completed_rewrite = copy.deepcopy(candidate)
    completed_rewrite["spec"]["events"]["preparation"][  # type: ignore[index]
        "observed_state"
    ] = "rewritten-sanitized-state"
    assert "completed event preparation is immutable" in "\n".join(
        EVIDENCE.validate_evidence_progression(baseline, completed_rewrite)
    )

    status_regression = copy.deepcopy(candidate)
    status_regression["spec"]["handoff_status"] = "PREPARING"  # type: ignore[index]
    assert "handoff_status regresses" in "\n".join(
        EVIDENCE.validate_evidence_progression(baseline, status_regression)
    )


def test_evidence_progression_rejects_completed_row_regression() -> None:
    baseline = activation_ready_evidence()
    candidate = copy.deepcopy(baseline)
    candidate["spec"]["events"]["writer_fence_result"] = evidence_row(  # type: ignore[index]
        "PENDING", 3
    )
    assert EVIDENCE.validate_evidence_progression(baseline, candidate)


def test_evidence_progression_rejects_batch_event_update() -> None:
    baseline = base_evidence()
    candidate = copy.deepcopy(baseline)
    set_event(candidate, "preparation", "VERIFIED")
    set_event(candidate, "writer_fence_authorization", "VERIFIED")
    candidate["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    assert "at most one pending event row" in "\n".join(
        EVIDENCE.validate_evidence_progression(baseline, candidate)
    )


def test_evidence_rejects_same_cluster_with_different_namespaces() -> None:
    evidence = base_evidence()
    evidence["spec"]["target"]["context"] = evidence["spec"]["source"]["context"]
    assert "contexts must be distinct clusters" in "\n".join(
        EVIDENCE.validate_evidence(evidence, phase="auto", expected_outcome=None)
    )


def test_evidence_rejects_same_canonical_cluster_fingerprint() -> None:
    evidence = base_evidence()
    evidence["spec"]["target"]["cluster_fingerprint"] = evidence["spec"][  # type: ignore[index]
        "source"
    ]["cluster_fingerprint"]
    assert "cluster_fingerprints must be distinct" in "\n".join(
        EVIDENCE.validate_evidence(evidence, phase="auto", expected_outcome=None)
    )


def ce_guarded_evidence(status: str = "VERIFIED") -> dict[str, object]:
    document = base_evidence()
    document["spec"]["enforcement_mode"] = "ce-guarded"  # type: ignore[index]
    document["spec"]["ce_guarded_authorization"] = evidence_row(status, 1)  # type: ignore[index]
    return document


def test_evidence_ce_guarded_requires_closed_authorization_row() -> None:
    evidence = base_evidence()
    evidence["spec"]["enforcement_mode"] = "ce-guarded"  # type: ignore[index]
    failures = EVIDENCE.validate_evidence(evidence, phase="bootstrap", expected_outcome=None)
    assert "requires a closed" in "\n".join(failures)

    closed = ce_guarded_evidence()
    assert EVIDENCE.validate_evidence(closed, phase="bootstrap", expected_outcome=None) == []


def test_evidence_ce_guarded_authorization_row_must_be_verified_and_closed() -> None:
    pending = ce_guarded_evidence(status="PENDING")
    assert "status must be VERIFIED" in "\n".join(
        EVIDENCE.validate_evidence(pending, phase="bootstrap", expected_outcome=None)
    )

    empty = ce_guarded_evidence()
    empty["spec"]["ce_guarded_authorization"] = {  # type: ignore[index]
        "evidence_ref": None,
        "timestamp_utc": None,
        "actor": None,
        "observed_state": None,
        "status": "VERIFIED",
    }
    assert "not a stable approved reference" in "\n".join(
        EVIDENCE.validate_evidence(empty, phase="bootstrap", expected_outcome=None)
    )

    extra_key = ce_guarded_evidence()
    extra_key["spec"]["ce_guarded_authorization"]["bypass"] = "x"  # type: ignore[index]
    assert "exact closed schema" in "\n".join(
        EVIDENCE.validate_evidence(extra_key, phase="bootstrap", expected_outcome=None)
    )


def test_evidence_sre_policy_forbids_ce_guarded_authorization_row() -> None:
    defaulted = base_evidence()
    defaulted["spec"]["ce_guarded_authorization"] = evidence_row("VERIFIED", 1)  # type: ignore[index]
    assert "forbids spec.ce_guarded_authorization" in "\n".join(
        EVIDENCE.validate_evidence(defaulted, phase="bootstrap", expected_outcome=None)
    )

    explicit = base_evidence()
    explicit["spec"]["enforcement_mode"] = "sre-policy"  # type: ignore[index]
    explicit["spec"]["ce_guarded_authorization"] = evidence_row("VERIFIED", 1)  # type: ignore[index]
    assert "forbids spec.ce_guarded_authorization" in "\n".join(
        EVIDENCE.validate_evidence(explicit, phase="bootstrap", expected_outcome=None)
    )

    plain = base_evidence()
    assert EVIDENCE.validate_evidence(plain, phase="bootstrap", expected_outcome=None) == []


def test_evidence_rejects_unknown_enforcement_mode() -> None:
    evidence = base_evidence()
    evidence["spec"]["enforcement_mode"] = "hybrid"  # type: ignore[index]
    assert "not an allowed enforcement mode" in "\n".join(
        EVIDENCE.validate_evidence(evidence, phase="bootstrap", expected_outcome=None)
    )


def parallel_evidence() -> dict[str, object]:
    document = ce_guarded_evidence()
    document["spec"]["activation_mode"] = "parallel"  # type: ignore[index]
    for index, event in enumerate(EVIDENCE.PARALLEL_SOURCE_ROWS, start=4):
        document["spec"]["events"][event] = evidence_row("NOT_INVOKED", index)  # type: ignore[index]
    return document


def test_evidence_parallel_bootstrap_requires_not_invoked_source_rows() -> None:
    parallel = parallel_evidence()
    assert EVIDENCE.validate_evidence(
        parallel, phase="bootstrap", expected_outcome=None
    ) == []

    pending = copy.deepcopy(parallel)
    pending["spec"]["events"]["source_zero"] = evidence_row("PENDING", 5)  # type: ignore[index]
    failures = EVIDENCE.validate_evidence(pending, phase="bootstrap", expected_outcome=None)
    assert "every event row PENDING" in "\n".join(failures)

    verified = copy.deepcopy(parallel)
    verified["spec"]["events"]["source_scale_authorization"] = evidence_row(  # type: ignore[index]
        "VERIFIED", 4
    )
    assert "requires source_scale_authorization NOT_INVOKED" in "\n".join(
        EVIDENCE.validate_evidence(verified, phase="bootstrap", expected_outcome=None)
    )


def test_evidence_stop_source_forbids_not_invoked_source_rows() -> None:
    evidence = ce_guarded_evidence()
    evidence["spec"]["activation_mode"] = "stop-source"  # type: ignore[index]
    evidence["spec"]["events"]["source_zero"] = evidence_row("NOT_INVOKED", 5)  # type: ignore[index]
    assert "forbids NOT_INVOKED source rows" in "\n".join(
        EVIDENCE.validate_evidence(evidence, phase="bootstrap", expected_outcome=None)
    )


def test_evidence_rejects_unknown_activation_mode() -> None:
    evidence = ce_guarded_evidence()
    evidence["spec"]["activation_mode"] = "hybrid"  # type: ignore[index]
    assert "not an allowed activation mode" in "\n".join(
        EVIDENCE.validate_evidence(evidence, phase="bootstrap", expected_outcome=None)
    )


def test_evidence_parallel_activation_ready_skips_source_rows() -> None:
    document = parallel_evidence()
    document["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    for event in ("preparation", "writer_fence_authorization", "writer_fence_result",
                  "activation_authorization"):
        set_event(document, event, "VERIFIED")
    assert EVIDENCE.validate_evidence(
        document, phase="activation-ready", expected_outcome=None
    ) == []


def test_evidence_parallel_conversion_is_single_mr() -> None:
    baseline = ce_guarded_evidence()
    candidate = parallel_evidence()
    assert EVIDENCE.validate_evidence_progression(baseline, candidate) == []

    extra_change = copy.deepcopy(candidate)
    set_event(extra_change, "preparation", "VERIFIED")
    assert EVIDENCE.validate_evidence_progression(baseline, extra_change)


def test_evidence_parallel_success_and_rollback_rows() -> None:
    document = parallel_evidence()
    document["spec"]["handoff_status"] = "VERIFIED"  # type: ignore[index]
    for event in EVIDENCE.HANDOFF_EVENTS:
        if event in EVIDENCE.PARALLEL_SOURCE_ROWS:
            continue
        set_event(document, event, "VERIFIED")
    for index, event in enumerate(EVIDENCE.ROLLBACK_EVENTS, start=100):
        document["spec"]["events"][event] = evidence_row("NOT_INVOKED", index)  # type: ignore[index]
    assert EVIDENCE.validate_evidence(
        document, phase="terminal", expected_outcome="success"
    ) == []

    rolled = parallel_evidence()
    rolled["spec"]["handoff_status"] = "ABORTED_ROLLED_BACK"  # type: ignore[index]
    rolled["spec"]["rollback_status"] = "VERIFIED"  # type: ignore[index]
    set_event(rolled, "preparation", "VERIFIED")
    for index, event in enumerate(EVIDENCE.HANDOFF_EVENTS[1:], start=2):
        if event in EVIDENCE.PARALLEL_SOURCE_ROWS:
            continue
        rolled["spec"]["events"][event] = evidence_row("NOT_INVOKED", index)  # type: ignore[index]
    for index, event in enumerate(EVIDENCE.ROLLBACK_EVENTS, start=100):
        rolled["spec"]["events"][event] = evidence_row("NOT_INVOKED", index)  # type: ignore[index]
    set_event(rolled, "rollback_authorization", "VERIFIED")
    rolled["spec"]["events"]["rollback_traffic_isolation"] = evidence_row(  # type: ignore[index]
        "NOT_APPLICABLE", 101
    )
    rolled["spec"]["events"]["rollback_target_zero"] = evidence_row("VERIFIED", 102)  # type: ignore[index]
    rolled["spec"]["events"]["rollback_source_restore"] = evidence_row("NOT_APPLICABLE", 103)  # type: ignore[index]
    rolled["spec"]["events"]["rollback_source_ready"] = evidence_row("NOT_APPLICABLE", 104)  # type: ignore[index]
    rolled["spec"]["events"]["rollback_route_restore"] = evidence_row("NOT_APPLICABLE", 105)  # type: ignore[index]
    rolled["spec"]["events"]["rollback_writer_policy"] = evidence_row("VERIFIED", 106)  # type: ignore[index]
    failures = EVIDENCE.validate_evidence(rolled, phase="terminal", expected_outcome="rollback")
    assert failures == []


def test_evidence_status_only_activation_ready_advance() -> None:
    baseline = copy.deepcopy(ce_guarded_evidence())
    baseline["spec"]["activation_mode"] = "parallel"  # type: ignore[index]
    for index, event in enumerate(EVIDENCE.PARALLEL_SOURCE_ROWS, start=4):
        baseline["spec"]["events"][event] = evidence_row("NOT_INVOKED", index)  # type: ignore[index]
    baseline["spec"]["handoff_status"] = "PREPARING"  # type: ignore[index]
    for event in ("preparation", "writer_fence_authorization", "writer_fence_result",
                  "activation_authorization"):
        set_event(baseline, event, "VERIFIED")
    candidate = copy.deepcopy(baseline)
    candidate["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    assert EVIDENCE.validate_evidence_progression(baseline, candidate) == []

    without_auth = copy.deepcopy(baseline)
    without_auth["spec"]["events"]["activation_authorization"] = evidence_row(  # type: ignore[index]
        "PENDING", 6
    )
    no_auth_advance = copy.deepcopy(without_auth)
    no_auth_advance["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    assert EVIDENCE.validate_evidence_progression(without_auth, no_auth_advance)


def test_evidence_parallel_mode_is_immutable_in_progression() -> None:
    baseline = parallel_evidence()
    downgraded = copy.deepcopy(baseline)
    downgraded["spec"]["activation_mode"] = "stop-source"  # type: ignore[index]
    failures = EVIDENCE.validate_evidence_progression(baseline, downgraded)
    assert failures


def test_evidence_enforcement_mode_and_authorization_are_immutable_in_progression() -> None:
    baseline = base_evidence()
    escalated = ce_guarded_evidence()
    assert "immutable spec.enforcement_mode" in "\n".join(
        EVIDENCE.validate_evidence_progression(baseline, escalated)
    )

    downgraded = ce_guarded_evidence()
    downgraded["spec"]["ce_guarded_authorization"]["observed_state"] = (  # type: ignore[index]
        "rewritten-authorization-state"
    )
    assert "immutable spec.ce_guarded_authorization" in "\n".join(
        EVIDENCE.validate_evidence_progression(ce_guarded_evidence(), downgraded)
    )

    progressed = copy.deepcopy(ce_guarded_evidence())
    progressed["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    set_event(progressed, "preparation", "VERIFIED")
    assert EVIDENCE.validate_evidence_progression(
        ce_guarded_evidence(), progressed
    ) == []


def test_evidence_cli_compares_candidate_to_trusted_baseline(tmp_path: Path) -> None:
    baseline = activation_ready_evidence()
    candidate = copy.deepcopy(baseline)
    candidate["spec"]["events"]["preparation"]["actor"] = "different-owner"  # type: ignore[index]
    baseline_path = tmp_path / "baseline.yaml"
    candidate_path = tmp_path / "candidate.yaml"
    baseline_path.write_text(
        yaml.safe_dump(baseline, sort_keys=False), encoding="utf-8"
    )
    candidate_path.write_text(
        yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8"
    )
    assert EVIDENCE.main(
        [
            "check_legacy_target_handoff_evidence.py",
            "--phase",
            "auto",
            "--baseline-evidence",
            str(baseline_path),
            str(candidate_path),
        ]
    ) == 1


def test_rollback_not_applicable_requires_forward_traffic_never_verified() -> None:
    document = rollback_evidence(post_traffic=True)
    set_event(document, "rollback_traffic_isolation", "NOT_APPLICABLE")
    set_event(document, "rollback_route_restore", "NOT_APPLICABLE")
    failures = EVIDENCE.validate_evidence(
        document, phase="terminal", expected_outcome="rollback"
    )
    assert "verified traffic authorization forbids NOT_APPLICABLE" in "\n".join(failures)


def test_failed_traffic_still_requires_isolation_and_route_restore() -> None:
    document = rollback_evidence(post_traffic=True)
    set_event(document, "traffic_result", "FAILED")
    set_event(document, "runtime_acceptance", "NOT_INVOKED")
    set_event(document, "rollback_traffic_isolation", "NOT_APPLICABLE")
    set_event(document, "rollback_route_restore", "NOT_APPLICABLE")
    failures = EVIDENCE.validate_evidence(
        document, phase="terminal", expected_outcome="rollback"
    )
    assert "verified traffic authorization forbids NOT_APPLICABLE" in "\n".join(
        failures
    )


@pytest.mark.parametrize(
    "observed_state",
    [
        "state=(stdout):ready",
        "state=[raw manifest output]",
        "state={stderr}:failed",
        "state=(terminal-output):captured",
        "metadata: name=webhook",
        "spec: replicas=0",
        "data: redacted",
        "stringData: redacted",
        "--- manifest follows",
        "state={ready}",
        "opaque=" + "A" * 120,
    ],
)
def test_evidence_rejects_punctuation_wrapped_raw_output_markers(
    observed_state: str,
) -> None:
    document = base_evidence()
    row = evidence_row("VERIFIED", 1)
    row["observed_state"] = observed_state
    document["spec"]["events"]["preparation"] = row  # type: ignore[index]
    document["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    assert "observed_state is not bounded sanitized evidence" in "\n".join(
        EVIDENCE.validate_evidence(document, phase="auto", expected_outcome=None)
    )


@pytest.mark.parametrize(
    "yaml_text",
    [
        "value: &anchor marker\ncopy: *anchor\n",
        "value: one\nvalue: two\n",
        "value: \"bad\\u0001value\"\n",
    ],
)
def test_evidence_cli_rejects_unsafe_yaml_without_echo(
    tmp_path: Path, yaml_text: str
) -> None:
    path = tmp_path / "unsafe-evidence.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    result = EVIDENCE.main(
        [
            "check_legacy_target_handoff_evidence.py",
            "--phase",
            "auto",
            str(path),
        ]
    )
    assert result == 2


def test_safe_read_rejects_symlinked_parent_directory(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    (real_parent / "evidence.yaml").write_text("safe: data\n", encoding="utf-8")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(EVIDENCE.SafeInputError):
        EVIDENCE.load_single_yaml(
            str(linked_parent / "evidence.yaml"),
            max_bytes=1024,
            max_tokens=128,
        )


def controller(*, marker: str = "dormant", replicas: int = 0) -> dict[str, object]:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Rollout",
        "metadata": {
            "name": "webhook",
            "namespace": "staging-webhook",
            "labels": {"app": "webhook"},
            "annotations": {DORMANT.RUNTIME_STATE_ANNOTATION: marker},
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": "webhook"}},
            "template": {
                "metadata": {"labels": {"app": "webhook"}},
                "spec": {"containers": [{"name": "webhook", "image": "example"}]},
            },
        },
    }


def render_documents(*, marker: str = "dormant", replicas: int = 0) -> list[dict[str, object]]:
    return [
        controller(marker=marker, replicas=replicas),
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "webhook", "namespace": "staging-webhook"},
            "spec": {"selector": {"app": "webhook"}},
        },
        {
            "apiVersion": "external-secrets.io/v1",
            "kind": "ExternalSecret",
            "metadata": {"name": "webhook", "namespace": "staging-webhook"},
            "spec": {"target": {"name": "webhook"}},
        },
    ]


def validate_render(documents: list[dict[str, object]]) -> list[str]:
    return DORMANT.validate_documents(
        documents,
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
    )


def test_dormant_validator_requires_exact_marker_and_safe_service() -> None:
    assert validate_render(render_documents()) == []
    missing_marker = render_documents()
    del missing_marker[0]["metadata"]["annotations"]  # type: ignore[index]
    assert "runtime-state marker" in "\n".join(validate_render(missing_marker))


@pytest.mark.parametrize(
    ("api_version", "kind"),
    [
        ("v1", "Endpoints"),
        ("discovery.k8s.io/v1", "EndpointSlice"),
        ("networking.k8s.io/v1", "Ingress"),
    ],
)
def test_dormant_validator_rejects_direct_traffic_resources(
    api_version: str, kind: str
) -> None:
    documents = render_documents()
    documents.append(
        {
            "apiVersion": api_version,
            "kind": kind,
            "metadata": {"name": "traffic", "namespace": "staging-webhook"},
            "spec": {},
        }
    )
    assert "forbidden direct traffic resource" in "\n".join(validate_render(documents))


@pytest.mark.parametrize(
    "service_patch",
    [
        {"type": "LoadBalancer"},
        {"type": "ExternalName", "externalName": "example.invalid"},
        {"externalIPs": ["192.0.2.1"]},
        {"loadBalancerClass": "example"},
        {"ports": [{"port": 80, "nodePort": 30080}]},
        {"selector": {}},
        {"selector": {"app": "other"}},
    ],
)
def test_dormant_validator_rejects_unsafe_service_variants(
    service_patch: dict[str, object],
) -> None:
    documents = render_documents()
    documents[1]["spec"].update(service_patch)  # type: ignore[index]
    assert validate_render(documents)


def test_branch_guard_accepts_exact_activation_and_rejects_extra_render_delta() -> None:
    baseline = render_documents()
    candidate = render_documents(marker="active", replicas=1)
    evidence = activation_ready_evidence()
    assert DORMANT.validate_branch_guard(
        baseline,
        candidate,
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
        baseline_evidence=evidence,
        candidate_evidence=evidence,
        changed_paths=("k8s/overlays/staging-eu/replicas.yaml",),
        evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
        allowed_transition_prefixes=("k8s/overlays/staging-eu/",),
    ) == []
    candidate[2]["spec"]["extra"] = "drift"  # type: ignore[index]
    assert "only runtime-state marker and controller replicas" in "\n".join(
        DORMANT.validate_branch_guard(
            baseline,
            candidate,
            app="webhook",
            namespace="staging-webhook",
            expected_kind="Rollout",
            baseline_evidence=evidence,
            candidate_evidence=evidence,
            changed_paths=("k8s/overlays/staging-eu/replicas.yaml",),
            evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
            allowed_transition_prefixes=("k8s/overlays/staging-eu/",),
        )
    )


@pytest.mark.parametrize(
    "changed_path",
    [
        ".gitlab-ci.yml",
        "scripts/ci/check_dormant_target.py",
        "src/main/java/com/addx/Webhook.java",
        "k8s/overlays/prod-eu/deployment.yaml",
        "Jenkinsfile",
    ],
)
def test_branch_guard_transition_rejects_every_path_outside_exact_allowlist(
    changed_path: str,
) -> None:
    failures = DORMANT.validate_branch_guard(
        render_documents(),
        render_documents(marker="active", replicas=1),
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
        baseline_evidence=activation_ready_evidence(),
        candidate_evidence=activation_ready_evidence(),
        changed_paths=("k8s/overlays/staging-eu/replicas.yaml", changed_path),
        evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
        allowed_transition_prefixes=("k8s/overlays/staging-eu/",),
    )
    assert "outside the exact target-overlay allowlist" in "\n".join(failures)


def test_branch_guard_transition_rejects_same_mr_evidence_change() -> None:
    baseline_evidence = activation_ready_evidence()
    candidate_evidence = progressed_evidence(
        baseline_evidence, "activation_transition", "VERIFIED"
    )
    failures = DORMANT.validate_branch_guard(
        render_documents(),
        render_documents(marker="active", replicas=1),
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
        baseline_evidence=baseline_evidence,
        candidate_evidence=candidate_evidence,
        changed_paths=(
            "k8s/overlays/staging-eu/replicas.yaml",
            "docs/deployment/legacy-target-handoff-evidence.yaml",
        ),
        evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
        allowed_transition_prefixes=("k8s/overlays/staging-eu/",),
    )
    assert "evidence-only MR" in "\n".join(failures)


def test_branch_guard_accepts_prior_evidence_only_mr() -> None:
    baseline_evidence = base_evidence()
    candidate_evidence = progressed_evidence(
        baseline_evidence, "preparation", "VERIFIED"
    )
    candidate_evidence["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    assert DORMANT.validate_branch_guard(
        render_documents(),
        render_documents(),
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
        baseline_evidence=baseline_evidence,
        candidate_evidence=candidate_evidence,
        changed_paths=("docs/deployment/legacy-target-handoff-evidence.yaml",),
        evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
        allowed_transition_prefixes=("k8s/overlays/staging-eu/",),
        render_byte_identical=True,
    ) == []


@pytest.mark.parametrize("render_changed", [False, True])
def test_branch_guard_rejects_evidence_only_mr_with_extra_path_or_render_delta(
    render_changed: bool,
) -> None:
    baseline_evidence = base_evidence()
    candidate_evidence = progressed_evidence(
        baseline_evidence, "preparation", "VERIFIED"
    )
    candidate_evidence["spec"]["handoff_status"] = "IN_PROGRESS"  # type: ignore[index]
    candidate_render = render_documents()
    changed_paths = ("docs/deployment/legacy-target-handoff-evidence.yaml",)
    if render_changed:
        candidate_render[2]["spec"]["refreshInterval"] = "1h"  # type: ignore[index]
    else:
        changed_paths += ("docs/extra.md",)
    failures = DORMANT.validate_branch_guard(
        render_documents(),
        candidate_render,
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
        baseline_evidence=baseline_evidence,
        candidate_evidence=candidate_evidence,
        changed_paths=changed_paths,
        evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
        allowed_transition_prefixes=("k8s/overlays/staging-eu/",),
        render_byte_identical=not render_changed,
    )
    assert "change exactly the evidence path" in "\n".join(failures)


def test_branch_guard_preserves_dormant_and_post_activation_replica_contracts() -> None:
    evidence = base_evidence()
    dormant_candidate = render_documents()
    dormant_candidate[2]["spec"]["refreshInterval"] = "1h"  # type: ignore[index]
    assert DORMANT.validate_branch_guard(
        render_documents(),
        dormant_candidate,
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
        baseline_evidence=evidence,
        candidate_evidence=evidence,
        changed_paths=("k8s/overlays/staging-eu/secret-refresh.yaml",),
        evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
    ) == []
    assert DORMANT.validate_branch_guard(
        render_documents(marker="active", replicas=1),
        render_documents(marker="active", replicas=2),
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
        baseline_evidence=success_evidence(traffic=True),
        candidate_evidence=success_evidence(traffic=True),
        changed_paths=("docs/ordinary.md",),
        evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
    )


def test_branch_guard_freezes_dormant_render_after_operational_authorization() -> None:
    candidate = render_documents()
    candidate[2]["spec"]["refreshInterval"] = "1h"  # type: ignore[index]
    failures = DORMANT.validate_branch_guard(
        render_documents(),
        candidate,
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
        baseline_evidence=activation_ready_evidence(),
        candidate_evidence=activation_ready_evidence(),
        changed_paths=("k8s/overlays/staging-eu/secret-refresh.yaml",),
        evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
    )
    assert "immutable after operational authorization" in "\n".join(failures)


@pytest.mark.parametrize("post_traffic", [False, True])
def test_branch_guard_accepts_safe_pre_and_post_traffic_rollback(
    post_traffic: bool,
) -> None:
    evidence = rollback_evidence(post_traffic=post_traffic, in_progress=True)
    assert DORMANT.validate_branch_guard(
        render_documents(marker="active", replicas=1),
        render_documents(),
        app="webhook",
        namespace="staging-webhook",
        expected_kind="Rollout",
        baseline_evidence=evidence,
        candidate_evidence=evidence,
        changed_paths=("k8s/overlays/staging-eu/replicas.yaml",),
        evidence_path="docs/deployment/legacy-target-handoff-evidence.yaml",
        allowed_transition_prefixes=("k8s/overlays/staging-eu/",),
    ) == []


def writer_inventory(*, target_directed: bool = False) -> dict[str, object]:
    writers: list[dict[str, object]] = []
    if target_directed:
        writers.append(
            {
                "writer_id": "jenkins/job/webhook",
                "context": "eu-eks-staging",
                "namespace": "staging-webhook",
                "app": "webhook",
                "write_capability": "target-directed",
                "state": "disabled",
            }
        )
    return {"schema_version": 1, "writers": writers}


def fake_inventory(
    *,
    producer_gvr: str | None = None,
    namespace_absent: bool = False,
    fail_gvr: str | None = None,
    discovered_extra: tuple[str, ...] = (),
    fail_discovery: bool = False,
):
    calls: list[list[str]] = []

    def run(_kubectl: str, arguments: list[str]) -> tuple[int, bytes]:
        calls.append(arguments)
        if "namespace" in arguments and "--namespace" not in arguments:
            if namespace_absent:
                return 0, b""
            return 0, b'{"apiVersion":"v1","kind":"Namespace"}'
        if "api-resources" in arguments:
            if fail_discovery:
                return 1, b""
            resources = (*LIVE_EMPTY.INVENTORY_GVRS, *discovered_extra)
            return 0, ("\n".join(resources) + "\n").encode()
        gvr = arguments[arguments.index("get") + 1]
        if gvr == fail_gvr:
            return 1, b""
        items: list[dict[str, object]] = []
        if gvr == producer_gvr:
            item: dict[str, object] = {"apiVersion": "v1", "kind": "Item"}
            if gvr == "horizontalpodautoscalers.autoscaling":
                item["spec"] = {"scaleTargetRef": {"kind": "Rollout", "name": "webhook"}}
            elif gvr == "scaledobjects.keda.sh":
                item["spec"] = {"scaleTargetRef": {"name": "webhook"}}
            elif gvr == "scaledjobs.keda.sh":
                item["spec"] = {"jobTargetRef": {"template": {}}}
            items = [item]
        return 0, json.dumps({"apiVersion": "v1", "kind": "List", "items": items}).encode()

    return run, calls


def test_live_empty_accepts_absent_namespace_and_exact_empty_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, calls = fake_inventory(namespace_absent=True)
    monkeypatch.setattr(LIVE_EMPTY, "_run_kubectl", runner)
    assert LIVE_EMPTY.check_live_empty(
        kubectl="kubectl",
        context="eu-eks-staging",
        namespace="staging-webhook",
        app="webhook",
        writer_document=writer_inventory(),
    ) == (0, [])
    assert len(calls) == 1

    runner, calls = fake_inventory()
    monkeypatch.setattr(LIVE_EMPTY, "_run_kubectl", runner)
    result, failures = LIVE_EMPTY.check_live_empty(
        kubectl="kubectl",
        context="eu-eks-staging",
        namespace="staging-webhook",
        app="webhook",
        writer_document=writer_inventory(),
    )
    assert result == 0 and not failures
    assert len(calls) == 2 + len(LIVE_EMPTY.INVENTORY_GVRS)
    assert all("--context" in call for call in calls)
    assert all(
        "--namespace" in call
        for call in calls[1:]
        if "api-resources" not in call
    )


def test_live_empty_inventory_includes_every_traffic_resource_class() -> None:
    assert {
        "services",
        "ingresses.networking.k8s.io",
        "endpoints",
        "endpointslices.discovery.k8s.io",
    } <= set(LIVE_EMPTY.INVENTORY_GVRS)


@pytest.mark.parametrize(
    "gvr",
    [
        "services",
        "ingresses.networking.k8s.io",
        "endpoints",
        "endpointslices.discovery.k8s.io",
    ],
)
def test_live_empty_rejects_exact_namespace_traffic_resources(
    monkeypatch: pytest.MonkeyPatch, gvr: str
) -> None:
    runner, _ = fake_inventory(producer_gvr=gvr)
    monkeypatch.setattr(LIVE_EMPTY, "_run_kubectl", runner)
    result, failures = LIVE_EMPTY.check_live_empty(
        kubectl="kubectl",
        context="eu-eks-staging",
        namespace="staging-webhook",
        app="webhook",
        writer_document=writer_inventory(),
    )
    assert result == 1
    assert f"forbidden resource class {gvr}" in "\n".join(failures)


@pytest.mark.parametrize("gvr", LIVE_EMPTY.INVENTORY_GVRS)
def test_live_empty_rejects_every_required_producer_and_autoscaler(
    monkeypatch: pytest.MonkeyPatch, gvr: str
) -> None:
    runner, _ = fake_inventory(producer_gvr=gvr)
    monkeypatch.setattr(LIVE_EMPTY, "_run_kubectl", runner)
    result, failures = LIVE_EMPTY.check_live_empty(
        kubectl="kubectl",
        context="eu-eks-staging",
        namespace="staging-webhook",
        app="webhook",
        writer_document=writer_inventory(),
    )
    assert result == 1
    assert gvr in "\n".join(failures)


def test_live_empty_stops_on_auth_unknown_crd_and_target_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _ = fake_inventory(fail_gvr="rollouts.argoproj.io")
    monkeypatch.setattr(LIVE_EMPTY, "_run_kubectl", runner)
    result, _ = LIVE_EMPTY.check_live_empty(
        kubectl="kubectl",
        context="eu-eks-staging",
        namespace="staging-webhook",
        app="webhook",
        writer_document=writer_inventory(),
    )
    assert result == 2
    result, _ = LIVE_EMPTY.check_live_empty(
        kubectl="kubectl",
        context="eu-eks-staging",
        namespace="staging-webhook",
        app="webhook",
        writer_document=writer_inventory(target_directed=True),
    )
    assert result == 1

    runner, _ = fake_inventory(fail_discovery=True)
    monkeypatch.setattr(LIVE_EMPTY, "_run_kubectl", runner)
    result, failures = LIVE_EMPTY.check_live_empty(
        kubectl="kubectl",
        context="eu-eks-staging",
        namespace="staging-webhook",
        app="webhook",
        writer_document=writer_inventory(),
    )
    assert result == 2
    assert "discovery" in "\n".join(failures)


def test_live_empty_discovers_and_stops_on_unclassified_crd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown_gvr = "mysteryruns.example.io"
    runner, calls = fake_inventory(
        producer_gvr=unknown_gvr,
        discovered_extra=(unknown_gvr,),
    )
    monkeypatch.setattr(LIVE_EMPTY, "_run_kubectl", runner)
    result, failures = LIVE_EMPTY.check_live_empty(
        kubectl="kubectl",
        context="eu-eks-staging",
        namespace="staging-webhook",
        app="webhook",
        writer_document=writer_inventory(),
    )
    assert result == 2
    assert "unclassified namespaced CRD" in "\n".join(failures)
    assert any(unknown_gvr in call for call in calls)


def test_live_empty_main_stops_on_real_kubectl_process_failure(tmp_path: Path) -> None:
    kubectl = tmp_path / "kubectl-fail"
    kubectl.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    kubectl.chmod(0o755)
    inventory = tmp_path / "writers.yaml"
    inventory.write_text(
        yaml.safe_dump(writer_inventory(), sort_keys=False), encoding="utf-8"
    )
    assert LIVE_EMPTY.main(
        [
            "check_legacy_target_live_empty.py",
            "--context",
            "eu-eks-staging",
            "--namespace",
            "staging-webhook",
            "--app",
            "webhook",
            "--writer-inventory",
            str(inventory),
            "--kubectl",
            str(kubectl),
        ]
    ) == 2


def test_route_conflict_metadata_is_exact_and_does_not_block_additive_routes() -> None:
    table_path = SKILL_ROOT / "references/data/routes-build.yaml"
    table = yaml.safe_load(table_path.read_text(encoding="utf-8"))
    conflicts = table["route_conflicts"]
    assert ROUTES.validate_route_conflicts(
        conflicts, table_path, table["build_intents"]
    ) == 0
    participants = {
        (row["workflow_file"], row["mode"])
        for row in conflicts[0]["participants"]
    }
    assert participants == {
        ("workflows/add-target-cluster.md", "legacy-live"),
        ("workflows/adopt-kubectl-workload-into-argocd.md", "default"),
    }
    assert "workflows/new-service.md" not in {row[0] for row in participants}
    assert "workflows/add-rds.md" not in {row[0] for row in participants}

    composite = (
        "legacy source to empty target cluster and adopt existing "
        "kubectl-managed service into argo cd"
    )
    matched = ROUTES.resolve_route_conflicts(
        composite, table["build_intents"], conflicts
    )
    assert [row["id"] for row in matched] == [
        "legacy-live-cross-cluster-vs-same-cluster-adoption"
    ]
    assert matched[0]["question"].endswith("?")

    additive = "legacy source to empty target cluster and add rds"
    assert ROUTES.resolve_route_conflicts(
        additive, table["build_intents"], conflicts
    ) == []
    assert ROUTES.select_build_route_modes(additive, table["build_intents"]) == {
        ("workflows/add-target-cluster.md", "legacy-live"),
        ("workflows/add-rds.md", "default"),
    }


def provenance_args(digest: str) -> argparse.Namespace:
    return argparse.Namespace(
        expected_origin="https://gitlab.addx.ai/PAAS/webhook.git",
        protected_branch="staging",
        revision="a" * 40,
        overlay="k8s/overlays/staging-us",
        expected_render_digest=digest,
        provenance="ancestor",
        merged_mr_ref=None,
        gitlab_token_env="GITLAB_TOKEN",
        git="git",
        kubectl="kubectl",
    )


def test_provenance_uses_isolated_config_fresh_db_and_exact_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = b"apiVersion: v1\nkind: ConfigMap\n"
    digest = "sha256:" + hashlib.sha256(rendered).hexdigest()
    args = provenance_args(digest)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, *, env, cwd=None, timeout=60):
        del cwd, timeout
        calls.append((command, env))
        if command[-3:] == ["remote", "get-url", "origin"]:
            return 0, (args.expected_origin + "\n").encode()
        if "rev-parse" in command:
            return 0, (args.revision + "\n").encode()
        if command[-3:] == ["replace", "-l"] or "status" in command:
            return 0, b""
        if command[:2] == ["kubectl", "kustomize"]:
            return 0, rendered
        return 0, b""

    monkeypatch.setenv("GITLAB_TOKEN", "credential-marker")
    monkeypatch.setattr(PROVENANCE, "_run", fake_run)
    monkeypatch.setattr(PROVENANCE, "_tracked_paths", lambda *args: ("tracked",))
    monkeypatch.setattr(
        PROVENANCE, "validate_kustomize_dependency_closure", lambda **kwargs: []
    )
    monkeypatch.setattr(PROVENANCE, "_protected_branch_proof", lambda **kwargs: True)
    passed, observed = PROVENANCE.verify_provenance(args)
    assert passed and observed == digest
    flattened = "\n".join(" ".join(command) for command, _ in calls)
    assert "credential-marker" not in flattened
    assert "init --quiet" in flattened
    assert "--filter=blob:none" in flattened
    assert "checkout --quiet --detach" in flattened
    assert "replace -l" in flattened
    assert any(env["GIT_CONFIG_NOSYSTEM"] == "1" for _, env in calls)
    assert any(env["GIT_NO_REPLACE_OBJECTS"] == "1" for _, env in calls)
    assert any(env["GIT_CONFIG_VALUE_0"] == "never" for _, env in calls)


def test_provenance_missing_requests_fails_closed_with_fixed_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_import = builtins.__import__

    def import_without_requests(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "requests":
            raise ModuleNotFoundError("simulated unavailable dependency")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_requests)
    path = VALIDATOR_ROOT / "check_legacy_reference_provenance.py"
    spec = importlib.util.spec_from_file_location(
        "check_legacy_reference_provenance_without_requests", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main([path.name]) == 2
    assert capsys.readouterr().out == (
        "FAIL: required HTTP client dependency is unavailable\n"
    )


@pytest.mark.parametrize(
    "origin",
    [
        "ssh://git@gitlab.addx.ai/PAAS/webhook.git",
        "https://user@gitlab.addx.ai/PAAS/webhook.git",
        "https://gitlab.addx.ai/PAAS/webhook.git?token=value",
        "file:///tmp/webhook.git",
    ],
)
def test_provenance_rejects_unapproved_origin_before_commands(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    args = provenance_args("sha256:" + "0" * 64)
    args.expected_origin = origin
    monkeypatch.setenv("GITLAB_TOKEN", "credential-marker")
    monkeypatch.setattr(
        PROVENANCE,
        "_run",
        lambda *args, **kwargs: pytest.fail("command ran for unapproved origin"),
    )
    assert PROVENANCE.verify_provenance(args) == (False, None)


@pytest.mark.parametrize("branch", ["main", "master", "DEV-STAGE", "prod"])
def test_provenance_rejects_non_staging_reference_before_commands(
    monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    args = provenance_args("sha256:" + "0" * 64)
    args.protected_branch = branch
    monkeypatch.setenv("GITLAB_TOKEN", "credential-marker")
    monkeypatch.setattr(
        PROVENANCE,
        "_run",
        lambda *args, **kwargs: pytest.fail("command ran for non-staging branch"),
    )
    assert PROVENANCE.verify_provenance(args) == (False, None)


def test_provenance_stops_on_real_fetch_failure_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = provenance_args("sha256:" + "0" * 64)
    calls: list[list[str]] = []

    def fail_fetch(command, *, env, cwd=None, timeout=60):
        del env, cwd, timeout
        calls.append(command)
        if "fetch" in command:
            return 7, b""
        return 0, b""

    monkeypatch.setenv("GITLAB_TOKEN", "credential-marker")
    monkeypatch.setattr(PROVENANCE, "_run", fail_fetch)
    monkeypatch.setattr(
        PROVENANCE,
        "_protected_branch_proof",
        lambda **kwargs: pytest.fail("protected-branch API ran after failed fetch"),
    )
    assert PROVENANCE.verify_provenance(args) == (False, None)
    assert any("fetch" in command for command in calls)
    assert not any(command[:2] == ["kubectl", "kustomize"] for command in calls)


def test_provenance_stops_on_protected_branch_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = provenance_args("sha256:" + "0" * 64)
    calls: list[list[str]] = []

    def successful_git_until_proof(command, *, env, cwd=None, timeout=60):
        del env, cwd, timeout
        calls.append(command)
        if command[-3:] == ["remote", "get-url", "origin"]:
            return 0, (args.expected_origin + "\n").encode()
        if "rev-parse" in command:
            return 0, (args.revision + "\n").encode()
        return 0, b""

    monkeypatch.setenv("GITLAB_TOKEN", "credential-marker")
    monkeypatch.setattr(PROVENANCE, "_run", successful_git_until_proof)
    monkeypatch.setattr(PROVENANCE, "_protected_branch_proof", lambda **kwargs: False)
    assert PROVENANCE.verify_provenance(args) == (False, None)
    assert not any(command[:2] == ["kubectl", "kustomize"] for command in calls)


def test_provenance_stops_before_render_when_reference_closure_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = provenance_args("sha256:" + "0" * 64)
    calls: list[list[str]] = []

    def fake_run(command, *, env, cwd=None, timeout=60):
        del env, cwd, timeout
        calls.append(command)
        if command[-3:] == ["remote", "get-url", "origin"]:
            return 0, (args.expected_origin + "\n").encode()
        if "rev-parse" in command:
            return 0, (args.revision + "\n").encode()
        if command[-3:] == ["replace", "-l"] or "status" in command:
            return 0, b""
        return 0, b""

    monkeypatch.setenv("GITLAB_TOKEN", "credential-marker")
    monkeypatch.setattr(PROVENANCE, "_run", fake_run)
    monkeypatch.setattr(PROVENANCE, "_protected_branch_proof", lambda **kwargs: True)
    monkeypatch.setattr(PROVENANCE, "_tracked_paths", lambda *args: ("tracked",))
    monkeypatch.setattr(
        PROVENANCE,
        "validate_kustomize_dependency_closure",
        lambda **kwargs: ["remote dependency"],
    )
    assert PROVENANCE.verify_provenance(args) == (False, None)
    assert not any(command[:2] == ["kubectl", "kustomize"] for command in calls)


def test_provenance_helpers_fail_closed_on_project_branch_and_api_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert PROVENANCE._origin_project(
        "https://gitlab.addx.ai/PAAS/webhook.git"
    ) == "PAAS/webhook"
    assert PROVENANCE._origin_project("https://user@gitlab.addx.ai/PAAS/webhook.git") is None
    assert PROVENANCE._safe_overlay("k8s/overlays/staging-us")
    assert not PROVENANCE._safe_overlay("../outside")

    branch_document = {
        "name": "staging",
        "protected": True,
        "commit": {"id": "a" * 40},
    }
    monkeypatch.setattr(PROVENANCE, "_api_get", lambda *args, **kwargs: branch_document)
    assert PROVENANCE._protected_branch_proof(
        session=object(),
        project="PAAS/webhook",
        branch="staging",
        token="redacted",
        tip="a" * 40,
    )
    branch_document["commit"] = {"id": "b" * 40}
    assert not PROVENANCE._protected_branch_proof(
        session=object(),
        project="PAAS/webhook",
        branch="staging",
        token="redacted",
        tip="a" * 40,
    )


def test_provenance_api_reader_rejects_non_success_and_oversized_payload() -> None:
    class Response:
        def __init__(self, status: int, chunks: list[bytes]) -> None:
            self.status_code = status
            self._chunks = chunks

        def iter_content(self, _size: int):
            return iter(self._chunks)

    class Session:
        def __init__(self, response: Response) -> None:
            self.response = response

        def get(self, *args, **kwargs):
            del args, kwargs
            return self.response

    assert PROVENANCE._api_get(
        Session(Response(403, [])), "https://gitlab.addx.ai/api/v4/test", "redacted"
    ) is None
    assert PROVENANCE._api_get(
        Session(Response(200, [b'{"protected":true}'])),
        "https://gitlab.addx.ai/api/v4/test",
        "redacted",
    ) == {"protected": True}
    assert PROVENANCE._api_get(
        Session(Response(200, [b"x" * (PROVENANCE.MAX_API_BYTES + 1)])),
        "https://gitlab.addx.ai/api/v4/test",
        "redacted",
    ) is None


def _write_kustomization(path: Path, **fields: object) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
    }
    document.update(fields)
    kustomization = path / "kustomization.yaml"
    kustomization.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return kustomization


def test_kustomize_closure_accepts_tracked_regular_local_dependencies(
    tmp_path: Path,
) -> None:
    base = tmp_path / "k8s/base"
    deployment = base / "deployment.yaml"
    base.mkdir(parents=True)
    deployment.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    base_kustomization = _write_kustomization(base, resources=["deployment.yaml"])
    overlay_kustomization = _write_kustomization(
        tmp_path / "k8s/overlays/staging-eu", resources=["../../base"]
    )
    tracked = tuple(
        path.relative_to(tmp_path).as_posix()
        for path in (deployment, base_kustomization, overlay_kustomization)
    )
    assert DORMANT.validate_kustomize_dependency_closure(
        repository_root=str(tmp_path),
        overlay="k8s/overlays/staging-eu",
        tracked_paths=tracked,
    ) == []


def test_kustomize_closure_cli_uses_bounded_tracked_inventory(tmp_path: Path) -> None:
    kustomization = _write_kustomization(
        tmp_path / "k8s/overlays/staging-eu", resources=[]
    )
    tracked = tmp_path / "tracked-paths"
    tracked.write_bytes(
        kustomization.relative_to(tmp_path).as_posix().encode("utf-8") + b"\0"
    )
    assert DORMANT.main(
        [
            "check_dormant_target.py",
            "--app",
            "webhook",
            "--namespace",
            "staging-webhook",
            "--expected-kind",
            "Rollout",
            "--phase",
            "kustomize-closure",
            "--repository-root",
            str(tmp_path),
            "--overlay",
            "k8s/overlays/staging-eu",
            "--tracked-paths-file",
            str(tracked),
        ]
    ) == 0


@pytest.mark.parametrize(
    ("field", "reference"),
    [
        ("resources", "https://gitlab.example.invalid/team/base.git//k8s?ref=main"),
        ("bases", "github.com/example/base//k8s?ref=v1"),
        ("components", "git::ssh://git@example.invalid/component.git"),
    ],
)
def test_kustomize_closure_rejects_remote_base_and_component_bypasses(
    tmp_path: Path, field: str, reference: str
) -> None:
    kustomization = _write_kustomization(
        tmp_path / "k8s/overlays/staging-eu", **{field: [reference]}
    )
    failures = DORMANT.validate_kustomize_dependency_closure(
        repository_root=str(tmp_path),
        overlay="k8s/overlays/staging-eu",
        tracked_paths=(kustomization.relative_to(tmp_path).as_posix(),),
    )
    assert "remote, unsafe, missing, or symlinked" in "\n".join(failures)


def test_kustomize_closure_rejects_tracked_symlink_dependency_bypass(
    tmp_path: Path,
) -> None:
    external = tmp_path / "base.yaml"
    external.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    overlay = tmp_path / "k8s/overlays/staging-eu"
    kustomization = _write_kustomization(overlay, resources=["linked.yaml"])
    linked = overlay / "linked.yaml"
    linked.symlink_to(external)
    failures = DORMANT.validate_kustomize_dependency_closure(
        repository_root=str(tmp_path),
        overlay="k8s/overlays/staging-eu",
        tracked_paths=(
            kustomization.relative_to(tmp_path).as_posix(),
            linked.relative_to(tmp_path).as_posix(),
        ),
    )
    assert "symlinked" in "\n".join(failures)


def test_kustomize_closure_rejects_untracked_regular_dependency(tmp_path: Path) -> None:
    overlay = tmp_path / "k8s/overlays/staging-eu"
    kustomization = _write_kustomization(overlay, resources=["untracked.yaml"])
    (overlay / "untracked.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8"
    )
    failures = DORMANT.validate_kustomize_dependency_closure(
        repository_root=str(tmp_path),
        overlay="k8s/overlays/staging-eu",
        tracked_paths=(kustomization.relative_to(tmp_path).as_posix(),),
    )
    assert "tracked regular local file" in "\n".join(failures)


@pytest.mark.parametrize("field", ["generators", "transformers", "helmCharts"])
def test_kustomize_closure_rejects_exec_plugin_and_helm_fields(
    tmp_path: Path, field: str
) -> None:
    kustomization = _write_kustomization(
        tmp_path / "k8s/overlays/staging-eu", **{field: []}
    )
    failures = DORMANT.validate_kustomize_dependency_closure(
        repository_root=str(tmp_path),
        overlay="k8s/overlays/staging-eu",
        tracked_paths=(kustomization.relative_to(tmp_path).as_posix(),),
    )
    assert "plugin" in "\n".join(failures) or "Helm" in "\n".join(failures)


def test_docs_and_ci_recipe_bind_protected_staging_branch_and_machine_evidence() -> None:
    workflow = legacy_live_workflow_text()
    recipe = (SKILL_ROOT / "recipes/ci/gitlab-ci-legacy-target-lock.yml.tmpl").read_text(
        encoding="utf-8"
    )
    migration = (SKILL_ROOT / "recipes/docs/target-cluster-migration.md").read_text(
        encoding="utf-8"
    )
    for text in (workflow, migration):
        assert "targetRevision: staging" in text
        assert "migrations.addx.io/runtime-state" in text
        assert "check_legacy_target_handoff_evidence.py" in text
    assert '$CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "staging"' in recipe
    assert '$CI_COMMIT_BRANCH == "staging"' in recipe
    assert 'name: "{{legacy_lock_validation_image}}@sha256:' in recipe
    assert "{{legacy_lock_validation_image_digest}}" in recipe
    assert "inherit:" in recipe and "default: false" in recipe
    assert "variables: false" in recipe
    assert 'entrypoint: [""]' in recipe
    assert "before_script: []" in recipe and "after_script: []" in recipe
    assert "--phase branch-guard" in recipe
    assert "--phase bootstrap" in recipe
    assert "--baseline-evidence" in recipe
    assert "CI_MERGE_REQUEST_EVENT_TYPE" in recipe and "merged_result" in recipe
    assert "CI_MERGE_REQUEST_TARGET_BRANCH_SHA" in recipe
    assert "validator_root=/opt/addx/cicd-developer/validators" in recipe
    assert "$gate_tmp/base/scripts/ci" not in recipe
    assert 'dormant_validator="scripts/ci/' not in recipe
    assert 'evidence_validator="scripts/ci/' not in recipe
    assert '--baseline-evidence "$gate_tmp/baseline/{{legacy_handoff_evidence_path}}"' in recipe
    assert '--evidence-file "$gate_tmp/candidate/{{legacy_handoff_evidence_path}}"' in recipe
    assert "--changed-paths-file" in recipe
    assert '--evidence-path "{{legacy_handoff_evidence_path}}"' in recipe
    assert '--allowed-transition-prefix "{{target_overlay}}/"' in recipe
    assert "--phase kustomize-closure" in recipe
    assert "ls-files -z" in recipe
    assert recipe.index("--phase kustomize-closure") < recipe.index(
        '"$kubectl_bin" kustomize'
    )
    assert "--load-restrictor=LoadRestrictionsRootOnly" in recipe
    assert "--enable-helm" not in recipe
    assert "python_bin=/usr/bin/python3" in recipe and '"$python_bin" -I -c' in recipe
    assert "run_validator" in recipe and "validator_launcher=" in recipe
    assert "unset PYTHONPATH PYTHONHOME" in recipe
    assert "BASH_ENV ENV" in recipe and "CI_JOB_TOKEN" in recipe
    assert 'worktree add --detach "$gate_tmp/candidate" "$candidate_sha"' in recipe
    assert 'worktree add --detach "$gate_tmp/baseline" "$baseline_sha"' in recipe
    assert "stage: .pipeline-policy-pre" in recipe
    assert "diff --no-renames --name-only -z" in recipe
    assert "rev-parse --path-format=absolute --git-common-dir" in recipe
    assert "rev-parse --absolute-git-dir" not in recipe
    assert '--repository-root "$gate_tmp/candidate"' in recipe
    assert '--repository-root "$CI_PROJECT_DIR"' not in recipe
    assert "pipeline execution policy" in workflow
    assert "STOP and emit an Ops Todo" in workflow


def test_ce_guarded_recipe_binds_basic_protected_staging_pipeline() -> None:
    recipe = (
        SKILL_ROOT / "recipes/ci/gitlab-ci-legacy-target-state-gate.yml.tmpl"
    ).read_text(encoding="utf-8")
    workflow = legacy_live_workflow_text()
    assert "CE-guarded legacy-live target state gate" in recipe
    assert "stage: .pre" in recipe
    assert "stage: .pipeline-policy-pre" not in recipe
    assert "merged_result" not in recipe
    assert '" != basic ]' not in recipe
    assert "basic|detached" in recipe
    assert 'case "${CI_MERGE_REQUEST_EVENT_TYPE:-}" in' in recipe
    assert '${CI_MERGE_REQUEST_TARGET_BRANCH_SHA:-${CI_MERGE_REQUEST_DIFF_BASE_SHA:-}}' in recipe
    assert '$CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "staging"' in recipe
    assert '$CI_COMMIT_BRANCH == "staging"' in recipe
    assert 'name: "{{legacy_lock_validation_image}}@sha256:' in recipe
    assert "inherit:" in recipe and "default: false" in recipe
    assert "variables: false" in recipe
    assert 'entrypoint: [""]' in recipe
    assert "before_script: []" in recipe and "after_script: []" in recipe
    assert "--phase branch-guard" in recipe
    assert "--phase bootstrap" in recipe
    assert "--baseline-evidence" in recipe
    assert "--phase kustomize-closure" in recipe
    assert "--load-restrictor=LoadRestrictionsRootOnly" in recipe
    assert "validator_root=/opt/addx/cicd-developer/validators" in recipe
    assert '"$python_bin" -I -c' in recipe
    assert "unset PYTHONPATH PYTHONHOME" in recipe
    assert "CI_JOB_TOKEN" in recipe
    assert "--repository-root \"$gate_tmp/candidate\"" in recipe
    assert "--repository-root \"$CI_PROJECT_DIR\"" not in recipe
    assert "worktree add --detach \"$gate_tmp/baseline\"" in recipe


def test_workflow_ce_guarded_mode_requires_authorization_and_ce_controls() -> None:
    workflow = " ".join(legacy_live_workflow_text().lower().split())
    for contract in (
        "ce-guarded degraded enforcement",
        "enterprise: false",
        "push access to `no one`",
        "only_allow_merge_if_pipeline_succeeds",
        "ce_guarded_authorization",
        "shadowable",
        "merged-result",
        "toctou",
        "replacement ops todo",
        "allow_failure",
    ):
        assert contract in workflow
    assert "candidate ci is not a fallback" in workflow


def test_policy_no_renames_exposes_both_sides_of_a_moved_forbidden_path(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    (repository / "Jenkinsfile").write_text("pipeline {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "Jenkinsfile"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ],
        cwd=repository,
        check=True,
    )
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    target = repository / "k8s/overlays/staging-eu"
    target.mkdir(parents=True)
    subprocess.run(
        ["git", "mv", "Jenkinsfile", "k8s/overlays/staging-eu/moved.yaml"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "candidate",
        ],
        cwd=repository,
        check=True,
    )
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    changed = subprocess.run(
        [
            "git",
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            baseline,
            candidate,
            "--",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout.rstrip(b"\0").split(b"\0")
    assert changed == [b"Jenkinsfile", b"k8s/overlays/staging-eu/moved.yaml"]


def test_policy_common_git_dir_materializes_a_linked_worktree_commit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    linked = tmp_path / "linked"
    policy_repository = tmp_path / "policy-repository"
    policy_checkout = tmp_path / "policy-checkout"
    source.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=source, check=True)
    (source / "tracked.txt").write_text("trusted\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "--detach", str(linked), "HEAD"],
        cwd=source,
        check=True,
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=linked,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    common_dir = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=linked,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "init", "--quiet", str(policy_repository)], check=True)
    environment = os.environ.copy()
    environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(
        Path(common_dir) / "objects"
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(policy_repository),
            "worktree",
            "add",
            "--detach",
            str(policy_checkout),
            revision,
        ],
        check=True,
        env=environment,
        capture_output=True,
    )
    assert (policy_checkout / "tracked.txt").read_text(encoding="utf-8") == "trusted\n"


def test_isolated_policy_launcher_imports_image_owned_sibling_modules() -> None:
    launcher = (
        "import runpy,sys; "
        f"root={str(VALIDATOR_ROOT)!r}; "
        "sys.path.insert(0,root); "
        "script=sys.argv.pop(1); "
        'runpy.run_path(script,run_name="__main__")'
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            launcher,
            str(VALIDATOR_ROOT / "check_legacy_target_application.py"),
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def legacy_application() -> dict[str, object]:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": "webhook-staging-eu",
            "namespace": "argo-cd",
            "labels": {"app": "webhook", "env": "staging-eu"},
        },
        "spec": {
            "source": {
                "repoURL": "https://gitlab.addx.ai/PAAS/webhook.git",
                "targetRevision": "staging",
                "path": "k8s/overlays/staging-eu",
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": "staging-webhook",
            },
        },
    }


def test_legacy_application_validator_binds_exact_staging_identity() -> None:
    expected = {
        "app": "webhook",
        "application_name": "webhook-staging-eu",
        "expected_repo": "https://gitlab.addx.ai/PAAS/webhook.git",
        "expected_path": "k8s/overlays/staging-eu",
        "expected_namespace": "staging-webhook",
    }
    assert APPLICATION.validate_application(legacy_application(), **expected) == []
    for field, value in (
        ("targetRevision", "main"),
        ("repoURL", "https://gitlab.addx.ai/PAAS/other.git"),
        ("path", "k8s/overlays/prod-eu"),
    ):
        document = legacy_application()
        document["spec"]["source"][field] = value  # type: ignore[index]
        assert APPLICATION.validate_application(document, **expected)
    document = legacy_application()
    document["spec"]["destination"]["namespace"] = "prod-webhook"  # type: ignore[index]
    assert APPLICATION.validate_application(document, **expected)


def test_legacy_live_workflow_stops_prod_before_writes() -> None:
    workflow = (
        (SKILL_ROOT / "workflows/add-target-cluster.md").read_text(encoding="utf-8")
        + "\n"
        + legacy_live_workflow_text()
    )
    normalized = " ".join(workflow.split())
    assert "`legacy-live` is staging-only" in normalized
    assert "$source_branch=staging" in normalized
    assert "$target_branch=staging" in normalized
    assert "prod self-check to bypass this STOP" in normalized
    assert "prod target in `gitops` mode only" in normalized
    assert "A staging invocation contains no prod destination" in normalized
    assert "prod `gitops` invocation contains only the exact target" in normalized
    assert "Keep target always dormant and digest-pinned" in normalized
    assert "In `gitops`, include the target Harbor image-list" in normalized


def test_four_historical_add_target_route_keywords_remain_asserted() -> None:
    table = yaml.safe_load(
        (SKILL_ROOT / "references/data/routes-build.yaml").read_text(encoding="utf-8")
    )
    route = next(
        row
        for row in table["build_intents"]
        if row["workflow_file"] == "workflows/add-target-cluster.md"
    )
    for keyword in (
        "add target cluster",
        "deploy to new region",
        "expand cluster",
        "new env target",
    ):
        assert keyword in route["keywords"]


def test_cicd_ci_has_deterministic_critical_legacy_validator_coverage_gate() -> None:
    ci = (REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    for validator in (
        "_legacy_target_common.py",
        "check_legacy_target_application.py",
        "check_legacy_target_handoff_evidence.py",
        "check_dormant_target.py",
        "check_legacy_target_live_empty.py",
        "check_legacy_reference_provenance.py",
    ):
        assert validator in ci
    assert "coverage report" in ci
    assert "--show-missing" in ci
    assert "--fail-under=" in ci
    assert "python-coverage-cicd-developer.json" in ci
