#!/usr/bin/env python3
"""Validate VictoriaMetrics scrape resources against their repository context."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from _scan import ParseError, iter_docs


API_VERSION = "operator.victoriametrics.com/v1beta1"
REPO_CONTEXTS = ("auto", "app", "k8s", "argocd-apps", "crossplane-infra")
ENDPOINT_FIELD = {
    "VMServiceScrape": "endpoints",
    "VMPodScrape": "podMetricsEndpoints",
}
FORBIDDEN_ENDPOINT_FIELDS = {
    "authorization",
    "basicAuth",
    "bearerTokenFile",
    "bearerTokenSecret",
    "headers",
    "httpHeaders",
    "oauth2",
    "proxyURL",
    "tlsConfig",
}
DURATION = re.compile(r"^(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$")
WORKLOAD_KINDS = {"Rollout", "Deployment", "StatefulSet"}
APP_NAME_LABEL = "app.kubernetes.io/name"

DCGM_NAME = "gke-managed-dcgm-exporter"
DCGM_NAMESPACE = "victoria-metrics"
DCGM_SOURCE_NAMESPACE = "gke-managed-system"
DCGM_EXPORTER_LABEL = "gke-managed-dcgm-exporter"
DCGM_LABELS = {
    "app.kubernetes.io/part-of": "victoria-metrics",
    APP_NAME_LABEL: DCGM_EXPORTER_LABEL,
    "monitoring.addx.io/profile": "cluster",
}
DCGM_ENDPOINT = {
    "port": "metrics",
    "path": "/metrics",
    "scheme": "http",
    "interval": "30s",
    "scrapeTimeout": "10s",
    "filterRunning": True,
    "honorLabels": True,
}
DCGM_RELABEL = {
    "action": "keep",
    "sourceLabels": ["__name__"],
    "regex": "^DCGM_.*",
}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _name(doc: dict[str, Any]) -> str:
    name = _mapping(doc.get("metadata")).get("name")
    return name if isinstance(name, str) and name else "<unnamed>"


def _namespace(doc: dict[str, Any]) -> str:
    namespace = _mapping(doc.get("metadata")).get("namespace")
    return namespace if isinstance(namespace, str) else ""


def _labels(doc: dict[str, Any]) -> dict[str, str]:
    labels = _mapping(_mapping(doc.get("metadata")).get("labels"))
    return {
        key: value
        for key, value in labels.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _matches(labels: dict[str, str], selector: dict[str, str]) -> bool:
    return all(labels.get(key) == value for key, value in selector.items())


def _duration_seconds(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = DURATION.fullmatch(value)
    if not match or not any(match.groupdict().values()):
        return None
    return (
        int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )


def _forbidden_path(value: object, path: str = "") -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if key in FORBIDDEN_ENDPOINT_FIELDS:
                return key_path
            found = _forbidden_path(nested, key_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _forbidden_path(nested, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str) and "://" in value:
        return path or "value"
    return None


def _selector(doc: dict[str, Any], failures: list[str]) -> dict[str, str]:
    spec = _mapping(doc.get("spec"))
    selector = _mapping(spec.get("selector"))
    match_labels = _mapping(selector.get("matchLabels"))
    if selector.get("matchExpressions"):
        failures.append("selector must use exact matchLabels, not matchExpressions")
    if not match_labels:
        failures.append("spec.selector.matchLabels must be non-empty")
        return {}
    result: dict[str, str] = {}
    for key, value in match_labels.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key or not value:
            failures.append("spec.selector.matchLabels must contain non-empty string labels")
            return {}
        result[key] = value
    return result


def _endpoints(doc: dict[str, Any], failures: list[str]) -> list[str]:
    kind = doc.get("kind")
    field = ENDPOINT_FIELD[str(kind)]
    spec = _mapping(doc.get("spec"))
    values = spec.get(field)
    if not isinstance(values, list) or not values:
        failures.append(f"spec.{field} must be a non-empty list")
        return []
    ports: list[str] = []
    for index, endpoint in enumerate(values):
        if not isinstance(endpoint, dict):
            failures.append(f"spec.{field}[{index}] must be a mapping")
            continue
        forbidden = _forbidden_path(endpoint)
        if forbidden:
            failures.append(f"spec.{field}[{index}] must not contain credentials or URL field {forbidden}")
        port = endpoint.get("port")
        if not isinstance(port, str) or not port:
            failures.append(f"spec.{field}[{index}].port must be a named Service/container port")
        else:
            ports.append(port)
        path = endpoint.get("path")
        if not isinstance(path, str) or not path.startswith("/") or "://" in path:
            failures.append(f"spec.{field}[{index}].path must be an in-cluster absolute path")
        interval = _duration_seconds(endpoint.get("interval"))
        if interval is None or interval < 30:
            failures.append(f"spec.{field}[{index}].interval must be at least 30s")
    return ports


def _service_port_names(doc: dict[str, Any]) -> set[str]:
    ports = _mapping(doc.get("spec")).get("ports")
    if not isinstance(ports, list):
        return set()
    return {
        name
        for port in ports
        if isinstance(port, dict)
        for name in [port.get("name")]
        if isinstance(name, str) and name
    }


def _workload_port_names(doc: dict[str, Any]) -> set[str]:
    template = _mapping(_mapping(doc.get("spec")).get("template"))
    pod_spec = _mapping(template.get("spec"))
    containers = pod_spec.get("containers")
    if not isinstance(containers, list):
        return set()
    result: set[str] = set()
    for container in containers:
        ports = _mapping(container).get("ports")
        if not isinstance(ports, list):
            continue
        for port in ports:
            name = _mapping(port).get("name")
            if isinstance(name, str) and name:
                result.add(name)
    return result


def _workload_labels(doc: dict[str, Any]) -> dict[str, str]:
    template = _mapping(_mapping(doc.get("spec")).get("template"))
    metadata = _mapping(template.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    return {
        key: value
        for key, value in labels.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _target_matches(
    doc: dict[str, Any],
    selector: dict[str, str],
    docs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str], str]:
    kind = doc["kind"]
    namespace = _namespace(doc)
    if kind == "VMServiceScrape":
        matches = [
            item
            for item in docs
            if item.get("kind") == "Service"
            and _namespace(item) == namespace
            and _matches(_labels(item), selector)
        ]
        ports = _service_port_names(matches[0]) if len(matches) == 1 else set()
        return matches, ports, "Service"
    matches = [
        item
        for item in docs
        if item.get("kind") in WORKLOAD_KINDS
        and _namespace(item) == namespace
        and _matches(_workload_labels(item), selector)
    ]
    ports = _workload_port_names(matches[0]) if len(matches) == 1 else set()
    return matches, ports, "workload"


def _require_exact_keys(
    value: object, expected: set[str], path: str, failures: list[str]
) -> dict[str, Any]:
    if not isinstance(value, dict):
        failures.append(f"{path} must be a mapping")
        return {}
    if set(value) != expected:
        failures.append(f"{path} fields must be exactly {sorted(expected)}")
    return value


def _check_gke_managed_dcgm(doc: dict[str, Any]) -> list[str]:
    """Validate the one supported cross-namespace profile as a closed schema."""
    failures: list[str] = []
    _require_exact_keys(doc, {"apiVersion", "kind", "metadata", "spec"}, "resource", failures)
    if doc.get("apiVersion") != API_VERSION:
        failures.append(f"apiVersion must be {API_VERSION}")
    if doc.get("kind") != "VMPodScrape":
        failures.append("k8s context permits only VMPodScrape for the GKE-managed DCGM profile")

    metadata = _require_exact_keys(
        doc.get("metadata"), {"name", "namespace", "labels"}, "metadata", failures
    )
    if metadata.get("name") != DCGM_NAME:
        failures.append(f"metadata.name must be {DCGM_NAME}")
    if metadata.get("namespace") != DCGM_NAMESPACE:
        failures.append(f"metadata.namespace must be {DCGM_NAMESPACE}")
    if metadata.get("labels") != DCGM_LABELS:
        failures.append("metadata.labels must be the exact GKE-managed DCGM label set")

    spec = _require_exact_keys(
        doc.get("spec"),
        {"namespaceSelector", "selector", "podMetricsEndpoints"},
        "spec",
        failures,
    )
    namespace_selector = _require_exact_keys(
        spec.get("namespaceSelector"), {"matchNames"}, "spec.namespaceSelector", failures
    )
    if namespace_selector.get("matchNames") != [DCGM_SOURCE_NAMESPACE]:
        failures.append(f"spec.namespaceSelector.matchNames must be [{DCGM_SOURCE_NAMESPACE!r}]")
    selector = _require_exact_keys(spec.get("selector"), {"matchLabels"}, "spec.selector", failures)
    if selector.get("matchLabels") != {APP_NAME_LABEL: DCGM_EXPORTER_LABEL}:
        failures.append("spec.selector.matchLabels must be the exact GKE-managed DCGM selector")

    endpoints = spec.get("podMetricsEndpoints")
    if not isinstance(endpoints, list) or len(endpoints) != 1:
        failures.append("spec.podMetricsEndpoints must contain exactly one endpoint")
        return failures
    endpoint = _require_exact_keys(
        endpoints[0],
        set(DCGM_ENDPOINT) | {"metricRelabelConfigs"},
        "spec.podMetricsEndpoints[0]",
        failures,
    )
    for field, expected in DCGM_ENDPOINT.items():
        if endpoint.get(field) != expected:
            failures.append(f"spec.podMetricsEndpoints[0].{field} must be {expected!r}")
    relabel_configs = endpoint.get("metricRelabelConfigs")
    if not isinstance(relabel_configs, list) or len(relabel_configs) != 1:
        failures.append("spec.podMetricsEndpoints[0].metricRelabelConfigs must contain exactly one rule")
        return failures
    relabel = _require_exact_keys(
        relabel_configs[0], set(DCGM_RELABEL), "spec.podMetricsEndpoints[0].metricRelabelConfigs[0]", failures
    )
    for field, expected in DCGM_RELABEL.items():
        if relabel.get(field) != expected:
            failures.append(
                f"spec.podMetricsEndpoints[0].metricRelabelConfigs[0].{field} must be {expected!r}"
            )
    forbidden = _forbidden_path(endpoint)
    if forbidden:
        failures.append(f"GKE-managed DCGM endpoint must not contain credentials or URL field {forbidden}")
    return failures


def _contains_dcgm(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_dcgm(key) or _contains_dcgm(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_dcgm(item) for item in value)
    return isinstance(value, str) and "dcgm" in value.lower()


def _is_dcgm_like(doc: dict[str, Any]) -> bool:
    """Reserve every DCGM-looking scrape for the sole k8s static profile."""
    return _contains_dcgm(doc)


def _check_generic_scrape(
    doc: dict[str, Any],
    all_docs: list[dict[str, Any]],
    require_target_match: bool,
) -> list[str]:
    failures: list[str] = []
    if doc.get("apiVersion") != API_VERSION:
        failures.append(f"apiVersion must be {API_VERSION}")
    name = _name(doc)
    if name == "<unnamed>":
        failures.append("metadata.name is required")
    labels = _labels(doc)
    if labels.get("app.kubernetes.io/part-of") != "victoria-metrics":
        failures.append("metadata.labels app.kubernetes.io/part-of must be victoria-metrics")
    if not labels.get(APP_NAME_LABEL):
        failures.append("metadata.labels app.kubernetes.io/name is required")
    spec = _mapping(doc.get("spec"))
    if _is_dcgm_like(doc):
        failures.append("DCGM-like scrape resources require the exact k8s GKE-managed DCGM profile")
    if "namespaceSelector" in spec:
        failures.append("namespaceSelector is forbidden outside the exact k8s GKE-managed DCGM profile")
    selector = _selector(doc, failures)
    ports = _endpoints(doc, failures)
    if require_target_match and selector:
        namespace = _namespace(doc)
        if not namespace:
            failures.append("rendered resource must have metadata.namespace before target matching")
        else:
            matches, actual_ports, target_kind = _target_matches(doc, selector, all_docs)
            if len(matches) != 1:
                failures.append(
                    f"selector must match exactly one in-namespace {target_kind}; found {len(matches)}"
                )
            else:
                for port in ports:
                    if port not in actual_ports:
                        failures.append(
                            f"endpoint port {port!r} is not a named port on the selected {target_kind}"
                        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--repo-context",
        choices=REPO_CONTEXTS,
        default="auto",
        help="repository ownership context; only k8s permits the exact managed DCGM profile",
    )
    parser.add_argument(
        "--require-target-match",
        action="store_true",
        help="require generic scrapes to match exactly one rendered Service or workload",
    )
    args = parser.parse_args(argv)
    if not args.directory.is_dir():
        print(f"FAIL: directory not found: {args.directory}", file=sys.stderr)
        return 2

    parsed: list[tuple[Path, dict[str, Any]]] = []
    for path, document in iter_docs(args.directory):
        if isinstance(document, ParseError):
            print(f"FAIL: {path}: {document.message}", file=sys.stderr)
            return 2
        if isinstance(document, dict):
            parsed.append((path, document))
    docs = [document for _, document in parsed]
    scrapes = [
        (path, document)
        for path, document in parsed
        if document.get("kind") in ENDPOINT_FIELD
    ]
    if not scrapes:
        if args.require_target_match:
            print("FAIL: no VictoriaMetrics scrape resources found")
            return 1
        print("PASS: no VictoriaMetrics scrape resources found")
        return 0

    failures: list[str] = []
    for path, document in scrapes:
        object_name = f"{document.get('kind')}/{_name(document)}"
        if args.repo_context == "k8s" and _is_dcgm_like(document):
            reasons = _check_gke_managed_dcgm(document)
        else:
            reasons = _check_generic_scrape(document, docs, args.require_target_match)
        for reason in reasons:
            failures.append(f"FAIL: {path}: {object_name}: {reason}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"PASS: validated {len(scrapes)} VictoriaMetrics scrape resource(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
