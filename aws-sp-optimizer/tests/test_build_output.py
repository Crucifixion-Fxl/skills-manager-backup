"""Integration-style tests for build_output using constructed FinalResult."""

from datetime import datetime, timezone

import pytest

from scripts._common import (
    BaselineStats,
    DiscountAudit,
    FinalResult,
    HourlySeries,
    OrgConfig,
    PricingCacheMeta,
    SPAudit,
    TrendBucket,
    ValidatedCandidate,
    ValidationContext,
    ValidationResult,
    Window,
    WindowSearchResult,
)
from scripts.output_builder import build_output


def _full_inputs(must_review: bool = False):
    cfg = OrgConfig(
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
        window_end="today",
        prefer="balanced",
    )
    ctx = ValidationContext(
        caller_account_id="111111111111",
        org_id="o-abc",
        master_account_email="root@example.com",
        member_account_ids=["111111111111"],
        member_accounts_count=1,
        primary_region="us-east-1",
        cur_table_has_resource_tags_user_service=False,
    )
    validation = ValidationResult(context=ctx)
    stats = BaselineStats(
        sample_size_hours=2160,
        expected_sample_size_hours=2160,
        completeness_pct=1.0,
        p0=0,
        p5=0,
        p10=0,
        p17=0,
        p25=0,
        p50=10.0,
        p75=0,
        p83=0,
        p90=0,
        p95=0,
        p100=0,
        mean=10.0,
        std=0,
        min=0,
        max=0,
    )
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
        coverage_pct=0.85 if must_review else 0.99,  # trigger d_blended_coverage_low
        matched_entries=50,
        unmatched_entries=10,
        unmatched_top10_by_cost=[{"key": "x", "cost": 50.0}] if must_review else [],
    )
    sp_audit = SPAudit(
        count=0,
        compute_count=0,
        non_compute_count=0,
        total_commitment_all=0,
        compute_commitment=0,
        utilization_30d=1.0,
        utilization_source="ce_api",
        effective=0,
        has_non_compute_sps=False,
        active_sp_list=[],
    )
    now = datetime(2026, 4, 13, tzinfo=timezone.utc)
    buckets = [
        TrendBucket(
            period_start=now, period_end=now, sample_size=720, p17=5.0, mean=10.0, min=1.0, max=20.0
        )
    ] * 3
    final = FinalResult(
        C_star_total=5.0,
        C_existing_effective=0.0,
        C_new_delta=5.0,
        d_blended=0.17,
        formula_branch="stationary",
        trend_classification="stable",
        trend_buckets=buckets,
        trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None,
        non_monotonic_pattern=None,
        baseline_stats=stats,
        bootstrap_se=0.1,
        bootstrap_95ci=[4.9, 5.1],
        d_audit=d_audit,
        sp_audit=sp_audit,
        trend_aware_audit=None,
    )
    window = Window(end=now, days=90)
    chosen = ValidatedCandidate(
        window=window,
        trend_classification="stable",
        trend_buckets=buckets,
        trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None,
        sample_size_hours=2160,
        completeness_pct=1.0,
        deviation_score=0,
    )
    search = WindowSearchResult(
        status="success",
        chosen=chosen,
        top_5=[chosen],
        search_summary={
            "total_candidates": 75,
            "passed": 75,
            "rejected_by_category": {},
            "top_5_alternatives": [],
        },
    )
    series = HourlySeries(hours=[], data={}, window_start=window.start, window_end=window.end)
    pcm = PricingCacheMeta(
        version="v1",
        age_hours=12.0,
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        regions=["us-east-1"],
    )
    return cfg, validation, final, search, series, pcm, window


def test_happy_path_status_ok_with_purchase_instructions():
    cfg, validation, final, search, _, pcm, window = _full_inputs()
    output = build_output(
        config=cfg,
        validation=validation,
        final=final,
        search=search,
        all_relevant_sps=[],
        requested_window=window,
        pricing_cache_meta=pcm,
    )
    assert output["status"] == "ok"
    assert output["purchase_instructions"] is not None
    assert output["recommendation"]["must_review_before_acting"] is False
    assert output["recommendation"]["delta_per_hour_to_buy"] == pytest.approx(5.0)
    # INV-7: annual ≈ delta * 8760
    assert abs(output["recommendation"]["annual_delta_usd"] - 5.0 * 8760) < 0.02


