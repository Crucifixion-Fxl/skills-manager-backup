"""Git 精确变更比较核心。"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from typing import Any


class InputError(RuntimeError):
    """输入的 Git ref 或发布契约不合法。"""


def run_git(
    args: list[str], data: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        input=data,
        capture_output=True,
        check=False,
    )


def git(args: list[str], data: bytes | None = None) -> bytes:
    result = run_git(args, data)
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise InputError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def resolve_ref(value: str) -> str:
    return git(["rev-parse", "--verify", f"{value}^{{commit}}"]).decode().strip()


def validate_full_sha(value: str, label: str) -> str:
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None:
        raise InputError(f"{label} must be a full lowercase commit SHA")
    return value


def require_full_commit_sha(value: str, label: str) -> str:
    validate_full_sha(value, label)
    resolved = resolve_ref(value)
    if resolved != value:
        raise InputError(f"{label} resolved to {resolved}, expected {value}")
    return resolved


def resolve_bound_ref(ref: str, expected_sha: str, label: str) -> str:
    expected = require_full_commit_sha(expected_sha, f"{label} expected SHA")
    resolved = resolve_ref(ref)
    if resolved != expected:
        raise InputError(f"{label} {ref} resolved to {resolved}, expected {expected}")
    return resolved


def resolve_remote_tracking_ref(ref: str, label: str) -> str:
    symbolic = git(["rev-parse", "--symbolic-full-name", ref]).decode().strip()
    if not symbolic.startswith("refs/remotes/"):
        raise InputError(
            f"{label} must be a fetched remote-tracking ref, got {symbolic or ref}"
        )
    return resolve_ref(ref)


def require_ancestor(base: str, head: str, label: str) -> None:
    result = run_git(["merge-base", "--is-ancestor", base, head])
    if result.returncode == 1:
        raise InputError(f"{label} base {base} is not an ancestor of head {head}")
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise InputError(f"cannot validate {label} ancestry: {detail}")


def changed_paths(base: str, head: str) -> set[str]:
    raw = git(["diff", "--name-only", "-z", "--no-renames", base, head])
    return {item.decode(errors="surrogateescape") for item in raw.split(b"\0") if item}


def tree_entry(ref: str, path: str) -> dict[str, str] | None:
    raw = git(["--literal-pathspecs", "ls-tree", "-z", ref, "--", path])
    records = [record for record in raw.split(b"\0") if record]
    if not records:
        return None
    if len(records) != 1 or b"\t" not in records[0]:
        raise InputError(f"cannot resolve exact tree entry {ref}:{path}")
    metadata, raw_path = records[0].split(b"\t", 1)
    resolved_path = raw_path.decode(errors="surrogateescape")
    if resolved_path != path:
        raise InputError(f"tree entry path mismatch for {ref}:{path}")
    fields = metadata.decode().split()
    if len(fields) != 3:
        raise InputError(f"invalid tree entry metadata for {ref}:{path}")
    mode, object_type, object_id = fields
    return {"mode": mode, "type": object_type, "oid": object_id}


def change_signature(base: str, head: str, path: str) -> dict[str, Any]:
    return {
        "before": tree_entry(base, path),
        "after": tree_entry(head, path),
    }


def validate_repo_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise InputError(f"{label} must be a non-empty string")
    if value.startswith("/") or "\\" in value or "\0" in value:
        raise InputError(f"{label} must be a repository-relative POSIX path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise InputError(f"{label} contains an invalid path segment")
    return value


def validate_pattern(value: Any, label: str) -> str:
    pattern = validate_repo_path(value, label)
    first_segment = pattern.split("/", 1)[0]
    if any(token in first_segment for token in ("*", "?", "[")):
        raise InputError(f"{label} must start with a literal top-level path segment")
    return pattern


def path_matches(path: str, pattern: str) -> bool:
    path_parts = path.split("/")
    pattern_parts = pattern.split("/")

    def match(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        segment = pattern_parts[pattern_index]
        if segment == "**":
            return match(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and match(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], segment)
            and match(path_index + 1, pattern_index + 1)
        )

    return match(0, 0)


def reject_unknown_keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise InputError(f"{label} contains unknown keys: {', '.join(unknown)}")


def matching_rule(path: str, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [
        rule
        for rule in rules
        if any(path_matches(path, pattern) for pattern in rule["patterns"])
    ]
    if len(matches) > 1:
        indexes = ", ".join(str(rule["index"]) for rule in matches)
        raise InputError(f"path {path!r} matches multiple allowed rules: {indexes}")
    return matches[0] if matches else None


def difference_kind(
    canonical_signature: tuple[str, str, str] | None,
    candidate_signature: tuple[str, str, str] | None,
) -> str:
    if canonical_signature is None:
        return "extra-in-candidate"
    if candidate_signature is None:
        return "missing-from-candidate"
    return "change-differs"


def compare(
    canonical_base: str,
    canonical_head: str,
    candidate_base: str,
    candidate_head: str,
    rules: list[dict[str, Any]],
    control_paths: set[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    canonical_paths = changed_paths(canonical_base, canonical_head)
    candidate_paths = changed_paths(candidate_base, candidate_head)
    for path in control_paths or set():
        canonical_paths.discard(path)
        candidate_paths.discard(path)
    if not canonical_paths:
        raise InputError("canonical change is empty")
    matched: list[str] = []
    blocked: list[dict[str, Any]] = []
    allowed: list[dict[str, Any]] = []
    for path in sorted(canonical_paths | candidate_paths):
        canonical_signature = (
            change_signature(canonical_base, canonical_head, path)
            if path in canonical_paths
            else None
        )
        candidate_signature = (
            change_signature(candidate_base, candidate_head, path)
            if path in candidate_paths
            else None
        )
        if (
            canonical_signature is not None
            and canonical_signature == candidate_signature
        ):
            matched.append(path)
            continue
        kind = difference_kind(canonical_signature, candidate_signature)
        item = {
            "path": path,
            "kind": kind,
            "canonical_signature": canonical_signature,
            "candidate_signature": candidate_signature,
        }
        rule = matching_rule(path, rules)
        if rule is None:
            blocked.append(item)
            continue
        rule["matched"].append(path)
        allowed.append(
            {
                **item,
                "rule_index": rule["index"],
                "reason": rule["reason"],
                "owner": rule["owner"],
                "expires": rule["expires"],
            }
        )
    unused = [
        f"allowed_differences[{rule['index']}] matched no actual difference"
        for rule in rules
        if not rule["matched"]
    ]
    return matched, blocked, allowed, unused
