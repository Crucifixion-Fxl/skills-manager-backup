#!/usr/bin/env python3
"""Validate the registration/execution contract for guarded one-shot Jobs."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # noqa: F401
except ImportError:  # pragma: no cover - CI installs PyYAML.
    print("FAIL: PyYAML not installed. install: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

from _scan import ParseError, iter_files


APPROVAL_KEY = "ops.addx.io/execution-approval"
COMPONENT_KEY = "app.kubernetes.io/component"
APPROVAL_URL = re.compile(r"^https://gitlab\.addx\.ai/.+/-/issues/[0-9]+#note_[0-9]+$")
ARTIFACT_WORK_VOLUME_FAILURE = (
    "artifact handoff requires exactly one bounded work emptyDir"
)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _one_shot_job(doc: dict[str, Any]) -> bool:
    if doc.get("kind") != "Job" or doc.get("apiVersion") != "batch/v1":
        return False
    metadata = _mapping(doc.get("metadata"))
    annotations = _mapping(metadata.get("annotations"))
    labels = _mapping(metadata.get("labels"))
    return APPROVAL_KEY in annotations or labels.get(COMPONENT_KEY) == "one-shot-job"


def _approval_failures(annotations: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    approval = annotations.get(APPROVAL_KEY)
    suspended = spec.get("suspend")
    if approval == "pending":
        return (
            [] if suspended is True else ["pending approval requires spec.suspend=true"]
        )
    if isinstance(approval, str) and APPROVAL_URL.fullmatch(approval):
        return (
            []
            if suspended is False
            else ["approval note URL requires spec.suspend=false"]
        )
    return [f"{APPROVAL_KEY} must be pending or a full GitLab issue note URL"]


def _resource_failures(
    container: dict[str, Any], index: int, container_kind: str = "container"
) -> list[str]:
    resources = _mapping(container.get("resources"))
    requests = _mapping(resources.get("requests"))
    limits = _mapping(resources.get("limits"))
    failures = []
    for resource in ("cpu", "memory", "ephemeral-storage"):
        if not requests.get(resource):
            failures.append(
                f"{container_kind}[{index}] missing resources.requests.{resource}"
            )
        if not limits.get(resource):
            failures.append(
                f"{container_kind}[{index}] missing resources.limits.{resource}"
            )
    security = _mapping(container.get("securityContext"))
    if security.get("allowPrivilegeEscalation") is not False:
        failures.append(
            f"{container_kind}[{index}] must disable privilege escalation"
        )
    if security.get("readOnlyRootFilesystem") is not True:
        failures.append(
            f"{container_kind}[{index}] must use a read-only root filesystem"
        )
    capabilities = _mapping(security.get("capabilities"))
    if "ALL" not in (capabilities.get("drop") or []):
        failures.append(f"{container_kind}[{index}] must drop ALL capabilities")
    return failures


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _artifact_identity_failures(pod_spec: dict[str, Any]) -> list[str]:
    security = _mapping(pod_spec.get("securityContext"))
    run_as_user = security.get("runAsUser")
    failures = []
    if not _positive_integer(run_as_user):
        failures.append(
            "artifact handoff pod securityContext requires a positive runAsUser"
        )
    run_as_group = security.get("runAsGroup")
    if not _positive_integer(run_as_group) or run_as_group != run_as_user:
        failures.append(
            "artifact handoff pod securityContext requires runAsGroup=runAsUser"
        )
    fs_group = security.get("fsGroup")
    if not _positive_integer(fs_group) or fs_group != run_as_user:
        failures.append("artifact handoff pod securityContext requires fsGroup=runAsUser")
    return failures


def _artifact_work_volume_failures(pod_spec: dict[str, Any]) -> list[str]:
    volumes = pod_spec.get("volumes")
    work_volumes = []
    if isinstance(volumes, list):
        work_volumes = [
            volume
            for volume in volumes
            if isinstance(volume, dict) and volume.get("name") == "work"
        ]

    bounded = False
    if len(work_volumes) == 1:
        empty_dir = work_volumes[0].get("emptyDir")
        bounded = isinstance(empty_dir, dict) and bool(empty_dir.get("sizeLimit"))
    return [] if bounded else [ARTIFACT_WORK_VOLUME_FAILURE]


def _artifact_container_failures(
    container: dict[str, Any], run_as_user: Any
) -> list[str]:
    name = container.get("name", "<unnamed>")
    failures = []
    image = container.get("image")
    if not isinstance(image, str) or not re.search(r"@sha256:[0-9a-f]{64}$", image):
        failures.append(
            f"artifact handoff container {name} requires an immutable sha256 digest"
        )
    mounts = container.get("volumeMounts") or []
    if not any(
        isinstance(mount, dict)
        and mount.get("name") == "work"
        and mount.get("mountPath") == "/work"
        for mount in mounts
    ):
        failures.append(
            f"artifact handoff container {name} must mount shared work at /work"
        )
    container_user = _mapping(container.get("securityContext")).get("runAsUser")
    if container_user is not None and container_user != run_as_user:
        failures.append(
            f"artifact handoff container {name} must not override the shared runAsUser"
        )
    return failures


def _artifact_handoff_failures(
    annotations: dict[str, Any], pod_spec: dict[str, Any]
) -> list[str]:
    if annotations.get("ops.addx.io/artifact-handoff") != "true":
        return []

    failures = []
    init_containers = pod_spec.get("initContainers") or []
    containers = pod_spec.get("containers") or []
    init_names = [
        _mapping(container).get("name")
        for container in init_containers
        if isinstance(container, dict)
    ]
    container_names = [
        _mapping(container).get("name")
        for container in containers
        if isinstance(container, dict)
    ]
    if init_names != ["artifact-stage"]:
        failures.append(
            "artifact handoff requires exactly one artifact-stage initContainer"
        )
    if container_names != ["run", "artifact-relay"]:
        failures.append(
            "artifact handoff requires exact run and artifact-relay containers"
        )

    required = [*init_containers, *containers]
    for container in required:
        if not isinstance(container, dict):
            continue
        run_as_user = _mapping(pod_spec.get("securityContext")).get("runAsUser")
        failures.extend(_artifact_container_failures(container, run_as_user))

    helper_image_mismatch = (
        init_names == ["artifact-stage"]
        and container_names == ["run", "artifact-relay"]
        and _mapping(init_containers[0]).get("image")
        != _mapping(containers[1]).get("image")
    )
    if helper_image_mismatch:
        failures.append(
            "artifact-stage and artifact-relay must use the same helper image"
        )
    failures.extend(_artifact_identity_failures(pod_spec))
    failures.extend(_artifact_work_volume_failures(pod_spec))
    return failures


def _init_container_failures(pod_spec: dict[str, Any]) -> list[str]:
    init_containers = pod_spec.get("initContainers") or []
    if not isinstance(init_containers, list):
        return ["pod spec initContainers must be a list"]

    failures = []
    for index, container in enumerate(init_containers):
        if not isinstance(container, dict):
            failures.append(f"initContainer[{index}] must be a mapping")
            continue
        failures.extend(
            _resource_failures(container, index, container_kind="initContainer")
        )
    return failures


def _pod_failures(pod_spec: dict[str, Any]) -> list[str]:
    failures = []
    expected = {
        "automountServiceAccountToken": False,
        "restartPolicy": "Never",
    }
    for field, value in expected.items():
        if pod_spec.get(field) != value:
            failures.append(f"pod spec requires {field}={value}")
    if not pod_spec.get("serviceAccountName"):
        failures.append("pod spec requires explicit serviceAccountName")
    if not pod_spec.get("imagePullSecrets"):
        failures.append("pod spec requires imagePullSecrets")

    pod_security = _mapping(pod_spec.get("securityContext"))
    if pod_security.get("runAsNonRoot") is not True:
        failures.append("pod securityContext requires runAsNonRoot=true")
    if _mapping(pod_security.get("seccompProfile")).get("type") != "RuntimeDefault":
        failures.append("pod securityContext requires seccompProfile RuntimeDefault")

    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or not containers:
        failures.append("pod spec requires at least one container")
    else:
        for index, container in enumerate(containers):
            if not isinstance(container, dict):
                failures.append(f"container[{index}] must be a mapping")
                continue
            failures.extend(_resource_failures(container, index))

    failures.extend(_init_container_failures(pod_spec))

    empty_dirs = [
        _mapping(_mapping(volume).get("emptyDir"))
        for volume in (pod_spec.get("volumes") or [])
        if isinstance(volume, dict) and "emptyDir" in volume
    ]
    if not empty_dirs:
        failures.append("pod spec requires a bounded emptyDir")
    elif any(not empty_dir.get("sizeLimit") for empty_dir in empty_dirs):
        failures.append("every emptyDir requires sizeLimit")
    return failures


def _pod_template_failures(template: dict[str, Any]) -> list[str]:
    failures = []
    pod_metadata = _mapping(template.get("metadata"))
    pod_labels = _mapping(pod_metadata.get("labels"))
    for label in ("app", "env"):
        if not pod_labels.get(label):
            failures.append(f"pod template requires non-empty {label} label")
    failures.extend(_pod_failures(_mapping(template.get("spec"))))
    return failures


def _job_failures(doc: dict[str, Any]) -> list[str]:
    metadata = _mapping(doc.get("metadata"))
    annotations = _mapping(metadata.get("annotations"))
    labels = _mapping(metadata.get("labels"))
    spec = _mapping(doc.get("spec"))
    failures = _approval_failures(annotations, spec)

    if labels.get(COMPONENT_KEY) != "one-shot-job":
        failures.append(f"metadata.labels.{COMPONENT_KEY} must be one-shot-job")
    if any(key.startswith("argocd.argoproj.io/hook") for key in annotations):
        failures.append("one-shot Job must not be an Argo CD hook")
    if "ttlSecondsAfterFinished" in spec:
        failures.append("one-shot Job must not set ttlSecondsAfterFinished")

    expected = {"completions": 1, "parallelism": 1, "backoffLimit": 0}
    for field, value in expected.items():
        if spec.get(field) != value:
            failures.append(f"spec.{field} must equal {value}")
    deadline = spec.get("activeDeadlineSeconds")
    if not isinstance(deadline, int) or deadline <= 0:
        failures.append("spec.activeDeadlineSeconds must be a positive integer")

    template = _mapping(spec.get("template"))
    failures.extend(_pod_template_failures(template))
    pod_spec = _mapping(template.get("spec"))
    failures.extend(_artifact_handoff_failures(annotations, pod_spec))
    return failures


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_one_shot_job.py <directory>", file=sys.stderr)
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
            if not isinstance(doc, dict) or not _one_shot_job(doc):
                continue
            checked += 1
            name = _mapping(doc.get("metadata")).get("name", "<unnamed>")
            failures.extend(
                f"FAIL: {file}: Job/{name}: {failure}" for failure in _job_failures(doc)
            )

    if failures:
        print("\n".join(failures))
        print(f"FAIL: {len(failures)} guarded one-shot Job violation(s)")
        return 1
    if checked == 0:
        print(f"PASS: no guarded one-shot Jobs under {root} (nothing to check)")
        return 0
    print(f"PASS: checked {checked} guarded one-shot Job(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
