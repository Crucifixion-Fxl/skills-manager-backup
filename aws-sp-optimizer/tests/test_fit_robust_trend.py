"""Tests for fit_robust_trend — recovers slope on synthetic data."""

import numpy as np

from scripts.newsvendor import fit_robust_trend


def test_recovers_linear_slope():
    n = 720  # 30 days × 24 hours
    timestamps = np.array([i * 3600 for i in range(n)], dtype=float)  # posix-like
    true_slope = 0.01  # per hour
    X = 10.0 + true_slope * np.arange(n)
    trend = fit_robust_trend(X, timestamps)
    # statsmodels RLM uses POSIX-seconds, so slope is per-second
    # 0.01 per hour = 0.01 / 3600 per second ≈ 2.78e-6
    assert abs(trend.slope - 0.01 / 3600) < 1e-7
    assert trend.r2 > 0.99


def test_handles_outliers_robustly():
    n = 720
    timestamps = np.array([i * 3600 for i in range(n)], dtype=float)
    X = 10.0 + 0.01 * np.arange(n)
    # Inject 5% outliers
    rng = np.random.default_rng(42)
    idx = rng.choice(n, size=n // 20, replace=False)
    X[idx] += 500
    trend = fit_robust_trend(X, timestamps)
    # Robust fit should ignore outliers — slope still close to 0.01/3600
    assert abs(trend.slope - 0.01 / 3600) < 5e-7


def test_flat_distribution_slope_zero():
    n = 720
    timestamps = np.array([i * 3600 for i in range(n)], dtype=float)
    X = np.full(n, 10.0)
    trend = fit_robust_trend(X, timestamps)
    assert abs(trend.slope) < 1e-10
