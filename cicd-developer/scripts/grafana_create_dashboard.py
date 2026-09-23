#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Create a deterministic, policy-managed service Dashboard source file."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from grafana_change_summary import render_summary
from grafana_dashboard_uri import (
    dashboard_report_path,
    expected_dashboard_uri,
    validate_dashboard_uid,
)
from grafana_discover_workload import Workload, discover_workload
from grafana_repository import (
    DashboardRepositoryError,
    canonicalize_dashboard,
    dashboard_relative_path,
    expected_datasource_uid,
    locate_repository,
    validate_dashboard,
)


SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = SKILL_ROOT / "recipes" / "grafana" / "dashboard.json.tmpl"


def stable_uid(namespace: str, service: str, environment: str) -> str:
    """Derive a stable, Grafana-valid UID without random values."""
    value = f"{environment}-{namespace}-{service}"
    if len(value) < 8:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        return f"dashboard-{digest}"
    if len(value) <= 40:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{value[:31].rstrip('-')}-{digest}"


def _line_defaults(unit: str) -> dict[str, Any]:
    return {
        "unit": unit,
        "custom": {
            "drawStyle": "line",
            "fillOpacity": 0,
            "lineInterpolation": "linear",
            "lineWidth": 1,
            "showPoints": "auto",
            "spanNulls": False,
        },
    }


def panel(
    panel_id: int,
    title: str,
    unit: str,
    expression: str,
    datasource_uid: str,
    x: int,
    y: int,
    width: int = 12,
) -> dict[str, Any]:
    """Build a line-chart panel with a stable ID and its source expression."""
    return {
        "datasource": {"type": "prometheus", "uid": datasource_uid},
        "fieldConfig": {"defaults": _line_defaults(unit), "overrides": []},
        "gridPos": {"h": 7, "w": width, "x": x, "y": y},
        "id": panel_id,
        "options": {
            "legend": {
                "calcs": [],
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [{"expr": expression, "refId": "A"}],
        "title": title,
        "type": "timeseries",
    }


def build_standard_panels(
    namespace: str,
    service: str,
    datasource_uid: str,
) -> list[dict[str, Any]]:
    """Build opt-in standard service panels without Dashboard variables.

    Cluster ownership selects the data source; it is not a PromQL selector.
    Explicit developer panel specs always take precedence over these defaults.
    """
    scope = f'namespace="{namespace}",service="{service}"'
    return [
        panel(1, "Available Replicas", "short", f"sum(kube_deployment_status_replicas_available{{{scope}}})", datasource_uid, 0, 0, 6),
        panel(2, "CPU Usage", "percentunit", f'sum(rate(container_cpu_usage_seconds_total{{{scope},container!=""}}[5m]))', datasource_uid, 6, 0, 9),
        panel(3, "Memory Usage", "bytes", f'sum(container_memory_working_set_bytes{{{scope},container!=""}})', datasource_uid, 15, 0, 9),
        panel(4, "Pod Restarts", "short", f"sum(increase(kube_pod_container_status_restarts_total{{{scope}}}[15m]))", datasource_uid, 0, 7),
        panel(5, "HTTP Request Rate", "reqps", f"sum(rate(http_server_requests_seconds_count{{{scope}}}[5m]))", datasource_uid, 12, 7),
        panel(6, "HTTP Error Rate", "percentunit", f'sum(rate(http_server_requests_seconds_count{{{scope},status=~"5.."}}[5m])) / clamp_min(sum(rate(http_server_requests_seconds_count{{{scope}}}[5m])), 1)', datasource_uid, 0, 14),
        panel(7, "P95 Request Latency", "s", f"histogram_quantile(0.95, sum by (le) (rate(http_server_requests_seconds_bucket{{{scope}}}[5m])))", datasource_uid, 12, 14),
    ]


def _parse_panel_specs(value: str | None) -> list[tuple[str, str, str]] | None:
    """Parse developer-provided panels without changing their PromQL text."""
    if value is None:
        return None
    try:
        document = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("--panel-specs-json must be a JSON array") from exc
    if not isinstance(document, list) or not document:
        raise ValueError("--panel-specs-json must contain at least one panel")

    panels: list[tuple[str, str, str]] = []
    for index, item in enumerate(document, 1):
        if not isinstance(item, dict):
            raise ValueError(f"panel spec {index} must be a JSON object")
        unexpected = sorted(set(item) - {"expr", "title", "unit"})
        if unexpected:
            raise ValueError(f"panel spec {index} has unsupported fields: {unexpected}")
        title = item.get("title")
        expression = item.get("expr")
        unit = item.get("unit", "short")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"panel spec {index} requires a non-empty title")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError(f"panel spec {index} requires a non-empty expr")
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"panel spec {index} unit must be a non-empty string")
        # Do not strip, scope, aggregate, or otherwise rewrite `expression`.
        panels.append((title.strip(), expression, unit.strip()))
    return panels


