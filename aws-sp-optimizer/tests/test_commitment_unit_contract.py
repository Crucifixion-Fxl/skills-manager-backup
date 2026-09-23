"""Contract test: recommendation ↔ purchase_instructions ↔ CLI `--commitment`
must all use identical AWS list SP fee $/hr units. Locks the MR !285 P0 fix.

If any of the four sites drift out of sync (e.g. someone reintroduces a net-vs-list
conversion in one place but not another), this test fails with a clear message
pointing at the mismatch.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from scripts._common import (
    BaselineStats,
    DiscountAudit,
    FinalResult,
    OrgConfig,
    SPAudit,
)
from scripts.output_builder import build_purchase_instructions

ORG = OrgConfig(
    alias="t",
    description=None,
    profile="prof",
    payer_account_id="111111111111",
    org_id="o-abc",
    cur_database="d",
    cur_table="t",
    athena_output="s3://b/p/",
    athena_workgroup="primary",
    primary_region="us-east-1",
    window_days=60,
    window_end="today",
    prefer="balanced",
)


def _final_result(c_new_delta: float, d_raw: float, e_sp: float, e_od: float) -> FinalResult:
    stats = BaselineStats(
        sample_size_hours=2160, expected_sample_size_hours=2160, completeness_pct=1.0,
        p0=0, p5=0, p10=0, p17=0, p25=0, p50=c_new_delta, p75=0, p83=0, p90=0,
        p95=0, p100=0, mean=c_new_delta, std=0, min=0, max=0,
    )
    d_cal = 1.0 - (1.0 - d_raw) * (1.0 - e_sp) / (1.0 - e_od)
    d_audit = DiscountAudit(
        d_raw=d_raw, d_calibrated=d_cal, e_sp=e_sp, e_od=e_od,
        edp_uniform=(abs(e_sp - e_od) <= 0.005),
        calibration_applied=(abs(e_sp - e_od) > 0.005),
        source="aws_public_pricing_api_weighted",
        pricing_cache_version="v1",
        pricing_cache_fetched_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
        mix_window_days=30, coverage_pct=1.0, matched_entries=50,
        unmatched_entries=0, unmatched_top10_by_cost=[],
    )
    sp_audit = SPAudit(
        count=0, compute_count=0, non_compute_count=0,
        total_commitment_all=0, compute_commitment=0,
        utilization_30d=1.0, utilization_source="ce_api",
        effective=0, has_non_compute_sps=False, active_sp_list=[],
    )
    return FinalResult(
        C_star_total=c_new_delta, C_existing_effective=0.0, C_new_delta=c_new_delta,
        d_blended=d_cal, formula_branch="stationary", trend_classification="stable",
        trend_buckets=[], trend_b3_over_b1_ratio=1.0,
        non_monotonic_swing_pct=None, non_monotonic_pattern=None,
        baseline_stats=stats, bootstrap_se=0.0, bootstrap_95ci=None,
        d_audit=d_audit, sp_audit=sp_audit, trend_aware_audit=None,
    )


def _extract_commitment_from_command(cmd: str) -> str:
    """Pull the `--commitment 'X.XXXX'` number out of the aws CLI command string."""
    m = re.search(r"--commitment '(\d+\.\d{4})'", cmd)
    assert m is not None, f"could not find --commitment in command: {cmd[:200]}"
    return m.group(1)


def test_commitment_unit_consistency_across_all_four_sites():
    """The recommendation, purchase summary, CLI argument, and purpose-string price
    must all be the same number (list SP fee $/hr) for the same FinalResult.

    Regression guard for the MR !285 P0 where `delta_per_hour_to_buy` was in
    net OD-equivalent $/hr but plugged directly into `--commitment` which AWS
    bills at list SP fee rate — a factor-of-(1-d_raw)/(1-e_od) over-commitment.
    """
    delta = 5.6789
    # Non-uniform EDP amplifies any unit drift — worst-case regression detector
    final = _final_result(c_new_delta=delta, d_raw=0.17, e_sp=0.03, e_od=0.09)
    pi = build_purchase_instructions(ORG, final, exclude_filter=None)

    # Site 1: purchase_instructions.summary.commitment_per_hour_usd (rounded)
    summary_value = pi["summary"]["commitment_per_hour_usd"]
    # Site 2: --commitment '...' in the CLI command template
    cli_value = float(_extract_commitment_from_command(pi["step_2_create_savings_plan"]["command_template"]))
    # Site 3: parameters_explained["--commitment"].value (4-digit string)
    param_value = float(pi["step_2_create_savings_plan"]["parameters_explained"]["--commitment"]["value"])
    # Site 4: the dollar figure embedded in the purpose string
    purpose = pi["step_2_create_savings_plan"]["purpose"]
    m = re.search(r"\$(\d+\.\d{4})/hr", purpose)
    assert m is not None, f"no $/hr in purpose string: {purpose}"
    purpose_value = float(m.group(1))

    # Four-way equality — any divergence = unit mismatch has reappeared
    expected = round(delta, 4)
    assert summary_value == expected, f"summary.commitment_per_hour_usd mismatch: {summary_value} vs {expected}"
    assert cli_value == expected, f"CLI --commitment mismatch: {cli_value} vs {expected}"
    assert param_value == expected, f"parameters_explained --commitment mismatch: {param_value} vs {expected}"
    assert purpose_value == expected, f"purpose string $/hr mismatch: {purpose_value} vs {expected}"


def test_annual_delta_matches_commitment_times_hours():
    """annual_delta_usd / 8760 must equal commitment_per_hour_usd exactly.

    AWS bills list SP fee A for 8760 h/yr; annual_delta must be that gross
    figure (post-EDP fee is surfaced separately via expected_annual_sp_fee_usd).
    """
    delta = 12.3456
    final = _final_result(c_new_delta=delta, d_raw=0.17, e_sp=0.03, e_od=0.09)
    pi = build_purchase_instructions(ORG, final, exclude_filter=None)

    annual = pi["summary"]["annual_total_usd"]
    commit = pi["summary"]["commitment_per_hour_usd"]
    assert abs(annual - commit * 8760) < 0.02, (
        f"annual_total_usd ({annual}) != commitment ({commit}) × 8760 ({commit * 8760})"
    )


def test_zero_delta_emits_all_zeros_consistently():
    """Edge case: if C_new_delta is 0 (existing SPs cover full need), every
    monetary site must be 0.0 — no hidden non-zero units creeping in."""
    final = _final_result(c_new_delta=0.0, d_raw=0.17, e_sp=0.03, e_od=0.09)
    pi = build_purchase_instructions(ORG, final, exclude_filter=None)

    # Output fields round() to 4 or 2 decimals, so 0 commitment rounds to
    # literal 0.0 with zero float drift. Bound instead of `==` to satisfy S1244.
    assert abs(pi["summary"]["commitment_per_hour_usd"]) < 1e-9
    assert abs(pi["summary"]["annual_total_usd"]) < 1e-9
    cli_value = float(_extract_commitment_from_command(pi["step_2_create_savings_plan"]["command_template"]))
    assert abs(cli_value) < 1e-9
