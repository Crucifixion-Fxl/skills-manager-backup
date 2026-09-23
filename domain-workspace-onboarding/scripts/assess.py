#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml>=6.0"]
# ///
"""Read-only workspace admission assessment against the central catalog and registry."""

# Structured output is JSON produced with json.dumps by common.emit.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import (
    boundary_signature,
    canonical_git_url,
    emit,
    governance_data,
    load_yaml,
    materialize_git_commit,
    verify_standards_checkout,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Return one four-way workspace recommendation with catalog and repository overlap evidence."
    )
    parser.add_argument("--standards-root", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    return parser.parse_args()


def decide(request: dict[str, Any], duplicate: bool) -> tuple[str, list[str]]:
    scope = request.get("scope") or {}
    reasons: list[str] = []
    if scope.get("reusable_standard") is True:
        return "standards", ["scope is reusable across workspaces and is not business-specific topology"]
    if scope.get("single_source_owned") is True or scope.get("temporary_project") is True:
        return "source-repository-docs", ["durable residue belongs beside source; transient material is excluded"]
    if duplicate:
        return "extend-existing", ["candidate name, key, or complete boundary signature duplicates the catalog"]
    if scope.get("durable") is not True or scope.get("multi_repository") is not True:
        return "source-repository-docs", ["a durable cross-repository governance boundary is not evidenced"]
    if scope.get("independent_roadmap") is not True:
        return "extend-existing", ["no independent roadmap or lifecycle distinguishes a new workspace"]
    reasons.append("durable, independently roadmapped, cross-repository scope is evidenced")
    return "new-workspace", reasons


def _validate_boundary(request: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    boundary = request.get("boundary") or {}
    if not isinstance(boundary, dict):
        errors.append("boundary must be a map")
        boundary = {}
    if (
        not str(boundary.get("summary") or "").strip()
        or not isinstance(boundary.get("includes"), list)
        or not boundary.get("includes")
        or not isinstance(boundary.get("excludes"), list)
        or not boundary.get("excludes")
    ):
        errors.append("boundary summary, includes, and excludes are required for catalog comparison")
    return boundary


def _repository_identities(
    request: dict[str, Any], errors: list[str]
) -> tuple[list[Any], dict[str, str], dict[str, str]]:
    repositories = request.get("repositories") or []
    if not isinstance(repositories, list):
        errors.append("repositories must be an array")
        repositories = []
    canonical_repositories: dict[str, str] = {}
    repository_identities: dict[str, str] = {}
    for repository in repositories:
        try:
            identity = canonical_git_url(str(repository))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if identity in canonical_repositories:
            errors.append(f"duplicate equivalent repository URL: {repository}")
        canonical_repositories[identity] = str(repository)
        repository_identities[str(repository)] = identity
    return repositories, canonical_repositories, repository_identities


def _validate_candidate_boundaries(
    request: dict[str, Any], errors: list[str]
) -> list[dict[str, Any]]:
    candidates = request.get("candidate_boundaries") or []
    if not isinstance(candidates, list):
        errors.append("candidate_boundaries must be an array")
        candidates = []
    if not candidates:
        errors.append("candidate_boundaries must be non-empty")
    allowed_types = {
        "stable-business-domain",
        "shared-platform-domain",
        "enablement-domain",
    }
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or not str(candidate.get("label") or "").strip():
            errors.append(f"candidate_boundaries[{index}].label is required")
        elif candidate.get("domain_type_candidate") not in allowed_types:
            errors.append(f"candidate_boundaries[{index}].domain_type_candidate is invalid")
    return candidates


def _validate_scope(
    request: dict[str, Any], repository_count: int, errors: list[str]
) -> dict[str, Any]:
    scope = request.get("scope") or {}
    for field in ("durable", "multi_repository", "independent_roadmap"):
        if not isinstance(scope.get(field), bool):
            errors.append(f"scope.{field} must be boolean")
    if scope.get("multi_repository") is True and repository_count < 2:
        errors.append("multi-repository scope requires at least two distinct repository URLs")
    return scope


def _registry_by_repository(registry: dict[str, Any]) -> dict[str, list[Any]]:
    registry_by_repo = {}
    for row in registry.get("repositories") or []:
        if not isinstance(row, dict):
            continue
        try:
            identity = canonical_git_url(str(row.get("repo") or ""))
        except ValueError:
            continue
        registry_by_repo[identity] = row.get("references") or []
    return registry_by_repo


def _repository_overlap_evidence(
    repositories: list[Any],
    repository_identities: dict[str, str],
    registry_by_repo: dict[str, list[Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    overlaps = []
    overlap_workspaces: set[str] = set()
    for repo in repositories:
        identity = repository_identities.get(str(repo), "")
        refs = registry_by_repo.get(identity, [])
        names = sorted({str(ref.get("workspace")) for ref in refs if ref.get("workspace")})
        overlap_workspaces.update(names)
        overlaps.append(
            {
                "repo": repo,
                "existing_workspaces": names,
                "references": refs,
                "overlap": bool(refs),
            }
        )
    return overlaps, overlap_workspaces


def _catalog_comparisons(
    catalog: dict[str, Any],
    boundary: dict[str, Any],
    repositories: list[Any],
    repository_identities: dict[str, str],
    registry_by_repo: dict[str, list[Any]],
) -> tuple[list[dict[str, Any]], bool]:
    proposed_name = boundary.get("proposed_name")
    proposed_key = boundary.get("key")
    proposed_signature = boundary_signature(boundary)
    comparisons = []
    duplicate = False
    for workspace in catalog.get("workspaces") or []:
        if workspace.get("lifecycle") == "archived":
            continue
        existing_boundary = workspace.get("boundary") or {}
        exact = {
            "name": bool(proposed_name and proposed_name == workspace.get("name")),
            "boundary_key": bool(proposed_key and proposed_key == existing_boundary.get("key")),
            "boundary_signature": bool(
                proposed_signature and proposed_signature == boundary_signature(existing_boundary)
            ),
        }
        is_duplicate = any(exact.values())
        duplicate = duplicate or is_duplicate
        shared_repos = sorted(
            repo
            for repo in repositories
            if workspace.get("name") in {
                str(ref.get("workspace"))
                for ref in registry_by_repo.get(repository_identities.get(str(repo), ""), [])
            }
        )
        relationship = "none"
        if is_duplicate:
            relationship = "duplicate"
        elif shared_repos:
            relationship = "partial"
        comparisons.append(
            {
                "workspace": workspace.get("name"),
                "lifecycle": workspace.get("lifecycle"),
                "domain_type": workspace.get("domain_type"),
                "exact_matches": exact,
                "shared_repositories": shared_repos,
                "relationship": relationship,
            }
        )
    return comparisons, duplicate


def _unresolved_items(
    scope: dict[str, Any], boundary: dict[str, Any], candidates: list[dict[str, Any]]
) -> list[str]:
    unresolved = ["proposal approval and current GitLab creation permission"]
    if scope.get("owner_status") != "confirmed":
        unresolved.append("confirmed governance Owner evidence")
    if not boundary.get("proposed_name"):
        unresolved.append("final workspace English name")
    if not boundary.get("key"):
        unresolved.append("final unique boundary key")
    for candidate in candidates:
        if not candidate.get("final_name") or not candidate.get("boundary_key"):
            unresolved.append(f"final name and boundary for candidate: {candidate.get('label', '<unnamed>')}")
    return sorted(set(unresolved))


def _verified_governance(
    standards_root: Path,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    verification, verification_errors = verify_standards_checkout(
        standards_root, Path(__file__).with_name("verify_standards.py")
    )
    if verification_errors:
        emit(
            {
                "assessment_valid": False,
                "creation_authorized": False,
                "errors": verification_errors,
                "standards_verification": verification,
            },
            1,
        )
    verified_sha = str(verification["local_head"])
    with materialize_git_commit(standards_root, verified_sha) as snapshot:
        catalog, registry = governance_data(snapshot)
    return verified_sha, verification, catalog, registry


def main() -> None:
    args = parse_args()
    request = load_yaml(args.request.resolve())
    verified_sha, verification, catalog, registry = _verified_governance(
        args.standards_root.resolve()
    )
    errors: list[str] = []
    if request.get("schema_version") != 1:
        errors.append("request schema_version must be 1")
    if not str(request.get("assessment_id") or "").strip():
        errors.append("assessment_id is required")
    boundary = _validate_boundary(request, errors)
    repositories, canonical_repositories, repository_identities = _repository_identities(
        request, errors
    )
    candidates = _validate_candidate_boundaries(request, errors)
    scope = _validate_scope(request, len(canonical_repositories), errors)
    registry_by_repo = _registry_by_repository(registry)
    overlaps, overlap_workspaces = _repository_overlap_evidence(
        repositories, repository_identities, registry_by_repo
    )
    comparisons, duplicate = _catalog_comparisons(
        catalog, boundary, repositories, repository_identities, registry_by_repo
    )
    recommendation, reasons = decide(request, duplicate)
    excluded = request.get("excluded_materials") or []
    if excluded:
        reasons.append(f"{len(excluded)} local or transient material item(s) remain excluded")
    if errors:
        emit({"assessment_valid": False, "creation_authorized": False, "errors": errors}, 1)
    emit(
        {
            "assessment_id": request["assessment_id"],
            "assessment_valid": True,
            "recommendation": recommendation,
            "recommendation_reasons": reasons,
            "creation_authorized": False,
            "slug_or_boundary_guessed": False,
            "catalog_comparisons": comparisons,
            "repository_overlaps": overlaps,
            "overlap_workspaces": sorted(overlap_workspaces),
            "candidate_boundaries": candidates,
            "unresolved": _unresolved_items(scope, boundary, candidates),
            "evidence": {
                "standards_sha": verified_sha,
                "catalog": f"{verified_sha}:catalog/workspaces.yaml",
                "registry": f"{verified_sha}:catalog/repository-references.yaml",
            },
            "standards_verification": verification,
        }
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        emit({"assessment_valid": False, "creation_authorized": False, "errors": [str(exc)]}, 1)
