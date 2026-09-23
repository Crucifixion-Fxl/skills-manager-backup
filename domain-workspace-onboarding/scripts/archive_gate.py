#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml>=6.0"]
# ///
"""Trusted-snapshot and raw-evidence checks for ARCHIVE candidates."""

# Library module; gate.py provides argparse --help and json.dumps structured output.

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from common import (
    governance_data,
    load_yaml,
    materialize_git_commit,
    nonempty,
    trusted_validator_env,
    validate_raw_observation,
    verify_standards_checkout,
)
from validation_gate import workspace_git_checks


def _is_full_sha(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 40 and all(character in "0123456789abcdef" for character in text)


def _archive_lifecycle(
    evidence: dict[str, Any], errors: list[str]
) -> tuple[Any, str]:
    phase = evidence.get("phase")
    if phase not in {"preflight", "post-transition"}:
        errors.append("archive phase must be preflight or post-transition")
    expected_lifecycle = "archived" if phase == "post-transition" else "deprecated"
    if (
        evidence.get("current_lifecycle") != expected_lifecycle
        or evidence.get("target_lifecycle") != "archived"
    ):
        errors.append(
            f"ARCHIVE {phase or 'unknown'} requires current_lifecycle={expected_lifecycle} "
            "and target_lifecycle=archived"
        )
    return phase, expected_lifecycle


def _archive_catalog_row(
    catalog: dict[str, Any], workspace: str, expected_lifecycle: str, phase: Any, errors: list[str]
) -> dict[str, Any] | None:
    catalog_rows = catalog.get("workspaces")
    if not isinstance(catalog_rows, list):
        errors.append("central catalog workspaces must be an array")
        catalog_rows = []
    catalog_row = next(
        (
            row
            for row in catalog_rows
            if isinstance(row, dict) and row.get("name") == workspace
        ),
        None,
    )
    if not catalog_row:
        errors.append("workspace is not present in the central catalog")
    elif catalog_row.get("lifecycle") != expected_lifecycle:
        errors.append(
            f"central catalog workspace must be {expected_lifecycle} for {phase}"
        )
    return catalog_row


def _validate_consumer_migrations(
    evidence: dict[str, Any], workspace: str, errors: list[str]
) -> None:
    migrations = evidence.get("consumer_migrations")
    if not isinstance(migrations, list) or not migrations:
        errors.append("consumer_migrations must be non-empty")
        migrations = []
    for index, row in enumerate(migrations):
        if not isinstance(row, dict):
            errors.append(f"consumer_migrations[{index}] must be a map")
            continue
        if not nonempty(row.get("consumer")):
            errors.append(f"consumer_migrations[{index}] is incomplete")
        validate_raw_observation(
            f"consumer_migrations[{index}]",
            row,
            errors,
            "migrated",
            f"consumer-migrated:{workspace}:{row.get('consumer', '')}",
        )


def _validate_archive_pointers(
    evidence: dict[str, Any], workspace: str, errors: list[str]
) -> None:
    for name in ("redirect", "source_pointer"):
        record = evidence.get(name)
        target = record.get("target", "") if isinstance(record, dict) else ""
        if not isinstance(record, dict) or not nonempty(target):
            errors.append(f"{name} must be ready with target and evidence")
        validate_raw_observation(
            name,
            record,
            errors,
            "ready",
            f"{name.replace('_', '-')}-ready:{workspace}:{target}",
        )


def _validate_archive_observations(
    evidence: dict[str, Any], workspace: str, phase: Any, errors: list[str]
) -> None:
    catalog_status = "complete" if phase == "post-transition" else "reviewed"
    validate_raw_observation(
        "catalog_update",
        evidence.get("catalog_update"),
        errors,
        catalog_status,
        f"catalog-transition-{catalog_status}:{workspace}:archived",
    )
    if not _is_full_sha(evidence.get("archive_commit")):
        errors.append("archive_commit must be a full SHA")
    read_only_status = "complete" if phase == "post-transition" else "reviewed"
    validate_raw_observation(
        "repository_read_only",
        evidence.get("repository_read_only"),
        errors,
        read_only_status,
        f"repository-read-only-{read_only_status}:{workspace}",
    )
    if phase == "post-transition":
        validate_raw_observation(
            "transition_approval",
            evidence.get("transition_approval"),
            errors,
            "approved",
            f"archive-transition-approved:{workspace}:{evidence.get('archive_commit', '')}",
        )


def _validate_declared_archive_git(evidence: dict[str, Any], errors: list[str]) -> None:
    git = evidence.get("git") or {}
    if not isinstance(git, dict):
        errors.append("archive git evidence must be a map")
        git = {}
    if git.get("clean") is not True:
        errors.append("archive Git working tree must be clean")
    if not _is_full_sha(git.get("head_sha")) or git.get("head_sha") != git.get(
        "remote_head_sha"
    ):
        errors.append("archive Git local/remote heads must be equal full SHAs")


def archive_requirements(
    evidence: dict[str, Any], catalog: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if evidence.get("schema_version") != 1:
        errors.append("archive evidence schema_version must be 1")
    phase, expected_lifecycle = _archive_lifecycle(evidence, errors)
    workspace = str(evidence.get("workspace") or "")
    if not workspace:
        errors.append("archive workspace is required")
    catalog_row = _archive_catalog_row(
        catalog, workspace, expected_lifecycle, phase, errors
    )
    validate_raw_observation(
        "owner",
        evidence.get("owner"),
        errors,
        "confirmed",
        f"workspace-owner-confirmed:{workspace}",
    )
    _validate_consumer_migrations(evidence, workspace, errors)
    _validate_archive_pointers(evidence, workspace, errors)
    _validate_archive_observations(evidence, workspace, phase, errors)
    _validate_declared_archive_git(evidence, errors)
    return errors, {
        "phase": phase,
        "expected_lifecycle": expected_lifecycle,
        "catalog_lifecycle": catalog_row.get("lifecycle") if catalog_row else None,
        "catalog_repository": catalog_row.get("repository") if catalog_row else None,
        "catalog_archive": catalog_row.get("archive") if catalog_row else None,
    }


def archive_gate(
    standards_root: Path,
    workspace_root: Path,
    evidence_path: Path,
) -> tuple[list[str], dict[str, Any]]:
    verification, errors = verify_standards_checkout(
        standards_root, Path(__file__).with_name("verify_standards.py")
    )
    details: dict[str, Any] = {"standards_verification": verification}
    if errors:
        return errors, details
    try:
        with materialize_git_commit(standards_root, str(verification["local_head"])) as snapshot:
            catalog, _ = governance_data(snapshot)
            evidence = load_yaml(evidence_path)
            archive_errors, archive_details = archive_requirements(evidence, catalog)
            result = subprocess.run(
                [str(snapshot / "scripts/workspace-validate"), str(workspace_root)],
                check=False,
                capture_output=True,
                text=True,
                env=trusted_validator_env(),
            )
            governance_result = subprocess.run(
                [str(snapshot / "scripts/governance-validate")],
                cwd=snapshot,
                check=False,
                capture_output=True,
                text=True,
                env=trusted_validator_env(),
            )
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors.append(f"cannot validate archive candidate from standards snapshot: {exc}")
        return errors, details
    errors.extend(archive_errors)
    details.update(archive_details)
    details.update(
        {
            "official_validator_source_sha": str(verification["local_head"]),
            "official_workspace_validator_exit": result.returncode,
            "official_workspace_validator_stdout": result.stdout.strip(),
            "official_workspace_validator_stderr": result.stderr.strip(),
            "official_governance_validator_exit": governance_result.returncode,
            "official_governance_validator_stdout": governance_result.stdout.strip(),
            "official_governance_validator_stderr": governance_result.stderr.strip(),
        }
    )
    if result.returncode:
        errors.append("official standards workspace validator failed")
    if governance_result.returncode:
        errors.append("official standards governance validator failed")
    catalog_repository = archive_details.get("catalog_repository")
    git_errors, git_details = workspace_git_checks(
        workspace_root,
        evidence.get("git") or {},
        str(catalog_repository) if catalog_repository else None,
    )
    errors.extend(git_errors)
    details["actual_git"] = git_details
    workspace_manifest = load_yaml(workspace_root / "workspace.yaml")
    if evidence.get("workspace") != workspace_manifest.get("workspace"):
        errors.append("archive evidence workspace does not match actual workspace manifest")
    expected_lifecycle = archive_details.get("expected_lifecycle")
    if workspace_manifest.get("lifecycle") != expected_lifecycle:
        errors.append(
            f"actual workspace lifecycle must be {expected_lifecycle} for {evidence.get('phase')}"
        )
    if evidence.get("archive_commit") != git_details.get("head_sha"):
        errors.append("archive_commit does not match actual workspace HEAD")
    if evidence.get("phase") == "post-transition":
        catalog_archive = archive_details.get("catalog_archive") or {}
        if not isinstance(catalog_archive, dict):
            errors.append("central catalog archive record must be a map")
            catalog_archive = {}
        if catalog_archive.get("archive_commit") != git_details.get("head_sha"):
            errors.append("catalog archive.archive_commit does not match actual workspace HEAD")
        redirect = evidence.get("redirect") or {}
        if not isinstance(redirect, dict):
            redirect = {}
        if catalog_archive.get("redirect") != redirect.get("target"):
            errors.append("catalog archive.redirect does not match verified redirect target")
    return errors, details
