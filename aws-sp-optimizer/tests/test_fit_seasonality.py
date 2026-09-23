"""Tests for fit_seasonality — 168 hour-of-week dummies via OLS."""

from datetime import datetime, timedelta, timezone

import numpy as np

from scripts.newsvendor import fit_seasonality


def test_captures_weekly_pattern():
    """Construct a signal with strong weekly seasonality and verify R² is high."""
    n_hours = 14 * 24  # 2 weeks
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)  # Monday
    timestamps = np.array(
        [(start + timedelta(hours=i)).timestamp() for i in range(n_hours)], dtype=float
    )
    # Pattern: hour-of-week 8-18 on weekdays is high, rest is low
    residual = np.zeros(n_hours)
    for i in range(n_hours):
        dt = start + timedelta(hours=i)
        weekday = dt.weekday()  # 0=Mon .. 6=Sun
        hour = dt.hour
        if weekday < 5 and 8 <= hour <= 18:
            residual[i] = 5.0
    # Add small noise
    rng = np.random.default_rng(42)
    residual += rng.normal(0, 0.1, n_hours)

    season = fit_seasonality(residual, timestamps)
    assert season.r2 > 0.95


def test_flat_residual_zero_r2():
    n_hours = 7 * 24
    timestamps = np.array([i * 3600 for i in range(n_hours)], dtype=float)
    residual = np.zeros(n_hours)
    season = fit_seasonality(residual, timestamps)
    # All zeros → SS_tot = 0 → r2 defined as 0 (or 1 depending on convention); allow either
    assert season.r2 in (0.0, 1.0)
