#!/usr/bin/env python3
# /// script
# dependencies = ["defusedxml>=0.7.1"]
# ///
"""Verify whether CI ran or explicitly degraded real-standards tests."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as ET

EXPECTED_REAL_STANDARDS_TESTS = 11
EXPECTED_TOTAL_TESTS = 77
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REAL_STANDARDS_SKIP_REASON = "set DOMAIN_WORKSPACE_STANDARDS_ROOT"
EXPECTED_REAL_STANDARDS_CASES = {
    "test_security_iot_joint_assess_is_read_only_and_preserves_candidates",
    "test_assess_normalizes_equivalent_repository_urls",
    "test_assess_rejects_new_workspace_without_boundary_or_repositories",
    "test_real_standards_init_uses_official_schema_and_validator",
    "test_real_standards_init_rejects_unchanged_template",
    "test_real_standards_propose_uses_snapshot_without_authorizing_creation",
    "test_real_standards_init_negative_gates[unapproved]",
    "test_real_standards_init_negative_gates[duplicate-boundary]",
    "test_real_standards_init_negative_gates[owner]",
    "test_real_standards_init_negative_gates[reuse]",
    "test_archive_cli_exception_is_fail_closed",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail unless the JUnit report proves the declared standards validation mode."
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("online", "offline"))
    parser.add_argument("--standards-sha", default="")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def report_data(
    report: Path,
) -> tuple[dict[str, int], dict[str, str | None], str, list[str]]:
    root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts = {
        key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    cases, case_counts = _testcase_data(root)
    errors = []
    if counts != case_counts:
        errors.append("JUnit aggregate counts do not match testcase nodes")
    report_sha, sha_errors = _report_standards_sha(root)
    errors.extend(sha_errors)
    return counts, cases, report_sha, errors


def _testcase_data(root: Any) -> tuple[dict[str, str | None], dict[str, int]]:
    cases: dict[str, str | None] = {}
    case_counts = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    for case in root.findall(".//testcase"):
        name = str(case.attrib.get("name") or "")
        if not name or name in cases:
            raise ValueError("JUnit report contains a missing or duplicate testcase name")
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        skip_reason = None if skipped is None else str(skipped.attrib.get("message") or "")
        cases[name] = skip_reason
        case_counts["tests"] += 1
        case_counts["failures"] += failure is not None
        case_counts["errors"] += error is not None
        case_counts["skipped"] += skipped is not None
    return cases, case_counts


def _report_standards_sha(root: Any) -> tuple[str, list[str]]:
    sha_values = {
        str(node.attrib.get("value") or "")
        for node in root.findall(".//property[@name='domain_workspace_standards_sha']")
    }
    errors = (
        ["JUnit report contains conflicting standards SHA properties"]
        if len(sha_values) > 1
        else []
    )
    report_sha = next(iter(sha_values), "")
    return report_sha, errors


def _validate_report_basics(
    counts: dict[str, int], cases: dict[str, str | None]
) -> list[str]:
    errors = []
    if counts["failures"] or counts["errors"]:
        errors.append("JUnit report contains failures or errors")
    if counts["tests"] < EXPECTED_TOTAL_TESTS:
        errors.append(f"JUnit report must contain at least {EXPECTED_TOTAL_TESTS} tests")
    if EXPECTED_REAL_STANDARDS_CASES - set(cases):
        errors.append("JUnit report is missing expected real-standards testcases")
    return errors


def _validate_online_mode(
    standards_sha: str,
    counts: dict[str, int],
    cases: dict[str, str | None],
    report_sha: str,
) -> list[str]:
    errors = []
    if not FULL_SHA.fullmatch(standards_sha):
        errors.append("online mode requires an exact standards SHA")
    if report_sha != standards_sha:
        errors.append("online JUnit standards SHA does not match the verified checkout")
    if counts["skipped"]:
        errors.append("online mode must execute every real-standards test")
    if any(cases.get(name) is not None for name in EXPECTED_REAL_STANDARDS_CASES):
        errors.append("online mode did not execute every expected real-standards testcase")
    return errors


def _validate_offline_mode(
    standards_sha: str,
    counts: dict[str, int],
    cases: dict[str, str | None],
    report_sha: str,
) -> list[str]:
    errors = []
    if standards_sha:
        errors.append("offline mode must not claim a standards SHA")
    if report_sha:
        errors.append("offline JUnit must not claim a standards SHA")
    if counts["skipped"] != EXPECTED_REAL_STANDARDS_TESTS:
        errors.append(
            f"offline mode must skip exactly {EXPECTED_REAL_STANDARDS_TESTS} real-standards tests"
        )
    skipped_cases = {name for name, reason in cases.items() if reason is not None}
    if skipped_cases != EXPECTED_REAL_STANDARDS_CASES:
        errors.append("offline mode skipped testcase set is not the real-standards allowlist")
    if any(
        REAL_STANDARDS_SKIP_REASON not in str(cases.get(name) or "")
        for name in EXPECTED_REAL_STANDARDS_CASES
    ):
        errors.append("offline mode contains an unrelated skipped test")
    return errors


def validate_mode(
    mode: str,
    standards_sha: str,
    counts: dict[str, int],
    cases: dict[str, str | None],
    report_sha: str,
) -> list[str]:
    errors = _validate_report_basics(counts, cases)
    if mode == "online":
        errors.extend(
            _validate_online_mode(standards_sha, counts, cases, report_sha)
        )
    else:
        errors.extend(
            _validate_offline_mode(standards_sha, counts, cases, report_sha)
        )
    return errors


def main() -> None:
    args = parse_args()
    counts, cases, report_sha, errors = report_data(args.report)
    errors.extend(validate_mode(args.mode, args.standards_sha, counts, cases, report_sha))
    payload = {
        "mode": args.mode,
        "degraded": args.mode == "offline",
        "standards_sha": args.standards_sha or None,
        **counts,
        "passed": counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"],
        "errors_detail": errors,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
