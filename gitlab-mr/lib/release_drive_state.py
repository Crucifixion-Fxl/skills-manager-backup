"""组装 gitlab-mr Driver 的生产晋级状态。"""

from __future__ import annotations

from typing import Any


def promotion_state(
    args: Any,
    staging_flow_exists: bool | None,
    parity_report: dict[str, Any] | None,
    parity_report_sha256: str,
) -> dict[str, Any]:
    refs = parity_report["refs"] if parity_report else {}
    provenance = parity_report["provenance"] if parity_report else {}
    candidate = provenance.get("candidate_mr", {})
    canonical = provenance.get("canonical_verification_mr", {})
    flow_evidence: Any = args.staging_flow_evidence
    external_gates: list[dict[str, Any]] = []
    contract: Any = None
    if parity_report:
        flow_evidence = {
            "canonical_origin_mr": provenance.get("canonical_origin_mr", {}),
            "canonical_verification_mr": canonical,
        }
        external_gates = parity_report.get("external_gates", [])
        contract = parity_report.get("contract")
    candidate_target_ref = ""
    candidate_source_ref = ""
    if candidate:
        candidate_target_ref = f"origin/{candidate.get('target_branch', '')}"
        candidate_source_ref = f"refs/release-parity/mr-{candidate.get('iid', '')}"
    return {
        "enabled": args.mr_mode == "production-promotion",
        "staging_flow_exists": staging_flow_exists,
        "staging_branch": args.staging_branch,
        "staging_flow_evidence": flow_evidence,
        "canonical_branch": canonical.get("source_branch", ""),
        "canonical_verified_sha": refs.get("canonical", ""),
        "canonical_base_sha": refs.get("canonical_base", ""),
        "candidate_target_ref": candidate_target_ref,
        "candidate_target_sha": refs.get("candidate_base", ""),
        "candidate_source_ref": candidate_source_ref,
        "candidate_mr_sha": refs.get("candidate", ""),
        "contract": contract or {},
        "parity_report_sha256": parity_report_sha256,
        "external_gates": external_gates,
        "not_applicable_reason": args.not_applicable_reason,
    }


def cleanup_state(
    cleanup_report: dict[str, Any] | None,
    cleanup_report_sha256: str,
) -> dict[str, Any]:
    if cleanup_report is None:
        return {"enabled": False}
    return {
        "enabled": True,
        **cleanup_report,
        "attestation_sha256": cleanup_report_sha256,
    }
