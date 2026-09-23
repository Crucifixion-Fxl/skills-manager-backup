#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml>=6.0"]
# ///
"""CLI for fail-closed INIT, VALIDATE, and ARCHIVE workspace gates."""

# Structured output is emitted with json.dumps by common.emit.

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from archive_gate import archive_gate
from archive_gate import archive_requirements as archive_requirements
from common import emit
from init_archive import init_gate
from init_archive import init_requirements as init_requirements
from proposal_gate import proposal_gate
from validation_gate import validate_evidence as validate_evidence
from validation_gate import validate_gate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run fail-closed domain workspace lifecycle gates.")
    sub = parser.add_subparsers(dest="mode", required=True)
    init = sub.add_parser("init", help="Check proposal and official governance validator before INIT.")
    init.add_argument("--standards-root", required=True, type=Path)
    init.add_argument("--proposal", required=True, type=Path)
    propose = sub.add_parser("propose", help="Validate a proposal from the trusted standards snapshot.")
    propose.add_argument("--standards-root", required=True, type=Path)
    propose.add_argument("--proposal", required=True, type=Path)
    validate = sub.add_parser("validate", help="Check actual workspace and separated evidence layers.")
    validate.add_argument("--standards-root", required=True, type=Path)
    validate.add_argument("--workspace-root", required=True, type=Path)
    validate.add_argument("--evidence", required=True, type=Path)
    validate.add_argument("--require-activation", action="store_true")
    archive = sub.add_parser("archive", help="Check deprecated-to-archived evidence.")
    archive.add_argument("--standards-root", required=True, type=Path)
    archive.add_argument("--workspace-root", required=True, type=Path)
    archive.add_argument("--evidence", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "init":
        errors, details = init_gate(args.standards_root.resolve(), args.proposal.resolve())
        payload = {"mode": "INIT", "authorized": not errors, "errors": errors, "details": details}
    elif args.mode == "propose":
        errors, details = proposal_gate(args.standards_root.resolve(), args.proposal.resolve())
        payload = {
            "mode": "PROPOSE",
            "authorized": False,
            "creation_authorized": False,
            "proposal_valid": not errors,
            "errors": errors,
            "details": details,
        }
    elif args.mode == "validate":
        errors, details = validate_gate(
            args.standards_root.resolve(),
            args.workspace_root.resolve(),
            args.evidence.resolve(),
            args.require_activation,
        )
        payload = {
            "mode": "VALIDATE",
            "authorized": False,
            "activation_authorized": False,
            "evidence_package_valid": not errors,
            "activation_candidate_ready": bool(args.require_activation and not errors),
            "errors": errors,
            "details": details,
        }
    else:
        errors, details = archive_gate(
            args.standards_root.resolve(),
            args.workspace_root.resolve(),
            args.evidence.resolve(),
        )
        payload = {
            "mode": "ARCHIVE",
            "authorized": False,
            "archive_authorized": False,
            "archive_phase": details.get("phase"),
            "archive_candidate_ready": bool(
                details.get("phase") == "preflight" and not errors
            ),
            "archive_transition_verified": bool(
                details.get("phase") == "post-transition" and not errors
            ),
            "errors": errors,
            "details": details,
        }
    emit(payload, 1 if errors else 0)


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as exc:
        mode = sys.argv[1].upper() if len(sys.argv) > 1 else "UNKNOWN"
        payload = {"mode": mode, "authorized": False, "errors": [str(exc)]}
        if mode == "ARCHIVE":
            payload.update(
                {
                    "archive_authorized": False,
                    "archive_candidate_ready": False,
                    "archive_transition_verified": False,
                }
            )
        emit(payload, 1)
