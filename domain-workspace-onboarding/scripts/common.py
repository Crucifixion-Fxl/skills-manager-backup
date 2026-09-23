#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml>=6.0"]
# ///
"""Shared, dependency-light helpers for domain workspace onboarding scripts."""

# Library module; executable entry points provide argparse --help.

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

BASELINE_SHA = "779ed7dd25627bda043774e0c63f210b7a284d36"
TRUSTED_STANDARDS_REPOSITORY_SHA256 = (
    "aa3e60293c1f7f0f3e25e8794088f50d7bcde73d9e089405a77bdc035aecee57"
)
ROLES = {"primary", "dependency", "related"}
DOMAIN_TYPES = {
    "stable-business-domain",
    "shared-platform-domain",
    "enablement-domain",
}
REQUIRED_STANDARDS_PATHS = (
    "docs/workspace-onboarding.html",
    "catalog/workspaces.yaml",
    "catalog/repository-references.yaml",
    "schemas/workspace-proposal.schema.json",
    "templates/workspace-proposal.yaml",
    "scripts/governance-validate",
    "scripts/workspace-validate",
)
MAX_SNAPSHOT_FILES = 5_000
MAX_SNAPSHOT_BLOB_BYTES = 10_000_000
MAX_SNAPSHOT_TOTAL_BYTES = 100_000_000
MAX_GIT_TREE_RECORD_BYTES = 100_000
MAX_GIT_TREE_TOTAL_RECORD_BYTES = 10_000_000
STREAM_CHUNK_BYTES = 65_536
VALIDATOR_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP")
GIT_ENV_KEYS = (*VALIDATOR_ENV_KEYS, "HOME")
INVALID_GIT_TREE_ENTRY = "invalid entry in standards Git tree"


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"YAML root must be a map: {path}")
    return value


def nonempty(value: Any) -> bool:
    return bool(str(value or "").strip())


def record_ready(record: Any, statuses: set[str]) -> bool:
    return (
        isinstance(record, dict)
        and record.get("status") in statuses
        and nonempty(record.get("evidence"))
    )


def validate_raw_observation(
    name: str,
    record: Any,
    errors: list[str],
    required_status: str,
    expected_observed_value: str | None = None,
) -> None:
    if not isinstance(record, dict) or record.get("status") != required_status:
        errors.append(f"{name} requires status={required_status}")
        return
    _validate_evidence_url(name, record, errors)
    _validate_observed_value(name, record, errors, expected_observed_value)
    _validate_evidence_file(name, record, errors)
    _validate_verified_at(name, record, errors)


def _validate_evidence_url(
    name: str, record: dict[str, Any], errors: list[str]
) -> None:
    evidence_url = str(record.get("evidence") or "")
    parsed = urlsplit(evidence_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
        errors.append(f"{name} evidence must be an auditable HTTPS URL")
    for field in ("observed_value", "verifier"):
        if not nonempty(record.get(field)):
            errors.append(f"{name}.{field} is required")


def _validate_observed_value(
    name: str,
    record: dict[str, Any],
    errors: list[str],
    expected_observed_value: str | None,
) -> None:
    if (
        expected_observed_value is not None
        and record.get("observed_value") != expected_observed_value
    ):
        errors.append(
            f"{name}.observed_value must equal {expected_observed_value!r}"
        )


def _validate_evidence_file(
    name: str, record: dict[str, Any], errors: list[str]
) -> None:
    evidence_path = Path(str(record.get("evidence_file") or ""))
    expected_hash = str(record.get("evidence_sha256") or "")
    if not evidence_path.is_absolute() or evidence_path.is_symlink() or not evidence_path.is_file():
        errors.append(f"{name}.evidence_file must be an absolute regular file")
    else:
        try:
            raw = evidence_path.read_bytes()
        except OSError as exc:
            errors.append(f"cannot read {name} evidence: {exc}")
        else:
            if len(raw) > 5_000_000:
                errors.append(f"{name} evidence exceeds 5 MB")
            if (
                len(expected_hash) != 64
                or any(character not in "0123456789abcdef" for character in expected_hash)
                or hashlib.sha256(raw).hexdigest() != expected_hash
            ):
                errors.append(f"{name}.evidence_sha256 does not match")
            observed = str(record.get("observed_value") or "").encode()
            if observed and observed not in raw:
                errors.append(f"{name}.observed_value is absent from raw evidence")


def _validate_verified_at(
    name: str, record: dict[str, Any], errors: list[str]
) -> None:
    try:
        verified_at = datetime.fromisoformat(str(record.get("verified_at")))
    except ValueError:
        errors.append(f"{name}.verified_at must be ISO-8601")
    else:
        now = datetime.now(UTC)
        if verified_at.tzinfo is None:
            errors.append(f"{name}.verified_at must include timezone")
        elif not now - timedelta(hours=24) <= verified_at.astimezone(UTC) <= now + timedelta(minutes=5):
            errors.append(f"{name}.verified_at is outside the current validation window")


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        safe_git_command(root, *args),
        check=check,
        capture_output=True,
        text=True,
        env=git_object_env(),
    )


