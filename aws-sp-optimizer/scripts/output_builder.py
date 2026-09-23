"""Assemble Risk/Warning objects, build JSON output dict, purchase instructions.

See spec 6.4, 6.5, 6.6, 6.7, 8.x.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from scripts._common import (
    FinalResult,
    HourlySeries,
    OrgConfig,
    PricingCacheMeta,
    SPInfo,
    ValidatedCandidate,
    ValidationResult,
    Window,
    WindowSearchResult,
    base_output,
    compute_untagged_fraction,
)

# Warning code → (severity, message template) — full dict filled out in Phase 5
_WARNING_TEMPLATES: dict[str, tuple[str, str]] = {
    "resource_tags_user_service_missing": (
        "info",
        "CUR table lacks resource_tags_user_service column — untagged cost risk is disabled",
    ),
    "ce_api_unavailable_utilization_assumed_100": (
        "warning",
        "CE API unavailable — assuming 100% SP utilization",
    ),
    "non_compute_sps_ignored_in_gap_calculation": (
        "info",
        "Non-Compute SPs detected but not counted in gap calculation (v1 simplification)",
    ),
    "existing_sp_mild_low_utilization": (
        "warning",
        "Existing Compute SP utilization is 90-95%, slightly below optimal",
    ),
    "mild_decline_ignored_in_formula": (
        "warning",
        "Mild decline trend detected — stationary formula is used but may slightly over-commit",
    ),
    "pricing_cache_age_hours_high": (
        "info",
        "Pricing cache is older than 7 days",
    ),
    "trend_aware_projection_partially_clamped": (
        "warning",
        "Trend-aware projection clamped on 20-50% of horizon",
    ),
    "region_discovery_failed_fallback_to_primary": (
        "warning",
        "Region discovery query failed — using primary_region only (may cause d_blended coverage gap)",
    ),
    "region_discovery_many_regions_slow_cold_start": (
        "info",
        "Workload spans N regions — first cold start will take ~N×5 minutes to build pricing cache",
    ),
}


# Risk code → (severity, must_review, interpretation, chinese explanation, recommended actions)
_RISK_TEMPLATES: dict[str, dict] = {
    "non_monotonic_oscillation": {
        "severity": "high",
        "must_review_before_acting": True,
        "summary": "Baseline oscillates non-monotonically",
        "interpretation": "Non-monotonic workload — the newsvendor assumption of stationary demand is violated",
        "user_facing_explanation": "工作负载出现波动（非单调），formula 的前置假设（平稳分布）不满足；推荐值可能过高或过低",
        "recommended_actions_for_llm": [
            "Show the user the trend buckets and ask them to investigate the swing",
            "Recommend waiting for the workload to stabilize before purchasing",
        ],
    },
    "trend_aware_projection_clamped": {
        "severity": "high",
        "must_review_before_acting": True,
        "summary": "Trend-aware projection clamped on >50% of horizon",
        "interpretation": "Linear extrapolation would go negative; the floor dominates the projection",
        "user_facing_explanation": "趋势外推超过 50% 的时间被 floor 截断，说明线性外推不可靠",
        "recommended_actions_for_llm": [
            "Show the user both the stationary comparison and the clamped projection",
            "Recommend that they manually verify the business context",
        ],
    },
    "d_blended_coverage_low": {
        "severity": "high",
        "must_review_before_acting": True,
        "summary": "Discount rate coverage is below 90%",
        "interpretation": "Too many CUR rows couldn't be matched to the pricing cache — d_blended is unreliable",
        "user_facing_explanation": "d_blended 覆盖率低于 90%，说明定价缓存未能匹配到足够的 CUR 条目",
        "recommended_actions_for_llm": [
            "Run with --refresh-cache to rebuild the pricing cache",
            "Show the unmatched_top10_by_cost to the user and ask about the workload",
        ],
    },
    "d_blended_coverage_medium": {
        "severity": "medium",
        "must_review_before_acting": False,
        "summary": "Discount rate coverage 90-98%",
        "interpretation": "Most rows matched; the result is usable with slight uncertainty",
        "user_facing_explanation": "d_blended 覆盖率 90-98%，略有不确定性但可用",
        "recommended_actions_for_llm": [
            "Note the coverage percentage in your response to the user",
        ],
    },
    "existing_sp_low_utilization": {
        "severity": "medium",
        "must_review_before_acting": False,
        "summary": "Existing SP utilization below 90%",
        "interpretation": "Previous SP purchases are under-used; buying more may compound the problem",
        "user_facing_explanation": "当前已有的 Compute SP 利用率低于 90%，新增购买可能进一步加剧浪费",
        "recommended_actions_for_llm": [
            "Suggest investigating the underutilization before purchasing new SP",
        ],
    },
    "baseline_sample_size_low": {
        "severity": "medium",
        "must_review_before_acting": False,
        "summary": "Baseline completeness 90-95% — fewer samples than expected",
        "interpretation": "Completeness is between 90% and 95%; formula runs but confidence is reduced",
        "user_facing_explanation": "样本完整率 90-95%，结果仍然可用但置信度略低",
        "recommended_actions_for_llm": [
            "Acknowledge the reduced sample size when presenting the recommendation",
        ],
    },
    "recent_sp_expiry_near_window": {
        "severity": "medium",
        "must_review_before_acting": False,
        "summary": "A Compute SP expired just before or after the analysis window",
        "interpretation": "Baseline may reflect post-expiry reactive state OR recommendation may become stale within days",
        "user_facing_explanation": "临近分析窗口有 Compute SP 刚刚到期（或即将到期），基线或推荐可能失真",
        "recommended_actions_for_llm": [
            "Check whether the expired/expiring SP affects the validity of this recommendation",
        ],
    },
    "untagged_cost_fraction_high": {
        "severity": "low",
        "must_review_before_acting": False,
        "summary": "More than 10% of SP-eligible cost is untagged",
        "interpretation": "Tag coverage is low; root-cause attribution via resource_tags_user_service is limited",
        "user_facing_explanation": "超过 10% 的 SP 适用成本未打标签，后续归因成本归属的能力受限",
        "recommended_actions_for_llm": [
            "Mention the tag gap as a follow-up improvement",
        ],
    },
}


def make_risk(code: str, **details) -> dict:
    """Factory for Risk objects. Returns a plain dict matching the TS schema."""
    template = _RISK_TEMPLATES.get(code)
    if template is None:
        raise ValueError(f"Unknown risk code: {code!r}")
    return {
        "severity": template["severity"],
        "code": code,
        "must_review_before_acting": template["must_review_before_acting"],
        "summary": template["summary"],
        "details": dict(details),
        "interpretation": template["interpretation"],
        "user_facing_explanation": template["user_facing_explanation"],
        "recommended_actions_for_llm": list(template["recommended_actions_for_llm"]),
    }


def make_warning(code: str, *, context: dict | None = None) -> dict:
    """Factory for Warning objects. Returns a plain dict per spec 8.2."""
    severity, message = _WARNING_TEMPLATES.get(code, ("warning", ""))
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "context": context or {},
    }


def _risks_trend_aware(trend_audit) -> tuple[list[dict], list[dict]]:
    """Rule 2: trend-aware projection clamping."""
    if not trend_audit:
        return [], []
    frac = trend_audit.fraction_of_horizon_clamped
    if frac > 0.5:
        return [
            make_risk(
                "trend_aware_projection_clamped",
                fraction_of_horizon_clamped=round(frac, 4),
                linear_extrapolation_endpoint=round(trend_audit.projection_end_mean_raw, 4),
                min_future_mean_floor=round(trend_audit.min_future_mean_floor, 4),
                horizon_hours=trend_audit.horizon_hours,
                stationary_C_star_comparison=round(trend_audit.stationary_C_star_comparison, 4),
            )
        ], []
    if frac > 0.2:
        return [], [
            make_warning(
                "trend_aware_projection_partially_clamped",
                context={
                    "fraction_of_horizon_clamped": round(frac, 4),
                    "projection_end_mean_raw": round(trend_audit.projection_end_mean_raw, 4),
                    "min_future_mean_floor": round(trend_audit.min_future_mean_floor, 4),
                },
            )
        ]
    return [], []


def _risks_d_coverage(d_audit) -> list[dict]:
    """Rule 3: d_blended coverage tiers."""
    cov = d_audit.coverage_pct
    if cov >= 0.98:
        return []
    code = "d_blended_coverage_low" if cov < 0.90 else "d_blended_coverage_medium"
    return [
        make_risk(
            code,
            coverage_pct=round(cov, 4),
            matched_entries=d_audit.matched_entries,
            unmatched_entries=d_audit.unmatched_entries,
            unmatched_top10_by_cost=d_audit.unmatched_top10_by_cost,
        )
    ]


def _risks_sp_utilization(sp_audit) -> tuple[list[dict], list[dict]]:
    """Rule 4: SP utilization tiers + fallback + non-compute SP flags."""
    risks: list[dict] = []
    warnings: list[dict] = []
    util = sp_audit.utilization_30d
    if util < 0.90:
        risks.append(
            make_risk(
                "existing_sp_low_utilization",
                utilization_30d=round(util, 4),
                total_commitment=round(sp_audit.compute_commitment, 4),
                effective_coverage=round(sp_audit.effective, 4),
            )
        )
    elif util < 0.95:
        warnings.append(
            make_warning(
                "existing_sp_mild_low_utilization",
                context={"utilization_30d": round(util, 4)},
            )
        )
    if sp_audit.utilization_source == "fallback_assumed_100pct":
        warnings.append(
            make_warning(
                "ce_api_unavailable_utilization_assumed_100",
                context={"assumed_utilization": 1.0},
            )
        )
    if sp_audit.has_non_compute_sps:
        warnings.append(
            make_warning(
                "non_compute_sps_ignored_in_gap_calculation",
                context={
                    "non_compute_count": sp_audit.non_compute_count,
                    "total_sp_count": sp_audit.count,
                },
            )
        )
    return risks, warnings


def _risks_baseline_sample_size(final: FinalResult) -> list[dict]:
    """Rule 6: baseline sample-size below 95% of expected."""
    stats = final.baseline_stats
    if stats.sample_size_hours >= 0.95 * stats.expected_sample_size_hours:
        return []
    return [
        make_risk(
            "baseline_sample_size_low",
            actual_hours=stats.sample_size_hours,
            expected_hours=stats.expected_sample_size_hours,
            completeness_pct=round(stats.completeness_pct, 4),
        )
    ]


def _risk_for_sp_expiry(sp: SPInfo, chosen_window: Window) -> dict | None:
    """Helper for rule 7: per-SP expiry-near-window check."""
    if sp.type != "Compute":
        return None
    if sp.state == "retired":
        if chosen_window.start - timedelta(days=7) <= sp.end < chosen_window.start:
            return make_risk(
                "recent_sp_expiry_near_window",
                case="pre_window_retired",
                sp_id=sp.id,
                sp_type=sp.type,
                expired_at=sp.end.isoformat(),
                commitment_per_hour=float(sp.commitment),
                days_before_window_start=(chosen_window.start - sp.end).days,
            )
    elif sp.state in ("active", "payment-pending") and (
        chosen_window.end < sp.end <= chosen_window.end + timedelta(days=7)
    ):
        return make_risk(
            "recent_sp_expiry_near_window",
            case="post_window_active_expiring",
            sp_id=sp.id,
            sp_type=sp.type,
            expires_at=sp.end.isoformat(),
            commitment_per_hour=float(sp.commitment),
            days_after_window_end=(sp.end - chosen_window.end).days,
        )
    return None


def _risks_sp_expiry(
    all_relevant_sps: list[SPInfo], chosen_window: Window
) -> list[dict]:
    """Rule 7: expiry events near the baseline window (Compute SPs only)."""
    return [
        r
        for sp in all_relevant_sps
        if (r := _risk_for_sp_expiry(sp, chosen_window)) is not None
    ]


def _risks_untagged_fraction(
    config: OrgConfig | None,
    validation: ValidationResult,
    chosen_window: Window,
    exclude_filter=None,
    available_tag_columns: frozenset[str] | None = None,
) -> list[dict]:
    """Rule 8: untagged cost fraction. Skipped when config or context missing."""
    if config is None or validation.context is None:
        return []
    if exclude_filter is None:
        from scripts.exclude_filter import ExcludeFilter
        exclude_filter = ExcludeFilter()
    if available_tag_columns is None:
        available_tag_columns = frozenset()
    untagged_fraction = compute_untagged_fraction(
        config, validation.context, chosen_window,
        exclude_filter=exclude_filter,
        available_tag_columns=available_tag_columns,
    )
    if untagged_fraction <= 0.10:
        return []
    return [
        make_risk(
            "untagged_cost_fraction_high",
            fraction=round(untagged_fraction, 4),
            threshold=0.10,
        )
    ]


def _warnings_auxiliary(
    final: FinalResult, pricing_cache_meta: PricingCacheMeta
) -> list[dict]:
    """Rules 5 + 9: mild-decline warning + pricing cache age warning."""
    warnings: list[dict] = []
    if final.trend_classification == "mild_decline":
        warnings.append(
            make_warning(
                "mild_decline_ignored_in_formula",
                context={
                    "trend_b3_over_b1_ratio": round(final.trend_b3_over_b1_ratio, 4),
                    "classification": final.trend_classification,
                },
            )
        )
    if pricing_cache_meta.age_hours > 168:
        warnings.append(
            make_warning(
                "pricing_cache_age_hours_high",
                context={
                    "age_hours": round(pricing_cache_meta.age_hours, 2),
                    "threshold_hours": 168,
                    "cached_version": pricing_cache_meta.version,
                },
            )
        )
    return warnings


def _warnings_edp(d_audit) -> list[dict]:
    """Emit EDP warning based on uniform/differential detection."""
    if d_audit.edp_uniform:
        return [
            {
                "severity": "info",
                "code": "edp_uniform_confirmed",
                "message": (
                    f"EDP applies uniformly to SP and OD (e={d_audit.e_sp:.4f}). "
                    "No calibration applied."
                ),
                "context": {},
            }
        ]
    return [
        {
            "severity": "warning",
            "code": "edp_differential_calibrated",
            "message": (
                f"Differential EDP detected: e_sp={d_audit.e_sp:.4f}, e_od={d_audit.e_od:.4f}. "
                f"d was calibrated from {d_audit.d_raw:.4f} to {d_audit.d_calibrated:.4f}. "
                "Verify EDP/PPA contract terms; recommendation remains numerically correct."
            ),
            "context": {},
        }
    ]


def assemble_risks_and_warnings(
    *,
    config: OrgConfig | None,
    validation: ValidationResult,
    window_result: WindowSearchResult,
    final: FinalResult,
    all_relevant_sps: list[SPInfo],
    chosen_window: Window,
    pricing_cache_meta: PricingCacheMeta,
    exclude_filter=None,
    available_tag_columns: frozenset[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Aggregate all Risk and Warning objects. See spec 6.6.

    config may be None in unit tests that bypass the untagged check.
    """
    risks: list[dict] = list(window_result.chosen_window_risks or [])
    warnings: list[dict] = []

    ta_risks, ta_warnings = _risks_trend_aware(final.trend_aware_audit)
    risks.extend(ta_risks)
    warnings.extend(ta_warnings)

    risks.extend(_risks_d_coverage(final.d_audit))

    sp_risks, sp_warnings = _risks_sp_utilization(final.sp_audit)
    risks.extend(sp_risks)
    warnings.extend(sp_warnings)

    risks.extend(_risks_baseline_sample_size(final))
    risks.extend(_risks_sp_expiry(all_relevant_sps, chosen_window))
    risks.extend(_risks_untagged_fraction(
        config, validation, chosen_window,
        exclude_filter=exclude_filter,
        available_tag_columns=available_tag_columns,
    ))

    warnings.extend(_warnings_auxiliary(final, pricing_cache_meta))
    warnings.extend(_warnings_edp(final.d_audit))
    warnings.extend(validation.warnings)

    return risks, warnings


