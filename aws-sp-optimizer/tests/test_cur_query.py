"""Tests for cur_query.build_hourly_series — SQL formatting + row aggregation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from scripts._common import HourlySeries, OrgConfig
from scripts.cur_query import build_hourly_series


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


def test_builds_series_from_mock_rows():
    start = datetime(2026, 4, 13, 10, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=3)
    # Usage rows: net_cost_usd = 0.91 * gross_list_usd
    # SavingsPlanCoveredUsage rows: sp_effective_cost_usd drives net via e_sp
    rows = [
        {
            "usage_hour": "2026-04-13 10:00:00.000",
            "line_type": "Usage",
            "account_id": "111111111111",
            "product_code": "AmazonEC2",
            "region": "us-east-1",
            "instance_type": "c5.xlarge",
            "operation": "RunInstances",
            "usage_type": "USE1-BoxUsage:c5.xlarge",
            "gross_list_usd": "5.0",
            "net_cost_usd": str(0.91 * 5.0),
            "sp_effective_cost_usd": "0",
            "usage_amount": "10.0",
        },
        {
            "usage_hour": "2026-04-13 10:00:00.000",
            "line_type": "Usage",
            "account_id": "111111111111",
            "product_code": "AWSLambda",
            "region": "us-east-1",
            "instance_type": None,
            "operation": "Invoke",
            "usage_type": "Lambda-GB-Second",
            "gross_list_usd": "0.5",
            "net_cost_usd": str(0.91 * 0.5),
            "sp_effective_cost_usd": "0",
            "usage_amount": "1000.0",
        },
        {
            "usage_hour": "2026-04-13 11:00:00.000",
            "line_type": "SavingsPlanCoveredUsage",
            "account_id": "111111111111",
            "product_code": "AmazonEC2",
            "region": "us-east-1",
            "instance_type": "c5.xlarge",
            "operation": "RunInstances",
            "usage_type": "USE1-BoxUsage:c5.xlarge",
            "gross_list_usd": "6.0",
            "net_cost_usd": "0",
            "sp_effective_cost_usd": "5.4",  # net contribution: 5.4 * 0.91 = 4.914
            "usage_amount": "10.0",
        },
    ]

    with patch("scripts.cur_query.athena_execute", return_value=rows) as mock_execute:
        series = build_hourly_series(_cfg(), start, end, e_sp=0.09)

    assert isinstance(series, HourlySeries)
    assert len(series.hours) == 3
    assert series.window_start == start
    assert series.window_end == end
    # Hour 10:00: two Usage rows, net = 0.91*(5.0+0.5) = 5.005
    h10 = start
    assert abs(series.data[h10]["total_list_usd"] - 5.5) < 1e-9
    assert abs(series.data[h10]["total_net_usd"] - 0.91 * 5.5) < 1e-9
    assert len(series.data[h10]["mix"]) == 2
    # Hour 11:00: SavingsPlanCoveredUsage row, net = 5.4 * (1 - 0.09) = 4.914
    h11 = start + timedelta(hours=1)
    assert abs(series.data[h11]["total_list_usd"] - 6.0) < 1e-9
    assert abs(series.data[h11]["total_net_usd"] - 5.4 * 0.91) < 1e-9
    # Hour 12:00 (still in window) has no rows
    h12 = start + timedelta(hours=2)
    assert series.data[h12]["total_list_usd"] == pytest.approx(0.0, abs=1e-9)
    assert series.data[h12]["total_net_usd"] == pytest.approx(0.0, abs=1e-9)
    # Assert the SQL was called with the expected database/table
    assert mock_execute.called
    query = mock_execute.call_args[0][1]
    assert 'FROM "d"."t"' in query
    assert "line_item_usage_start_date >= TIMESTAMP" in query
    # Athena TIMESTAMP literal: 'YYYY-MM-DD HH:MM:SS' — space separator,
    # no 'T', no tz suffix. Regression guard for bug discovered during
    # Phase 7 cold smoke (INVALID_LITERAL rejection by Athena).
    assert "TIMESTAMP '2026-04-13 10:00:00'" in query
    assert "2026-04-13T10:00:00+00:00" not in query  # rejected format
    # New columns present in SQL
    assert "line_item_line_item_type" in query
    assert "line_item_net_unblended_cost" in query
    assert "savings_plan_savings_plan_effective_cost" in query


def test_rejects_naive_datetimes():
    import pytest

    with pytest.raises(AssertionError):
        build_hourly_series(
            _cfg(), datetime(2026, 4, 13, 10, 0, 0), datetime(2026, 4, 13, 11, 0, 0),
            e_sp=0.09,
        )


# ---------------------------------------------------------------------------
# Tests for compute_edp_factors (Task 6)
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402 (already imported above, but fine)

from scripts._common import OrgConfig  # noqa: E402 (already imported above)
from scripts.cur_query import compute_edp_factors  # noqa: E402


def _make_config():
    return OrgConfig(
        alias="test", description=None, profile="p", payer_account_id="1",
        org_id="o", cur_database="cur-db", cur_table="a4x_report",
        athena_output="s3://bucket/out/", athena_workgroup="primary",
        primary_region="us-east-1", window_days=30,
        window_end=datetime(2026, 4, 20, tzinfo=timezone.utc), prefer="balanced",
    )


def _tz_window():
    return datetime(2026, 3, 20, tzinfo=timezone.utc), datetime(2026, 4, 20, tzinfo=timezone.utc)


def test_compute_edp_factors_uniform():
    cfg = _make_config()
    s, e = _tz_window()
    rows_od = [{"e_od_unblended": "100.0", "e_od_net": "91.0"}]
    rows_sp = [{"e_sp_unblended": "50.0", "e_sp_net": "45.5"}]
    with patch("scripts.cur_query.athena_execute", side_effect=[rows_od, rows_sp]):
        e_sp, e_od, audit = compute_edp_factors(cfg, s, e)
    assert abs(e_sp - 0.09) < 1e-6
    assert abs(e_od - 0.09) < 1e-6
    assert audit["uniform"] is True
    assert audit["e_sp_inferred_from_e_od"] is False


def test_compute_edp_factors_differential():
    cfg = _make_config()
    s, e = _tz_window()
    rows_od = [{"e_od_unblended": "100.0", "e_od_net": "91.0"}]
    rows_sp = [{"e_sp_unblended": "50.0", "e_sp_net": "50.0"}]   # SP fee not discounted
    with patch("scripts.cur_query.athena_execute", side_effect=[rows_od, rows_sp]):
        e_sp, e_od, audit = compute_edp_factors(cfg, s, e)
    assert abs(e_sp - 0.0) < 1e-6
    assert abs(e_od - 0.09) < 1e-6
    assert audit["uniform"] is False
    assert audit["delta"] > 0.005


def test_compute_edp_factors_no_sp_inferred():
    """Account has Usage rows but no SavingsPlanRecurringFee yet → e_sp=e_od."""
    cfg = _make_config()
    s, e = _tz_window()
    rows_od = [{"e_od_unblended": "100.0", "e_od_net": "91.0"}]
    rows_sp_empty = [{"e_sp_unblended": None, "e_sp_net": None}]
    with patch("scripts.cur_query.athena_execute", side_effect=[rows_od, rows_sp_empty]):
        e_sp, e_od, audit = compute_edp_factors(cfg, s, e)
    assert abs(e_sp - e_od) < 1e-9
    assert audit["e_sp_inferred_from_e_od"] is True
    assert audit["uniform"] is True


def test_compute_edp_factors_no_od_raises():
    """No Usage rows → raise NoDataError."""
    from scripts._common import NoDataError
    cfg = _make_config()
    s, e = _tz_window()
    rows_od_empty = [{"e_od_unblended": None, "e_od_net": None}]
    rows_sp_empty = [{"e_sp_unblended": None, "e_sp_net": None}]
    with patch("scripts.cur_query.athena_execute", side_effect=[rows_od_empty, rows_sp_empty]):
        import pytest
        with pytest.raises(NoDataError) as exc:
            compute_edp_factors(cfg, s, e)
        assert exc.value.code == "no_od_rows_for_edp"


from scripts.exclude_filter import ExcludeFilter, TagExclusion


def test_build_hourly_series_injects_filter_clauses(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(
        "scripts.cur_query.athena_execute",
        lambda cfg, q: captured.append(q) or [],
    )

    from scripts.cur_query import build_hourly_series
    from datetime import datetime, timezone

    flt = ExcludeFilter(
        usage_type_patterns=("g4dn.",),
        account_ids=("123456789012",),
        tag_exclusions=(TagExclusion(key="lifecycle", values=("ephemeral",)),),
    )
    build_hourly_series(
        _cfg(),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 1, 10, 5, tzinfo=timezone.utc),
        e_sp=0.09,
        exclude_filter=flt,
        available_tag_columns=frozenset({"resource_tags_user_lifecycle"}),
    )
    # Sonar S6465/S6466 false positives: len == 1 assert + captured[0] pattern
    # should be fine (sibling at L256 uses len == 2 + captured[0]/captured[1]
    # with no complaint), and tuple unpacking also tripped S6465. Inline the
    # substring checks directly on captured[0] to match the sibling exactly.
    assert len(captured) == 1, "build_hourly_series should call athena_execute exactly once"
    assert "NOT LIKE '%g4dn.%'" in captured[0]
    assert "NOT IN ('123456789012')" in captured[0]
    assert "resource_tags_user_lifecycle" in captured[0]


def test_compute_edp_factors_applies_filter_to_od_only(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(
        "scripts.cur_query.athena_execute",
        lambda cfg, q: captured.append(q) or [
            {"e_od_unblended": "100", "e_od_net": "91"},
            {"e_sp_unblended": "100", "e_sp_net": "91"},
        ][len(captured) - 1 : len(captured)],
    )

    from scripts.cur_query import compute_edp_factors
    from datetime import datetime, timezone

    compute_edp_factors(
        _cfg(),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 10, tzinfo=timezone.utc),
        exclude_filter=ExcludeFilter(usage_type_patterns=("g4dn.",)),
        available_tag_columns=frozenset(),
    )
    assert len(captured) == 2
    # OD query has filter:
    assert "NOT LIKE '%g4dn.%'" in captured[0]
    # SP fee query does NOT have filter:
    assert "NOT LIKE '%g4dn.%'" not in captured[1]
