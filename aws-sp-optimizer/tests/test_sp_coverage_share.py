"""Tests for compute_sp_coverage_share — attenuator for existing SP coverage under filter."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from scripts._common import compute_sp_coverage_share, OrgConfig
from scripts.exclude_filter import ExcludeFilter


def _cfg():
    return OrgConfig(
        alias="t", description="t", profile="p",
        payer_account_id="002497567426", org_id="o-t",
        cur_database="db", cur_table="cur",
        athena_output="s3://x/", athena_workgroup="primary",
        primary_region="us-east-1",
        window_days=60, window_end="today", prefer="balanced",
    )


def _ts(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def test_empty_filter_returns_one():
    """No filter ⇒ share = 1.0, no CUR query executed."""
    with patch("scripts._common.athena_execute") as mock:
        share, audit = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            ExcludeFilter(), frozenset(),
        )
    assert share == pytest.approx(1.0)
    assert audit["total_cost_usd"] is None  # short-circuit
    mock.assert_not_called()


def test_filter_shrinks_share_proportionally():
    rows = [{"retained_cost": "600", "total_cost": "1000"}]
    with patch("scripts._common.athena_execute", return_value=rows):
        share, audit = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            ExcludeFilter(account_ids=("123456789012",)),
            frozenset(),
        )
    assert share == pytest.approx(0.6)
    assert audit["retained_cost_usd"] == pytest.approx(600.0)
    assert audit["total_cost_usd"] == pytest.approx(1000.0)


def test_zero_sp_covered_usage_returns_one():
    """No SP consumption in window ⇒ share = 1.0 (no basis for reduction)."""
    rows = [{"retained_cost": "0", "total_cost": "0"}]
    with patch("scripts._common.athena_execute", return_value=rows):
        share, audit = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            ExcludeFilter(account_ids=("123456789012",)),
            frozenset(),
        )
    assert share == pytest.approx(1.0)
    assert audit["total_cost_usd"] == pytest.approx(0.0)


def test_share_clamped_to_01():
    rows = [{"retained_cost": "1200", "total_cost": "1000"}]  # pathological
    with patch("scripts._common.athena_execute", return_value=rows):
        share, _ = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            ExcludeFilter(account_ids=("123456789012",)),
            frozenset(),
        )
    assert share == pytest.approx(1.0)


def test_athena_failure_returns_neutral_share():
    """Athena exception → share = 1.0 (neutral), graceful degradation like compute_untagged_fraction."""
    def raise_boom(_cfg, _q):
        raise RuntimeError("athena boom")

    with patch("scripts._common.athena_execute", side_effect=raise_boom):
        share, audit = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            ExcludeFilter(account_ids=("123456789012",)),
            frozenset(),
        )
    assert share == pytest.approx(1.0)
    assert audit["retained_cost_usd"] is None
    assert audit["total_cost_usd"] is None
    assert "athena query failed" in audit["note"]
    assert "RuntimeError" in audit["note"]


def test_all_filters_skipped_returns_one():
    """Non-empty filter but all clauses skipped (e.g., missing tag columns) → share = 1.0."""
    from scripts.exclude_filter import TagExclusion
    # Tag-only filter, with empty available_tag_columns → build_exclude_sql_clauses returns no clauses (all skipped with warnings)
    flt = ExcludeFilter(
        tag_exclusions=(TagExclusion(key="nonexistent", values=("x",)),),
    )
    with patch("scripts._common.athena_execute") as mock:
        share, audit = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            flt, frozenset(),
        )
    assert share == pytest.approx(1.0)
    assert audit["retained_cost_usd"] is None
    assert audit["total_cost_usd"] is None
    assert "no clauses" in audit["note"]
    mock.assert_not_called()  # should short-circuit before athena call
