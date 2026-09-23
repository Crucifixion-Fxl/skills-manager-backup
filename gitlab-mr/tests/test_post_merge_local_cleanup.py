from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "cleanup_merged_worktree.py"
MR_IID = 42


def git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=check,
    )


def write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def setup_repo(
    tmp_path: Path,
) -> tuple[Path, Path, str, dict[str, object], dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "tests@example.com")
    git(repo, "config", "user.name", "Test User")
    write(repo, "README.md", "base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")

    worktree = tmp_path / "session-worktree"
    git(repo, "worktree", "add", "-b", "feature/cleanup", str(worktree))
    write(worktree, "feature.txt", "feature\n")
    git(worktree, "add", ".")
    git(worktree, "commit", "-m", "feature")
    sha = git(worktree, "rev-parse", "HEAD").stdout.strip()

    response: dict[str, object] = {
        "iid": MR_IID,
        "state": "merged",
        "merged_at": "2026-09-15T08:00:00Z",
        "source_branch": "feature/cleanup",
        "target_branch": "main",
        "sha": sha,
    }
    response_path = tmp_path / "mr.json"
    response_path.write_text(json.dumps(response), encoding="utf-8")
    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps({"default_branch": "main"}),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_glab = fake_bin / "glab"
    fake_glab.write_text(
        """#!/usr/bin/env python3
import os
import sys

mr_endpoint = f"projects/:id/merge_requests/{os.environ['FAKE_MR_IID']}"
if sys.argv[1:] == ["api", mr_endpoint]:
    response_path = os.environ["FAKE_MR_RESPONSE"]
elif sys.argv[1:] == ["api", "projects/:id"]:
    response_path = os.environ["FAKE_PROJECT_RESPONSE"]
else:
    print(f"unexpected glab invocation: {sys.argv}", file=sys.stderr)
    raise SystemExit(2)
with open(response_path, encoding="utf-8") as source:
    print(source.read())
""",
        encoding="utf-8",
    )
    fake_glab.chmod(0o755)
    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import subprocess

failure_prefix = json.loads(os.environ.get("FAKE_GIT_FAIL_PREFIX", "[]"))
if failure_prefix and sys.argv[1:1 + len(failure_prefix)] == failure_prefix:
    if os.environ.get("FAKE_GIT_FAIL_AFTER") == "1":
        subprocess.run(
            [os.environ["REAL_GIT"], *sys.argv[1:]],
            check=True,
        )
    print("injected git failure", file=sys.stderr)
    raise SystemExit(9)
if os.environ.get("FAKE_GIT_PROBE_FAILURE") == "1" and (
    sys.argv[1:2] == ["show-ref"] or sys.argv[1:3] == ["worktree", "list"]
) and not os.path.exists(os.environ["FAKE_WORKTREE"]):
    print("injected probe failure", file=sys.stderr)
    raise SystemExit(8)
real_git = os.environ["REAL_GIT"]
os.execv(real_git, [real_git, *sys.argv[1:]])
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env = os.environ.copy()
    env["FAKE_MR_IID"] = str(MR_IID)
    env["FAKE_MR_RESPONSE"] = str(response_path)
    env["FAKE_PROJECT_RESPONSE"] = str(project_path)
    env["FAKE_WORKTREE"] = str(worktree)
    env["REAL_GIT"] = shutil.which("git", path=env["PATH"]) or "git"
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return repo, worktree, sha, response, env


def run_cleanup(
    worktree: Path,
    env: dict[str, str],
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--worktree",
            str(worktree),
            "--branch",
            "feature/cleanup",
            "--mr-iid",
            str(MR_IID),
            *extra,
        ],
        cwd=worktree,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def update_response(env: dict[str, str], response: dict[str, object]) -> None:
    Path(env["FAKE_MR_RESPONSE"]).write_text(
        json.dumps(response),
        encoding="utf-8",
    )


