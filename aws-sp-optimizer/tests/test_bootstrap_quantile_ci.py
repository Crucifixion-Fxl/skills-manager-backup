"""Tests for bootstrap_quantile_ci — degenerate case + sanity ordering."""

import numpy as np
import pytest

from scripts.newsvendor import bootstrap_quantile_ci, newsvendor_stationary


def test_constant_x_degenerate_zero_se():
    """When X is all one value, every bootstrap returns the same quantile.
    SE=0, CI=[v, v]. Must NOT collapse to None."""
    X = np.full(1296, 5.0)
    se, lo, hi = bootstrap_quantile_ci(X, 0.17)
    assert se == pytest.approx(0.0, abs=1e-9)
    assert lo == pytest.approx(5.0, abs=1e-9)
    assert hi == pytest.approx(5.0, abs=1e-9)


def test_ci_brackets_point_estimate():
    rng = np.random.default_rng(0)
    X = rng.uniform(1.0, 100.0, 2160)
    C = newsvendor_stationary(X, 0.17)
    se, lo, hi = bootstrap_quantile_ci(X, 0.17, n_boot=200)
    assert se > 0
    assert lo <= C <= hi


def test_deterministic_with_seed():
    rng = np.random.default_rng(0)
    X = rng.uniform(1.0, 100.0, 2160)
    a = bootstrap_quantile_ci(X, 0.17, n_boot=100, seed=42)
    b = bootstrap_quantile_ci(X, 0.17, n_boot=100, seed=42)
    assert a == b
