"""确定性证明生产分支 MR 只退休已迁移的 staging writer。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit

from release_parity_core import (
    InputError,
    require_ancestor,
    require_full_commit_sha,
    resolve_bound_ref,
    validate_full_sha,
)
from release_parity_gitlab import (
    fetch_ref,
    glab_json,
    glab_object,
    mr_sha,
    require_mr,
    required_text,
)
from release_workflow_policy import PolicyError, is_production_target
from staging_writer_cleanup_ci import validate_ci_transition
from staging_writer_cleanup_contract import (
    load_cleanup_contract,
    string_list,
    validate_declared_paths,
)


def _pipeline_id(url: Any, project_path: str) -> tuple[str, int]:
    if not isinstance(url, str):
        raise InputError("accepted_pipeline must be an HTTPS GitLab pipeline URL")
    parsed = urlsplit(url)
    expected_prefix = f"/{project_path}/-/pipelines/"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "gitlab.addx.ai"
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(expected_prefix)
    ):
        raise InputError(
            "accepted_pipeline must be an HTTPS gitlab.addx.ai URL for this project"
        )
    suffix = parsed.path.removeprefix(expected_prefix)
    if re.fullmatch(r"[1-9][0-9]*", suffix) is None:
        raise InputError("accepted_pipeline must end with a numeric pipeline id")
    return url, int(suffix)


def _attest(args: Any) -> dict[str, Any]:
    project_path = required_text(args.project_path, "project_path")
    project = quote(project_path, safe="")
    candidate = glab_object(f"projects/{project}/merge_requests/{args.mr_iid}")
    require_mr(candidate, "cleanup candidate MR", state="opened")
    if candidate.get("iid") != args.mr_iid:
        raise InputError("cleanup candidate MR iid does not match state")
    if candidate.get("source_branch") != args.branch:
        raise InputError("cleanup candidate MR source branch does not match state")
    if candidate.get("target_branch") != args.target_branch:
        raise InputError("cleanup candidate MR target does not match state")
    if not is_production_target(args.target_branch):
        raise InputError("staging writer cleanup target must be a production branch")
    candidate_sha = mr_sha(candidate, "cleanup candidate MR")
    if candidate_sha != args.head_sha:
        raise InputError("cleanup candidate MR SHA does not match head_sha")

    target = glab_object(
        f"projects/{project}/repository/branches/{quote(args.target_branch, safe='')}"
    )
    staging = glab_object(
        f"projects/{project}/repository/branches/{quote(args.staging_branch, safe='')}"
    )
    if target.get("name") != args.target_branch or target.get("protected") is not True:
        raise InputError("cleanup target branch must be the protected target branch")
    if staging.get("name") != args.staging_branch:
        raise InputError("staging branch name does not match state")
    if staging.get("protected") is not True:
        raise InputError("staging branch must be protected")
    protected = glab_object(
        f"projects/{project}/protected_branches/{quote(args.staging_branch, safe='')}"
    )
    if protected.get("name") != args.staging_branch:
        raise InputError("protected staging branch policy name does not match")
    if protected.get("allow_force_push") is not False:
        raise InputError("protected staging branch must disable force push")
    target_commit = target.get("commit")
    staging_commit = staging.get("commit")
    if not isinstance(target_commit, dict) or not isinstance(staging_commit, dict):
        raise InputError("branch commit metadata must be an object")
    target_sha = validate_full_sha(
        required_text(target_commit.get("id"), "target branch commit"),
        "target branch commit",
    )
    staging_head_sha = validate_full_sha(
        required_text(staging_commit.get("id"), "staging branch commit"),
        "staging branch commit",
    )
    candidate_ref = f"refs/cleanup-attestation/mr-{args.mr_iid}"
    target_ref = f"refs/remotes/origin/{args.target_branch}"
    staging_ref = f"refs/remotes/origin/{args.staging_branch}"
    fetch_ref(f"refs/merge-requests/{args.mr_iid}/head", candidate_ref)
    fetch_ref(f"refs/heads/{args.target_branch}", target_ref)
    fetch_ref(f"refs/heads/{args.staging_branch}", staging_ref)
    resolve_bound_ref(candidate_ref, candidate_sha, "cleanup candidate MR")
    resolve_bound_ref(target_ref, target_sha, "cleanup target branch")
    resolve_bound_ref(staging_ref, staging_head_sha, "cleanup staging branch")
    require_ancestor(target_sha, candidate_sha, "cleanup candidate")

    contract, metadata = load_cleanup_contract(
        candidate_ref,
        target_ref,
        args.cleanup_contract,
    )
    if contract.get("staging_branch") != args.staging_branch:
        raise InputError("cleanup contract staging_branch does not match state")
    accepted_sha = validate_full_sha(
        required_text(
            contract.get("accepted_staging_sha"),
            "accepted_staging_sha",
        ),
        "accepted_staging_sha",
    )
    require_full_commit_sha(accepted_sha, "accepted_staging_sha")
    require_ancestor(accepted_sha, staging_head_sha, "accepted staging lineage")
    pipeline_url, pipeline_id = _pipeline_id(
        contract.get("accepted_pipeline"), project_path
    )
    pipeline = glab_object(f"projects/{project}/pipelines/{pipeline_id}")
    if pipeline.get("status") != "success":
        raise InputError("accepted staging pipeline status must be success")
    if pipeline.get("ref") != args.staging_branch or pipeline.get("sha") != accepted_sha:
        raise InputError("accepted pipeline must bind the declared staging branch and SHA")
    if pipeline.get("web_url") != pipeline_url:
        raise InputError("accepted pipeline web_url does not match cleanup contract")
    if pipeline.get("id") != pipeline_id:
        raise InputError("accepted pipeline id does not match cleanup contract")
    verification, documentation = validate_declared_paths(
        contract,
        metadata,
        target_ref,
        candidate_ref,
    )
    writers, gates = validate_ci_transition(
        contract,
        target_ref,
        candidate_ref,
        args.target_branch,
        verification,
    )
    staging_gate = required_text(
        contract.get("staging_contract_gate_job"),
        "staging_contract_gate_job",
    )
    accepted_jobs = string_list(contract.get("accepted_jobs"), "accepted_jobs")
    if staging_gate in writers or set(accepted_jobs) != {*writers, staging_gate}:
        raise InputError(
            "accepted_jobs must bind the staging contract gate and every retired "
            "writer exactly"
        )
    jobs = glab_json(
        f"projects/{project}/pipelines/{pipeline_id}/jobs?per_page=100"
    )
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise InputError("accepted pipeline jobs must be a JSON list of objects")
    actual_jobs = {
        job.get("name"): job.get("status")
        for job in jobs
        if isinstance(job.get("name"), str)
    }
    if any(
        sum(job.get("name") == name for job in jobs) != 1
        or actual_jobs.get(name) != "success"
        for name in accepted_jobs
    ):
        raise InputError("accepted pipeline jobs do not match cleanup contract")
    policy_url = (
        f"https://gitlab.addx.ai/api/v4/projects/{project}/protected_branches/"
        f"{quote(args.staging_branch, safe='')}"
    )
    return {
        "code_status": "pass",
        "workflow": "staging-writer-cleanup",
        "contract": metadata,
        "candidate_mr_sha": candidate_sha,
        "candidate_target_sha": target_sha,
        "staging_branch": args.staging_branch,
        "staging_branch_head_sha": staging_head_sha,
        "accepted_staging_sha": accepted_sha,
        "accepted_pipeline": pipeline_url,
        "accepted_pipeline_id": pipeline_id,
        "accepted_pipeline_api_url": (
            f"https://gitlab.addx.ai/api/v4/projects/{project}/pipelines/{pipeline_id}"
        ),
        "accepted_pipeline_jobs_url": (
            f"https://gitlab.addx.ai/api/v4/projects/{project}/pipelines/"
            f"{pipeline_id}/jobs?per_page=100"
        ),
        "accepted_jobs": accepted_jobs,
        "staging_contract_gate_job": staging_gate,
        "staging_branch_policy_url": policy_url,
        "removed_writer_jobs": writers,
        "contract_gate_jobs": gates,
        "verification_paths": verification,
        "documentation_paths": documentation,
    }


def attest_staging_writer_cleanup(args: Any) -> tuple[dict[str, Any], str]:
    try:
        report = _attest(args)
    except InputError as exc:
        raise PolicyError(f"staging writer cleanup attestation failed: {exc}") from exc
    raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    return report, hashlib.sha256(raw).hexdigest()