def git_object_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in GIT_ENV_KEYS if key in os.environ}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def remote_git_env() -> dict[str, str]:
    env = git_object_env()
    for key in ("SSH_AUTH_SOCK", "CI_JOB_TOKEN"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def isolated_git_env() -> dict[str, str]:
    """Return a minimal environment that ignores ambient Git configuration."""
    env = {key: os.environ[key] for key in VALIDATOR_ENV_KEYS if key in os.environ}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def workspace_status_git_env(objects: Path) -> dict[str, str]:
    """Return a config-free environment for status against a sanitized Git dir."""
    env = isolated_git_env()
    env.update(
        {
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects),
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return env


def workspace_remote_git_env() -> dict[str, str]:
    """Return an isolated environment for an explicitly bound workspace remote."""
    env = isolated_git_env()
    env.update(
        {
            "GIT_ALLOW_PROTOCOL": "https:ssh",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_SSH_COMMAND": (
                "ssh -F /dev/null -oBatchMode=yes -oConnectTimeout=15 "
                "-oIdentityAgent=none -oIdentityFile=none -oIdentitiesOnly=yes"
            ),
        }
    )
    trusted_ssh_command = os.environ.get("DOMAIN_WORKSPACE_GIT_SSH_COMMAND")
    if trusted_ssh_command:
        env["GIT_SSH_COMMAND"] = trusted_ssh_command
        if "SSH_AUTH_SOCK" in os.environ:
            env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    return env


def safe_git_command(
    root: Path | None,
    *args: str,
    credential_helper: str | None = None,
) -> list[str]:
    command = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "credential.helper=",
    ]
    if credential_helper:
        command.extend(["-c", f"credential.helper={credential_helper}"])
    if root is not None:
        command.extend(["-C", str(root)])
    command.extend(args)
    return command