def build_context(
    config: OrgConfig,
    validation: ValidationResult,
    final: FinalResult,
    pricing_cache_meta: PricingCacheMeta,
    discovered_regions: list[dict] | None = None,
    exclude_filter=None,
    available_tag_columns: frozenset[str] | None = None,
    sp_coverage_share: float = 1.0,
    sp_share_audit: dict | None = None,
) -> dict:
    ctx = validation.context
    assert ctx is not None, "build_context called with None context"
    if exclude_filter is None:
        from scripts.exclude_filter import ExcludeFilter
        exclude_filter = ExcludeFilter()
    if available_tag_columns is None:
        available_tag_columns = frozenset()
    if sp_share_audit is None:
        sp_share_audit = {"note": "filter empty; share short-circuited to 1.0"}

    result: dict = {
        "account_id": ctx.caller_account_id,
        "org_id": ctx.org_id,
        "profile": config.profile,
        "payer_account_id": ctx.caller_account_id,
        "org_alias": config.alias,
        "member_accounts_count": ctx.member_accounts_count,
        "primary_region": ctx.primary_region,
        "discount_rate_used": round(final.d_audit.d_calibrated, 4),
        "discount_source": "aws_public_pricing_api_weighted",
        "pricing_cache_version": pricing_cache_meta.version,
        "pricing_cache_age_hours": round(pricing_cache_meta.age_hours, 2),
    }
    if discovered_regions is not None:
        result["discovered_regions"] = discovered_regions

    # Filter reflection — None when no filter applied; else full audit block.
    if exclude_filter.is_empty():
        result["applied_exclude_filter"] = None
    else:
        from scripts.exclude_filter import build_exclude_sql_clauses
        _, filter_warnings = build_exclude_sql_clauses(
            exclude_filter, available_tag_columns,
        )
        result["applied_exclude_filter"] = {
            "usage_type_patterns": list(exclude_filter.usage_type_patterns),
            "account_ids": list(exclude_filter.account_ids),
            "tag_exclusions": [
                {"key": t.key, "values": list(t.values)}
                for t in exclude_filter.tag_exclusions
            ],
            "include_untagged": exclude_filter.include_untagged,
            "sp_coverage_share": sp_coverage_share,
            "sp_coverage_share_audit": sp_share_audit,
            "skipped_filter_warnings": filter_warnings,
        }

    return result


