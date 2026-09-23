"""Run each status path end-to-end under heavy mocking, capture the output
dict, and write it to examples/. Run from the skill directory:
    python tests/generate_examples.py
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# Import run_optimizer plus every mockable dependency
from scripts._common import (
    CallerIdentity,
    Column,
    ConfigError,
    HourlySeries,
    OrgAccount,
    Organization,
    RatioEntry,
    Ratios,
    SPInfo,
    TableSchema,
)
from scripts.aws_sp_optimizer import parse_args, run_optimizer

OUTPUT_DIR = Path(__file__).parent.parent / "examples"
OUTPUT_DIR.mkdir(exist_ok=True)

# Point config_loader at the sample fixture so functions that reach load_config
# succeed without needing a real ~/.config/aws-sp-optimizer/orgs.yaml.
_FIXTURE = Path(__file__).parent / "fixtures" / "sample_orgs.yaml"
os.environ["AWS_SP_OPTIMIZER_CONFIG_PATH"] = str(_FIXTURE)


def _write(name: str, output: dict) -> None:
    path = OUTPUT_DIR / name
    path.write_text(json.dumps(output, indent=2, default=str))
    print(f"wrote {path}")


def _required_cur_columns():
    return {
        "line_item_usage_start_date", "line_item_line_item_type",
        "line_item_product_code", "line_item_usage_type", "line_item_operation",
        "line_item_usage_account_id", "line_item_usage_amount",
        "line_item_resource_id", "pricing_public_on_demand_cost",
        "pricing_term", "product_instance_type", "product_region_code",
        "resource_tags_user_service",
    }


def _happy_path_series():
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    start = end - timedelta(days=104)
    n = 104 * 24
    hours = [start + timedelta(hours=i) for i in range(n)]
    data = {h: {"total_list_usd": 10.0, "total_net_usd": 9.1, "mix": {}} for h in hours}
    for h in hours[-30 * 24:]:
        data[h]["mix"] = {
            ("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{i}", f"U{i}"): 1.0
            for i in range(15)
        }
    return HourlySeries(hours=hours, data=data, window_start=start, window_end=end)


def _happy_path_ratios():
    return Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={
            "us-east-1": {
                f"c5.xlarge|Op{i}": RatioEntry(0.83, 1.0, 0.83, 0.17)
                for i in range(15)
            }
        },
        lambda_rates={}, fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v1"},
        },
        primary_region="us-east-1",
    )


def _run_with_mocks(args, series, ratios, all_relevant_sps, has_tags_col=True):
    caller = CallerIdentity(account_id="002497567426", arn="arn", user_id="u")
    org = Organization(
        id="o-4bw6d7ou28",
        master_account_id="002497567426",
        master_account_email="root@example.com",
    )
    accounts = [OrgAccount(id="002497567426", name="master", status="ACTIVE")]
    cols = list(_required_cur_columns()) if has_tags_col else [
        c for c in _required_cur_columns() if c != "resource_tags_user_service"
    ]
    table = TableSchema(
        name="a4x_report", database="cur-db",
        columns=[Column(name=c, type="string") for c in cols],
    )
    with patch("scripts.validate_environment.sts_get_caller_identity", return_value=caller), \
         patch("scripts.validate_environment.organizations_describe_organization", return_value=org), \
         patch("scripts.validate_environment.organizations_list_accounts", return_value=accounts), \
         patch("scripts.validate_environment.glue_get_table", return_value=table), \
         patch("scripts.validate_environment.athena_execute", return_value=[{"line_item_usage_account_id": "002497567426"}]), \
         patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=all_relevant_sps), \
         patch("scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh", return_value=ratios), \
         patch("scripts.aws_sp_optimizer.cur_query.build_hourly_series", return_value=series), \
         patch("scripts.aws_sp_optimizer.cur_query.discover_workload_regions",
               return_value=[("us-east-1", 1.0)]), \
         patch("scripts.newsvendor.ce_get_savings_plans_utilization",
               return_value={"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}):
        return run_optimizer(args)


def gen_ok():
    args = parse_args(["--org", "a4x-us"])
    output = _run_with_mocks(args, _happy_path_series(), _happy_path_ratios(), [])
    _write("sample_output_ok.json", output)


def gen_ok_with_adjustment():
    # Inject a retired SP that forces window_search to slide back.
    # The SP ended 10 days before series end so it falls inside the default
    # requested window (end=2026-04-13, days=90) but outside older candidates
    # (i >= 10), allowing those to pass and triggering an ok_with_adjustment
    # status. We pin --window-end to 2026-04-13 so requested.end aligns with
    # the mocked series.window_end; otherwise parse_window_end('today') drifts
    # past the series and every candidate fails severe_data.
    series_end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    retired_end = series_end - timedelta(days=10)
    sp = SPInfo(
        id="sp-retired", type="Compute",
        ec2_instance_family=None, region=None, commitment=1.0,
        start=retired_end - timedelta(days=365), end=retired_end,
        state="retired", payment_option="No Upfront",
    )
    args = parse_args(["--org", "a4x-us", "--window-end", "2026-04-13"])
    output = _run_with_mocks(args, _happy_path_series(), _happy_path_ratios(), [sp])
    _write("sample_output_ok_with_adjustment.json", output)


def gen_needs_decision():
    # Build a steeply declining series (severe_decline across every candidate)
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    start = end - timedelta(days=104)
    n = 104 * 24
    hours = [start + timedelta(hours=i) for i in range(n)]
    data = {}
    for i, h in enumerate(hours):
        # 10.0 → 1.0 over 104 days
        value = max(1.0, 10.0 - (9.0 / n) * i)
        data[h] = {"total_list_usd": value, "total_net_usd": value * 0.91, "mix": {}}
    for h in hours[-30 * 24:]:
        data[h]["mix"] = {
            ("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{j}", f"U{j}"): 1.0
            for j in range(15)
        }
    series = HourlySeries(hours=hours, data=data, window_start=start, window_end=end)
    args = parse_args(["--org", "a4x-us"])
    output = _run_with_mocks(args, series, _happy_path_ratios(), [])
    _write("sample_output_needs_decision.json", output)


def gen_needs_setup():
    # Force config_loader to fail by pointing it at a nonexistent file
    os.environ["AWS_SP_OPTIMIZER_CONFIG_PATH"] = "/nonexistent-orgs-for-test.yaml"
    args = parse_args(["--org", "a4x-us"])
    try:
        run_optimizer(args)
    except ConfigError as e:
        from scripts._common import base_output
        output = {**base_output(), **e.to_output_dict()}
        _write("sample_output_needs_setup.json", output)
    finally:
        # Restore fixture path so subsequent generators use a valid config
        os.environ["AWS_SP_OPTIMIZER_CONFIG_PATH"] = str(_FIXTURE)


def gen_needs_validation_fix():
    # Force validate_environment to return a permissions_missing blocker
    from botocore.exceptions import ClientError

    from scripts._common import ValidationBlockerError, base_output
    caller = CallerIdentity(account_id="002497567426", arn="arn", user_id="u")
    args = parse_args(["--org", "a4x-us"])
    with patch("scripts.validate_environment.sts_get_caller_identity", return_value=caller), \
         patch("scripts.validate_environment.organizations_describe_organization") as mock_org:
        mock_org.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException"}}, "DescribeOrganization"
        )
        try:
            run_optimizer(args)
        except ValidationBlockerError as e:
            output = {**base_output(), **e.to_output_dict()}
            _write("sample_output_needs_validation_fix.json", output)


if __name__ == "__main__":
    gen_ok()
    gen_ok_with_adjustment()
    gen_needs_decision()
    gen_needs_setup()
    gen_needs_validation_fix()
