"""Tests for validate_environment Check 3 — CUR schema."""

from unittest.mock import patch

from botocore.exceptions import ClientError

from scripts._common import CallerIdentity, Column, OrgAccount, Organization, OrgConfig, TableSchema
from scripts.validate_environment import validate_environment


def _base_config() -> OrgConfig:
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


def _happy_path_stubs():
    caller = CallerIdentity(account_id="111111111111", arn="arn", user_id="u")
    org = Organization(
        id="o-abc", master_account_id="111111111111", master_account_email="root@example.com"
    )
    accounts = [OrgAccount(id="111111111111", name="master", status="ACTIVE")]
    return caller, org, accounts


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


def test_cur_table_not_found():
    caller, org, accounts = _happy_path_stubs()
    with (
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=caller),
        patch("scripts.validate_environment.organizations_describe_organization", return_value=org),
        patch("scripts.validate_environment.organizations_list_accounts", return_value=accounts),
        patch("scripts.validate_environment.glue_get_table") as mock_glue,
    ):
        mock_glue.side_effect = ClientError(
            {"Error": {"Code": "EntityNotFoundException"}}, "GetTable"
        )
        result = validate_environment(_base_config())
    assert any(b.code == "cur_table_not_found" for b in result.blockers)


def test_cur_schema_incomplete():
    caller, org, accounts = _happy_path_stubs()
    partial = TableSchema(
        name="t",
        database="d",
        columns=[Column("line_item_usage_start_date", "timestamp")],
    )
    with (
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=caller),
        patch("scripts.validate_environment.organizations_describe_organization", return_value=org),
        patch("scripts.validate_environment.organizations_list_accounts", return_value=accounts),
        patch("scripts.validate_environment.glue_get_table", return_value=partial),
        patch("scripts.validate_environment.athena_execute", return_value=[]),
    ):
        result = validate_environment(_base_config())
    assert any(b.code == "cur_schema_incomplete" for b in result.blockers)


def test_resource_tags_warning_when_column_missing():
    caller, org, accounts = _happy_path_stubs()
    full_columns = [Column(name=c, type="string") for c in REQUIRED_COLUMNS]
    schema = TableSchema(name="t", database="d", columns=full_columns)
    with (
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=caller),
        patch("scripts.validate_environment.organizations_describe_organization", return_value=org),
        patch("scripts.validate_environment.organizations_list_accounts", return_value=accounts),
        patch("scripts.validate_environment.glue_get_table", return_value=schema),
        patch(
            "scripts.validate_environment.athena_execute",
            return_value=[{"line_item_usage_account_id": "111111111111"}],
        ),
    ):
        result = validate_environment(_base_config())
    assert result.context is not None
    assert result.context.cur_table_has_resource_tags_user_service is False
    assert any(w["code"] == "resource_tags_user_service_missing" for w in result.warnings)


def test_resource_tags_present():
    caller, org, accounts = _happy_path_stubs()
    columns = [Column(name=c, type="string") for c in REQUIRED_COLUMNS]
    columns.append(Column("resource_tags_user_service", "string"))
    schema = TableSchema(name="t", database="d", columns=columns)
    with (
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=caller),
        patch("scripts.validate_environment.organizations_describe_organization", return_value=org),
        patch("scripts.validate_environment.organizations_list_accounts", return_value=accounts),
        patch("scripts.validate_environment.glue_get_table", return_value=schema),
        patch(
            "scripts.validate_environment.athena_execute",
            return_value=[{"line_item_usage_account_id": "111111111111"}],
        ),
    ):
        result = validate_environment(_base_config())
    assert result.context is not None
    assert result.context.cur_table_has_resource_tags_user_service is True
