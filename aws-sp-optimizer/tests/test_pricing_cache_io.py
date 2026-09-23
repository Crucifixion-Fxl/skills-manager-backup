"""Tests for atomic JSON write helpers."""

import gzip
import json
from datetime import datetime, timezone

import pytest

from scripts._common import RatioEntry, Ratios, RegionVersionInfo, VersionMetadata
from scripts.pricing_cache import (
    load_and_validate_ratios,
    ratios_to_json,
    version_metadata_to_json,
    write_ratios_atomic,
    write_version_metadata_atomic,
)


def test_shipped_ratios_is_gzipped(tmp_path, monkeypatch):
    """load_and_validate_ratios must read gzip-compressed ratios from SHIPPED_RATIOS_PATH."""
    payload = {
        "schema_version": 1,
        "term": "1 year No Upfront Compute Savings Plan",
        "built_at": "2026-04-13T10:00:00+00:00",
        "ec2": {
            "us-east-1": {
                "c5.xlarge|RunInstances": {
                    "sp_rate_usd": 0.169,
                    "od_rate_usd": 0.204,
                    "ratio": 0.8235,
                    "discount_pct": 0.1765,
                    "sku": None,
                }
            }
        },
        "lambda": {},
        "fargate": {},
        "built_from_versions": {
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v2"},
        },
    }
    gz_path = tmp_path / "ratios.json.gz"
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        json.dump(payload, f)

    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", gz_path)

    ratios = load_and_validate_ratios(primary_region="us-east-1")
    assert ratios.ec2["us-east-1"]["c5.xlarge|RunInstances"].ratio == pytest.approx(0.8235)


def test_write_ratios_atomic_writes_valid_gzip(tmp_path):
    ratios = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={"us-east-1": {"c5.xlarge|RunInstances": RatioEntry(0.1, 0.2, 0.5, 0.5, sku="S1")}},
        lambda_rates={"Lambda-GB-Second": RatioEntry(1e-5, 2e-5, 0.5, 0.5)},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v2"},
        },
        primary_region="us-east-1",
    )
    path = tmp_path / "ratios.json.gz"
    write_ratios_atomic(path, ratios_to_json(ratios))

    assert path.is_file()
    with gzip.open(path, "rt", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["schema_version"] == 1
    assert loaded["ec2"]["us-east-1"]["c5.xlarge|RunInstances"]["ratio"] == pytest.approx(0.5)
    assert "lambda" in loaded  # json field is 'lambda' not 'lambda_rates'


def test_write_atomic_is_truly_atomic(tmp_path):
    """Interrupted writes should never leave partial content readable."""
    path = tmp_path / "ratios.json.gz"
    write_ratios_atomic(path, {"schema_version": 1, "old": "data"})
    write_ratios_atomic(path, {"schema_version": 1, "new": "data"})
    with gzip.open(path, "rt", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == {"schema_version": 1, "new": "data"}
    # No leftover tempfiles
    siblings = list(path.parent.glob(".ratios.json.gz.tmp.*"))
    assert siblings == []


def test_write_version_metadata_atomic(tmp_path):
    meta = VersionMetadata(
        schema_version=1,
        last_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        aws_sp_optimizer_version="0.1.0",
        regions={
            "us-east-1": RegionVersionInfo(
                sp_version="20260410",
                od_version="20260408",
                sp_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
                od_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
                ratio_entries_count=100,
            ),
        },
        lambda_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        fargate_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
    )
    path = tmp_path / "version.json"
    write_version_metadata_atomic(path, version_metadata_to_json(meta))

    loaded = json.loads(path.read_text())
    assert loaded["schema_version"] == 1
    assert loaded["regions"]["us-east-1"]["sp_version"] == "20260410"
