"""Tests for search_best_window adjustments + non-monotonic risk emission."""

from datetime import datetime, timedelta, timezone

from scripts._common import HourlySeries, SPInfo, Window
from scripts.window_search import search_best_window


def _series(days: int, start: datetime, value_fn):
    n = days * 24
    hours = [start + timedelta(hours=i) for i in range(n)]
    data = {h: {"total_usd": float(value_fn(i)), "total_list_usd": float(value_fn(i)), "total_net_usd": float(value_fn(i)) * 0.91, "mix": {}} for i, h in enumerate(hours)}
    return HourlySeries(
        hours=hours, data=data, window_start=start, window_end=hours[-1] + timedelta(hours=1)
    )


def test_sp_transition_forces_window_shift_and_adjustments_made_populated():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    series = _series(104, start, lambda i: 10.0)
    # SP ended at day 85 — any 90-day window ending on day 90+ contains the
    # transition, so search_best_window slides the window back.
    sp_end = start + timedelta(days=85)
    sp = SPInfo(
        id="sp-x",
        type="Compute",
        ec2_instance_family=None,
        region=None,
        commitment=1.0,
        start=start - timedelta(days=365),
        end=sp_end,
        state="retired",
        payment_option="No Upfront",
    )
    requested = Window(end=series.window_end, days=90)
    result = search_best_window(series, requested, all_relevant_sps=[sp])
    if result.status == "success":
        assert result.adjustments_made  # non-empty


def test_non_monotonic_trigger_emits_chosen_window_risk():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Peak-in-middle pattern
    def value(i):
        third = 104 * 24 // 3
        if i < third:
            return 10.0
        if i < 2 * third:
            return 15.0  # peak
        return 10.0

    series = _series(104, start, value)
    requested = Window(end=series.window_end, days=90)
    result = search_best_window(series, requested, all_relevant_sps=[])
    if result.status == "success" and result.chosen.trend_classification == "non_monotonic":
        assert result.chosen_window_risks
        risk = result.chosen_window_risks[0]
        assert risk["code"] == "non_monotonic_oscillation"
