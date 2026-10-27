"""Auditor 合并内容完整性证据校验（gitlab-mr Rule 14 / Step 2.87）。

报告里的自报数据不作为依据：在当前仓库（MR 所在仓库，cwd）里用 `git ls-remote` 核对目标分支 SHA，
并对同一对 SHA 重跑 check_merge_integrity.py，比较退出码和每条结论（check / level / files）。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from merge_integrity_sync import LONG_LIVED_TARGET, sync_parents

CHECKER = Path(__file__).resolve().parents[1] / "scripts" / "check_merge_integrity.py"
SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
SIGNATURE = ("approved_by", "approved_at_utc", "reason")


class MergeIntegrityError(ValueError):
    """完整性证据缺失、未绑定当前 HEAD / 远端目标，或 FAIL 没有匹配的签字豁免。"""


def require_text(value: Any, label: str, error: type[Exception] = MergeIntegrityError) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error(f"{label} must be a non-empty string")
    return value


def merge_integrity_required(target_branch: Any) -> bool:
    return isinstance(target_branch, str) and bool(LONG_LIVED_TARGET.match(target_branch))


def _git(error: type[Exception], *args: str) -> str:
    result = subprocess.run(["git", *args], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise error(f"git {args[0]} failed: {result.stderr.strip() or result.returncode}")
    return result.stdout.strip()


def remote_target_sha(branch: str, error: type[Exception]) -> str:
    out = _git(error, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}").split()
    if not out or not SHA.match(out[0]):
        raise error(f"cannot resolve origin/{branch} with git ls-remote")
    return out[0]


def require_commits(error: type[Exception], *shas: str) -> None:
    for sha in shas:
        if subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode != 0:
            raise error(f"commit {sha} is not available locally; fetch the target branch and MR head first")


def canonical(findings: list[dict[str, Any]]) -> set[tuple[str, str, tuple[str, ...]]]:
    return {(f.get("check"), f.get("level"), tuple(sorted(f.get("files") or []))) for f in findings}


def rerun(target_sha: str, head_sha: str, error: type[Exception]) -> dict[str, Any]:
    result = subprocess.run([sys.executable, str(CHECKER), target_sha, head_sha, "--json"],
                            text=True, capture_output=True, check=False)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise error(f"merge integrity re-run did not complete (exit {result.returncode}): "
                    f"{result.stderr.strip()}") from exc
    if report.get("exit_code") != result.returncode:
        raise error("merge integrity re-run exit code is inconsistent")
    return report


def waiver_matches(waiver: Any, head_sha: str, target_sha: str, checks: set, paths: set) -> bool:
    if not isinstance(waiver, dict) or waiver.get("head_sha") != head_sha or waiver.get("target_sha") != target_sha:
        return False
    w_checks, w_files = waiver.get("checks"), waiver.get("files")
    if not all(isinstance(v, list) and v and all(isinstance(i, str) and i for i in v) for v in (w_checks, w_files)):
        return False
    if not all(isinstance(waiver.get(k), str) and waiver[k].strip() for k in SIGNATURE):
        return False
    try:
        approved = datetime.fromisoformat(waiver["approved_at_utc"].replace("Z", "+00:00"))
    except ValueError:
        return False
    return approved.utcoffset() is not None and approved.utcoffset().total_seconds() == 0 \
        and checks <= set(w_checks) and paths <= set(w_files)


def validate_report(evidence: dict[str, Any], head_sha: str, target_sha: str, error: type[Exception]) -> dict:
    path = Path(require_text(evidence.get("evidence_file"), "merge_integrity.evidence_file", error))
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise error(f"cannot read merge integrity report: {exc}") from exc
    if hashlib.sha256(content).hexdigest() != evidence.get("report_sha256"):
        raise error("merge integrity report hash does not match report_sha256")
    try:
        report = json.loads(content)
    except json.JSONDecodeError as exc:
        raise error(f"merge integrity report is not JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise error("merge integrity report must be a JSON object")
    if report.get("source_sha") != head_sha or report.get("target_sha") != target_sha:
        raise error("merge integrity report is not bound to the audited head/target SHA")
    findings = report.get("findings")
    if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
        raise error("merge integrity report has no findings list")
    if {f.get("check") for f in findings} != set("ABCDE"):
        raise error("merge integrity report must contain results for checks A-E")
    exit_code = evidence.get("exit_code")
    failing = any(f.get("level") == "FAIL" for f in findings)
    unverified = any(f.get("level") == "UNVERIFIED" for f in findings)
    expected = 1 if failing else 2 if unverified else 0
    if report.get("exit_code") != exit_code or exit_code != expected \
            or report.get("failed") is not failing or report.get("unverified") is not unverified:
        raise error("merge integrity exit code / failed / unverified do not match the findings")
    return report


def verified_report(evidence: dict[str, Any], source_sha: str, target_sha: str, error: type[Exception]) -> dict:
    """Hash/binding/consistency checks plus a fresh re-run of the same SHA pair."""
    report = validate_report(evidence, source_sha, target_sha, error)
    fresh = rerun(target_sha, source_sha, error)
    if fresh.get("exit_code") != report["exit_code"] or canonical(fresh["findings"]) != canonical(report["findings"]):
        raise error(f"merge integrity report for {source_sha[:12]} differs from a fresh re-run on the same SHAs")
    return report


def validate_merge_integrity(
    state: dict[str, Any], audit: dict[str, Any], error: type[Exception] = MergeIntegrityError
) -> None:
    """Bind the merge content-integrity report to the audited HEAD and the live target (gitlab-mr Rule 14)."""
    evidence = audit.get("merge_integrity")
    if not isinstance(evidence, dict):
        raise error("audit.merge_integrity must be an object")
    applicable = evidence.get("applicable")
    if not isinstance(applicable, bool):
        raise error("merge_integrity.applicable must be a boolean")
    branch = state.get("target_branch")
    if merge_integrity_required(branch) and not applicable:
        raise error("merge integrity check is required for this target branch")
    head_sha = require_text(audit.get("head_sha"), "audit.head_sha", error)
    live_target = remote_target_sha(require_text(branch, "state.target_branch", error), error)
    require_commits(error, head_sha, live_target)
    if not applicable:
        if _git(error, "rev-list", "--merges", f"{live_target}..{head_sha}"):
            raise error("merge integrity check is required: the MR range contains merge commits")
        return
    if evidence.get("source_sha") != head_sha:
        raise error("merge_integrity.source_sha does not match audited head_sha")
    target_sha = evidence.get("target_sha")
    if not isinstance(target_sha, str) or not SHA.match(target_sha):
        raise error("merge_integrity.target_sha must be a full commit SHA")
    if target_sha != live_target:
        raise error(f"merge_integrity.target_sha is not the current origin/{branch} ({live_target[:12]})")
    reports = [(head_sha, verified_report(evidence, head_sha, target_sha, error))]
    syncs = evidence.get("sync_reports", [])
    if not isinstance(syncs, list) or not all(isinstance(item, dict) for item in syncs):
        raise error("merge_integrity.sync_reports must be a list of objects")
    required = sync_parents(target_sha, head_sha, error)
    given = [item.get("merged_sha") for item in syncs]
    if not all(isinstance(g, str) for g in given) or sorted(given) != required:
        raise error("merge_integrity.sync_reports must hold one `<target_sha> <merged_sha>` report for each merged "
                    f"long-lived commit: {[s[:12] for s in required]}, got {[str(g)[:12] for g in given]}")
    reports += [(item["merged_sha"], verified_report(item, item["merged_sha"], target_sha, error)) for item in syncs]
    if any(report["exit_code"] == 2 for _, report in reports):
        raise error("merge integrity check did not complete (exit 2 / unverified)")
    failing = [f for _, report in reports for f in report["findings"] if f.get("level") == "FAIL"]
    if not failing:
        return
    checks = {f.get("check") for f in failing}
    paths = {p for f in failing for p in f.get("files") or []}
    if any(waiver_matches(w, head_sha, target_sha, checks, paths) for w in state.get("merge_integrity_waivers") or []):
        return
    raise error(
        "merge integrity FAIL without a matching signed waiver: "
        + "; ".join(f"{f.get('check')}: {f.get('message')}" for f in failing)
    )
