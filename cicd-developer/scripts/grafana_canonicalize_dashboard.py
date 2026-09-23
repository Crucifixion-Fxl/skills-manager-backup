#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Canonicalize one Dashboard source using the target repository implementation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grafana_repository import DashboardRepositoryError, canonicalize_dashboard, locate_repository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        repo_root = locate_repository(Path.cwd(), args.repo_root)
        canonicalize_dashboard(repo_root, args.file, check=args.check)
        print(
            json.dumps(
                {"canonicalized": str(args.file), "check": args.check},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    except DashboardRepositoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
