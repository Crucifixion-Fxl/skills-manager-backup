"""Tests for validate_environment Check 1 — STS caller + account match."""

from unittest.mock import patch

from botocore.exceptions import ClientError, NoCredentialsError

from scripts._common import CallerIdentity, OrgConfig
from scripts.validate_environment import validate_environment


def _base_config(payer_id="111111111111") -> OrgConfig:
    return OrgConfig(
        alias="test",
        description=None,
        profile="prof",
        payer_account_id=payer_id,
        org_id="o-abc",
        cur_database="d",
        cur_table="t",
        athena_output="s3://b/p/",
        athena_workgroup="primary",
        primary_region="us-east-1",
        window_days=90,
        window_end="today",
        prefer="balanced",
    )


def test_no_credentials_returns_blocker():
    with patch("scripts.validate_environment.sts_get_caller_identity") as mock_sts:
        mock_sts.side_effect = NoCredentialsError()
        result = validate_environment(_base_config())
    assert len(result.blockers) == 1
    assert result.blockers[0].code == "profile_auth_failed"
    assert result.context is None


def test_client_error_on_sts_returns_aws_error_blocker():
    with patch("scripts.validate_environment.sts_get_caller_identity") as mock_sts:
        mock_sts.side_effect = ClientError(
            {"Error": {"Code": "InvalidClientTokenId"}}, "GetCallerIdentity"
        )
        result = validate_environment(_base_config())
    assert len(result.blockers) == 1
    assert result.blockers[0].code == "unspecified_aws_error"


def test_account_id_mismatch_returns_blocker():
    caller = CallerIdentity(account_id="222222222222", arn="arn", user_id="u")
    with patch("scripts.validate_environment.sts_get_caller_identity", return_value=caller):
        result = validate_environment(_base_config(payer_id="111111111111"))
    assert len(result.blockers) == 1
    assert result.blockers[0].code == "account_id_mismatch"
    assert result.blockers[0].context["actual_account"] == "222222222222"
    assert result.blockers[0].context["expected_account"] == "111111111111"
