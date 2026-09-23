import pytest

from scripts.newsvendor import choose_formula_branch


def test_stable_uses_stationary():
    assert choose_formula_branch("stable") == "stationary"


def test_increasing_uses_stationary():
    assert choose_formula_branch("increasing") == "stationary"


def test_mild_decline_uses_stationary():
    assert choose_formula_branch("mild_decline") == "stationary"


def test_moderate_decline_uses_trend_aware():
    assert choose_formula_branch("moderate_decline") == "trend_aware"


def test_non_monotonic_uses_stationary():
    assert choose_formula_branch("non_monotonic") == "stationary"


def test_severe_decline_raises():
    with pytest.raises(AssertionError):
        choose_formula_branch("severe_decline")
