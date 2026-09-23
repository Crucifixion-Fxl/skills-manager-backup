"""Tests for build_context and build_baseline."""

from datetime import datetime, timezone

import pytest

from scripts._common import (
    BaselineStats,
    DiscountAudit,
    FinalResult,
    OrgConfig,
    PricingCacheMeta,
    SPAudit,
    TrendBucket,
    ValidatedCandidate,
    ValidationContext,
    ValidationResult,
    Window,
)
from scripts.output_builder import build_baseline, build_context


def _ctx():
    return ValidationContext(
        caller_account_id="111111111111",
        org_id="o-abc",
        master_account_email="root@example.com",
        member_account_ids=["111111111111"],
        member_accounts_count=1,
        primary_region="us-east-1",
        cur_table_has_resource_tags_user_service=True,
    )


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
        window_end="today",
        prefer="balanced",
    )


def _final():
    stats = BaselineStats(
        sample_size_hours=2160,
        expected_sample_size_hours=2160,
        completeness_pct=1.0,
        p0=1.0,
        p5=2.0,
        p10=3.0,
        p17=4.0,
        p25=5.0,
        p50=10.0,
        p75=15.0,
        p83=16.0,
        p90=18.0,
        p95=19.0,
        p100=20.0,
        mean=10.0,
        std=2.0,
        min=1.0,
        max=20.0,
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
        coverage_pct=0.99,
        matched_entries=50,
        unmatched_entries=0,
        unmatched_top10_by_cost=[],
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
            period_start=now, period_end=now, sample_size=720, p17=4.0, mean=10.0, min=1.0, max=20.0
        )
        for _ in range(3)
    ]
    return FinalResult(
        C_star_total=4.0,
        C_existing_effective=0.0,
        C_new_delta=4.0,
        d_blended=0.17,
        formula_branch="stationary",
        trend_classification="stable",
        trend_buckets=buckets,
        trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None,
        non_monotonic_pattern=None,
        baseline_stats=stats,
        bootstrap_se=0.1,
        bootstrap_95ci=[3.9, 4.1],
        d_audit=d_audit,
        sp_audit=sp_audit,
        trend_aware_audit=None,
    )


def _chosen():
    w = Window(end=datetime(2026, 4, 13, tzinfo=timezone.utc), days=90)
    now = w.start
    return ValidatedCandidate(
        window=w,
        trend_classification="stable",
        trend_buckets=[
            TrendBucket(
                period_start=now,
                period_end=now,
                sample_size=720,
                p17=4.0,
                mean=10.0,
                min=1.0,
                max=20.0,
            )
        ]
        * 3,
        trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None,
        sample_size_hours=2160,
        completeness_pct=1.0,
        deviation_score=0,
    )


def test_build_context_fields():
    ctx = _ctx()
    cache_meta = PricingCacheMeta(
        version="v1",
        age_hours=12.3,
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        regions=["us-east-1"],
    )
    validation = ValidationResult(context=ctx)
    result = build_context(_cfg(), validation, _final(), cache_meta)
    assert result["account_id"] == "111111111111"
    assert result["org_alias"] == "a4x-us"
    assert result["discount_rate_used"] == pytest.approx(0.17)
    assert result["pricing_cache_version"] == "v1"
    assert result["pricing_cache_age_hours"] == pytest.approx(12.3)


def test_build_baseline_fields():
    final = _final()
    chosen = _chosen()
    requested = chosen.window
    result = build_baseline(final, chosen, requested)
    assert result["requested_window"] == {"end": requested.end.isoformat(), "days": 90}
    assert result["actual_window"] == {"end": chosen.window.end.isoformat(), "days": 90}
    assert result["raw_stats"]["p50"] == pytest.approx(10.0)
    assert result["trend_classification"] == "stable"
    assert len(result["trend_buckets"]) == 3
    # Task 12: d_blended_audit replaced by discount_audit with 11 fields
    assert "discount_audit" in result
    da = result["discount_audit"]
    assert da["d_raw"] == pytest.approx(0.17)
    assert da["d_calibrated"] == pytest.approx(0.17)
    assert da["e_sp"] == pytest.approx(0.09)
    assert da["e_od"] == pytest.approx(0.09)
    assert da["edp_uniform"] is True
    assert da["calibration_applied"] is False
    assert da["e_sp_inferred_from_e_od"] is False
    assert da["coverage_pct"] == pytest.approx(0.99)
    assert da["matched_entries"] == 50
    assert da["unmatched_entries"] == 0
    assert da["unmatched_top10_by_cost"] == []
