"""Tests for ensure_cache_fresh decision tree — uses a pre-populated tmp cache dir."""

from datetime import datetime, timezone

import pytest
import responses


@responses.activate
def test_cache_hit_returns_existing_ratios(tmp_path, monkeypatch):
    """When local version matches upstream, do NOT rebuild — just load."""
    from scripts._common import RatioEntry, Ratios, RegionVersionInfo, VersionMetadata
    from scripts.pricing_cache import (
        ensure_cache_fresh,
        ratios_to_json,
        version_metadata_to_json,
        write_ratios_atomic,
        write_version_metadata_atomic,
    )

    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", tmp_path / "ratios.json.gz")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_VERSION_PATH", tmp_path / "version.json")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_DATA_DIR", tmp_path)

    # Pre-populate the cache — use real YYYYMMDDHHMMSS version timestamps
    # so _is_version_stale can parse them. Same cached vs upstream → no rebuild.
    cached_ver = "20260416000000"
    now = datetime.now(timezone.utc)
    existing_ratios = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=now,
        ec2={"us-east-1": {"c5.xlarge|RunInstances": RatioEntry(0.1, 0.2, 0.5, 0.5)}},
        lambda_rates={},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": cached_ver},
            "od_by_region": {"us-east-1": cached_ver},
        },
        primary_region="us-east-1",
    )
    write_ratios_atomic(tmp_path / "ratios.json.gz", ratios_to_json(existing_ratios))
    write_version_metadata_atomic(
        tmp_path / "version.json",
        version_metadata_to_json(
            VersionMetadata(
                schema_version=1,
                last_refreshed_at=now,
                aws_sp_optimizer_version="0.1.0",
                regions={
                    "us-east-1": RegionVersionInfo(
                        sp_version=cached_ver,
                        od_version=cached_ver,
                        sp_refreshed_at=now,
                        od_refreshed_at=now,
                        ratio_entries_count=1,
                    )
                },
                lambda_refreshed_at=now,
                fargate_refreshed_at=now,
            )
        ),
    )

    # Upstream indices report the SAME versions — no rebuild needed
    # Real AWS shape: SP region index uses list with "versionUrl" field
    responses.add(
        responses.GET,
        "https://pricing.us-east-1.amazonaws.com/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/current/region_index.json",
        json={
            "regions": [
                {
                    "regionCode": "us-east-1",
                    "versionUrl": f"/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/{cached_ver}/us-east-1/index.json",
                }
            ]
        },
    )
    responses.add(
        responses.GET,
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/region_index.json",
        json={
            "regions": {
                "us-east-1": {
                    "currentVersionUrl": f"/offers/v1.0/aws/AmazonEC2/{cached_ver}/us-east-1/index.json"
                }
            }
        },
    )

    result = ensure_cache_fresh(
        required_regions={"us-east-1"},
        primary_region="us-east-1",
    )
    assert result.version == cached_ver
    assert "c5.xlarge|RunInstances" in result.ec2["us-east-1"]


def test_skip_refresh_without_cache_raises(tmp_path, monkeypatch):
    """--skip-pricing-refresh with no local cache → CacheError."""
    from scripts._common import CacheError
    from scripts.pricing_cache import ensure_cache_fresh

    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", tmp_path / "ratios.json.gz")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_VERSION_PATH", tmp_path / "version.json")

    with pytest.raises(CacheError) as excinfo:
        ensure_cache_fresh(
            required_regions={"us-east-1"},
            primary_region="us-east-1",
            skip_refresh=True,
        )
    assert excinfo.value.code == "skip_refresh_but_no_cache"


def test_skip_refresh_requires_primary_region_in_required():
    from scripts.pricing_cache import ensure_cache_fresh

    with pytest.raises(AssertionError):
        ensure_cache_fresh(
            required_regions={"us-east-1"},
            primary_region="eu-central-1",  # not in set
        )
