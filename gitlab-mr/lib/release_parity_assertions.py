"""校验候选树内容断言与外部门禁声明。"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit

from release_parity_core import (
    InputError,
    git,
    reject_unknown_keys,
    tree_entry,
)


def coverage_failures(
    allowed: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    candidate_ref: str,
) -> list[str]:
    failures = []
    for difference in allowed:
        checks = [
            check
            for check in rules[difference["rule_index"]]["checks"]
            if check["ref"] == "candidate" and check["path"] == difference["path"]
        ]
        expects_absent = tree_entry(candidate_ref, difference["path"]) is None
        covered = (
            any(check["absent"] for check in checks)
            if expects_absent
            else any(not check["absent"] and check["sha256"] for check in checks)
        )
        if not covered:
            expectation = "absent: true" if expects_absent else "an exact sha256"
            failures.append(
                f"allowed difference {difference['path']!r} needs an exact candidate "
                f"required_content check with {expectation}"
            )
    return failures


def check_content(
    checks: list[dict[str, Any]],
    refs: dict[str, str],
) -> list[str]:
    failures = []
    for check in checks:
        ref_name, path = check["ref"], check["path"]
        entry = tree_entry(refs[ref_name], path)
        display = f"{ref_name}:{path}"
        if check["absent"]:
            if entry is not None:
                failures.append(f"{display} must be absent")
            continue
        if entry is None:
            failures.append(f"{display} cannot be read because it is absent")
            continue
        if entry["type"] != check["object_type"]:
            failures.append(
                f"{display} object type is {entry['type']}, "
                f"expected {check['object_type']}"
            )
            continue
        if entry["mode"] != check["mode"]:
            failures.append(
                f"{display} mode is {entry['mode']}, expected {check['mode']}"
            )
            continue
        raw_content = git(["cat-file", "-p", entry["oid"]])
        if check["sha256"] is not None:
            actual_sha256 = hashlib.sha256(raw_content).hexdigest()
            if actual_sha256 != check["sha256"]:
                failures.append(
                    f"{display} sha256 is {actual_sha256}, expected {check['sha256']}"
                )
        content = raw_content.decode(errors="replace")
        failures.extend(
            f"{display} is missing required content {item!r}"
            for item in check["contains"]
            if item not in content
        )
        failures.extend(
            f"{display} contains forbidden content {item!r}"
            for item in check["not_contains"]
            if item in content
        )
    return failures


def _validated_expected_content(
    value: Any,
    label: str,
    *,
    required: bool,
) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise InputError(f"{label}.expected_contains must contain non-empty strings")
    if required and not value:
        raise InputError(f"{label}.expected_contains is required")
    return value


def _validated_gate(
    gate: Any,
    index: int,
    seen_names: set[str],
) -> dict[str, Any]:
    label = f"external_gates[{index}]"
    if not isinstance(gate, dict):
        raise InputError(f"{label} must be a mapping")
    reject_unknown_keys(
        gate,
        {"name", "required", "evidence", "expected_contains"},
        label,
    )
    if not isinstance(gate.get("name"), str) or not gate["name"].strip():
        raise InputError(f"{label}.name is required")
    if gate["name"] in seen_names:
        raise InputError(f"{label}.name duplicates {gate['name']!r}")
    seen_names.add(gate["name"])

    required = gate.get("required", True)
    if not isinstance(required, bool):
        raise InputError(f"{label}.required must be boolean")
    evidence = gate.get("evidence")
    if evidence is not None and not isinstance(evidence, str):
        raise InputError(f"{label}.evidence must be a string")
    parsed = urlsplit(str(evidence or ""))
    if required and (
        parsed.scheme != "https" or not parsed.netloc or parsed.username is not None
    ):
        raise InputError(f"{label}.evidence must be an auditable HTTPS URL")
    expected_contains = _validated_expected_content(
        gate.get("expected_contains", []),
        label,
        required=required,
    )
    return {
        "name": gate["name"],
        "required": required,
        "evidence": evidence,
        "expected_contains": expected_contains,
        "verification": "live-audit-required",
    }


def gate_declarations(contract: dict[str, Any]) -> list[dict[str, Any]]:
    gates = contract.get("external_gates", [])
    if not isinstance(gates, list):
        raise InputError("external_gates must be a list")
    seen_names: set[str] = set()
    return [
        _validated_gate(gate, index, seen_names) for index, gate in enumerate(gates)
    ]
