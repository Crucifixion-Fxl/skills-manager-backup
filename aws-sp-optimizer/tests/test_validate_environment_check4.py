"""Tests for Check 4 — CUR Org coverage."""

from unittest.mock import patch

from scripts._common import CallerIdentity, Column, OrgAccount, Organization, OrgConfig, TableSchema
from scripts.validate_environment import validate_environment

REQUIRED_COLUMNS = {
    "line_item_usage_start_date",
    "line_item_line_item_type",
    "line_item_product_code",
    "line_item_usage_type",
    "line_item_operation",
    "line_item_usage_account_id",
    "line_item_usage_amount",
    "line_item_resource_id",
    "pricing_public_on_demand_cost",
    "pricing_term",
    "product_instance_type",
    "product_region_code",
}


def _cfg():
    return OrgConfig(
        alias="t",
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


def _full_table():
    cols = [Column(c, "string") for c in REQUIRED_COLUMNS]
    return TableSchema(name="t", database="d", columns=cols)


def _stubs_through_check3(caller=None, accounts=None):
    caller = caller or CallerIdentity("111111111111", "arn", "u")
    org = Organization("o-abc", "111111111111", "root@example.com")
    accounts = accounts or [
        OrgAccount("111111111111", "master", "ACTIVE"),
        OrgAccount("222222222222", "m1", "ACTIVE"),
        OrgAccount("333333333333", "m2", "ACTIVE"),
        OrgAccount("444444444444", "m3", "ACTIVE"),
    ]
    return (
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=caller),
        patch("scripts.validate_environment.organizations_describe_organization", return_value=org),
        patch("scripts.validate_environment.organizations_list_accounts", return_value=accounts),
        patch("scripts.validate_environment.glue_get_table", return_value=_full_table()),
    )


def test_cur_org_wide_coverage_happy_path():
    stubs = _stubs_through_check3()
    with (
        stubs[0],
        stubs[1],
        stubs[2],
        stubs[3],
        patch("scripts.validate_environment.athena_execute") as mock_athena,
    ):
        mock_athena.return_value = [
            {"line_item_usage_account_id": "111111111111"},
            {"line_item_usage_account_id": "222222222222"},
            {"line_item_usage_account_id": "333333333333"},
            {"line_item_usage_account_id": "444444444444"},
        ]
        result = validate_environment(_cfg())
    assert result.blockers == []


def test_cur_coverage_below_50pct_raises_blocker():
    stubs = _stubs_through_check3()
    with (
        stubs[0],
        stubs[1],
        stubs[2],
        stubs[3],
        patch("scripts.validate_environment.athena_execute") as mock_athena,
    ):
        # Only master account in CUR → 1/4 = 25% coverage
        mock_athena.return_value = [{"line_item_usage_account_id": "111111111111"}]
        result = validate_environment(_cfg())
    assert any(b.code == "cur_not_org_wide" for b in result.blockers)


def test_org_with_no_active_members_raises_blocker():
    accounts = [OrgAccount("111111111111", "m", "SUSPENDED")]
    stubs = _stubs_through_check3(accounts=accounts)
    with stubs[0], stubs[1], stubs[2], stubs[3]:
        result = validate_environment(_cfg())
    assert any(b.code == "org_has_no_active_members" for b in result.blockers)


def test_probe_query_accepts_both_month_formats():
    """Real AWS CUR v1 uses unpadded month partitions (month='4'), but some
    installations zero-pad ('04'). Probe must match both via IN (...)."""
    stubs = _stubs_through_check3()
    captured_queries = []

    def capture(config, query):
        captured_queries.append(query)
        return [
            {"line_item_usage_account_id": "111111111111"},
            {"line_item_usage_account_id": "222222222222"},
            {"line_item_usage_account_id": "333333333333"},
            {"line_item_usage_account_id": "444444444444"},
        ]

    with (
        stubs[0],
        stubs[1],
        stubs[2],
        stubs[3],
        patch("scripts.validate_environment.athena_execute", side_effect=capture),
    ):
        validate_environment(_cfg())
    assert captured_queries
    query = captured_queries[0]
    import re

    # Must use IN (...) form that covers both unpadded and zero-padded
    m = re.search(r"month\s+IN\s*\(\s*'(\d{1,2})'\s*,\s*'(\d{1,2})'\s*\)", query)
    assert m, f"query: {query}"
    unpadded, padded = m.group(1), m.group(2)
    # padded form is always two digits
    assert len(padded) == 2, f"padded form must be 2 digits: {padded}"
    # unpadded and padded represent the same month
    assert int(unpadded) == int(padded), f"mismatched months: {unpadded} vs {padded}"
