#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml>=6.0"]
# ///
"""Validate PROPOSE artifacts with a trusted standards Git snapshot."""

# Library module; gate.py provides argparse --help and json.dumps structured output.

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from common import (
    load_yaml,
    materialize_git_commit,
    trusted_validator_env,
    verify_standards_checkout,
)
from init_archive import proposal_identity_placeholders


def proposal_gate(
    standards_root: Path, proposal_path: Path
) -> tuple[list[str], dict[str, Any]]:
    verification, errors = verify_standards_checkout(
        standards_root, Path(__file__).with_name("verify_standards.py")
    )
    details: dict[str, Any] = {"standards_verification": verification}
    if errors:
        details["official_validator_executed"] = False
        return errors, details
    verified_sha = str(verification["local_head"])
    try:
        with materialize_git_commit(standards_root, verified_sha) as snapshot:
            proposal = load_yaml(proposal_path)
            placeholders = proposal_identity_placeholders(proposal)
            if placeholders:
                errors.append(
                    f"PROPOSE rejects standards template placeholders: {', '.join(placeholders)}"
                )
            if proposal_path.read_bytes() == (
                snapshot / "templates/workspace-proposal.yaml"
            ).read_bytes():
                errors.append("PROPOSE rejects the unchanged standards proposal template")
            result = subprocess.run(
                [str(snapshot / "scripts/governance-validate"), "--proposal", str(proposal_path)],
                check=False,
                capture_output=True,
                text=True,
                env=trusted_validator_env(),
            )
    except (OSError, ValueError) as exc:
        errors.append(f"cannot validate proposal from standards snapshot: {exc}")
        details["official_validator_executed"] = False
        return errors, details
    if result.returncode:
        errors.append("official standards governance validator failed")
    details.update(
        {
            "official_validator_executed": True,
            "official_validator_source_sha": verified_sha,
            "official_validator_exit": result.returncode,
            "official_validator_stdout": result.stdout.strip(),
            "official_validator_stderr": result.stderr.strip(),
        }
    )
    return errors, details
