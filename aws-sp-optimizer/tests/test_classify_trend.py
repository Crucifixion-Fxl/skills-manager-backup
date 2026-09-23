"""Tests for classify_trend — exhaustive threshold coverage."""

from datetime import datetime, timezone

import pytest

from scripts._common import TrendBucket
from scripts.window_search import classify_trend


def _buckets(p17_values):
    now = datetime(2026, 4, 13, tzinfo=timezone.utc)
    return [
        TrendBucket(period_start=now, period_end=now, sample_size=480, p17=p, mean=p, min=p, max=p)
        for p in p17_values
    ]


def test_stable_exactly_flat():
    cls, ratio, swing = classify_trend(_buckets([10.0, 10.0, 10.0]))
    assert cls == "stable"
    assert ratio == pytest.approx(1.0)
    assert swing is None


def test_stable_slightly_below_1():
    """R22-1: ratio 0.99 is essentially flat and must be 'stable'."""
    cls, ratio, _ = classify_trend(_buckets([10.0, 10.0, 9.9]))
    assert cls == "stable"
    assert abs(ratio - 0.99) < 0.001


def test_stable_slightly_above_1():
    cls, _, _ = classify_trend(_buckets([10.0, 10.0, 10.1]))
    assert cls == "stable"


def test_increasing():
    cls, ratio, _ = classify_trend(_buckets([10.0, 11.0, 12.0]))
    assert cls == "increasing"
    assert ratio == pytest.approx(1.2)


def test_mild_decline():
    cls, ratio, _ = classify_trend(_buckets([10.0, 9.8, 9.6]))
    assert cls == "mild_decline"
    assert 0.95 <= ratio < 0.98


def test_moderate_decline():
    cls, ratio, _ = classify_trend(_buckets([10.0, 9.5, 9.0]))
    assert cls == "moderate_decline"
    assert 0.85 <= ratio < 0.95


def test_severe_decline():
    cls, ratio, _ = classify_trend(_buckets([10.0, 8.0, 6.0]))
    assert cls == "severe_decline"
    assert ratio < 0.85


def test_degenerate_b1_zero_is_severe_decline():
    cls, _, __ = classify_trend(_buckets([0.0, 5.0, 10.0]))
    assert cls == "severe_decline"


def test_non_monotonic_peak_in_middle():
    cls, _, swing = classify_trend(_buckets([10.0, 15.0, 10.0]))
    assert cls == "non_monotonic"
    assert swing is not None and swing > 0.1


def test_non_monotonic_sub_threshold_falls_to_monotonic():
    """8% swing < 10% threshold → classify by ratio."""
    cls, _, __ = classify_trend(_buckets([10.0, 10.6, 10.0]))  # 6% swing
    assert cls == "stable"
