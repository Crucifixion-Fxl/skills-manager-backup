"""解析 staging writer cleanup 契约并验证候选 diff。"""

from __future__ import annotations

import hashlib
from typing import Any

import yaml
from release_parity_contract import UniqueKeyLoader
from release_parity_core import (
    InputError,
    changed_paths,
    git,
    tree_entry,
    validate_repo_path,
)

CONTRACT_KEYS = {
    "version",
    "workflow",
    "staging_branch",
    "accepted_staging_sha",
    "accepted_pipeline",
    "writer_jobs",
    "contract_gate_jobs",
    "staging_contract_gate_job",
    "accepted_jobs",
    "verification_paths",
    "documentation_paths",
}


def load_yaml_blob(ref: str, path: str, label: str) -> tuple[Any, dict[str, str]]:
    entry = tree_entry(ref, path)
    if entry is None or entry["type"] != "blob" or entry["mode"] != "100644":
        raise InputError(f"{label} must be a regular 100644 blob at {ref}:{path}")
    raw = git(["cat-file", "-p", entry["oid"]])
    try:
        value = yaml.load(raw.decode("utf-8"), Loader=UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise InputError(f"cannot parse {label}: {exc}") from exc
    metadata = {
        "path": path,
        "mode": entry["mode"],
        "type": entry["type"],
        "oid": entry["oid"],
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    return value, metadata


def string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise InputError(f"{label} must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise InputError(f"{label} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise InputError(f"{label} must not contain duplicates")
    return value


def load_cleanup_contract(
    candidate_ref: str,
    target_ref: str,
    path: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    path = validate_repo_path(path, "cleanup contract path")
    if not path.startswith("release-contracts/") or not path.endswith(
        (".yaml", ".yml")
    ):
        raise InputError(
            "cleanup contract path must be release-contracts/<name>.yaml or .yml"
        )
    if tree_entry(target_ref, path) is not None:
        raise InputError("cleanup contract must be newly committed by the candidate MR")
    value, metadata = load_yaml_blob(candidate_ref, path, "cleanup contract")
    if not isinstance(value, dict):
        raise InputError("cleanup contract must be a YAML mapping")
    unknown = sorted(set(value) - CONTRACT_KEYS)
    if unknown:
        raise InputError(f"cleanup contract contains unknown keys: {', '.join(unknown)}")
    if type(value.get("version")) is not int or value["version"] != 1:
        raise InputError("cleanup contract version must be integer 1")
    if value.get("workflow") != "staging-writer-cleanup":
        raise InputError("cleanup contract workflow must be staging-writer-cleanup")
    return value, metadata


def validate_declared_paths(
    contract: dict[str, Any],
    metadata: dict[str, str],
    target_ref: str,
    candidate_ref: str,
) -> tuple[list[str], list[str]]:
    verification_paths = [
        validate_repo_path(item, f"verification_paths[{index}]")
        for index, item in enumerate(
            string_list(contract.get("verification_paths"), "verification_paths")
        )
    ]
    documentation_paths = [
        validate_repo_path(item, f"documentation_paths[{index}]")
        for index, item in enumerate(
            string_list(contract.get("documentation_paths"), "documentation_paths")
        )
    ]
    if not all(
        path.startswith(("scripts/", "tests/")) and path.endswith(".py")
        for path in verification_paths
    ):
        raise InputError("verification_paths must be Python files under scripts/ or tests/")
    if not all(path.startswith("docs/") for path in documentation_paths):
        raise InputError("documentation_paths must be under docs/")
    declared = {
        ".gitlab-ci.yml",
        metadata["path"],
        *verification_paths,
        *documentation_paths,
    }
    actual = changed_paths(target_ref, candidate_ref)
    undeclared = sorted(actual - declared)
    missing = sorted(declared - actual)
    if undeclared:
        raise InputError(
            "cleanup candidate changed undeclared paths: " + ", ".join(undeclared)
        )
    if missing:
        raise InputError(
            "cleanup contract declared unchanged paths: " + ", ".join(missing)
        )
    for path in verification_paths:
        if tree_entry(target_ref, path) is not None:
            raise InputError(f"cleanup verification path must be newly added: {path}")
        entry = tree_entry(candidate_ref, path)
        if entry is None or entry["type"] != "blob" or entry["mode"] not in {
            "100644",
            "100755",
        }:
            raise InputError(f"cleanup verification path is not a regular file: {path}")
    return verification_paths, documentation_paths
