#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validate and resolve the owner-controlled GitLab token map for a Buzz Agent.

The map contains identifiers and environment-variable *names* only.  Token
values are deliberately outside this file and are read from a 0600 runtime
environment by the controlled wrapper.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping


class ProjectTokenMapError(ValueError):
    """A project-token map is unsafe or does not match the strict schema."""


SAFE_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
PROJECT_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)+$")
TOKEN_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
SAFE_PROFILE = frozenset({"planner", "reporter", "developer"})
RESERVED_ENV = frozenset({
    "GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN", "PRIVATE_TOKEN",
    "JOB_TOKEN", "CI_JOB_TOKEN", "GITLAB_HOST", "GITLAB_API_HOST", "GITLAB_URI",
    "GITLAB_URL", "BUZZ_AGENT_NAME", "BUZZ_AGENT_PUBKEY", "BUZZ_AUTH_TAG",
    "BUZZ_ACP_CHANNELS",
})
TOP_LEVEL_KEYS = frozenset({"version", "host", "projects"})
PROJECT_KEYS = frozenset({"project_id", "project_path", "token_env", "profile"})


@dataclasses.dataclass(frozen=True)
class ProjectTokenEntry:
    project_id: int
    project_path: str
    token_env: str
    profile: str


@dataclasses.dataclass(frozen=True)
class ProjectTokenMap:
    version: int
    host: str
    projects: tuple[ProjectTokenEntry, ...]

    def by_id(self, project_id: int) -> ProjectTokenEntry:
        for entry in self.projects:
            if entry.project_id == project_id:
                return entry
        raise ProjectTokenMapError(f"project id {project_id} is not mapped")

    def by_path(self, project_path: str) -> ProjectTokenEntry:
        for entry in self.projects:
            if entry.project_path == project_path:
                return entry
        raise ProjectTokenMapError(f"project path {project_path!r} is not mapped")

    def select(self, *, project_id: int | None = None, project_path: str | None = None) -> ProjectTokenEntry:
        if (project_id is None) == (project_path is None):
            raise ProjectTokenMapError("select exactly one mapped project id or project path")
        return self.by_id(project_id) if project_id is not None else self.by_path(project_path or "")

    def token_values(self, environ: Mapping[str, str] | None = None) -> dict[str, str]:
        source = os.environ if environ is None else environ
        values: dict[str, str] = {}
        for entry in self.projects:
            value = source.get(entry.token_env)
            if not value or any(char.isspace() or ord(char) < 0x20 for char in value):
                raise ProjectTokenMapError(f"mapped token env {entry.token_env} is missing or malformed")
            values[entry.token_env] = value
        return values


