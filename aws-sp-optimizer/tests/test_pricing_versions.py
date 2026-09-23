"""Tests for the region_index.json fetchers."""

import responses
from datetime import datetime, timezone, timedelta

from scripts.pricing_cache import (
    EC2_OD_REGION_INDEX_URL,
    SP_REGION_INDEX_URL,
    fetch_od_region_versions,
    fetch_sp_region_versions,
)


@responses.activate
def test_fetch_sp_versions_returns_dict():
    # Real AWS shape (verified 2026-04-14): regions is a list, field is "versionUrl"
    responses.add(
        responses.GET,
        SP_REGION_INDEX_URL,
        json={
            "publicationDate": "2026-04-10",
            "regions": [
                {
                    "regionCode": "us-east-1",
                    "versionUrl": "/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/20260410/us-east-1/index.json",
                },
                {
                    "regionCode": "eu-central-1",
                    "versionUrl": "/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/20260410/eu-central-1/index.json",
                },
            ],
        },
        status=200,
    )
    versions = fetch_sp_region_versions({"us-east-1"})
    assert versions == {"us-east-1": "20260410"}


@responses.activate
def test_fetch_sp_versions_errors_on_missing_region():
    import pytest

    from scripts._common import CacheError

    responses.add(
        responses.GET,
        SP_REGION_INDEX_URL,
        json={
            "regions": [
                {
                    "regionCode": "us-east-1",
                    "versionUrl": "/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/20260410/us-east-1/index.json",
                }
            ]
        },
        status=200,
    )
    with pytest.raises(CacheError) as excinfo:
        fetch_sp_region_versions({"us-east-1", "ap-fantasy-99"})
    assert excinfo.value.code == "region_not_in_sp_index"


@responses.activate
def test_fetch_od_versions_returns_dict():
    responses.add(
        responses.GET,
        EC2_OD_REGION_INDEX_URL,
        json={
            "regions": {
                "us-east-1": {
                    "currentVersionUrl": "/offers/v1.0/aws/AmazonEC2/20260408/us-east-1/index.json"
                },
            },
        },
        status=200,
    )
    versions = fetch_od_region_versions({"us-east-1"})
    assert versions == {"us-east-1": "20260408"}


from scripts.pricing_cache import _parse_version_ts, _is_version_stale


def test_parse_version_ts():
    assert _parse_version_ts("20260416164016") == datetime(2026, 4, 16, 16, 40, 16, tzinfo=timezone.utc)


def test_fresh_version_not_stale():
    assert _is_version_stale("20260416164016", "20260420000000") is False


def test_version_over_one_year_is_stale():
    # cached 2025-01-01, upstream 2026-02-01 → 13 months delta
    assert _is_version_stale("20250101000000", "20260201000000") is True


def test_exactly_below_365_days_not_stale():
    # 364 days delta
    assert _is_version_stale("20250420000000", "20260419000000") is False
