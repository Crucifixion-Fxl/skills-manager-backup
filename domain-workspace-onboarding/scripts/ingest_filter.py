#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml>=6.0"]
# ///
"""Classify legacy material before any domain workspace ingestion."""

# Structured output is JSON produced with json.dumps by common.emit.

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from common import emit, load_yaml

ALLOWED = {
    "durable-domain-context",
    "repeatable-cross-repository-workflow",
    "accepted-cross-repository-decision",
    "authoritative-source-pointer",
    "exact-sha-snapshot",
}
SOURCE_OWNED = {"source-repository-detail"}
FORBIDDEN = {
    "personal-material",
    "issue-mr-worktree",
    "checkout-state",
    "sql",
    "log",
    "secret",
    "credential",
    "screenshot",
    "exported-comment",
    "one-off-evidence",
    "temporary-project",
    "product-source",
}
SENSITIVE_MARKERS = {
    "secret",
    "secrets",
    "credential",
    "credentials",
    "token",
    "tokens",
    "password",
    "passwords",
    "privatekey",
    "privatekeys",
    "apikey",
    "apikeys",
    "private-key",
    "private-keys",
    "private_key",
    "private_keys",
    "api-key",
    "api-keys",
    "api_key",
    "api_keys",
}
TRANSIENT_SEGMENTS = {".git", ".worktree", ".worktrees", "worktree", "worktrees", "log", "logs"}
PATH_MARKER_DELIMITERS = "/-_."
FORBIDDEN_SUFFIXES = {
    ".env",
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".sql",
    ".log",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
}
SECRET_CONTENT = re.compile(
    r"(?im)(?:password|secret|token|api[-_]?key|private[-_]?key)\s*[:=]\s*['\"]?[^\s'\"]{6,}"
)
PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


def _contains_delimited_marker(value: str, marker: str) -> bool:
    start = 0
    while True:
        index = value.find(marker, start)
        if index < 0:
            return False
        end = index + len(marker)
        left_delimited = index == 0 or value[index - 1] in PATH_MARKER_DELIMITERS
        right_delimited = end == len(value) or value[end] in PATH_MARKER_DELIMITERS
        if left_delimited and right_delimited:
            return True
        start = index + 1


def _contains_sensitive_path_part(lowered: str) -> bool:
    return any(_contains_delimited_marker(lowered, marker) for marker in SENSITIVE_MARKERS)


def _is_issue_or_mr_segment(segment: str) -> bool:
    prefix = "issue" if segment.startswith("issue") else "mr"
    if not segment.startswith(prefix):
        return False
    suffix = segment[len(prefix):]
    if suffix[:1] in {"-", "_"}:
        suffix = suffix[1:]
    return bool(suffix) and all(character.isdecimal() for character in suffix)


def _contains_transient_path_part(lowered: str) -> bool:
    for segment in lowered.split("/"):
        if segment in TRANSIENT_SEGMENTS:
            return True
        if _is_issue_or_mr_segment(segment):
            return True
    return False


def path_disposition(path_value: str) -> str | None:
    normalized = path_value.strip().replace("\\", "/")
    path = Path(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        return "unsafe-path"
    lowered = normalized.lower()
    name = path.name.lower()
    if name == ".env" or name.startswith(".env.") or any(
        name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES
    ):
        return "sensitive-or-transient-path"
    if _contains_sensitive_path_part(lowered) or _contains_transient_path_part(lowered):
        return "sensitive-or-transient-path"
    return None


def inspect_allowed_file(source_root: Path, path_value: str) -> tuple[str | None, str | None]:
    relative = Path(path_value)
    unresolved = source_root / relative
    current = source_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return "symlink-content-not-ingestable", None
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(source_root)
    except ValueError:
        return "unsafe-path", None
    if not candidate.is_file():
        return "allowed-item-must-be-a-regular-file", None
    try:
        raw = candidate.read_bytes()
    except OSError:
        return "allowed-item-unreadable", None
    if len(raw) > 1_000_000:
        return "allowed-item-exceeds-1mb", None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "binary-content-not-ingestable", None
    if SECRET_CONTENT.search(text) or PRIVATE_KEY_HEADER.search(text):
        return "secret-like-content", None
    return None, hashlib.sha256(raw).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Produce a fail-closed include/exclude plan for legacy workspace material."
    )
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    return parser.parse_args()


def _inventory_items(
    inventory: dict, errors: list[str]
) -> tuple[dict, list[dict]]:
    if inventory.get("schema_version") != 1:
        errors.append("inventory schema_version must be 1")
    owner = inventory.get("owner_agent") or {}
    if owner.get("independent") is not True or not str(owner.get("conversation_id") or "").strip():
        errors.append("an independent Owner Agent conversation is required")
    items = inventory.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items must be a non-empty array")
        items = []
    return owner, items


def _classify_item(source_root: Path, item: dict) -> dict:
    path_reason = path_disposition(str(item["path"]))
    category = item.get("category")
    if path_reason:
        return {**item, "disposition": "exclude", "reason": path_reason}
    if category in ALLOWED:
        content_reason, source_sha256 = inspect_allowed_file(
            source_root, str(item["path"])
        )
        if content_reason:
            return {**item, "disposition": "exclude", "reason": content_reason}
        return {
            **item,
            "disposition": "distill-not-copy",
            "source_sha256": source_sha256,
        }
    if category in SOURCE_OWNED:
        return {**item, "disposition": "source-repository-docs"}
    reason = "forbidden-local-artifact" if category in FORBIDDEN else "unknown-category-fail-closed"
    return {**item, "disposition": "exclude", "reason": reason}


def main() -> None:
    args = parse_args()
    inventory = load_yaml(args.inventory.resolve())
    source_root = args.source_root.resolve()
    if not source_root.is_dir():
        emit(
            {
                "ingestion_valid": False,
                "workspace_eligible": False,
                "errors": ["source-root must be an existing directory"],
            },
            1,
        )
    errors: list[str] = []
    owner, items = _inventory_items(inventory, errors)
    included = []
    excluded = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not str(item.get("path") or "").strip():
            errors.append(f"items[{index}].path is required")
            continue
        classified = _classify_item(source_root, item)
        target = included if classified["disposition"] == "distill-not-copy" else excluded
        target.append(classified)

    if errors:
        emit({"ingestion_valid": False, "workspace_eligible": False, "errors": errors}, 1)
    emit(
        {
            "ingestion_valid": True,
            "workspace_eligible": bool(included),
            "included": included,
            "excluded": excluded,
            "bulk_copy_authorized": False,
            "owner_agent_conversation": owner["conversation_id"],
            "next_route": "workspace-ingest" if included else "source-repository-docs-or-no-workspace",
        }
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        emit({"ingestion_valid": False, "workspace_eligible": False, "errors": [str(exc)]}, 1)