def build_baseline(
    final: FinalResult,
    chosen: ValidatedCandidate,
    requested_window: Window,
) -> dict:
    stats = final.baseline_stats
    return {
        "requested_window": {
            "end": requested_window.end.isoformat(),
            "days": requested_window.days,
        },
        "actual_window": {
            "end": chosen.window.end.isoformat(),
            "days": chosen.window.days,
        },
        "sample_size_hours": stats.sample_size_hours,
        "expected_sample_size_hours": stats.expected_sample_size_hours,
        "completeness_pct": round(stats.completeness_pct, 4),
        "raw_stats": {
            "p0": round(stats.p0, 4),
            "p5": round(stats.p5, 4),
            "p10": round(stats.p10, 4),
            "p17": round(stats.p17, 4),
            "p25": round(stats.p25, 4),
            "p50": round(stats.p50, 4),
            "p75": round(stats.p75, 4),
            "p83": round(stats.p83, 4),
            "p90": round(stats.p90, 4),
            "p95": round(stats.p95, 4),
            "p100": round(stats.p100, 4),
            "mean": round(stats.mean, 4),
            "std": round(stats.std, 4),
            "min": round(stats.min, 4),
            "max": round(stats.max, 4),
        },
        "trend_classification": final.trend_classification,
        "trend_buckets": [
            {
                "period": f"{b.period_start.date()}~{b.period_end.date()}",
                "sample_size": b.sample_size,
                "p17": round(b.p17, 4),
                "mean": round(b.mean, 4),
                "min": round(b.min, 4),
                "max": round(b.max, 4),
            }
            for b in final.trend_buckets
        ],
        "trend_b3_over_b1_ratio": round(final.trend_b3_over_b1_ratio, 4),
        "non_monotonic_details": (
            {
                "pattern": final.non_monotonic_pattern,
                "swing_pct": round(final.non_monotonic_swing_pct, 4),
            }
            if final.non_monotonic_swing_pct is not None
            else None
        ),
        "discount_audit": {
            "d_raw": round(final.d_audit.d_raw, 4),
            "d_calibrated": round(final.d_audit.d_calibrated, 4),
            "e_sp": round(final.d_audit.e_sp, 4),
            "e_od": round(final.d_audit.e_od, 4),
            "edp_uniform": final.d_audit.edp_uniform,
            "calibration_applied": final.d_audit.calibration_applied,
            "e_sp_inferred_from_e_od": final.d_audit.e_sp_inferred_from_e_od,
            "source": final.d_audit.source,
            "pricing_cache_version": final.d_audit.pricing_cache_version,
            "pricing_cache_fetched_at": final.d_audit.pricing_cache_fetched_at.isoformat(),
            "mix_window_days": final.d_audit.mix_window_days,
            "coverage_pct": round(final.d_audit.coverage_pct, 4),
            "matched_entries": final.d_audit.matched_entries,
            "unmatched_entries": final.d_audit.unmatched_entries,
            "unmatched_top10_by_cost": final.d_audit.unmatched_top10_by_cost,
        },
    }


