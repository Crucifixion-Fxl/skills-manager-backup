"""Tests for main() — exit codes + error wrapping."""

import json
import sys
from unittest.mock import patch

import pytest

from scripts.aws_sp_optimizer import main


def test_ok_exit_0(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aws-sp-optimizer", "--org", "a4x-us"])
    with patch("scripts.aws_sp_optimizer.run_optimizer") as mock_run:
        mock_run.return_value = {
            "schema_version": 1,
            "status": "ok",
            "context": {},
            "baseline": {},
            "recommendation": {
                "delta_per_hour_to_buy": 5.0,
                "annual_delta_usd": round(5.0 * 8760, 2),
                "must_review_before_acting": False,
            },
            "search_summary": {},
            "purchase_instructions": {
                "step_2_create_savings_plan": {
                    "command_template": "aws ... --commitment '5.0000' ..."
                }
            },
            "risks": [],
            "warnings": [],
        }
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"


def test_config_error_exit_2(capsys, monkeypatch):
    from scripts._common import ConfigError

    monkeypatch.setattr(sys, "argv", ["aws-sp-optimizer", "--org", "nope"])
    with patch("scripts.aws_sp_optimizer.run_optimizer") as mock_run:
        mock_run.side_effect = ConfigError(code="config_missing", message="no file")
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
    output = json.loads(capsys.readouterr().out)
    # L8 regression: needs_setup path must survive validate_output_invariants
    # (INV-8 requires non-empty instruction_for_llm). Without the
    # to_output_dict synthesis the invariant would fire and downgrade
    # status to script_bug, exit code to 1.
    assert output["status"] == "needs_setup"
    assert output["error_code"] == "config_missing"
    assert output.get("instruction_for_llm")  # non-empty


def test_validation_blocker_error_exit_2(capsys, monkeypatch):
    """L9 regression: ValidationBlockerError → needs_validation_fix must
    survive validate_output_invariants (INV-11 requires non-empty blockers[]
    AND instruction_for_llm). Before the to_output_dict override on
    ValidationBlockerError, the inherited base payload had neither."""
    from scripts._common import Blocker, ValidationBlockerError, ValidationContext

    blockers = [
        Blocker(
            code="cur_table_not_found",
            message="table missing",
            llm_next_action="READ troubleshooting.md",
        ),
    ]
    ctx = ValidationContext(
        caller_account_id="111111111111",
        org_id="o-abc",
        master_account_email="root@example.com",
        member_account_ids=["111111111111"],
        member_accounts_count=1,
        primary_region="us-east-1",
        cur_table_has_resource_tags_user_service=None,
    )
    monkeypatch.setattr(sys, "argv", ["aws-sp-optimizer", "--org", "a4x-us"])
    with patch("scripts.aws_sp_optimizer.run_optimizer") as mock_run:
        mock_run.side_effect = ValidationBlockerError(
            blockers=blockers, validation_context=ctx, warnings=[]
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "needs_validation_fix"
    assert output["blockers"]  # non-empty
    assert output["blockers"][0]["code"] == "cur_table_not_found"
    assert output.get("instruction_for_llm")  # non-empty
    assert "validation_context" in output


def test_uncaught_exception_exit_1_script_bug(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aws-sp-optimizer", "--org", "a4x-us"])
    with patch("scripts.aws_sp_optimizer.run_optimizer") as mock_run:
        mock_run.side_effect = RuntimeError("boom")
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "script_bug"
    assert output["error_type"] == "RuntimeError"


def test_invariant_violation_falls_back_to_script_bug(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aws-sp-optimizer", "--org", "a4x-us"])
    with patch("scripts.aws_sp_optimizer.run_optimizer") as mock_run:
        # Return a broken output that trips INV-1
        mock_run.return_value = {"schema_version": 99, "status": "ok"}
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "script_bug"
    assert output["error_type"] == "InvariantViolation"


def test_needs_cache_refresh_exit_2(capsys, monkeypatch):
    """Regression guard for MR !290 post-review P1: needs_cache_refresh must
    land in exit-code 2 (needs user action), not fall through to exit 1
    (script_bug). Previously `needs_cache_refresh` wasn't in the exit=2 set
    so shell callers treated a stale-cache status as an internal crash.
    """
    monkeypatch.setattr(sys, "argv", ["aws-sp-optimizer", "--org", "a4x-us"])
    with patch("scripts.aws_sp_optimizer.run_optimizer") as mock_run:
        mock_run.return_value = {
            "schema_version": 1,
            "status": "needs_cache_refresh",
            "context": {},
            "actions": [{"key": "pull_from_git", "label": "...", "command": "..."}],
            "reason": "pricing_cache_stale",
        }
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "needs_cache_refresh"
