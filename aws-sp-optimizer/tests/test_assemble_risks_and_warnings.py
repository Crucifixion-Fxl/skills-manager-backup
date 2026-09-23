"""Tests for assemble_risks_and_warnings."""

from datetime import datetime, timezone

from scripts._common import (
    BaselineStats,
    DiscountAudit,
    FinalResult,
    PricingCacheMeta,
    SPAudit,
    TrendAwareAudit,
    TrendBucket,
    ValidationContext,
    ValidationResult,
    Window,
    WindowSearchResult,
)
from scripts.output_builder import assemble_risks_and_warnings


def _dummy_bucket():
    now = datetime(2026, 4, 13, tzinfo=timezone.utc)
    return TrendBucket(
        period_start=now, period_end=now, sample_size=480, p17=10.0, mean=10.0, min=10.0, max=10.0
    )


def _final(
    *,
    d_coverage: float = 0.99,
    util: float = 1.0,
    util_source: str = "ce_api",
    completeness: float = 1.0,
    trend_class: str = "stable",
    trend_audit: TrendAwareAudit | None = None,
    has_non_compute: bool = False,
):
    d_audit = DiscountAudit(
        d_raw=0.17,
        d_calibrated=0.17,
        e_sp=0.09,
        e_od=0.09,
        edp_uniform=True,
        calibration_applied=False,
        source="aws_public_pricing_api_weighted",
        pricing_cache_version="v1",
        pricing_cache_fetched_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        mix_window_days=30,
        coverage_pct=d_coverage,
        matched_entries=50,
        unmatched_entries=0,
        unmatched_top10_by_cost=[],
    )
    sp_audit = SPAudit(
        count=0,
        compute_count=0,
        non_compute_count=1 if has_non_compute else 0,
        total_commitment_all=0,
        compute_commitment=0,
        utilization_30d=util,
        utilization_source=util_source,
        effective=0,
        has_non_compute_sps=has_non_compute,
        active_sp_list=[],
    )
    stats = BaselineStats(
        sample_size_hours=int(90 * 24 * completeness),
        expected_sample_size_hours=90 * 24,
        completeness_pct=completeness,
        p0=0,
        p5=0,
        p10=0,
        p17=0,
        p25=0,
        p50=0,
        p75=0,
        p83=0,
        p90=0,
        p95=0,
        p100=0,
        mean=0,
        std=0,
        min=0,
        max=0,
    )
    return FinalResult(
        C_star_total=10.0,
        C_existing_effective=0.0,
        C_new_delta=10.0,
        d_blended=0.17,
        formula_branch="stationary",
        trend_classification=trend_class,
        trend_buckets=[_dummy_bucket()] * 3,
        trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None,
        non_monotonic_pattern=None,
        baseline_stats=stats,
        bootstrap_se=0.1,
        bootstrap_95ci=[9.5, 10.5],
        d_audit=d_audit,
        sp_audit=sp_audit,
        trend_aware_audit=trend_audit,
    )


def _args_for_assemble(final, window=None, context=None, validation=None, all_sps=None):
    if window is None:
        window = Window(end=datetime(2026, 4, 13, tzinfo=timezone.utc), days=90)
    if context is None:
        context = ValidationContext(
            caller_account_id="111111111111",
            org_id="o-abc",
            master_account_email="root@example.com",
            member_account_ids=["111111111111"],
            member_accounts_count=1,
            primary_region="us-east-1",
            cur_table_has_resource_tags_user_service=False,  # disables untagged check
        )
    if validation is None:
        validation = ValidationResult(context=context)
    cfg = None  # type: ignore — assemble_risks_and_warnings uses config only for untagged; we mock that
    return {
        "config": cfg,
        "validation": validation,
        "window_result": WindowSearchResult(status="success", chosen_window_risks=[]),
        "final": final,
        "all_relevant_sps": all_sps or [],
        "chosen_window": window,
        "pricing_cache_meta": PricingCacheMeta(
            version="v1",
            age_hours=10.0,
            built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
            regions=["us-east-1"],
        ),
    }


def test_happy_path_no_risks():
    risks, warnings = assemble_risks_and_warnings(**_args_for_assemble(_final()))
    assert risks == []
    # Task 12: EDP warning is always emitted — filter EDP codes to check no other warnings
    non_edp = [w for w in warnings if w["code"] not in ("edp_uniform_confirmed", "edp_differential_calibrated")]
    assert non_edp == []


def test_d_coverage_low_emits_risk():
    risks, _ = assemble_risks_and_warnings(**_args_for_assemble(_final(d_coverage=0.85)))
    assert any(r["code"] == "d_blended_coverage_low" for r in risks)


def test_d_coverage_medium_emits_risk_without_gate():
    risks, _ = assemble_risks_and_warnings(**_args_for_assemble(_final(d_coverage=0.95)))
    med_risk = next((r for r in risks if r["code"] == "d_blended_coverage_medium"), None)
    assert med_risk is not None
    assert med_risk["must_review_before_acting"] is False


def test_utilization_below_90_emits_risk():
    risks, _ = assemble_risks_and_warnings(**_args_for_assemble(_final(util=0.85)))
    assert any(r["code"] == "existing_sp_low_utilization" for r in risks)


def test_utilization_90_95_emits_warning():
    _, warnings = assemble_risks_and_warnings(**_args_for_assemble(_final(util=0.92)))
    assert any(w["code"] == "existing_sp_mild_low_utilization" for w in warnings)


def test_ce_fallback_emits_warning():
    _, warnings = assemble_risks_and_warnings(
        **_args_for_assemble(_final(util=1.0, util_source="fallback_assumed_100pct"))
    )
    assert any(w["code"] == "ce_api_unavailable_utilization_assumed_100" for w in warnings)


def test_mild_decline_emits_warning():
    _, warnings = assemble_risks_and_warnings(
        **_args_for_assemble(_final(trend_class="mild_decline"))
    )
    assert any(w["code"] == "mild_decline_ignored_in_formula" for w in warnings)


def test_sample_size_low_emits_risk():
    risks, _ = assemble_risks_and_warnings(**_args_for_assemble(_final(completeness=0.92)))
    assert any(r["code"] == "baseline_sample_size_low" for r in risks)


def test_pricing_cache_age_high_emits_warning():
    args = _args_for_assemble(_final())
    args["pricing_cache_meta"] = PricingCacheMeta(
        version="v1",
        age_hours=200.0,
        built_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        regions=["us-east-1"],
    )
    _, warnings = assemble_risks_and_warnings(**args)
    assert any(w["code"] == "pricing_cache_age_hours_high" for w in warnings)