def _build_coverage_scope(org_id: str, exclude_filter) -> str:
    """Human-readable scope string reflecting applied filter. Empty filter ⇒
    byte-for-byte preserves the pre-filter default string."""
    base = (
        "All EC2 BoxUsage (non-Spot) + Fargate + Lambda across all member "
        f"accounts in Org {org_id}"
    )
    if exclude_filter.is_empty():
        return base
    axes = []
    if exclude_filter.usage_type_patterns:
        axes.append(
            "excl. usage_type patterns ["
            + ", ".join(exclude_filter.usage_type_patterns)
            + "]"
        )
    if exclude_filter.account_ids:
        axes.append(
            "excl. accounts [" + ", ".join(exclude_filter.account_ids) + "]"
        )
    if exclude_filter.tag_exclusions:
        axes.append(
            "excl. tags ["
            + ", ".join(
                f"{t.key}={','.join(t.values)}"
                for t in exclude_filter.tag_exclusions
            )
            + "]"
        )
    return (
        "EC2 BoxUsage (non-Spot) + Fargate + Lambda across Org "
        f"{org_id}, filtered: {'; '.join(axes)}"
    )


def build_purchase_instructions(
    config: OrgConfig, final: FinalResult, exclude_filter=None
) -> dict:
    """Construct the PurchaseInstructions dict. INV-10 enforced via round-first.

    See spec 8.4.
    """
    if exclude_filter is None:
        from scripts.exclude_filter import ExcludeFilter
        exclude_filter = ExcludeFilter()
    delta_rounded = round(final.C_new_delta, 4)
    commitment_str = f"{delta_rounded:.4f}"
    today = datetime.now(timezone.utc).date().isoformat()
    client_token = f"sp-optimizer-{today}-{config.payer_account_id}"
    annual_total = delta_rounded * 8760

    return {
        "headline_warning": (
            "⚠️ SP purchase is IRREVERSIBLE. Once executed, you commit to a fixed "
            "hourly fee for 365 days. AWS does NOT allow cancellation, refund, or "
            "grace-period reversal."
        ),
        "summary": {
            "product": "1 year No Upfront Compute Savings Plan",
            "commitment_per_hour_usd": round(final.C_new_delta, 4),
            "annual_total_usd": round(annual_total, 2),
            "profile": config.profile,
            "target_account": config.payer_account_id,
            "target_account_role": "Org management/payer",
            "coverage_scope": _build_coverage_scope(config.org_id, exclude_filter),
        },
        "step_1_lookup_offering_id": {
            "purpose": (
                "Fetch the current Compute SP offering ID. AWS offering IDs are stable "
                "per region/term/payment combination but should be re-fetched at purchase "
                "time to guarantee freshness."
            ),
            "command": (
                f"aws savingsplans describe-savings-plans-offerings "
                f"--profile {config.profile} --plan-types Compute --durations 31536000 "
                f"--currencies USD --payment-options 'No Upfront' "
                f"--query 'searchResults[?planType==`Compute`] | [0].offeringId' --output text"
            ),
            "expected_output": "A single offering ID string (UUID-like, ~36 chars)",
            "failure_modes": [
                "Empty output → offering not available in this region; check AWS service health",
                "Permission denied → profile lacks savingsplans:DescribeSavingsPlansOfferings",
                "Multiple results → query filter needs tightening",
            ],
        },
        "step_2_create_savings_plan": {
            "purpose": (
                "THE IRREVERSIBLE PURCHASE STEP. After this command succeeds, you are "
                f"committed to paying ${commitment_str}/hr × 8760 hours = "
                f"${annual_total:,.2f} regardless of actual usage."
            ),
            "command_template": (
                f"aws savingsplans create-savings-plan "
                f"--profile {config.profile} "
                f"--savings-plan-offering-id <PASTE_OFFERING_ID_FROM_STEP_1> "
                f"--commitment '{commitment_str}' "
                f"--client-token '{client_token}' "
                f"--tags 'ManagedBy=aws-sp-optimizer-skill,"
                f"PurchasedAt={today},"
                f"Methodology=newsvendor-p{int(final.d_audit.d_calibrated * 100)},"
                f"DiscountRate={final.d_audit.d_calibrated:.4f},"
                f"RecommendedBy=aws-sp-optimizer-skill-v2'"
            ),
            "parameters_explained": _build_parameters_explained(
                config, final, commitment_str, client_token
            ),
            "what_aws_does_when_executed": [
                "1. Authenticates the request against the profile's IAM policy",
                "2. Validates the offering ID is currently purchasable",
                "3. Validates the commitment is within account SP quota (default $1000/hr)",
                "4. Creates the SavingsPlan resource (state: payment-pending → active)",
                "5. Begins accruing the hourly fee immediately upon 'active' state",
                "6. Begins applying the SP discount to eligible compute usage immediately",
            ],
            "what_aws_does_NOT_do": [
                "Send an email confirmation — the purchase is silent",
                "Allow cancellation, refund, or adjustment within any grace period",
                "Warn you if the commitment exceeds your historical usage",
                "Prorate the first hour — billing starts from the hour of activation",
                "Check that the commitment matches other SPs you already own",
            ],
            "post_purchase_verification": {
                "immediate_check": {
                    "command": (
                        f"aws savingsplans describe-savings-plans --profile {config.profile} "
                        f"--states active --query 'savingsPlans[?commitment==`{commitment_str}`]'"
                    ),
                    "expected": (
                        "One entry with state='active' (or 'payment-pending' initially), "
                        "start=today, end=today+365d"
                    ),
                },
                "cost_explorer_visible_at": "Within 24 hours in Cost Explorer",
                "cur_visible_at": (
                    "Next CUR refresh cycle (24-48 hours); "
                    "line_item_line_item_type='SavingsPlanRecurringFee'"
                ),
                "utilization_check_at_week_1": (
                    f"aws ce get-savings-plans-utilization --profile {config.profile} "
                    f"--time-period Start=<today+7d>,End=<today+8d> --granularity DAILY"
                ),
            },
        },
        "safety_checklist_before_running": _build_safety_checklist(config, final),
        "alternatives_if_not_confident": _build_alternatives(commitment_str),
    }


