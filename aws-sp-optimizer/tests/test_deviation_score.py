from datetime import datetime, timedelta, timezone

from scripts._common import Window
from scripts.window_search import compute_deviation_score


def test_exact_match_is_zero():
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    req = Window(end=end, days=90)
    assert compute_deviation_score(req, req, "balanced") == 0


def test_freshness_loss_weighted_higher_under_freshness_prefer():
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    req = Window(end=end, days=90)
    cand = Window(end=end - timedelta(days=3), days=90)  # 3-day freshness loss
    assert compute_deviation_score(cand, req, "freshness") == 9  # 3*3
    assert compute_deviation_score(cand, req, "balanced") == 3
    assert compute_deviation_score(cand, req, "sample-size") == 3


def test_duration_loss_weighted_under_sample_size():
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    req = Window(end=end, days=90)
    cand = Window(end=end, days=60)  # 30-day duration loss
    assert compute_deviation_score(cand, req, "sample-size") == 90  # 30*3
    assert compute_deviation_score(cand, req, "balanced") == 30
