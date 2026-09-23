#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""校验 MR 模式并创建 gitlab-mr Driver 状态文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from release_drive_state import cleanup_state, promotion_state
from release_drive_validation import (
    require_current_git_head,
    require_https_url,
    require_no_remote_staging_branch,
    require_sha,
    require_text,
)
from release_workflow_policy import PolicyError, classify_mr_mode
from staging_writer_cleanup import attest_staging_writer_cleanup


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Validate MR workflow mode and initialize Driver state."
    )
    result.add_argument("--mr-iid", required=True, type=int)
    result.add_argument("--project-path", required=True)
    result.add_argument("--branch", required=True)
    result.add_argument("--target-branch", required=True)
    result.add_argument("--mr-mode", required=True)
    result.add_argument("--head-sha", required=True)
    result.add_argument(
        "--staging-flow-exists",
        choices=("true", "false", "unknown"),
        default="unknown",
    )
    result.add_argument("--staging-branch", default="staging")
    result.add_argument("--staging-flow-evidence", default="")
    result.add_argument("--canonical-verification-mr", type=int)
    result.add_argument("--candidate-mr", type=int)
    result.add_argument("--contract", default="")
    result.add_argument("--cleanup-contract", default="")
    result.add_argument("--not-applicable-reason", default="")
    result.add_argument("--emergency-approved", action="store_true")
    result.add_argument("--emergency-owner", default="")
    result.add_argument("--emergency-reason", default="")
    result.add_argument("--emergency-verification", default="")
    result.add_argument("--emergency-backport", default="")
    result.add_argument("--output", required=True, type=Path)
    result.add_argument("--json", action="store_true", dest="as_json")
    return result


def run_parity_check(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if args.canonical_verification_mr is None or args.candidate_mr is None:
        raise PolicyError(
            "canonical_verification_mr and candidate_mr are required "
            "for production promotion"
        )
    command = [
        sys.executable,
        str(Path(__file__).with_name("release_parity_check.py")),
        "--project-path",
        args.project_path,
        "--canonical-verification-mr",
        str(args.canonical_verification_mr),
        "--candidate-mr",
        str(args.candidate_mr),
        "--staging-branch",
        args.staging_branch,
        "--json",
    ]
    if args.contract:
        command.extend(["--contract", args.contract])
    result = subprocess.run(
        command,
        capture_output=True,
        text=False,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise PolicyError(f"release parity check failed: {detail}")
    raw = result.stdout
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyError("release parity check returned invalid JSON") from exc
    if not isinstance(report, dict) or report.get("code_status") != "pass":
        raise PolicyError("release parity report must contain code_status=pass")
    if not isinstance(report.get("refs"), dict) or not isinstance(
        report.get("provenance"), dict
    ):
        raise PolicyError("parity_report is missing refs or provenance")
    return report, hashlib.sha256(raw).hexdigest()


def _require_matching_mode(
    args: argparse.Namespace,
    staging_flow_exists: bool | None,
    verified_sha_available: bool,
    staging_writer_cleanup_attested: bool,
) -> None:
    expected_mode = classify_mr_mode(
        args.target_branch,
        staging_flow_exists=staging_flow_exists,
        verified_sha_available=verified_sha_available,
        staging_writer_cleanup_attested=staging_writer_cleanup_attested,
        emergency_approved=args.emergency_approved,
    )
    if args.mr_mode != expected_mode:
        raise PolicyError(
            f"mr_mode {args.mr_mode!r} conflicts with evidence; "
            f"expected {expected_mode!r}"
        )


def _validate_promotion_report(
    args: argparse.Namespace,
    parity_report: dict[str, Any] | None,
) -> None:
    if parity_report is None:
        raise PolicyError("release parity report is required for promotion")
    refs = parity_report["refs"]
    provenance = parity_report["provenance"]
    candidate = provenance.get("candidate_mr")
    canonical = provenance.get("canonical_verification_mr")
    if not isinstance(candidate, dict) or not isinstance(canonical, dict):
        raise PolicyError("parity_report is missing MR provenance")
    if provenance.get("project_path") != args.project_path:
        raise PolicyError("parity_report project_path does not match state")
    if candidate.get("target_branch") != args.target_branch:
        raise PolicyError("parity_report target_branch does not match state")
    if refs.get("candidate") != args.head_sha:
        raise PolicyError("head_sha must equal parity_report candidate MR SHA")
    for field in ("canonical_base", "canonical", "candidate_base", "candidate"):
        require_sha(str(refs.get(field, "")), f"parity_report.refs.{field}")


def _validate_emergency_fields(args: argparse.Namespace) -> None:
    for value, label in (
        (args.emergency_owner, "emergency_owner"),
        (args.emergency_reason, "emergency_reason"),
        (args.emergency_verification, "emergency_verification"),
        (args.emergency_backport, "emergency_backport"),
    ):
        require_text(value, label)


def _validate_mode_evidence(
    args: argparse.Namespace,
    parity_report: dict[str, Any] | None,
    cleanup_report: dict[str, Any] | None,
) -> None:
    if args.mr_mode == "production-promotion":
        _validate_promotion_report(args, parity_report)
    elif args.mr_mode == "production-non-promotion":
        require_https_url(args.staging_flow_evidence, "staging_flow_evidence")
        require_text(args.not_applicable_reason, "not_applicable_reason")
        require_no_remote_staging_branch(args.staging_branch)
    elif args.mr_mode == "staging-writer-cleanup":
        if cleanup_report is None or cleanup_report.get("code_status") != "pass":
            raise PolicyError("attested cleanup report is required")
    elif args.mr_mode == "emergency-hotfix":
        _validate_emergency_fields(args)


def build_state(args: argparse.Namespace) -> dict[str, object]:
    staging_flow_exists = {
        "true": True,
        "false": False,
        "unknown": None,
    }[args.staging_flow_exists]
    parity_report = None
    parity_report_sha256 = ""
    cleanup_report = None
    cleanup_report_sha256 = ""
    wants_promotion = args.mr_mode == "production-promotion"
    if wants_promotion:
        parity_report, parity_report_sha256 = run_parity_check(args)
    require_sha(args.head_sha, "head_sha")
    require_current_git_head(args.head_sha)
    wants_cleanup = args.mr_mode == "staging-writer-cleanup"
    if wants_cleanup:
        cleanup_report, cleanup_report_sha256 = attest_staging_writer_cleanup(args)
    _require_matching_mode(
        args,
        staging_flow_exists,
        wants_promotion,
        cleanup_report is not None,
    )
    _validate_mode_evidence(args, parity_report, cleanup_report)
    return {
        "mr_iid": args.mr_iid,
        "branch": args.branch,
        "target_branch": args.target_branch,
        "mr_mode": args.mr_mode,
        "project_path": args.project_path,
        "promotion": promotion_state(
            args,
            staging_flow_exists,
            parity_report,
            parity_report_sha256,
        ),
        "cleanup": cleanup_state(cleanup_report, cleanup_report_sha256),
        "emergency": {
            "approved": args.emergency_approved,
            "owner": args.emergency_owner,
            "reason": args.emergency_reason,
            "verification": args.emergency_verification,
            "backport": args.emergency_backport,
        },
        "last_snapshot": {
            "head_sha": args.head_sha,
            "pipeline_id": 0,
            "unresolved_discussions": 0,
            "checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "no_change_streak": 0,
        "history": [],
        "completed": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        state = build_state(args)
        args.output.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, PolicyError) as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Driver state initialized: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
