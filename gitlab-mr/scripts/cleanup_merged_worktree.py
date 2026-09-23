#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Safely remove one explicitly selected worktree after its MR is merged."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

COMMAND_TIMEOUT_SECONDS = 60
class CleanupError(ValueError):
    """The requested local cleanup is not proven safe."""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Verify a merged GitLab MR against one current-session linked worktree, "
            "then optionally remove that worktree and its local branch."
        )
    )
    result.add_argument("--worktree", required=True, type=Path)
    result.add_argument("--branch", required=True)
    result.add_argument("--mr-iid", required=True, type=int)
    result.add_argument(
        "--execute",
        action="store_true",
        help="Perform the cleanup after all checks pass; otherwise emit a preview.",
    )
    return result


def run(
    command: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=check,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = (
            exc.stderr.strip()
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        raise CleanupError(
            f"command failed ({' '.join(command[:3])}): {detail}"
        ) from exc


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, check=check)


def parse_worktrees(raw: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        records.append(current)
    return records


def canonical_existing_directory(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise CleanupError(f"{label} must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CleanupError(f"cannot resolve {label}: {exc}") from exc
    if not resolved.is_dir():
        raise CleanupError(f"{label} must be a directory")
    return resolved


def load_gitlab_object(worktree: Path, endpoint: str, label: str) -> dict[str, Any]:
    result = run(
        ["glab", "api", endpoint],
        cwd=worktree,
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CleanupError(f"GitLab {label} API returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise CleanupError(f"GitLab {label} API must return a JSON object")
    return value


def load_mr(worktree: Path, mr_iid: int) -> dict[str, Any]:
    return load_gitlab_object(worktree, f"projects/:id/merge_requests/{mr_iid}", "MR")


def load_project(worktree: Path) -> dict[str, Any]:
    return load_gitlab_object(worktree, "projects/:id", "project")

def validate_local(
    worktree: Path,
    branch: str,
) -> tuple[str, Path]:
    if not (worktree / ".git").is_file() or (worktree / ".git").is_symlink():
        raise CleanupError("target must be a linked worktree, not the primary checkout")

    branch_check = git(worktree, "check-ref-format", "--branch", branch, check=False)
    if branch_check.returncode != 0:
        raise CleanupError("branch is not a valid local branch name")

    records = parse_worktrees(
        git(worktree, "worktree", "list", "--porcelain").stdout
    )
    matching = [
        record
        for record in records
        if record.get("worktree")
        and Path(record["worktree"]).resolve() == worktree
    ]
    if len(matching) != 1:
        raise CleanupError("target is not exactly one registered worktree")
    expected_ref = f"refs/heads/{branch}"
    if matching[0].get("branch") != expected_ref:
        raise CleanupError("worktree is not checked out on the requested branch")
    if sum(record.get("branch") == expected_ref for record in records) != 1:
        raise CleanupError("requested branch is associated with another worktree")

    dirty = git(
        worktree,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
    ).stdout
    if dirty:
        raise CleanupError("worktree has tracked, untracked, or ignored files")

    local_sha = git(worktree, "rev-parse", expected_ref).stdout.strip()
    if matching[0].get("HEAD") != local_sha:
        raise CleanupError("worktree HEAD does not match the requested local branch")

    administrators = [
        Path(record["worktree"]).resolve()
        for record in records
        if record.get("worktree")
        and Path(record["worktree"]).resolve() != worktree
        and Path(record["worktree"]).is_dir()
    ]
    if not administrators:
        raise CleanupError("no surviving worktree is available to perform cleanup")
    return local_sha, administrators[0]


def validate_mr(
    mr: dict[str, Any], project: dict[str, Any], *, branch: str, local_sha: str
) -> None:
    if mr.get("state") != "merged" or not mr.get("merged_at"):
        raise CleanupError(
            "MR is not merged; pipeline success or mergeability is insufficient"
        )
    if mr.get("source_branch") != branch:
        raise CleanupError("MR source branch does not match the requested local branch")
    if mr.get("sha") != local_sha:
        raise CleanupError("local branch HEAD does not match the merged MR source SHA")
    target_branch = mr.get("target_branch")
    default_branch = project.get("default_branch")
    if not isinstance(target_branch, str) or not target_branch:
        raise CleanupError("MR target branch is missing or invalid")
    if not isinstance(default_branch, str) or not default_branch:
        raise CleanupError("GitLab project default branch is missing or invalid")
    if target_branch != default_branch:
        raise CleanupError("MR target branch is not the repository default branch")
    if target_branch == branch:
        raise CleanupError("refusing to delete the repository default branch")


def result_payload(*, status: str, worktree: Path, branch: str, mr_iid: int,
                   sha: str) -> dict[str, Any]:
    return {
        "status": status, "mr_iid": mr_iid, "branch": branch,
        "source_sha": sha, "worktree": str(worktree),
        "remote_branch_deleted": False,
    }


def probe_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return git(cwd, *args, check=False)
    except CleanupError:
        return None


def observe_cleanup(administrator: Path, worktree: Path, branch: str) -> dict[str, bool | None]:
    try:
        worktree_removed: bool | None = not worktree.exists()
    except OSError:
        worktree_removed = None
    observed: dict[str, bool | None] = {
        "worktree_removed": worktree_removed,
        "local_branch_deleted": None,
        "worktree_metadata_pruned": None,
    }
    ref = probe_git(administrator, "show-ref", "--verify", "--quiet",
                    f"refs/heads/{branch}")
    if ref and ref.returncode in {0, 1}:
        observed["local_branch_deleted"] = ref.returncode == 1
    records = probe_git(administrator, "worktree", "list", "--porcelain")
    if records and records.returncode == 0:
        entries = parse_worktrees(records.stdout)
        observed["worktree_metadata_pruned"] = not any(
            item.get("worktree") == str(worktree) for item in entries
        )
    return observed


def main() -> int:
    args = parser().parse_args()
    mutation_started = False
    try:
        worktree = canonical_existing_directory(args.worktree, "worktree")
        local_sha, administrator = validate_local(worktree, args.branch)
        mr = load_mr(worktree, args.mr_iid)
        project = load_project(worktree)
        validate_mr(mr, project, branch=args.branch, local_sha=local_sha)
        if not args.execute:
            payload = result_payload(
                status="ready", worktree=worktree, branch=args.branch,
                mr_iid=args.mr_iid, sha=local_sha,
            )
            print(json.dumps(payload, sort_keys=True))
            return 0

        # Recheck local state immediately before the destructive operations.
        rechecked_sha, rechecked_administrator = validate_local(
            worktree, args.branch
        )
        if rechecked_sha != local_sha or rechecked_administrator != administrator:
            raise CleanupError("worktree state changed after MR verification")
        os.chdir(administrator)
        mutation_started = True
        git(administrator, "worktree", "remove", str(worktree))
        git(
            administrator,
            "update-ref",
            "-d",
            f"refs/heads/{args.branch}",
            local_sha,
        )
        git(administrator, "worktree", "prune")
        payload = result_payload(
            status="cleaned", worktree=worktree, branch=args.branch,
            mr_iid=args.mr_iid, sha=local_sha,
        )
        payload.update({
            "worktree_removed": True,
            "local_branch_deleted": True,
            "worktree_metadata_pruned": True,
        })
        print(json.dumps(payload, sort_keys=True))
        return 0
    except CleanupError as exc:
        if mutation_started:
            payload = result_payload(
                status="partial", worktree=worktree, branch=args.branch,
                mr_iid=args.mr_iid, sha=local_sha,
            )
            payload.update({
                "reason": str(exc),
                **observe_cleanup(administrator, worktree, args.branch),
            })
            print(json.dumps(payload, sort_keys=True), file=sys.stderr)
            return 3
        print(
            json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
