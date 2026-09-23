"""Tests for NeedsCacheRefreshError raising path in ensure_cache_fresh."""
from unittest.mock import MagicMock, patch

import pytest

from scripts._common import NeedsCacheRefreshError
from scripts.pricing_cache import ensure_cache_fresh


def _make_cached_meta(sp_version="20240101000000", od_version="20240101000000"):
    meta = MagicMock()
    meta.schema_version = 1
    region_info = MagicMock(sp_version=sp_version, od_version=od_version)
    meta.regions = {"us-east-1": region_info}
    meta.last_refreshed_at = MagicMock()
    meta.last_refreshed_at.isoformat = MagicMock(return_value="2024-01-01T00:00:00+00:00")
    return meta


def test_stale_cache_raises_when_refresh_cache_false(monkeypatch):
    """upstream is >1y newer than cached → raise NeedsCacheRefreshError with code='cache_stale_over_1year'."""
    with patch("scripts.pricing_cache.load_version_metadata", return_value=_make_cached_meta()), \
         patch("scripts.pricing_cache.fetch_sp_region_versions",
               return_value={"us-east-1": "20260416164016"}), \
         patch("scripts.pricing_cache.fetch_od_region_versions",
               return_value={"us-east-1": "20260416164016"}):
        with pytest.raises(NeedsCacheRefreshError) as exc:
            ensure_cache_fresh(
                required_regions={"us-east-1"},
                primary_region="us-east-1",
                refresh_cache=False,
                skip_refresh=False,
            )
        assert exc.value.code == "cache_stale_over_1year"
        assert "us-east-1" in exc.value.context.get("stale_regions", [])


def test_missing_region_raises_when_refresh_cache_false(monkeypatch):
    """Required region absent from cached_meta → raise with code='cache_missing_regions'."""
    meta = _make_cached_meta()  # only has us-east-1
    with patch("scripts.pricing_cache.load_version_metadata", return_value=meta), \
         patch("scripts.pricing_cache.fetch_sp_region_versions",
               return_value={"us-east-1": "20240101000000", "eu-west-1": "20240101000000"}), \
         patch("scripts.pricing_cache.fetch_od_region_versions",
               return_value={"us-east-1": "20240101000000", "eu-west-1": "20240101000000"}):
        with pytest.raises(NeedsCacheRefreshError) as exc:
            ensure_cache_fresh(
                required_regions={"us-east-1", "eu-west-1"},
                primary_region="us-east-1",
                refresh_cache=False,
                skip_refresh=False,
            )
        assert exc.value.code == "cache_missing_regions"
        assert "eu-west-1" in exc.value.context.get("missing_regions", [])


def test_refresh_cache_true_still_rebuilds(monkeypatch):
    """When refresh_cache=True, do NOT raise — proceed to rebuild path (subprocess)."""
    # We stub the subprocess executor entirely so the test doesn't spawn a real process.
    meta = _make_cached_meta()
    with patch("scripts.pricing_cache.load_version_metadata", return_value=meta), \
         patch("scripts.pricing_cache.fetch_sp_region_versions",
               return_value={"us-east-1": "20260416164016"}), \
         patch("scripts.pricing_cache.fetch_od_region_versions",
               return_value={"us-east-1": "20260416164016"}), \
         patch("scripts.pricing_cache.multiprocessing"), \
         patch("scripts.pricing_cache.ProcessPoolExecutor") as pool_cls, \
         patch("scripts.pricing_cache.load_and_validate_ratios", return_value="FAKE_RATIOS"):
        pool_instance = MagicMock()
        pool_cls.return_value.__enter__ = MagicMock(return_value=pool_instance)
        pool_cls.return_value.__exit__ = MagicMock(return_value=False)
        fake_future = MagicMock()
        fake_future.result = MagicMock(return_value=None)
        pool_instance.submit = MagicMock(return_value=fake_future)
        result = ensure_cache_fresh(
            required_regions={"us-east-1"},
            primary_region="us-east-1",
            refresh_cache=True,
            skip_refresh=False,
        )
        assert result == "FAKE_RATIOS"
