#!/usr/bin/python3 -I
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Fail closed unless a post-upgrade receipt preserves the local Buzz topology."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


SHA40 = re.compile(r"[0-9a-f]{40}")
MAX_RECEIPT_BYTES = 16 * 1024 * 1024
GAPS = {f"LA-{number:02d}" for number in range(1, 13)}
STATUSES = ("pass", "fail", "unknown", "not_applicable")
TOP_LEVEL_KEYS = {
    "ok",
    "expected_sha",
    "inventory",
    "inventory_ids",
    "summary",
    "gaps",
    "checks",
}
INVENTORY_KEYS = {
    "agents",
    "sync_services",
    "feishu_services",
    "todo_services",
    "join_services",
    "harnesses",
    "buzz_cli_binaries",
    "transient_services",
    "lookup_only_units",
}
STABLE_IDENTITIES = (
    "agents", "agent_services", "sync_services", "feishu_services",
    "todo_services", "join_services", "persistent_timers", "harnesses",
)
INVENTORY_ID_KEYS = {
    *STABLE_IDENTITIES,
    "harnesses",
    "buzz_cli_binaries",
    "transient_units",
    "lookup_only_units",
}
COUNTED_IDENTITIES = {
    "agents": "agents",
    "sync_services": "sync_services",
    "feishu_services": "feishu_services",
    "todo_services": "todo_services",
    "join_services": "join_services",
    "harnesses": "harnesses",
    "transient_services": "transient_units",
    "lookup_only_units": "lookup_only_units",
}


def trusted_directory_chain(path: Path) -> bool:
    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except OSError:
            return False
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, os.geteuid()}
            or bool(stat.S_IMODE(metadata.st_mode) & 0o022)
        ):
            return False
    return True


