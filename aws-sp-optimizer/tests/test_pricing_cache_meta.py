from datetime import datetime, timezone

import pytest

from scripts._common import PricingCacheMeta, RegionVersionInfo, VersionMetadata


def test_region_version_info():
    info = RegionVersionInfo(
        sp_version="20260410",
        od_version="20260408",
        sp_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        od_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ratio_entries_count=3847,
    )
    assert info.sp_version == "20260410"


def test_version_metadata_minimal():
    meta = VersionMetadata(
        schema_version=1,
        last_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        aws_sp_optimizer_version="0.1.0",
        regions={},
        lambda_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        fargate_refreshed_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
    )
    assert meta.schema_version == 1


def test_pricing_cache_meta_fields():
    m = PricingCacheMeta(
        version="20260410",
        age_hours=47.5,
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        regions=["us-east-1", "eu-central-1"],
    )
    assert m.version == "20260410"
    assert m.age_hours == pytest.approx(47.5)
    assert m.regions == ["us-east-1", "eu-central-1"]
