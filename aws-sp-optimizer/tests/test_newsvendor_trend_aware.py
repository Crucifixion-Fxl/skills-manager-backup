"""Tests for newsvendor_trend_aware — degenerate cases + slope recovery."""

from datetime import datetime, timedelta, timezone

import numpy as np

from scripts.newsvendor import newsvendor_stationary, newsvendor_trend_aware


def _timestamps_for(n_hours: int):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return np.array([(start + timedelta(hours=i)).timestamp() for i in range(n_hours)], dtype=float)


def test_zero_slope_matches_stationary_within_2pct():
    """A zero-slope X should produce a trend_aware C* close to stationary."""
    rng = np.random.default_rng(42)
    n = 2160
    X = rng.uniform(5.0, 15.0, n)
    ts = _timestamps_for(n)
    d = 0.17

    stat_c = newsvendor_stationary(X, d)
    trend_c, _ = newsvendor_trend_aware(X, ts, d)

    assert abs(trend_c - stat_c) / stat_c < 0.05  # within 5% (loose due to bisection precision)


def test_declining_workload_trend_c_below_stationary():
    """A moderate decline should push trend_aware below stationary (it expects less future)."""
    n = 2160
    ts = _timestamps_for(n)
    trend_slope = -2.0 / n  # total 2-unit decline
    rng = np.random.default_rng(42)
    X = 10.0 + trend_slope * np.arange(n) + rng.normal(0, 0.5, n)
    d = 0.17

    stat_c = newsvendor_stationary(X, d)
    trend_c, audit = newsvendor_trend_aware(X, ts, d)

    assert trend_c < stat_c
    assert audit.slope_per_hour < 0
    assert audit.delta_pct_vs_stationary < 0


def test_audit_populated():
    n = 2160
    ts = _timestamps_for(n)
    X = np.full(n, 10.0) + np.random.default_rng(0).normal(0, 0.1, n)
    _, audit = newsvendor_trend_aware(X, ts, 0.17)
    assert audit.horizon_hours == 8760
    assert audit.min_future_mean_floor > 0
    assert audit.fraction_of_horizon_clamped >= 0
    assert audit.stationary_C_star_comparison > 0