def _build_parameters_explained(
    config: OrgConfig, final: FinalResult, commitment_str: str, client_token: str
) -> dict:
    return {
        "--profile": {
            "value": config.profile,
            "why_this_value": (
                f"SP is bought at the Org payer account {config.payer_account_id}; "
                f"coverage propagates to all member accounts via consolidated billing."
            ),
            "verification": f"aws sts get-caller-identity --profile {config.profile}",
        },
        "--savings-plan-offering-id": {
            "value": "<from step 1>",
            "meaning": "AWS internal SKU identifying the SP product",
            "source_of_truth": "describe-savings-plans-offerings",
            "why_fresh": "AWS rotates offering IDs when pricing changes",
        },
        "--commitment": {
            "value": commitment_str,
            "unit": "USD per hour",
            "meaning": "Fixed hourly fee AWS will charge for 365 days",
            "source_of_truth": "Computed by aws-sp-optimizer using newsvendor formula",
            "formula_inputs": {
                "d_calibrated": round(final.d_audit.d_calibrated, 4),
                "C_star_total": round(final.C_star_total, 4),
                "C_existing_effective": round(final.C_existing_effective, 4),
                "C_new_delta": round(final.C_new_delta, 4),
                "formula_branch": final.formula_branch,
            },
        },
        "--client-token": {
            "value": client_token,
            "meaning": "Idempotency key; retry-safe",
            "safety": (
                "Same token → same SP (no double-purchase). "
                "Change the token only for a genuinely new purchase."
            ),
        },
        "--tags": {
            "value": "see command template",
            "purpose": (
                "Resource tags for audit trail. They appear in CUR's "
                "resource_tags_aws_* columns for correlation with this run."
            ),
        },
    }