def build_requested_panels(
    specs: list[tuple[str, str, str]], datasource_uid: str
) -> list[dict[str, Any]]:
    """Create stable line panels while retaining each supplied PromQL verbatim."""
    return [
        panel(
            panel_id=index,
            title=title,
            unit=unit,
            expression=expression,
            datasource_uid=datasource_uid,
            x=((index - 1) % 2) * 12,
            y=((index - 1) // 2) * 7,
        )
        for index, (title, expression, unit) in enumerate(specs, 1)
    ]


def _slug(label: str, value: str) -> str:
    if not SLUG.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase slug")
    return value


def _resolve_service_and_namespace(args: argparse.Namespace) -> tuple[str, str, Workload | None]:
    discovered: Workload | None = None
    if args.application_root:
        try:
            discovered = discover_workload(args.application_root, args.workload_name)
        except ValueError:
            if not (args.service and args.namespace):
                raise
    service = args.service or (discovered.name if discovered else None)
    namespace = args.namespace or (discovered.namespace if discovered else None)
    if not service:
        raise ValueError(
            "service was not provided; pass --application-root to discover a "
            "Rollout/Deployment or explicitly pass --service"
        )
    if not namespace:
        raise ValueError("namespace was not provided and is absent from the selected workload")
    return _slug("service", service), _slug("namespace", namespace), discovered


def _output_path(repo_root: Path, output: Path | None, environment: str, namespace: str, service: str) -> Path:
    if output is None:
        environments = json.loads((repo_root / "config/environments.json").read_text(encoding="utf-8"))
        configuration = environments.get(environment)
        if not isinstance(configuration, dict):
            raise ValueError("environment is not configured in config/environments.json")
        output = Path(configuration["dashboardPath"]) / namespace / f"{service}.json"
    selected = output if output.is_absolute() else repo_root / output
    dashboard_relative_path(repo_root, selected)
    return selected


def create_dashboard(args: argparse.Namespace) -> dict[str, str]:
    """Create, canonicalize, validate, and summarize a Dashboard source change."""
    repo_root = locate_repository(Path.cwd(), args.repo_root)
    service, namespace, discovered = _resolve_service_and_namespace(args)
    team = _slug("team", args.team)
    cluster = _slug("cluster", args.cluster)
    environment = _slug("environment", args.environment)
    victoria_metrics_ref = _slug("victoria_metrics_ref", args.victoria_metrics_ref)
    datasource_uid = expected_datasource_uid(
        repo_root,
        environment=environment,
        cluster=cluster,
        victoria_metrics_ref=victoria_metrics_ref,
    )
    if args.datasource_uid and args.datasource_uid != datasource_uid:
        raise ValueError(
            "--datasource-uid does not match the cluster-mapped VictoriaMetrics "
            f"data source UID {datasource_uid!r}"
        )
    output = _output_path(repo_root, args.output, environment, namespace, service)
    if output.exists():
        raise ValueError(f"refusing to overwrite existing dashboard: {output}")
    panel_specs = _parse_panel_specs(getattr(args, "panel_specs_json", None))
    dashboard = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    uid = validate_dashboard_uid(stable_uid(namespace, service, environment))
    dashboard_uri = expected_dashboard_uri(uid)
    dashboard.update(
        {
            "panels": (
                build_requested_panels(panel_specs, datasource_uid)
                if panel_specs is not None
                else build_standard_panels(namespace, service, datasource_uid)
            ),
            "tags": [
                f"cluster-{cluster}",
                f"environment-{environment}",
                "managed-by-grafana-skill",
                f"namespace-{namespace}",
                f"service-{service}",
                f"team-{team}",
                f"victoria-metrics-ref-{victoria_metrics_ref}",
            ],
            "templating": {"list": []},
            "title": service,
            "uid": uid,
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    try:
        canonicalize_dashboard(repo_root, output)
        validate_dashboard(repo_root, output)
        relative = dashboard_relative_path(repo_root, output)
        summary_path = dashboard_report_path(repo_root, uid, "change-summary")
        summary_path.write_text(render_summary(None, dashboard, relative), encoding="utf-8")
    except (DashboardRepositoryError, OSError, ValueError):
        output.unlink(missing_ok=True)
        raise
    source = f"{discovered.kind}/{discovered.name}" if discovered and not args.service else "explicit --service"
    return {
        "branch": f"grafana/{team}-{service}-{environment}",
        "commit": f"feat(grafana): add {service} {environment} dashboard",
        "datasourceUid": datasource_uid,
        "expectedDashboardUri": dashboard_uri,
        "path": relative,
        "serviceSource": source,
        "summary": summary_path.relative_to(repo_root).as_posix(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--service")
    parser.add_argument("--application-root", type=Path)
    parser.add_argument("--workload-name")
    parser.add_argument("--team", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--victoria-metrics-ref", required=True)
    parser.add_argument("--namespace")
    parser.add_argument("--datasource-uid", help="compatibility check only; the catalog selects the UID")
    parser.add_argument(
        "--panel-specs-json",
        help=(
            "JSON array of {title, expr, unit?}; supplied PromQL is preserved "
            "verbatim and takes precedence over opt-in standard panels"
        ),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(create_dashboard(args), ensure_ascii=False, sort_keys=True))
    except (DashboardRepositoryError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
