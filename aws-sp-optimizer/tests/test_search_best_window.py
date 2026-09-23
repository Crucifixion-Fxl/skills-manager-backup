"""Tests for search_best_window — the big one. Uses synthetic HourlySeries."""

from datetime import datetime, timedelta, timezone

import numpy as np

from scripts._common import HourlySeries, SPInfo, Window
from scripts.window_search import search_best_window


def _series(days: int, start: datetime, value_fn):
    """Build a synthetic HourlySeries where data[h]['total_usd'] = value_fn(i)."""
    n = days * 24
    hours = [start + timedelta(hours=i) for i in range(n)]
    data = {h: {"total_usd": float(value_fn(i)), "total_list_usd": float(value_fn(i)), "total_net_usd": float(value_fn(i)) * 0.91, "mix": {}} for i, h in enumerate(hours)}
    return HourlySeries(
        hours=hours, data=data, window_start=start, window_end=hours[-1] + timedelta(hours=1)
    )


def test_all_stable_happy_path_returns_requested_window():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = _series(104, start, lambda i: 10.0 + 0.1 * np.random.default_rng(0).normal())
    requested = Window(end=series.window_end, days=90)
    result = search_best_window(series, requested, all_relevant_sps=[])
    assert result.status == "success"
    assert result.chosen is not None
    assert result.chosen.window.end == requested.end
    assert result.chosen.window.days == 90


def test_severe_decline_rejects_all():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Linear decline from 10 → 1 spanning ~100 days (0.09 per day = 0.09/24 per hour).
    # i is the hour index; slope 0.09/24 keeps values declining across all 75 candidate
    # windows so that every window sees b3/b1 far below 0.85 → severe_decline.
    series = _series(104, start, lambda i: max(1.0, 10.0 - 0.09 / 24 * i))
    requested = Window(end=series.window_end, days=90)
    result = search_best_window(series, requested, all_relevant_sps=[])
    assert result.status == "failed"
    assert result.failure_reason == "no_viable_window"
    assert "severe_decline" in (result.rejection_summary or {})


def test_sp_transition_rejects_affected_candidates():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = _series(104, start, lambda i: 10.0)
    # SP that ENDED in the middle of the MAX window → every candidate containing
    # that timestamp is rejected.
    mid_ts = start + timedelta(days=52)
    sp = SPInfo(
        id="sp-x",
        type="Compute",
        ec2_instance_family=None,
        region=None,
        commitment=1.0,
        start=start - timedelta(days=365),
        end=mid_ts,
        state="retired",
        payment_option="No Upfront",
    )
    requested = Window(end=series.window_end, days=90)
    result = search_best_window(series, requested, all_relevant_sps=[sp])
    # Some candidates will pass (those whose window is entirely before or after mid_ts);
    # regardless of success/failure, sp_transition_in_window must appear in rejections.
    assert "sp_transition_in_window" in (result.rejection_summary or {})


def test_candidates_enumerated_correctly():
    """15 window_end options × 5 window_days = 75 candidates total."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = _series(104, start, lambda i: 10.0)
    requested = Window(end=series.window_end, days=90)
    result = search_best_window(series, requested, all_relevant_sps=[])
    assert result.search_summary is not None
    assert result.search_summary["total_candidates"] == 75
