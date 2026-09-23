#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Safely invoke repository-owned Grafana Dashboard-as-Code utilities.

This adapter deliberately owns no Dashboard policy and never reads Grafana
credentials.  It only locates a Dashboard-as-Code checkout and delegates to
that checkout's reviewed Python modules.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence


REPOSITORY_MARKERS = (
    "config/environments.json",
    "config/datasource-allowlist.json",
    "config/victoria-metrics-services.json",
    "scripts/validate_all.py",
    "scripts/datasource_catalog.py",
)


class DashboardRepositoryError(RuntimeError):
    """The selected directory is not a usable Dashboard-as-Code repository."""


def locate_repository(start: Path, explicit: Path | None = None) -> Path:
    """Return the checked-out Dashboard-as-Code repository without cloning it."""
    candidates = [explicit] if explicit else [start, *start.parents]
    for candidate in candidates:
        if candidate is None:
            continue
        root = candidate.resolve()
        if all((root / marker).is_file() for marker in REPOSITORY_MARKERS):
            return root
    raise DashboardRepositoryError(
        "not inside grafana-dashboards-as-code; pass --repo-root for an "
        "isolated checkout"
    )


def dashboard_relative_path(repo_root: Path, path: Path) -> str:
    """Return a safe source-relative path and reject files outside dashboards/."""
    selected = path if path.is_absolute() else repo_root / path
    try:
        relative = selected.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise DashboardRepositoryError(
            f"dashboard path must be inside {repo_root}"
        ) from exc
    if not relative.parts or relative.parts[0] != "dashboards":
        raise DashboardRepositoryError("dashboard source must be under dashboards/")
    return relative.as_posix()


def run_repository_module(
    repo_root: Path,
    module: str,
    arguments: Sequence[str],
    *,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one repository module with no shell expansion or secret logging."""
    command = [sys.executable, "-m", module, *arguments]
    result = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        text=True,
        capture_output=capture_output,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "repository command failed").strip()
        raise DashboardRepositoryError(f"{module} failed: {detail}")
    return result


def expected_datasource_uid(
    repo_root: Path,
    *,
    environment: str,
    cluster: str,
    victoria_metrics_ref: str,
) -> str:
    """Resolve the one reviewed data source through the target repository."""
    program = (
        "import sys\n"
        "from pathlib import Path\n"
        "from scripts.datasource_catalog import expected_datasource_uid\n"
        "print(expected_datasource_uid(\n"
        "    Path(sys.argv[1]), environment=sys.argv[2], cluster=sys.argv[3],\n"
        "    victoria_metrics_ref=sys.argv[4]\n"
        "))\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(repo_root),
            environment,
            cluster,
            victoria_metrics_ref,
        ],
        cwd=repo_root,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "data source resolution failed").strip()
        raise DashboardRepositoryError(detail)
    datasource_uid = result.stdout.strip()
    if not datasource_uid:
        raise DashboardRepositoryError("repository data source catalog returned no UID")
    return datasource_uid


def canonicalize_dashboard(repo_root: Path, path: Path, *, check: bool = False) -> None:
    """Delegate canonical formatting to the Dashboard repository."""
    arguments = [str(path)]
    if check:
        arguments.append("--check")
    run_repository_module(repo_root, "scripts.canonicalize_dashboard", arguments)


def validate_dashboard(repo_root: Path, path: Path) -> None:
    """Run the repository's single source of truth validator for one file."""
    run_repository_module(
        repo_root,
        "scripts.validate_all",
        ["--repo-root", str(repo_root), "--file", str(path)],
    )


def validate_repository_config(repo_root: Path) -> None:
    """Validate reviewed environment, VM, data-source, and folder catalogs."""
    run_repository_module(repo_root, "scripts.validate_config", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    datasource = subparsers.add_parser("datasource", help="resolve one data source")
    datasource.add_argument("--environment", required=True)
    datasource.add_argument("--cluster", required=True)
    datasource.add_argument("--victoria-metrics-ref", required=True)

    validate = subparsers.add_parser("validate", help="validate one dashboard source")
    validate.add_argument("file", type=Path)

    canonicalize = subparsers.add_parser("canonicalize", help="canonicalize source")
    canonicalize.add_argument("file", type=Path)
    canonicalize.add_argument("--check", action="store_true")

    subparsers.add_parser("validate-config", help="validate repository catalogs")
    args = parser.parse_args()
    try:
        repo_root = locate_repository(Path.cwd(), args.repo_root)
        if args.command == "datasource":
            print(
                json.dumps(
                    {
                        "datasourceUid": expected_datasource_uid(
                            repo_root,
                            environment=args.environment,
                            cluster=args.cluster,
                            victoria_metrics_ref=args.victoria_metrics_ref,
                        )
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "validate":
            validate_dashboard(repo_root, args.file)
        elif args.command == "canonicalize":
            canonicalize_dashboard(repo_root, args.file, check=args.check)
        else:
            validate_repository_config(repo_root)
    except DashboardRepositoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
