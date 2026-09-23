#!/usr/bin/env python3
"""Validate guarded periodic CronJobs produced by the new-cronjob workflow."""

from __future__ import annotations

import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs PyYAML.
    print("FAIL: PyYAML not installed. install: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


ACTIVATION_KEY = "ops.addx.io/activation-review"
COMPONENT_KEY = "app.kubernetes.io/component"
COMPONENT_VALUE = "scheduled-job"
REVIEW_URL = re.compile(
    r"^https://gitlab\.addx\.ai/"
    r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+/-/merge_requests/[1-9][0-9]*$"
)
DIGEST = re.compile(r"^[0-9a-f]{64}$")
TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
TIME_ZONE = re.compile(r"^(?:UTC|[A-Za-z0-9._+-]+/[A-Za-z0-9._+-]+)$")
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
DNS_SUBDOMAIN = re.compile(
    r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$"
)
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CPU_QUANTITY = re.compile(r"^(?:[0-9]+m|[0-9]+(?:\.[0-9]+)?)$")
BYTE_QUANTITY = re.compile(r"^[0-9]+(?:Ei|Pi|Ti|Gi|Mi|Ki|E|P|T|G|M|K)$")
UNSAFE_PROCESS_WRAPPERS = {"ash", "bash", "dash", "env", "ksh", "sh", "zsh"}
INT32_MAX = 2**31 - 1
INT64_MAX = 2**63 - 1
MONTH_NAMES = {
    name: index
    for index, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
        start=1,
    )
}
DAY_NAMES = {
    name: index
    for index, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))
}
CRON_FIELDS = (
    (0, 59, {}),
    (0, 23, {}),
    (1, 31, {}),
    (1, 12, MONTH_NAMES),
    (0, 7, DAY_NAMES),
)


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate mapping key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _non_negative_integer(value: Any, maximum: int = INT64_MAX) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= maximum
    )


def _positive_integer(value: Any, maximum: int = INT64_MAX) -> bool:
    return _non_negative_integer(value, maximum) and value > 0


def _non_empty_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _positive_quantity(value: Any, pattern: re.Pattern[str]) -> bool:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        return False
    numeric = re.match(r"^[0-9]+(?:\.[0-9]+)?", value)
    if numeric is None:
        return False
    try:
        return Decimal(numeric.group()) > 0
    except InvalidOperation:
        return False


def _unknown_fields(
    value: dict[str, Any], allowed: set[str], location: str
) -> list[str]:
    failures = []
    for field in sorted(value, key=lambda item: str(item)):
        if not isinstance(field, str):
            failures.append(f"{location} contains an unsupported non-string field")
        elif field not in allowed:
            failures.append(f"{location} contains unsupported field {field}")
    return failures


