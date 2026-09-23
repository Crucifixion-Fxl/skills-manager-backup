"""Exhaustive tests for Ratios.lookup dispatch — EC2/Lambda/Fargate paths."""

from datetime import datetime, timezone

import pytest

from scripts._common import RatioEntry, Ratios


def _make_ratios() -> Ratios:
    return Ratios(
        schema_version=1,
        term="1 year No Upfront Compute Savings Plan",
        built_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        ec2={
            "us-east-1": {
                "c5.xlarge|RunInstances": RatioEntry(0.123, 0.17, 0.7235, 0.2765),
                "c5.xlarge|RunInstances:0002": RatioEntry(0.180, 0.25, 0.72, 0.28),  # Windows
            },
        },
        lambda_rates={
            "Lambda-GB-Second": RatioEntry(0.00001483, 0.00001667, 0.8896, 0.1104),
            "Lambda-GB-Second-ARM": RatioEntry(0.00001200, 0.00001333, 0.9, 0.1),
        },
        fargate={
            "us-east-1": {
                "Fargate-vCPU-Hours:perCPU": RatioEntry(0.03278, 0.04048, 0.8099, 0.1901),
            },
        },
        built_from_versions={
            "sp_by_region": {"us-east-1": "20260410"},
            "od_by_region": {"us-east-1": "20260408"},
        },
        primary_region="us-east-1",
    )


def test_ec2_linux_lookup():
    r = _make_ratios()
    entry = r.lookup(
        "AmazonEC2", "us-east-1", "c5.xlarge", "RunInstances", "USE1-BoxUsage:c5.xlarge"
    )
    assert entry is not None
    assert entry.ratio == pytest.approx(0.7235)


def test_ec2_windows_lookup():
    r = _make_ratios()
    entry = r.lookup(
        "AmazonEC2", "us-east-1", "c5.xlarge", "RunInstances:0002", "USE1-BoxUsage:c5.xlarge"
    )
    assert entry is not None
    assert entry.ratio == pytest.approx(0.72)


def test_ec2_unknown_instance_type_returns_none():
    r = _make_ratios()
    assert r.lookup("AmazonEC2", "us-east-1", "zzz.unknown", "RunInstances", "x") is None


def test_ec2_unknown_region_returns_none():
    r = _make_ratios()
    assert r.lookup("AmazonEC2", "eu-west-99", "c5.xlarge", "RunInstances", "x") is None


def test_ec2_null_instance_type_returns_none():
    """Lambda/Fargate CUR rows have product_instance_type=NULL so they must
    NOT accidentally hit the EC2 table."""
    r = _make_ratios()
    assert r.lookup("AmazonEC2", "us-east-1", None, "Compute", "x") is None


def test_lambda_lookup_by_usage_type():
    r = _make_ratios()
    entry = r.lookup("AWSLambda", "us-east-1", None, "Invoke", "Lambda-GB-Second")
    assert entry is not None
    assert entry.ratio == pytest.approx(0.8896)


def test_lambda_arm_variant():
    r = _make_ratios()
    entry = r.lookup("AWSLambda", "us-east-1", None, "Invoke", "Lambda-GB-Second-ARM")
    assert entry is not None
    assert entry.ratio == pytest.approx(0.9)


def test_lambda_unknown_usage_type_returns_none():
    r = _make_ratios()
    assert r.lookup("AWSLambda", "us-east-1", None, "Invoke", "Lambda-Other") is None


def test_fargate_ecs_lookup():
    r = _make_ratios()
    entry = r.lookup("AmazonECS", "us-east-1", None, "Compute", "Fargate-vCPU-Hours:perCPU")
    assert entry is not None
    assert entry.ratio == pytest.approx(0.8099)


def test_fargate_eks_uses_same_table():
    r = _make_ratios()
    entry = r.lookup("AmazonEKS", "us-east-1", None, "Compute", "Fargate-vCPU-Hours:perCPU")
    assert entry is not None
    assert entry.ratio == pytest.approx(0.8099)


def test_unknown_product_code_returns_none():
    r = _make_ratios()
    assert r.lookup("AmazonS3", "us-east-1", None, "Get", "x") is None
