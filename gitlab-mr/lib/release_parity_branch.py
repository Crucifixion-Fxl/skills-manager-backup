"""从 GitLab 绑定长期 staging 环境分支的生产晋级。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from urllib.parse import quote

from release_parity_core import InputError, git, resolve_bound_ref, validate_full_sha
from release_parity_gitlab import (
    CANDIDATE_MR,
    CANONICAL_VERIFICATION_MR,
    fetch_ref,
    glab_object,
    mr_sha,
    require_mr,
    required_text,
)
from release_workflow_policy import is_production_target


def validate_acceptance_note(
    note: dict[str, Any],
    *,
    note_id: int,
    verification_mr_iid: int,
    merge_sha: str,
    pipeline_id: int,
    pipeline_url: str,
    merger_id: int,
    merged_at: str,
) -> dict[str, str]:
    """Apply the same provenance contract at initialization and audit."""
    for value, label in (
        (note_id, "acceptance note ID"),
        (verification_mr_iid, "verification MR IID"),
        (pipeline_id, "staging pipeline ID"),
        (merger_id, "staging merger ID"),
    ):
        if type(value) is not int or value <= 0:
            raise InputError(f"{label} must be a positive integer")
    validate_full_sha(merge_sha, "verified staging merge SHA")
    required_text(pipeline_url, "staging pipeline URL")
    required_text(merged_at, "verification MR.merged_at")
    body = required_text(note.get("body"), "staging acceptance note.body")
    marker = (
        f"<!-- staging-acceptance:PASS:v1:{verification_mr_iid}:"
        f"{merge_sha}:{pipeline_id} -->"
    )
    author = note.get("author")
    if not isinstance(author, dict) or author.get("id") != merger_id:
        raise InputError(
            "staging acceptance note must be authored by the staging merger"
        )
    lines = body.splitlines()
    if (
        note.get("id") != note_id
        or note.get("system") is not False
        or note.get("noteable_iid") != verification_mr_iid
        or len(lines) < 2
        or lines[0] != marker
        or not lines[1].startswith("验收通过：")
        or pipeline_url not in body
    ):
        raise InputError(
            "staging acceptance note is not bound to the merge and pipeline"
        )
    note_at = required_text(
        note.get("created_at"), "staging acceptance note.created_at"
    )
    try:
        accepted = datetime.fromisoformat(note_at)
        merged = datetime.fromisoformat(merged_at)
    except ValueError as exc:
        raise InputError("invalid staging merge or acceptance timestamp") from exc
    if accepted.tzinfo is None or merged.tzinfo is None:
        raise InputError(
            "staging merge and acceptance timestamps must include timezones"
        )
    if accepted < merged:
        raise InputError("staging acceptance note predates the verification merge")
    return {
        "acceptance_marker": marker,
        "note_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }


def load_branch_promotion_provenance(
    project_path: str,
    canonical_verification_mr_iid: int,
    candidate_mr_iid: int,
    staging_branch: str,
    staging_pipeline_id: int,
    staging_acceptance_note_id: int,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Bind a long-lived staging branch's verified merge to a production MR.

    This mode deliberately requires the candidate to be the exact staging
    merge commit. A tree-only match would hide unreviewed commits or a stale
    staging branch; a later staging merge requires a newer verification MR.
    """
    project = quote(required_text(project_path, "project_path"), safe="")
    mr_path = f"projects/{project}/merge_requests"
    verification = glab_object(f"{mr_path}/{canonical_verification_mr_iid}")
    candidate = glab_object(f"{mr_path}/{candidate_mr_iid}")
    require_mr(
        verification,
        CANONICAL_VERIFICATION_MR,
        target_branch=staging_branch,
        state="merged",
    )
    require_mr(candidate, CANDIDATE_MR, state="opened")

    target_branch = required_text(
        candidate.get("target_branch"), "candidate MR target_branch"
    )
    if not is_production_target(target_branch):
        raise InputError(
            f"candidate MR target {target_branch!r} is not a production branch"
        )
    verified_merge = validate_full_sha(
        required_text(
            verification.get("merge_commit_sha"),
            "canonical verification MR.merge_commit_sha",
        ),
        "canonical verification MR.merge_commit_sha",
    )
    candidate_sha = mr_sha(candidate, CANDIDATE_MR)
    if candidate_sha != verified_merge:
        raise InputError(
            f"candidate MR SHA {candidate_sha} is not verified staging merge {verified_merge}"
        )

    def branch_sha(name: str) -> str:
        branch = glab_object(
            f"projects/{project}/repository/branches/{quote(name, safe='')}"
        )
        commit = branch.get("commit")
        if not isinstance(commit, dict):
            raise InputError(f"{name} branch commit must be a JSON object")
        return validate_full_sha(
            required_text(commit.get("id"), f"{name} branch commit"),
            f"{name} branch commit",
        )

    staging_sha = branch_sha(staging_branch)
    if staging_sha != verified_merge:
        raise InputError(
            f"staging branch SHA {staging_sha} is not verified merge {verified_merge}"
        )
    target_sha = branch_sha(target_branch)

    if staging_pipeline_id <= 0 or staging_acceptance_note_id <= 0:
        raise InputError(
            "branch promotion requires positive staging pipeline and acceptance note IDs"
        )
    pipeline_path = f"projects/{project}/pipelines/{staging_pipeline_id}"
    pipeline = glab_object(pipeline_path)
    if any(
        pipeline.get(field) != value
        for field, value in (
            ("id", staging_pipeline_id),
            ("sha", verified_merge),
            ("ref", staging_branch),
            ("status", "success"),
        )
    ):
        raise InputError(
            "staging postmerge pipeline is not successful for the verified merge"
        )
    pipeline_url = required_text(pipeline.get("web_url"), "staging pipeline.web_url")
    note_path = (
        f"{mr_path}/{canonical_verification_mr_iid}/notes/{staging_acceptance_note_id}"
    )
    note = glab_object(note_path)
    merger = verification.get("merged_by")
    if not isinstance(merger, dict) or type(merger.get("id")) is not int:
        raise InputError("verification MR merged_by must identify the acceptance owner")
    merge_at = required_text(verification.get("merged_at"), "verification MR.merged_at")
    note_binding = validate_acceptance_note(
        note,
        note_id=staging_acceptance_note_id,
        verification_mr_iid=canonical_verification_mr_iid,
        merge_sha=verified_merge,
        pipeline_id=staging_pipeline_id,
        pipeline_url=pipeline_url,
        merger_id=merger["id"],
        merged_at=merge_at,
    )

    candidate_ref = f"refs/release-parity/mr-{candidate_mr_iid}"
    staging_ref = f"refs/remotes/origin/{staging_branch}"
    target_ref = f"refs/remotes/origin/{target_branch}"
    fetch_ref(f"refs/merge-requests/{candidate_mr_iid}/head", candidate_ref)
    fetch_ref(f"refs/heads/{staging_branch}", staging_ref)
    fetch_ref(f"refs/heads/{target_branch}", target_ref)
    resolve_bound_ref(candidate_ref, candidate_sha, CANDIDATE_MR)
    resolve_bound_ref(staging_ref, staging_sha, "verified staging branch")
    resolve_bound_ref(target_ref, target_sha, "candidate target branch")

    candidate_tree = git(["rev-parse", f"{candidate_sha}^{{tree}}"]).decode().strip()
    merged_tree = (
        git(["merge-tree", "--write-tree", target_sha, candidate_sha]).decode().strip()
    )
    if merged_tree != candidate_tree:
        raise InputError(
            "production merge result differs from the verified staging tree; "
            f"merged={merged_tree}, verified={candidate_tree}"
        )

    refs = {
        "canonical_base": target_sha,
        "canonical": verified_merge,
        "candidate_base": target_sha,
        "candidate": candidate_sha,
    }
    provenance = {
        "mode": "branch-promotion",
        "project_path": project_path,
        "staging_branch": staging_branch,
        "merge_result": {"target_sha": target_sha, "tree_sha": merged_tree},
        "staging_acceptance": {
            "pipeline_id": staging_pipeline_id,
            "pipeline_api_url": pipeline_path,
            "pipeline_url": pipeline_url,
            "note_id": staging_acceptance_note_id,
            "verification_mr_iid": canonical_verification_mr_iid,
            "verification_merged_at": merge_at,
            "acceptance_author_id": merger["id"],
            "note_api_url": note_path,
            "note_url": f"{required_text(verification.get('web_url'), 'verification MR.web_url')}#note_{staging_acceptance_note_id}",
            **note_binding,
        },
        "canonical_verification_mr": {
            "iid": canonical_verification_mr_iid,
            "source_branch": verification["source_branch"],
            "source_head_sha": mr_sha(verification, CANONICAL_VERIFICATION_MR),
            "merge_commit_sha": verified_merge,
        },
        "candidate_mr": {
            "iid": candidate_mr_iid,
            "source_branch": candidate["source_branch"],
            "target_branch": target_branch,
            "head_sha": candidate_sha,
            "target_sha": target_sha,
        },
    }
    return refs, provenance