def _build_safety_checklist(config: OrgConfig, final: FinalResult) -> list:
    return [
        {
            "check": "Profile account match",
            "command": f"aws sts get-caller-identity --profile {config.profile}",
            "expected": f"Account = {config.payer_account_id}",
        },
        {
            "check": "Org membership unchanged",
            "command": f"aws organizations describe-organization --profile {config.profile}",
            "expected": f"Id = {config.org_id}, MasterAccountId = {config.payer_account_id}",
        },
        {
            "check": "No new SP purchased since recommendation",
            "command": (
                f"aws savingsplans describe-savings-plans --profile {config.profile} --states active"
            ),
            "expected": "Same set of active SPs as when recommendation was generated",
        },
        {
            "check": "Recommendation age",
            "condition": "Recommendation was generated at most 7 days ago",
            "if_older": "Re-run aws-sp-optimizer before purchasing",
        },
        {
            "check": "User has budget authority",
            "condition": f"Confirm ability to commit ${round(final.C_new_delta, 4) * 8760:,.2f}/year",  # noqa: E501
        },
        {
            "check": "Service quota allows commitment",
            "note": "Default SP commitment quota is $1000/hr",
        },
    ]


def _build_alternatives(commitment_str: str) -> list:
    half = float(commitment_str) / 2
    return [
        {
            "strategy": "Buy half first, re-evaluate in 30 days",
            "command_delta": f"Change --commitment '{commitment_str}' to '{half:.4f}'",
            "rationale": "Lower risk; can buy the remaining half later",
        },
        {
            "strategy": "Wait 1 week and re-run",
            "rationale": "Gives more data if the trend signal was marginal",
        },
        {
            "strategy": "Purchase via AWS console",
            "url": "https://console.aws.amazon.com/cost-management/home#/savings-plans/purchase",
            "rationale": "Visual confirmation step before commit",
        },
        {
            "strategy": "Cross-check with AWS's own recommendation",
            "command_delta": (
                "aws ce get-savings-plans-purchase-recommendation "
                "--savings-plans-type COMPUTE_SP --term-in-years ONE_YEAR "
                "--payment-option NO_UPFRONT --lookback-period-in-days SIXTY_DAYS"
            ),
            "rationale": "If AWS's own algo gives a similar number (±20%), confidence is high",
        },
    ]


