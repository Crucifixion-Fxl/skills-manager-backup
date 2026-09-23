#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render the approved public Dashboard URI from a stable Dashboard UID."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


TARGET_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "grafana"
    / "target-repository.json"
)
UID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,39}$")
APPROVED_DASHBOARD_HOST = "grafana-us.addx.live"
REPORT_SUFFIXES = {
    "change-summary": "-change-summary.md",
    "source-removal": "-source-removal.md",
}


def _dashboard_uri_template() -> str:
    try:
        value = json.loads(TARGET_CONFIG_PATH.read_text(encoding="utf-8"))["dashboardUriTemplate"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Dashboard URI configuration: {exc}") from exc
    if not isinstance(value, str) or value.count("{uid}") != 1:
        raise ValueError("dashboardUriTemplate must contain exactly one {uid} placeholder")
    return value


def validate_dashboard_uid(uid: object) -> str:
    """Return one valid UID before it is used in a URI, path, or branch name."""
    if not isinstance(uid, str) or not UID_PATTERN.fullmatch(uid):
        raise ValueError("dashboard uid must be a stable 8-40 character lowercase slug")
    return uid


def dashboard_report_path(repo_root: Path, uid: object, report_kind: str) -> Path:
    """Return a checked report path strictly contained by the repository reports directory."""
    validated_uid = validate_dashboard_uid(uid)
    suffix = REPORT_SUFFIXES.get(report_kind)
    if suffix is None:
        raise ValueError("unsupported dashboard report kind")

    repository_root = repo_root.resolve()
    reports_root = (repository_root / "reports").resolve()
    try:
        reports_root.relative_to(repository_root)
    except ValueError as exc:
        raise ValueError("reports directory must be inside the Dashboard repository") from exc
    reports_root.mkdir(parents=True, exist_ok=True)

    summary_path = (reports_root / f"{validated_uid}{suffix}").resolve()
    try:
        summary_path.relative_to(reports_root)
    except ValueError as exc:
        raise ValueError("dashboard report path must stay inside reports/") from exc
    return summary_path


def expected_dashboard_uri(uid: str) -> str:
    """Return an output-only, approved public Dashboard URI for one stable UID."""
    validated_uid = validate_dashboard_uid(uid)
    uri = _dashboard_uri_template().replace("{uid}", validated_uid)
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or parsed.hostname != APPROVED_DASHBOARD_HOST
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or "/d/" not in parsed.path
    ):
        raise ValueError("dashboardUriTemplate is not the approved public Dashboard URI template")
    return uri


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                {"expectedDashboardUri": expected_dashboard_uri(args.uid), "uid": args.uid},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
