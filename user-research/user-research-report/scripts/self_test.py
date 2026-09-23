#!/usr/bin/env python3
"""Offline behavioral checks for report-package validation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run_validator(validator: Path, main: Path, evidence: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(validator),
            "--main",
            str(main),
            "--evidence",
            str(evidence),
            "--manifest",
            str(manifest),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    validator = Path(__file__).with_name("validate_report_package.py")
    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="user-research-report-") as raw_dir:
        root = Path(raw_dir)
        main_report = root / "main-report.md"
        evidence = root / "evidence.md"
        manifest = root / "manifest.json"

        main_report.write_text(
            "# Main report v1\n\n[Evidence](evidence.md)\n\nGOAL-1\n\nN=42\n",
            encoding="utf-8",
        )
        evidence.write_text(
            "# Evidence v1\n\n[Main](main-report.md)\n\nEVIDENCE-1\n\nN=42\n",
            encoding="utf-8",
        )
        manifest.write_text(
            json.dumps(
                {
                    "version": "v1",
                    "research_goals": [
                        {
                            "id": "goal-1",
                            "main_marker": "GOAL-1",
                            "evidence_marker": "EVIDENCE-1",
                        }
                    ],
                    "shared_facts": [{"id": "sample", "value": "N=42"}],
                    "required_files": ["main-report.md", "evidence.md"],
                }
            ),
            encoding="utf-8",
        )

        valid = run_validator(validator, main_report, evidence, manifest)
        if valid.returncode != 0:
            raise AssertionError(f"valid package failed: {valid.stdout}{valid.stderr}")
        checks.append("valid package accepted")

        evidence.write_text(
            "# Evidence v2\n\n[Main](main-report.md)\n\nEVIDENCE-1\n\nN=42\n",
            encoding="utf-8",
        )
        mismatch = run_validator(validator, main_report, evidence, manifest)
        if mismatch.returncode == 0 or "version mismatch" not in mismatch.stdout:
            raise AssertionError("version mismatch was not rejected")
        checks.append("version mismatch rejected")

    print(json.dumps({"status": "pass", "checks": checks}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
