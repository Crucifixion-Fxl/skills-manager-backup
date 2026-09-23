#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""比较 staging 已验证变更与生产晋级候选变更。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from release_parity_assertions import (
    check_content,
    coverage_failures,
    gate_declarations,
)
from release_parity_contract import (
    allow_rules,
    load_contract,
    parse_content_checks,
)
from release_parity_core import (
    InputError,
    compare,
    require_ancestor,
)
from release_parity_gitlab import load_gitlab_provenance


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Compare exact verified and promoted changes."
    )
    result.add_argument("--project-path", required=True)
    result.add_argument("--canonical-verification-mr", required=True, type=int)
    result.add_argument("--candidate-mr", required=True, type=int)
    result.add_argument("--staging-branch", default="staging")
    result.add_argument("--contract")
    result.add_argument("--json", action="store_true", dest="as_json")
    return result


def release_status(code_status: str, requires_live_gate_audit: bool) -> str:
    if code_status == "fail":
        return "fail"
    if requires_live_gate_audit:
        return "live-audit-required"
    return "pass"


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        refs, provenance = load_gitlab_provenance(
            args.project_path,
            args.canonical_verification_mr,
            args.candidate_mr,
            args.staging_branch,
        )
        require_ancestor(refs["canonical_base"], refs["canonical"], "canonical")
        require_ancestor(refs["candidate_base"], refs["candidate"], "candidate")
        contract, contract_metadata = load_contract(args.contract, refs["candidate"])
        rules = allow_rules(contract)
        matched, blocked, allowed, failures = compare(
            refs["canonical_base"],
            refs["canonical"],
            refs["candidate_base"],
            refs["candidate"],
            rules,
            {args.contract} if args.contract else set(),
        )
        failures += coverage_failures(allowed, rules, refs["candidate"])
        checks = parse_content_checks(
            contract.get("required_content"),
            "required_content",
            required=False,
        )
        checks.extend(check for rule in rules for check in rule["checks"])
        failures += check_content(checks, refs)
        external_gates = gate_declarations(contract)
    except InputError as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2
    code_status = "pass" if not blocked and not failures else "fail"
    requires_live_gate_audit = any(gate["required"] for gate in external_gates)
    report = {
        "status": release_status(code_status, requires_live_gate_audit),
        "code_status": code_status,
        "refs": refs,
        "provenance": provenance,
        "matched_paths": matched,
        "allowed_differences": allowed,
        "blocking_differences": blocked,
        "contract_failures": failures,
        "contract": contract_metadata,
        "external_gates": external_gates,
        "requires_live_gate_audit": requires_live_gate_audit,
    }
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"release code parity: {code_status.upper()}")
        print(f"release readiness: {report['status'].upper()}")
        print(f"matched paths: {len(matched)}")
        print(f"allowed differences: {len(allowed)}")
        print(f"blocking differences: {len(blocked)}")
        for item in blocked:
            print(f"  BLOCK {item['kind']}: {item['path']}")
        for failure in failures:
            print(f"  CONTRACT {failure}")
        if contract_metadata:
            print(
                "contract: "
                f"{contract_metadata['path']} "
                f"oid={contract_metadata['oid']} "
                f"sha256={contract_metadata['sha256']}"
            )
        for gate in external_gates:
            if gate["required"]:
                print(
                    f"  LIVE VERIFY external gate {gate['name']!r}: {gate['evidence']}"
                )
    return 0 if code_status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
