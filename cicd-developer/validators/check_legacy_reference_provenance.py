#!/usr/bin/env python3
"""Prove a legacy target reference from a fresh, config-isolated Git object DB."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from _legacy_target_common import is_stable_evidence_ref
from check_dormant_target import validate_kustomize_dependency_closure


MAX_COMMAND_OUTPUT = 16 * 1024 * 1024
MAX_API_BYTES = 1024 * 1024
MISSING_REQUESTS_MESSAGE = "FAIL: required HTTP client dependency is unavailable"
FULL_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
RENDER_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_BRANCH = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,126}[A-Za-z0-9])?$")
SAFE_OVERLAY_PART = re.compile(r"^[A-Za-z0-9._-]+$")


class ControlledArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.exit(2, "FAIL: invalid command-line arguments\n")


def _parser() -> argparse.ArgumentParser:
    parser = ControlledArgumentParser(
        description="Verify protected reference provenance and exact render digest."
    )
    parser.add_argument("--expected-origin", required=True)
    parser.add_argument("--protected-branch", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--expected-render-digest", required=True)
    parser.add_argument("--provenance", choices=("ancestor", "merged-mr"), required=True)
    parser.add_argument("--merged-mr-ref")
    parser.add_argument("--gitlab-token-env", default="GITLAB_TOKEN")
    parser.add_argument("--git", default="git")
    parser.add_argument("--kubectl", default="kubectl")
    return parser


def _origin_project(origin: str) -> str | None:
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "gitlab.addx.ai"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith(".git")
    ):
        return None
    project = parsed.path.removeprefix("/").removesuffix(".git")
    parts = project.split("/")
    if len(parts) < 2 or any(not SAFE_OVERLAY_PART.fullmatch(part) for part in parts):
        return None
    return project


def _safe_overlay(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and len(path.parts) >= 2
        and all(part not in {"", ".", ".."} and SAFE_OVERLAY_PART.fullmatch(part) for part in path.parts)
    )


def _isolated_env(root: Path) -> dict[str, str]:
    home = root / "home"
    xdg = root / "xdg"
    home.mkdir(mode=0o700)
    xdg.mkdir(mode=0o700)
    global_config = root / "global.gitconfig"
    global_config.touch(mode=0o600)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(global_config),
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "4",
        "GIT_CONFIG_KEY_0": "protocol.file.allow",
        "GIT_CONFIG_VALUE_0": "never",
        "GIT_CONFIG_KEY_1": "core.hooksPath",
        "GIT_CONFIG_VALUE_1": "/dev/null",
        "GIT_CONFIG_KEY_2": "fetch.fsckObjects",
        "GIT_CONFIG_VALUE_2": "true",
        "GIT_CONFIG_KEY_3": "transfer.fsckObjects",
        "GIT_CONFIG_VALUE_3": "true",
    }
    askpass = os.environ.get("GIT_ASKPASS")
    if askpass:
        env["GIT_ASKPASS"] = askpass
    for name in ("SSL_CERT_FILE", "GIT_SSL_CAINFO"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def _run(
    command: list[str], *, env: dict[str, str], cwd: Path | None = None, timeout: int = 60
) -> tuple[int, bytes] | None:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if len(result.stdout) > MAX_COMMAND_OUTPUT or len(result.stderr) > MAX_COMMAND_OUTPUT:
        return None
    return result.returncode, result.stdout


def _git(
    binary: str,
    repository: Path,
    arguments: list[str],
    *,
    env: dict[str, str],
    timeout: int = 60,
) -> tuple[int, bytes] | None:
    return _run(
        [binary, "-C", str(repository), *arguments],
        env=env,
        timeout=timeout,
    )


def _tracked_paths(binary: str, repository: Path, env: dict[str, str]) -> tuple[str, ...] | None:
    result = _git(binary, repository, ["ls-files", "-z"], env=env)
    if result is None or result[0] != 0 or not result[1].endswith(b"\0"):
        return None
    try:
        paths = tuple(item.decode("utf-8") for item in result[1][:-1].split(b"\0"))
    except UnicodeDecodeError:
        return None
    return paths if paths and len(set(paths)) == len(paths) else None


def _api_get(session: requests.Session, url: str, token: str) -> dict[str, Any] | None:
    if requests is None:
        return None
    try:
        response = session.get(
            url,
            headers={"PRIVATE-TOKEN": token},
            timeout=(5, 15),
            allow_redirects=False,
            stream=True,
        )
        if response.status_code != 200:
            return None
        raw = bytearray()
        for chunk in response.iter_content(65_536):
            raw.extend(chunk)
            if len(raw) > MAX_API_BYTES:
                return None
        document = json.loads(raw)
    except (requests.RequestException, ValueError, MemoryError):
        return None
    return document if isinstance(document, dict) else None


def _mr_iid(reference: str, project: str) -> int | None:
    if not is_stable_evidence_ref(reference):
        return None
    parsed = urlsplit(reference)
    expected_prefix = f"/{project}/-/merge_requests/"
    if not parsed.path.startswith(expected_prefix):
        return None
    suffix = parsed.path.removeprefix(expected_prefix)
    return int(suffix) if suffix.isdigit() and int(suffix) > 0 else None


def _protected_branch_proof(
    *, session: requests.Session, project: str, branch: str, token: str, tip: str
) -> bool:
    endpoint = (
        "https://gitlab.addx.ai/api/v4/projects/"
        f"{quote(project, safe='')}/repository/branches/{quote(branch, safe='')}"
    )
    document = _api_get(session, endpoint, token)
    commit = document.get("commit") if isinstance(document, dict) else None
    return bool(
        document
        and document.get("name") == branch
        and document.get("protected") is True
        and isinstance(commit, dict)
        and commit.get("id") == tip
    )


def _merged_mr_proof(
    *,
    session: requests.Session,
    project: str,
    branch: str,
    revision: str,
    reference: str,
    token: str,
) -> str | None:
    iid = _mr_iid(reference, project)
    if iid is None:
        return None
    endpoint = (
        "https://gitlab.addx.ai/api/v4/projects/"
        f"{quote(project, safe='')}/merge_requests/{iid}"
    )
    document = _api_get(session, endpoint, token)
    if not document or document.get("state") != "merged" or document.get("target_branch") != branch:
        return None
    diff_refs = document.get("diff_refs")
    head_sha = diff_refs.get("head_sha") if isinstance(diff_refs, dict) else None
    if head_sha != revision:
        return None
    merged_commit = document.get("squash_commit_sha") or document.get("merge_commit_sha")
    return merged_commit if isinstance(merged_commit, str) and FULL_SHA.fullmatch(merged_commit) else None


def verify_provenance(args: argparse.Namespace) -> tuple[bool, str | None]:
    project = _origin_project(args.expected_origin)
    if (
        project is None
        or args.protected_branch != "staging"
        or not SAFE_BRANCH.fullmatch(args.protected_branch)
        or ".." in args.protected_branch
        or not FULL_SHA.fullmatch(args.revision)
        or not _safe_overlay(args.overlay)
        or (
            args.expected_render_digest != "compute"
            and not RENDER_DIGEST.fullmatch(args.expected_render_digest)
        )
        or (args.provenance == "merged-mr") != bool(args.merged_mr_ref)
    ):
        return False, None
    token = os.environ.get(args.gitlab_token_env)
    if not token:
        return False, None
    with tempfile.TemporaryDirectory(prefix="legacy-reference-proof-") as temporary:
        root = Path(temporary)
        repository = root / "repository"
        repository.mkdir(mode=0o700)
        env = _isolated_env(root)
        commands = (
            [args.git, "init", "--quiet", str(repository)],
            [args.git, "-C", str(repository), "remote", "add", "origin", args.expected_origin],
            [
                args.git,
                "-C",
                str(repository),
                "fetch",
                "--quiet",
                "--no-tags",
                "--no-recurse-submodules",
                "--filter=blob:none",
                "origin",
                f"+refs/heads/{args.protected_branch}:refs/remotes/origin/{args.protected_branch}",
            ],
        )
        for command in commands:
            result = _run(command, env=env, timeout=120)
            if result is None or result[0] != 0:
                return False, None
        remote = _git(args.git, repository, ["remote", "get-url", "origin"], env=env)
        tip_result = _git(
            args.git,
            repository,
            ["rev-parse", f"refs/remotes/origin/{args.protected_branch}^{{commit}}"],
            env=env,
        )
        if remote is None or tip_result is None or remote[0] or tip_result[0]:
            return False, None
        try:
            remote_url = remote[1].decode("utf-8").strip()
            tip = tip_result[1].decode("ascii").strip()
        except UnicodeDecodeError:
            return False, None
        if remote_url != args.expected_origin or not FULL_SHA.fullmatch(tip):
            return False, None
        session = requests.Session()
        session.trust_env = False
        if not _protected_branch_proof(
            session=session,
            project=project,
            branch=args.protected_branch,
            token=token,
            tip=tip,
        ):
            return False, None
        if args.provenance == "ancestor":
            ancestry = _git(
                args.git,
                repository,
                ["merge-base", "--is-ancestor", args.revision, tip],
                env=env,
            )
            if ancestry is None or ancestry[0] != 0:
                return False, None
        else:
            iid = _mr_iid(args.merged_mr_ref, project)
            merged_commit = _merged_mr_proof(
                session=session,
                project=project,
                branch=args.protected_branch,
                revision=args.revision,
                reference=args.merged_mr_ref,
                token=token,
            )
            if merged_commit is None or iid is None:
                return False, None
            mr_fetch = _git(
                args.git,
                repository,
                [
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    "--no-recurse-submodules",
                    "origin",
                    f"+refs/merge-requests/{iid}/head:refs/remotes/origin/mr-{iid}",
                ],
                env=env,
                timeout=120,
            )
            mr_head = _git(
                args.git,
                repository,
                ["rev-parse", f"refs/remotes/origin/mr-{iid}^{{commit}}"],
                env=env,
            )
            if (
                mr_fetch is None
                or mr_fetch[0] != 0
                or mr_head is None
                or mr_head[0] != 0
                or mr_head[1].decode("ascii", errors="ignore").strip()
                != args.revision
            ):
                return False, None
            ancestry = _git(
                args.git,
                repository,
                ["merge-base", "--is-ancestor", merged_commit, tip],
                env=env,
            )
            if ancestry is None or ancestry[0] != 0:
                return False, None
        checkout = _git(
            args.git,
            repository,
            ["-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", args.revision],
            env=env,
            timeout=120,
        )
        if checkout is None or checkout[0] != 0:
            return False, None
        checks = (
            (["rev-parse", "HEAD"], args.revision.encode("ascii")),
            (["status", "--porcelain=v1", "--untracked-files=all"], b""),
            (["replace", "-l"], b""),
        )
        for command, expected in checks:
            result = _git(args.git, repository, command, env=env)
            if result is None or result[0] != 0 or result[1].strip() != expected:
                return False, None
        tracked_paths = _tracked_paths(args.git, repository, env)
        if tracked_paths is None or validate_kustomize_dependency_closure(
            repository_root=str(repository),
            overlay=args.overlay,
            tracked_paths=tracked_paths,
        ):
            return False, None
        render_env = {
            "PATH": env["PATH"],
            "HOME": env["HOME"],
            "XDG_CONFIG_HOME": env["XDG_CONFIG_HOME"],
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": env["GIT_CONFIG_GLOBAL"],
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "KUBECONFIG": "/dev/null",
        }
        rendered = _run(
            [
                args.kubectl,
                "kustomize",
                "--load-restrictor=LoadRestrictionsRootOnly",
                args.overlay,
            ],
            cwd=repository,
            env=render_env,
            timeout=120,
        )
        if rendered is None or rendered[0] != 0 or not rendered[1]:
            return False, None
        digest = "sha256:" + hashlib.sha256(rendered[1]).hexdigest()
        if args.expected_render_digest != "compute" and digest != args.expected_render_digest:
            return False, None
        return True, digest


def main(argv: list[str]) -> int:
    if requests is None:
        print(MISSING_REQUESTS_MESSAGE)
        return 2
    try:
        args = _parser().parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code)
    try:
        passed, digest = verify_provenance(args)
    except (RecursionError, ValueError, OverflowError, MemoryError):
        print("FAIL: reference provenance validation could not complete safely")
        return 2
    if not passed or digest is None:
        print("FAIL: protected reference provenance or exact render digest is invalid")
        return 1
    print(f"PASS: protected reference provenance binds the detached revision to {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