def test_must_review_gates_purchase_instructions_to_null():
    """INV-5: must_review_before_acting ⇔ purchase_instructions == null."""
    cfg, validation, final, search, _, pcm, window = _full_inputs(must_review=True)
    output = build_output(
        config=cfg,
        validation=validation,
        final=final,
        search=search,
        all_relevant_sps=[],
        requested_window=window,
        pricing_cache_meta=pcm,
    )
    assert output["status"] == "ok"
    assert output["recommendation"]["must_review_before_acting"] is True
    assert output["purchase_instructions"] is None


def test_ok_with_adjustment_includes_adjustments_made():
    cfg, validation, final, search, _, pcm, window = _full_inputs()
    search.adjustments_made = [
        {
            "reason": "sp_transition_in_window",
            "action": "slid window back",
            "trade_off": {"freshness_loss_days": 3, "duration_loss_days": 0, "deviation_score": 3},
            "requested_window": {"end": window.end.isoformat(), "days": 90},
            "actual_window": {"end": window.end.isoformat(), "days": 90},
        }
    ]
    output = build_output(
        config=cfg,
        validation=validation,
        final=final,
        search=search,
        all_relevant_sps=[],
        requested_window=window,
        pricing_cache_meta=pcm,
    )
    assert output["status"] == "ok_with_adjustment"
    assert "adjustments_made" in output
    assert output["adjustments_made"]


def test_context_applied_exclude_filter_is_none_when_filter_empty():
    """Empty filter ⇒ context['applied_exclude_filter'] is None, coverage_scope unchanged."""
    from scripts.exclude_filter import ExcludeFilter

    cfg, validation, final, search, _, pcm, window = _full_inputs()
    output = build_output(
        config=cfg,
        validation=validation,
        final=final,
        search=search,
        all_relevant_sps=[],
        requested_window=window,
        pricing_cache_meta=pcm,
        discovered_regions=[],
        exclude_filter=ExcludeFilter(),
        available_tag_columns=frozenset(),
        sp_coverage_share=1.0,
        sp_share_audit={"note": "filter empty; share short-circuited to 1.0"},
    )
    assert output["context"]["applied_exclude_filter"] is None
    assert "All EC2 BoxUsage" in output["purchase_instructions"]["summary"]["coverage_scope"]
    assert "filtered:" not in output["purchase_instructions"]["summary"]["coverage_scope"]


def test_context_applied_exclude_filter_populated_with_axes():
    """Non-empty filter ⇒ context['applied_exclude_filter'] has all 4 axes + share + audit + warnings."""
    from scripts.exclude_filter import ExcludeFilter, TagExclusion

    cfg, validation, final, search, _, pcm, window = _full_inputs()
    flt = ExcludeFilter(
        usage_type_patterns=("g4dn.",),
        account_ids=("123456789012",),
        tag_exclusions=(TagExclusion(key="lifecycle", values=("ephemeral",)),),
        include_untagged=False,
    )
    output = build_output(
        config=cfg,
        validation=validation,
        final=final,
        search=search,
        all_relevant_sps=[],
        requested_window=window,
        pricing_cache_meta=pcm,
        discovered_regions=[],
        exclude_filter=flt,
        # empty available_tag_columns → tag clause skipped → warning emitted
        available_tag_columns=frozenset(),
        sp_coverage_share=0.6,
        sp_share_audit={
            "retained_cost_usd": 600.0,
            "total_cost_usd": 1000.0,
            "note": "share = retained / total",
        },
    )
    aef = output["context"]["applied_exclude_filter"]
    assert aef["usage_type_patterns"] == ["g4dn."]
    assert aef["account_ids"] == ["123456789012"]
    assert aef["tag_exclusions"] == [{"key": "lifecycle", "values": ["ephemeral"]}]
    assert aef["include_untagged"] is False
    assert aef["sp_coverage_share"] == pytest.approx(0.6)
    assert aef["sp_coverage_share_audit"]["retained_cost_usd"] == pytest.approx(600.0)
    assert len(aef["skipped_filter_warnings"]) >= 1  # tag column missing warning
    # Coverage scope reflects filter
    scope = output["purchase_instructions"]["summary"]["coverage_scope"]
    assert "filtered:" in scope
    assert "g4dn." in scope
    assert "123456789012" in scope
    assert "lifecycle=ephemeral" in scope
