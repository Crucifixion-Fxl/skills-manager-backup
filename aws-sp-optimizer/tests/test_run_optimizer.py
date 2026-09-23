"""Integration test for run_optimizer — wires all phases together with mocks."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scripts._common import (
    HourlySeries,
    OrgConfig,
    RatioEntry,
    Ratios,
    ValidationContext,
    ValidationResult,
)
from scripts.aws_sp_optimizer import parse_args, run_optimizer


def _cfg():
    return OrgConfig(
        alias="a4x-us",
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
        window_end="2026-04-13",
        prefer="balanced",
    )


def _ratios():
    return Ratios(
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


def _ctx():
    return ValidationContext(
        caller_account_id="111111111111",
        org_id="o-abc",
        master_account_email="root@example.com",
        member_account_ids=["111111111111"],
        member_accounts_count=1,
        primary_region="us-east-1",
        cur_table_has_resource_tags_user_service=False,
    )


def _series_with_mix():
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    start = end - timedelta(days=104)
    n = 104 * 24
    hours = [start + timedelta(hours=i) for i in range(n)]
    data = {h: {"total_usd": 10.0, "total_list_usd": 10.0, "total_net_usd": 10.0 * 0.91, "mix": {}} for h in hours}
    for h in hours[-30 * 24 :]:
        data[h]["mix"] = {
            ("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{i}", f"U{i}"): 1.0 for i in range(15)
        }
    return HourlySeries(hours=hours, data=data, window_start=start, window_end=end)


_DISCOVER_PATCH = "scripts.aws_sp_optimizer.cur_query.discover_workload_regions"
_DISCOVER_DEFAULT = [("us-east-1", 1.0)]
_EDP_PATCH = "scripts.aws_sp_optimizer.cur_query.compute_edp_factors"
_EDP_DEFAULT = (0.09, 0.09, {"uniform": True, "delta": 0.0, "e_sp_sample_usd": 100.0, "e_od_sample_usd": 1000.0, "e_sp_inferred_from_e_od": False})


def test_run_optimizer_happy_path_returns_ok_output():
    args = parse_args(["--org", "a4x-us"])
    with (
        patch("scripts.aws_sp_optimizer.config_loader.load_config", return_value=_cfg()),
        patch(
            "scripts.aws_sp_optimizer.validate_environment",
            return_value=ValidationResult(context=_ctx()),
        ),
        patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=[]),
        patch("scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh", return_value=_ratios()),
        patch(_EDP_PATCH, return_value=_EDP_DEFAULT),
        patch(
            "scripts.aws_sp_optimizer.cur_query.build_hourly_series",
            return_value=_series_with_mix(),
        ),
        patch(_DISCOVER_PATCH, return_value=_DISCOVER_DEFAULT),
        patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce,
    ):
        mock_ce.return_value = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
        output = run_optimizer(args)

    assert output["status"] == "ok"
    assert "recommendation" in output
    assert output["recommendation"]["delta_per_hour_to_buy"] >= 0
    # INV-5: must_review should be False → purchase_instructions present
    assert output["purchase_instructions"] is not None
    # Task 12: discount_audit must be present in baseline with all 11 fields
    da = output["baseline"]["discount_audit"]
    required_fields = {
        "d_raw", "d_calibrated", "e_sp", "e_od", "edp_uniform", "calibration_applied",
        "e_sp_inferred_from_e_od", "coverage_pct", "matched_entries",
        "unmatched_entries", "unmatched_top10_by_cost",
    }
    assert required_fields <= set(da.keys()), f"Missing discount_audit fields: {required_fields - set(da.keys())}"


def test_run_optimizer_validate_only_returns_validation_ok():
    args = parse_args(["--org", "a4x-us", "--validate-only"])
    with (
        patch("scripts.aws_sp_optimizer.config_loader.load_config", return_value=_cfg()),
        patch(
            "scripts.aws_sp_optimizer.validate_environment",
            return_value=ValidationResult(context=_ctx()),
        ),
    ):
        output = run_optimizer(args)
    assert output["status"] == "validation_ok"
    assert output["config_alias"] == "a4x-us"
    assert output["blockers"] == []


def test_run_optimizer_needs_human_decision_when_search_fails():
    """When window_search returns status='failed' (no viable candidate), the
    orchestrator must short-circuit to a needs_human_decision output instead
    of calling compute_optimal_commit (which would crash on None chosen)."""
    from scripts._common import WindowSearchResult

    args = parse_args(["--org", "a4x-us"])
    failed_search = WindowSearchResult(
        status="failed",
        failure_reason="no_viable_window",
        rejection_summary={
            "severe_decline": {"count": 75, "samples_omitted_from_output": True},
        },
    )
    with (
        patch("scripts.aws_sp_optimizer.config_loader.load_config", return_value=_cfg()),
        patch(
            "scripts.aws_sp_optimizer.validate_environment",
            return_value=ValidationResult(context=_ctx()),
        ),
        patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=[]),
        patch("scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh", return_value=_ratios()),
        patch(_EDP_PATCH, return_value=_EDP_DEFAULT),
        patch(
            "scripts.aws_sp_optimizer.cur_query.build_hourly_series",
            return_value=_series_with_mix(),
        ),
        patch(_DISCOVER_PATCH, return_value=_DISCOVER_DEFAULT),
        patch("scripts.aws_sp_optimizer.search_best_window", return_value=failed_search),
    ):
        output = run_optimizer(args)

    assert output["status"] == "needs_human_decision"
    assert "failure_dossier" in output
    assert output["failure_dossier"]["reason_summary"] == "no_viable_window"
    assert "severe_decline" in output["failure_dossier"]["rejection_summary"]


def test_run_optimizer_merges_discovered_with_primary_and_additional():
    """Discovery result ∪ primary ∪ --additional-regions passed to ensure_cache_fresh."""
    args = parse_args(["--org", "a4x-us", "--additional-regions", "ap-northeast-2"])
    with (
        patch("scripts.aws_sp_optimizer.config_loader.load_config", return_value=_cfg()),
        patch(
            "scripts.aws_sp_optimizer.validate_environment",
            return_value=ValidationResult(context=_ctx()),
        ),
        patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=[]),
        patch(
            "scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh",
            return_value=_ratios(),
        ) as mock_cache,
        patch(_EDP_PATCH, return_value=_EDP_DEFAULT),
        patch(
            "scripts.aws_sp_optimizer.cur_query.build_hourly_series",
            return_value=_series_with_mix(),
        ),
        patch(
            _DISCOVER_PATCH,
            return_value=[("us-east-1", 0.7), ("eu-central-1", 0.3)],
        ),
        patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce,
    ):
        mock_ce.return_value = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
        run_optimizer(args)

    call_kwargs = mock_cache.call_args[1]
    assert call_kwargs["required_regions"] == {"us-east-1", "eu-central-1", "ap-northeast-2"}


def test_run_optimizer_discovery_failure_falls_back_to_primary():
    """If discover_workload_regions raises, ensure_cache_fresh gets only primary_region
    and output warnings contain region_discovery_failed_fallback_to_primary."""
    from scripts._common import AthenaError

    args = parse_args(["--org", "a4x-us"])
    with (
        patch("scripts.aws_sp_optimizer.config_loader.load_config", return_value=_cfg()),
        patch(
            "scripts.aws_sp_optimizer.validate_environment",
            return_value=ValidationResult(context=_ctx()),
        ),
        patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=[]),
        patch(
            "scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh",
            return_value=_ratios(),
        ) as mock_cache,
        patch(_EDP_PATCH, return_value=_EDP_DEFAULT),
        patch(
            "scripts.aws_sp_optimizer.cur_query.build_hourly_series",
            return_value=_series_with_mix(),
        ),
        patch(
            _DISCOVER_PATCH,
            side_effect=AthenaError(code="query_failed", message="simulated failure"),
        ),
        patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce,
    ):
        mock_ce.return_value = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
        output = run_optimizer(args)

    call_kwargs = mock_cache.call_args[1]
    assert call_kwargs["required_regions"] == {"us-east-1"}
    warning_codes = [w["code"] for w in output["warnings"]]
    assert "region_discovery_failed_fallback_to_primary" in warning_codes


def test_slow_cold_start_warning_at_4_regions():
    """When discovery yields 4+ regions, output contains the slow-cold-start warning."""
    args = parse_args(["--org", "a4x-us"])
    four_regions = [
        ("us-east-1", 0.4),
        ("eu-central-1", 0.3),
        ("ap-southeast-1", 0.2),
        ("ap-northeast-1", 0.1),
    ]
    with (
        patch("scripts.aws_sp_optimizer.config_loader.load_config", return_value=_cfg()),
        patch(
            "scripts.aws_sp_optimizer.validate_environment",
            return_value=ValidationResult(context=_ctx()),
        ),
        patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=[]),
        patch(
            "scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh",
            return_value=_ratios(),
        ),
        patch(_EDP_PATCH, return_value=_EDP_DEFAULT),
        patch(
            "scripts.aws_sp_optimizer.cur_query.build_hourly_series",
            return_value=_series_with_mix(),
        ),
        patch(_DISCOVER_PATCH, return_value=four_regions),
        patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce,
    ):
        mock_ce.return_value = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
        output = run_optimizer(args)

    warning_codes = [w["code"] for w in output["warnings"]]
    assert "region_discovery_many_regions_slow_cold_start" in warning_codes
    slow_warn = next(
        w for w in output["warnings"]
        if w["code"] == "region_discovery_many_regions_slow_cold_start"
    )
    assert slow_warn["context"]["region_count"] == 4


def test_only_regions_bypasses_discovery_and_additional():
    """--only-regions replaces discovery and --additional-regions entirely.
    Use case: power user relying on a known shipped cache."""
    args = parse_args([
        "--org", "a4x-us",
        "--only-regions", "us-east-1,eu-central-1",
        "--additional-regions", "ap-northeast-2",  # should be IGNORED
    ])
    with (
        patch("scripts.aws_sp_optimizer.config_loader.load_config", return_value=_cfg()),
        patch(
            "scripts.aws_sp_optimizer.validate_environment",
            return_value=ValidationResult(context=_ctx()),
        ),
        patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=[]),
        patch(
            "scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh",
            return_value=_ratios(),
        ) as mock_cache,
        patch(_EDP_PATCH, return_value=_EDP_DEFAULT),
        patch(
            "scripts.aws_sp_optimizer.cur_query.build_hourly_series",
            return_value=_series_with_mix(),
        ),
        patch(_DISCOVER_PATCH) as mock_discover,
        patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce,
    ):
        mock_ce.return_value = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
        run_optimizer(args)

    # discover_workload_regions must NOT be called when --only-regions is set
    mock_discover.assert_not_called()
    # ensure_cache_fresh receives exactly the --only-regions set (union primary)
    call_kwargs = mock_cache.call_args[1]
    assert call_kwargs["required_regions"] == {"us-east-1", "eu-central-1"}


def test_needs_cache_refresh_payload_has_three_actions():
    """When ensure_cache_fresh raises NeedsCacheRefreshError, output has status=needs_cache_refresh
    and exactly 3 entries in actions[]."""
    from scripts._common import NeedsCacheRefreshError

    args = parse_args(["--org", "a4x-us"])
    with (
        patch("scripts.aws_sp_optimizer.config_loader.load_config", return_value=_cfg()),
        patch(
            "scripts.aws_sp_optimizer.validate_environment",
            return_value=ValidationResult(context=_ctx()),
        ),
        patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=[]),
        patch(
            "scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh",
            side_effect=NeedsCacheRefreshError(
                code="pricing_cache_stale",
                message="Cache is more than 1 year old",
                context={"age_days": 400},
            ),
        ),
        patch(_DISCOVER_PATCH, return_value=_DISCOVER_DEFAULT),
    ):
        output = run_optimizer(args)

    assert output["status"] == "needs_cache_refresh"
    assert output["reason"] == "pricing_cache_stale"
    assert len(output["actions"]) == 3
    action_keys = {a["key"] for a in output["actions"]}
    assert action_keys == {"pull_from_git", "rebuild_locally", "skip_refresh_once"}


def test_edp_anomaly_gate_fires_when_e_sp_out_of_range():
    """When e_sp >= 0.99, output must be needs_human_decision with error_code=edp_anomaly."""
    args = parse_args(["--org", "a4x-us"])
    with (
        patch("scripts.aws_sp_optimizer.config_loader.load_config", return_value=_cfg()),
        patch(
            "scripts.aws_sp_optimizer.validate_environment",
            return_value=ValidationResult(context=_ctx()),
        ),
        patch("scripts.aws_sp_optimizer.fetch_all_relevant_sps", return_value=[]),
        patch("scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh", return_value=_ratios()),
        patch(
            _EDP_PATCH,
            return_value=(0.99, 0.09, {"uniform": False, "delta": 0.9}),
        ),
        patch(_DISCOVER_PATCH, return_value=_DISCOVER_DEFAULT),
    ):
        output = run_optimizer(args)

    assert output["status"] == "needs_human_decision"
    assert output["error_code"] == "edp_anomaly"
    assert "e_sp=0.9900" in output["error_message"]


def test_run_optimizer_threads_filter_through_build_hourly_series(monkeypatch):
    """Smoke: run_optimizer with --exclude-usage-type-pattern threads the filter
    into build_hourly_series. Narrower than full orchestrator smoke — proves
    the plumbing reaches at least one downstream CUR call."""
    monkeypatch.setattr(
        "scripts.aws_sp_optimizer.config_loader.load_config",
        lambda *a, **kw: _cfg(),
    )
    monkeypatch.setattr(
        "scripts.aws_sp_optimizer.validate_environment",
        lambda cfg: type("V", (), {
            "blockers": [], "warnings": [], "context": _ctx()
        })(),
    )
    monkeypatch.setattr(
        "scripts.aws_sp_optimizer.fetch_all_relevant_sps",
        lambda **kw: [],
    )
    monkeypatch.setattr(
        "scripts.aws_sp_optimizer.cur_query.discover_workload_regions",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "scripts.aws_sp_optimizer.pricing_cache.ensure_cache_fresh",
        lambda **kw: _ratios(),
    )
    monkeypatch.setattr(
        "scripts.aws_sp_optimizer.cur_query.compute_edp_factors",
        lambda *a, **kw: (0.09, 0.09, {"uniform": True, "delta": 0.0,
                                        "e_sp_sample_usd": 100.0, "e_od_sample_usd": 100.0,
                                        "e_sp_inferred_from_e_od": False}),
    )

    captured_build_kwargs = {}
    def spy_build(*a, **kw):
        captured_build_kwargs.update(kw)
        return _series_with_mix()
    monkeypatch.setattr(
        "scripts.aws_sp_optimizer.cur_query.build_hourly_series",
        spy_build,
    )

    # Short-circuit everything after build_hourly_series with a failure
    monkeypatch.setattr(
        "scripts.aws_sp_optimizer.search_best_window",
        lambda **kw: type("S", (), {
            "status": "failed", "failure_reason": "stub", "rejection_summary": {},
            "chosen": None
        })(),
    )

    from scripts.aws_sp_optimizer import parse_args, run_optimizer
    args = parse_args([
        "--org", "a4x-us",
        "--exclude-usage-type-pattern", "g4dn.",
    ])
    run_optimizer(args)

    assert "exclude_filter" in captured_build_kwargs
    flt = captured_build_kwargs["exclude_filter"]
    assert flt.usage_type_patterns == ("g4dn.",)
    assert "available_tag_columns" in captured_build_kwargs
