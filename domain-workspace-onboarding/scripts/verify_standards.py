#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml>=6.0"]
# ///
"""Verify the pinned Domain Workspaces Standards baseline and interfaces."""

# Structured output is JSON produced with json.dumps by common.emit.

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import subprocess
from pathlib import Path

from common import (
    BASELINE_SHA,
    REQUIRED_STANDARDS_PATHS,
    TRUSTED_STANDARDS_REPOSITORY_SHA256,
    canonical_git_url,
    commit_exists,
    emit,
    git_blob,
    is_ancestor,
    remote_git_env,
    run_git,
    safe_git_command,
)

ALLOWED_LOCAL_GIT_CONFIG = {
    "core": {
        "repositoryformatversion",
        "filemode",
        "bare",
        "logallrefupdates",
        "ignorecase",
        "precomposeunicode",
    },
    "remote": {"url", "fetch", "tagopt", "promisor", "partialclonefilter"},
    "branch": {"remote", "merge", "description"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail closed unless standards contains the pinned baseline or a compatible descendant."
    )
    parser.add_argument("--standards-root", required=True, type=Path)
    parser.add_argument("--remote", action="store_true", help="Also verify origin/main without fetching.")
    return parser.parse_args()


def _git_config_path(root: Path) -> Path:
    marker = root / ".git"
    if marker.is_symlink():
        raise ValueError("standards .git marker must not be a symlink")
    if marker.is_dir():
        config = marker / "config"
    elif marker.is_file():
        raise ValueError("standards linked worktrees are not allowed")
    else:
        raise ValueError("standards checkout has no readable .git metadata")
    if config.is_symlink() or not config.is_file():
        raise ValueError("standards Git config must be a regular file")
    return config


def _local_git_config_errors(root: Path) -> list[str]:
    try:
        parser = configparser.RawConfigParser(
            interpolation=None, strict=False, allow_no_value=True
        )
        with _git_config_path(root).open(encoding="utf-8") as source:
            parser.read_file(source)
    except (OSError, configparser.Error, ValueError) as exc:
        return [f"cannot validate standards Git config: {exc}"]
    errors = []
    for section in parser.sections():
        family = section.split(' "', 1)[0].lower()
        allowed = ALLOWED_LOCAL_GIT_CONFIG.get(family, set())
        for key, _value in parser.items(section, raw=True):
            if key.lower() not in allowed:
                errors.append(f"standards Git config contains unsafe key: {family}.{key}")
    return errors


def _local_head(root: Path) -> str:
    try:
        return run_git(root, "rev-parse", "HEAD").stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        emit({"compatible": False, "errors": [f"cannot read standards Git state: {exc}"]}, 1)
    raise AssertionError("emit must terminate")


def _validate_local_state(root: Path, head: str, errors: list[str]) -> None:
    dirty = run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        check=False,
    ).stdout.strip()
    if dirty:
        errors.append("standards checkout is dirty; refuse to execute working-tree governance code")
    replacements = run_git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace",
        check=False,
    ).stdout.strip()
    if replacements:
        errors.append("standards checkout contains Git replace refs")
    if not commit_exists(root, BASELINE_SHA):
        errors.append(f"minimum baseline commit is absent: {BASELINE_SHA}")
    elif not is_ancestor(root, BASELINE_SHA, head):
        errors.append(f"local HEAD {head} is not a descendant of baseline {BASELINE_SHA}")


def _validated_origin(root: Path, errors: list[str]) -> tuple[str, bool]:
    origin = run_git(root, "remote", "get-url", "origin", check=False).stdout.strip()
    if not origin:
        errors.append("standards origin is missing")
        return origin, False
    try:
        identity = canonical_git_url(origin)
        identity_hash = hashlib.sha256(identity.encode()).hexdigest()
        if identity_hash != TRUSTED_STANDARDS_REPOSITORY_SHA256:
            errors.append("standards origin does not match the trusted repository identity")
            return origin, False
    except ValueError as exc:
        errors.append(str(exc))
        return origin, False
    return origin, True


