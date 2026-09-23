#!/usr/bin/env python3
"""check_sentry_resource_names.py <directory>

Rejects Sentry self-service manifests that still use the old shared names:
`sentry-onboard`, `sentry-onboard-config`, and `sentry-dsn`.

Those names are unsafe in shared namespaces because ArgoCD ownership is keyed by
kind + namespace + name. New manifests must be app-scoped:

  <app>-sentry-onboard
  <app>-sentry-onboard-config
  <app>-sentry-dsn

Exit codes:
  0 = no unsafe Sentry names found
  1 = one or more unsafe names found
  2 = dependency missing, invalid usage, or unreadable YAML
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # noqa: F401  (kept so the dep-missing message stays consistent; _scan imports yaml)
except ImportError:  # pragma: no cover - CI installs PyYAML before running validators.
    print("FAIL: PyYAML not installed. install: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

from _scan import ParseError, iter_files


def _is_sentry_configmap(doc: dict[str, Any]) -> bool:
    data = doc.get("data")
    if not isinstance(data, dict):
        return False
    return any(key in data for key in ("APP", "PLATFORM", "VAULT_PATH_SCHEMA", "VAULT_K8S_ROLE"))


def _container_images(doc: dict[str, Any]) -> list[str]:
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return []
    template = spec.get("template")
    if not isinstance(template, dict):
        return []
    pod_spec = template.get("spec")
    if not isinstance(pod_spec, dict):
        return []
    images = []
    for container in pod_spec.get("containers") or []:
        if isinstance(container, dict) and isinstance(container.get("image"), str):
            images.append(container["image"])
    return images


def _job_pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return {}
    template = spec.get("template")
    if not isinstance(template, dict):
        return {}
    pod_spec = template.get("spec")
    return pod_spec if isinstance(pod_spec, dict) else {}


def _walk(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        yield path, obj
        for key, value in obj.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield from _walk(value, next_path)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _walk(value, f"{path}[{index}]")


def _is_sentry_external_secret(doc: dict[str, Any]) -> bool:
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return False
    for _, node in _walk(spec):
        if not isinstance(node, dict):  # pragma: no cover - _walk only yields dict nodes here.
            continue
        remote_ref = node.get("remoteRef")
        if not isinstance(remote_ref, dict):
            continue
        key = remote_ref.get("key") or remote_ref.get("remoteKey")
        if isinstance(key, str) and "/sentry/" in f"/{key}":
            return True
    return False


def _check_sentry_configmap(kind: Any, name: Any, doc: dict[str, Any]) -> list[str]:
    if kind == "ConfigMap" and name == "sentry-onboard-config" and _is_sentry_configmap(doc):
        return ["ConfigMap/sentry-onboard-config must be app-scoped"]
    return []


def _check_sentry_service_account(kind: Any, name: Any) -> list[str]:
    if kind == "ServiceAccount" and name == "sentry-onboard":
        return ["ServiceAccount/sentry-onboard must be app-scoped"]
    return []


def _check_sentry_job(kind: Any, name: Any, doc: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if kind != "Job":
        return violations

    pod_spec = _job_pod_spec(doc)
    images = _container_images(doc)
    is_sentry_job = (
        name == "sentry-onboard"
        or pod_spec.get("serviceAccountName") == "sentry-onboard"
        or any("sentry-onboard" in image for image in images)
    )
    if is_sentry_job and name == "sentry-onboard":
        violations.append("Job/sentry-onboard must be app-scoped")
    if is_sentry_job and pod_spec.get("serviceAccountName") == "sentry-onboard":
        violations.append("Job serviceAccountName sentry-onboard must be app-scoped")
    for field_path, node in _walk(pod_spec):
        if not isinstance(node, dict):  # pragma: no cover - _walk only yields dict nodes here.
            continue
        config_map_ref = node.get("configMapRef")
        if isinstance(config_map_ref, dict) and config_map_ref.get("name") == "sentry-onboard-config":
            violations.append(
                f"Job {field_path}.configMapRef.name sentry-onboard-config must be app-scoped"
            )
    return violations


def _check_sentry_external_secret(kind: Any, name: Any, doc: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if kind == "ExternalSecret" and _is_sentry_external_secret(doc):
        spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
        target = spec.get("target") if isinstance(spec.get("target"), dict) else {}
        if name == "sentry-dsn":
            violations.append("ExternalSecret/sentry-dsn must be app-scoped")
        if target.get("name") == "sentry-dsn":
            violations.append("ExternalSecret target.name sentry-dsn must be app-scoped")
    return violations


def _check_sentry_secret_refs(doc: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    for field_path, node in _walk(doc):
        if not isinstance(node, dict):  # pragma: no cover - _walk only yields dict nodes here.
            continue
        secret_ref = node.get("secretKeyRef")
        if isinstance(secret_ref, dict) and secret_ref.get("name") == "sentry-dsn":
            violations.append(
                f"{field_path}.secretKeyRef.name sentry-dsn must be app-scoped"
            )
    return violations


def _check_doc(file: Path, doc: dict[str, Any]) -> list[str]:
    metadata = doc.get("metadata")
    name = metadata.get("name") if isinstance(metadata, dict) else None
    kind = doc.get("kind")

    violations = [
        *_check_sentry_configmap(kind, name, doc),
        *_check_sentry_service_account(kind, name),
        *_check_sentry_job(kind, name, doc),
        *_check_sentry_external_secret(kind, name, doc),
        *_check_sentry_secret_refs(doc),
    ]

    return [f"FAIL: {file}: {violation}" for violation in violations]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_sentry_resource_names.py <directory>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"FAIL: directory not found: {root}", file=sys.stderr)
        return 2

    failures: list[str] = []
    checked = 0
    for file, docs in iter_files(root):
        if isinstance(docs, ParseError):
            print(f"FAIL: {file}: {docs.message}")
            return 2
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            checked += 1
            failures.extend(_check_doc(file, doc))

    if failures:
        print("\n".join(failures))
        print(f"FAIL: {len(failures)} unsafe Sentry resource name(s) found")
        return 1

    print(f"PASS: checked {checked} YAML document(s), no unsafe Sentry resource names")
    return 0


if __name__ == "__main__":  # pragma: no cover - covered via main() in tests.
    sys.exit(main(sys.argv))
