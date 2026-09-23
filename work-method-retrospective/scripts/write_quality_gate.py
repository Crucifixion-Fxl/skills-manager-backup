#!/usr/bin/env python3
"""Persist an accepted rerun quality gate for later weekly reuse."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "work_method_validate_manifest", SCRIPT_DIR / "validate_manifest.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("manifest validator could not be loaded")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
APPROVAL_SPEC = importlib.util.spec_from_file_location(
    "weekly_report_validate_approval",
    SCRIPT_DIR.parents[1]
    / "weekly-report-publish"
    / "scripts"
    / "validate_approval.py",
)
if APPROVAL_SPEC is None or APPROVAL_SPEC.loader is None:
    raise RuntimeError("weekly report approval validator could not be loaded")
APPROVAL = importlib.util.module_from_spec(APPROVAL_SPEC)
APPROVAL_SPEC.loader.exec_module(APPROVAL)
QUALITY_GATE_ROOT = VALIDATOR.QUALITY_GATE_ROOT
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"quality gate path contains a symlink component: {cursor}")


def write_quality_gate(
    manifest_path: Path,
    report_path: Path,
    approval_receipt_path: Path,
    weekly_report_path: Path,
    author: str,
    week: str,
    accepted_by: str,
    artifact_name: str,
    *,
    gate_root: Path | None = None,
    state_root: Path | None = None,
) -> dict[str, str]:
    _require(SAFE_NAME.fullmatch(artifact_name) is not None, "artifact name is invalid")
    _require(report_path.is_file() and not report_path.is_symlink(), "report must be a regular non-symlink file")
    document = VALIDATOR.read_private_manifest(
        manifest_path, state_root=state_root or VALIDATOR.STATE_ROOT
    )
    manifest_root = manifest_path.resolve().parent
    VALIDATOR.validate_manifest(document, artifact_root=manifest_root)
    _require(document.get("schema") == VALIDATOR.SCHEMA_V3, "quality gate receipt requires a v3 manifest")
    comparison = document.get("efficiency_eval", {})
    _require(comparison.get("mode") == "rerun", "only a rerun can create a quality gate receipt")
    _require(comparison.get("decision") == "accept", "quality gate rerun was not accepted")
    _require(bool(accepted_by.strip()), "accepted_by is required")
    expected_review = (
        manifest_root / comparison["quality_review"]["artifact_ref"]
    ).resolve()
    _require(
        report_path.resolve() == expected_review,
        "report must be the validated blind quality review artifact",
    )
    approval = APPROVAL.validate_receipt(
        approval_receipt_path,
        weekly_report_path,
        author,
        week,
    )
    _require(
        approval.get("round_id") == document.get("round_id"),
        "approval receipt round does not match the quality comparison",
    )
    _require(
        approval.get("approved_by") == accepted_by.strip(),
        "approval receipt approving user does not match accepted_by",
    )
    _require(
        approval.get("quality_review_digest")
        == comparison["quality_review"]["artifact_sha256"],
        "approval receipt quality review digest does not match",
    )

    baseline = comparison["baseline"]
    candidate = comparison["candidate"]
    quality_review = json.loads(report_path.read_text(encoding="utf-8"))

    for name, run in (("baseline", baseline), ("candidate", candidate)):
        report_candidate = manifest_root / run["report_ref"]
        _require(not report_candidate.is_symlink(), f"{name} report must not be a symlink")
        report_file = report_candidate.resolve()
        _require(
            report_file.parent == manifest_root or manifest_root in report_file.parents,
            f"{name} report is outside the manifest directory",
        )
        _require(report_file.is_file(), f"{name} report is missing")
        actual_digest = "sha256:" + hashlib.sha256(report_file.read_bytes()).hexdigest()
        _require(
            actual_digest == run["report_digest"],
            f"{name} report digest does not match",
        )

    metric = (
        "input_tokens"
        if baseline.get("input_tokens") is not None
        and candidate.get("input_tokens") is not None
        else "input_characters"
    )

    def comparison_run(run: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "report_digest": run["report_digest"],
            "input_digest": run["input_digest"],
            "input_characters": run["input_characters"],
            "quality_dimensions": run["quality_dimensions"],
            "required_claim_count": len(comparison["required_claim_ids"]),
            "unsupported_claim_count": len(run["unsupported_claim_ids"]),
            "privacy_violation_count": len(run["privacy_violations"]),
            "critical_gates": run["critical_gates"],
        }
        if run.get("input_tokens") is not None:
            result["input_tokens"] = run["input_tokens"]
            result["token_receipt_digest"] = run["token_receipt_digest"]
            token_receipt_path = (manifest_root / run["token_receipt_ref"]).resolve()
            token_receipt = json.loads(token_receipt_path.read_text(encoding="utf-8"))
            provider_response = token_receipt["provider_response"]
            result["token_evidence"] = {
                "source": token_receipt["source"],
                "provider": provider_response["provider"],
                "model": provider_response["model"],
                "request_id": provider_response["request_id"],
                "counter_name": token_receipt["counter_name"],
                "counter_version": token_receipt["counter_version"],
            }
        return result

    root = gate_root or QUALITY_GATE_ROOT
    _reject_symlink_components(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _reject_symlink_components(root)
    root_metadata = root.stat()
    _require(stat.S_ISDIR(root_metadata.st_mode), "quality gate root must be a directory")
    _require(root_metadata.st_uid == os.getuid(), "quality gate root must be owned by the current user")
    os.chmod(root, 0o700)
    target = root / artifact_name
    _require(not target.exists() and not target.is_symlink(), "quality gate artifact already exists")
    receipt: dict[str, Any] = {
        "schema": "addx.work_method_quality_gate.v1",
        "snapshot_id": comparison["snapshot_id"],
        "filter_hash": comparison["filter_hash"],
        "selection_policy_hash": comparison["selection_policy_hash"],
        "quality_policy_hash": comparison["quality_policy_hash"],
        "rubric_version": comparison["rubric_version"],
        "decision": "accept",
        "accepted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "accepted_by": accepted_by.strip(),
        "report_digest": "sha256:" + hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "approval_provenance": {
            "receipt_digest": "sha256:"
            + hashlib.sha256(approval_receipt_path.read_bytes()).hexdigest(),
            "round_id": approval["round_id"],
            "author": approval["author"],
            "week": approval["week"],
            "weekly_report_digest": approval["report_digest"],
            "approved_by": approval["approved_by"],
            "accepted_at": approval["accepted_at"],
        },
        "quality_comparison": {
            "method": comparison["comparison_protocol"]["method"],
            "metric": metric,
            "snapshot_digest": comparison["snapshot_digest"],
            "quality_review_digest": comparison["quality_review"][
                "artifact_sha256"
            ],
            "quality_review_ref": comparison["quality_review"]["artifact_ref"],
            "packet_digest": comparison["comparison_protocol"]["packet_digest"],
            "claim_ledger_digest": comparison["comparison_protocol"][
                "claim_ledger_digest"
            ],
            "label_map_digest": comparison["comparison_protocol"]["label_map_digest"],
            "baseline_label": comparison["baseline"]["blind_label"],
            "candidate_label": comparison["candidate"]["blind_label"],
            "required_claim_ids": sorted(comparison["required_claim_ids"]),
            "reviewer": quality_review["reviewer"],
            "baseline": comparison_run(baseline),
            "candidate": comparison_run(candidate),
        },
    }
    serialized = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    with tempfile.NamedTemporaryFile("wb", dir=root, delete=False) as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    return {
        "artifact_ref": artifact_name,
        "artifact_sha256": "sha256:" + hashlib.sha256(serialized).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--approval-receipt", required=True, type=Path)
    parser.add_argument("--weekly-report", required=True, type=Path)
    parser.add_argument("--author", required=True)
    parser.add_argument("--week", required=True)
    parser.add_argument("--accepted-by", required=True)
    parser.add_argument("--artifact-name", required=True)
    args = parser.parse_args()
    try:
        result = write_quality_gate(
            args.manifest,
            args.report,
            args.approval_receipt,
            args.weekly_report,
            args.author,
            args.week,
            args.accepted_by,
            args.artifact_name,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        VALIDATOR.ContractError,
        APPROVAL.ApprovalError,
    ) as error:
        print(json.dumps({"status": "invalid", "error": str(error)}))
        return 1
    print(json.dumps({"status": "written", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
