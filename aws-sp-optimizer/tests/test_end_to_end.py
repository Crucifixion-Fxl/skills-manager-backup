"""End-to-end integration test — config to JSON output with full module wiring.

This is the 'happy path' scenario. Four more integration tests (needs_setup,
needs_human_decision, ok_with_adjustment, aws_api_error) live alongside but
are placeholders here — Phase 6 Task 6.x can expand as smoke tests catch gaps.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts._common import (
    CallerIdentity,
    Column,
    HourlySeries,
    OrgAccount,
    Organization,
    RatioEntry,
    Ratios,
    TableSchema,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_orgs.yaml"


def test_e2e_happy_path_ok_status(monkeypatch, tmp_path, capsys):
    import sys

    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(FIXTURE))
    # Pin --window-end so this e2e is deterministic regardless of wall-clock
    # date. Without the pin, parse_window_end("today") drifts relative to the
    # hardcoded series end=2026-04-13 and window_search flips ok →
    # ok_with_adjustment (same reason gen_ok_with_adjustment needed the pin).
    monkeypatch.setattr(sys, "argv", ["aws-sp-optimizer", "--org", "a4x-us", "--window-end", "2026-04-13"])

    # Mocks for the AWS / pricing-cache calls
    caller = CallerIdentity(account_id="002497567426", arn="arn", user_id="u")
    org = Organization(
        id="o-4bw6d7ou28",
        master_account_id="002497567426",
        master_account_email="root@example.com",
    )
    accounts = [OrgAccount(id="002497567426", name="master", status="ACTIVE")]
    required_columns = {
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
        "resource_tags_user_service",
    }
    table_schema = TableSchema(
        name="a4x_report",
        database="cur-db",
        columns=[Column(name=c, type="string") for c in required_columns],
    )
    ratios = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={
            "us-east-1": {f"c5.xlarge|Op{i}": RatioEntry(0.83, 1.0, 0.83, 0.17) for i in range(15)}
        },
        lambda_rates={},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v1"},
        },
        primary_region="us-east-1",
    )

    # Build a synthetic HourlySeries with mix on the recent 30d window
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    start = end - timedelta(days=104)
    n = 104 * 24
    hours = [start + timedelta(hours=i) for i in range(n)]
    data = {h: {"total_usd": 10.0, "total_list_usd": 10.0, "total_net_usd": 10.0 * 0.91, "mix": {}} for h in hours}
    for h in hours[-30 * 24 :]:
        data[h]["mix"] = {
            ("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{i}", f"U{i}"): 1.0 for i in range(15)
        }
    series = HourlySeries(hours=hours, data=data, window_start=start, window_end=end)

    patches = [
        patch("scripts.validate_environment.sts_get_caller_identity", return_value=caller),
        patch("scripts.validate_environment.organizations_describe_organization", return_value=org),
        patch("scripts.validate_environment.organizations_list_accounts", return_value=accounts),
        patch("scripts.validate_environment.glue_get_table", return_value=table_schema),
        patch(
            "scripts.validate_environment.athena_execute",
            return_value=[{"line_item_usage_account_id": "002497567426"}],
        ),
        patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=[]),
        patch("scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh", return_value=ratios),
        patch(
            "scripts.aws_sp_optimizer.cur_query.compute_edp_factors",
            return_value=(0.09, 0.09, {"uniform": True, "delta": 0.0, "e_sp_sample_usd": 100.0, "e_od_sample_usd": 1000.0, "e_sp_inferred_from_e_od": False}),
        ),
        patch("scripts.aws_sp_optimizer.cur_query.build_hourly_series", return_value=series),
        patch(
            "scripts.aws_sp_optimizer.cur_query.discover_workload_regions",
            return_value=[("us-east-1", 1.0)],
        ),
        patch(
            "scripts.newsvendor.ce_get_savings_plans_utilization",
            return_value={"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}},
        ),
    ]
    for p in patches:
        p.start()
    try:
        from scripts.aws_sp_optimizer import main

        with pytest.raises(SystemExit) as exc:
            main()
    finally:
        for p in patches:
            p.stop()

    assert exc.value.code == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["status"] == "ok"
    assert output["schema_version"] == 1
    assert output["recommendation"]["delta_per_hour_to_buy"] >= 0
    assert output["purchase_instructions"] is not None
    assert output["context"]["org_alias"] == "a4x-us"
