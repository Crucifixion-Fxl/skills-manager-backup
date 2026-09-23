import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("submit_review_result.js")


def _valid_result() -> dict[str, object]:
    return {
        "schema_version": "1.1",
        "conclusion": "通过",
        "score": "8.5/10",
        "should_pass": True,
        "scope": "审查范围。",
        "documentation": "文档检查。",
        "quality": "质量检查。",
        "tdd_assessment": {
            "level": "N/A",
            "applicability": "not_applicable",
            "summary": "测试渲染器自身的提交协议，不评估业务行为 TDD。",
            "evidence": ["MR diff only changes review-result protocol tests"],
            "gaps": [],
        },
        "e2e": "端到端检查。",
        "red_lines": [],
        "suggestions": [],
    }


def _submit(
    workspace: Path, payload: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "CODE_REVIEW_WORKSPACE": str(workspace),
            "CODE_REVIEW_EXECUTION_ID": "ci-123-abcdef",
        },
    )


def test_submission_validates_and_atomically_writes_fixed_result_and_receipt(
    tmp_path: Path,
) -> None:
    result = _submit(tmp_path, _valid_result())

    assert result.returncode == 0, result.stderr
    assert "review_result_submitted=true" in result.stdout
    written = json.loads((tmp_path / "review-result.json").read_text(encoding="utf-8"))
    receipt = json.loads(
        (tmp_path / "review-result.receipt.json").read_text(encoding="utf-8")
    )
    assert written == _valid_result()
    assert receipt["execution_id"] == "ci-123-abcdef"
    assert len(receipt["sha256"]) == 64


def test_submission_is_idempotent_and_keeps_the_latest_valid_revision(
    tmp_path: Path,
) -> None:
    assert _submit(tmp_path, _valid_result()).returncode == 0
    repeated = _submit(tmp_path, _valid_result())
    changed = _valid_result()
    changed["scope"] = "不同结果。"

    assert repeated.returncode == 0
    assert "idempotent=true" in repeated.stdout
    revised = _submit(tmp_path, changed)
    assert revised.returncode == 0, revised.stderr
    assert "idempotent=false" in revised.stdout
    assert (
        json.loads((tmp_path / "review-result.json").read_text(encoding="utf-8"))[
            "scope"
        ]
        == "不同结果。"
    )


def test_submission_rejects_invalid_schema_without_publishing_files(
    tmp_path: Path,
) -> None:
    payload = _valid_result()
    payload["should_pass"] = False

    result = _submit(tmp_path, payload)

    assert result.returncode == 2
    assert "conclusion and should_pass are inconsistent" in result.stderr
    assert not (tmp_path / "review-result.json").exists()
    assert not (tmp_path / "review-result.receipt.json").exists()


def test_submission_accepts_schema_1_1_without_tdd_assessment_as_non_gating_signal_gap(
    tmp_path: Path,
) -> None:
    payload = _valid_result()
    del payload["tdd_assessment"]

    result = _submit(tmp_path, payload)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "review-result.json").exists()
    assert (tmp_path / "review-result.receipt.json").exists()