def load_receipt(path: Path) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        if (
            not path.is_absolute()
            or path.resolve(strict=True) != path
            or not trusted_directory_chain(path.parent)
        ):
            raise ValueError("receipt path must be canonical")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or bool(stat.S_IMODE(before.st_mode) & 0o077)
            or before.st_size > MAX_RECEIPT_BYTES
        ):
            raise ValueError("receipt metadata is unsafe")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_RECEIPT_BYTES:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_RECEIPT_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(payload) > MAX_RECEIPT_BYTES
            or len(payload) != before.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise ValueError("receipt changed or is oversized")
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("receipt is unreadable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise ValueError("receipt must be an object")
    return value


def validate_shape(receipt: dict[str, Any], expected_sha: str) -> None:
    if (
        set(receipt) != TOP_LEVEL_KEYS
        or receipt.get("expected_sha") != expected_sha
        or not isinstance(receipt.get("ok"), bool)
        or not isinstance(receipt.get("inventory"), dict)
        or not isinstance(receipt.get("inventory_ids"), dict)
        or not isinstance(receipt.get("summary"), dict)
        or not isinstance(receipt.get("gaps"), dict)
        or not isinstance(receipt.get("checks"), list)
        or set(receipt["gaps"]) != GAPS
    ):
        raise ValueError("receipt schema or target mismatch")
    inventory = receipt["inventory"]
    identities = receipt["inventory_ids"]
    summary = receipt["summary"]
    checks = receipt["checks"]
    if (
        set(inventory) != INVENTORY_KEYS
        or set(identities) != INVENTORY_ID_KEYS
        or set(summary) != set(STATUSES)
        or not checks
        or len(checks) > 100_000
        or any(type(value) is not int or value < 0 for value in inventory.values())
        or any(type(value) is not int or value < 0 for value in summary.values())
    ):
        raise ValueError("receipt inventory, summary or checks are invalid")
    for key, value in identities.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, list)
            or not all(
                isinstance(item, str)
                and item
                and len(item) <= 512
                and not any(ord(character) < 0x20 or ord(character) == 0x7F for character in item)
                for item in value
            )
            or value != sorted(set(value))
        ):
            raise ValueError("inventory identities are not stable sets")
    if inventory["agents"] == 0:
        raise ValueError("receipt has no persistent agent inventory")
    for count_key, identity_key in COUNTED_IDENTITIES.items():
        if inventory[count_key] != len(identities[identity_key]):
            raise ValueError(f"inventory count disagrees with identities: {count_key}")
    if inventory["agents"] != len(identities["agent_services"]):
        raise ValueError("agent service inventory disagrees with agents")
    if len(identities["persistent_timers"]) != sum(
        inventory[key]
        for key in ("sync_services", "feishu_services", "todo_services", "join_services")
    ):
        raise ValueError("timer inventory disagrees with persistent services")
    expected_cli_ids = ["buzz-cli"] if inventory["buzz_cli_binaries"] else []
    if identities["buzz_cli_binaries"] != expected_cli_ids:
        raise ValueError("Buzz CLI inventory disagrees with its public identity")

    derived_summary = {status: 0 for status in STATUSES}
    derived_gap_counts = {
        gap_id: {status: 0 for status in STATUSES} for gap_id in GAPS
    }
    for check in checks:
        if not isinstance(check, dict) or set(check) not in (
            {"category", "subject", "status", "code", "gap_ids"},
            {"category", "subject", "status", "code", "gap_ids", "detail"},
        ):
            raise ValueError("check record schema is invalid")
        status_value = check.get("status")
        gap_ids = check.get("gap_ids")
        if (
            status_value not in STATUSES
            or not all(
                isinstance(check.get(key), str)
                and bool(check[key])
                and len(check[key]) <= 4096
                for key in ("category", "subject", "code")
            )
            or (
                "detail" in check
                and (
                    not isinstance(check["detail"], str)
                    or not check["detail"]
                    or len(check["detail"]) > 4096
                )
            )
            or not isinstance(gap_ids, list)
            or not gap_ids
            or gap_ids != sorted(set(gap_ids))
            or not set(gap_ids) <= GAPS
        ):
            raise ValueError("check record is invalid")
        derived_summary[status_value] += 1
        for gap_id in gap_ids:
            derived_gap_counts[gap_id][status_value] += 1
    if summary != derived_summary:
        raise ValueError("summary does not match checks")
    if receipt["ok"] != (summary["fail"] == 0 and summary["unknown"] == 0):
        raise ValueError("ok does not match summary")
    for gap_id, value in receipt["gaps"].items():
        if (
            not isinstance(value, dict)
            or set(value) != {"status", *STATUSES}
            or value["status"] not in STATUSES
            or any(type(value[status]) is not int or value[status] < 0 for status in STATUSES)
            or any(value[status] != derived_gap_counts[gap_id][status] for status in STATUSES)
        ):
            raise ValueError("gap record does not match checks")
        counts = derived_gap_counts[gap_id]
        expected_status = next(
            (status for status in ("fail", "unknown", "pass") if counts[status]),
            "not_applicable",
        )
        if value["status"] != expected_status:
            raise ValueError("gap status does not match its counts")


def compare(before: dict[str, Any], after: dict[str, Any], expected_sha: str) -> None:
    validate_shape(before, expected_sha)
    validate_shape(after, expected_sha)
    if (
        after.get("ok") is not True
        or after["summary"].get("fail") != 0
        or after["summary"].get("unknown") != 0
        or any(value.get("status") in {"fail", "unknown"} for value in after["gaps"].values())
    ):
        raise ValueError("post-upgrade receipt is not a PASS")
    for key in STABLE_IDENTITIES:
        if before["inventory_ids"].get(key) != after["inventory_ids"].get(key):
            raise ValueError(f"persistent topology changed: {key}")
    if (
        after["inventory"].get("transient_services") != 0
        or after["inventory"].get("lookup_only_units") != 0
        or after["inventory_ids"].get("transient_units") != []
        or after["inventory_ids"].get("lookup_only_units") != []
    ):
        raise ValueError("post-upgrade inventory still has transient or shadow units")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    if not sys.flags.isolated:
        return 2
    if SHA40.fullmatch(args.expected_sha) is None:
        return 2
    try:
        compare(load_receipt(args.before), load_receipt(args.after), args.expected_sha)
    except ValueError:
        return 2
    print(json.dumps({"ok": True, "expected_sha": args.expected_sha}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
