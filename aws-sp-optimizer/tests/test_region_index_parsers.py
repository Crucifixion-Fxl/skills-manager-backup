"""Tests for the dual-shape region_index parsers (P7.1).

SP index uses list shape {"regions": [{"regionCode", "versionUrl"}]}.
EC2/Lambda/ECS offer indexes use dict shape {"regions": {region_code: {"currentVersionUrl": ...}}}.
"""

from unittest.mock import patch

import pytest

from scripts._common import CacheError
from scripts.pricing_cache import (
    fetch_od_region_versions,
    fetch_sp_region_versions,
)

# ---------------------------------------------------------------------------
# SP list-shape parser tests
# ---------------------------------------------------------------------------


def test_sp_region_index_parses_list_shape():
    """Real AWS shape: regions is a list of {regionCode, versionUrl} objects."""
    payload = {
        "regions": [
            {
                "regionCode": "us-east-1",
                "versionUrl": "/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/20260410233015/us-east-1/index.json",
            },
            {
                "regionCode": "eu-west-1",
                "versionUrl": "/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/20260410233015/eu-west-1/index.json",
            },
        ]
    }
    with patch("scripts.pricing_cache.stream_download_json", return_value=payload):
        result = fetch_sp_region_versions({"us-east-1"})
    assert result == {"us-east-1": "20260410233015"}


def test_sp_region_index_missing_region_raises():
    """If the requested region is absent from the SP list, raise region_not_in_sp_index."""
    payload = {
        "regions": [
            {
                "regionCode": "eu-west-1",
                "versionUrl": "/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/20260410233015/eu-west-1/index.json",
            }
        ]
    }
    with patch("scripts.pricing_cache.stream_download_json", return_value=payload):
        with pytest.raises(CacheError) as excinfo:
            fetch_sp_region_versions({"us-east-1"})
    assert excinfo.value.code == "region_not_in_sp_index"


def test_sp_region_index_shape_guard():
    """If regions is a dict (old assumed shape), guard raises sp_region_index_shape_changed."""
    payload = {
        "regions": {
            "us-east-1": {
                "currentVersionUrl": "/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/20260410/us-east-1/index.json"
            }
        }
    }
    with patch("scripts.pricing_cache.stream_download_json", return_value=payload):
        with pytest.raises(CacheError) as excinfo:
            fetch_sp_region_versions({"us-east-1"})
    assert excinfo.value.code == "sp_region_index_shape_changed"


# ---------------------------------------------------------------------------
# Offer dict-shape parser tests
# ---------------------------------------------------------------------------


def test_offer_region_index_parses_dict_shape():
    """Real AWS shape: regions is a dict keyed by region code with currentVersionUrl."""
    payload = {
        "regions": {
            "us-east-1": {
                "currentVersionUrl": "/offers/v1.0/aws/AmazonEC2/20260410233128/us-east-1/index.json"
            }
        }
    }
    with patch("scripts.pricing_cache.stream_download_json", return_value=payload):
        result = fetch_od_region_versions({"us-east-1"})
    assert result == {"us-east-1": "20260410233128"}


def test_offer_region_index_missing_region_raises():
    """If the requested region is absent from the offer dict, raise region_not_in_od_index."""
    payload = {
        "regions": {
            "eu-west-1": {
                "currentVersionUrl": "/offers/v1.0/aws/AmazonEC2/20260410233128/eu-west-1/index.json"
            }
        }
    }
    with patch("scripts.pricing_cache.stream_download_json", return_value=payload):
        with pytest.raises(CacheError) as excinfo:
            fetch_od_region_versions({"us-east-1"})
    assert excinfo.value.code == "region_not_in_od_index"