def _secure_map_file(path: Path) -> str:
    if not path.is_absolute() or path.is_symlink():
        raise ProjectTokenMapError("mapping file must be an absolute non-symlink regular file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except (OSError, TypeError) as exc:
        raise ProjectTokenMapError("mapping file must be an absolute non-symlink regular file") from exc
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise ProjectTokenMapError("mapping file must be current-user-owned and mode 0600")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            return stream.read()
    except (OSError, UnicodeError) as exc:
        raise ProjectTokenMapError("mapping file could not be read securely") from exc
    finally:
        if fd not in {-1, None}:
            os.close(fd)


def _strict_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProjectTokenMapError(f"{field} must be a non-empty trimmed string")
    return value


def validate_mapping(raw: Mapping[str, Any]) -> ProjectTokenMap:
    if not isinstance(raw, Mapping):
        raise ProjectTokenMapError("mapping must be a JSON object")
    unknown = set(raw) - TOP_LEVEL_KEYS
    missing = TOP_LEVEL_KEYS - set(raw)
    if unknown or missing:
        raise ProjectTokenMapError(f"mapping keys must be exactly version, host, projects (unknown={sorted(unknown)}, missing={sorted(missing)})")
    if raw["version"] != 1 or isinstance(raw["version"], bool):
        raise ProjectTokenMapError("mapping version must be integer 1")
    host = _strict_string(raw["host"], "host").lower()
    if not SAFE_HOST.fullmatch(host) or "://" in host or "/" in host:
        raise ProjectTokenMapError("host must be a bare DNS name without scheme, path or port")
    projects = raw["projects"]
    if not isinstance(projects, list) or not projects:
        raise ProjectTokenMapError("projects must be a non-empty array")
    entries: list[ProjectTokenEntry] = []
    seen_ids: set[int] = set()
    seen_paths: set[str] = set()
    seen_env: set[str] = set()
    for index, item in enumerate(projects):
        if not isinstance(item, Mapping):
            raise ProjectTokenMapError(f"projects[{index}] must be an object")
        unknown_entry = set(item) - PROJECT_KEYS
        missing_entry = PROJECT_KEYS - set(item)
        if unknown_entry or missing_entry:
            raise ProjectTokenMapError(f"projects[{index}] keys are not exact (unknown={sorted(unknown_entry)}, missing={sorted(missing_entry)})")
        project_id = item["project_id"]
        if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
            raise ProjectTokenMapError(f"projects[{index}].project_id must be a positive integer")
        project_path = _strict_string(item["project_path"], f"projects[{index}].project_path")
        if not PROJECT_PATH.fullmatch(project_path):
            raise ProjectTokenMapError(f"projects[{index}].project_path is not a safe full GitLab path")
        token_env = _strict_string(item["token_env"], f"projects[{index}].token_env")
        if not TOKEN_ENV.fullmatch(token_env) or token_env in RESERVED_ENV or token_env.startswith("BUZZ_"):
            raise ProjectTokenMapError(f"projects[{index}].token_env is reserved or malformed")
        profile = _strict_string(item["profile"], f"projects[{index}].profile")
        if profile not in SAFE_PROFILE:
            raise ProjectTokenMapError(f"projects[{index}].profile is unsupported")
        if project_id in seen_ids:
            raise ProjectTokenMapError(f"duplicate project_id {project_id}")
        if project_path in seen_paths:
            raise ProjectTokenMapError(f"duplicate project_path {project_path}")
        if token_env in seen_env:
            raise ProjectTokenMapError(f"duplicate token_env {token_env}")
        seen_ids.add(project_id)
        seen_paths.add(project_path)
        seen_env.add(token_env)
        entries.append(ProjectTokenEntry(project_id, project_path, token_env, profile))
    # Sorting makes resolution and provisioning deterministic even if an owner
    # hand-edits the JSON order.
    entries.sort(key=lambda entry: entry.project_id)
    return ProjectTokenMap(version=1, host=host, projects=tuple(entries))


def load_mapping(path: str | Path) -> ProjectTokenMap:
    file_path = Path(path).expanduser()
    try:
        raw = json.loads(_secure_map_file(file_path))
    except json.JSONDecodeError as exc:
        raise ProjectTokenMapError("mapping file contains invalid JSON") from exc
    return validate_mapping(raw)


def mapping_to_public_dict(mapping: ProjectTokenMap) -> dict[str, Any]:
    """Return a non-secret, canonical representation for receipts/tests."""
    return {
        "version": mapping.version,
        "host": mapping.host,
        "projects": [dataclasses.asdict(entry) for entry in mapping.projects],
    }


def mapping_sha256(mapping: ProjectTokenMap) -> str:
    """Return the digest used to bind an L4 receipt to the exact owner map."""
    canonical = json.dumps(
        mapping_to_public_dict(mapping),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


if __name__ == "__main__":  # pragma: no cover - useful operator validation command
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mapping")
    args = parser.parse_args()
    try:
        value = load_mapping(args.mapping)
    except ProjectTokenMapError as exc:
        parser.error(str(exc))
    print(json.dumps(mapping_to_public_dict(value), ensure_ascii=False, indent=2))
