"""Tests for RatioEntry and Ratios dataclasses. Ratios.lookup gets its own
task (2.3)."""

from datetime import datetime, timezone

import pytest

from scripts._common import RatioEntry, Ratios


def test_ratio_entry_minimal():
    r = RatioEntry(sp_rate_usd=0.123, od_rate_usd=0.170, ratio=0.7235, discount_pct=0.2765)
    assert r.sp_rate_usd == pytest.approx(0.123)
    assert r.sku is None
    assert r.os is None
    assert r.tenancy is None


def test_ratio_entry_full():
    r = RatioEntry(
        sp_rate_usd=0.1,
        od_rate_usd=0.2,
        ratio=0.5,
        discount_pct=0.5,
        sku="FOO",
        os="Linux",
        tenancy="Shared",
    )
    assert r.sku == "FOO"


def test_ratios_version_reads_primary_region():
    entry = RatioEntry(0.1, 0.2, 0.5, 0.5)
    r = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={"us-east-1": {"c5.xlarge|RunInstances": entry}},
        lambda_rates={},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "20260410"},
            "od_by_region": {"us-east-1": "20260408"},
        },
        primary_region="us-east-1",
    )
    assert r.version == "20260410"
    assert r.cached_at == datetime(2026, 4, 13, tzinfo=timezone.utc)