def _validated_remote_head(
    root: Path,
    origin: str,
    origin_validated: bool,
    head: str,
    verify_remote: bool,
    errors: list[str],
) -> str | None:
    if not verify_remote:
        return None
    if not origin_validated:
        return None
    result = subprocess.run(
        safe_git_command(
            None,
            "ls-remote",
            origin,
            "refs/heads/main",
            credential_helper=os.environ.get(
                "DOMAIN_WORKSPACE_STANDARDS_GIT_CREDENTIAL_HELPER"
            ),
        ),
        check=False,
        capture_output=True,
        text=True,
        env=remote_git_env(),
    )
    rows = result.stdout.strip().split()
    if result.returncode != 0 or not rows:
        errors.append("cannot read standards origin/main")
        return None
    remote_head = rows[0]
    if not commit_exists(root, remote_head):
        errors.append(f"remote HEAD {remote_head} is not present locally; fetch and re-run")
    elif not is_ancestor(root, BASELINE_SHA, remote_head):
        errors.append(f"remote HEAD {remote_head} does not contain baseline {BASELINE_SHA}")
    if remote_head != head:
        errors.append(
            f"local HEAD {head} diverges from origin/main {remote_head}; "
            "update the standards checkout and re-run"
        )
    return remote_head


def _validate_interfaces(root: Path, head: str, errors: list[str]) -> bytes:
    missing = []
    for interface_path in REQUIRED_STANDARDS_PATHS:
        result = run_git(root, "cat-file", "-e", f"{head}:{interface_path}", check=False)
        if result.returncode:
            missing.append(interface_path)
    if missing:
        errors.append(f"missing standards interfaces in verified commit: {', '.join(missing)}")
        return b""
    try:
        schema_raw = git_blob(root, head, "schemas/workspace-proposal.schema.json")
        schema = json.loads(schema_raw)
        decisions = schema["properties"]["decision"]["properties"]["outcome"]["enum"]
        expected = ["new-workspace", "extend-existing", "source-repository-docs", "standards"]
        if decisions != expected:
            errors.append("proposal decision enum is incompatible with this skill")
        validator = git_blob(root, head, "scripts/governance-validate").decode()
        if "--proposal" not in validator or "--self-test" not in validator:
            errors.append("governance validator CLI is incompatible with this skill")
        return schema_raw
    except (OSError, KeyError, ValueError) as exc:
        errors.append(f"cannot verify standards interfaces: {exc}")
        return b""


def _result_payload(
    root: Path,
    origin: str,
    origin_validated: bool,
    head: str,
    remote_head: str | None,
    schema_raw: bytes,
    errors: list[str],
) -> dict:
    payload = {
        "baseline_sha": BASELINE_SHA,
        "compatible": not errors,
        "errors": errors,
        "local_head": head,
        "origin_identity_sha256": (
            hashlib.sha256(canonical_git_url(origin).encode()).hexdigest()
            if origin_validated
            else None
        ),
        "remote_head": remote_head,
        "sop": str(root / "docs/workspace-onboarding.html"),
        "validator": str(root / "scripts/governance-validate"),
    }
    if schema_raw:
        payload["schema_sha256"] = hashlib.sha256(schema_raw).hexdigest()
    return payload


def main() -> None:
    args = parse_args()
    root = args.standards_root.resolve()
    errors = _local_git_config_errors(root)
    if errors:
        emit({"compatible": False, "errors": errors}, 1)
    head = _local_head(root)
    _validate_local_state(root, head, errors)
    origin, origin_validated = _validated_origin(root, errors)
    remote_head = _validated_remote_head(
        root, origin, origin_validated, head, args.remote, errors
    )
    schema_raw = _validate_interfaces(root, head, errors)
    emit(
        _result_payload(
            root,
            origin,
            origin_validated,
            head,
            remote_head,
            schema_raw,
            errors,
        ),
        1 if errors else 0,
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        emit({"compatible": False, "errors": [str(exc)]}, 1)
