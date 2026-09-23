#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml>=6.0"]
# ///
"""Pure and trusted-snapshot checks for INIT and ARCHIVE lifecycle modes."""

# Library module; gate.py provides argparse --help and json.dumps structured output.

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from common import (
    boundary_signature,
    canonical_git_url,
    governance_data,
    load_yaml,
    materialize_git_commit,
    nonempty,
    trusted_validator_env,
    verify_standards_checkout,
)


def _auditable_https(value: Any) -> bool:
    parsed = urlsplit(str(value or ""))
    return parsed.scheme == "https" and bool(parsed.netloc) and parsed.username is None


def _is_placeholder(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return bool(re.search(r"(^|[/_.:\-\s])examples?([/_.:\-\s]|$)", normalized))


def proposal_identity_placeholders(proposal: dict[str, Any]) -> list[str]:
    workspace = proposal.get("workspace") or {}
    boundary = workspace.get("boundary") or {}
    owner = workspace.get("governance_owner") or {}
    decision = proposal.get("decision") or {}
    approval = decision.get("approval") or {}
    creation = decision.get("gitlab_creation") or {}
    fields = {
        "proposal_id": proposal.get("proposal_id"),
        "workspace.name": workspace.get("name"),
        "workspace.display_name": workspace.get("display_name"),
        "workspace.boundary.key": boundary.get("key"),
        "governance_owner.name": owner.get("name"),
        "governance_owner.gitlab_username": owner.get("gitlab_username"),
        "approval.approved_by": approval.get("approved_by"),
        "approval.reference": approval.get("reference"),
        "gitlab_creation.project_path": creation.get("project_path"),
        "gitlab_creation.creator": creation.get("creator"),
    }
    write_scopes = (
        ((proposal.get("migration") or {}).get("independent_owner_agent") or {}).get(
            "write_scope"
        )
        or []
    )
    for index, value in enumerate(write_scopes):
        fields[f"migration.write_scope[{index}]"] = value
    return [name for name, value in fields.items() if _is_placeholder(value)]


def _decision_and_owner_errors(
    proposal: dict[str, Any], errors: list[str]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    decision = proposal.get("decision") or {}
    approval = decision.get("approval") or {}
    workspace = proposal.get("workspace") or {}
    owner = workspace.get("governance_owner") or {}
    if decision.get("outcome") != "new-workspace":
        errors.append("INIT requires decision.outcome=new-workspace")
    if (
        approval.get("status") != "approved"
        or not nonempty(approval.get("approved_by"))
        or not nonempty(approval.get("reference"))
    ):
        errors.append("INIT requires approved decision evidence")
    if owner.get("status") != "confirmed" or not all(
        nonempty(owner.get(key)) for key in ("name", "gitlab_username", "evidence")
    ):
        errors.append("INIT requires a confirmed governance Owner and evidence")
    elif not _auditable_https(owner.get("evidence")):
        errors.append("INIT governance Owner evidence must be an auditable HTTPS URL")
    return decision, approval, workspace, owner


def _identity_and_creation_errors(
    proposal: dict[str, Any],
    decision: dict[str, Any],
    approval: dict[str, Any],
    owner: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    placeholders = proposal_identity_placeholders(proposal)
    if placeholders:
        errors.append(f"INIT rejects standards template placeholders: {', '.join(placeholders)}")
    if not _auditable_https(approval.get("reference")):
        errors.append("INIT approval reference must be an auditable HTTPS URL")
    creation = decision.get("gitlab_creation") or {}
    if (
        creation.get("creator_role") not in {"group-owner", "explicit-project-creation-delegate"}
        or creation.get("creator") != owner.get("gitlab_username")
        or str(creation.get("verified_at") or "") != date.today().isoformat()
        or not _auditable_https(creation.get("evidence"))
    ):
        errors.append(
            "INIT requires same-day auditable GitLab creation permission for the governance Owner"
        )
    return creation


def _catalog_uniqueness_errors(
    workspace: dict[str, Any], creation: dict[str, Any], catalog_rows: list[Any], errors: list[str]
) -> None:
    boundary = workspace.get("boundary") or {}
    if any(row.get("name") == workspace.get("name") for row in catalog_rows):
        errors.append("workspace name is already cataloged")
    namespace = creation.get("namespace") or "domain-workspaces"
    project_path = creation.get("project_path") or workspace.get("name")
    repository_suffix = f"/{namespace}/{project_path}"
    catalog_repository_identities = []
    for row in catalog_rows:
        try:
            catalog_repository_identities.append(canonical_git_url(str(row.get("repository") or "")))
        except ValueError:
            continue
    if any(identity.endswith(repository_suffix) for identity in catalog_repository_identities):
        errors.append("workspace repository is already cataloged")
    if any(row.get("boundary", {}).get("key") == boundary.get("key") for row in catalog_rows):
        errors.append("workspace boundary key is already cataloged")
    signature = boundary_signature(boundary)
    if signature and any(
        boundary_signature(row.get("boundary") or {}) == signature for row in catalog_rows
    ):
        errors.append("workspace boundary signature duplicates the catalog")


def _proposal_repositories(proposal: dict[str, Any], errors: list[str]) -> list[dict[str, Any]]:
    repositories = proposal.get("repositories") or []
    if not repositories or any(
        repo.get("role") not in {"primary", "dependency", "related"}
        or not nonempty(repo.get("reason"))
        or not nonempty(repo.get("boundary"))
        for repo in repositories
    ):
        errors.append("INIT requires repository role, reason, and boundary evidence")
    return repositories


def _registry_index(registry: dict[str, Any]) -> dict[str, list[str]]:
    registry_by_repo: dict[str, list[str]] = {}
    for row in registry.get("repositories") or []:
        try:
            key = canonical_git_url(str(row.get("repo") or ""))
        except ValueError:
            continue
        registry_by_repo[key] = sorted(
            str(ref.get("workspace"))
            for ref in row.get("references") or []
            if ref.get("workspace")
        )
    return registry_by_repo


def _overlapping_repositories(
    repositories: list[dict[str, Any]], registry_by_repo: dict[str, list[str]], errors: list[str]
) -> dict[str, list[str]]:
    overlaps: dict[str, list[str]] = {}
    for repo in repositories:
        remote = str(repo.get("repo") or "")
        try:
            key = canonical_git_url(remote)
        except ValueError:
            errors.append(f"invalid repository URL: {remote}")
            continue
        if key in registry_by_repo:
            overlaps[key] = registry_by_repo[key]
    return overlaps


def _reuse_proof_rows(
    proposal: dict[str, Any], errors: list[str]
) -> dict[str, dict[str, Any]]:
    reuse_rows: dict[str, dict[str, Any]] = {}
    for row in proposal.get("repository_reuse") or []:
        try:
            key = canonical_git_url(str(row.get("repo") or ""))
        except ValueError:
            errors.append(f"invalid repository reuse URL: {row.get('repo')}")
            continue
        if key in reuse_rows:
            errors.append(f"duplicate repository reuse proof: {key}")
        reuse_rows[key] = row
    return reuse_rows


def _validate_reuse_proof(
    reuse_rows: dict[str, dict[str, Any]], overlaps: dict[str, list[str]], errors: list[str]
) -> None:
    if set(reuse_rows) != set(overlaps):
        errors.append("INIT reuse proof must cover exactly every overlapping repository")
    for key, expected_workspaces in overlaps.items():
        row = reuse_rows.get(key) or {}
        if (
            sorted(row.get("existing_workspaces") or []) != expected_workspaces
            or not nonempty(row.get("reuse_reason"))
            or not nonempty(row.get("boundary"))
        ):
            errors.append(f"INIT reuse proof is incomplete for {key}")


def init_requirements(
    proposal: dict[str, Any],
    catalog: dict[str, Any],
    registry: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    decision, approval, workspace, owner = _decision_and_owner_errors(proposal, errors)
    creation = _identity_and_creation_errors(
        proposal, decision, approval, owner, errors
    )
    catalog_rows = catalog.get("workspaces") or []
    _catalog_uniqueness_errors(workspace, creation, catalog_rows, errors)
    repositories = _proposal_repositories(proposal, errors)
    overlaps = _overlapping_repositories(repositories, _registry_index(registry), errors)
    _validate_reuse_proof(_reuse_proof_rows(proposal, errors), overlaps, errors)
    return errors, {"overlapping_repositories": overlaps}


def init_gate(standards_root: Path, proposal_path: Path) -> tuple[list[str], dict[str, Any]]:
    verification, errors = verify_standards_checkout(
        standards_root, Path(__file__).with_name("verify_standards.py")
    )
    details: dict[str, Any] = {"standards_verification": verification}
    if errors:
        details["official_validator_executed"] = False
        return errors, details

    verified_sha = str(verification["local_head"])
    with materialize_git_commit(standards_root, verified_sha) as snapshot:
        proposal = load_yaml(proposal_path)
        catalog, registry = governance_data(snapshot)
        requirement_errors, requirement_details = init_requirements(proposal, catalog, registry)
        errors.extend(requirement_errors)
        if proposal_path.read_bytes() == (snapshot / "templates/workspace-proposal.yaml").read_bytes():
            errors.append("INIT rejects the unchanged standards proposal template")
        validator = snapshot / "scripts/governance-validate"
        result = subprocess.run(
            [str(validator), "--proposal", str(proposal_path)],
            check=False,
            capture_output=True,
            text=True,
            env=trusted_validator_env(),
        )
    if result.returncode:
        errors.append("official standards governance validator failed")
    details.update(requirement_details)
    details.update(
        {
            "official_validator_executed": True,
            "official_validator_source_sha": verified_sha,
            "official_validator_exit": result.returncode,
            "official_validator_stdout": result.stdout.strip(),
            "official_validator_stderr": result.stderr.strip(),
        }
    )
    return errors, details
