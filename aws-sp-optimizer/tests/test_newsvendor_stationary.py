"""Strict-TDD tests for newsvendor_stationary. Property-based + unit."""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis.strategies import floats, integers

from scripts._common import NoDataError
from scripts.newsvendor import newsvendor_stationary


def _flat(n: int, value: float = 10.0) -> np.ndarray:
    return np.full(n, value, dtype=float)


def test_boundary_d_zero_raises_value_error():
    with pytest.raises(ValueError):
        newsvendor_stationary(_flat(1296), 0.0)


def test_boundary_d_one_raises_value_error():
    with pytest.raises(ValueError):
        newsvendor_stationary(_flat(1296), 1.0)


def test_below_floor_raises_insufficient_data():
    with pytest.raises(NoDataError) as excinfo:
        newsvendor_stationary(_flat(1295), 0.17)
    assert "1296" in excinfo.value.message or "1296" in str(excinfo.value.message)


def test_flat_distribution_returns_the_flat_value():
    X = _flat(1296, 5.0)
    assert newsvendor_stationary(X, 0.5) == pytest.approx(5.0)


def test_median_matches_np_median_at_d_0_5():
    rng = np.random.default_rng(42)
    X = rng.uniform(1.0, 100.0, 2160)
    C = newsvendor_stationary(X, 0.5)
    assert abs(C - np.median(X)) < 1e-9


@settings(max_examples=25, deadline=None)
@given(
    seed=integers(min_value=0, max_value=2**31 - 1),
    n=integers(min_value=1296, max_value=2160),
    d=floats(0.01, 0.99),
)
def test_newsvendor_stationary_properties(seed, n, d):
    """Property: C* is in [X.min, X.max] and empirical CDF at C is ≈ d."""
    rng = np.random.default_rng(seed)
    x_arr = rng.uniform(0.1, 1000.0, size=n)
    C = newsvendor_stationary(x_arr, d)
    # Result is within the data range
    assert x_arr.min() <= C <= x_arr.max()
    # Empirical CDF at C is close to d (within 1/n + float drift)
    empirical_cdf = float(np.mean(x_arr <= C))
    assert abs(empirical_cdf - d) < 1 / n + 1e-6
    # At d=0.5 the result is the median
    if abs(d - 0.5) < 0.001:
        assert abs(C - float(np.median(x_arr))) < 1e-6
