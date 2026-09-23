"""Tests for make_risk factory — produces dicts matching Risk TS interface."""

import pytest

from scripts.output_builder import make_risk


def test_non_monotonic_oscillation_payload():
    risk = make_risk(
        "non_monotonic_oscillation",
        pattern="peak_in_middle",
        swing_pct=0.15,
        buckets_p17=[10.0, 15.0, 10.0],
        bucket_periods=[{"start": "2026-01-01", "end": "2026-01-30"}],
    )
    assert risk["code"] == "non_monotonic_oscillation"
    assert risk["severity"] == "high"
    assert risk["must_review_before_acting"] is True
    assert risk["details"]["pattern"] == "peak_in_middle"
    assert risk["details"]["swing_pct"] == pytest.approx(0.15)
    assert risk["user_facing_explanation"]  # non-empty Chinese explanation
    assert isinstance(risk["recommended_actions_for_llm"], list)


def test_d_blended_coverage_low():
    risk = make_risk(
        "d_blended_coverage_low",
        coverage_pct=0.85,
        matched_entries=50,
        unmatched_entries=20,
        unmatched_top10_by_cost=[{"key": "x", "cost": 100.0}],
    )
    assert risk["severity"] == "high"
    assert risk["must_review_before_acting"] is True
    assert risk["details"]["coverage_pct"] == pytest.approx(0.85)


def test_d_blended_coverage_medium_is_not_gate():
    risk = make_risk(
        "d_blended_coverage_medium",
        coverage_pct=0.95,
        matched_entries=50,
        unmatched_entries=5,
        unmatched_top10_by_cost=[],
    )
    assert risk["severity"] == "medium"
    assert risk["must_review_before_acting"] is False


def test_untagged_cost_fraction_high():
    risk = make_risk("untagged_cost_fraction_high", fraction=0.15, threshold=0.10)
    assert risk["severity"] == "low"
    assert risk["must_review_before_acting"] is False


def test_unknown_code_raises():
    with pytest.raises(ValueError):
        make_risk("nonexistent_code", foo="bar")
