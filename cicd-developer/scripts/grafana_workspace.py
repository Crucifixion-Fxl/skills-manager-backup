#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Use an isolated Git worktree for Grafana Dashboard source changes only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


TARGET_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "grafana"
    / "target-repository.json"
)


def _target_setting(name: str) -> str:
    try:
        value = json.loads(TARGET_CONFIG_PATH.read_text(encoding="utf-8"))[name]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid Grafana target repository configuration: {exc}") from exc
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"invalid Grafana target repository configuration field: {name}")
    return value


TARGET_REPOSITORY_URL = _target_setting("remote")
TARGET_PROJECT = _target_setting("project")
DEFAULT_BRANCH = _target_setting("defaultBranch")
BRANCH_PATTERN = re.compile(r"^grafana/[a-z0-9][a-z0-9/_-]*$")
MR_URL_PATTERN = re.compile(r"https?://[^\s]+/-/merge_requests/\d+(?:[?#][^\s]*)?")


class WorkspaceError(RuntimeError):
    """A requested source operation violates Git or repository isolation."""


@dataclass(frozen=True)
class CallerSnapshot:
    root: Path | None
    status: str
    remotes: str


def normalize_remote(value: str) -> str:
    """Compare HTTPS remotes independent of trailing slash and .git suffix."""
    normalized = value.strip().rstrip("/")
    return normalized[:-4] if normalized.endswith(".git") else normalized


