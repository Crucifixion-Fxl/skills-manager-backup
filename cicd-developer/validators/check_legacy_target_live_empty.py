#!/usr/bin/env python3
"""Read the exact target namespace and prove it is empty for legacy-live prep."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from _legacy_target_common import SafeInputError, load_single_yaml


MAX_WRITER_BYTES = 128 * 1024
MAX_WRITER_TOKENS = 8_192
MAX_KUBECTL_OUTPUT = 8 * 1024 * 1024
K8S_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
SAFE_CONTEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,254}$")
SAFE_WRITER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,254}$")
INVENTORY_GVRS = (
    "pods",
    "services",
    "endpoints",
    "endpointslices.discovery.k8s.io",
    "ingresses.networking.k8s.io",
    "replicationcontrollers",
    "deployments.apps",
    "replicasets.apps",
    "rollouts.argoproj.io",
    "statefulsets.apps",
    "daemonsets.apps",
    "jobs.batch",
    "cronjobs.batch",
    "horizontalpodautoscalers.autoscaling",
    "scaledobjects.keda.sh",
    "scaledjobs.keda.sh",
)
DISCOVERED_PRODUCER_GVRS = {
    "advancedstatefulsets.kruise.io",
    "broadcastjobs.kruise.io",
    "clonesets.kruise.io",
    "configurations.serving.knative.dev",
    "cronworkflows.argoproj.io",
    "deploymentconfigs.apps.openshift.io",
    "experiments.argoproj.io",
    "flinkdeployments.flink.apache.org",
    "flinksessionjobs.flink.apache.org",
    "mpijobs.kubeflow.org",
    "paddlejobs.kubeflow.org",
    "pipelineruns.tekton.dev",
    "pytorchjobs.kubeflow.org",
    "rayclusters.ray.io",
    "rayjobs.ray.io",
    "rayservices.ray.io",
    "revisions.serving.knative.dev",
    "scheduledsparkapplications.sparkoperator.k8s.io",
    "services.serving.knative.dev",
    "sparkapplications.sparkoperator.k8s.io",
    "taskruns.tekton.dev",
    "tfjobs.kubeflow.org",
    "uniteddeployments.kruise.io",
    "verticalpodautoscalers.autoscaling.k8s.io",
    "workflows.argoproj.io",
    "xgboostjobs.kubeflow.org",
}
SAFE_SUPPORT_CRD_GVRS = {
    "certificates.cert-manager.io",
    "externalsecrets.external-secrets.io",
    "issuers.cert-manager.io",
    "kafkascramcredentials.platform.addx.io",
    "objectbuckets.platform.addx.io",
    "podmonitors.monitoring.coreos.com",
    "prometheusrules.monitoring.coreos.com",
    "pushsecrets.external-secrets.io",
    "secretproviderclasses.secrets-store.csi.x-k8s.io",
    "secretstores.external-secrets.io",
    "servicemonitors.monitoring.coreos.com",
    "vmpodscrapes.operator.victoriametrics.com",
    "vmrules.operator.victoriametrics.com",
    "vmservicescrapes.operator.victoriametrics.com",
}
BUILTIN_API_GROUPS = {
    "admissionregistration.k8s.io",
    "apps",
    "authentication.k8s.io",
    "authorization.k8s.io",
    "autoscaling",
    "batch",
    "certificates.k8s.io",
    "coordination.k8s.io",
    "discovery.k8s.io",
    "events.k8s.io",
    "flowcontrol.apiserver.k8s.io",
    "networking.k8s.io",
    "node.k8s.io",
    "policy",
    "rbac.authorization.k8s.io",
    "resource.k8s.io",
    "scheduling.k8s.io",
    "storage.k8s.io",
}
AUTOSCALER_GVRS = {
    "horizontalpodautoscalers.autoscaling",
    "scaledobjects.keda.sh",
    "scaledjobs.keda.sh",
}
GVR_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,252}[a-z0-9]$")


class ControlledArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.exit(2, "FAIL: invalid command-line arguments\n")


def _parser() -> argparse.ArgumentParser:
    parser = ControlledArgumentParser(
        description="Prove an exact legacy-live target namespace has no Pod producer."
    )
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--writer-inventory", required=True)
    parser.add_argument("--kubectl", default="kubectl")
    return parser


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _run_kubectl(
    kubectl: str, arguments: list[str]
) -> tuple[int, bytes] | None:
    command = [kubectl, *arguments]
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.communicate()
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    if len(stdout) > MAX_KUBECTL_OUTPUT or len(stderr) > MAX_KUBECTL_OUTPUT:
        return None
    return process.returncode, stdout


def _load_json(raw: bytes) -> Any | None:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError, MemoryError):
        return None


def _validate_writer_inventory(
    document: Any, *, context: str, namespace: str, app: str
) -> list[str]:
    failures: list[str] = []
    if not isinstance(document, dict) or set(document) != {"schema_version", "writers"}:
        return ["writer inventory must use the exact closed schema"]
    if document["schema_version"] != 1 or not isinstance(document["writers"], list):
        return ["writer inventory schema version or writers list is invalid"]
    seen: set[str] = set()
    for row in document["writers"]:
        if not isinstance(row, dict) or set(row) != {
            "writer_id",
            "context",
            "namespace",
            "app",
            "write_capability",
            "state",
        }:
            failures.append("writer inventory row must use the exact closed schema")
            continue
        writer_id = row["writer_id"]
        if not isinstance(writer_id, str) or not SAFE_WRITER_ID.fullmatch(writer_id):
            failures.append("writer inventory contains an invalid writer identity")
            continue
        if writer_id in seen:
            failures.append("writer inventory contains a duplicate writer identity")
        seen.add(writer_id)
        if row["write_capability"] not in {"none", "target-directed"}:
            failures.append("writer inventory contains an unknown write capability")
        if row["state"] not in {"enabled", "disabled", "idle", "unknown"}:
            failures.append("writer inventory contains an unknown writer state")
        if (
            not isinstance(row["context"], str)
            or not SAFE_CONTEXT.fullmatch(row["context"])
            or not isinstance(row["namespace"], str)
            or not K8S_NAME.fullmatch(row["namespace"])
            or not isinstance(row["app"], str)
            or not K8S_NAME.fullmatch(row["app"])
        ):
            failures.append("writer inventory contains an invalid target identity")
        if (
            row["context"] == context
            and row["namespace"] == namespace
            and row["app"] == app
            and row["write_capability"] == "target-directed"
        ):
            failures.append("target-directed legacy writer exists for the exact target tuple")
    return failures


def _list_items(document: Any) -> list[dict[str, Any]] | None:
    if not isinstance(document, dict) or set(document) - {
        "apiVersion",
        "kind",
        "metadata",
        "items",
    }:
        return None
    if document.get("kind") != "List" or not isinstance(document.get("items"), list):
        return None
    items = document["items"]
    if len(items) > 10_000 or any(not isinstance(item, dict) for item in items):
        return None
    return items


def _discover_namespaced_gvrs(raw: bytes) -> set[str] | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    resources: set[str] = set()
    for line in text.splitlines():
        value = line.strip()
        if not value or not GVR_NAME.fullmatch(value) or value in resources:
            return None
        resources.add(value)
        if len(resources) > 4_096:
            return None
    return resources or None


def _autoscaler_reference_is_structured(gvr: str, item: dict[str, Any]) -> bool:
    spec = item.get("spec")
    if not isinstance(spec, dict):
        return False
    if gvr == "horizontalpodautoscalers.autoscaling":
        reference = spec.get("scaleTargetRef")
        return (
            isinstance(reference, dict)
            and isinstance(reference.get("kind"), str)
            and isinstance(reference.get("name"), str)
        )
    if gvr == "scaledobjects.keda.sh":
        reference = spec.get("scaleTargetRef")
        return isinstance(reference, dict) and isinstance(reference.get("name"), str)
    reference = spec.get("jobTargetRef")
    return isinstance(reference, dict)


def check_live_empty(
    *, kubectl: str, context: str, namespace: str, app: str, writer_document: Any
) -> tuple[int, list[str]]:
    writer_failures = _validate_writer_inventory(
        writer_document, context=context, namespace=namespace, app=app
    )
    if writer_failures:
        return 1, writer_failures
    namespace_query = _run_kubectl(
        kubectl,
        [
            "--context",
            context,
            "get",
            "namespace",
            namespace,
            "--ignore-not-found=true",
            "--request-timeout=15s",
            "-o",
            "json",
        ],
    )
    if namespace_query is None or namespace_query[0] != 0:
        return 2, ["exact target namespace authentication or inventory query failed"]
    if not namespace_query[1].strip():
        return 0, []
    namespace_object = _load_json(namespace_query[1])
    if not isinstance(namespace_object, dict) or namespace_object.get("kind") != "Namespace":
        return 2, ["exact target namespace inventory response is invalid"]

    discovery = _run_kubectl(
        kubectl,
        [
            "--context",
            context,
            "api-resources",
            "--namespaced=true",
            "--verbs=list",
            "--cached=false",
            "-o",
            "name",
        ],
    )
    if discovery is None or discovery[0] != 0:
        return 2, ["target API discovery or authorization inventory failed"]
    discovered = _discover_namespaced_gvrs(discovery[1])
    if discovered is None or not set(INVENTORY_GVRS) <= discovered:
        return 2, ["required target producer/CRD discovery is incomplete"]

    dynamic_producers = sorted(DISCOVERED_PRODUCER_GVRS & discovered)
    unknown_crds = sorted(
        resource
        for resource in discovered
        if "." in resource
        and resource.split(".", 1)[1] not in BUILTIN_API_GROUPS
        and resource not in set(INVENTORY_GVRS)
        and resource not in DISCOVERED_PRODUCER_GVRS
        and resource not in SAFE_SUPPORT_CRD_GVRS
    )

    failures: list[str] = []
    for gvr in (*INVENTORY_GVRS, *dynamic_producers, *unknown_crds):
        query = _run_kubectl(
            kubectl,
            [
                "--context",
                context,
                "--namespace",
                namespace,
                "get",
                gvr,
                "--request-timeout=15s",
                "-o",
                "json",
            ],
        )
        if query is None or query[0] != 0:
            return 2, ["required target resource or CRD inventory is unavailable"]
        items = _list_items(_load_json(query[1]))
        if items is None:
            return 2, ["required target resource inventory response is invalid"]
        if gvr in unknown_crds and items:
            return 2, ["unclassified namespaced CRD inventory is non-empty"]
        if gvr in AUTOSCALER_GVRS:
            for item in items:
                if not _autoscaler_reference_is_structured(gvr, item):
                    return 2, ["autoscaler target reference inventory is structurally unknown"]
        if items and gvr not in unknown_crds:
            failures.append(f"target namespace contains forbidden resource class {gvr}")
    return (1, failures) if failures else (0, [])


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code)
    if (
        not SAFE_CONTEXT.fullmatch(args.context)
        or not K8S_NAME.fullmatch(args.namespace)
        or not K8S_NAME.fullmatch(args.app)
        or not isinstance(args.kubectl, str)
        or not args.kubectl
    ):
        print("FAIL: target identity or kubectl command is invalid")
        return 2
    try:
        writer_document = load_single_yaml(
            args.writer_inventory,
            max_bytes=MAX_WRITER_BYTES,
            max_tokens=MAX_WRITER_TOKENS,
        )
    except SafeInputError:
        print("FAIL: writer inventory cannot be parsed safely")
        return 2
    try:
        result, failures = check_live_empty(
            kubectl=args.kubectl,
            context=args.context,
            namespace=args.namespace,
            app=args.app,
            writer_document=writer_document,
        )
    except (RecursionError, ValueError, OverflowError, MemoryError):
        print("FAIL: target emptiness validation could not complete safely")
        return 2
    if result:
        for failure in failures:
            print(f"FAIL: {failure}")
        return result
    print(
        "PASS: exact target namespace is absent or contains no traffic resource, "
        "producer, or autoscaler, and no target-directed writer exists"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
