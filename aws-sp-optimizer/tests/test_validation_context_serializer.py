"""Tests for validation_context_to_public_dict + ValidationBlockerError payload."""

from scripts._common import (
    Blocker,
    ValidationBlockerError,
    ValidationContext,
    validation_context_to_public_dict,
)


def test_none_in_none_out():
    assert validation_context_to_public_dict(None) is None


def test_omits_member_account_ids_list():
    ctx = ValidationContext(
        caller_account_id="111111111111",
        org_id="o-abc",
        master_account_email="root@example.com",
        member_account_ids=["111111111111", "222222222222", "333333333333"],
        member_accounts_count=3,
        primary_region="us-east-1",
        cur_table_has_resource_tags_user_service=True,
    )
    d = validation_context_to_public_dict(ctx)
    assert d is not None
    assert "member_account_ids" not in d
    assert d["member_accounts_count"] == 3
    assert d["caller_account_id"] == "111111111111"
    assert d["org_id"] == "o-abc"
    assert d["master_account_email"] == "root@example.com"
    assert d["primary_region"] == "us-east-1"
    assert d["cur_table_has_resource_tags_user_service"] is True


def test_validation_blocker_error_output_matches_inv11():
    """L9 regression: ValidationBlockerError.to_output_dict() must emit the
    NeedsValidationFixOutput shape with non-empty blockers[] and
    instruction_for_llm so INV-11 passes without falling back to script_bug."""
    blockers = [
        Blocker(
            code="profile_auth_failed",
            message="SSO expired",
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
    e = ValidationBlockerError(blockers=blockers, validation_context=ctx, warnings=[])
    payload = e.to_output_dict()
    assert payload["status"] == "needs_validation_fix"
    assert payload["blockers"]  # non-empty for INV-11
    assert payload["blockers"][0]["code"] == "profile_auth_failed"
    assert payload["validation_context"] is not None
    assert "member_account_ids" not in payload["validation_context"]
    assert payload["instruction_for_llm"]  # non-empty for INV-11
    assert "1 validation blocker" in payload["instruction_for_llm"]


def test_validation_blocker_error_with_none_context():
    """Check 1/2 failures leave validation_context=None. to_output_dict must
    still produce a valid payload (validation_context field = null)."""
    blockers = [Blocker(code="profile_auth_failed", message="no creds")]
    e = ValidationBlockerError(blockers=blockers, validation_context=None, warnings=[])
    payload = e.to_output_dict()
    assert payload["status"] == "needs_validation_fix"
    assert payload["validation_context"] is None
    assert payload["blockers"]
    assert payload["instruction_for_llm"]
