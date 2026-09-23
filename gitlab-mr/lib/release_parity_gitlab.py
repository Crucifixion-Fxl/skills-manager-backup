"""从 GitLab 实时读取并绑定生产晋级 ref。"""

from __future__ import annotations

import json
import subprocess
from typing import Any
from urllib.parse import quote, urlencode

from release_parity_core import (
    InputError,
    git,
    require_full_commit_sha,
    resolve_bound_ref,
    validate_full_sha,
)
from release_workflow_policy import is_production_target

CANONICAL_VERIFICATION_MR = "canonical verification MR"
CANDIDATE_MR = "candidate MR"
CANONICAL_ORIGIN_MR = "canonical origin MR"
CANONICAL_ORIGIN_BASE = "canonical origin MR diff_refs.base_sha"


def glab_json(path: str) -> Any:
    result = subprocess.run(
        ["glab", "api", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise InputError(f"glab api {path} failed: {detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InputError(f"glab api {path} returned invalid JSON") from exc
    return value


def glab_object(path: str) -> dict[str, Any]:
    value = glab_json(path)
    if not isinstance(value, dict):
        raise InputError(f"glab api {path} must return a JSON object")
    return value


def required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label} must be a non-empty string")
    return value


def mr_sha(mr: dict[str, Any], label: str) -> str:
    return validate_full_sha(
        required_text(mr.get("sha"), f"{label}.sha"),
        f"{label}.sha",
    )


def require_mr(
    mr: dict[str, Any],
    label: str,
    *,
    target_branch: str | None = None,
    state: str | None = None,
) -> None:
    if target_branch is not None and mr.get("target_branch") != target_branch:
        raise InputError(
            f"{label} target is {mr.get('target_branch')!r}, expected {target_branch!r}"
        )
    if state is not None and mr.get("state") != state:
        raise InputError(f"{label} state is {mr.get('state')!r}, expected {state!r}")
    required_text(mr.get("source_branch"), f"{label}.source_branch")
    mr_sha(mr, label)


def fetch_ref(remote_ref: str, local_ref: str) -> None:
    git(["fetch", "--no-tags", "origin", f"+{remote_ref}:{local_ref}"])


def load_gitlab_provenance(
    project_path: str,
    canonical_verification_mr_iid: int,
    candidate_mr_iid: int,
    staging_branch: str,
) -> tuple[dict[str, str], dict[str, Any]]:
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
    query = urlencode(
        {
            "state": "merged",
            "target_branch": staging_branch,
            "source_branch": verification["source_branch"],
            "order_by": "created_at",
            "sort": "asc",
            "per_page": "100",
        }
    )
    canonical_mrs = glab_json(f"{mr_path}?{query}")
    if not isinstance(canonical_mrs, list) or not canonical_mrs:
        raise InputError("cannot find the first merged staging MR for canonical branch")
    if not all(isinstance(item, dict) for item in canonical_mrs):
        raise InputError("canonical staging MR query returned invalid items")
    origin = canonical_mrs[0]
    canonical_origin_mr_iid = origin.get("iid")
    if type(canonical_origin_mr_iid) is not int:
        raise InputError("canonical origin MR iid must be an integer")
    require_mr(
        origin,
        CANONICAL_ORIGIN_MR,
        target_branch=staging_branch,
        state="merged",
    )
    if not any(
        item.get("iid") == canonical_verification_mr_iid for item in canonical_mrs
    ):
        raise InputError(
            "canonical verification MR is not in the canonical branch staging history"
        )

    target_branch = required_text(
        candidate.get("target_branch"),
        "candidate MR target_branch",
    )
    if not is_production_target(target_branch):
        raise InputError(
            f"candidate MR target {target_branch!r} is not a production branch"
        )
    target = glab_object(
        f"projects/{project}/repository/branches/{quote(target_branch, safe='')}"
    )
    target_commit = target.get("commit")
    if not isinstance(target_commit, dict):
        raise InputError("target branch commit must be a JSON object")
    target_sha = validate_full_sha(
        required_text(target_commit.get("id"), "target branch commit"),
        "target branch commit",
    )

    origin_diff_refs = origin.get("diff_refs")
    if not isinstance(origin_diff_refs, dict):
        raise InputError("canonical origin MR diff_refs must be a JSON object")
    origin_base = origin_diff_refs.get("base_sha")
    canonical_base = validate_full_sha(
        required_text(origin_base, CANONICAL_ORIGIN_BASE),
        CANONICAL_ORIGIN_BASE,
    )
    canonical_sha = mr_sha(verification, CANONICAL_VERIFICATION_MR)
    candidate_sha = mr_sha(candidate, CANDIDATE_MR)

    origin_ref = f"refs/release-parity/mr-{canonical_origin_mr_iid}"
    canonical_ref = f"refs/release-parity/mr-{canonical_verification_mr_iid}"
    candidate_ref = f"refs/release-parity/mr-{candidate_mr_iid}"
    target_ref = f"refs/remotes/origin/{target_branch}"
    fetch_ref(
        f"refs/merge-requests/{canonical_origin_mr_iid}/head",
        origin_ref,
    )
    fetch_ref(
        f"refs/merge-requests/{canonical_verification_mr_iid}/head",
        canonical_ref,
    )
    fetch_ref(f"refs/merge-requests/{candidate_mr_iid}/head", candidate_ref)
    fetch_ref(f"refs/heads/{target_branch}", target_ref)

    require_full_commit_sha(canonical_base, CANONICAL_ORIGIN_BASE)
    resolve_bound_ref(origin_ref, mr_sha(origin, CANONICAL_ORIGIN_MR), "origin MR")
    resolve_bound_ref(canonical_ref, canonical_sha, CANONICAL_VERIFICATION_MR)
    resolve_bound_ref(candidate_ref, candidate_sha, CANDIDATE_MR)
    resolve_bound_ref(target_ref, target_sha, "candidate target branch")

    refs = {
        "canonical_base": canonical_base,
        "canonical": canonical_sha,
        "candidate_base": target_sha,
        "candidate": candidate_sha,
    }
    provenance = {
        "project_path": project_path,
        "staging_branch": staging_branch,
        "canonical_origin_mr": {
            "iid": canonical_origin_mr_iid,
            "source_branch": origin["source_branch"],
            "base_sha": canonical_base,
            "head_sha": mr_sha(origin, CANONICAL_ORIGIN_MR),
        },
        "canonical_verification_mr": {
            "iid": canonical_verification_mr_iid,
            "source_branch": verification["source_branch"],
            "head_sha": canonical_sha,
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
