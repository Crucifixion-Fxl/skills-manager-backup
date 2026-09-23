#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run repository-owned Dashboard and catalog validation; own no policy rules."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grafana_repository import (
    DashboardRepositoryError,
    locate_repository,
    validate_dashboard,
    validate_repository_config,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--config", action="store_true", help="also validate repository catalogs")
    args = parser.parse_args()
    try:
        repo_root = locate_repository(Path.cwd(), args.repo_root)
        validated: dict[str, str | bool] = {"repository": str(repo_root)}
        if args.config:
            validate_repository_config(repo_root)
            validated["config"] = True
        if args.file:
            validate_dashboard(repo_root, args.file)
            validated["file"] = str(args.file)
        elif not args.config:
            from grafana_repository import run_repository_module

            run_repository_module(repo_root, "scripts.validate_all", ["--repo-root", str(repo_root)])
            validated["all"] = True
        print(json.dumps(validated, ensure_ascii=False, sort_keys=True))
    except DashboardRepositoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
