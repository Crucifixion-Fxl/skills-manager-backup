#!/usr/bin/env python3
"""Validate a rendered legacy-live target is mechanically dormant.

Usage:
  check_dormant_target.py --app <app> --namespace <namespace> \
    --expected-kind Deployment|Rollout --phase dormant \
    [<rendered-multi-document-yaml>|-]

  check_dormant_target.py --app <app> --namespace <namespace> \
    --expected-kind Deployment|Rollout \
    --phase activation-transition|rollback-transition \
    --baseline-render <rendered-yaml> --approved-replicas <count> \
    --evidence-ref <audited-ref> <candidate-rendered-yaml>

The input defaults to stdin. Exit codes: 0 pass, 1 contract violation,
2 usage, dependency, or unreadable/invalid input.
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from _legacy_target_common import (
    SafeInputError,
    is_stable_evidence_ref,
    load_single_yaml,
    safe_read_bytes,
)
from check_legacy_target_handoff_evidence import (
    validate_evidence,
    validate_evidence_progression,
)

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs PyYAML.
    print("FAIL: PyYAML not installed. install: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


MAX_RENDER_BYTES = 16 * 1024 * 1024
MAX_DOCUMENTS = 2048
MAX_YAML_TOKENS = 262_144
MAX_TRAVERSAL_NODES = 65_536
MAX_TRAVERSAL_DEPTH = 128
MAX_INTEGER_SCALAR_CHARS = 4_096
MAX_EVIDENCE_BYTES = 256 * 1024
MAX_EVIDENCE_TOKENS = 16_384
MAX_CHANGED_PATH_BYTES = 256 * 1024
MAX_CHANGED_PATHS = 4_096
MAX_TRACKED_PATH_BYTES = 16 * 1024 * 1024
MAX_TRACKED_PATHS = 200_000
MAX_KUSTOMIZATION_BYTES = 1024 * 1024
MAX_KUSTOMIZATION_TOKENS = 65_536
MAX_KUSTOMIZATION_FILES = 4_096
APP_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
NAMESPACE_NAME = APP_NAME
CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")
INTEGER_LIKE_SCALAR = re.compile(r"^[+\-0-9a-fA-F_xXbBoO:]+$")
EXPECTED_API_VERSIONS = {
    "Deployment": "apps/v1",
    "Rollout": "argoproj.io/v1alpha1",
}
IDENTITY_LABELS = ("app", "app.kubernetes.io/name")
RUNTIME_STATE_ANNOTATION = "migrations.addx.io/runtime-state"
RUNTIME_STATES = {"dormant", "active"}
KUSTOMIZATION_NAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
REMOTE_REFERENCE_MARKERS = ("://", "git::", "git@", "?", "#")
FORBIDDEN_KUSTOMIZE_EXECUTION_FIELDS = {
    "generators",
    "transformers",
    "validators",
    "helmCharts",
    "helmGlobals",
}
ALLOWED_KUSTOMIZATION_FIELDS = {
    "apiVersion",
    "kind",
    "metadata",
    "namePrefix",
    "nameSuffix",
    "namespace",
    "commonLabels",
    "labels",
    "commonAnnotations",
    "resources",
    "bases",
    "components",
    "images",
    "replicas",
    "patches",
    "patchesStrategicMerge",
    "patchesJson6902",
    "replacements",
    "vars",
    "configMapGenerator",
    "secretGenerator",
    "generatorOptions",
    "configurations",
    "crds",
    "openapi",
    "buildMetadata",
    "sortOptions",
}

# These resources can create application Pods directly or through a workload
# controller and are never valid in a legacy-live dormant target render. The
# structural fallback below also catches unknown CRs that embed a Pod template.
DIRECT_POD_PRODUCERS = {
    ("", "Pod"),
    ("", "ReplicationController"),
    ("apps", "DaemonSet"),
    ("apps", "ReplicaSet"),
    ("apps", "StatefulSet"),
    ("apps.openshift.io", "DeploymentConfig"),
    ("argoproj.io", "CronWorkflow"),
    ("argoproj.io", "Experiment"),
    ("argoproj.io", "Workflow"),
    ("batch", "CronJob"),
    ("batch", "Job"),
    ("flink.apache.org", "FlinkDeployment"),
    ("flink.apache.org", "FlinkSessionJob"),
    ("kruise.io", "AdvancedStatefulSet"),
    ("kruise.io", "BroadcastJob"),
    ("kruise.io", "CloneSet"),
    ("kruise.io", "UnitedDeployment"),
    ("kubeflow.org", "MPIJob"),
    ("kubeflow.org", "PaddleJob"),
    ("kubeflow.org", "PyTorchJob"),
    ("kubeflow.org", "TFJob"),
    ("kubeflow.org", "XGBoostJob"),
    ("ray.io", "RayCluster"),
    ("ray.io", "RayJob"),
    ("ray.io", "RayService"),
    ("serving.knative.dev", "Configuration"),
    ("serving.knative.dev", "Revision"),
    ("serving.knative.dev", "Service"),
    ("sparkoperator.k8s.io", "ScheduledSparkApplication"),
    ("sparkoperator.k8s.io", "SparkApplication"),
    ("tekton.dev", "PipelineRun"),
    ("tekton.dev", "TaskRun"),
}
AUTOSCALERS = {
    ("autoscaling", "HorizontalPodAutoscaler"),
    ("autoscaling.k8s.io", "VerticalPodAutoscaler"),
    ("keda.sh", "ScaledJob"),
    ("keda.sh", "ScaledObject"),
}
DIRECT_TRAFFIC_RESOURCES = {
    ("", "Endpoints"),
    ("discovery.k8s.io", "EndpointSlice"),
    ("networking.k8s.io", "Ingress"),
}
ALLOWED_SUPPORT_RESOURCES = {
    ("", "ConfigMap"),
    ("", "Namespace"),
    ("", "Service"),
    ("", "ServiceAccount"),
    ("cert-manager.io", "Certificate"),
    ("cert-manager.io", "Issuer"),
    ("external-secrets.io", "ExternalSecret"),
    ("external-secrets.io", "SecretStore"),
    ("monitoring.coreos.com", "PodMonitor"),
    ("monitoring.coreos.com", "PrometheusRule"),
    ("monitoring.coreos.com", "ServiceMonitor"),
    ("networking.k8s.io", "NetworkPolicy"),
    ("operator.victoriametrics.com", "VMPodScrape"),
    ("operator.victoriametrics.com", "VMRule"),
    ("operator.victoriametrics.com", "VMServiceScrape"),
    ("platform.addx.io", "Database"),
    ("platform.addx.io", "KafkaScramCredential"),
    ("platform.addx.io", "ObjectBucket"),
    ("policy", "PodDisruptionBudget"),
    ("rbac.authorization.k8s.io", "Role"),
    ("rbac.authorization.k8s.io", "RoleBinding"),
    ("secrets-store.csi.x-k8s.io", "SecretProviderClass"),
}


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


def _api_group(api_version: str) -> str:
    return api_version.split("/", 1)[0] if "/" in api_version else ""


def _safe_identity_text(value: str) -> bool:
    return len(value) <= 512 and CONTROL_CHAR.search(value) is None


def _has_embedded_pod_template(value: Any, parent_key: str = "") -> bool:
    stack: list[tuple[Any, str, int]] = [(value, parent_key, 0)]
    visited: set[int] = set()
    visited_nodes = 0

    while stack:
        current, current_parent, depth = stack.pop()
        visited_nodes += 1
        if visited_nodes > MAX_TRAVERSAL_NODES or depth > MAX_TRAVERSAL_DEPTH:
            raise ValueError("Pod-template traversal safety budget exceeded")

        if not isinstance(current, (dict, list)):
            continue
        object_id = id(current)
        if object_id in visited:
            continue
        visited.add(object_id)

        if isinstance(current, list):
            stack.extend((item, current_parent, depth + 1) for item in current)
            continue

        if current_parent.lower() in {
            "podspec",
            "podtemplate",
            "template",
            "jobtemplate",
        }:
            spec = current.get("spec") if isinstance(current.get("spec"), dict) else current
            if any(
                field in spec
                for field in ("containers", "initContainers", "ephemeralContainers")
            ):
                return True
        stack.extend(
            (child, key if isinstance(key, str) else "", depth + 1)
            for key, child in current.items()
        )
    return False


def _identity_failures(doc: dict[str, Any], app: str) -> list[str]:
    metadata = _mapping(doc.get("metadata"))
    spec = _mapping(doc.get("spec"))
    template = _mapping(spec.get("template"))
    selector = _mapping(spec.get("selector"))
    locations = {
        "metadata.labels": _mapping(metadata.get("labels")),
        "spec.selector.matchLabels": _mapping(selector.get("matchLabels")),
        "spec.template.metadata.labels": _mapping(
            _mapping(template.get("metadata")).get("labels")
        ),
    }
    failures: list[str] = []
    if metadata.get("name") != app:
        failures.append("controller metadata.name must equal the expected application identity")
    for location, labels in locations.items():
        if labels.get("app") != app:
            failures.append(f"{location}.app must equal the expected application identity")
        for label in IDENTITY_LABELS[1:]:
            if label in labels and labels[label] != app:
                failures.append(
                    f"{location}.{label} conflicts with the expected application identity"
                )
    return failures


def _controller_state_failures(
    doc: dict[str, Any], *, expected_state: str, expected_replicas: int
) -> list[str]:
    metadata = _mapping(doc.get("metadata"))
    annotations = _mapping(metadata.get("annotations"))
    failures: list[str] = []
    if annotations.get(RUNTIME_STATE_ANNOTATION) != expected_state:
        failures.append(
            "controller must carry the exact machine-readable runtime-state marker"
        )
    replicas = _mapping(doc.get("spec")).get("replicas")
    if (
        not isinstance(replicas, int)
        or isinstance(replicas, bool)
        or replicas != expected_replicas
    ):
        failures.append(
            f"spec.replicas must be integer {expected_replicas} for the requested phase"
        )
    return failures


def _service_failures(doc: dict[str, Any], app: str) -> list[str]:
    spec = _mapping(doc.get("spec"))
    failures: list[str] = []
    if doc.get("apiVersion") != "v1":
        failures.append("Service must use core v1")
    service_type = spec.get("type", "ClusterIP")
    if service_type != "ClusterIP":
        failures.append("Service type must be absent or ClusterIP")
    for field in ("externalName", "externalIPs", "loadBalancerClass"):
        if field in spec:
            failures.append(f"Service spec.{field} is forbidden")
    selector = spec.get("selector")
    if not isinstance(selector, dict) or selector.get("app") != app:
        failures.append("Service selector.app must equal the expected application identity")
    ports = spec.get("ports", [])
    if not isinstance(ports, list):
        failures.append("Service spec.ports must be a list when present")
        ports = []
    for port in ports:
        if isinstance(port, dict) and "nodePort" in port:
            failures.append("Service port nodePort is forbidden")
    return failures


def validate_documents(
    documents: list[Any],
    *,
    app: str,
    namespace: str,
    expected_kind: str,
    expected_state: str = "dormant",
    expected_replicas: int = 0,
) -> list[str]:
    if (
        not APP_NAME.fullmatch(app)
        or not NAMESPACE_NAME.fullmatch(namespace)
        or expected_kind not in EXPECTED_API_VERSIONS
        or expected_state not in RUNTIME_STATES
        or not isinstance(expected_replicas, int)
        or isinstance(expected_replicas, bool)
        or expected_replicas < 0
    ):
        raise ValueError("invalid validated CLI identity")

    expected_namespace = namespace
    failures: list[str] = []
    resources: list[tuple[int, dict[str, Any]]] = []
    identities: set[tuple[str, str, str, str]] = set()

    for index, document in enumerate(documents, start=1):
        if document is None:
            continue
        if not isinstance(document, dict):
            failures.append(f"document[{index}] must be a Kubernetes resource mapping")
            continue
        api_version = document.get("apiVersion")
        kind = document.get("kind")
        metadata = document.get("metadata")
        if not isinstance(api_version, str) or not api_version:
            failures.append(f"document[{index}] requires non-empty string apiVersion")
            continue
        if not _safe_identity_text(api_version):
            failures.append(f"document[{index}] apiVersion contains an unsafe identity value")
            continue
        if not isinstance(kind, str) or not kind:
            failures.append(f"document[{index}] requires non-empty string kind")
            continue
        if not _safe_identity_text(kind):
            failures.append(f"document[{index}] kind contains an unsafe identity value")
            continue
        if kind == "List":
            failures.append(
                f"document[{index}] List is forbidden; render resources as separate documents"
            )
            continue
        if (
            not isinstance(metadata, dict)
            or not isinstance(metadata.get("name"), str)
            or not metadata.get("name")
        ):
            failures.append(f"document[{index}] requires metadata.name")
            continue
        if not _safe_identity_text(metadata["name"]):
            failures.append(
                f"document[{index}] metadata.name contains an unsafe identity value"
            )
            continue

        resource_namespace = metadata.get("namespace", "")
        if resource_namespace is None:
            resource_namespace = ""
        group = _api_group(api_version)
        if group == "" and kind == "Namespace":
            if api_version != "v1":
                failures.append(
                    f"document[{index}] Namespace must use the required core apiVersion"
                )
            if resource_namespace != "":
                failures.append(
                    f"document[{index}] Namespace must not set metadata.namespace"
                )
            if metadata["name"] != expected_namespace:
                failures.append(
                    f"document[{index}] Namespace metadata.name must equal the expected "
                    "target namespace"
                )
            identity_namespace = ""
        elif (
            not isinstance(resource_namespace, str)
            or resource_namespace != expected_namespace
        ):
            failures.append(
                f"document[{index}] namespaced resource metadata.namespace must equal "
                "the expected target namespace"
            )
            continue
        else:
            identity_namespace = resource_namespace
        # Kubernetes identity is version-independent. Two documents for the
        # same GroupKind/namespace/name still address one API object even when
        # they use different served versions.
        identity = (group, kind, identity_namespace, metadata["name"])
        if identity in identities:
            failures.append(f"document[{index}] duplicates a resource identity")
        identities.add(identity)
        resources.append((index, document))

    controllers = [
        (index, document)
        for index, document in resources
        if document.get("kind") in EXPECTED_API_VERSIONS
    ]
    if len(controllers) != 1:
        failures.append(
            "render must contain exactly one supported application controller; "
            f"found {len(controllers)}"
        )

    for index, document in resources:
        api_version = str(document["apiVersion"])
        kind = str(document["kind"])
        group = _api_group(api_version)
        label = f"document[{index}]"

        if kind in EXPECTED_API_VERSIONS:
            if kind != expected_kind:
                failures.append(
                    f"{label} controller kind must equal the expected controller kind"
                )
                continue
            if api_version != EXPECTED_API_VERSIONS[expected_kind]:
                failures.append(
                    f"{label} must use the required apiVersion for expected {expected_kind}"
                )
            failures.extend(f"{label}: {failure}" for failure in _identity_failures(document, app))
            failures.extend(
                f"{label}: {failure}"
                for failure in _controller_state_failures(
                    document,
                    expected_state=expected_state,
                    expected_replicas=expected_replicas,
                )
            )
            continue

        if group == "" and kind == "Secret":
            failures.append(
                f"{label} core Secret resources are forbidden; use ESO or a platform "
                "credential resource"
            )
            continue
        if (group, kind) in DIRECT_POD_PRODUCERS:
            failures.append(f"{label} is a forbidden direct Pod producer")
            continue
        if (group, kind) in DIRECT_TRAFFIC_RESOURCES:
            failures.append(f"{label} is a forbidden direct traffic resource")
            continue
        if (
            (group, kind) in AUTOSCALERS
            or "autoscaler" in kind.lower()
            or kind.lower().startswith("scaled")
        ):
            failures.append(f"{label} is a forbidden autoscaling resource")
            continue
        if _has_embedded_pod_template(document.get("spec"), "spec"):
            failures.append(
                f"{label} embeds a Pod template and can activate application Pods"
            )
            continue
        if group == "" and kind == "Service":
            failures.extend(
                f"{label}: {failure}" for failure in _service_failures(document, app)
            )
            continue
        if (group, kind) not in ALLOWED_SUPPORT_RESOURCES:
            failures.append(
                f"{label} is not an allowlisted non-workload supporting resource; "
                "Pod-production semantics are unknown"
            )

    if not resources:
        failures.append("render contains no Kubernetes resources")
    return failures


def _find_controller_document(
    documents: list[Any], expected_kind: str
) -> dict[str, Any] | None:
    matches = [
        document
        for document in documents
        if isinstance(document, dict) and document.get("kind") == expected_kind
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def validate_transition(
    baseline_documents: list[Any],
    candidate_documents: list[Any],
    *,
    app: str,
    namespace: str,
    expected_kind: str,
    approved_replicas: int,
    transition: str,
    evidence_ref: str,
) -> list[str]:
    if (
        not isinstance(approved_replicas, int)
        or isinstance(approved_replicas, bool)
        or not 1 <= approved_replicas <= 100
    ):
        raise ValueError("approved replica count is outside the safe contract")
    if transition not in {"activation", "rollback"}:
        raise ValueError("unknown transition")
    failures: list[str] = []
    if not is_stable_evidence_ref(evidence_ref):
        failures.append("transition requires one stable audited evidence reference")
    if transition == "activation":
        baseline_state, baseline_replicas = "dormant", 0
        candidate_state, candidate_replicas = "active", approved_replicas
    else:
        baseline_state, baseline_replicas = "active", approved_replicas
        candidate_state, candidate_replicas = "dormant", 0
    failures.extend(
        f"baseline: {failure}"
        for failure in validate_documents(
            baseline_documents,
            app=app,
            namespace=namespace,
            expected_kind=expected_kind,
            expected_state=baseline_state,
            expected_replicas=baseline_replicas,
        )
    )
    failures.extend(
        f"candidate: {failure}"
        for failure in validate_documents(
            candidate_documents,
            app=app,
            namespace=namespace,
            expected_kind=expected_kind,
            expected_state=candidate_state,
            expected_replicas=candidate_replicas,
        )
    )
    baseline_controller = _find_controller_document(
        baseline_documents, expected_kind
    )
    candidate_controller = _find_controller_document(
        candidate_documents, expected_kind
    )
    if baseline_controller is None or candidate_controller is None:
        return failures
    normalized_candidate = copy.deepcopy(candidate_documents)
    normalized_controller = _find_controller_document(
        normalized_candidate, expected_kind
    )
    if normalized_controller is None:
        return failures
    metadata = _mapping(normalized_controller.get("metadata"))
    annotations = _mapping(metadata.get("annotations"))
    spec = _mapping(normalized_controller.get("spec"))
    annotations[RUNTIME_STATE_ANNOTATION] = baseline_state
    metadata["annotations"] = annotations
    spec["replicas"] = baseline_replicas
    normalized_controller["metadata"] = metadata
    normalized_controller["spec"] = spec
    if normalized_candidate != baseline_documents:
        failures.append(
            "transition may change only runtime-state marker and controller replicas"
        )
    return failures


def _controller_state(
    documents: list[Any], expected_kind: str
) -> tuple[str, int] | None:
    controller = _find_controller_document(documents, expected_kind)
    if controller is None:
        return None
    metadata = _mapping(controller.get("metadata"))
    annotations = _mapping(metadata.get("annotations"))
    state = annotations.get(RUNTIME_STATE_ANNOTATION)
    replicas = _mapping(controller.get("spec")).get("replicas")
    if (
        state not in RUNTIME_STATES
        or not isinstance(replicas, int)
        or isinstance(replicas, bool)
    ):
        return None
    return state, replicas


def _transition_evidence(
    evidence: Any,
    *,
    transition: str,
    app: str,
    namespace: str,
    expected_kind: str,
) -> tuple[int | None, str | None, list[str]]:
    if transition == "activation":
        failures = validate_evidence(
            evidence, phase="activation-ready", expected_outcome=None
        )
        event = "activation_authorization"
    else:
        failures = validate_evidence(
            evidence, phase="in-progress", expected_outcome=None
        )
        event = "rollback_authorization"
    if failures or not isinstance(evidence, dict):
        return None, None, ["transition evidence does not satisfy its required phase"]
    spec = _mapping(evidence.get("spec"))
    target = _mapping(spec.get("target"))
    controller = _mapping(target.get("controller"))
    if (
        spec.get("app") != app
        or target.get("namespace") != namespace
        or controller.get("kind") != expected_kind
        or controller.get("name") != app
    ):
        return None, None, ["transition evidence target identity does not match the render"]
    events = _mapping(spec.get("events"))
    row = _mapping(events.get(event))
    if transition == "rollback":
        traffic_status = _mapping(events.get("rollback_traffic_isolation")).get(
            "status"
        )
        if (
            spec.get("rollback_status") != "IN_PROGRESS"
            or row.get("status") != "VERIFIED"
            or traffic_status not in {"VERIFIED", "NOT_APPLICABLE"}
        ):
            return None, None, [
                "rollback transition requires authorization and target traffic isolation evidence"
            ]
    approved_replicas = target.get("approved_replicas")
    evidence_ref = row.get("evidence_ref")
    if (
        not isinstance(approved_replicas, int)
        or isinstance(approved_replicas, bool)
        or not isinstance(evidence_ref, str)
    ):
        return None, None, ["transition evidence is missing approved state"]
    return approved_replicas, evidence_ref, []


def _validate_evidence_target(
    evidence: Any,
    *,
    app: str,
    namespace: str,
    expected_kind: str,
) -> list[str]:
    evidence_failures = validate_evidence(
        evidence, phase="auto", expected_outcome=None
    )
    if evidence_failures or not isinstance(evidence, dict):
        return ["branch guard evidence does not satisfy the closed state machine"]
    spec = _mapping(evidence.get("spec"))
    target = _mapping(spec.get("target"))
    controller = _mapping(target.get("controller"))
    if (
        spec.get("app") != app
        or target.get("namespace") != namespace
        or controller.get("kind") != expected_kind
        or controller.get("name") != app
    ):
        return ["branch guard evidence target identity does not match the render"]
    return []


def _has_verified_operational_authorization(evidence: Any) -> bool:
    if not isinstance(evidence, dict):
        return False
    events = _mapping(_mapping(evidence.get("spec")).get("events"))
    return any(
        _mapping(events.get(event)).get("status") == "VERIFIED"
        for event in (
            "writer_fence_authorization",
            "source_scale_authorization",
            "activation_authorization",
            "traffic_authorization",
            "rollback_authorization",
        )
    )


def validate_branch_guard(
    baseline_documents: list[Any],
    candidate_documents: list[Any],
    *,
    app: str,
    namespace: str,
    expected_kind: str,
    baseline_evidence: Any,
    candidate_evidence: Any,
    changed_paths: tuple[str, ...] | None = None,
    evidence_path: str | None = None,
    allowed_transition_prefixes: tuple[str, ...] = (),
    render_byte_identical: bool | None = None,
) -> list[str]:
    evidence_failures = validate_evidence_progression(
        baseline_evidence, candidate_evidence
    )
    if evidence_failures:
        return ["branch guard evidence does not progress from trusted BASELINE"]
    evidence_changed = baseline_evidence != candidate_evidence
    render_changed = baseline_documents != candidate_documents
    if render_byte_identical is None:
        render_byte_identical = not render_changed
    if changed_paths is None or evidence_path is None:
        return ["branch guard requires exact trusted-base changed paths"]
    if evidence_changed:
        if changed_paths != (evidence_path,) or not render_byte_identical:
            return [
                "evidence-only MR must change exactly the evidence path and keep render byte-identical"
            ]
    elif evidence_path in changed_paths:
        return ["unchanged evidence path must not appear in the candidate change set"]

    baseline_state = _controller_state(baseline_documents, expected_kind)
    candidate_state = _controller_state(candidate_documents, expected_kind)
    if baseline_state is None or candidate_state is None:
        return ["branch guard requires one controller with a valid runtime-state marker"]
    baseline_marker, baseline_replicas = baseline_state
    candidate_marker, candidate_replicas = candidate_state
    if baseline_marker == "dormant" and candidate_marker == "dormant":
        failures: list[str] = []
        failures.extend(
            _validate_evidence_target(
                candidate_evidence,
                app=app,
                namespace=namespace,
                expected_kind=expected_kind,
            )
        )
        if (
            not failures
            and _has_verified_operational_authorization(candidate_evidence)
            and render_changed
        ):
            failures.append(
                "dormant render is immutable after operational authorization"
            )
        failures.extend(validate_documents(
            baseline_documents,
            app=app,
            namespace=namespace,
            expected_kind=expected_kind,
            expected_state="dormant",
            expected_replicas=0,
        ))
        failures.extend(
            validate_documents(
                candidate_documents,
                app=app,
                namespace=namespace,
                expected_kind=expected_kind,
                expected_state="dormant",
                expected_replicas=0,
            )
        )
        return failures
    if baseline_marker == "active" and candidate_marker == "active":
        if baseline_replicas < 1 or candidate_replicas != baseline_replicas:
            return ["active steady-state commits must preserve the approved replica count"]
        failures = validate_documents(
            baseline_documents,
            app=app,
            namespace=namespace,
            expected_kind=expected_kind,
            expected_state="active",
            expected_replicas=baseline_replicas,
        )
        failures.extend(
            validate_documents(
                candidate_documents,
                app=app,
                namespace=namespace,
                expected_kind=expected_kind,
                expected_state="active",
                expected_replicas=baseline_replicas,
            )
        )
        return failures
    transition = (
        "activation"
        if (baseline_marker, candidate_marker) == ("dormant", "active")
        else "rollback"
        if (baseline_marker, candidate_marker) == ("active", "dormant")
        else "invalid"
    )
    if transition == "invalid":
        return ["runtime-state transition is not authorized by the branch guard"]
    if evidence_changed:
        return ["runtime transition must consume prior trusted BASELINE evidence"]
    if not allowed_transition_prefixes:
        return ["runtime-state transition requires an exact target-overlay allowlist"]
    disallowed_paths = [
        path
        for path in changed_paths
        if not any(path.startswith(prefix) for prefix in allowed_transition_prefixes)
    ]
    if disallowed_paths:
        return [
            "runtime-state transition changed a path outside the exact target-overlay allowlist"
        ]
    approved, evidence_ref, evidence_failures = _transition_evidence(
        baseline_evidence,
        transition=transition,
        app=app,
        namespace=namespace,
        expected_kind=expected_kind,
    )
    if evidence_failures or approved is None or evidence_ref is None:
        return evidence_failures
    if transition == "activation" and candidate_replicas != approved:
        return ["activation replicas do not equal the audited approved count"]
    if transition == "rollback" and baseline_replicas != approved:
        return ["rollback baseline replicas do not equal the audited approved count"]
    return validate_transition(
        baseline_documents,
        candidate_documents,
        app=app,
        namespace=namespace,
        expected_kind=expected_kind,
        approved_replicas=approved,
        transition=transition,
        evidence_ref=evidence_ref,
    )


class ControlledArgumentParser(argparse.ArgumentParser):
    """Keep argparse failures fixed and free of attacker-controlled values."""

    def error(self, message: str) -> None:
        del message
        self.exit(2, "FAIL: invalid command-line arguments\n")


def _parser() -> argparse.ArgumentParser:
    parser = ControlledArgumentParser(
        description="Validate one exact zero-replica controller in a rendered legacy-live target."
    )
    parser.add_argument("--app", required=True, help="exact application identity")
    parser.add_argument(
        "--namespace", required=True, help="exact target namespace identity"
    )
    parser.add_argument(
        "--expected-kind",
        required=True,
        choices=sorted(EXPECTED_API_VERSIONS),
        help="expected application controller kind",
    )
    parser.add_argument(
        "--phase",
        choices=(
            "dormant",
            "active",
            "branch-guard",
            "kustomize-closure",
            "activation-transition",
            "rollback-transition",
        ),
        default="dormant",
        help="steady state or exact reviewed two-field transition",
    )
    parser.add_argument(
        "--baseline-render",
        help="trusted target-branch baseline render for a transition",
    )
    parser.add_argument(
        "--approved-replicas",
        type=int,
        help="approved active replica count for active/transition phases",
    )
    parser.add_argument(
        "--evidence-ref",
        help="stable audited activation or rollback evidence reference",
    )
    parser.add_argument(
        "--evidence-file",
        help="candidate closed handoff evidence YAML used by the branch guard",
    )
    parser.add_argument(
        "--baseline-evidence",
        help="trusted BASELINE handoff evidence used for authorization",
    )
    parser.add_argument(
        "--changed-paths-file",
        help="NUL-delimited trusted-base-to-candidate path set for the branch guard",
    )
    parser.add_argument(
        "--repository-root",
        help="absolute candidate repository root for local Kustomize closure proof",
    )
    parser.add_argument(
        "--overlay",
        help="candidate repository-relative target overlay directory",
    )
    parser.add_argument(
        "--tracked-paths-file",
        help="NUL-delimited candidate commit tracked-file inventory",
    )
    parser.add_argument(
        "--evidence-path",
        help="exact repository path permitted only in an evidence-only MR",
    )
    parser.add_argument(
        "--allowed-transition-prefix",
        action="append",
        default=[],
        help="repository path prefix allowed in an activation/rollback MR",
    )
    parser.add_argument(
        "render",
        nargs="?",
        default="-",
        help="rendered multi-document YAML path, or -/omitted for stdin",
    )
    return parser


def _safe_repository_path(value: object, *, prefix: bool = False) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1_024
        or "\\" in value
        or CONTROL_CHAR.search(value)
    ):
        return None
    normalized = value[:-1] if prefix and value.endswith("/") else value
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or str(path) != normalized
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return None
    return f"{normalized}/" if prefix else normalized


def _read_changed_paths(source: str) -> tuple[str, ...] | None:
    try:
        raw = safe_read_bytes(source, max_bytes=MAX_CHANGED_PATH_BYTES)
    except SafeInputError:
        return None
    if not raw:
        return ()
    if not raw.endswith(b"\0"):
        return None
    encoded_paths = raw[:-1].split(b"\0")
    if len(encoded_paths) > MAX_CHANGED_PATHS or any(not item for item in encoded_paths):
        return None
    paths: list[str] = []
    try:
        for encoded in encoded_paths:
            path = encoded.decode("utf-8")
            safe_path = _safe_repository_path(path)
            if safe_path is None:
                return None
            paths.append(safe_path)
    except UnicodeDecodeError:
        return None
    if len(set(paths)) != len(paths):
        return None
    return tuple(paths)


def _read_tracked_paths(source: str) -> tuple[str, ...] | None:
    try:
        raw = safe_read_bytes(source, max_bytes=MAX_TRACKED_PATH_BYTES)
    except SafeInputError:
        return None
    if not raw or not raw.endswith(b"\0"):
        return None
    encoded_paths = raw[:-1].split(b"\0")
    if (
        len(encoded_paths) > MAX_TRACKED_PATHS
        or any(not item for item in encoded_paths)
    ):
        return None
    paths: list[str] = []
    try:
        for encoded in encoded_paths:
            safe_path = _safe_repository_path(encoded.decode("utf-8"))
            if safe_path is None:
                return None
            paths.append(safe_path)
    except UnicodeDecodeError:
        return None
    if len(set(paths)) != len(paths):
        return None
    return tuple(paths)


def _repository_root(value: str) -> Path | None:
    try:
        root = Path(value)
        if not root.is_absolute() or root.is_symlink():
            return None
        resolved = root.resolve(strict=True)
        if resolved != root or not resolved.is_dir():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _local_dependency_path(root: Path, parent: Path, value: object) -> Path | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2_048
        or "\\" in value
        or ":" in value
        or CONTROL_CHAR.search(value)
        or any(marker in value for marker in REMOTE_REFERENCE_MARKERS)
    ):
        return None
    reference = PurePosixPath(value)
    if reference.is_absolute() or str(reference) != value:
        return None
    try:
        candidate = Path(os.path.abspath(parent / Path(*reference.parts)))
        candidate.relative_to(root)
        current = root
        for part in candidate.relative_to(root).parts:
            current = current / part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                return None
        return candidate
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return None


def _tracked_regular_file(root: Path, path: Path, tracked: set[str]) -> bool:
    try:
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
    except (OSError, ValueError):
        return False
    return stat.S_ISREG(mode) and not stat.S_ISLNK(mode) and relative in tracked


def _kustomization_file(root: Path, directory: Path, tracked: set[str]) -> Path | None:
    matches = [
        directory / name
        for name in KUSTOMIZATION_NAMES
        if _tracked_regular_file(root, directory / name, tracked)
    ]
    return matches[0] if len(matches) == 1 else None


def _dependency_values(document: dict[str, Any]) -> tuple[list[object], bool]:
    dependencies: list[object] = []
    valid = True
    for field in ("resources", "bases", "components", "configurations", "crds"):
        value = document.get(field, [])
        if not isinstance(value, list):
            valid = False
            continue
        dependencies.extend(value)

    strategic = document.get("patchesStrategicMerge", [])
    if not isinstance(strategic, list):
        valid = False
    else:
        dependencies.extend(item for item in strategic if isinstance(item, str))
        if any(not isinstance(item, (str, dict)) for item in strategic):
            valid = False

    for field in ("patches", "patchesJson6902", "replacements"):
        value = document.get(field, [])
        if not isinstance(value, list):
            valid = False
            continue
        for item in value:
            if not isinstance(item, dict):
                valid = False
                continue
            if "path" in item:
                dependencies.append(item["path"])

    openapi = document.get("openapi")
    if openapi is not None:
        if not isinstance(openapi, dict) or "path" not in openapi:
            valid = False
        else:
            dependencies.append(openapi["path"])

    for field in ("configMapGenerator", "secretGenerator"):
        generators = document.get(field, [])
        if not isinstance(generators, list):
            valid = False
            continue
        for generator in generators:
            if not isinstance(generator, dict):
                valid = False
                continue
            for list_field in ("files", "envs"):
                values = generator.get(list_field, [])
                if not isinstance(values, list):
                    valid = False
                    continue
                for item in values:
                    if not isinstance(item, str):
                        valid = False
                    else:
                        dependencies.append(item.split("=", 1)[-1])
            if "env" in generator:
                dependencies.append(generator["env"])
    return dependencies, valid


def validate_kustomize_dependency_closure(
    *, repository_root: str, overlay: str, tracked_paths: tuple[str, ...]
) -> list[str]:
    root = _repository_root(repository_root)
    if root is None:
        return ["candidate repository root is not one fixed regular directory"]
    tracked = set(tracked_paths)
    overlay_path = _local_dependency_path(root, root, overlay)
    if overlay_path is None or not overlay_path.is_dir():
        return ["target overlay is not a safe local repository directory"]
    initial = _kustomization_file(root, overlay_path, tracked)
    if initial is None:
        return ["target overlay lacks one tracked regular Kustomization file"]

    failures: list[str] = []
    pending = [initial]
    visited: set[Path] = set()
    while pending:
        kustomization = pending.pop()
        if kustomization in visited:
            continue
        visited.add(kustomization)
        if len(visited) > MAX_KUSTOMIZATION_FILES:
            return ["Kustomize dependency closure exceeds the file budget"]
        try:
            document = load_single_yaml(
                str(kustomization),
                max_bytes=MAX_KUSTOMIZATION_BYTES,
                max_tokens=MAX_KUSTOMIZATION_TOKENS,
            )
        except SafeInputError:
            return ["Kustomization input cannot be parsed safely"]
        if not isinstance(document, dict):
            return ["Kustomization must be one mapping"]
        if any(field in document for field in FORBIDDEN_KUSTOMIZE_EXECUTION_FIELDS):
            return ["Kustomize exec, plugin, validator, or Helm fields are forbidden"]
        if set(document) - ALLOWED_KUSTOMIZATION_FIELDS:
            return ["Kustomization contains an unclassified dependency field"]
        if document.get("kind") not in {"Kustomization", "Component"} or document.get(
            "apiVersion"
        ) not in {
            "kustomize.config.k8s.io/v1alpha1",
            "kustomize.config.k8s.io/v1beta1",
        }:
            return ["Kustomization identity is not an approved local form"]
        dependencies, structurally_valid = _dependency_values(document)
        if not structurally_valid:
            return ["Kustomize dependency fields are not structurally closed"]
        for value in dependencies:
            dependency = _local_dependency_path(root, kustomization.parent, value)
            if dependency is None:
                return ["Kustomize dependency is remote, unsafe, missing, or symlinked"]
            if dependency.is_dir():
                nested = _kustomization_file(root, dependency, tracked)
                if nested is None:
                    return ["local Kustomize directory lacks one tracked regular file"]
                pending.append(nested)
            elif not _tracked_regular_file(root, dependency, tracked):
                return ["Kustomize dependency must be a tracked regular local file"]
    return failures


def _read_render(source: str) -> str | None:
    try:
        raw = safe_read_bytes(source, max_bytes=MAX_RENDER_BYTES)
    except (SafeInputError, OSError, ValueError, OverflowError, MemoryError):
        print("FAIL: rendered YAML input cannot be read safely")
        return None
    if len(raw) > MAX_RENDER_BYTES:
        print(f"FAIL: rendered YAML exceeds {MAX_RENDER_BYTES} bytes")
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        print("FAIL: rendered YAML input must be UTF-8")
        return None


def _scan_render(text: str) -> bool:
    token_count = 0
    structural_nodes = 0
    collection_depth = 0
    explicit_documents = 0
    implicit_prefix = False
    saw_document_start = False
    ignored_tokens = (
        yaml.tokens.StreamStartToken,
        yaml.tokens.StreamEndToken,
        yaml.tokens.DocumentEndToken,
        yaml.tokens.DirectiveToken,
    )
    collection_start_tokens = (
        yaml.tokens.BlockMappingStartToken,
        yaml.tokens.BlockSequenceStartToken,
        yaml.tokens.FlowMappingStartToken,
        yaml.tokens.FlowSequenceStartToken,
    )
    collection_end_tokens = (
        yaml.tokens.BlockEndToken,
        yaml.tokens.FlowMappingEndToken,
        yaml.tokens.FlowSequenceEndToken,
    )
    try:
        for token in yaml.scan(text):
            token_count += 1
            if token_count > MAX_YAML_TOKENS:
                print(f"FAIL: rendered input exceeds {MAX_YAML_TOKENS} YAML tokens")
                return False
            if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)):
                print("FAIL: rendered input YAML anchors and aliases are forbidden")
                return False
            if isinstance(token, collection_start_tokens):
                structural_nodes += 1
                collection_depth += 1
                if structural_nodes > MAX_TRAVERSAL_NODES:
                    print(
                        f"FAIL: rendered input exceeds {MAX_TRAVERSAL_NODES} "
                        "structural YAML nodes"
                    )
                    return False
                if collection_depth > MAX_TRAVERSAL_DEPTH:
                    print(
                        f"FAIL: rendered input exceeds {MAX_TRAVERSAL_DEPTH} "
                        "levels of YAML nesting"
                    )
                    return False
            elif isinstance(token, collection_end_tokens):
                collection_depth = max(0, collection_depth - 1)
            elif isinstance(token, yaml.tokens.ScalarToken):
                structural_nodes += 1
                if structural_nodes > MAX_TRAVERSAL_NODES:
                    print(
                        f"FAIL: rendered input exceeds {MAX_TRAVERSAL_NODES} "
                        "structural YAML nodes"
                    )
                    return False
                if (
                    token.style is None
                    and len(token.value) > MAX_INTEGER_SCALAR_CHARS
                    and INTEGER_LIKE_SCALAR.fullmatch(token.value)
                ):
                    print("FAIL: rendered input integer scalar exceeds the safety budget")
                    return False
            if isinstance(token, yaml.tokens.DocumentStartToken):
                explicit_documents += 1
                saw_document_start = True
                if explicit_documents + int(implicit_prefix) > MAX_DOCUMENTS:
                    print(
                        f"FAIL: rendered input exceeds {MAX_DOCUMENTS} YAML documents"
                    )
                    return False
            elif not saw_document_start and not isinstance(token, ignored_tokens):
                if not implicit_prefix:
                    implicit_prefix = True
                    if explicit_documents + 1 > MAX_DOCUMENTS:
                        print(
                            f"FAIL: rendered input exceeds {MAX_DOCUMENTS} YAML documents"
                        )
                        return False
    except (yaml.YAMLError, RecursionError, ValueError, OverflowError, MemoryError):
        print("FAIL: rendered input contains invalid or duplicate YAML")
        return False

    document_count = explicit_documents + int(implicit_prefix)
    if document_count > MAX_DOCUMENTS:
        print(f"FAIL: rendered input exceeds {MAX_DOCUMENTS} YAML documents")
        return False
    return True


def _load_documents(text: str) -> list[Any] | None:
    documents: list[Any] = []
    try:
        for document in yaml.load_all(text, Loader=UniqueKeyLoader):
            documents.append(document)
            if len(documents) > MAX_DOCUMENTS:
                print(f"FAIL: rendered input exceeds {MAX_DOCUMENTS} YAML documents")
                return None
    except (yaml.YAMLError, RecursionError, ValueError, OverflowError, MemoryError):
        print("FAIL: rendered input contains invalid or duplicate YAML")
        return None
    return documents


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code)
    if not APP_NAME.fullmatch(args.app):
        print("FAIL: --app must be a canonical lowercase Kubernetes app identity")
        return 2
    if not NAMESPACE_NAME.fullmatch(args.namespace):
        print("FAIL: --namespace must be a canonical lowercase Kubernetes namespace")
        return 2

    transition_phase = args.phase in {"activation-transition", "rollback-transition"}
    branch_guard = args.phase == "branch-guard"
    closure_phase = args.phase == "kustomize-closure"
    if closure_phase:
        if (
            args.repository_root is None
            or args.overlay is None
            or args.tracked_paths_file is None
            or args.baseline_render is not None
            or args.approved_replicas is not None
            or args.evidence_ref is not None
            or args.evidence_file is not None
            or args.baseline_evidence is not None
            or args.changed_paths_file is not None
            or args.evidence_path is not None
            or args.allowed_transition_prefix
            or args.render != "-"
        ):
            print("FAIL: Kustomize closure phase requires only fixed closure inputs")
            return 2
        tracked_paths = _read_tracked_paths(args.tracked_paths_file)
        if tracked_paths is None:
            print("FAIL: tracked-path input cannot be parsed safely")
            return 2
        failures = validate_kustomize_dependency_closure(
            repository_root=args.repository_root,
            overlay=args.overlay,
            tracked_paths=tracked_paths,
        )
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            print(f"FAIL: {len(failures)} Kustomize closure violation(s)")
            return 1
        print("PASS: Kustomize closure contains only tracked regular local files")
        return 0

    if any(
        value is not None
        for value in (args.repository_root, args.overlay, args.tracked_paths_file)
    ):
        print("FAIL: Kustomize closure inputs are valid only for closure phase")
        return 2
    if transition_phase:
        if (
            not args.baseline_render
            or args.approved_replicas is None
            or args.evidence_ref is None
            or args.evidence_file is not None
            or args.baseline_evidence is not None
            or (args.baseline_render == "-" and args.render == "-")
        ):
            print("FAIL: transition phase requires baseline, approved replicas, and evidence")
            return 2
    elif branch_guard:
        if (
            not args.baseline_render
            or args.approved_replicas is not None
            or args.evidence_ref is not None
            or args.evidence_file is None
            or args.baseline_evidence is None
            or args.changed_paths_file is None
            or args.evidence_path is None
            or not args.allowed_transition_prefix
            or (args.baseline_render == "-" and args.render == "-")
        ):
            print("FAIL: branch guard requires a baseline and closed transition inputs")
            return 2
    elif args.phase == "active":
        if args.approved_replicas is None:
            print("FAIL: active phase requires approved replicas")
            return 2
        if (
            args.baseline_render is not None
            or args.evidence_ref is not None
            or args.evidence_file is not None
        ):
            print("FAIL: active phase does not accept transition-only arguments")
            return 2
    elif any(
        value is not None
        for value in (
            args.baseline_render,
            args.approved_replicas,
            args.evidence_ref,
            args.evidence_file,
            args.baseline_evidence,
            args.changed_paths_file,
            args.evidence_path,
        )
    ):
        print("FAIL: dormant phase does not accept transition-only arguments")
        return 2
    if not branch_guard and (
        args.changed_paths_file is not None
        or args.evidence_path is not None
        or args.allowed_transition_prefix
    ):
        print("FAIL: transition-scope arguments are valid only for the branch guard")
        return 2

    evidence_path = (
        _safe_repository_path(args.evidence_path)
        if args.evidence_path is not None
        else None
    )
    allowed_transition_prefixes = tuple(
        _safe_repository_path(value, prefix=True) or ""
        for value in args.allowed_transition_prefix
    )
    if (args.evidence_path is not None and evidence_path is None) or any(
        not value for value in allowed_transition_prefixes
    ):
        print("FAIL: allowed transition path contract is invalid")
        return 2

    text = _read_render(args.render)
    if text is None:
        return 2
    if not _scan_render(text):
        return 2
    documents = _load_documents(text)
    if documents is None:
        return 2

    try:
        if transition_phase or branch_guard:
            baseline_text = _read_render(args.baseline_render)
            if baseline_text is None:
                return 2
            if not _scan_render(baseline_text):
                return 2
            baseline_documents = _load_documents(baseline_text)
            if baseline_documents is None:
                return 2
            if branch_guard:
                changed_paths = _read_changed_paths(args.changed_paths_file)
                if changed_paths is None:
                    print("FAIL: changed-path input cannot be parsed safely")
                    return 2
                candidate_evidence = None
                baseline_evidence = None
                if args.evidence_file is not None and args.baseline_evidence is not None:
                    try:
                        candidate_evidence = load_single_yaml(
                            args.evidence_file,
                            max_bytes=MAX_EVIDENCE_BYTES,
                            max_tokens=MAX_EVIDENCE_TOKENS,
                        )
                        baseline_evidence = load_single_yaml(
                            args.baseline_evidence,
                            max_bytes=MAX_EVIDENCE_BYTES,
                            max_tokens=MAX_EVIDENCE_TOKENS,
                        )
                    except SafeInputError:
                        print("FAIL: transition evidence input cannot be parsed safely")
                        return 2
                failures = validate_branch_guard(
                    baseline_documents,
                    documents,
                    app=args.app,
                    namespace=args.namespace,
                    expected_kind=args.expected_kind,
                    baseline_evidence=baseline_evidence,
                    candidate_evidence=candidate_evidence,
                    changed_paths=changed_paths,
                    evidence_path=evidence_path,
                    allowed_transition_prefixes=allowed_transition_prefixes,
                    render_byte_identical=baseline_text.encode("utf-8")
                    == text.encode("utf-8"),
                )
            else:
                failures = validate_transition(
                    baseline_documents,
                    documents,
                    app=args.app,
                    namespace=args.namespace,
                    expected_kind=args.expected_kind,
                    approved_replicas=args.approved_replicas,
                    transition=(
                        "activation"
                        if args.phase == "activation-transition"
                        else "rollback"
                    ),
                    evidence_ref=args.evidence_ref,
                )
        else:
            failures = validate_documents(
                documents,
                app=args.app,
                namespace=args.namespace,
                expected_kind=args.expected_kind,
                expected_state=args.phase,
                expected_replicas=(args.approved_replicas if args.phase == "active" else 0),
            )
    except (yaml.YAMLError, RecursionError, ValueError, OverflowError, MemoryError):
        print("FAIL: dormant-target validation could not complete safely")
        return 2
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        print(f"FAIL: {len(failures)} dormant-target violation(s)")
        return 1
    print(
        "PASS: target runtime-state marker, replica contract, traffic boundary, "
        "and supporting resources match the requested phase"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