def update_project_response(
    env: dict[str, str],
    response: dict[str, object],
) -> None:
    Path(env["FAKE_PROJECT_RESPONSE"]).write_text(
        json.dumps(response),
        encoding="utf-8",
    )


def blocked_reason(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 2, result.stdout + result.stderr
    return json.loads(result.stderr)["reason"]


def test_command_timeout_is_structured_as_cleanup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location("cleanup_helper", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def time_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["glab", "api"], timeout=60)

    monkeypatch.setattr(module.subprocess, "run", time_out)
    with pytest.raises(module.CleanupError, match="timed out after 60 seconds"):
        module.run(["glab", "api", "projects/:id"], cwd=tmp_path)


def test_preview_is_read_only_and_reports_ready(tmp_path: Path) -> None:
    repo, worktree, sha, _, env = setup_repo(tmp_path)

    result = run_cleanup(worktree, env)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "branch": "feature/cleanup",
        "mr_iid": MR_IID,
        "remote_branch_deleted": False,
        "source_sha": sha,
        "status": "ready",
        "worktree": str(worktree),
    }
    assert worktree.is_dir()
    assert (
        git(repo, "show-ref", "--verify", "refs/heads/feature/cleanup").returncode
        == 0
    )


def test_execute_removes_squash_merged_worktree_and_local_branch(
    tmp_path: Path,
) -> None:
    repo, worktree, _, _, env = setup_repo(tmp_path)
    assert (
        git(
            repo,
            "merge-base",
            "--is-ancestor",
            "feature/cleanup",
            "main",
            check=False,
        ).returncode
        == 1
    )

    result = run_cleanup(worktree, env, "--execute")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "cleaned"
    assert not worktree.exists()
    assert (
        git(
            repo,
            "show-ref",
            "--verify",
            "refs/heads/feature/cleanup",
            check=False,
        ).returncode
        != 0
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"state": "opened", "merged_at": None}, "MR is not merged"),
        ({"source_branch": "feature/other"}, "MR source branch does not match"),
        ({"sha": "0" * 40}, "local branch HEAD does not match"),
        ({"target_branch": None}, "target branch is missing or invalid"),
        ({"target_branch": "staging"}, "not the repository default branch"),
    ],
)
def test_remote_evidence_mismatch_blocks_without_cleanup(
    tmp_path: Path,
    changes: dict[str, object],
    reason: str,
) -> None:
    repo, worktree, _, response, env = setup_repo(tmp_path)
    response.update(changes)
    update_response(env, response)

    result = run_cleanup(worktree, env, "--execute")

    assert reason in blocked_reason(result)
    assert worktree.is_dir()
    assert (
        git(repo, "show-ref", "--verify", "refs/heads/feature/cleanup").returncode
        == 0
    )


def test_dirty_worktree_blocks_before_api_cleanup(tmp_path: Path) -> None:
    repo, worktree, _, _, env = setup_repo(tmp_path)
    write(worktree, "untracked.txt", "keep me\n")

    result = run_cleanup(worktree, env, "--execute")

    assert "tracked, untracked, or ignored files" in blocked_reason(result)
    assert worktree.is_dir()
    assert (
        git(repo, "show-ref", "--verify", "refs/heads/feature/cleanup").returncode
        == 0
    )


def test_ignored_file_blocks_without_cleanup(tmp_path: Path) -> None:
    repo, worktree, _, response, env = setup_repo(tmp_path)
    write(worktree, ".gitignore", ".env.local\n")
    git(worktree, "add", ".gitignore")
    git(worktree, "commit", "-m", "ignore local env")
    response["sha"] = git(worktree, "rev-parse", "HEAD").stdout.strip()
    update_response(env, response)
    write(worktree, ".env.local", "local-only-data\n")

    result = run_cleanup(worktree, env, "--execute")

    assert "tracked, untracked, or ignored files" in blocked_reason(result)
    assert worktree.is_dir()
    assert (worktree / ".env.local").read_text(encoding="utf-8") == "local-only-data\n"
    assert (
        git(repo, "show-ref", "--verify", "refs/heads/feature/cleanup").returncode
        == 0
    )


