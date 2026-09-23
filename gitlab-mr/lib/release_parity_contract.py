"""严格解析并校验生产晋级发布契约。"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from typing import Any

import yaml
from release_parity_core import (
    InputError,
    git,
    path_matches,
    reject_unknown_keys,
    tree_entry,
    validate_pattern,
    validate_repo_path,
)
from release_parity_schema import ALLOW_RULE_KEYS, CONTENT_CHECK_KEYS
from yaml.constructor import ConstructorError


class UniqueKeyLoader(yaml.SafeLoader):
    """拒绝包含重复 key 的 YAML。"""


def construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_unique_mapping,
)


def _non_empty_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise InputError(f"{label} must contain non-empty strings")
    return value


def _validated_sha256(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise InputError(f"{label} must be 64 lowercase hex characters")
    return value


def _content_shape(
    check: dict[str, Any],
    item_label: str,
    *,
    absent: bool,
    contains: list[str],
    excludes: list[str],
    expected_sha256: str | None,
) -> tuple[Any, Any]:
    object_type = check.get("object_type")
    mode = check.get("mode")
    if absent:
        if (
            contains
            or excludes
            or expected_sha256
            or "object_type" in check
            or "mode" in check
        ):
            raise InputError(f"{item_label} cannot mix absent with content assertions")
        return object_type, mode
    if not contains and not excludes and expected_sha256 is None:
        raise InputError(f"{item_label} has no assertion")
    object_type = object_type or "blob"
    mode = mode or "100644"
    if object_type != "blob":
        raise InputError(f"{item_label}.object_type must be 'blob'")
    if mode not in {"100644", "100755"}:
        raise InputError(f"{item_label}.mode must be '100644' or '100755'")
    return object_type, mode


def _parse_content_check(check: Any, item_label: str) -> dict[str, Any]:
    if not isinstance(check, dict):
        raise InputError(f"{item_label} must be a mapping")
    reject_unknown_keys(check, CONTENT_CHECK_KEYS, item_label)
    ref_name = check.get("ref", "candidate")
    if ref_name not in {"canonical", "candidate"}:
        raise InputError(f"{item_label}.ref is invalid")
    path = validate_repo_path(check.get("path"), f"{item_label}.path")
    absent = check.get("absent", False)
    if not isinstance(absent, bool):
        raise InputError(f"{item_label}.absent must be boolean")
    contains = _non_empty_strings(
        check.get("contains", []),
        f"{item_label}.contains",
    )
    excludes = _non_empty_strings(
        check.get("not_contains", []),
        f"{item_label}.not_contains",
    )
    expected_sha256 = _validated_sha256(
        check.get("sha256"),
        f"{item_label}.sha256",
    )
    object_type, mode = _content_shape(
        check,
        item_label,
        absent=absent,
        contains=contains,
        excludes=excludes,
        expected_sha256=expected_sha256,
    )
    return {
        "ref": ref_name,
        "path": path,
        "contains": contains,
        "not_contains": excludes,
        "sha256": expected_sha256,
        "absent": absent,
        "object_type": object_type,
        "mode": mode,
    }


def parse_content_checks(
    value: Any, label: str, required: bool
) -> list[dict[str, Any]]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        suffix = "non-empty " if required else ""
        raise InputError(f"{label} must be a {suffix}list")
    return [
        _parse_content_check(check, f"{label}[{index}]")
        for index, check in enumerate(value)
    ]


def load_contract(
    contract_path: str | None,
    candidate_ref: str,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    if contract_path is None:
        return {}, None
    contract_path = validate_repo_path(contract_path, "contract path")
    if not contract_path.startswith("release-contracts/") or not contract_path.endswith(
        (".yaml", ".yml")
    ):
        raise InputError(
            "contract path must be release-contracts/<feature>.yaml or .yml"
        )
    entry = tree_entry(candidate_ref, contract_path)
    if entry is None:
        raise InputError(
            f"contract {contract_path} is absent from candidate commit {candidate_ref}"
        )
    if entry["type"] != "blob" or entry["mode"] != "100644":
        raise InputError(
            f"contract {contract_path} must be a regular 100644 blob, "
            f"got {entry['type']}/{entry['mode']}"
        )
    raw_content = git(["cat-file", "-p", entry["oid"]])
    try:
        text = raw_content.decode("utf-8")
        result = yaml.load(
            text,
            Loader=UniqueKeyLoader,
        )
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise InputError(f"cannot read contract {contract_path}: {exc}") from exc
    if not isinstance(result, dict):
        raise InputError("release contract must be a YAML mapping")
    reject_unknown_keys(
        result,
        {
            "version",
            "feature",
            "allowed_differences",
            "required_content",
            "external_gates",
        },
        "release contract",
    )
    if type(result.get("version")) is not int or result["version"] != 1:
        raise InputError("release contract version must be integer 1")
    if not isinstance(result.get("feature"), str) or not result["feature"].strip():
        raise InputError("release contract feature is required")
    metadata = {
        "path": contract_path,
        "mode": entry["mode"],
        "type": entry["type"],
        "oid": entry["oid"],
        "sha256": hashlib.sha256(raw_content).hexdigest(),
    }
    return result, metadata


def _expiry(rule: dict[str, Any], label: str, today: date) -> str | None:
    temporary = rule.get("temporary", False)
    if not isinstance(temporary, bool):
        raise InputError(f"{label}.temporary must be boolean")
    expires = rule.get("expires")
    if expires is None:
        if temporary:
            raise InputError(f"{label}.expires is required for temporary rules")
        return None
    if not isinstance(expires, str):
        raise InputError(f"{label}.expires must be a quoted YYYY-MM-DD string")
    try:
        expiry_date = date.fromisoformat(expires)
    except ValueError as exc:
        raise InputError(f"{label}.expires must use YYYY-MM-DD") from exc
    if expiry_date <= today:
        raise InputError(
            f"{label}.expires {expires} is not later than current UTC date {today}"
        )
    return expires


def _validated_patterns(rule: dict[str, Any], label: str) -> list[str]:
    patterns = rule.get("paths")
    if not isinstance(patterns, list) or not patterns:
        raise InputError(f"{label}.paths must be non-empty")
    return [
        validate_pattern(item, f"{label}.paths[{index}]")
        for index, item in enumerate(patterns)
    ]


def _validate_check_paths(
    checks: list[dict[str, Any]],
    patterns: list[str],
    label: str,
) -> None:
    for check in checks:
        if not any(path_matches(check["path"], pattern) for pattern in patterns):
            raise InputError(
                f"{label}.required_content path {check['path']!r} "
                "is outside the rule paths"
            )


def _parse_allow_rule(
    rule: Any,
    index: int,
    today: date,
) -> dict[str, Any]:
    label = f"allowed_differences[{index}]"
    if not isinstance(rule, dict):
        raise InputError(f"{label} must be a mapping")
    reject_unknown_keys(rule, ALLOW_RULE_KEYS, label)
    patterns = _validated_patterns(rule, label)
    for field in ("reason", "owner"):
        if not isinstance(rule.get(field), str) or not rule[field].strip():
            raise InputError(f"{label}.{field} is required")
    expires = _expiry(rule, label, today)
    checks = parse_content_checks(
        rule.get("required_content"),
        f"{label}.required_content",
        required=True,
    )
    _validate_check_paths(checks, patterns, label)
    return {
        "index": index,
        "patterns": patterns,
        "reason": rule["reason"],
        "owner": rule["owner"],
        "expires": expires,
        "checks": checks,
        "matched": [],
    }


def allow_rules(
    contract: dict[str, Any],
    today: date | None = None,
) -> list[dict[str, Any]]:
    today = today or datetime.now(UTC).date()
    rules = contract.get("allowed_differences", [])
    if not isinstance(rules, list):
        raise InputError("allowed_differences must be a list")
    return [_parse_allow_rule(rule, index, today) for index, rule in enumerate(rules)]
