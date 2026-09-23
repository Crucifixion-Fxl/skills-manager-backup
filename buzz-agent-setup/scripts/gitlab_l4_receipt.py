#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validate the owner-controlled receipt that enables project write canaries."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

try:  # Script execution and test import both work without packaging.
    from gitlab_agent_project_tokens import (
        ProjectTokenMap,
        ProjectTokenMapError,
        load_mapping,
        mapping_sha256,
        mapping_to_public_dict,
    )
except ImportError:  # pragma: no cover
    from .gitlab_agent_project_tokens import (
        ProjectTokenMap,
        ProjectTokenMapError,
        load_mapping,
        mapping_sha256,
        mapping_to_public_dict,
    )


class L4ReceiptError(ValueError):
    """The owner receipt is absent, unsafe, stale, or incomplete."""


WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
OWNER_L4_RECEIPT_ENV = "BUZZ_GITLAB_PROJECT_TOKEN_L4_RECEIPT"
OWNER_L4_HEAD_ENV = "BUZZ_GITLAB_PROJECT_TOKEN_L4_HEAD_SHA"
OWNER_PROVISIONING_RECEIPT_ENV = "BUZZ_GITLAB_PROJECT_TOKEN_PROVISIONING_RECEIPT"
RECEIPT_SCHEMA = "l4-project-write-receipt-v1"
HEAD_SHA = re.compile(r"^[0-9a-f]{40,64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_KEYS = frozenset({
    "schema_version", "status", "map_sha256", "head_sha", "write_methods",
    "projects", "ordinary_agent_gitlab_writes_enabled", "l4_canary",
    "provisioning_receipt_sha256", "evidence",
})
PROJECT_KEYS = frozenset({
    "project_id", "project_path", "profile", "token_id", "bot_user_id",
    "access_level", "scopes", "bot_external", "membership_project_ids", "write_canary",
})
EVIDENCE_KEYS = frozenset({
    "gitlab_version", "gitlab_revision", "verified_at", "verified_by", "canary_note_id", "runtime",
})
RUNTIME_KEYS = frozenset({"pid", "workers"})
CANARY_KEYS = frozenset({"status", "method_results"})
METHOD_RESULT_KEYS = frozenset({"method", "http_status", "endpoint", "response_sha256"})
PROFILE_CONTRACT = {
    "planner": (15, ["api"], True),
    "reporter": (20, ["api", "read_repository"], True),
    "developer": (30, ["api", "write_repository"], False),
}


