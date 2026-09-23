#!/usr/bin/env python3
"""check_s3_bucket_delete_guard.py <directory>

Ensure Crossplane S3 Bucket CRs cannot delete the real cloud bucket when the
GitOps manifest is removed or pruned.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


S3_BUCKET_GROUPS = {
    "s3.aws.m.upbound.io",
    "s3.aws.upbound.io",
}
FORBIDDEN_MANAGEMENT_POLICIES = {"*", "Delete"}
SAFE_POLICIES = '["Observe", "Create", "Update", "LateInitialize"]'


def resolve_dir(argv: list[str]) -> Path | None:
    if len(argv) < 2:
        print("usage: check_s3_bucket_delete_guard.py <directory>", file=sys.stderr)
        return None
    root = Path(argv[1])
    if not root.is_dir():
        print(f"FAIL: directory not found: {root}", file=sys.stderr)
        return None
    return root


def is_chart_template_path(path: Path) -> bool:
    return "templates" in path.parts


def iter_yaml_files(root: Path):
    for path in sorted({*root.rglob("*.yaml"), *root.rglob("*.yml")}):
        if is_chart_template_path(path):
            continue
        yield path


def load_docs(path: Path):
    try:
        return list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return [("YAML_ERROR", exc)]


def api_group(doc: dict[str, Any]) -> str:
    api_version = doc.get("apiVersion")
    if not isinstance(api_version, str) or "/" not in api_version:
        return ""
    return api_version.split("/", 1)[0]


def metadata_name(doc: dict[str, Any]) -> str:
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return "<unnamed>"
    name = metadata.get("name")
    return name if isinstance(name, str) else "<unnamed>"


def spec(doc: dict[str, Any]) -> dict[str, Any]:
    value = doc.get("spec")
    return value if isinstance(value, dict) else {}


def is_s3_bucket(doc: dict[str, Any]) -> bool:
    return api_group(doc) in S3_BUCKET_GROUPS and doc.get("kind") == "Bucket"


def check_bucket(path: Path, doc: dict[str, Any]) -> list[str]:
    obj = f"Bucket/{metadata_name(doc)}"
    policies = spec(doc).get("managementPolicies")

    if not isinstance(policies, list) or not policies:
        return [
            f"FAIL: {path}: {obj} must set non-empty spec.managementPolicies "
            f"without Delete/'*'. Prune=false or spec.deletionPolicy alone does "
            f"not protect the real S3 bucket. Use {SAFE_POLICIES}"
        ]

    normalized = {item for item in policies if isinstance(item, str)}
    forbidden = sorted(normalized & FORBIDDEN_MANAGEMENT_POLICIES)
    if forbidden:
        return [
            f"FAIL: {path}: {obj} managementPolicies {policies} includes "
            f"{forbidden}; remove Delete/'*' so GitOps removal cannot delete "
            f"the real S3 bucket. Use {SAFE_POLICIES}"
        ]

    return []


def check_doc(path: Path, doc: Any) -> tuple[int, list[str]]:
    if doc is None:
        return 0, []
    if isinstance(doc, tuple) and doc[0] == "YAML_ERROR":
        return 0, [f"FAIL: {path}: YAML parse error: {doc[1]}"]
    if not isinstance(doc, dict) or not is_s3_bucket(doc):
        return 0, []
    return 1, check_bucket(path, doc)


def main(argv: list[str]) -> int:
    root = resolve_dir(argv)
    if root is None:
        return 2

    checked = 0
    failures: list[str] = []
    for path in iter_yaml_files(root):
        for doc in load_docs(path):
            doc_checked, doc_failures = check_doc(path, doc)
            checked += doc_checked
            failures.extend(doc_failures)

    for failure in failures:
        print(failure)

    if failures:
        print(f"FAIL: {len(failures)} S3 Bucket delete guard violation(s)")
        return 1

    if checked == 0:
        print(f"PASS: no Crossplane S3 Bucket resources under {root} (nothing to check)")
        return 0

    print(f"PASS: checked {checked} Crossplane S3 Bucket resource(s) for no-Delete managementPolicies")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