KNOWN_STATUSES = frozenset(
    {
        "ok",
        "ok_with_adjustment",
        "needs_setup",
        "needs_validation_fix",
        "needs_human_decision",
        "needs_cache_refresh",
        "aws_api_error",
        "script_bug",
        "validation_ok",
        "validation_failed",
        "discovery_result",
    }
)


def build_output(
    *,
    config: OrgConfig,
    validation: ValidationResult,
    final: FinalResult,
    search: WindowSearchResult,
    all_relevant_sps: list,
    requested_window: Window,
    pricing_cache_meta: PricingCacheMeta,
    discovered_regions: list[dict] | None = None,
    exclude_filter=None,
    available_tag_columns: frozenset[str] | None = None,
    sp_coverage_share: float = 1.0,
    sp_share_audit: dict | None = None,
) -> dict:
    """Assemble the final OK/OkWithAdjustment output. See spec 6.7."""
    assert search.chosen is not None, "build_output called with failed search"

    if exclude_filter is None:
        from scripts.exclude_filter import ExcludeFilter
        exclude_filter = ExcludeFilter()
    if available_tag_columns is None:
        available_tag_columns = frozenset()

    risks, warnings = assemble_risks_and_warnings(
        config=config,
        validation=validation,
        window_result=search,
        final=final,
        all_relevant_sps=all_relevant_sps,
        chosen_window=search.chosen.window,
        pricing_cache_meta=pricing_cache_meta,
        exclude_filter=exclude_filter,
        available_tag_columns=available_tag_columns,
    )

    # INV-5 gate: Risk dicts from make_risk use dict access, not attribute
    must_review = any(r["must_review_before_acting"] for r in risks)

    adjustments = search.adjustments_made or []
    status = "ok_with_adjustment" if adjustments else "ok"

    # R36-2: compute derived amounts from rounded hourly to satisfy INV-7
    delta_rounded = round(final.C_new_delta, 4)

    output: dict = {
        **base_output(),
        "status": status,
        "context": build_context(
            config,
            validation,
            final,
            pricing_cache_meta,
            discovered_regions,
            exclude_filter=exclude_filter,
            available_tag_columns=available_tag_columns,
            sp_coverage_share=sp_coverage_share,
            sp_share_audit=sp_share_audit,
        ),
        "baseline": build_baseline(final, search.chosen, requested_window),
        "current_sps": final.sp_audit.active_sp_list,
        "recommendation": {
            "C_star_total_per_hour": round(final.C_star_total, 4),
            "C_existing_effective_per_hour": round(final.C_existing_effective, 4),
            "delta_per_hour_to_buy": delta_rounded,
            "annual_delta_usd": round(delta_rounded * 8760, 2),
            "expected_annual_sp_fee_usd": round(
                delta_rounded * 8760 * (1 - final.d_audit.e_sp), 2
            ),
            "expected_annual_savings_usd": round(
                delta_rounded * 8760 * (
                    (1 - final.d_audit.e_od) / (1 - final.d_audit.d_raw)
                    - (1 - final.d_audit.e_sp)
                ),
                2,
            ),
            "coverage_after_purchase_pct": round(
                (final.C_existing_effective + final.C_new_delta) / final.baseline_stats.mean, 4
            )
            if final.baseline_stats.mean > 0
            else 0.0,
            "formula_branch": final.formula_branch,
            "discount_rate_d": round(final.d_audit.d_calibrated, 4),
            "discount_rate_source": "aws_public_pricing_api_weighted",
            "bootstrap_95ci_on_C_star": (
                [round(v, 4) for v in final.bootstrap_95ci]
                if final.bootstrap_95ci is not None
                else None
            ),
            "bootstrap_se": (
                round(final.bootstrap_se, 4) if final.bootstrap_se is not None else None
            ),
            "trend_aware_audit": (
                asdict(final.trend_aware_audit) if final.trend_aware_audit else None
            ),
            "must_review_before_acting": must_review,
        },
        "search_summary": search.search_summary,
        "risks": risks,
        "warnings": warnings,
        "purchase_instructions": (
            None if must_review else build_purchase_instructions(config, final, exclude_filter)
        ),
    }

    if status == "ok_with_adjustment":
        output["adjustments_made"] = adjustments

    return output