def _secure_json(path: Path, label: str) -> Mapping[str, Any]:
    if not path.is_absolute() or path.is_symlink():
        raise L4ReceiptError(f"{label} must be an absolute non-symlink regular file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except (OSError, TypeError) as exc:
        raise L4ReceiptError(f"{label} must be an absolute non-symlink regular file") from exc
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise L4ReceiptError(f"{label} must be current-user-owned and mode 0600")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            try:
                value = json.load(stream)
            except json.JSONDecodeError as exc:
                raise L4ReceiptError(f"{label} contains invalid JSON") from exc
    except (OSError, UnicodeError) as exc:
        raise L4ReceiptError(f"{label} could not be read securely") from exc
    finally:
        if fd not in {-1, None}:
            os.close(fd)
    if not isinstance(value, Mapping):
        raise L4ReceiptError(f"{label} must be a JSON object")
    return value


def canonical_json_sha256(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_verified_receipt(mapping: ProjectTokenMap, environ: Mapping[str, str]) -> None:
    """Fail closed unless the receipt proves the exact map/head and canaries."""
    receipt_value = environ.get(OWNER_L4_RECEIPT_ENV)
    head_sha = environ.get(OWNER_L4_HEAD_ENV)
    provisioning_value = environ.get(OWNER_PROVISIONING_RECEIPT_ENV)
    if not receipt_value or not head_sha or not provisioning_value:
        raise L4ReceiptError(
            "L4 write receipt, owner-pinned head, and current provisioning receipt are required"
        )
    receipt_path = Path(receipt_value).expanduser()
    provisioning_path = Path(provisioning_value).expanduser()
    if not receipt_path.is_absolute():
        raise L4ReceiptError("L4 write receipt path must be absolute")
    if not provisioning_path.is_absolute():
        raise L4ReceiptError("provisioning receipt path must be absolute")
    if not HEAD_SHA.fullmatch(head_sha):
        raise L4ReceiptError("owner-pinned L4 head must be a lowercase Git SHA")
    receipt = _secure_json(receipt_path, "L4 write receipt")
    provisioning = _secure_json(provisioning_path, "provisioning receipt")
    if provisioning.get("schema_version") != "2.0" or provisioning.get("secret_material_in_receipt") is not False:
        raise L4ReceiptError("current provisioning receipt is not a secret-free schema v2 receipt")
    if provisioning.get("mapping") != mapping_to_public_dict(mapping):
        raise L4ReceiptError("current provisioning receipt mapping does not match the owner map")
    tokens = provisioning.get("tokens")
    if (not isinstance(tokens, list) or not tokens
            or any(not isinstance(item, Mapping)
                   or isinstance(item.get("project_id"), bool)
                   or not isinstance(item.get("project_id"), int)
                   or isinstance(item.get("token_id"), bool)
                   or not isinstance(item.get("token_id"), int) for item in tokens)):
        raise L4ReceiptError("current provisioning receipt has no complete token identities")
    for token in tokens:
        if (not isinstance(token.get("project_path"), str) or not isinstance(token.get("profile"), str)
                or isinstance(token.get("bot_user_id"), bool) or not isinstance(token.get("bot_user_id"), int)
                or isinstance(token.get("access_level"), bool) or not isinstance(token.get("access_level"), int)
                or not isinstance(token.get("scopes"), list) or not all(isinstance(scope, str) for scope in token["scopes"])
                or not isinstance(token.get("bot_external"), bool)
                or not isinstance(token.get("membership_project_ids"), list)
                or any(isinstance(project_id, bool) or not isinstance(project_id, int)
                       for project_id in token["membership_project_ids"])):
            raise L4ReceiptError("current provisioning receipt token identity is incomplete")
    token_project_ids = [item["project_id"] for item in tokens]
    token_ids = [item["token_id"] for item in tokens]
    if (len(token_project_ids) != len(set(token_project_ids))
            or len(token_ids) != len(set(token_ids))
            or set(token_project_ids) != {entry.project_id for entry in mapping.projects}
            or any(set(item["membership_project_ids"]) != {item["project_id"]} for item in tokens)):
        raise L4ReceiptError("current provisioning receipt projects do not match the owner map")
    token_by_id = {item["project_id"]: item for item in tokens}
    if set(receipt) != RECEIPT_KEYS:
        raise L4ReceiptError("L4 write receipt keys are not exact")
    if receipt["schema_version"] != RECEIPT_SCHEMA or receipt["status"] != "verified":
        raise L4ReceiptError("L4 write receipt is not verified")
    if receipt["ordinary_agent_gitlab_writes_enabled"] is not True or receipt["l4_canary"] != "passed":
        raise L4ReceiptError("L4 write receipt has not enabled the write canary")
    if receipt["map_sha256"] != mapping_sha256(mapping):
        raise L4ReceiptError("L4 write receipt map binding does not match the owner map")
    if receipt["head_sha"] != head_sha:
        raise L4ReceiptError("L4 write receipt head binding does not match the owner head")
    if (not isinstance(receipt["provisioning_receipt_sha256"], str)
            or not SHA256.fullmatch(receipt["provisioning_receipt_sha256"])
            or receipt["provisioning_receipt_sha256"] != canonical_json_sha256(provisioning)):
        raise L4ReceiptError("L4 write receipt provisioning binding does not match current tokens")
    evidence = receipt["evidence"]
    if not isinstance(evidence, Mapping) or set(evidence) != EVIDENCE_KEYS:
        raise L4ReceiptError("L4 write receipt evidence is incomplete")
    for field in ("gitlab_version", "gitlab_revision", "verified_by"):
        if not isinstance(evidence[field], str) or not evidence[field].strip():
            raise L4ReceiptError(f"L4 write receipt evidence field {field} is invalid")
    try:
        verified_at = dt.datetime.fromisoformat(evidence["verified_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise L4ReceiptError("L4 write receipt verified_at is invalid") from exc
    if (verified_at.tzinfo is None or isinstance(evidence["canary_note_id"], bool)
            or not isinstance(evidence["canary_note_id"], int)
            or evidence["canary_note_id"] <= 0):
        raise L4ReceiptError("L4 write receipt evidence timestamp or canary note is invalid")
    runtime = evidence["runtime"]
    if (not isinstance(runtime, Mapping) or set(runtime) != RUNTIME_KEYS
            or isinstance(runtime["pid"], bool) or not isinstance(runtime["pid"], int) or runtime["pid"] <= 0
            or isinstance(runtime["workers"], bool) or not isinstance(runtime["workers"], int) or runtime["workers"] <= 0):
        raise L4ReceiptError("L4 write receipt runtime evidence is invalid")
    methods = receipt["write_methods"]
    if (not isinstance(methods, list) or not all(isinstance(method, str) for method in methods)
            or len(methods) != len(WRITE_METHODS) or set(methods) != WRITE_METHODS):
        raise L4ReceiptError("L4 write receipt does not cover every write method")
    projects = receipt["projects"]
    if not isinstance(projects, list) or len(projects) != len(mapping.projects):
        raise L4ReceiptError("L4 write receipt project coverage does not match the owner map")
    by_id: dict[int, Mapping[str, Any]] = {}
    for item in projects:
        if not isinstance(item, Mapping) or set(item) != PROJECT_KEYS:
            raise L4ReceiptError("L4 write receipt project entries are not exact")
        project_id = item["project_id"]
        if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id in by_id:
            raise L4ReceiptError("L4 write receipt has duplicate or invalid project ids")
        by_id[project_id] = item
    for entry in mapping.projects:
        item = by_id.get(entry.project_id)
        token = token_by_id.get(entry.project_id)
        if item is None or token is None:
            raise L4ReceiptError("L4 write receipt project binding does not match the owner map")
        expected_token = {
            "project_path": token.get("project_path"),
            "profile": token.get("profile"),
            "token_id": token.get("token_id"),
            "bot_user_id": token.get("bot_user_id"),
            "access_level": token.get("access_level"),
            "scopes": token.get("scopes"),
            "bot_external": token.get("bot_external"),
            "membership_project_ids": token.get("membership_project_ids"),
        }
        profile_contract = PROFILE_CONTRACT.get(entry.profile)
        if (profile_contract is None or token["profile"] != entry.profile
                or token["access_level"] != profile_contract[0]
                or token["scopes"] != profile_contract[1]
                or token["bot_external"] is not profile_contract[2]):
            raise L4ReceiptError("current provisioning receipt token profile is not the mapped minimum")
        if (item["project_path"] != entry.project_path or item["profile"] != entry.profile
                or any(item[field] != value for field, value in expected_token.items())):
            raise L4ReceiptError("L4 write receipt project binding does not match the owner map")
        write_canary = item["write_canary"]
        if entry.profile != "developer":
            if write_canary != "not_applicable":
                raise L4ReceiptError("L4 write receipt non-developer canary is invalid")
            continue
        if (not isinstance(write_canary, Mapping) or set(write_canary) != CANARY_KEYS
                or write_canary["status"] != "passed"):
            raise L4ReceiptError("L4 write receipt developer canary is incomplete")
        results = write_canary["method_results"]
        if not isinstance(results, list) or len(results) != len(WRITE_METHODS):
            raise L4ReceiptError("L4 write receipt developer canary method coverage is incomplete")
        methods_seen: set[str] = set()
        for result in results:
            if not isinstance(result, Mapping) or set(result) != METHOD_RESULT_KEYS:
                raise L4ReceiptError("L4 write receipt developer canary response is incomplete")
            method = result["method"]
            endpoint = result["endpoint"]
            status = result["http_status"]
            if (not isinstance(method, str) or method in methods_seen or method not in WRITE_METHODS
                    or not isinstance(endpoint, str) or not endpoint.startswith("/")
                    or endpoint.startswith("//") or "\\" in endpoint or "://" in endpoint
                    or isinstance(status, bool) or not isinstance(status, int) or not 200 <= status < 300
                    or not isinstance(result["response_sha256"], str)
                    or not SHA256.fullmatch(result["response_sha256"])):
                raise L4ReceiptError("L4 write receipt developer canary response is invalid")
            methods_seen.add(method)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - operator validation helper
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--provisioning-receipt", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args(argv)
    try:
        mapping = load_mapping(args.mapping)
        load_verified_receipt(mapping, {
            OWNER_L4_RECEIPT_ENV: args.receipt,
            OWNER_L4_HEAD_ENV: args.head_sha,
            OWNER_PROVISIONING_RECEIPT_ENV: args.provisioning_receipt,
        })
    except (ProjectTokenMapError, L4ReceiptError) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps({"verified": True, "map_sha256": mapping_sha256(mapping)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
