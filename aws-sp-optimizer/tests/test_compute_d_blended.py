"""Tests for compute_d_blended — weighted average + coverage audit."""

from datetime import datetime, timedelta, timezone

import pytest

from scripts._common import (
    HourlySeries,
    NoDataError,
    RatioEntry,
    Ratios,
)
from scripts.newsvendor import compute_d_blended


def _ratios():
    return Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={
            "us-east-1": {
                "c5.xlarge|RunInstances": RatioEntry(0.123, 0.170, 0.7235, 0.2765),
            },
        },
        lambda_rates={
            "Lambda-GB-Second": RatioEntry(1.483e-5, 1.667e-5, 0.8896, 0.1104),
        },
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v2"},
        },
        primary_region="us-east-1",
    )


def _make_series(cost_pairs: list[tuple[tuple, float]]) -> HourlySeries:
    """Build a series that puts ALL entries in the recent 30d window so
    compute_d_blended sees them."""
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    start = end - timedelta(days=30)
    hours = [start + timedelta(hours=i) for i in range(30 * 24)]
    data = {h: {"total_list_usd": 0.0, "total_net_usd": 0.0, "mix": {}} for h in hours}
    target_hour = hours[-1]
    mix: dict = {}
    for key, cost in cost_pairs:
        mix[key] = mix.get(key, 0.0) + cost
    data[target_hour]["mix"] = mix
    _cost_total = sum(c for _, c in cost_pairs)
    data[target_hour]["total_list_usd"] = _cost_total
    data[target_hour]["total_net_usd"] = _cost_total * 0.91
    return HourlySeries(hours=hours, data=data, window_start=start, window_end=end)


def test_weighted_average_single_ratio_type():
    """When every matched entry has the same ratio, d_raw equals 1 - ratio.
    With uniform EDP (e_sp == e_od), d_calibrated == d_raw."""
    ratios = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={"us-east-1": {f"c5.xlarge|Op{i}": RatioEntry(0.7, 1.0, 0.7, 0.3) for i in range(12)}},
        lambda_rates={},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v1"},
        },
        primary_region="us-east-1",
    )
    pairs = [(("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{i}", f"U{i}"), 1.0) for i in range(12)]
    series = _make_series(pairs)
    d, audit = compute_d_blended(series, ratios, e_sp=0.09, e_od=0.09)
    assert abs(d - 0.3) < 1e-9  # uniform EDP → d_calibrated == d_raw == 0.3
    assert audit.matched_entries == 12


def test_min_matched_floor_raises():
    ratios = _ratios()
    series = _make_series([(("AmazonEC2", "us-east-1", "c5.xlarge", "RunInstances", "U1"), 10.0)])
    with pytest.raises(NoDataError) as excinfo:
        compute_d_blended(series, ratios, e_sp=0.09, e_od=0.09)
    assert excinfo.value.code == "insufficient_matched_entries_for_d"


def test_no_matched_rows_raises():
    ratios = _ratios()
    series = _make_series(
        [
            (("AmazonS3", "us-east-1", None, "Get", "Requests"), 10.0)  # unknown product
        ]
    )
    with pytest.raises(NoDataError) as excinfo:
        compute_d_blended(series, ratios, e_sp=0.09, e_od=0.09)
    assert excinfo.value.code == "no_matched_rows_for_d"


def test_weighted_average_with_enough_matches():
    """Build 10 EC2 mix keys by using 10 different operations, each populated with
    distinct RatioEntry in the cache."""
    ratios = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={"us-east-1": {f"c5.xlarge|Op{i}": RatioEntry(0.5, 1.0, 0.5, 0.5) for i in range(10)}},
        lambda_rates={},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v1"},
        },
        primary_region="us-east-1",
    )
    pairs = [(("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{i}", f"U{i}"), 1.0) for i in range(10)]
    series = _make_series(pairs)
    d, audit = compute_d_blended(series, ratios, e_sp=0.09, e_od=0.09)
    assert abs(d - 0.5) < 1e-9  # uniform EDP → d_calibrated == d_raw == 0.5
    assert audit.matched_entries == 10
    assert audit.unmatched_entries == 0
    assert audit.coverage_pct == pytest.approx(1.0)


def test_unmatched_included_in_audit():
    ratios = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={"us-east-1": {f"c5.xlarge|Op{i}": RatioEntry(0.5, 1.0, 0.5, 0.5) for i in range(10)}},
        lambda_rates={},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v1"},
        },
        primary_region="us-east-1",
    )
    pairs = [(("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{i}", f"U{i}"), 1.0) for i in range(10)]
    pairs.append((("AmazonEC2", "eu-west-99", "c5.xlarge", "Op0", "U"), 5.0))  # unknown region
    series = _make_series(pairs)
    _, audit = compute_d_blended(series, ratios, e_sp=0.09, e_od=0.09)
    assert audit.unmatched_entries == 1
    assert audit.coverage_pct < 1.0
    assert len(audit.unmatched_top10_by_cost) == 1
    assert audit.unmatched_top10_by_cost[0]["cost"] == pytest.approx(5.0)


def test_d_calibrated_uniform_edp_is_identity():
    """When e_sp == e_od, d_calibrated must equal d_raw (identity calibration)."""
    ratios = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={"us-east-1": {f"c5.xlarge|Op{i}": RatioEntry(0.83, 1.0, 0.83, 0.17) for i in range(12)}},
        lambda_rates={},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v1"},
        },
        primary_region="us-east-1",
    )
    pairs = [(("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{i}", f"U{i}"), 1.0) for i in range(12)]
    series = _make_series(pairs)
    d_cal, audit = compute_d_blended(series, ratios, e_sp=0.09, e_od=0.09)
    assert abs(d_cal - audit.d_raw) < 1e-9
    assert audit.calibration_applied is False
    assert audit.edp_uniform is True


def test_d_calibrated_sp_not_discounted_lowers_d():
    """When e_sp=0.0 (no EDP on SP) but e_od=0.09 (EDP on OD), d_calibrated < d_raw."""
    ratios = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={"us-east-1": {f"c5.xlarge|Op{i}": RatioEntry(0.83, 1.0, 0.83, 0.17) for i in range(12)}},
        lambda_rates={},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v1"},
        },
        primary_region="us-east-1",
    )
    pairs = [(("AmazonEC2", "us-east-1", "c5.xlarge", f"Op{i}", f"U{i}"), 1.0) for i in range(12)]
    series = _make_series(pairs)
    d_cal, audit = compute_d_blended(series, ratios, e_sp=0.0, e_od=0.09)
    assert d_cal < audit.d_raw
    assert audit.calibration_applied is True


def test_apply_edp_calibration_math():
    from scripts.newsvendor import _apply_edp_calibration
    # d=0.17, e_sp=0.0, e_od=0.09 → 1 - 0.83/0.91 = 0.0879...
    assert abs(_apply_edp_calibration(0.17, 0.0, 0.09) - 0.08791208791208793) < 1e-9


def test_d_blended_reads_only_from_series_so_filter_transparent():
    """Sanity: compute_d_blended uses series.data[...]['mix'] only.

    If a future refactor adds a separate CUR fetch inside compute_d_blended, this
    test should fail by catching the new data source. Keep this marker in the
    suite so d_raw consistency doesn't silently regress.
    """
    from scripts.newsvendor import compute_d_blended
    import inspect

    src = inspect.getsource(compute_d_blended)
    # Guard: no new CUR fetching primitives sneaking in
    assert "athena_execute" not in src
    assert "SELECT" not in src.upper()  # no embedded SQL
    # Guard: still reads from series (the filtered input)
    assert "series.hours" in src or "series.data" in src
