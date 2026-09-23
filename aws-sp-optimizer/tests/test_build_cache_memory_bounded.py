"""Tests for P7.2: memory-bounded build_cache (sequential extract + subprocess isolation)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts._common import CacheError

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Sub-fix 2a: sequential extract + del pattern
# ---------------------------------------------------------------------------


def test_build_cache_del_pattern_releases_intermediate(tmp_path, monkeypatch):
    """build_cache with the new sequential-extract pattern must still produce
    the correct RatioEntry for a SKU present in both the SP sheet and EC2 OD sheet."""
    import responses

    from scripts.pricing_cache import build_cache

    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", tmp_path / "ratios.json.gz")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_VERSION_PATH", tmp_path / "version.json")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_DATA_DIR", tmp_path)

    sp_rate_sheet = json.loads((FIXTURES / "mini_sp_rate_sheet.json").read_text())
    ec2_od_rate_sheet = json.loads((FIXTURES / "mini_ec2_od_rate_sheet.json").read_text())
    lambda_od_rate_sheet = {
        "products": {"LAMBDA_GBSEC_STD": {"attributes": {"group": "Lambda-GB-Second"}}},
        "terms": {
            "OnDemand": {
                "LAMBDA_GBSEC_STD": {
                    "LAMBDA_GBSEC_STD.X": {
                        "priceDimensions": {
                            "LAMBDA_GBSEC_STD.X.PD": {
                                "pricePerUnit": {"USD": "0.00001667"},
                                "unit": "seconds",
                            }
                        }
                    }
                }
            }
        },
    }
    ecs_od_rate_sheet = {
        "products": {"FARGATE_VCPU": {"attributes": {"usagetype": "Fargate-vCPU-Hours:perCPU"}}},
        "terms": {
            "OnDemand": {
                "FARGATE_VCPU": {
                    "FARGATE_VCPU.X": {
                        "priceDimensions": {
                            "FARGATE_VCPU.X.PD": {
                                "pricePerUnit": {"USD": "0.04048"},
                                "unit": "Hrs",
                            }
                        }
                    }
                }
            }
        },
    }

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://pricing.us-east-1.amazonaws.com/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/v1/us-east-1/index.json",
            json=sp_rate_sheet,
        )
        rsps.add(
            responses.GET,
            "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/v1/us-east-1/index.json",
            json=ec2_od_rate_sheet,
        )
        rsps.add(
            responses.GET,
            "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/region_index.json",
            json={
                "regions": {
                    "us-east-1": {
                        "currentVersionUrl": "/offers/v1.0/aws/AWSLambda/20260410/us-east-1/index.json"
                    }
                }
            },
        )
        rsps.add(
            responses.GET,
            "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/20260410/us-east-1/index.json",
            json=lambda_od_rate_sheet,
        )
        rsps.add(
            responses.GET,
            "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/region_index.json",
            json={
                "regions": {
                    "us-east-1": {
                        "currentVersionUrl": "/offers/v1.0/aws/AmazonECS/20260410/us-east-1/index.json"
                    }
                }
            },
        )
        rsps.add(
            responses.GET,
            "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/20260410/us-east-1/index.json",
            json=ecs_od_rate_sheet,
        )

        ratios = build_cache(
            required_regions={"us-east-1"},
            primary_region="us-east-1",
            sp_versions={"us-east-1": "v1"},
            od_versions={"us-east-1": "v1"},

        )

    assert "us-east-1" in ratios.ec2
    entry = ratios.ec2["us-east-1"]["c5.xlarge|RunInstances"]
    assert abs(entry.sp_rate_usd - 0.1230) < 1e-6
    assert abs(entry.od_rate_usd - 0.1700) < 1e-6
    assert abs(entry.ratio - 0.1230 / 0.1700) < 1e-6
    assert abs(entry.discount_pct - (1 - 0.1230 / 0.1700)) < 1e-6
    assert entry.os == "Linux"
    assert entry.tenancy == "Shared"


# ---------------------------------------------------------------------------
# Sub-fix 2b: subprocess isolation
# ---------------------------------------------------------------------------

# Shared SP and EC2 region_index payloads reused across subprocess tests.
_SP_INDEX_PAYLOAD = {
    "regions": [
        {
            "regionCode": "us-east-1",
            "versionUrl": "/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/v1/us-east-1/index.json",
        }
    ]
}
_EC2_INDEX_PAYLOAD = {
    "regions": {
        "us-east-1": {
            "currentVersionUrl": "/offers/v1.0/aws/AmazonEC2/v1/us-east-1/index.json"
        }
    }
}
_SP_INDEX_URL = "https://pricing.us-east-1.amazonaws.com/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/current/region_index.json"
_EC2_INDEX_URL = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/region_index.json"


def _make_mock_executor(future_result=None, future_side_effect=None):
    """Build a mock ProcessPoolExecutor context manager."""
    mock_future = MagicMock()
    if future_side_effect is not None:
        mock_future.result.side_effect = future_side_effect
    else:
        mock_future.result.return_value = future_result

    mock_executor = MagicMock()
    mock_executor.__enter__ = MagicMock(return_value=mock_executor)
    mock_executor.__exit__ = MagicMock(return_value=False)
    mock_executor.submit.return_value = mock_future
    return mock_executor, mock_future


import responses as responses_module  # noqa: E402 (needed for @responses.activate)


@responses_module.activate
def test_ensure_cache_fresh_calls_build_in_subprocess(tmp_path, monkeypatch):
    """When a rebuild is needed, ensure_cache_fresh must delegate to
    _build_cache_in_subprocess via ProcessPoolExecutor, then reload ratios from disk."""
    from datetime import datetime, timezone

    import scripts.pricing_cache as pc
    from scripts._common import RatioEntry, Ratios
    from scripts.pricing_cache import ensure_cache_fresh, ratios_to_json, write_ratios_atomic

    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", tmp_path / "ratios.json.gz")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_VERSION_PATH", tmp_path / "version.json")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_DATA_DIR", tmp_path)

    # Register upstream version index responses (no local cache → rebuild triggered).
    responses_module.add(responses_module.GET, _SP_INDEX_URL, json=_SP_INDEX_PAYLOAD)
    responses_module.add(responses_module.GET, _EC2_INDEX_URL, json=_EC2_INDEX_PAYLOAD)

    # Pre-write ratios.json so load_and_validate_ratios succeeds after subprocess.
    dummy_ratios = Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime.now(timezone.utc),
        ec2={"us-east-1": {"c5.xlarge|RunInstances": RatioEntry(0.1, 0.2, 0.5, 0.5)}},
        lambda_rates={},
        fargate={},
        built_from_versions={
            "sp_by_region": {"us-east-1": "v1"},
            "od_by_region": {"us-east-1": "v1"},
        },
        primary_region="us-east-1",
    )
    write_ratios_atomic(tmp_path / "ratios.json.gz", ratios_to_json(dummy_ratios))

    mock_executor, _mock_future = _make_mock_executor(future_result=None)

    with patch("scripts.pricing_cache.ProcessPoolExecutor", return_value=mock_executor) as mock_ppe:
        result = ensure_cache_fresh(
            required_regions={"us-east-1"},
            primary_region="us-east-1",
            refresh_cache=True,
        )

    # ProcessPoolExecutor was created with max_workers=1.
    mock_ppe.assert_called_once()
    assert mock_ppe.call_args[1].get("max_workers") == 1

    # submit was called with _build_cache_in_subprocess as the callable.
    mock_executor.submit.assert_called_once()
    submit_args = mock_executor.submit.call_args[0]
    assert submit_args[0] is pc._build_cache_in_subprocess
    assert submit_args[1] == {"us-east-1"}  # required_regions
    assert submit_args[2] == "us-east-1"  # primary_region

    # load_and_validate_ratios ran from the pre-written ratios.json.
    assert "us-east-1" in result.ec2


@responses_module.activate
def test_subprocess_cache_error_propagates(tmp_path, monkeypatch):
    """CacheError raised inside the subprocess must re-raise unchanged (not wrapped)."""
    from scripts.pricing_cache import ensure_cache_fresh

    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", tmp_path / "ratios.json.gz")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_VERSION_PATH", tmp_path / "version.json")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_DATA_DIR", tmp_path)

    responses_module.add(responses_module.GET, _SP_INDEX_URL, json=_SP_INDEX_PAYLOAD)
    responses_module.add(responses_module.GET, _EC2_INDEX_URL, json=_EC2_INDEX_PAYLOAD)

    inner_error = CacheError(code="test_err", message="test")
    mock_executor, _mock_future = _make_mock_executor(future_side_effect=inner_error)

    with patch("scripts.pricing_cache.ProcessPoolExecutor", return_value=mock_executor):
        with pytest.raises(CacheError) as excinfo:
            ensure_cache_fresh(
                required_regions={"us-east-1"},
                primary_region="us-east-1",
                refresh_cache=True,
            )

    assert excinfo.value.code == "test_err"
    assert excinfo.value is inner_error  # same object, not wrapped


@responses_module.activate
def test_subprocess_unexpected_exception_wraps_as_cache_error(tmp_path, monkeypatch):
    """An unexpected exception from the subprocess must be wrapped in a
    CacheError with code='pricing_cache_subprocess_failed'."""
    from scripts.pricing_cache import ensure_cache_fresh

    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", tmp_path / "ratios.json.gz")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_VERSION_PATH", tmp_path / "version.json")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_DATA_DIR", tmp_path)

    responses_module.add(responses_module.GET, _SP_INDEX_URL, json=_SP_INDEX_PAYLOAD)
    responses_module.add(responses_module.GET, _EC2_INDEX_URL, json=_EC2_INDEX_PAYLOAD)

    mock_executor, _mock_future = _make_mock_executor(future_side_effect=RuntimeError("boom"))

    with patch("scripts.pricing_cache.ProcessPoolExecutor", return_value=mock_executor):
        with pytest.raises(CacheError) as excinfo:
            ensure_cache_fresh(
                required_regions={"us-east-1"},
                primary_region="us-east-1",
                refresh_cache=True,
            )

    err = excinfo.value
    assert err.code == "pricing_cache_subprocess_failed"
    assert isinstance(err.__cause__, RuntimeError)
    assert "boom" in err.message
