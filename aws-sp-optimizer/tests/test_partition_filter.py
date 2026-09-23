"""Tests for generate_partition_filter — dual-form (unpadded + padded) months.

Spec originally assumed CUR v1 always uses zero-padded month partitions, but
real AWS CUR v1 created via the AWS-provided CF template uses unpadded months
('4' not '04'). The filter emits IN ('4','04') to match both layouts.
"""

from datetime import datetime, timezone

from scripts._common import generate_partition_filter


def test_same_month():
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    end = datetime(2026, 4, 30, tzinfo=timezone.utc)
    f = generate_partition_filter(start, end)
    assert f == "((year='2026' AND month IN ('4','04')))"


def test_year_boundary():
    start = datetime(2025, 11, 15, tzinfo=timezone.utc)
    end = datetime(2026, 2, 15, tzinfo=timezone.utc)
    f = generate_partition_filter(start, end)
    # November 2025, December 2025, January 2026, February 2026
    assert "year='2025' AND month IN ('11','11')" in f
    assert "year='2025' AND month IN ('12','12')" in f
    assert "year='2026' AND month IN ('1','01')" in f
    assert "year='2026' AND month IN ('2','02')" in f


def test_single_digit_months_emit_both_forms():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 31, tzinfo=timezone.utc)
    f = generate_partition_filter(start, end)
    assert "month IN ('1','01')" in f
    assert "month IN ('2','02')" in f
    assert "month IN ('3','03')" in f


def test_double_digit_months_collapse_to_same_form():
    """For months ≥ 10 the two forms are identical but still emitted as IN."""
    start = datetime(2025, 10, 1, tzinfo=timezone.utc)
    end = datetime(2025, 10, 31, tzinfo=timezone.utc)
    f = generate_partition_filter(start, end)
    assert "month IN ('10','10')" in f
