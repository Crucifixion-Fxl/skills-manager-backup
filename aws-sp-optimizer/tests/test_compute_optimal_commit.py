"""Tests for compute_optimal_commit — ties together 5.2/5.3/5.7/5.8."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pytest

from scripts._common import (
    HourlySeries,
    RatioEntry,
    Ratios,
    SPInfo,
    ValidatedCandidate,
    Window,
)
from scripts.newsvendor import compute_optimal_commit
from scripts.window_search import compute_trend_buckets


def _ratios_with_many_entries() -> Ratios:
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


def _series_with_recent_mix(days: int, value: float):
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    start = end - timedelta(days=days)
    n = days * 24
    hours = [start + timedelta(hours=i) for i in range(n)]
    data = {h: {"total_usd": value, "total_list_usd": value, "total_net_usd": value * 0.91, "mix": {}} for h in hours}
    # Put mix on the final hour (within recent 30d) so compute_d_blended sees it
    recent_hour = hours[-1]
    data[recent_hour]["mix"] = {
        ("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{i}", f"U{i}"): 1.0 for i in range(15)
    }
    return HourlySeries(hours=hours, data=data, window_start=start, window_end=end)


def test_stationary_branch_produces_final_result():
    series = _series_with_recent_mix(90, 10.0)
    ratios = _ratios_with_many_entries()
    chosen_window = Window(end=series.window_end, days=90)
    buckets = compute_trend_buckets(np.array([10.0] * (90 * 24)), chosen_window)
    chosen = ValidatedCandidate(
        window=chosen_window,
        trend_classification="stable",
        trend_buckets=buckets,
        trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None,
        sample_size_hours=90 * 24,
        completeness_pct=1.0,
        deviation_score=0,
    )

    with patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce:
        mock_ce.return_value = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
        final = compute_optimal_commit(
            profile="prof",
            series=series,
            ratios=ratios,
            all_relevant_sps=[],
            chosen=chosen,
            e_sp=0.09,
            e_od=0.09,
        )

    assert final.formula_branch == "stationary"
    assert final.C_new_delta >= 0
    assert final.C_star_total > 0
    assert final.d_blended > 0
    assert final.bootstrap_95ci is not None
    assert final.baseline_stats.sample_size_hours == 90 * 24
    assert final.trend_aware_audit is None


def test_existing_sp_reduces_delta():
    series = _series_with_recent_mix(90, 10.0)
    ratios = _ratios_with_many_entries()
    chosen_window = Window(end=series.window_end, days=90)
    buckets = compute_trend_buckets(np.array([10.0] * (90 * 24)), chosen_window)
    chosen = ValidatedCandidate(
        window=chosen_window,
        trend_classification="stable",
        trend_buckets=buckets,
        trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None,
        sample_size_hours=90 * 24,
        completeness_pct=1.0,
        deviation_score=0,
    )
    existing_sp = SPInfo(
        id="sp-x",
        type="Compute",
        ec2_instance_family=None,
        region=None,
        commitment=100.0,  # already way over the optimal
        start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end=datetime(2027, 1, 1, tzinfo=timezone.utc),
        state="active",
        payment_option="No Upfront",
    )
    with patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce:
        mock_ce.return_value = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
        final = compute_optimal_commit(
            profile="prof",
            series=series,
            ratios=ratios,
            all_relevant_sps=[existing_sp],
            chosen=chosen,
            e_sp=0.09,
            e_od=0.09,
        )
    assert final.C_new_delta == pytest.approx(0.0, abs=1e-9)  # clamped at zero


def test_moderate_decline_exercises_trend_aware_branch():
    """Wiring test: a moderate_decline chosen candidate must route through
    the trend_aware branch inside compute_optimal_commit and populate
    FinalResult.trend_aware_audit (non-None)."""
    # Build a declining series: b3/b1 ≈ 0.90 (inside moderate_decline band)
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    start = end - timedelta(days=90)
    n = 90 * 24
    hours = [start + timedelta(hours=i) for i in range(n)]
    data = {}
    for i, h in enumerate(hours):
        # Linear decline from 11 → 9 (roughly -18% over window → b3/b1 ≈ 0.85–0.90)
        value = 11.0 - (2.0 / n) * i
        data[h] = {"total_usd": value, "total_list_usd": value, "total_net_usd": value * 0.91, "mix": {}}
    # Put mix on recent 30d so compute_d_blended has enough matches
    for h in hours[-30 * 24 :]:
        data[h]["mix"] = {
            ("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{j}", f"U{j}"): 1.0 for j in range(15)
        }
    series = HourlySeries(hours=hours, data=data, window_start=start, window_end=end)
    ratios = _ratios_with_many_entries()

    X = np.array([data[h]["total_net_usd"] for h in hours])
    chosen_window = Window(end=end, days=90)
    buckets = compute_trend_buckets(X, chosen_window)
    chosen = ValidatedCandidate(
        window=chosen_window,
        trend_classification="moderate_decline",
        trend_buckets=buckets,
        trend_b3_over_b1_ratio=buckets[2].p17 / buckets[0].p17,
        non_monotonic_swing_pct=None,
        sample_size_hours=n,
        completeness_pct=1.0,
        deviation_score=0,
    )

    with patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce:
        mock_ce.return_value = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
        final = compute_optimal_commit(
            profile="prof",
            series=series,
            ratios=ratios,
            all_relevant_sps=[],
            chosen=chosen,
            e_sp=0.09,
            e_od=0.09,
        )

    assert final.formula_branch == "trend_aware"
    assert final.trend_aware_audit is not None
    assert final.trend_aware_audit.slope_per_hour < 0  # declining
    # Stationary branch bootstrap CI should be None for trend_aware
    assert final.bootstrap_se is None
    assert final.bootstrap_95ci is None
