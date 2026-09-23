"""Thin boundary tests for AWS wrappers — uses unittest.mock.patch on boto3.Session."""

import pytest
from unittest.mock import MagicMock, patch

from scripts._common import (
    SPInfo,
    TableSchema,
    glue_get_table,
    savingsplans_describe_savings_plans,
)


def test_glue_get_table_returns_tableschema():
    fake_client = MagicMock()
    fake_client.get_table.return_value = {
        "Table": {
            "Name": "a4x_report",
            "DatabaseName": "cur-db",
            "StorageDescriptor": {
                "Columns": [
                    {"Name": "line_item_usage_start_date", "Type": "timestamp"},
                    {"Name": "line_item_product_code", "Type": "string"},
                ],
            },
        }
    }
    with patch("boto3.Session") as mock_session_cls:
        mock_session_cls.return_value.client.return_value = fake_client
        ts = glue_get_table("prof", "cur-db", "a4x_report")
    assert isinstance(ts, TableSchema)
    assert ts.name == "a4x_report"
    assert ts.database == "cur-db"
    assert len(ts.columns) == 2
    assert ts.columns[0].name == "line_item_usage_start_date"
    assert ts.columns[0].type == "timestamp"


def test_savingsplans_describe_returns_spinfo_list():
    fake_client = MagicMock()
    # describe_savings_plans is NOT a boto3 paginator — the wrapper loops
    # manually with nextToken. Return a single page with no nextToken so
    # the loop terminates after one call.
    fake_client.describe_savings_plans.return_value = {
        "savingsPlans": [
            {
                "savingsPlanId": "sp-abc",
                "savingsPlanType": "Compute",
                "ec2InstanceFamily": None,
                "region": None,
                "commitment": "5.0",
                "start": "2025-10-01T00:00:00Z",
                "end": "2026-10-01T00:00:00Z",
                "state": "active",
                "paymentOption": "No Upfront",
            }
        ],
    }

    with patch("boto3.Session") as mock_session_cls:
        mock_session_cls.return_value.client.return_value = fake_client
        sps = savingsplans_describe_savings_plans("prof", states=["active"])

    assert len(sps) == 1
    assert isinstance(sps[0], SPInfo)
    assert sps[0].id == "sp-abc"
    assert sps[0].type == "Compute"
    assert sps[0].commitment == pytest.approx(5.0)
    assert sps[0].state == "active"
    fake_client.describe_savings_plans.assert_called_once()


def test_savingsplans_describe_paginates_with_next_token():
    """When describe_savings_plans returns a nextToken, the wrapper must
    issue additional calls until the token disappears."""
    fake_client = MagicMock()
    page1 = {
        "savingsPlans": [
            {
                "savingsPlanId": "sp-1",
                "savingsPlanType": "Compute",
                "ec2InstanceFamily": None,
                "region": None,
                "commitment": "1.0",
                "start": "2025-10-01T00:00:00Z",
                "end": "2026-10-01T00:00:00Z",
                "state": "active",
                "paymentOption": "No Upfront",
            }
        ],
        "nextToken": "token-abc",
    }
    page2 = {
        "savingsPlans": [
            {
                "savingsPlanId": "sp-2",
                "savingsPlanType": "Compute",
                "ec2InstanceFamily": None,
                "region": None,
                "commitment": "2.0",
                "start": "2025-10-01T00:00:00Z",
                "end": "2026-10-01T00:00:00Z",
                "state": "active",
                "paymentOption": "No Upfront",
            }
        ],
    }
    fake_client.describe_savings_plans.side_effect = [page1, page2]

    with patch("boto3.Session") as mock_session_cls:
        mock_session_cls.return_value.client.return_value = fake_client
        sps = savingsplans_describe_savings_plans("prof", states=["active"])

    assert [sp.id for sp in sps] == ["sp-1", "sp-2"]
    assert fake_client.describe_savings_plans.call_count == 2
    second_call_kwargs = fake_client.describe_savings_plans.call_args_list[1].kwargs
    assert second_call_kwargs.get("nextToken") == "token-abc"
