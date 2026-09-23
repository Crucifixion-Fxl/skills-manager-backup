"""Tests for cur_query.discover_workload_regions."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from scripts._common import OrgConfig
from scripts.cur_query import discover_workload_regions


def _cfg() -> OrgConfig:
    return OrgConfig(
        alias="t",
        description=None,
        profile="prof",
        payer_account_id="111111111111",
        org_id="o-abc",
        cur_database="d",
        cur_table="t",
        athena_output="s3://b/p/",
        athena_workgroup="primary",
        primary_region="us-east-1",
        window_days=60,
        window_end="today",
        prefer="balanced",
    )


def _w_start() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _w_end() -> datetime:
    return datetime(2026, 4, 14, tzinfo=timezone.utc)


def test_discover_workload_regions_happy_path():
    """Three regions with costs [100, 50, 25] → cost shares sum to 1.0."""
    rows = [
        {"region": "us-east-1", "total_cost": "100"},
        {"region": "eu-central-1", "total_cost": "50"},
        {"region": "ap-northeast-2", "total_cost": "25"},
    ]
    with patch("scripts.cur_query.athena_execute", return_value=rows):
        result = discover_workload_regions(_cfg(), _w_start(), _w_end())

    assert len(result) == 3
    regions = [r for r, _ in result]
    assert regions[0] == "us-east-1"
    assert regions[1] == "eu-central-1"
    assert regions[2] == "ap-northeast-2"
    # cost shares: 100/175≈0.5714, 50/175≈0.2857, 25/175≈0.1429
    shares = [pct for _, pct in result]
    assert abs(shares[0] - 0.5714) < 0.001
    assert abs(shares[1] - 0.2857) < 0.001
    assert abs(shares[2] - 0.1429) < 0.001
    # Shares should sum close to 1.0
    assert abs(sum(shares) - 1.0) < 0.001


def test_discover_workload_regions_empty_rows():
    """Empty CUR result → empty list (no crash)."""
    with patch("scripts.cur_query.athena_execute", return_value=[]):
        result = discover_workload_regions(_cfg(), _w_start(), _w_end())
    assert result == []


def test_discover_workload_regions_sql_excludes_global_and_null():
    """SQL template must contain IS NOT NULL and 'global' exclusion filters."""
    with patch("scripts.cur_query.athena_execute", return_value=[]) as mock_execute:
        discover_workload_regions(_cfg(), _w_start(), _w_end())

    assert mock_execute.called
    query = mock_execute.call_args[0][1]
    assert "product_region_code IS NOT NULL" in query
    assert "product_region_code <> 'global'" in query
    assert "product_region_code <> 'Any'" in query
    assert "product_region_code <> ''" in query


def test_discover_workload_regions_sql_contains_timestamp_literals():
    """SQL must use 'YYYY-MM-DD HH:MM:SS' format (no T, no tz suffix)."""
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 14, 0, 0, 0, tzinfo=timezone.utc)
    with patch("scripts.cur_query.athena_execute", return_value=[]) as mock_execute:
        discover_workload_regions(_cfg(), start, end)

    query = mock_execute.call_args[0][1]
    assert "TIMESTAMP '2026-01-01 00:00:00'" in query
    assert "TIMESTAMP '2026-04-14 00:00:00'" in query
    # Must NOT contain T separator or tz suffix (Athena rejects those)
    assert "2026-01-01T" not in query
    assert "+00:00" not in query


def test_discover_workload_regions_uses_104_day_partition_filter():
    """With ~104-day window crossing 4 months, the partition filter covers all months."""
    # window: 2026-01-01 → 2026-04-14 spans Jan, Feb, Mar, Apr
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 4, 14, tzinfo=timezone.utc)
    with patch("scripts.cur_query.athena_execute", return_value=[]) as mock_execute:
        discover_workload_regions(_cfg(), start, end)

    query = mock_execute.call_args[0][1]
    # Partition filter dual-form: both padded and unpadded months
    assert "year='2026'" in query
    # January: month IN ('1','01')
    assert "'1','01'" in query
    # April: month IN ('4','04')
    assert "'4','04'" in query


def test_discover_workload_regions_rejects_naive_datetimes():
    """Both window bounds must be tz-aware; naive datetimes raise AssertionError."""
    with pytest.raises(AssertionError):
        discover_workload_regions(
            _cfg(),
            datetime(2026, 1, 1),  # naive
            datetime(2026, 4, 14, tzinfo=timezone.utc),
        )


def test_discover_workload_regions_zero_cost_grand_total():
    """If all costs sum to 0 (shouldn't happen but defensive), return empty list."""
    rows = [{"region": "us-east-1", "total_cost": "0"}]
    with patch("scripts.cur_query.athena_execute", return_value=rows):
        result = discover_workload_regions(_cfg(), _w_start(), _w_end())
    assert result == []


from scripts.exclude_filter import ExcludeFilter


def test_discover_workload_regions_injects_exclude_filter_clauses(monkeypatch):
    captured_queries: list[str] = []

    def fake_athena_execute(_cfg, q):
        captured_queries.append(q)
        return [{"region": "us-east-1", "total_cost": "100.0"}]

    monkeypatch.setattr("scripts.cur_query.athena_execute", fake_athena_execute)

    from scripts.cur_query import discover_workload_regions
    from datetime import datetime, timezone

    flt = ExcludeFilter(
        usage_type_patterns=("g4dn.",),
        account_ids=("123456789012",),
    )
    discover_workload_regions(
        _cfg(),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 10, tzinfo=timezone.utc),
        exclude_filter=flt,
        available_tag_columns=frozenset(),
    )
    assert len(captured_queries) == 1
    q = captured_queries[0]
    assert "NOT LIKE '%g4dn.%'" in q
    assert "NOT IN ('123456789012')" in q


def test_discover_workload_regions_default_empty_filter(monkeypatch):
    """Default call (no filter arg) must preserve existing SQL byte-for-byte."""
    captured: list[str] = []
    monkeypatch.setattr(
        "scripts.cur_query.athena_execute",
        lambda cfg, q: captured.append(q) or [],
    )

    from scripts.cur_query import discover_workload_regions
    from datetime import datetime, timezone

    discover_workload_regions(
        _cfg(),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 10, tzinfo=timezone.utc),
    )
    assert "NOT LIKE" not in captured[0] or captured[0].count("NOT LIKE") == 1
    # one existing NOT LIKE for SpotUsage; no extras
    assert captured[0].count("NOT LIKE") == 1
