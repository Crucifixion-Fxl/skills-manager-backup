#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Remove one managed Dashboard source only; never call Grafana APIs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grafana_change_summary import render_summary
from grafana_dashboard_uri import (
    dashboard_report_path,
    expected_dashboard_uri,
    validate_dashboard_uid,
)
from grafana_repository import DashboardRepositoryError, dashboard_relative_path, locate_repository


MANAGED_TAG = "managed-by-grafana-skill"


def remove_source(
    repo_root: Path,
    source: Path,
    *,
    confirmed: bool,
) -> dict[str, str]:
    """Delete only a managed JSON source, leaving CI to handle remote deletion."""
    if not confirmed:
        raise ValueError("explicit source-removal confirmation is required")
    path = source if source.is_absolute() else repo_root / source
    relative = dashboard_relative_path(repo_root, path)
    if path.suffix != ".json" or not path.is_file():
        raise ValueError("source removal accepts one existing Dashboard JSON file")
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    tags = dashboard.get("tags")
    if not isinstance(tags, list) or MANAGED_TAG not in tags:
        raise ValueError("refusing to remove dashboard without managed-by-grafana-skill tag")
    uid = validate_dashboard_uid(dashboard.get("uid"))
    dashboard_uri = expected_dashboard_uri(uid)
    summary_path = dashboard_report_path(repo_root, uid, "source-removal")
    summary_path.write_text(
        render_summary(dashboard, {"title": dashboard.get("title"), "uid": uid, "panels": []}, relative)
        + "\nRemote Grafana deletion is intentionally deferred to protected GitLab CI.\n",
        encoding="utf-8",
    )
    path.unlink()
    return {
        "branch": f"grafana/remove-{uid}",
        "commit": f"feat(grafana): remove {dashboard.get('title', uid)} dashboard source",
        "expectedDashboardUri": dashboard_uri,
        "path": relative,
        "summary": summary_path.relative_to(repo_root).as_posix(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument(
        "--confirm-source-removal",
        action="store_true",
        help="required explicit confirmation; this deletes only the local source file",
    )
    args = parser.parse_args()
    if not args.confirm_source_removal:
        parser.error("--confirm-source-removal is required; no Grafana deletion is performed locally")
    try:
        repo_root = locate_repository(Path.cwd(), args.repo_root)
        print(
            json.dumps(
                remove_source(repo_root, args.file, confirmed=args.confirm_source_removal),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    except (DashboardRepositoryError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