def _string_map_failures(value: Any, location: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{location} must be a mapping"]
    if not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        return [f"{location} keys and values must be strings"]
    return []


def _cron_value(
    value: str, minimum: int, maximum: int, names: dict[str, int]
) -> int | None:
    normalized = value.upper()
    if normalized in names:
        return names[normalized]
    if not value.isdigit():
        return None
    number = int(value)
    return number if minimum <= number <= maximum else None


def _cron_field(
    field: str, minimum: int, maximum: int, names: dict[str, int]
) -> bool:
    for item in field.split(","):
        base, separator, step = item.partition("/")
        if separator and (not step.isdigit() or int(step) <= 0 or "/" in step):
            return False
        if base in {"*", "?"}:
            continue
        if "-" in base:
            start, dash, end = base.partition("-")
            if not dash or "-" in end:
                return False
            start_value = _cron_value(start, minimum, maximum, names)
            end_value = _cron_value(end, minimum, maximum, names)
            if start_value is None or end_value is None or start_value > end_value:
                return False
            continue
        if _cron_value(base, minimum, maximum, names) is None:
            return False
    return True


def _standard_five_field_schedule(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    fields = value.split()
    return len(fields) == 5 and all(
        _cron_field(field, minimum, maximum, names)
        for field, (minimum, maximum, names) in zip(fields, CRON_FIELDS)
    )


def _valid_time_zone(value: Any) -> bool:
    if not isinstance(value, str) or not TIME_ZONE.fullmatch(value):
        return False
    if value in {"UTC", "Etc/UTC"}:
        return True
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _immutable_tag_and_digest(value: Any) -> bool:
    if not isinstance(value, str) or any(character.isspace() for character in value):
        return False
    image, separator, digest = value.rpartition("@sha256:")
    if not separator or not DIGEST.fullmatch(digest):
        return False
    final_component = image.rsplit("/", 1)[-1]
    repository, tag_separator, tag = final_component.rpartition(":")
    return bool(repository and tag_separator and TAG.fullmatch(tag))


def _guarded_cronjob(doc: dict[str, Any]) -> bool:
    if doc.get("apiVersion") != "batch/v1" or doc.get("kind") != "CronJob":
        return False
    metadata = _mapping(doc.get("metadata"))
    annotations = _mapping(metadata.get("annotations"))
    labels = _mapping(metadata.get("labels"))
    return ACTIVATION_KEY in annotations or labels.get(COMPONENT_KEY) == COMPONENT_VALUE


def _activation_failures(
    annotations: dict[str, Any], spec: dict[str, Any], require_registration: bool
) -> list[str]:
    review = annotations.get(ACTIVATION_KEY)
    suspended = spec.get("suspend")
    failures: list[str] = []
    if review == "pending":
        if suspended is not True:
            failures.append("pending activation review requires spec.suspend=true")
    elif isinstance(review, str) and REVIEW_URL.fullmatch(review):
        if suspended is not False:
            failures.append("activation review URL requires spec.suspend=false")
    else:
        failures.append(
            f"{ACTIVATION_KEY} must be pending or a full GitLab merge request URL"
        )
    if require_registration and (review != "pending" or suspended is not True):
        failures.append(
            "registration validation requires activation-review=pending and spec.suspend=true"
        )
    return failures


def _reference_failures(
    value: Any,
    location: str,
    *,
    allow_optional: bool = True,
    extra_allowed: set[str] | None = None,
) -> list[str]:
    reference = _mapping(value)
    allowed = {"name", "optional"} if allow_optional else {"name"}
    allowed.update(extra_allowed or set())
    failures = _unknown_fields(reference, allowed, location)
    name = reference.get("name")
    if not isinstance(name, str) or not DNS_SUBDOMAIN.fullmatch(name) or len(name) > 253:
        failures.append(f"{location}.name must be a DNS subdomain string")
    if (
        allow_optional
        and "optional" in reference
        and not isinstance(reference.get("optional"), bool)
    ):
        failures.append(f"{location}.optional must be boolean when provided")
    return failures


def _env_key_selector_failures(source: Any, location: str) -> list[str]:
    source_mapping = _mapping(source)
    failures = _unknown_fields(
        source_mapping, {"key", "name", "optional"}, location
    )
    failures.extend(
        _reference_failures(source_mapping, location, extra_allowed={"key"})
    )
    if not isinstance(source_mapping.get("key"), str) or not source_mapping.get("key"):
        failures.append(f"{location}.key must be a non-empty string")
    return failures


def _env_field_ref_failures(source: Any, location: str) -> list[str]:
    source_mapping = _mapping(source)
    failures = _unknown_fields(
        source_mapping, {"apiVersion", "fieldPath"}, location
    )
    if not isinstance(source_mapping.get("fieldPath"), str) or not source_mapping.get(
        "fieldPath"
    ):
        failures.append(f"{location}.fieldPath must be a non-empty string")
    if "apiVersion" in source_mapping and not isinstance(
        source_mapping.get("apiVersion"), str
    ):
        failures.append(f"{location}.apiVersion must be a string")
    return failures


def _env_resource_field_ref_failures(source: Any, location: str) -> list[str]:
    source_mapping = _mapping(source)
    failures = _unknown_fields(
        source_mapping, {"containerName", "divisor", "resource"}, location
    )
    if not isinstance(source_mapping.get("resource"), str) or not source_mapping.get(
        "resource"
    ):
        failures.append(f"{location}.resource must be a non-empty string")
    for field in ("containerName", "divisor"):
        if field in source_mapping and not isinstance(source_mapping.get(field), str):
            failures.append(f"{location}.{field} must be a string")
    return failures


def _env_value_from_source_failures(
    source_name: Any, source: Any, location: str
) -> list[str]:
    if source_name in {"configMapKeyRef", "secretKeyRef"}:
        return _env_key_selector_failures(source, location)
    if source_name == "fieldRef":
        return _env_field_ref_failures(source, location)
    if source_name == "resourceFieldRef":
        return _env_resource_field_ref_failures(source, location)
    return []


def _env_value_from_failures(value: Any, location: str) -> list[str]:
    value_from = _mapping(value)
    allowed = {"configMapKeyRef", "fieldRef", "resourceFieldRef", "secretKeyRef"}
    failures = _unknown_fields(value_from, allowed, location)
    if len(value_from) != 1:
        failures.append(f"{location} requires exactly one supported source")
        return failures

    source_name, source = next(iter(value_from.items()))
    failures.extend(
        _env_value_from_source_failures(
            source_name, source, f"{location}.{source_name}"
        )
    )
    return failures


def _env_failures(value: Any, index: int) -> list[str]:
    location = f"container[0].env[{index}]"
    env = _mapping(value)
    failures = _unknown_fields(env, {"name", "value", "valueFrom"}, location)
    name = env.get("name")
    if not isinstance(name, str) or not ENV_NAME.fullmatch(name):
        failures.append(f"{location}.name must be a valid environment variable name")

    has_value = "value" in env
    has_value_from = "valueFrom" in env
    if has_value == has_value_from:
        failures.append(f"{location} requires exactly one of value or valueFrom")
    elif has_value and not isinstance(env.get("value"), str):
        failures.append(f"{location}.value must be a string")
    elif has_value_from:
        failures.extend(
            _env_value_from_failures(env.get("valueFrom"), f"{location}.valueFrom")
        )
    return failures


def _env_from_failures(value: Any, index: int) -> list[str]:
    location = f"container[0].envFrom[{index}]"
    env_from = _mapping(value)
    failures = _unknown_fields(env_from, {"prefix", "configMapRef", "secretRef"}, location)
    if "prefix" in env_from and not isinstance(env_from.get("prefix"), str):
        failures.append(f"{location}.prefix must be a string")
    references = [field for field in ("configMapRef", "secretRef") if field in env_from]
    if len(references) != 1:
        failures.append(f"{location} requires exactly one of configMapRef or secretRef")
    else:
        field = references[0]
        failures.extend(_reference_failures(env_from.get(field), f"{location}.{field}"))
    return failures


def _resource_and_security_failures(
    container: dict[str, Any], index: int
) -> list[str]:
    failures: list[str] = []
    resources = _mapping(container.get("resources"))
    failures.extend(
        _unknown_fields(
            resources, {"requests", "limits"}, f"container[{index}].resources"
        )
    )
    requests = _mapping(resources.get("requests"))
    limits = _mapping(resources.get("limits"))
    for location, values in (("requests", requests), ("limits", limits)):
        failures.extend(
            _unknown_fields(
                values,
                {"cpu", "memory", "ephemeral-storage"},
                f"container[{index}].resources.{location}",
            )
        )
        if not _positive_quantity(values.get("cpu"), CPU_QUANTITY):
            failures.append(
                f"container[{index}] resources.{location}.cpu must be a positive CPU quantity"
            )
        for resource in ("memory", "ephemeral-storage"):
            if not _positive_quantity(values.get(resource), BYTE_QUANTITY):
                failures.append(
                    f"container[{index}] resources.{location}.{resource} "
                    "must be a positive byte quantity with an explicit unit"
                )

    security = _mapping(container.get("securityContext"))
    failures.extend(
        _unknown_fields(
            security,
            {"allowPrivilegeEscalation", "readOnlyRootFilesystem", "capabilities"},
            f"container[{index}].securityContext",
        )
    )
    if security.get("allowPrivilegeEscalation") is not False:
        failures.append(f"container[{index}] must disable privilege escalation")
    if security.get("readOnlyRootFilesystem") is not True:
        failures.append(f"container[{index}] must use a read-only root filesystem")
    capabilities = _mapping(security.get("capabilities"))
    failures.extend(
        _unknown_fields(capabilities, {"drop"}, f"container[{index}].securityContext.capabilities")
    )
    if capabilities.get("drop") != ["ALL"]:
        failures.append(f"container[{index}] must drop exactly ALL capabilities")
    return failures


def _container_failures(container: dict[str, Any], index: int) -> list[str]:
    location = f"container[{index}]"
    failures = _unknown_fields(
        container,
        {
            "args",
            "command",
            "env",
            "envFrom",
            "image",
            "imagePullPolicy",
            "name",
            "resources",
            "securityContext",
            "volumeMounts",
        },
        location,
    )
    if container.get("name") != "run":
        failures.append(f"{location} must use the fixed name run")
    if not _immutable_tag_and_digest(container.get("image")):
        failures.append(f"{location} requires an immutable tag plus sha256 digest image")
    if container.get("imagePullPolicy") != "IfNotPresent":
        failures.append(f"{location} requires imagePullPolicy=IfNotPresent")
    command = container.get("command")
    if not _non_empty_string_list(command):
        failures.append(f"{location} requires a non-empty command list")
    elif Path(command[0]).name.lower() in UNSAFE_PROCESS_WRAPPERS:
        failures.append(f"{location} must not launch through a shell or env wrapper")
    if not _non_empty_string_list(container.get("args")):
        failures.append(f"{location} requires a non-empty args list")

    env = container.get("env")
    if not isinstance(env, list):
        failures.append(f"{location}.env must be an explicit list")
    else:
        env_names = [item.get("name") for item in env if isinstance(item, dict)]
        if len(env_names) != len(set(env_names)):
            failures.append(f"{location}.env names must be unique")
        for env_index, item in enumerate(env):
            if not isinstance(item, dict):
                failures.append(f"{location}.env[{env_index}] must be a mapping")
            else:
                failures.extend(_env_failures(item, env_index))

    env_from = container.get("envFrom")
    if not isinstance(env_from, list):
        failures.append(f"{location}.envFrom must be an explicit list")
    else:
        for env_from_index, item in enumerate(env_from):
            if not isinstance(item, dict):
                failures.append(f"{location}.envFrom[{env_from_index}] must be a mapping")
            else:
                failures.extend(_env_from_failures(item, env_from_index))

    mounts = container.get("volumeMounts")
    expected_mount = [{"name": "temp", "mountPath": "/tmp"}]
    if mounts != expected_mount:
        failures.append(f"{location} requires exactly the bounded temp volume mounted at /tmp")
    failures.extend(_resource_and_security_failures(container, index))
    return failures


def _placement_failures(pod_spec: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    node_selector = pod_spec.get("nodeSelector")
    if not isinstance(node_selector, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in node_selector.items()
    ):
        failures.append("pod spec requires an explicit string nodeSelector mapping")
    if not isinstance(pod_spec.get("affinity"), dict):
        failures.append("pod spec requires an explicit affinity mapping")
    tolerations = pod_spec.get("tolerations")
    if not isinstance(tolerations, list) or not all(
        isinstance(item, dict) for item in tolerations
    ):
        failures.append("pod spec requires an explicit tolerations list of mappings")
    return failures


def _pod_failures(pod_spec: dict[str, Any]) -> list[str]:
    failures = _unknown_fields(
        pod_spec,
        {
            "affinity",
            "automountServiceAccountToken",
            "containers",
            "imagePullSecrets",
            "nodeSelector",
            "restartPolicy",
            "securityContext",
            "serviceAccountName",
            "terminationGracePeriodSeconds",
            "tolerations",
            "volumes",
        },
        "pod spec",
    )
    if pod_spec.get("automountServiceAccountToken") is not False:
        failures.append("pod spec requires automountServiceAccountToken=false")
    service_account = pod_spec.get("serviceAccountName")
    if (
        not isinstance(service_account, str)
        or not DNS_SUBDOMAIN.fullmatch(service_account)
        or len(service_account) > 253
    ):
        failures.append("pod spec requires an explicit DNS subdomain serviceAccountName")
    if pod_spec.get("restartPolicy") != "Never":
        failures.append("pod spec requires restartPolicy=Never")
    if not _positive_integer(pod_spec.get("terminationGracePeriodSeconds"), INT64_MAX):
        failures.append("pod spec requires a positive terminationGracePeriodSeconds")

    image_pull_secrets = pod_spec.get("imagePullSecrets")
    if not isinstance(image_pull_secrets, list) or not image_pull_secrets:
        failures.append("pod spec requires a non-empty imagePullSecrets list")
    else:
        for index, item in enumerate(image_pull_secrets):
            failures.extend(
                _reference_failures(
                    item,
                    f"pod spec.imagePullSecrets[{index}]",
                    allow_optional=False,
                )
            )
    failures.extend(_placement_failures(pod_spec))

    pod_security = _mapping(pod_spec.get("securityContext"))
    failures.extend(
        _unknown_fields(
            pod_security,
            {"fsGroup", "runAsGroup", "runAsNonRoot", "runAsUser", "seccompProfile"},
            "pod securityContext",
        )
    )
    run_as_user = pod_security.get("runAsUser")
    if pod_security.get("runAsNonRoot") is not True:
        failures.append("pod securityContext requires runAsNonRoot=true")
    if not _positive_integer(run_as_user, INT64_MAX):
        failures.append("pod securityContext requires a positive runAsUser")
    if pod_security.get("runAsGroup") != run_as_user:
        failures.append("pod securityContext requires runAsGroup=runAsUser")
    if pod_security.get("fsGroup") != run_as_user:
        failures.append("pod securityContext requires fsGroup=runAsUser")
    seccomp = _mapping(pod_security.get("seccompProfile"))
    failures.extend(
        _unknown_fields(seccomp, {"type"}, "pod securityContext.seccompProfile")
    )
    if seccomp.get("type") != "RuntimeDefault":
        failures.append("pod securityContext requires seccompProfile RuntimeDefault")

    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        failures.append("pod spec requires exactly one run container")
    elif not isinstance(containers[0], dict):
        failures.append("container[0] must be a mapping")
    else:
        failures.extend(_container_failures(containers[0], 0))

    volumes = pod_spec.get("volumes")
    expected_volumes = {"name": "temp", "emptyDir": None}
    if not isinstance(volumes, list) or len(volumes) != 1 or not isinstance(volumes[0], dict):
        failures.append("pod spec requires exactly one bounded temp emptyDir")
    else:
        volume = volumes[0]
        failures.extend(_unknown_fields(volume, set(expected_volumes), "pod spec.volumes[0]"))
        empty_dir = _mapping(volume.get("emptyDir"))
        failures.extend(_unknown_fields(empty_dir, {"sizeLimit"}, "pod spec.volumes[0].emptyDir"))
        if volume.get("name") != "temp" or not _positive_quantity(
            empty_dir.get("sizeLimit"), BYTE_QUANTITY
        ):
            failures.append("pod spec requires exactly one bounded temp emptyDir")
    return failures


def _metadata_failures(metadata: dict[str, Any]) -> list[str]:
    failures = _unknown_fields(
        metadata, {"annotations", "labels", "name", "namespace"}, "metadata"
    )
    name = metadata.get("name")
    if (
        not isinstance(name, str)
        or not DNS_SUBDOMAIN.fullmatch(name)
        or len(name) > 52
    ):
        failures.append("metadata.name must be a CronJob-safe DNS name of at most 52 characters")
    namespace = metadata.get("namespace")
    if (
        not isinstance(namespace, str)
        or not DNS_LABEL.fullmatch(namespace)
        or len(namespace) > 63
    ):
        failures.append("metadata.namespace must be a DNS label string")
    failures.extend(_string_map_failures(metadata.get("annotations"), "metadata.annotations"))
    failures.extend(_string_map_failures(metadata.get("labels"), "metadata.labels"))
    labels = _mapping(metadata.get("labels"))
    if labels.get(COMPONENT_KEY) != COMPONENT_VALUE:
        failures.append(f"metadata.labels.{COMPONENT_KEY} must be {COMPONENT_VALUE}")
    for label in ("app", "app.kubernetes.io/name"):
        if not isinstance(labels.get(label), str) or not labels.get(label):
            failures.append(f"metadata.labels.{label} must be a non-empty string")
    return failures


def _template_metadata_failures(metadata: dict[str, Any], location: str) -> list[str]:
    failures = _unknown_fields(metadata, {"labels"}, location)
    failures.extend(_string_map_failures(metadata.get("labels"), f"{location}.labels"))
    labels = _mapping(metadata.get("labels"))
    for label in ("app", "app.kubernetes.io/name", COMPONENT_KEY):
        if not isinstance(labels.get(label), str) or not labels.get(label):
            failures.append(f"{location}.labels.{label} must be a non-empty string")
    if labels.get(COMPONENT_KEY) != COMPONENT_VALUE:
        failures.append(f"{location}.labels.{COMPONENT_KEY} must be {COMPONENT_VALUE}")
    return failures


def _cronjob_failures(
    doc: dict[str, Any], require_registration: bool = False
) -> list[str]:
    failures = _unknown_fields(doc, {"apiVersion", "kind", "metadata", "spec"}, "CronJob")
    metadata = _mapping(doc.get("metadata"))
    annotations = _mapping(metadata.get("annotations"))
    spec = _mapping(doc.get("spec"))
    failures.extend(_metadata_failures(metadata))
    failures.extend(_activation_failures(annotations, spec, require_registration))
    failures.extend(
        _unknown_fields(
            spec,
            {
                "concurrencyPolicy",
                "failedJobsHistoryLimit",
                "jobTemplate",
                "schedule",
                "startingDeadlineSeconds",
                "successfulJobsHistoryLimit",
                "suspend",
                "timeZone",
            },
            "spec",
        )
    )

    if not _standard_five_field_schedule(spec.get("schedule")):
        failures.append("spec.schedule must be a standard five-field cron expression")
    if "timeZone" in spec and not _valid_time_zone(spec.get("timeZone")):
        failures.append("spec.timeZone must be a real UTC or IANA Area/Location timezone")
    if spec.get("concurrencyPolicy") not in {"Allow", "Forbid", "Replace"}:
        failures.append("spec.concurrencyPolicy must be Allow, Forbid, or Replace")
    if not _positive_integer(spec.get("startingDeadlineSeconds"), INT64_MAX):
        failures.append("spec.startingDeadlineSeconds must be a positive integer")
    for field in ("successfulJobsHistoryLimit", "failedJobsHistoryLimit"):
        if not _non_negative_integer(spec.get(field), INT32_MAX):
            failures.append(f"spec.{field} must be a non-negative integer")

    job_template = _mapping(spec.get("jobTemplate"))
    failures.extend(_unknown_fields(job_template, {"metadata", "spec"}, "jobTemplate"))
    failures.extend(
        _template_metadata_failures(_mapping(job_template.get("metadata")), "jobTemplate.metadata")
    )
    job_spec = _mapping(job_template.get("spec"))
    failures.extend(
        _unknown_fields(
            job_spec,
            {"activeDeadlineSeconds", "backoffLimit", "template"},
            "jobTemplate.spec",
        )
    )
    if not _non_negative_integer(job_spec.get("backoffLimit"), INT32_MAX):
        failures.append("jobTemplate.spec.backoffLimit must be a non-negative integer")
    if not _positive_integer(job_spec.get("activeDeadlineSeconds"), INT64_MAX):
        failures.append(
            "jobTemplate.spec.activeDeadlineSeconds must be a positive integer"
        )
    template = _mapping(job_spec.get("template"))
    failures.extend(_unknown_fields(template, {"metadata", "spec"}, "pod template"))
    pod_metadata = _mapping(template.get("metadata"))
    failures.extend(_template_metadata_failures(pod_metadata, "pod template.metadata"))
    pod_labels = _mapping(pod_metadata.get("labels"))
    if not isinstance(pod_labels.get("env"), str) or not pod_labels.get("env"):
        failures.append("pod template requires non-empty env label")
    failures.extend(_pod_failures(_mapping(template.get("spec"))))
    return failures


def _yaml_paths(root: Path) -> tuple[list[Path] | None, str | None]:
    if root.is_symlink():
        return None, "scan root must not be a symlink"
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return None, "scan root cannot be resolved safely"
    paths = sorted({*root.rglob("*.yaml"), *root.rglob("*.yml")})
    for path in paths:
        if path.is_symlink():
            return None, f"YAML input must not be a symlink: {path.relative_to(root)}"
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return None, f"YAML input cannot be resolved safely: {path.relative_to(root)}"
        if not resolved.is_relative_to(resolved_root) or not path.is_file():
            return None, f"YAML input escapes the scan root: {path.relative_to(root)}"
    return paths, None


def _load_documents(path: Path) -> list[Any] | None:
    try:
        return list(yaml.load_all(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def _parse_args(argv: list[str]) -> tuple[Path, bool] | None:
    args = argv[1:]
    require_registration = False
    if args and args[0] == "--require-registration":
        require_registration = True
        args = args[1:]
    if len(args) != 1:
        print(
            "usage: check_cronjob.py [--require-registration] <directory>",
            file=sys.stderr,
        )
        return None
    root = Path(args[0])
    if not root.is_dir():
        print("FAIL: CronJob manifest directory not found", file=sys.stderr)
        return None
    return root, require_registration


def main(argv: list[str]) -> int:
    parsed = _parse_args(argv)
    if parsed is None:
        return 2
    root, require_registration = parsed
    paths, path_failure = _yaml_paths(root)
    if path_failure is not None:
        print(f"FAIL: {path_failure}")
        return 2
    assert paths is not None

    failures: list[str] = []
    checked = 0
    for file in paths:
        docs = _load_documents(file)
        if docs is None:
            print(f"FAIL: {file.relative_to(root)}: invalid or duplicate YAML")
            return 2
        for doc in docs:
            if not isinstance(doc, dict) or not _guarded_cronjob(doc):
                continue
            checked += 1
            name = _mapping(doc.get("metadata")).get("name", "<unnamed>")
            failures.extend(
                f"FAIL: {file.relative_to(root)}: CronJob/{name}: {failure}"
                for failure in _cronjob_failures(doc, require_registration)
            )

    if failures:
        print("\n".join(failures))
        print(f"FAIL: {len(failures)} guarded CronJob violation(s)")
        return 1
    if checked == 0:
        if require_registration:
            print("FAIL: registration validation found no guarded CronJob")
            return 1
        print(f"PASS: no guarded CronJobs under {root} (nothing to check)")
        return 0
    print(f"PASS: checked {checked} guarded CronJob(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