def trusted_validator_env() -> dict[str, str]:
    """Return a minimal environment without CI credentials for standards code."""
    env = {key: os.environ[key] for key in VALIDATOR_ENV_KEYS if key in os.environ}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def commit_exists(root: Path, sha: str) -> bool:
    result = run_git(root, "cat-file", "-e", f"{sha}^{{commit}}", check=False)
    return result.returncode == 0


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = run_git(root, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    return result.returncode == 0


def boundary_signature(boundary: dict[str, Any]) -> str | None:
    summary = str(boundary.get("summary") or "").strip()
    includes = boundary.get("includes")
    excludes = boundary.get("excludes")
    if not summary or not isinstance(includes, list) or not includes or not isinstance(excludes, list) or not excludes:
        return None
    values = [summary, *sorted(map(str, includes)), "--", *sorted(map(str, excludes))]
    return "|".join(" ".join(value.lower().split()) for value in values)


def governance_data(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = load_yaml(root / "catalog/workspaces.yaml")
    registry = load_yaml(root / "catalog/repository-references.yaml")
    return catalog, registry


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_git_url(value: str) -> str:
    """Return host/path identity for supported SSH and HTTPS Git remotes."""
    text = value.strip()
    if "://" not in text and "@" in text and ":" in text:
        user_host, path = text.split(":", 1)
        host = user_host.rsplit("@", 1)[-1]
    else:
        parsed = urlsplit(text)
        if parsed.scheme not in {"https", "ssh"} or not parsed.hostname:
            raise ValueError(f"unsupported Git remote URL: {value!r}")
        if parsed.password is not None:
            raise ValueError("Git remote URLs must not embed passwords or tokens")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"invalid Git remote URL: {value!r}") from exc
        default_port = 443 if parsed.scheme == "https" else 22
        host = parsed.hostname if port in {None, default_port} else f"{parsed.hostname}:{port}"
        path = parsed.path
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not host or not normalized_path or any(part in {"", ".", ".."} for part in normalized_path.split("/")):
        raise ValueError(f"invalid Git remote URL: {value!r}")
    return f"{host.lower()}/{normalized_path}"


def git_blob(root: Path, sha: str, path: str) -> bytes:
    result = subprocess.run(
        safe_git_command(root, "show", f"{sha}:{path}"),
        check=False,
        capture_output=True,
        env=git_object_env(),
    )
    if result.returncode:
        raise ValueError(f"cannot read {path} from standards commit {sha}")
    return result.stdout


def verify_standards_checkout(standards_root: Path, verifier: Path) -> tuple[dict[str, Any], list[str]]:
    result = subprocess.run(
        [
            sys.executable,
            str(verifier),
            "--standards-root",
            str(standards_root),
            "--remote",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"compatible": False, "errors": ["standards verifier returned invalid JSON"]}
    errors = []
    if result.returncode or payload.get("compatible") is not True:
        errors.append("trusted standards verification failed")
        errors.extend(str(error) for error in payload.get("errors") or [])
    return payload, errors


def _parse_git_tree_record(raw_record: bytes) -> tuple[str, str, int, str]:
    try:
        metadata, raw_name = raw_record.split(b"\t", 1)
        mode, object_type, object_id, raw_size = metadata.decode("ascii").split()
        name = raw_name.decode("utf-8")
    except ValueError as exc:
        raise ValueError(INVALID_GIT_TREE_ENTRY) from exc
    if object_type != "blob" or mode not in {"100644", "100755"}:
        raise ValueError(f"unsupported entry in standards Git tree: {name}")
    try:
        size = int(raw_size)
    except ValueError as exc:
        raise ValueError(INVALID_GIT_TREE_ENTRY) from exc
    return mode, object_id, size, name


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait()


def _updated_record_budget(separator: int, total_record_bytes: int) -> int:
    if separator > MAX_GIT_TREE_RECORD_BYTES:
        raise ValueError("standards Git tree entry exceeds path limit")
    updated = total_record_bytes + separator
    if updated > MAX_GIT_TREE_TOTAL_RECORD_BYTES:
        raise ValueError("standards Git tree exceeds path metadata limit")
    return updated


def _append_git_tree_entry(
    raw_record: bytes,
    entries: list[tuple[str, str, int, str]],
    total_bytes: int,
) -> int:
    if not raw_record:
        return total_bytes
    mode, object_id, size, name = _parse_git_tree_record(raw_record)
    if size > MAX_SNAPSHOT_BLOB_BYTES:
        raise ValueError(f"standards Git blob exceeds materialization limit: {name}")
    updated = total_bytes + size
    if updated > MAX_SNAPSHOT_TOTAL_BYTES:
        raise ValueError("standards Git tree exceeds materialization byte limit")
    entries.append((mode, object_id, size, name))
    if len(entries) > MAX_SNAPSHOT_FILES:
        raise ValueError("standards Git tree exceeds materialization file limit")
    return updated


def _git_tree_entries(root: Path, sha: str) -> list[tuple[str, str, int, str]]:
    process = subprocess.Popen(
        safe_git_command(root, "ls-tree", "-rlz", "--full-tree", sha),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=git_object_env(),
    )
    if process.stdout is None:
        _stop_process(process)
        raise ValueError(f"cannot read standards commit tree {sha}")
    entries: list[tuple[str, str, int, str]] = []
    total_bytes = 0
    total_record_bytes = 0
    pending = bytearray()
    try:
        while chunk := process.stdout.read(STREAM_CHUNK_BYTES):
            pending.extend(chunk)
            while (separator := pending.find(0)) >= 0:
                total_record_bytes = _updated_record_budget(
                    separator, total_record_bytes
                )
                raw_record = bytes(pending[:separator])
                del pending[: separator + 1]
                total_bytes = _append_git_tree_entry(
                    raw_record, entries, total_bytes
                )
            if len(pending) > MAX_GIT_TREE_RECORD_BYTES:
                raise ValueError("standards Git tree entry exceeds path limit")
        if pending:
            raise ValueError(INVALID_GIT_TREE_ENTRY)
        if process.wait():
            raise ValueError(f"cannot read standards commit tree {sha}")
        return entries
    finally:
        _stop_process(process)


def _copy_exact_blob(source: Any, target: Path, size: int, name: str) -> None:
    remaining = size
    with target.open("wb") as output:
        while remaining:
            chunk = source.read(min(STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                raise ValueError(f"incomplete standards Git blob response: {name}")
            output.write(chunk)
            remaining -= len(chunk)


def _write_git_blobs(
    root: Path, entries: list[tuple[str, str, int, str]], destination: Path
) -> None:
    process = subprocess.Popen(
        safe_git_command(root, "cat-file", "--batch"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=git_object_env(),
    )
    if process.stdin is None or process.stdout is None:
        _stop_process(process)
        raise ValueError("cannot read standards Git blobs")
    try:
        for mode, expected_object_id, expected_size, name in entries:
            process.stdin.write(f"{expected_object_id}\n".encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline(200)
            try:
                object_id, object_type, raw_size = header.decode("ascii").split()
                size = int(raw_size)
            except ValueError as exc:
                raise ValueError(f"invalid standards Git blob header: {name}") from exc
            if (
                not header.endswith(b"\n")
                or object_id != expected_object_id
                or object_type != "blob"
                or size != expected_size
            ):
                raise ValueError(
                    f"standards Git blob changed during materialization: {name}"
                )
            target = destination / Path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_exact_blob(process.stdout, target, size, name)
            if process.stdout.read(1) != b"\n":
                raise ValueError(
                    f"standards Git blob changed during materialization: {name}"
                )
            target.chmod(0o755 if mode == "100755" else 0o644)
        process.stdin.close()
        if process.stdout.read(1) or process.wait():
            raise ValueError("unexpected trailing data from standards Git blobs")
    finally:
        _stop_process(process)


def _validate_snapshot_names(entries: list[tuple[str, str, int, str]]) -> None:
    seen = set()
    for _mode, _object_id, _size, name in entries:
        relative = Path(name)
        if (
            "\\" in name
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("unsafe path in standards Git tree")
        identity = "/".join(
            unicodedata.normalize("NFC", part).casefold() for part in relative.parts
        )
        if identity in seen:
            raise ValueError(f"duplicate normalized path in standards Git tree: {name}")
        seen.add(identity)


@contextmanager
def materialize_git_commit(root: Path, sha: str):
    """Materialize only regular blobs from a pinned Git object tree."""
    entries = _git_tree_entries(root, sha)
    _validate_snapshot_names(entries)
    with tempfile.TemporaryDirectory(prefix="domain-workspace-standards-") as directory:
        destination = Path(directory)
        _write_git_blobs(root, entries, destination)
        yield destination


def emit(payload: dict[str, Any], exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(exit_code)
