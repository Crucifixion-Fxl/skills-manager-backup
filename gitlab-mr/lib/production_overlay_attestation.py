"""Attest a production-only Argo CD overlay without exempting feature code."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit

import yaml
from production_overlay_scope import require_production_only_yaml_change
from release_parity_core import (
    InputError,
    changed_paths,
    require_ancestor,
    resolve_bound_ref,
    tree_entry,
    validate_full_sha,
    validate_repo_path,
)
from release_parity_gitlab import (
    fetch_ref,
    glab_object,
    mr_sha,
    require_mr,
    required_text,
)
from release_workflow_policy import PolicyError, is_production_target

GITOPS_PROJECT = "DEV/argocd-apps"
GITOPS_BRANCH = "main"
PLAN_PATH = re.compile(r"docs/requirements/([1-9][0-9]*)/plan\.md\Z")
DEPLOYMENT_PLAN = "docs/deployment/prod-deployment-plan.md"
PROD_OVERLAY = re.compile(r"(?:\A|/)overlays/prod-[a-z0-9-]+\Z")


def _application_path(evidence_url: str) -> str:
    parsed = urlsplit(evidence_url)
    prefix = f"/{GITOPS_PROJECT}/-/blob/{GITOPS_BRANCH}/"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "gitlab.addx.ai"
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(prefix)
    ):
        raise InputError(
            "staging_flow_evidence must be a DEV/argocd-apps main blob URL"
        )
    path = parsed.path.removeprefix(prefix)
    if (
        not path.endswith((".yaml", ".yml"))
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or "%" in path
    ):
        raise InputError("production Application path must be a regular YAML path")
    return path


def _branch(project: str, name: str, label: str) -> str:
    branch = glab_object(
        f"projects/{project}/repository/branches/{quote(name, safe='')}"
    )
    if branch.get("name") != name or branch.get("protected") is not True:
        raise InputError(f"{label} must be the protected {name} branch")
    commit = branch.get("commit")
    if not isinstance(commit, dict):
        raise InputError(f"{label} commit metadata must be an object")
    return validate_full_sha(
        required_text(commit.get("id"), f"{label} commit"),
        f"{label} commit",
    )


def _application(evidence_url: str) -> tuple[dict[str, Any], dict[str, str]]:
    path = _application_path(evidence_url)
    project = quote(GITOPS_PROJECT, safe="")
    sha = _branch(project, GITOPS_BRANCH, "GitOps evidence")
    file = glab_object(
        f"projects/{project}/repository/files/{quote(path, safe='')}?ref={sha}"
    )
    if file.get("file_path") != path or file.get("encoding") != "base64":
        raise InputError("GitOps evidence file path or encoding does not match")
    content = required_text(file.get("content"), "GitOps evidence content")
    try:
        raw = base64.b64decode(content, validate=True)
        application = yaml.safe_load(raw)
    except (ValueError, yaml.YAMLError) as exc:
        raise InputError("GitOps evidence is not a valid Application YAML") from exc
    if not isinstance(application, dict):
        raise InputError("GitOps evidence must contain an Application object")
    if (
        application.get("apiVersion") != "argoproj.io/v1alpha1"
        or application.get("kind") != "Application"
    ):
        raise InputError("GitOps evidence must be an Argo CD Application")
    digest = hashlib.sha256(raw).hexdigest()
    if file.get("content_sha256") != digest:
        raise InputError("GitOps evidence content digest does not match GitLab")
    return application, {
        "project": GITOPS_PROJECT,
        "branch": GITOPS_BRANCH,
        "commit_sha": sha,
        "path": path,
        "blob_sha256": digest,
        "url": evidence_url,
    }


def _source_project(repo_url: Any) -> str:
    value = required_text(repo_url, "Application spec.source.repoURL")
    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.netloc == "gitlab.addx.ai":
        path = parsed.path.lstrip("/")
    elif value.startswith("git@gitlab.addx.ai:"):
        path = value.removeprefix("git@gitlab.addx.ai:")
    else:
        raise InputError("Application repoURL must use gitlab.addx.ai")
    return path.removesuffix(".git")


def _existing_regular_yaml(base: str, head: str, path: str) -> None:
    if not path.endswith((".yaml", ".yml")):
        raise InputError(f"production overlay change is not YAML: {path}")
    before = tree_entry(base, path)
    after = tree_entry(head, path)
    if any(
        entry is None
        or entry["type"] != "blob"
        or entry["mode"] not in {"100644", "100755"}
        for entry in (before, after)
    ):
        raise InputError(f"production overlay change must modify regular YAML: {path}")


def _attest(args: Any) -> dict[str, Any]:
    project_path = required_text(args.project_path, "project_path")
    project = quote(project_path, safe="")
    mr = glab_object(f"projects/{project}/merge_requests/{args.mr_iid}")
    require_mr(mr, "non-promotion candidate MR", state="opened")
    if (
        mr.get("iid") != args.mr_iid
        or mr.get("source_branch") != args.branch
        or mr.get("target_branch") != args.target_branch
        or not is_production_target(args.target_branch)
    ):
        raise InputError("non-promotion candidate MR identity or target differs")
    candidate_sha = mr_sha(mr, "non-promotion candidate MR")
    if candidate_sha != args.head_sha:
        raise InputError("non-promotion candidate MR SHA does not match head_sha")

    target_sha = _branch(project, args.target_branch, "candidate target")
    staging_sha = _branch(project, args.staging_branch, "staging")
    candidate_ref = f"refs/non-promotion/mr-{args.mr_iid}"
    target_ref = f"refs/remotes/origin/{args.target_branch}"
    staging_ref = f"refs/remotes/origin/{args.staging_branch}"
    fetch_ref(f"refs/merge-requests/{args.mr_iid}/head", candidate_ref)
    fetch_ref(f"refs/heads/{args.target_branch}", target_ref)
    fetch_ref(f"refs/heads/{args.staging_branch}", staging_ref)
    resolve_bound_ref(candidate_ref, candidate_sha, "non-promotion candidate")
    resolve_bound_ref(target_ref, target_sha, "non-promotion target")
    resolve_bound_ref(staging_ref, staging_sha, "non-promotion staging")
    require_ancestor(target_sha, candidate_sha, "non-promotion candidate")

    application, app_provenance = _application(args.staging_flow_evidence)
    metadata = application.get("metadata")
    spec = application.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise InputError("Application metadata and spec must be objects")
    labels = metadata.get("labels")
    source = spec.get("source")
    if not isinstance(labels, dict) or not isinstance(source, dict):
        raise InputError("Application labels and spec.source must be objects")
    environment = required_text(labels.get("env"), "Application environment")
    application_name = required_text(metadata.get("name"), "Application name")
    overlay = validate_repo_path(source.get("path"), "Application source path")
    if (
        not environment.startswith("prod-")
        or PROD_OVERLAY.search(overlay) is None
        or _source_project(source.get("repoURL")) != project_path
        or source.get("targetRevision") != args.target_branch
    ):
        raise InputError(
            "Application must own a prod-* overlay of this project and target branch"
        )
    region = environment.removeprefix("prod-")
    if not application_name.endswith(f"-prod-{region}"):
        raise InputError("production Application name must match its environment")
    staging_application, staging_provenance = _application(
        required_text(args.staging_application_evidence, "staging_application_evidence")
    )
    if staging_provenance["commit_sha"] != app_provenance["commit_sha"]:
        raise InputError(
            "production and staging Applications must use the same GitOps commit"
        )
    staging_metadata = staging_application.get("metadata")
    staging_spec = staging_application.get("spec")
    if not isinstance(staging_metadata, dict) or not isinstance(staging_spec, dict):
        raise InputError("staging Application metadata and spec must be objects")
    staging_labels = staging_metadata.get("labels")
    staging_source = staging_spec.get("source")
    if not isinstance(staging_labels, dict) or not isinstance(staging_source, dict):
        raise InputError("staging Application labels and source must be objects")
    staging_overlay = overlay.removesuffix(f"prod-{region}") + f"staging-{region}"
    staging_path = validate_repo_path(
        staging_source.get("path"), "staging Application source path"
    )
    # A staging Application may use a numeric cluster qualifier (for example,
    # staging-us-390) while still owning a separate staging-only overlay.
    staging_overlay_matches = re.fullmatch(
        rf"{re.escape(staging_overlay)}(?:-[0-9]+)?", staging_path
    ) is not None
    if (
        staging_metadata.get("name")
        != application_name.removesuffix(f"-prod-{region}") + f"-staging-{region}"
        or staging_labels.get("env") != f"staging-{region}"
        or _source_project(staging_source.get("repoURL")) != project_path
        or staging_source.get("targetRevision") != args.staging_branch
        or not staging_overlay_matches
    ):
        raise InputError("staging Application must use its separate staging overlay")

    paths = changed_paths(target_sha, candidate_sha)
    if not paths:
        raise InputError("non-promotion candidate has no changes")
    overlay_paths: list[str] = []
    plan_paths: list[str] = []
    deployment_plan_paths: list[str] = []
    for path in sorted(paths):
        if path.startswith(f"{overlay}/"):
            _existing_regular_yaml(target_sha, candidate_sha, path)
            require_production_only_yaml_change(target_sha, candidate_sha, path)
            staging_entry = tree_entry(staging_sha, path)
            if staging_entry is not None and staging_entry != tree_entry(
                target_sha, path
            ):
                raise InputError(f"staging already differs on production path: {path}")
            overlay_paths.append(path)
        elif PLAN_PATH.fullmatch(path):
            plan_paths.append(path)
        elif path == DEPLOYMENT_PLAN:
            before = tree_entry(target_sha, path)
            after = tree_entry(candidate_sha, path)
            if not all(
                entry is not None
                and entry["type"] == "blob"
                and entry["mode"] == "100644"
                for entry in (before, after)
            ):
                raise InputError("deployment plan must modify a regular file")
            deployment_plan_paths.append(path)
        else:
            raise InputError(f"non-promotion diff contains non-production path: {path}")
    if not overlay_paths or len(plan_paths) > 1:
        raise InputError("candidate needs production YAML and at most one issue plan")
    if plan_paths:
        issue_path = (
            f"Work Item: https://gitlab.addx.ai/{project_path}/-/issues/"
            f"{PLAN_PATH.fullmatch(plan_paths[0]).group(1)}"
        )
        description = required_text(mr.get("description"), "MR description")
        if re.search(rf"(?m)^{re.escape(issue_path)}[ \t]*$", description) is None:
            raise InputError("issue plan path does not match MR Work Item")
        after = tree_entry(candidate_sha, plan_paths[0])
        if after is None or after["type"] != "blob" or after["mode"] != "100644":
            raise InputError("issue plan must be a regular file")
    return {
        "code_status": "pass",
        "workflow": "production-only-argocd-overlay",
        "candidate_mr_iid": args.mr_iid,
        "candidate_mr_sha": candidate_sha,
        "candidate_target_sha": target_sha,
        "staging_branch": args.staging_branch,
        "staging_branch_sha": staging_sha,
        "application": app_provenance,
        "staging_application": staging_provenance,
        "application_name": metadata.get("name"),
        "application_environment": environment,
        "application_overlay": overlay,
        "overlay_paths": overlay_paths,
        "plan_paths": plan_paths,
        "deployment_plan_paths": deployment_plan_paths,
    }


def attest_production_overlay(args: Any) -> tuple[dict[str, Any], str]:
    try:
        report = _attest(args)
    except InputError as exc:
        raise PolicyError(f"production overlay attestation failed: {exc}") from exc
    raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    return report, hashlib.sha256(raw).hexdigest()
