#!/usr/bin/env python3
"""check_object_bucket.py <directory> [--objectbucket-target <target>]."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from _scan import ParseError, iter_docs, resolve_dir


API_VERSION = "platform.addx.io/v1alpha1"
KIND = "ObjectBucket"
DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
LOGICAL_NAME_MAX = 18
NAMESPACE_MAX = 63
ACCESS_MODES = {"ReadOnly", "ReadWrite"}
IDENTITY_MODES = {"DirectKSA", "GSAImpersonation"}
EXPECTED_TOP_LEVEL = {"apiVersion", "kind", "metadata", "spec"}
EXPECTED_METADATA = {"name", "namespace", "annotations", "labels"}
EXPECTED_ANNOTATIONS = {
    "argocd.argoproj.io/sync-wave",
    "argocd.argoproj.io/sync-options",
}
APP_LABEL = "platform.addx.io/app"
EXPECTED_LABELS = {APP_LABEL}
EXPECTED_SPEC = {"profile", "residency", "protection", "access"}
EXPECTED_ACCESS = {"serviceAccountName", "identityMode", "mode"}
LEGACY_ACCESS = {"serviceAccountName", "mode"}
EXPECTED_SYNC_OPTIONS = {"Prune=confirm", "Delete=confirm"}
READINESS_FILE = (
    Path(__file__).resolve().parent.parent
    / "references"
    / "object-storage"
    / "objectbucket-readiness.yaml"
)
GRANDFATHERED_REQUIRED_FIELDS = {
    "target",
    "namespace",
    "name",
    "app",
    "identity_mode",
    "service_account_name",
    "mode",
}
LEGACY_IDENTITY_MODE = "omitted_legacy_direct_ksa"


def readiness_failure(message: str) -> None:
    print(f"FAIL: {READINESS_FILE}: {message}", file=sys.stderr)
    raise SystemExit(2)


GrandfatherKey = tuple[str, str, str, str]
GrandfatherContract = tuple[str, str]


def validated_grandfathered_item(
    index: int,
    item: Any,
) -> tuple[GrandfatherKey, GrandfatherContract]:
    field = f"grandfathered_objects[{index}]"
    if not isinstance(item, dict):
        readiness_failure(f"{field} must be a mapping")

    missing = sorted(GRANDFATHERED_REQUIRED_FIELDS - set(item))
    if missing:
        readiness_failure(f"{field} is missing required fields: {missing}")

    invalid_strings = sorted(
        key
        for key in GRANDFATHERED_REQUIRED_FIELDS
        if not isinstance(item[key], str) or not item[key]
    )
    if invalid_strings:
        readiness_failure(
            f"{field} fields must be non-empty strings: {invalid_strings}"
        )
    if item["identity_mode"] != LEGACY_IDENTITY_MODE:
        readiness_failure(
            f"{field}.identity_mode must be {LEGACY_IDENTITY_MODE!r}"
        )
    if item["mode"] not in ACCESS_MODES:
        readiness_failure(f"{field}.mode must be ReadOnly or ReadWrite")

    key: GrandfatherKey = (
        item["target"],
        item["namespace"],
        item["name"],
        item["app"],
    )
    contract: GrandfatherContract = (
        item["service_account_name"],
        item["mode"],
    )
    return key, contract


def load_grandfathered_objects() -> dict[GrandfatherKey, GrandfatherContract]:
    try:
        readiness = yaml.safe_load(READINESS_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"FAIL: cannot load {READINESS_FILE}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    items = readiness.get("grandfathered_objects", [])
    if not isinstance(items, list):
        readiness_failure("grandfathered_objects must be a list")

    result: dict[GrandfatherKey, GrandfatherContract] = {}
    for index, item in enumerate(items):
        key, contract = validated_grandfathered_item(index, item)
        if key in result:
            readiness_failure(
                f"grandfathered_objects[{index}] duplicates ObjectBucket "
                f"identity {key!r}"
            )
        result[key] = contract
    return result


GRANDFATHERED_OBJECTS = load_grandfathered_objects()


def object_id(doc: dict[str, Any]) -> str:
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return "ObjectBucket/<unnamed>"
    return f"ObjectBucket/{metadata.get('name', '<unnamed>')}"


def exact_keys(
    value: Any,
    expected: set[str],
    field: str,
    path: Path,
    obj: str,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(value, dict):
        return {}, [f"FAIL: {path}: {obj} {field} must be a mapping"]
    actual = set(value)
    if actual == expected:
        return value, []
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    return value, [
        f"FAIL: {path}: {obj} {field} keys must be exactly {sorted(expected)}; "
        f"missing={missing}, extra={extra}"
    ]


def valid_dns_label(value: Any, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= max_length
        and DNS_LABEL.fullmatch(value) is not None
    )


def grandfathered_contract(
    metadata: dict[str, Any],
    objectbucket_target: str | None,
) -> tuple[str, str] | None:
    if not objectbucket_target:
        return None
    labels = metadata.get("labels") or {}
    return GRANDFATHERED_OBJECTS.get(
        (
            objectbucket_target,
            metadata.get("namespace"),
            metadata.get("name"),
            labels.get(APP_LABEL),
        )
    )


def check_access(
    path: Path,
    obj: str,
    metadata: dict[str, Any],
    spec: dict[str, Any],
    objectbucket_target: str | None,
) -> list[str]:
    failures: list[str] = []
    legacy_contract = grandfathered_contract(metadata, objectbucket_target)
    expected_access = LEGACY_ACCESS if legacy_contract else EXPECTED_ACCESS
    access, errors = exact_keys(
        spec.get("access"), expected_access, "spec.access", path, obj
    )
    failures.extend(errors)
    if not valid_dns_label(access.get("serviceAccountName"), NAMESPACE_MAX):
        failures.append(
            f"FAIL: {path}: {obj} spec.access.serviceAccountName must be a "
            "same-namespace KSA DNS label"
        )
    if legacy_contract:
        legacy_service_account, legacy_mode = legacy_contract
        if access.get("serviceAccountName") != legacy_service_account:
            failures.append(
                f"FAIL: {path}: {obj} grandfathered serviceAccountName must remain "
                f"{legacy_service_account!r}"
            )
        if access.get("mode") != legacy_mode:
            failures.append(
                f"FAIL: {path}: {obj} grandfathered mode must remain {legacy_mode!r}"
            )
    elif access.get("identityMode") not in IDENTITY_MODES:
        failures.append(
            f"FAIL: {path}: {obj} spec.access.identityMode must be DirectKSA "
            "or GSAImpersonation"
        )
    if access.get("mode") not in ACCESS_MODES:
        failures.append(
            f"FAIL: {path}: {obj} spec.access.mode must be ReadOnly or ReadWrite"
        )
    return failures


def check_object_bucket(
    path: Path,
    doc: dict[str, Any],
    objectbucket_target: str | None,
) -> list[str]:
    obj = object_id(doc)
    failures: list[str] = []

    _, errors = exact_keys(doc, EXPECTED_TOP_LEVEL, "top-level", path, obj)
    failures.extend(errors)

    metadata, errors = exact_keys(
        doc.get("metadata"), EXPECTED_METADATA, "metadata", path, obj
    )
    failures.extend(errors)

    if not valid_dns_label(metadata.get("name"), LOGICAL_NAME_MAX):
        failures.append(
            f"FAIL: {path}: {obj} metadata.name must be a 1-{LOGICAL_NAME_MAX} "
            "character DNS label so ConfigMap/objectbucket-<name> is predictable"
        )
    if not valid_dns_label(metadata.get("namespace"), NAMESPACE_MAX):
        failures.append(
            f"FAIL: {path}: {obj} metadata.namespace must be an explicit DNS label"
        )

    annotations, errors = exact_keys(
        metadata.get("annotations"),
        EXPECTED_ANNOTATIONS,
        "metadata.annotations",
        path,
        obj,
    )
    failures.extend(errors)
    if annotations.get("argocd.argoproj.io/sync-wave") != "-2":
        failures.append(f"FAIL: {path}: {obj} sync-wave must be the string '-2'")
    sync_options = annotations.get("argocd.argoproj.io/sync-options")
    parsed_options = (
        {part.strip() for part in sync_options.split(",") if part.strip()}
        if isinstance(sync_options, str)
        else set()
    )
    if parsed_options != EXPECTED_SYNC_OPTIONS:
        failures.append(
            f"FAIL: {path}: {obj} sync-options must be exactly "
            "Prune=confirm,Delete=confirm"
        )

    labels, errors = exact_keys(
        metadata.get("labels"), EXPECTED_LABELS, "metadata.labels", path, obj
    )
    failures.extend(errors)
    if not valid_dns_label(labels.get(APP_LABEL), NAMESPACE_MAX):
        failures.append(
            f"FAIL: {path}: {obj} platform.addx.io/app must be a DNS label"
        )

    spec, errors = exact_keys(doc.get("spec"), EXPECTED_SPEC, "spec", path, obj)
    failures.extend(errors)
    for field, expected in {
        "profile": "standard",
        "residency": "local",
        "protection": "Retained",
    }.items():
        if spec.get(field) != expected:
            failures.append(
                f"FAIL: {path}: {obj} spec.{field} must be {expected!r}"
            )

    failures.extend(check_access(path, obj, metadata, spec, objectbucket_target))

    return failures


def is_object_bucket(doc: Any) -> bool:
    return (
        isinstance(doc, dict)
        and doc.get("apiVersion") == API_VERSION
        and doc.get("kind") == KIND
    )


def parse_inputs(argv: list[str]) -> tuple[Path | None, str | None]:
    usage = (
        "usage: check_object_bucket.py <directory> "
        "[--objectbucket-target <target>]"
    )
    if len(argv) not in {2, 4}:
        print(usage, file=sys.stderr)
        return None, None
    if len(argv) == 4 and argv[2] != "--objectbucket-target":
        print(usage, file=sys.stderr)
        return None, None

    root = resolve_dir(argv[:2], "check_object_bucket.py")
    target = argv[3] if len(argv) == 4 else None
    return root, target


def main(argv: list[str]) -> int:
    root, objectbucket_target = parse_inputs(argv)
    if root is None:
        return 2

    checked = 0
    failures: list[str] = []
    for path, doc in iter_docs(root):
        if isinstance(doc, ParseError):
            failures.append(f"FAIL: {path}: {doc.message}")
            continue
        if not is_object_bucket(doc):
            continue
        checked += 1
        failures.extend(check_object_bucket(path, doc, objectbucket_target))

    for failure in failures:
        print(failure)

    if failures:
        print(f"FAIL: {len(failures)} ObjectBucket contract violation(s)")
        return 1
    if checked == 0:
        print(f"PASS: no ObjectBucket resources under {root} (nothing to check)")
        return 0

    print(f"PASS: checked {checked} ObjectBucket resource(s) against the developer contract")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