def _run(command: Sequence[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        list(command), cwd=cwd, check=False, text=True, capture_output=True
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise WorkspaceError(detail)
    return result.stdout.strip()


def _git(directory: Path, *arguments: str) -> str:
    return _run(["git", "-C", str(directory), *arguments])


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _git_root(directory: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
        check=False,
        text=True,
        capture_output=True,
    )
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def snapshot_caller(caller_root: Path) -> CallerSnapshot:
    """Capture caller Git identity before a separate Dashboard operation."""
    root = _git_root(caller_root)
    if root is None:
        return CallerSnapshot(None, "", "")
    return CallerSnapshot(
        root,
        _git(root, "status", "--porcelain=v1", "--untracked-files=all"),
        _git(root, "remote", "-v"),
    )


def assert_caller_unchanged(snapshot: CallerSnapshot) -> None:
    """Fail closed if a caller repository changed during the operation."""
    if snapshot.root is None:
        return
    current = snapshot_caller(snapshot.root)
    if current != snapshot:
        raise WorkspaceError("caller repository changed during Grafana source operation")


def assert_workspace(workspace: Path, caller_root: Path) -> Path:
    """Verify exact target origin and ensure the worktree cannot be nested."""
    root = _git_root(workspace)
    if root is None or root != workspace.resolve():
        raise WorkspaceError("workspace must be the root of a Git checkout")
    if _inside(root, caller_root) or _inside(caller_root, root):
        raise WorkspaceError("Dashboard workspace must be outside the caller repository")
    origin = _git(root, "remote", "get-url", "origin")
    if normalize_remote(origin) != normalize_remote(TARGET_REPOSITORY_URL):
        raise WorkspaceError("workspace origin is not the approved Grafana Dashboard repository")
    return root


def _branch(workspace: Path) -> str:
    branch = _git(workspace, "branch", "--show-current")
    if not BRANCH_PATTERN.fullmatch(branch):
        raise WorkspaceError("only a grafana/<name> feature branch may be changed")
    return branch


def prepare_workspace(
    caller_root: Path,
    branch: str,
    workspace_parent: Path | None = None,
) -> Path:
    """Fresh-clone the fixed source repository outside the caller checkout."""
    if not BRANCH_PATTERN.fullmatch(branch):
        raise WorkspaceError("branch must match grafana/<lowercase-slug>")
    caller_root = caller_root.resolve()
    if workspace_parent and _inside(workspace_parent.resolve(), caller_root):
        raise WorkspaceError("workspace parent must be outside the caller repository")
    snapshot = snapshot_caller(caller_root)
    temporary_root = Path(
        tempfile.mkdtemp(
            prefix="cicd-developer-grafana-",
            dir=str(workspace_parent.resolve()) if workspace_parent else None,
        )
    )
    workspace = temporary_root / "grafana-dashboards-as-code"
    if _inside(workspace, caller_root):
        raise WorkspaceError("refusing to clone the Dashboard repository inside caller")
    _run(
        [
            "git",
            "clone",
            "--origin",
            "origin",
            "--branch",
            DEFAULT_BRANCH,
            "--single-branch",
            TARGET_REPOSITORY_URL,
            str(workspace),
        ]
    )
    root = assert_workspace(workspace, caller_root)
    _git(root, "checkout", "-b", branch, f"origin/{DEFAULT_BRANCH}")
    assert_caller_unchanged(snapshot)
    return root


def _source_paths(values: Sequence[str]) -> list[str]:
    if not values:
        raise WorkspaceError("at least one dashboard source path is required")
    result: list[str] = []
    for value in values:
        selected = Path(value)
        if selected.is_absolute() or ".." in selected.parts:
            raise WorkspaceError("source path must be a relative dashboards/*.json path")
        if len(selected.parts) < 2 or selected.parts[0] != "dashboards" or selected.suffix != ".json":
            raise WorkspaceError("only Dashboard JSON sources under dashboards/ may be committed")
        result.append(selected.as_posix())
    if len(result) != len(set(result)):
        raise WorkspaceError("dashboard source paths must be unique")
    return result


def commit_sources(
    workspace: Path,
    caller_root: Path,
    paths: Sequence[str],
    message: str,
) -> str:
    """Commit exactly named Dashboard source paths; reports never enter commits."""
    root = assert_workspace(workspace, caller_root)
    branch = _branch(root)
    sources = _source_paths(paths)
    if not message.strip():
        raise WorkspaceError("commit message is required")
    _git(root, "add", "-A", "--", *sources)
    staged = [line for line in _git(root, "diff", "--cached", "--name-only").splitlines() if line]
    unexpected = sorted(set(staged) - set(sources))
    if unexpected:
        raise WorkspaceError(f"refusing to commit unexpected staged paths: {', '.join(unexpected)}")
    if not staged:
        raise WorkspaceError("no Dashboard source changes are staged")
    _git(root, "commit", "-m", message)
    return branch


def push_branch(workspace: Path, caller_root: Path) -> str:
    """Push only the checked Grafana feature branch; never main or force-push."""
    root = assert_workspace(workspace, caller_root)
    branch = _branch(root)
    _git(root, "push", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}")
    return branch


def _merge_request_url(*outputs: str) -> str:
    """Extract the exact MR URL emitted by glab instead of returning free-form output."""
    for output in outputs:
        match = MR_URL_PATTERN.search(output)
        if match:
            return match.group(0).rstrip(").,;")
    raise WorkspaceError("glab succeeded but did not return a merge request URL")


def _target_repository_reference() -> str:
    """Return a host-qualified repository reference for GitLab CLI calls."""
    return TARGET_REPOSITORY_URL.removesuffix(".git")


def create_merge_request(workspace: Path, caller_root: Path, title: str, description: str) -> str:
    """Open an MR against the fixed target project through existing glab auth."""
    root = assert_workspace(workspace, caller_root)
    branch = _branch(root)
    if not title.strip():
        raise WorkspaceError("merge request title is required")
    result = subprocess.run(
        [
            "glab",
            "mr",
            "create",
            "--repo",
            _target_repository_reference(),
            "--source-branch",
            branch,
            "--target-branch",
            DEFAULT_BRANCH,
            "--title",
            title,
            "--description",
            description,
        ],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise WorkspaceError((result.stderr or result.stdout or "glab MR creation failed").strip())
    return _merge_request_url(result.stdout, result.stderr)


def submit_sources(
    workspace: Path,
    caller_root: Path,
    paths: Sequence[str],
    message: str,
    title: str,
    description: str,
) -> dict[str, str]:
    """Commit, push, and open the required review MR for source changes only."""
    branch = commit_sources(workspace, caller_root, paths, message)
    root = assert_workspace(workspace, caller_root)
    commit = _git(root, "rev-parse", "HEAD")
    push_branch(root, caller_root)
    merge_request = create_merge_request(root, caller_root, title, description)
    return {"branch": branch, "commit": commit, "mergeRequest": merge_request}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="fresh-clone an isolated feature worktree")
    prepare.add_argument("--caller-root", type=Path, required=True)
    prepare.add_argument("--branch", required=True)
    prepare.add_argument("--workspace-parent", type=Path)
    verify = subparsers.add_parser("verify", help="verify workspace isolation and origin")
    verify.add_argument("--caller-root", type=Path, required=True)
    verify.add_argument("--workspace", type=Path, required=True)
    commit = subparsers.add_parser("commit", help="commit explicit Dashboard source paths")
    commit.add_argument("--caller-root", type=Path, required=True)
    commit.add_argument("--workspace", type=Path, required=True)
    commit.add_argument("--file", action="append", required=True)
    commit.add_argument("--message", required=True)
    push = subparsers.add_parser("push", help="push only a Grafana feature branch")
    push.add_argument("--caller-root", type=Path, required=True)
    push.add_argument("--workspace", type=Path, required=True)
    mr = subparsers.add_parser("create-mr", help="open an MR in the fixed Dashboard project")
    mr.add_argument("--caller-root", type=Path, required=True)
    mr.add_argument("--workspace", type=Path, required=True)
    mr.add_argument("--title", required=True)
    mr.add_argument("--description", default="")
    submit = subparsers.add_parser(
        "submit", help="commit named sources, push a feature branch, and create its review MR"
    )
    submit.add_argument("--caller-root", type=Path, required=True)
    submit.add_argument("--workspace", type=Path, required=True)
    submit.add_argument("--file", action="append", required=True)
    submit.add_argument("--message", required=True)
    submit.add_argument("--title", required=True)
    submit.add_argument("--description", default="")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            workspace = prepare_workspace(args.caller_root, args.branch, args.workspace_parent)
            output = {"branch": args.branch, "workspace": str(workspace)}
        elif args.command == "verify":
            output = {"workspace": str(assert_workspace(args.workspace, args.caller_root)), "valid": True}
        elif args.command == "commit":
            output = {"branch": commit_sources(args.workspace, args.caller_root, args.file, args.message), "committed": True}
        elif args.command == "push":
            output = {"branch": push_branch(args.workspace, args.caller_root), "pushed": True}
        elif args.command == "create-mr":
            output = {"mergeRequest": create_merge_request(args.workspace, args.caller_root, args.title, args.description)}
        else:
            output = submit_sources(
                args.workspace,
                args.caller_root,
                args.file,
                args.message,
                args.title,
                args.description,
            )
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    except (OSError, WorkspaceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
