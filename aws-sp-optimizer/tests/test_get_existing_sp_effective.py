"""Tests for get_existing_sp_effective — active-only filtering, CE fallback."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from scripts._common import SPInfo
from scripts.newsvendor import get_existing_sp_effective


def _compute_sp(id_: str, commitment: float, state: str = "active") -> SPInfo:
    return SPInfo(
        id=id_,
        type="Compute",
        ec2_instance_family=None,
        region=None,
        commitment=commitment,
        start=datetime(2025, 10, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
        state=state,
        payment_option="No Upfront",
    )


def _ec2_sp() -> SPInfo:
    return SPInfo(
        id="sp-ec2",
        type="EC2Instance",
        ec2_instance_family="c5",
        region="us-east-1",
        commitment=2.0,
        start=datetime(2025, 10, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
        state="active",
        payment_option="No Upfront",
    )


def test_compute_only_happy_path():
    sps = [_compute_sp("sp-a", 3.0), _compute_sp("sp-b", 2.0)]
    fake_ce = {
        "Total": {"Utilization": {"UtilizationPercentage": "95.0", "UsedCommitment": "4.75"}}
    }
    window_end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    with patch("scripts.newsvendor.ce_get_savings_plans_utilization", return_value=fake_ce):
        effective, audit = get_existing_sp_effective("prof", sps, window_end)
    assert audit.compute_count == 2
    assert audit.compute_commitment == pytest.approx(5.0)
    assert audit.utilization_30d == pytest.approx(0.95)
    assert audit.utilization_source == "ce_api"
    assert effective == pytest.approx(5.0 * 0.95)
    assert not audit.has_non_compute_sps


def test_ce_failure_falls_back_to_100pct():
    sps = [_compute_sp("sp-a", 3.0)]
    with patch(
        "scripts.newsvendor.ce_get_savings_plans_utilization",
        side_effect=RuntimeError("CE down"),
    ):
        effective, audit = get_existing_sp_effective(
            "prof", sps, datetime(2026, 4, 13, tzinfo=timezone.utc)
        )
    assert audit.utilization_source == "fallback_assumed_100pct"
    assert audit.utilization_30d == pytest.approx(1.0)
    assert effective == pytest.approx(3.0)


def test_non_compute_sps_tracked_but_not_counted():
    sps = [_compute_sp("sp-a", 3.0), _ec2_sp()]
    fake_ce = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
    with patch("scripts.newsvendor.ce_get_savings_plans_utilization", return_value=fake_ce):
        effective, audit = get_existing_sp_effective(
            "prof", sps, datetime(2026, 4, 13, tzinfo=timezone.utc)
        )
    assert audit.count == 2
    assert audit.compute_count == 1
    assert audit.non_compute_count == 1
    assert audit.has_non_compute_sps
    assert effective == pytest.approx(3.0)  # EC2 SP not counted
    # active_sp_list includes both, with different counted_toward_gap
    compute_entry = next(e for e in audit.active_sp_list if e["savings_plan_id"] == "sp-a")
    ec2_entry = next(e for e in audit.active_sp_list if e["savings_plan_id"] == "sp-ec2")
    assert compute_entry["counted_toward_gap"] is True
    assert ec2_entry["counted_toward_gap"] is False
    assert ec2_entry["effective_coverage_per_hour"] == pytest.approx(0.0)


def test_date_params_passed_as_yyyy_mm_dd():
    """R34-2: CE API requires YYYY-MM-DD strings, not ISO timestamps."""
    sps = [_compute_sp("sp-a", 1.0)]
    fake_ce = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
    captured = {}

    def capture(profile, start, end, granularity="DAILY"):
        captured["start"] = start
        captured["end"] = end
        return fake_ce

    with patch("scripts.newsvendor.ce_get_savings_plans_utilization", side_effect=capture):
        get_existing_sp_effective("prof", sps, datetime(2026, 4, 13, 10, 30, tzinfo=timezone.utc))

    import re

    assert re.match(r"^\d{4}-\d{2}-\d{2}$", captured["start"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", captured["end"])
    assert "T" not in captured["start"]


def test_sp_coverage_share_attenuates_effective():
    """effective = compute_commitment × utilization × share."""
    sps = [_compute_sp("sp-a", 10.0)]
    fake_ce = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
    window_end = datetime(2026, 4, 10, tzinfo=timezone.utc)
    with patch("scripts.newsvendor.ce_get_savings_plans_utilization", return_value=fake_ce):
        effective, audit = get_existing_sp_effective(
            profile="prof",
            all_relevant_sps=sps,
            window_end=window_end,
            sp_coverage_share=0.6,
        )
    assert effective == pytest.approx(6.0)  # 10 × 1.0 × 0.6
    assert audit.sp_coverage_share == pytest.approx(0.6)


def test_sp_coverage_share_default_is_one():
    """No arg ⇒ share defaults to 1.0, behavior identical to pre-filter."""
    sps = [_compute_sp("sp-a", 10.0)]
    fake_ce = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
    window_end = datetime(2026, 4, 10, tzinfo=timezone.utc)
    with patch("scripts.newsvendor.ce_get_savings_plans_utilization", return_value=fake_ce):
        effective, audit = get_existing_sp_effective(
            profile="prof", all_relevant_sps=sps, window_end=window_end,
        )
    assert effective == pytest.approx(10.0)
    assert audit.sp_coverage_share == pytest.approx(1.0)


def test_sp_coverage_share_zero_gives_zero_effective():
    """share=0.0 ⇒ all SP-covered workload excluded; effective drops to zero.

    Guards against a future refactor that accidentally uses `sp_coverage_share or 1.0`
    which would coerce 0.0 to 1.0 (falsy-default footgun).
    """
    sps = [_compute_sp("sp-a", 10.0)]
    fake_ce = {"Total": {"Utilization": {"UtilizationPercentage": "100.0"}}}
    window_end = datetime(2026, 4, 10, tzinfo=timezone.utc)
    with patch("scripts.newsvendor.ce_get_savings_plans_utilization", return_value=fake_ce):
        effective, audit = get_existing_sp_effective(
            profile="prof",
            all_relevant_sps=sps,
            window_end=window_end,
            sp_coverage_share=0.0,
        )
    assert effective == pytest.approx(0.0)
    assert audit.sp_coverage_share == pytest.approx(0.0)
