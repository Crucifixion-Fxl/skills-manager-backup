"""Recheck branch-present production overlay evidence during MR audit."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from production_overlay_attestation import attest_production_overlay
from release_drive_validation import remote_branch_exists, require_current_git_head
from release_workflow_policy import PolicyError


def validate_non_promotion_overlay_audit(
    state: dict[str, Any],
    release: dict[str, Any],
    promotion: dict[str, Any],
    validate_observation: Callable[..., str],
    require_text: Callable[[Any, str], str],
    audit_error: type[Exception],
) -> None:
    evidence_url = promotion.get("staging_flow_evidence")
    validate_observation(
        release.get("workflow_evidence"),
        expected_name="staging-flow-not-applicable",
        expected_url=evidence_url,
    )
    staging_branch = require_text(
        promotion.get("staging_branch"), "promotion.staging_branch"
    )
    if staging_branch != "staging":
        raise audit_error("production-non-promotion requires staging_branch=staging")
    try:
        branch_present = remote_branch_exists(staging_branch)
    except PolicyError as exc:
        raise audit_error(str(exc)) from exc
    attested = state.get("non_promotion")
    if not branch_present:
        if isinstance(attested, dict) and attested.get("enabled") is True:
            raise audit_error("staging branch disappeared since attestation")
        return

    if not isinstance(attested, dict) or attested.get("enabled") is not True:
        raise audit_error("branch-present non-promotion needs attested state")
    staging_application = attested.get("staging_application")
    if not isinstance(staging_application, dict):
        raise audit_error("attested staging Application evidence is missing")
    snapshot = state.get("last_snapshot")
    if not isinstance(snapshot, dict):
        raise audit_error("state.last_snapshot must be an object")
    head_sha = require_text(snapshot.get("head_sha"), "last_snapshot.head_sha")
    try:
        require_current_git_head(head_sha)
        report, digest = attest_production_overlay(
            SimpleNamespace(
                project_path=state.get("project_path"),
                mr_iid=state.get("mr_iid"),
                branch=state.get("branch"),
                target_branch=state.get("target_branch"),
                staging_branch=staging_branch,
                head_sha=head_sha,
                staging_flow_evidence=evidence_url,
                staging_application_evidence=staging_application.get("url"),
            )
        )
    except PolicyError as exc:
        raise audit_error(str(exc)) from exc
    if (
        digest != attested.get("attestation_sha256")
        or digest != release.get("non_promotion_attestation_sha256")
        or report.get("candidate_mr_sha") != head_sha
    ):
        raise audit_error("production overlay attestation changed")
