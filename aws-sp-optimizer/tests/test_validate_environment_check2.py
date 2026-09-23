"""Tests for validate_environment Check 2 — Organization membership."""

from unittest.mock import patch

from botocore.exceptions import ClientError

from scripts._common import CallerIdentity, OrgAccount, Organization, OrgConfig
from scripts.validate_environment import validate_environment


def _base_config() -> OrgConfig:
    return OrgConfig(
        alias="test",
        description=None,
        profile="prof",
        payer_account_id="111111111111",
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


def _good_caller():
    return CallerIdentity(account_id="111111111111", arn="arn", user_id="u")


def test_not_in_organization():
    with (
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=_good_caller()),
        patch("scripts.validate_environment.organizations_describe_organization") as mock_org,
    ):
        mock_org.side_effect = ClientError(
            {"Error": {"Code": "AWSOrganizationsNotInUseException"}},
            "DescribeOrganization",
        )
        result = validate_environment(_base_config())
    assert len(result.blockers) == 1
    assert result.blockers[0].code == "not_in_organization"


def test_org_id_mismatch():
    bad_org = Organization(
        id="o-different", master_account_id="111111111111", master_account_email="root@example.com"
    )
    with (
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=_good_caller()),
        patch(
            "scripts.validate_environment.organizations_describe_organization", return_value=bad_org
        ),
    ):
        result = validate_environment(_base_config())
    assert len(result.blockers) == 1
    assert result.blockers[0].code == "org_id_mismatch"


def test_not_payer():
    org = Organization(
        id="o-abc", master_account_id="999999999999", master_account_email="root@example.com"
    )
    with (
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=_good_caller()),
        patch("scripts.validate_environment.organizations_describe_organization", return_value=org),
    ):
        result = validate_environment(_base_config())
    assert len(result.blockers) == 1
    assert result.blockers[0].code == "not_payer"


def test_context_populated_after_check2():
    """After Check 2 passes, ValidationContext has core fields."""
    org = Organization(
        id="o-abc", master_account_id="111111111111", master_account_email="root@example.com"
    )
    accounts = [
        OrgAccount(id="111111111111", name="master", status="ACTIVE"),
        OrgAccount(id="222222222222", name="member-1", status="ACTIVE"),
        OrgAccount(id="333333333333", name="old", status="SUSPENDED"),
    ]
    with (
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=_good_caller()),
        patch("scripts.validate_environment.organizations_describe_organization", return_value=org),
        patch("scripts.validate_environment.organizations_list_accounts", return_value=accounts),
        patch("scripts.validate_environment.glue_get_table") as mock_glue,
    ):
        # Make glue fail fast so we don't proceed past check 3
        mock_glue.side_effect = ClientError(
            {"Error": {"Code": "EntityNotFoundException"}}, "GetTable"
        )
        result = validate_environment(_base_config())

    assert result.context is not None
    assert result.context.caller_account_id == "111111111111"
    assert result.context.org_id == "o-abc"
    assert result.context.member_accounts_count == 2  # SUSPENDED excluded
    assert set(result.context.member_account_ids) == {"111111111111", "222222222222"}
    assert result.context.primary_region == "us-east-1"
