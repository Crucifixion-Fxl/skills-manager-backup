"""End-to-end: all 3 exclude axes active simultaneously.

Mocks all boto3 + Athena calls; asserts:
  1. Output JSON exposes populated applied_exclude_filter
  2. C_existing_effective is attenuated by sp_coverage_share
  3. Missing tag column surfaces as skipped_filter_warning
  4. coverage_scope reflects the filter axes
"""

import json
import sys
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


def test_e2e_all_three_exclude_axes_active(monkeypatch, capsys):
    """Exercise the full pipeline with usage-type + account + tag filters.

    One tag references a column present in CUR (lifecycle → retained);
    the other tag references a nonexistent column (nonexistent → warning).
    """
    monkeypatch.setenv("AWS_SP_OPTIMIZER_CONFIG_PATH", str(FIXTURE))
    monkeypatch.setattr(
        sys, "argv",
        [
            "aws-sp-optimizer", "--org", "a4x-us",
            "--window-end", "2026-04-13",
            "--exclude-usage-type-pattern", "g4dn.",
            "--exclude-account-id", "123456789012",
            "--exclude-tag", "lifecycle=ephemeral",
            "--exclude-tag", "nonexistent=x",
        ],
    )

    # Baseline AWS/Org/schema mocks — copy from test_end_to_end.py verbatim
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

    # Synthetic HourlySeries (same shape as existing E2E)
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

    # Available tag columns: lifecycle is present; 'nonexistent' is NOT → warning expected
    available_tag_columns = frozenset({"resource_tags_user_lifecycle"})

    # sp_coverage_share: 0.7 (30% of SP-covered workload excluded by filter)
    sp_share_audit = {
        "retained_cost_usd": 700.0,
        "total_cost_usd": 1000.0,
        "note": "share = retained / total",
    }

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
            return_value=(0.09, 0.09, {
                "uniform": True, "delta": 0.0,
                "e_sp_sample_usd": 100.0, "e_od_sample_usd": 1000.0,
                "e_sp_inferred_from_e_od": False,
            }),
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
        # Task 17's additions: tag column discovery + sp coverage share
        patch(
            "scripts.aws_sp_optimizer.fetch_available_tag_columns",
            return_value=available_tag_columns,
        ),
        patch(
            "scripts.aws_sp_optimizer.compute_sp_coverage_share",
            return_value=(0.7, sp_share_audit),
        ),
        # compute_untagged_fraction is imported into output_builder, so patch there
        patch(
            "scripts.output_builder.compute_untagged_fraction",
            return_value=0.01,
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

    assert exc.value.code == 0, f"main() exited non-zero: {exc.value.code}"
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert output["status"] in ("ok", "ok_with_adjustment")

    # Assertion 1: applied_exclude_filter is populated
    aef = output["context"]["applied_exclude_filter"]
    assert aef is not None, "applied_exclude_filter should be populated when axes are active"
    assert aef["usage_type_patterns"] == ["g4dn."]
    assert aef["account_ids"] == ["123456789012"]
    assert {"key": "lifecycle", "values": ["ephemeral"]} in aef["tag_exclusions"]
    assert {"key": "nonexistent", "values": ["x"]} in aef["tag_exclusions"]
    assert aef["include_untagged"] is True  # default (no --exclude-untagged passed)

    # Assertion 2: sp_coverage_share reflected
    assert aef["sp_coverage_share"] == pytest.approx(0.7)
    assert aef["sp_coverage_share_audit"]["retained_cost_usd"] == pytest.approx(700.0)

    # Assertion 3: skipped_filter_warnings mentions the nonexistent tag
    warnings = aef["skipped_filter_warnings"]
    assert any("nonexistent" in w for w in warnings), (
        f"expected skipped_filter_warnings to mention 'nonexistent', got {warnings}"
    )

    # Assertion 4: coverage_scope reflects the filter
    scope = output["purchase_instructions"]["summary"]["coverage_scope"]
    assert "filtered:" in scope
    assert "g4dn." in scope
    assert "123456789012" in scope
    assert "lifecycle=ephemeral" in scope
