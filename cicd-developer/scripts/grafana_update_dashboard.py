#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Update managed Dashboard source and validate it before retaining changes."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from grafana_change_summary import render_summary
from grafana_create_dashboard import panel
from grafana_dashboard_uri import (
    dashboard_report_path,
    expected_dashboard_uri,
    validate_dashboard_uid,
)
from grafana_repository import (
    DashboardRepositoryError,
    canonicalize_dashboard,
    dashboard_relative_path,
    locate_repository,
    validate_dashboard,
)


MANAGED_TAG = "managed-by-grafana-skill"
JVM_HEAP_PANEL_TITLE = "JVM Heap Memory"


def all_panels(dashboard: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every ordinary or nested panel."""
    pending = list(dashboard.get("panels", []))
    while pending:
        selected = pending.pop()
        if not isinstance(selected, dict):
            continue
        yield selected
        nested = selected.get("panels")
        if isinstance(nested, list):
            pending.extend(nested)


def _add_jvm_heap(
    dashboard: dict[str, Any], panels: list[dict[str, Any]], expression: str
) -> None:
    if any(selected.get("title") == JVM_HEAP_PANEL_TITLE for selected in panels):
        raise ValueError("JVM heap panel already exists")
    datasource_uid = next(
        (
            selected.get("datasource", {}).get("uid")
            for selected in panels
            if isinstance(selected.get("datasource"), dict)
            and selected["datasource"].get("uid")
        ),
        None,
    )
    if not datasource_uid:
        raise ValueError("cannot infer an allowlisted data source from existing panels")
    panel_id = max((selected.get("id", 0) for selected in panels), default=0) + 1
    bottom = max(
        (
            selected.get("gridPos", {}).get("y", 0) + selected.get("gridPos", {}).get("h", 0)
            for selected in panels
            if isinstance(selected.get("gridPos"), dict)
        ),
        default=0,
    )
    dashboard.setdefault("panels", []).append(
        panel(
            panel_id,
            JVM_HEAP_PANEL_TITLE,
            "bytes",
            expression,
            datasource_uid,
            0,
            bottom,
            24,
        )
    )


def update_dashboard(args: argparse.Namespace) -> dict[str, str]:
    """Apply requested source-only edits and restore the original on failure."""
    if bool(args.panel_id is not None) != bool(args.promql):
        raise ValueError("--panel-id and --promql must be used together")
    if args.panel_id is None and not args.add_jvm_heap:
        raise ValueError("request a PromQL change or --add-jvm-heap")
    jvm_promql = getattr(args, "jvm_promql", None)
    if args.add_jvm_heap and (not isinstance(jvm_promql, str) or not jvm_promql.strip()):
        raise ValueError(
            "--add-jvm-heap requires --jvm-promql; do not infer or add query labels"
        )
    repo_root = locate_repository(Path.cwd(), args.repo_root)
    path = args.file if args.file.is_absolute() else repo_root / args.file
    relative = dashboard_relative_path(repo_root, path)
    original = path.read_text(encoding="utf-8")
    dashboard = json.loads(original)
    tags = dashboard.get("tags", [])
    if MANAGED_TAG not in set(tags if isinstance(tags, list) else []):
        raise ValueError("refusing to update dashboard without managed-by-grafana-skill tag")
    uid = validate_dashboard_uid(dashboard.get("uid"))
    dashboard_uri = expected_dashboard_uri(uid)
    before = copy.deepcopy(dashboard)
    panels = list(all_panels(dashboard))
    if args.panel_id is not None:
        matches = [selected for selected in panels if selected.get("id") == args.panel_id]
        if len(matches) != 1:
            raise ValueError(f"panel id {args.panel_id} was not found exactly once")
        targets = matches[0].get("targets")
        if not isinstance(targets, list) or not targets or not isinstance(targets[0], dict):
            raise ValueError(f"panel id {args.panel_id} has no query target")
        targets[0]["expr"] = args.promql
    if args.add_jvm_heap:
        _add_jvm_heap(dashboard, panels, jvm_promql)
    try:
        path.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        canonicalize_dashboard(repo_root, path)
        validate_dashboard(repo_root, path)
        summary_path = dashboard_report_path(repo_root, uid, "change-summary")
        summary_path.write_text(render_summary(before, dashboard, relative), encoding="utf-8")
    except (DashboardRepositoryError, OSError, ValueError):
        path.write_text(original, encoding="utf-8")
        raise
    return {
        "branch": f"grafana/update-{uid}",
        "commit": f"feat(grafana): update {dashboard['title']}",
        "expectedDashboardUri": dashboard_uri,
        "path": relative,
        "summary": summary_path.relative_to(repo_root).as_posix(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--panel-id", type=int)
    parser.add_argument("--promql")
    parser.add_argument("--add-jvm-heap", action="store_true")
    parser.add_argument("--jvm-promql")
    args = parser.parse_args()
    try:
        print(json.dumps(update_dashboard(args), ensure_ascii=False, sort_keys=True))
    except (DashboardRepositoryError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
