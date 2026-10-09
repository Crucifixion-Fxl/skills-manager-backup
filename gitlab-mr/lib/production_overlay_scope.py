"""Restrict non-promotion overlays to networking or NodePool placement edits."""

from __future__ import annotations

import difflib
import json
from typing import Any

import yaml
from release_parity_core import InputError, git, tree_entry

MAX_YAML_BYTES = 512 * 1024
PLACEMENT_PATHS = {
    "/spec/template/spec/nodeSelector/karpenter.sh~1nodepool",
    "/spec/template/spec/nodeSelector/karpenter.k8s.aws~1ec2nodeclass",
    "/spec/template/spec/nodeSelector/network.addx.io~1egress",
}
WORKLOAD_KINDS = {"Rollout", "Deployment", "StatefulSet", "DaemonSet", "Job"}


def _document(ref: str, path: str) -> dict[str, Any]:
    entry = tree_entry(ref, path)
    if entry is None or entry["type"] != "blob":
        raise InputError(f"production YAML is not a blob: {path}")
    size = int(git(["cat-file", "-s", entry["oid"]]).decode().strip())
    if size > MAX_YAML_BYTES:
        raise InputError(f"production YAML exceeds size limit: {path}")
    try:
        documents = list(yaml.safe_load_all(git(["cat-file", "blob", entry["oid"]])))
    except yaml.YAMLError as exc:
        raise InputError(f"production YAML cannot be parsed: {path}") from exc
    if len(documents) != 1 or not isinstance(documents[0], dict):
        raise InputError(f"production YAML must contain one mapping: {path}")
    return documents[0]


def _placement_patch(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"target", "patch"}:
        return False
    target = value["target"]
    if (
        not isinstance(target, dict)
        or target.get("kind") not in WORKLOAD_KINDS
        or not isinstance(target.get("name"), str)
        or not target["name"]
        or set(target) - {"group", "version", "kind", "name", "namespace"}
        or not isinstance(value["patch"], str)
    ):
        return False
    try:
        operations = yaml.safe_load(value["patch"])
    except yaml.YAMLError:
        return False
    return (
        isinstance(operations, list)
        and bool(operations)
        and all(
            isinstance(operation, dict)
            and set(operation) == {"op", "path", "value"}
            and operation["op"] in {"add", "replace"}
            and operation["path"] in PLACEMENT_PATHS
            and isinstance(operation["value"], str)
            and bool(operation["value"])
            for operation in operations
        )
    )


def _patches_are_placement_only(before: Any, after: Any) -> bool:
    if not isinstance(before, list) or not isinstance(after, list):
        return False
    old = [json.dumps(item, sort_keys=True) for item in before]
    new = [json.dumps(item, sort_keys=True) for item in after]
    for operation, a_start, a_end, b_start, b_end in difflib.SequenceMatcher(
        a=old, b=new, autojunk=False
    ).get_opcodes():
        if operation != "equal" and not all(
            _placement_patch(item)
            for item in before[a_start:a_end] + after[b_start:b_end]
        ):
            return False
    return True


def require_production_only_yaml_change(base: str, head: str, path: str) -> None:
    before = _document(base, path)
    after = _document(head, path)
    if before.get("kind") != after.get("kind"):
        raise InputError(f"production YAML kind changed: {path}")
    if before.get("kind") == "NetworkPolicy":
        if (
            before.get("apiVersion")
            == after.get("apiVersion")
            == "networking.k8s.io/v1"
            and before.get("metadata") == after.get("metadata")
            and set(before) == set(after) == {"apiVersion", "kind", "metadata", "spec"}
        ):
            return
    elif before.get("kind") == "Kustomization":
        old_unchanged = {
            key: value for key, value in before.items() if key != "patches"
        }
        new_unchanged = {key: value for key, value in after.items() if key != "patches"}
        if old_unchanged == new_unchanged and _patches_are_placement_only(
            before.get("patches", []), after.get("patches", [])
        ):
            return
    raise InputError(
        f"production-only exception permits NetworkPolicy spec or NodePool selector patches only: {path}"
    )
