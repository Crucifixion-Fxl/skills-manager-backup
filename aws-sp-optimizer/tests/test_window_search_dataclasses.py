from datetime import datetime, timezone

from scripts._common import (
    RejectedCandidate,
    ValidatedCandidate,
    Window,
    WindowSearchResult,
)


def test_validated_candidate_fields():
    w = Window(end=datetime(2026, 4, 13, tzinfo=timezone.utc), days=90)
    c = ValidatedCandidate(
        window=w,
        trend_classification="stable",
        trend_buckets=[],
        trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None,
        sample_size_hours=2160,
        completeness_pct=1.0,
        deviation_score=0,
    )
    assert c.trend_classification == "stable"


def test_rejected_candidate_defaults():
    w = Window(end=datetime(2026, 4, 13, tzinfo=timezone.utc), days=90)
    r = RejectedCandidate(window=w, reason="severe_decline")
    assert r.b3_b1_ratio is None
    assert r.transition_events is None


def test_window_search_result_defaults():
    result = WindowSearchResult(status="failed")
    assert result.chosen is None
    assert result.top_5 == []