def test_missing_project_default_branch_blocks_without_cleanup(tmp_path: Path) -> None:
    repo, worktree, _, _, env = setup_repo(tmp_path)
    update_project_response(env, {})

    result = run_cleanup(worktree, env, "--execute")

    assert "project default branch is missing or invalid" in blocked_reason(result)
    assert worktree.is_dir()
    assert (
        git(repo, "show-ref", "--verify", "refs/heads/feature/cleanup").returncode
        == 0
    )


@pytest.mark.parametrize(
    ("failure_prefix", "expected_state", "branch_survives"),
    [
        (
            ["update-ref", "-d"],
            {
                "worktree_removed": True,
                "local_branch_deleted": False,
                "worktree_metadata_pruned": True,
            },
            True,
        ),
        (
            ["worktree", "prune"],
            {
                "worktree_removed": True,
                "local_branch_deleted": True,
                "worktree_metadata_pruned": True,
            },
            False,
        ),
    ],
)
def test_destructive_failure_reports_exact_partial_state(
    tmp_path: Path,
    failure_prefix: list[str],
    expected_state: dict[str, bool],
    branch_survives: bool,
) -> None:
    repo, worktree, _, _, env = setup_repo(tmp_path)
    env["FAKE_GIT_FAIL_PREFIX"] = json.dumps(failure_prefix)

    result = run_cleanup(worktree, env, "--execute")

    assert result.returncode == 3, result.stdout + result.stderr
    payload = json.loads(result.stderr)
    assert payload["status"] == "partial"
    assert payload["remote_branch_deleted"] is False
    for key, expected in expected_state.items():
        assert payload[key] is expected
    assert not worktree.exists()
    branch_result = git(
        repo,
        "show-ref",
        "--verify",
        "refs/heads/feature/cleanup",
        check=False,
    )
    assert (branch_result.returncode == 0) is branch_survives


def test_failure_after_worktree_removal_reports_observed_state(
    tmp_path: Path,
) -> None:
    repo, worktree, _, _, env = setup_repo(tmp_path)
    env["FAKE_GIT_FAIL_PREFIX"] = json.dumps(["worktree", "remove"])
    env["FAKE_GIT_FAIL_AFTER"] = "1"

    result = run_cleanup(worktree, env, "--execute")

    assert result.returncode == 3, result.stdout + result.stderr
    payload = json.loads(result.stderr)
    assert payload["status"] == "partial"
    assert payload["worktree_removed"] is True
    assert payload["local_branch_deleted"] is False
    assert payload["worktree_metadata_pruned"] is True
    assert not worktree.exists()
    assert (
        git(repo, "show-ref", "--verify", "refs/heads/feature/cleanup").returncode
        == 0
    )


def test_probe_failure_keeps_unknown_partial_fields(
    tmp_path: Path,
) -> None:
    _, worktree, _, _, env = setup_repo(tmp_path)
    env["FAKE_GIT_FAIL_PREFIX"] = json.dumps(["update-ref", "-d"])
    env["FAKE_GIT_PROBE_FAILURE"] = "1"

    result = run_cleanup(worktree, env, "--execute")

    assert result.returncode == 3, result.stdout + result.stderr
    payload = json.loads(result.stderr)
    assert payload["status"] == "partial"
    assert payload["worktree_removed"] is True
    assert payload["local_branch_deleted"] is None
    assert payload["worktree_metadata_pruned"] is None


def test_primary_checkout_is_never_a_cleanup_target(tmp_path: Path) -> None:
    repo, _, _, _, env = setup_repo(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--worktree",
            str(repo),
            "--branch",
            "main",
            "--mr-iid",
            str(MR_IID),
            "--execute",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert "linked worktree" in blocked_reason(result)
    assert repo.is_dir()
