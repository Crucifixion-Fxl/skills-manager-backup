"""Tests for fetch_all_relevant_sps — active + recently retired."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scripts._common import SPInfo, fetch_all_relevant_sps


def _sp(id_: str, state: str, end: datetime) -> SPInfo:
    return SPInfo(
        id=id_,
        type="Compute",
        ec2_instance_family=None,
        region=None,
        commitment=5.0,
        start=end - timedelta(days=365),
        end=end,
        state=state,
        payment_option="No Upfront",
    )


def test_fetches_active_and_retired_within_cutoff():
    max_window_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # cutoff = max_window_start - timedelta(days=7) -> 2025-12-25 (enforced inside impl)

    active = [_sp("sp-1", "active", datetime(2026, 12, 31, tzinfo=timezone.utc))]
    retired_all = [
        _sp("sp-old", "retired", datetime(2025, 12, 24, tzinfo=timezone.utc)),  # before cutoff
        _sp("sp-recent", "retired", datetime(2025, 12, 26, tzinfo=timezone.utc)),  # after cutoff
    ]

    def fake_describe(profile, states):
        if states == ["active", "payment-pending"]:
            return active
        if states == ["retired"]:
            return retired_all
        return []

    with patch("scripts._common.savingsplans_describe_savings_plans", side_effect=fake_describe):
        result = fetch_all_relevant_sps("prof", max_window_start=max_window_start)

    ids = {sp.id for sp in result}
    assert "sp-1" in ids  # active
    assert "sp-recent" in ids  # retired within cutoff
    assert "sp-old" not in ids  # retired before cutoff
