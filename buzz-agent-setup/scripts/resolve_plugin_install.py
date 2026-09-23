#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Resolve one Agent's exact installed plugin revision from its runtime registry.

The result is a non-secret validation receipt. Scanning a cache directory is
intentionally unsupported because the newest directory is not necessarily the
revision selected by the running harness.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Sequence


SHA40 = re.compile(r"[0-9a-f]{40}")
SKILL_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
PATH_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


def _regular_non_symlink(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file: {path}")


def _frontmatter_name(path: Path) -> str | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return None


def _validate_install_root(install: Path, required_skills: Sequence[str]) -> list[str]:
    if not install.is_absolute():
        raise ValueError("plugin installPath must be absolute")
    if install.is_symlink():
        raise ValueError("plugin installPath must not be a symlink")
    try:
        resolved = install.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("registered plugin installPath does not exist") from exc
    if resolved != install or not install.is_dir():
        raise ValueError("plugin installPath must be a real directory without symlinks")

    checked: list[str] = []
    for skill_name in required_skills:
        if SKILL_NAME.fullmatch(skill_name) is None:
            raise ValueError(f"invalid Skill name: {skill_name}")
        skill_file = install / "skills" / skill_name / "SKILL.md"
        if not skill_file.is_file() or skill_file.is_symlink():
            raise ValueError(f"missing required Skill: {skill_name}")
        if _frontmatter_name(skill_file) != skill_name:
            raise ValueError(f"Skill frontmatter name mismatch: {skill_name}")
        checked.append(skill_name)
    return checked


def resolve_install(
    registry: Path, plugin_id: str, required_skills: Sequence[str]
) -> dict[str, Any]:
    _regular_non_symlink(registry, "plugin registry")
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("plugin registry is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("plugins"), dict):
        raise ValueError("plugin registry has no plugins object")
    records = payload["plugins"].get(plugin_id)
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError(f"expected exactly one installed record for {plugin_id}")
    record = records[0]
    if not isinstance(record, dict):
        raise ValueError("plugin install record must be an object")

    raw_path = record.get("installPath")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("plugin install record has no installPath")
    install = Path(raw_path)
    checked = _validate_install_root(install, required_skills)

    sha = record.get("gitCommitSha")
    if not isinstance(sha, str) or SHA40.fullmatch(sha) is None:
        raise ValueError("plugin gitCommitSha must be a full lowercase commit SHA")

    return {
        "plugin_id": plugin_id,
        "install_path": str(install),
        "git_commit_sha": sha,
        "version": record.get("version"),
        "scope": record.get("scope"),
        "required_skills": checked,
    }


def resolve_codex_install(
    plugin_listing: dict[str, Any],
    cache_root: Path,
    plugin_id: str,
    required_skills: Sequence[str],
) -> dict[str, Any]:
    records = plugin_listing.get("installed")
    if not isinstance(records, list):
        raise ValueError("Codex plugin list has no installed array")
    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("pluginId") == plugin_id
        and record.get("installed") is True
        and record.get("enabled") is True
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one enabled Codex install for {plugin_id}")
    record = matches[0]
    components = [record.get(key) for key in ("marketplaceName", "name", "version")]
    if any(
        not isinstance(value, str) or PATH_COMPONENT.fullmatch(value) is None
        for value in components
    ):
        raise ValueError("Codex plugin install has an invalid cache path component")
    marketplace, name, version = components
    install = cache_root / marketplace / name / version
    checked = _validate_install_root(install, required_skills)

    source_record = record.get("source")
    marketplace_record = record.get("marketplaceSource")
    if (
        not isinstance(source_record, dict)
        or source_record.get("source") != "local"
        or not isinstance(source_record.get("path"), str)
        or not isinstance(marketplace_record, dict)
        or marketplace_record.get("sourceType") != "git"
    ):
        raise ValueError("Codex install is not backed by one Git marketplace snapshot")
    source = Path(source_record["path"])
    _validate_install_root(source, required_skills)
    try:
        revision = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        tracked_status = subprocess.run(
            ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        ref_name = subprocess.run(
            ["git", "-C", str(source), "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise ValueError("Codex marketplace source has no readable git revision") from exc
    if SHA40.fullmatch(revision) is None:
        raise ValueError("Codex marketplace source has no full git revision")
    if tracked_status:
        raise ValueError("Codex marketplace snapshot has tracked changes")
    for skill_name in checked:
        installed_skill = install / "skills" / skill_name / "SKILL.md"
        source_skill = source / "skills" / skill_name / "SKILL.md"
        if installed_skill.read_bytes() != source_skill.read_bytes():
            raise ValueError(
                f"installed Skill differs from marketplace snapshot: {skill_name}"
            )

    metadata_path = install / ".codex-marketplace-install.json"
    if metadata_path.exists() or metadata_path.is_symlink():
        _regular_non_symlink(metadata_path, "Codex marketplace install metadata")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Codex marketplace install metadata is invalid") from exc
        if (
            not isinstance(metadata, dict)
            or metadata.get("source_type") != "git"
            or metadata.get("revision") != revision
        ):
            raise ValueError("Codex install metadata disagrees with marketplace revision")

    return {
        "plugin_id": plugin_id,
        "install_path": str(install),
        "git_commit_sha": revision,
        "version": version,
        "scope": "user",
        "ref_name": ref_name,
        "source": marketplace_record.get("source"),
        "required_skills": checked,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--require-skill", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = resolve_install(args.registry, args.plugin_id, args.require_skill)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **receipt}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
