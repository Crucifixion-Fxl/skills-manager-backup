import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

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
    workspace: Path, payload: dict[str, object], session: str = "ses_root"
) -> subprocess.CompletedProcess[str]:
    binding = workspace / "root-binding.json"
    if not binding.exists():
        binding.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "execution_id": "ci-123-abcdef",
                    "root_session_id": "ses_root",
                    "state": "bound",
                }
            )
        )
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
            "CODE_REVIEW_ROOT_BINDING_PATH": str(binding),
            "CODE_REVIEW_CALLER_SESSION_ID": session,
        },
    )


def test_child_cannot_replace_valid_root_result(tmp_path: Path) -> None:
    assert _submit(tmp_path, _valid_result()).returncode == 0
    before = (tmp_path / "review-result.json").read_bytes()
    child = _valid_result()
    child["scope"] = "partial child"
    assert _submit(tmp_path, child, "ses_child").returncode == 2
    assert (tmp_path / "review-result.json").read_bytes() == before


def test_unknown_caller_cannot_submit(tmp_path: Path) -> None:
    assert _submit(tmp_path, _valid_result(), "").returncode == 2
    assert not (tmp_path / "review-result.json").exists()


def test_submission_validates_and_writes_matching_fixed_result_and_receipt(
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
    assert receipt == {
        "schema_version": "1.1",
        "execution_id": "ci-123-abcdef",
        "root_session_id": "ses_root",
        "submitting_session_id": "ses_root",
        "sha256": hashlib.sha256(
            (tmp_path / "review-result.json").read_bytes()
        ).hexdigest(),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "invalid"),
        ("schema_version", "1.0"),
        ("root_session_id", "ses_child"),
        ("submitting_session_id", "ses_child"),
        ("execution_id", "old-execution"),
        ("sha256", "0" * 64),
    ],
)
def test_mismatched_receipt_is_revalidated_not_accepted_as_idempotent(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    assert _submit(tmp_path, _valid_result()).returncode == 0
    receipt_path = tmp_path / "review-result.receipt.json"
    expected = json.loads(receipt_path.read_text())
    damaged = {**expected, field: value}
    receipt_path.write_text(json.dumps(damaged))

    result = _submit(tmp_path, _valid_result())

    assert result.returncode == 0, result.stderr
    assert "idempotent=false" in result.stdout
    assert json.loads(receipt_path.read_text()) == expected


@pytest.mark.parametrize(
    "missing", ["review-result.json", "review-result.receipt.json"]
)
def test_partial_pair_is_recoverable_by_valid_root_submission(
    tmp_path: Path, missing: str
) -> None:
    assert _submit(tmp_path, _valid_result()).returncode == 0
    expected = {
        name: (tmp_path / name).read_bytes()
        for name in ["review-result.json", "review-result.receipt.json"]
    }
    (tmp_path / missing).unlink()
    result = _submit(tmp_path, _valid_result())
    assert result.returncode == 0, result.stderr
    assert "idempotent=false" in result.stdout
    assert all(
        (tmp_path / name).read_bytes() == content for name, content in expected.items()
    )


def test_invalid_revision_preserves_existing_valid_result_and_receipt(
    tmp_path: Path,
) -> None:
    assert _submit(tmp_path, _valid_result()).returncode == 0
    paths = [tmp_path / "review-result.json", tmp_path / "review-result.receipt.json"]
    before = [p.read_bytes() for p in paths]
    invalid = {**_valid_result(), "should_pass": False}
    assert _submit(tmp_path, invalid).returncode == 2
    assert [p.read_bytes() for p in paths] == before


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
