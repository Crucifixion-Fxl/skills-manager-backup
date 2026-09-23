"""Tests for build_purchase_instructions — structure + INV-10 consistency."""

from datetime import datetime, timezone

from scripts._common import (
    BaselineStats,
    DiscountAudit,
    FinalResult,
    OrgConfig,
    SPAudit,
)
from scripts.output_builder import build_purchase_instructions


def _final(c_new_delta: float = 5.6789) -> FinalResult:
    stats = BaselineStats(
        sample_size_hours=2160,
        expected_sample_size_hours=2160,
        completeness_pct=1.0,
        p0=0,
        p5=0,
        p10=0,
        p17=0,
        p25=0,
        p50=0,
        p75=0,
        p83=0,
        p90=0,
        p95=0,
        p100=0,
        mean=10.0,
        std=0,
        min=0,
        max=0,
    )
    d_audit = DiscountAudit(
        d_raw=0.17,
        d_calibrated=0.17,
        e_sp=0.09,
        e_od=0.09,
        edp_uniform=True,
        calibration_applied=False,
        source="aws_public_pricing_api_weighted",
        pricing_cache_version="v1",
        pricing_cache_fetched_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        mix_window_days=30,
        coverage_pct=1.0,
        matched_entries=50,
        unmatched_entries=0,
        unmatched_top10_by_cost=[],
    )
    sp_audit = SPAudit(
        count=0,
        compute_count=0,
        non_compute_count=0,
        total_commitment_all=0,
        compute_commitment=0,
        utilization_30d=1.0,
        utilization_source="ce_api",
        effective=0,
        has_non_compute_sps=False,
        active_sp_list=[],
    )
    return FinalResult(
        C_star_total=c_new_delta,
        C_existing_effective=0.0,
        C_new_delta=c_new_delta,
        d_blended=0.17,
        formula_branch="stationary",
        trend_classification="stable",
        trend_buckets=[],
        trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None,
        non_monotonic_pattern=None,
        baseline_stats=stats,
        bootstrap_se=0.0,
        bootstrap_95ci=None,
        d_audit=d_audit,
        sp_audit=sp_audit,
        trend_aware_audit=None,
    )


def _cfg() -> OrgConfig:
    return OrgConfig(
        alias="a4x-us",
        description=None,
        profile="aws-prod",
        payer_account_id="111111111111",
        org_id="o-abc",
        cur_database="d",
        cur_table="t",
        athena_output="s3://b/p/",
        athena_workgroup="primary",
        primary_region="us-east-1",
        window_days=90,
        window_end="today",
        prefer="balanced",
    )


def test_structure_and_keys():
    pi = build_purchase_instructions(_cfg(), _final())
    assert "headline_warning" in pi
    assert pi["summary"]["product"] == "1 year No Upfront Compute Savings Plan"
    assert pi["step_1_lookup_offering_id"]["command"].startswith(
        "aws savingsplans describe-savings-plans-offerings"
    )
    cmd = pi["step_2_create_savings_plan"]["command_template"]
    assert "aws savingsplans create-savings-plan" in cmd
    assert "--commitment '5.6789'" in cmd  # exactly 4 decimal places
    assert pi["safety_checklist_before_running"]
    assert pi["alternatives_if_not_confident"]


def test_inv10_commitment_uses_rounded_value():
    """Commitment string must match round(C_new_delta, 4)."""
    # Case where raw float would round-trip differently via format-spec
    final = _final(c_new_delta=5.67895)
    pi = build_purchase_instructions(_cfg(), final)
    import re

    match = re.search(
        r"--commitment '([^']+)'", pi["step_2_create_savings_plan"]["command_template"]
    )
    assert match
    commit_str = match.group(1)
    rounded = round(5.67895, 4)
    assert commit_str == f"{rounded:.4f}"
