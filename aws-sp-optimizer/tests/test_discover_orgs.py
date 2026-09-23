"""Tests for discover_orgs probing using moto mocks."""

from unittest.mock import patch

from scripts.discover_orgs import discover_all, probe_profile


def test_probe_profile_auth_failure_returns_cant_use():
    """When sts_get_caller_identity raises NoCredentialsError, we return
    can_use=False with reason='auth_failed' instead of propagating."""
    from botocore.exceptions import NoCredentialsError

    with patch("scripts.discover_orgs.sts_get_caller_identity") as mock_sts:
        mock_sts.side_effect = NoCredentialsError()
        entry = probe_profile("broken-profile")

    assert entry["profile"] == "broken-profile"
    assert entry["can_use"] is False
    assert entry["reason"] == "auth_failed"


def test_probe_profile_not_in_organization_returns_cant_use():
    from botocore.exceptions import ClientError

    from scripts._common import CallerIdentity

    caller = CallerIdentity(account_id="111111111111", arn="arn:...", user_id="AID")

    with (
        patch("scripts.discover_orgs.sts_get_caller_identity", return_value=caller),
        patch("scripts.discover_orgs.organizations_describe_organization") as mock_org,
    ):
        mock_org.side_effect = ClientError(
            {"Error": {"Code": "AWSOrganizationsNotInUseException"}},
            "DescribeOrganization",
        )
        entry = probe_profile("standalone")

    assert entry["can_use"] is False
    assert entry["reason"] == "not_in_organization"
    assert entry["account_id"] == "111111111111"


def test_probe_profile_not_payer_returns_cant_use():
    from scripts._common import CallerIdentity, Organization

    caller = CallerIdentity(account_id="222222222222", arn="arn", user_id="AID")
    org = Organization(
        id="o-abc",
        master_account_id="111111111111",  # different from caller
        master_account_email="root@example.com",
    )
    with (
        patch("scripts.discover_orgs.sts_get_caller_identity", return_value=caller),
        patch("scripts.discover_orgs.organizations_describe_organization", return_value=org),
    ):
        entry = probe_profile("member-profile")

    assert entry["can_use"] is False
    assert entry["reason"] == "not_payer"


def test_discover_all_aggregates_profiles():
    with (
        patch("scripts.discover_orgs.list_profiles_from_aws_cli_config", return_value=["a", "b"]),
        patch("scripts.discover_orgs.probe_profile") as mock_probe,
    ):
        mock_probe.side_effect = [
            {"profile": "a", "can_use": True, "account_id": "111111111111"},
            {"profile": "b", "can_use": False, "reason": "auth_failed"},
        ]
        result = discover_all()

    assert result["profiles_checked"] == 2
    assert result["profiles_with_errors"] == []
    assert len(result["discovered_orgs"]) == 2


def test_discover_all_catches_probe_exceptions():
    with (
        patch("scripts.discover_orgs.list_profiles_from_aws_cli_config", return_value=["a"]),
        patch("scripts.discover_orgs.probe_profile") as mock_probe,
    ):
        mock_probe.side_effect = RuntimeError("boom")
        result = discover_all()

    assert result["profiles_checked"] == 1
    assert len(result["profiles_with_errors"]) == 1
    assert result["profiles_with_errors"][0]["profile"] == "a"
    assert result["profiles_with_errors"][0]["error_type"] == "RuntimeError"
    assert result["discovered_orgs"] == []
