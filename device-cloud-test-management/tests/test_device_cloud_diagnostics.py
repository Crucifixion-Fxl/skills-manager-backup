"""Regression tests for structured Device Cloud diagnostic conclusions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "device_cloud_diagnostics.py"
sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location("device_cloud_diagnostics_cli", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
DIAGNOSTICS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DIAGNOSTICS
SPEC.loader.exec_module(DIAGNOSTICS)


class SuccessfulClient:
    def job_report(self, job_id: int) -> dict:
        return {
            "job": {
                "id": job_id,
                "testPlanId": 1094,
                "status": "COMPLETE",
                "resources": [{"kind": "PHONE"}],
            },
            "features": [],
        }


def test_successful_job_has_no_failure_reason_or_failure_classification(
    tmp_path: Path,
) -> None:
    result = DIAGNOSTICS.full_diagnosis(SuccessfulClient(), 1028, tmp_path)

    assert result["terminal"]["finalStatus"] == "COMPLETE"
    assert result["terminal"]["firstFailureReason"] is None
    assert result["terminal"]["failureClassification"] == "无失败"


def test_completed_job_with_real_failure_is_still_classified() -> None:
    classification = DIAGNOSTICS._failure_classification(
        "expected live button but element not found",
        "COMPLETE",
        [{"kind": "PHONE"}],
    )

    assert classification == "元素/导航不兼容"
