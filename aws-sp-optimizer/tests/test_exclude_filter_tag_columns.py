from unittest.mock import patch

from scripts._common import OrgConfig
from scripts.exclude_filter import fetch_available_tag_columns


def _cfg():
    return OrgConfig(
        alias="t",
        description="t",
        profile="p",
        payer_account_id="002497567426",
        org_id="o-test",
        cur_database="db",
        cur_table="cur",
        athena_output="s3://x/",
        athena_workgroup="primary",
        primary_region="us-east-1",
        window_days=30,
        window_end="today",
        prefer="balanced",
    )


def test_fetch_returns_frozenset_of_matching_columns():
    rows = [
        {"column_name": "resource_tags_user_lifecycle"},
        {"column_name": "resource_tags_user_cost_category"},
        {"column_name": "line_item_usage_type"},  # non-tag should not leak
    ]
    with patch("scripts.exclude_filter.athena_execute", return_value=rows):
        cols = fetch_available_tag_columns(_cfg())
    assert cols == frozenset(
        {"resource_tags_user_lifecycle", "resource_tags_user_cost_category"}
    )
    assert isinstance(cols, frozenset)


def test_fetch_returns_empty_when_no_tag_columns():
    with patch("scripts.exclude_filter.athena_execute", return_value=[]):
        cols = fetch_available_tag_columns(_cfg())
    assert cols == frozenset()


def test_fetch_is_tolerant_of_athena_failure():
    """information_schema should be best-effort: on error, return empty + warn."""
    with patch(
        "scripts.exclude_filter.athena_execute", side_effect=RuntimeError("boom")
    ):
        cols = fetch_available_tag_columns(_cfg())
    assert cols == frozenset()
