"""Round-trip write-then-load tests for ratios.json.gz and version.json."""

from datetime import datetime, timezone

import pytest

from scripts._common import (
    CacheError,
    RatioEntry,
    Ratios,
    RegionVersionInfo,
    VersionMetadata,
)
from scripts.pricing_cache import (
    load_and_validate_ratios,
    load_version_metadata,
    ratios_to_json,
    version_metadata_to_json,
    write_ratios_atomic,
    write_version_metadata_atomic,
)


def _sample_ratios():
    return Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, 10, 0, 0, tzinfo=timezone.utc),
        ec2={"us-east-1": {"c5.xlarge|RunInstances": RatioEntry(0.1, 0.2, 0.5, 0.5, sku="S1")}},
        lambda_rates={"Lambda-GB-Second": RatioEntry(1e-5, 2e-5, 0.5, 0.5)},
        fargate={"us-east-1": {"Fargate-vCPU-Hours:perCPU": RatioEntry(0.03, 0.04, 0.75, 0.25)}},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v2"},
        },
        primary_region="us-east-1",
    )


def test_ratios_roundtrip(tmp_path, monkeypatch):
    gz_path = tmp_path / "ratios.json.gz"
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", gz_path)
    original = _sample_ratios()
    write_ratios_atomic(gz_path, ratios_to_json(original))

    loaded = load_and_validate_ratios(primary_region="us-east-1")
    assert loaded.schema_version == 1
    assert loaded.primary_region == "us-east-1"
    assert loaded.ec2["us-east-1"]["c5.xlarge|RunInstances"].ratio == pytest.approx(0.5)
    assert loaded.lambda_rates["Lambda-GB-Second"].ratio == pytest.approx(0.5)
    assert loaded.fargate["us-east-1"]["Fargate-vCPU-Hours:perCPU"].ratio == pytest.approx(0.75)


def test_ratios_load_missing_file_raises(tmp_path, monkeypatch):
    """When shipped cache is missing, raise ratios_missing."""
    monkeypatch.setattr(
        "scripts.pricing_cache.SHIPPED_RATIOS_PATH", tmp_path / "shipped_nope.json.gz"
    )
    with pytest.raises(CacheError) as excinfo:
        load_and_validate_ratios(primary_region="us-east-1")
    assert excinfo.value.code in {"ratios_missing", "pricing_json_corrupt"}


def test_ratios_load_schema_mismatch_raises(tmp_path, monkeypatch):
    import gzip
    import json

    gz_path = tmp_path / "ratios.json.gz"
    payload = {
        "schema_version": 99,
        "ec2": {},
        "lambda": {},
        "fargate": {},
        "built_at": "2026-04-13T10:00:00+00:00",
        "term": "x",
        "built_from_versions": {"sp_by_region": {}, "od_by_region": {}},
    }
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        json.dump(payload, f)
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", gz_path)
    with pytest.raises(CacheError) as excinfo:
        load_and_validate_ratios(primary_region="us-east-1")
    assert excinfo.value.code == "pricing_json_corrupt"


def test_version_metadata_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_VERSION_PATH", tmp_path / "version.json")
    original = VersionMetadata(
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
    write_version_metadata_atomic(tmp_path / "version.json", version_metadata_to_json(original))

    loaded = load_version_metadata()
    assert loaded is not None
    assert loaded.schema_version == 1
    assert "us-east-1" in loaded.regions
    assert loaded.regions["us-east-1"].sp_version == "20260410"


def test_version_metadata_load_missing_returns_none(tmp_path, monkeypatch):
    """Shipped cache missing → returns None."""
    monkeypatch.setattr(
        "scripts.pricing_cache.SHIPPED_VERSION_PATH", tmp_path / "shipped_nope.json"
    )
    assert load_version_metadata() is None
