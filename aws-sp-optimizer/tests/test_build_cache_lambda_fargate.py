"""Tests for Lambda + Fargate paths in build_cache. Uses the same fixture
as Task 2.8 plus the inline Lambda/ECS OD sheets."""

import json
from pathlib import Path

import responses

FIXTURES = Path(__file__).parent / "fixtures"


@responses.activate
def test_build_cache_populates_lambda(tmp_path, monkeypatch):
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
                    "LAMBDA_GBSEC_STD.JRTCKXETXF": {
                        "priceDimensions": {
                            "LAMBDA_GBSEC_STD.JRTCKXETXF.6YS6EN2CT7": {
                                "pricePerUnit": {"USD": "0.00001667"},
                                "unit": "seconds",
                            },
                        },
                    },
                },
            },
        },
    }
    ecs_od_rate_sheet = {
        "products": {"FARGATE_VCPU": {"attributes": {"usagetype": "Fargate-vCPU-Hours:perCPU"}}},
        "terms": {
            "OnDemand": {
                "FARGATE_VCPU": {
                    "FARGATE_VCPU.X.Y": {
                        "priceDimensions": {
                            "FARGATE_VCPU.X.Y.PD": {
                                "pricePerUnit": {"USD": "0.04048"},
                                "unit": "Hrs",
                            },
                        },
                    },
                },
            },
        },
    }

    # Mock SP + EC2 OD rate sheets
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

    # _build_lambda_rates / _build_fargate_rates resolve the OD version at
    # call time via region_index.json. Mock both the index AND the rate sheet.
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

    lambda_entry = ratios.lambda_rates["Lambda-GB-Second"]
    assert abs(lambda_entry.sp_rate_usd - 0.00001483) < 1e-10
    assert abs(lambda_entry.od_rate_usd - 0.00001667) < 1e-10
    assert abs(lambda_entry.ratio - 0.00001483 / 0.00001667) < 1e-6

    fargate_entry = ratios.fargate["us-east-1"]["Fargate-vCPU-Hours:perCPU"]
    assert abs(fargate_entry.sp_rate_usd - 0.03278) < 1e-6
    assert abs(fargate_entry.od_rate_usd - 0.04048) < 1e-6