def _inv_schema(output: dict) -> str:
    """INV-1/2: schema_version and known status."""
    assert output.get("schema_version") == 1, "INV-1: schema_version != 1"
    status = output.get("status")
    assert status in KNOWN_STATUSES, f"INV-2: unknown status {status!r}"
    return status


def _inv_ok_structure(output: dict, status: str) -> None:
    """INV-3/4: ok* statuses have required top-level keys and adjustments."""
    if status in ("ok", "ok_with_adjustment"):
        for k in ("recommendation", "context", "baseline"):
            assert k in output, f"INV-3: {status} missing {k}"
    if status == "ok_with_adjustment":
        assert output.get("adjustments_made"), (
            "INV-4: ok_with_adjustment has empty adjustments_made"
        )
        assert "search_summary" in output, "INV-4: ok_with_adjustment missing search_summary"


def _inv_recommendation(output: dict) -> None:
    """INV-5/6/7: must_review gate + delta invariants. INV-5 is the HARD GATE."""
    if "recommendation" not in output:
        return
    must_review = output["recommendation"]["must_review_before_acting"]
    assert isinstance(must_review, bool), (
        f"INV-5: must_review_before_acting must be bool, got {type(must_review).__name__}"
    )
    pi = output.get("purchase_instructions")
    if must_review is True:
        assert pi is None, "INV-5: must_review=true but purchase_instructions != null"
    else:
        assert pi is not None, "INV-5: must_review=false but purchase_instructions == null"

    assert output["recommendation"]["delta_per_hour_to_buy"] >= 0, "INV-6"

    rec = output["recommendation"]
    expected = round(rec["delta_per_hour_to_buy"] * 8760, 2)
    assert abs(rec["annual_delta_usd"] - expected) < 0.02, "INV-7"


def _inv_purchase_template(output: dict) -> None:
    """INV-10: command_template commitment formatting matches delta."""
    if not output.get("purchase_instructions"):
        return
    cmd = output["purchase_instructions"]["step_2_create_savings_plan"]["command_template"]
    commit_str = f"{output['recommendation']['delta_per_hour_to_buy']:.4f}"
    assert f"--commitment '{commit_str}'" in cmd, (
        "INV-10: command_template commitment formatting mismatch"
    )


def _inv_error_statuses(output: dict, status: str) -> None:
    """INV-8/9/11/12/13: each non-ok status has its mandatory payload."""
    if status == "needs_setup":
        assert output.get("instruction_for_llm"), "INV-8: needs_setup missing instruction_for_llm"

    if status == "needs_human_decision":
        assert "failure_dossier" in output, (
            "INV-9: needs_human_decision missing failure_dossier"
        )

    if status == "needs_validation_fix":
        assert output.get("blockers"), "INV-11: needs_validation_fix has empty blockers[]"
        assert output.get("instruction_for_llm"), (
            "INV-11: needs_validation_fix missing instruction_for_llm"
        )

    if status in ("validation_ok", "validation_failed"):
        assert output.get("config_alias"), "INV-12: validate-only output missing config_alias"
        assert output.get("instruction_for_llm"), (
            "INV-12: validate-only output missing instruction_for_llm"
        )
        assert "blockers" in output, "INV-12: validate-only output missing blockers field"
        if status == "validation_failed":
            assert output["blockers"], "INV-12: validation_failed must have non-empty blockers"
        else:
            assert not output["blockers"], "INV-12: validation_ok must have empty blockers"

    if status == "discovery_result":
        assert "discovered_orgs" in output, "INV-13: discovery_result missing discovered_orgs"
        assert output.get("instruction_for_llm"), (
            "INV-13: discovery_result missing instruction_for_llm"
        )


def validate_output_invariants(output: dict) -> None:
    """Asserts INV-1 through INV-13. Raises AssertionError on any violation;
    main() catches and converts to script_bug output."""
    status = _inv_schema(output)
    _inv_ok_structure(output, status)
    _inv_recommendation(output)
    _inv_purchase_template(output)
    _inv_error_statuses(output, status)
