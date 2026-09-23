"""Tests for compute_trend_buckets — splits X into 3 even segments."""

from datetime import datetime, timezone

import numpy as np

from scripts._common import Window
from scripts.window_search import compute_trend_buckets


def _w(days: int = 60) -> Window:
    return Window(end=datetime(2026, 4, 13, tzinfo=timezone.utc), days=days)


def test_three_equal_buckets():
    X = np.arange(1440, dtype=float)  # 60 days × 24 hours
    buckets = compute_trend_buckets(X, _w(60))
    assert len(buckets) == 3
    assert buckets[0].sample_size == 480
    assert buckets[1].sample_size == 480
    assert buckets[2].sample_size == 480


def test_monotonic_data_bucket_p17_increases():
    X = np.arange(1440, dtype=float)
    buckets = compute_trend_buckets(X, _w(60))
    assert buckets[0].p17 < buckets[1].p17 < buckets[2].p17


def test_period_boundaries_span_window():
    X = np.full(1440, 5.0)
    w = _w(60)
    buckets = compute_trend_buckets(X, w)
    assert buckets[0].period_start == w.start
    assert buckets[2].period_end == w.end
