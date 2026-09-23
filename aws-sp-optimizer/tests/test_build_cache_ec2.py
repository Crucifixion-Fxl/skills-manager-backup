"""Tests for build_cache EC2 flow — parses fixture rate sheets."""

import json
from pathlib import Path

import responses

FIXTURES = Path(__file__).parent / "fixtures"


@responses.activate
def test_build_cache_ec2_joins_sp_and_od(tmp_path, monkeypatch):
    from scripts.pricing_cache import build_cache

    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_RATIOS_PATH", tmp_path / "ratios.json.gz")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_VERSION_PATH", tmp_path / "version.json")
    monkeypatch.setattr("scripts.pricing_cache.SHIPPED_DATA_DIR", tmp_path)

    sp_rate_sheet = json.loads((FIXTURES / "mini_sp_rate_sheet.json").read_text())
    ec2_od_rate_sheet = json.loads((FIXTURES / "mini_ec2_od_rate_sheet.json").read_text())
    lambda_od_rate_sheet = {
        "terms": {
            "OnDemand": {
                "LAMBDA_GBSEC_STD": {
                    "LAMBDA_GBSEC_STD.X.Y": {
                        "priceDimensions": {
                            "LAMBDA_GBSEC_STD.X.Y.PD": {
                                "pricePerUnit": {"USD": "0.00001667"},
                                "unit": "seconds",
                            }
                        }
                    }
                }
            }
        },
        "products": {"LAMBDA_GBSEC_STD": {"attributes": {"group": "Lambda-GB-Second"}}},
    }
    ecs_od_rate_sheet = {
        "terms": {
            "OnDemand": {
                "FARGATE_VCPU": {
                    "FARGATE_VCPU.X.Y": {
                        "priceDimensions": {
                            "FARGATE_VCPU.X.Y.PD": {
                                "pricePerUnit": {"USD": "0.04048"},
                                "unit": "Hrs",
                            }
                        }
                    }
                }
            }
        },
        "products": {"FARGATE_VCPU": {"attributes": {"usagetype": "Fargate-vCPU-Hours:perCPU"}}},
    }

    # Mock SP + EC2 OD rate sheets (hardcoded to 'v1' because sp_versions /
    # od_versions are passed into build_cache; Lambda + ECS fetch their own
    # current version via region_index.json in Task 2.9's helpers — we mock
    # both URLs here so that Task 2.9 doesn't break this test).
    responses.add(
        responses.GET,
        "https://pricing.us-east-1.amazonaws.com/savingsPlan/v1.0/aws/AWSComputeSavingsPlan/v1/us-east-1/index.json",
        json=sp_rate_sheet,
    )
    responses.add(
        responses.GET,
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/v1/us-east-1/index.json",
        json=ec2_od_rate_sheet,
    )
    responses.add(
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
    responses.add(
        responses.GET,
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/20260410/us-east-1/index.json",
        json=lambda_od_rate_sheet,
    )
    responses.add(
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
    responses.add(
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
    # sp_rate = 0.1230, od_rate = 0.1700, ratio ≈ 0.7235, discount ≈ 0.2765
    assert abs(entry.sp_rate_usd - 0.1230) < 1e-6
    assert abs(entry.od_rate_usd - 0.1700) < 1e-6
    assert abs(entry.ratio - 0.1230 / 0.1700) < 1e-6
    assert abs(entry.discount_pct - (1 - 0.1230 / 0.1700)) < 1e-6
    assert entry.os == "Linux"
    assert entry.tenancy == "Shared"
