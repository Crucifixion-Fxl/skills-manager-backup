"""Newsvendor formula: stationary + trend-aware branches, bootstrap CI.

See spec 5.0, 5.3, 5.4, 5.5. Populated across Phase 4 tasks.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from datetime import timedelta as _td

import numpy as np

from scripts._common import (
    BaselineStats,
    DiscountAudit,
    FinalResult,
    HourlySeries,
    NoDataError,
    Ratios,
    SeasonalityModel,
    SPAudit,
    SPInfo,
    TrendAwareAudit,
    TrendModel,
    ValidatedCandidate,
    ce_get_savings_plans_utilization,
)

logger = logging.getLogger(__name__)


def get_existing_sp_effective(
    profile: str,
    all_relevant_sps: list[SPInfo],
    window_end: datetime,
    sp_coverage_share: float = 1.0,
) -> tuple[float, SPAudit]:
    """Compute effective SP coverage. See spec 5.7.

    When an exclude filter is active, sp_coverage_share < 1.0 attenuates the
    effective coverage: effective = compute_commitment × utilization_pct × share.
    Caller must pass the share computed from compute_sp_coverage_share().
    Defaults to 1.0 (no attenuation), preserving pre-filter behavior.

    ``share`` is the retained-fraction of SP *effective* coverage (already
    utilization-baked-in in the source CUR column
    ``savings_plan_savings_plan_effective_cost``), so composing
    ``commit × util × share`` yields retained actual coverage, not a
    double-discounted quantity.
    """
    active_sps = [sp for sp in all_relevant_sps if sp.state in ("active", "payment-pending")]
    sps = active_sps

    compute_commitment = sum(float(sp.commitment) for sp in sps if sp.type == "Compute")

    try:
        util = ce_get_savings_plans_utilization(
            profile=profile,
            start=(window_end - timedelta(days=30)).date().isoformat(),
            end=window_end.date().isoformat(),
            granularity="DAILY",
        )
        utilization_pct = float(util["Total"]["Utilization"]["UtilizationPercentage"]) / 100
        utilization_source = "ce_api"
    except Exception:  # noqa: BLE001
        utilization_pct = 1.0
        utilization_source = "fallback_assumed_100pct"

    effective = compute_commitment * utilization_pct * sp_coverage_share

    # Per-SP effective_coverage_per_hour is NOT attenuated by sp_coverage_share —
    # share is a workload-mix property, not per-SP. Summing these entries can
    # exceed audit.effective when a filter is active; that's expected.
    active_sp_list = [
        {
            "savings_plan_id": sp.id,
            "type": sp.type,
            "ec2_instance_family": sp.ec2_instance_family,
            "region": sp.region,
            "commitment_per_hour": float(sp.commitment),
            "start": sp.start.isoformat(),
            "end": sp.end.isoformat(),
            "payment_option": sp.payment_option,
            "state": sp.state,
            "utilization_30d_pct": utilization_pct if sp.type == "Compute" else None,
            "utilization_measured_at": window_end.isoformat(),
            "effective_coverage_per_hour": (
                float(sp.commitment) * utilization_pct if sp.type == "Compute" else 0.0
            ),
            "counted_toward_gap": sp.type == "Compute",
        }
        for sp in sps
    ]

    return effective, SPAudit(
        count=len(sps),
        compute_count=sum(1 for sp in sps if sp.type == "Compute"),
        non_compute_count=sum(1 for sp in sps if sp.type != "Compute"),
        total_commitment_all=sum(float(sp.commitment) for sp in sps),
        compute_commitment=compute_commitment,
        utilization_30d=utilization_pct,
        utilization_source=utilization_source,
        effective=effective,
        has_non_compute_sps=any(sp.type != "Compute" for sp in sps),
        active_sp_list=active_sp_list,
        sp_coverage_share=sp_coverage_share,
    )


def newsvendor_stationary(X: np.ndarray, d: float) -> float:  # NOSONAR: X = random variable per formula
    """Stationary newsvendor quantile: C* = quantile_d(X). See spec 5.3.

    Uses np.quantile's default 'linear' interpolation. For sample sizes
    N >= 1296 (enforced below) the empirical CDF at the returned C is
    within 1/N of d — below all downstream rounding precisions.
    The 1296 floor = 0.90 * 60 days * 24 hours, matching window_search's
    hard completeness floor on the smallest allowed window.
    """
    if len(X) < 1296:
        raise NoDataError(
            code="insufficient_hourly_samples",
            message=f"Need ≥1296 hourly samples, got {len(X)}",
        )
    if not 0 < d < 1:
        raise ValueError(f"Discount rate d must be in (0,1), got {d}")
    return float(np.quantile(X, d))


def bootstrap_quantile_ci(
    X: np.ndarray,  # NOSONAR: X = random variable per formula
    d: float,
    n_boot: int = 1000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Bootstrap 95% CI on the d-quantile of X.

    Returns (standard_error, ci_lower_2.5pct, ci_upper_97.5pct).
    Constant X → SE=0 and CI=[v, v]; callers must use `is not None`
    checks (NOT truthy) when deciding whether to emit the CI.
    """
    rng = np.random.default_rng(seed)
    boots = np.array(
        [np.quantile(rng.choice(X, size=len(X), replace=True), d) for _ in range(n_boot)]
    )
    se = float(boots.std(ddof=1)) if len(boots) > 1 else 0.0
    return se, float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))


def _apply_edp_calibration(d_raw: float, e_sp: float, e_od: float) -> float:
    """d_calibrated = 1 - (1 - d_raw) * (1 - e_sp) / (1 - e_od)."""
    if e_od >= 0.999:
        raise ValueError(f"e_od={e_od} too close to 1.0; cannot calibrate")
    return 1.0 - (1.0 - d_raw) * (1.0 - e_sp) / (1.0 - e_od)


def compute_d_blended(
    series: HourlySeries,
    ratios: Ratios,
    e_sp: float,
    e_od: float,
    d_mix_window_days: int = 30,
) -> tuple[float, DiscountAudit]:
    """Compute workload-weighted d_raw, then apply EDP calibration.

    Returns (d_calibrated, audit). audit.d_raw preserves the pre-calibration value.
    See spec 5.2. Raises NoDataError on zero matches or <10 matches.
    """
    recent_cutoff = series.window_end - _td(days=d_mix_window_days)
    total_gross_od = 0.0
    total_sp_equiv = 0.0
    unmatched: list[dict] = []
    matched_count = 0

    for h in series.hours:
        if h < recent_cutoff:
            continue
        for mix_key, cost in series.data[h]["mix"].items():
            product_code, region, itype, operation, usage_type = mix_key
            ratio_entry = ratios.lookup(product_code, region, itype, operation, usage_type)
            if ratio_entry is None:
                unmatched.append(
                    {
                        "key": f"{product_code}/{region}/{itype or '-'}/{operation}/{usage_type}",
                        "cost": cost,
                    }
                )
                continue
            total_gross_od += cost
            total_sp_equiv += cost * ratio_entry.ratio
            matched_count += 1

    if total_gross_od == 0:
        raise NoDataError(
            code="no_matched_rows_for_d",
            message="No matched rows for d calculation",
        )

    MIN_MATCHED_FOR_RELIABLE_D = 10
    if matched_count < MIN_MATCHED_FOR_RELIABLE_D:
        raise NoDataError(
            code="insufficient_matched_entries_for_d",
            message=(
                f"Only {matched_count} matched entries for d calculation; "
                f"need ≥{MIN_MATCHED_FOR_RELIABLE_D}."
            ),
            context={
                "matched_count": matched_count,
                "minimum_required": MIN_MATCHED_FOR_RELIABLE_D,
            },
            llm_next_action="READ references/troubleshooting.md section 'insufficient_d_coverage'",
            user_fix_options=[
                "Run with --refresh-cache to rebuild the pricing cache",
                "Verify cached regions match the regions your CUR data spans",
            ],
        )

    d_raw = 1 - total_sp_equiv / total_gross_od
    d_calibrated = _apply_edp_calibration(d_raw, e_sp, e_od)
    unmatched_cost = sum(u["cost"] for u in unmatched)
    coverage_pct = total_gross_od / (total_gross_od + unmatched_cost)
    edp_uniform = abs(e_sp - e_od) <= 0.005

    # Store unrounded floats on the audit dataclass: downstream math reads
    # these values (e.g. compute_optimal_commit uses d_audit.d_raw to build
    # sp_fee_factor, output_builder computes expected_annual_savings_usd
    # from e_sp / e_od / d_raw). JSON serialisation in output_builder already
    # rounds to 4 decimals at display time, so the external contract is
    # unchanged. Rounding here loses up to ~5e-5 of precision in the formula
    # for a purely cosmetic gain — a trade the audit pipeline doesn't need.
    return d_calibrated, DiscountAudit(
        d_raw=d_raw,
        d_calibrated=d_calibrated,
        e_sp=e_sp,
        e_od=e_od,
        edp_uniform=edp_uniform,
        calibration_applied=not edp_uniform,
        source="aws_public_pricing_api_weighted",
        pricing_cache_version=ratios.version,
        pricing_cache_fetched_at=ratios.cached_at,
        mix_window_days=d_mix_window_days,
        coverage_pct=coverage_pct,
        matched_entries=matched_count,
        unmatched_entries=len(unmatched),
        unmatched_top10_by_cost=sorted(unmatched, key=lambda u: -u["cost"])[:10],
    )


def fit_robust_trend(X: np.ndarray, timestamps: np.ndarray) -> TrendModel:  # NOSONAR: X = random variable per formula
    """Fit a robust linear trend using statsmodels RLM with HuberT norm.

    Subtracts timestamps[0] before fitting to avoid numerical conditioning
    issues at ~1.7e9 POSIX seconds.
    """
    import statsmodels.api as sm
    from statsmodels.robust.norms import HuberT
    from statsmodels.robust.robust_linear_model import RLM

    t0 = timestamps[0]
    t_norm = timestamps - t0
    exog = sm.add_constant(t_norm)
    model = RLM(X, exog, M=HuberT())
    result = model.fit()

    slope = float(result.params[1])
    intercept = float(result.params[0]) + slope * (-t0)  # undo normalization
    # R² for RLM: use 1 - SS_res / SS_tot
    fitted = result.fittedvalues
    ss_res = float(np.sum((X - fitted) ** 2))
    ss_tot = float(np.sum((X - X.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0

    class _FittedPredict:
        """Adapter so TrendModel.predict(ts) accepts POSIX timestamps."""

        def __init__(self, inner_result, t0: float):
            self.inner = inner_result
            self.t0 = t0

        def predict(self, ts):
            ts_arr = np.asarray(ts, dtype=float)
            exog_new = sm.add_constant(ts_arr - self.t0, has_constant="add")
            return self.inner.predict(exog_new)

    return TrendModel(
        slope=slope,
        intercept=intercept,
        r2=r2,
        _statsmodels_result=_FittedPredict(result, t0),
    )


def fit_seasonality(residual: np.ndarray, timestamps: np.ndarray) -> SeasonalityModel:
    """Fit 168 hour-of-week dummies (7 days × 24 hours) via OLS on detrended residuals.

    Do NOT add a separate 24 hour-of-day set — it's linearly dependent on the
    168-dummy basis and produces a rank-deficient design matrix. See spec 5.4.
    """
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    import statsmodels.api as sm

    hour_of_week = np.array(
        [
            _dt.fromtimestamp(float(ts), tz=_tz.utc).weekday() * 24
            + _dt.fromtimestamp(float(ts), tz=_tz.utc).hour
            for ts in timestamps
        ],
        dtype=int,
    )

    # Build design matrix: one column per hour-of-week, drop first to avoid collinearity
    # when intercept is included.
    dummies = np.zeros((len(residual), 168 - 1), dtype=float)
    for i, how in enumerate(hour_of_week):
        if how > 0:  # skip column 0 (reference)
            dummies[i, how - 1] = 1.0
    exog = sm.add_constant(dummies, has_constant="add")

    ss_tot = float(np.sum((residual - residual.mean()) ** 2))
    if ss_tot == 0:
        return SeasonalityModel(r2=0.0, _statsmodels_result=None)

    try:
        result = sm.OLS(residual, exog).fit()
    except Exception:  # noqa: BLE001 — degenerate residual
        return SeasonalityModel(r2=0.0, _statsmodels_result=None)

    fitted = result.fittedvalues
    ss_res = float(np.sum((residual - fitted) ** 2))
    r2 = 1 - ss_res / ss_tot

    class _SeasonalPredict:
        def __init__(self, inner, ref_ts):
            self.inner = inner
            self.ref_hour_of_week = None  # computed per predict call

        def predict(self, ts):
            from datetime import datetime as _dt2
            from datetime import timezone as _tz2

            ts_arr = np.asarray(ts, dtype=float)
            how = np.array(
                [
                    _dt2.fromtimestamp(float(t), tz=_tz2.utc).weekday() * 24
                    + _dt2.fromtimestamp(float(t), tz=_tz2.utc).hour
                    for t in ts_arr
                ],
                dtype=int,
            )
            dummies_new = np.zeros((len(ts_arr), 168 - 1), dtype=float)
            for i, h in enumerate(how):
                if h > 0:
                    dummies_new[i, h - 1] = 1.0
            exog_new = sm.add_constant(dummies_new, has_constant="add")
            return self.inner.predict(exog_new)

    return SeasonalityModel(r2=r2, _statsmodels_result=_SeasonalPredict(result, timestamps[0]))


def newsvendor_trend_aware(
    X: np.ndarray,  # NOSONAR: X = random variable per formula
    timestamps: np.ndarray,
    d: float,
    horizon_hours: int = 8760,
    min_future_mean_frac: float = 0.5,
) -> tuple[float, TrendAwareAudit]:
    """Trend-aware newsvendor using bisection on avg_F(C) = d. See spec 5.4."""
    trend = fit_robust_trend(X, timestamps)
    trend_fitted = trend.predict(timestamps)
    season = fit_seasonality(X - trend_fitted, timestamps)
    if season._statsmodels_result is not None:
        seasonal_fitted = season.predict(timestamps)
    else:
        seasonal_fitted = np.zeros_like(X)
    residual = X - trend_fitted - seasonal_fitted
    residual_sorted = np.sort(residual)

    def F_residual(c: float) -> float:  # NOSONAR: F = CDF notation per formula-derivation §5.4
        return float(np.searchsorted(residual_sorted, c, side="right")) / len(residual_sorted)

    now_ts = timestamps[-1] + 3600
    future_ts = now_ts + np.arange(horizon_hours) * 3600
    now_mean = float(trend.predict(np.array([now_ts]))[0])
    trend_future = trend.predict(future_ts)
    if season._statsmodels_result is not None:
        season_future = season.predict(future_ts)
    else:
        season_future = np.zeros(horizon_hours)
    future_mean_raw = trend_future + season_future
    floor = max(now_mean * min_future_mean_frac, 0.01)
    future_mean = np.maximum(future_mean_raw, floor)

    def avg_F(C: float) -> float:  # NOSONAR: F/C = CDF/commitment notation per formula-derivation §5.4
        return float(np.mean([F_residual(C - fm) for fm in future_mean]))

    lo = float(X.min())
    hi = max(float(X.max()) * 2, float(X.max()) + float(residual.std(ddof=1) * 10))
    for _ in range(50):
        mid = (lo + hi) / 2
        if avg_F(mid) < d:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-4:
            break

    if hi - lo >= 1e-4 and abs((lo + hi) / 2 - hi) < 1e-3:
        logger.warning(
            "newsvendor_trend_aware bisection may not have converged; "
            "C_star=%.4f at upper bound %.4f",
            (lo + hi) / 2,
            hi,
        )

    C_star = (lo + hi) / 2  # NOSONAR: C* = optimal commitment per newsvendor formula
    fraction_clamped = float(np.sum(future_mean_raw < floor) / horizon_hours)

    stat_C = newsvendor_stationary(X, d)  # NOSONAR: C = commitment per newsvendor formula
    if stat_C > 0:
        delta_pct = (C_star - stat_C) / stat_C * 100
    else:
        delta_pct = 0.0

    return C_star, TrendAwareAudit(
        slope_per_hour=trend.slope * 3600,  # trend.slope is per second
        trend_r2=trend.r2,
        seasonality_r2=season.r2,
        residual_std=float(residual.std(ddof=1)) if len(residual) > 1 else 0.0,
        projection_now_mean=now_mean,
        projection_end_mean_raw=float(future_mean_raw[-1]),
        projection_end_mean_clamped=float(future_mean[-1]),
        min_future_mean_floor=floor,
        fraction_of_horizon_clamped=fraction_clamped,
        horizon_hours=horizon_hours,
        stationary_C_star_comparison=stat_C,
        delta_pct_vs_stationary=delta_pct,
    )


def choose_formula_branch(trend_classification: str) -> str:
    """Select formula branch based on trend classification. See spec 5.5."""
    match trend_classification:
        case "stable":
            return "stationary"
        case "increasing":
            # Conservative choice — stationary under-commits when trend is rising.
            return "stationary"
        case "mild_decline":
            # Stationary plus a warning raised in the risks layer.
            return "stationary"
        case "moderate_decline":
            return "trend_aware"
        case "severe_decline":
            raise AssertionError("severe_decline should be rejected by window_search")
        case "non_monotonic":
            # Stationary plus a high-severity risk raised in the risks layer.
            return "stationary"
        case _:
            raise ValueError(f"Unknown trend classification: {trend_classification!r}")


def compute_optimal_commit(
    profile: str,
    series: HourlySeries,
    ratios: Ratios,
    all_relevant_sps: list[SPInfo],
    chosen: ValidatedCandidate,
    e_sp: float,
    e_od: float,
    sp_coverage_share: float = 1.0,
) -> FinalResult:
    """Orchestrator for a single chosen candidate. See spec 5.8."""
    d_blended, d_audit = compute_d_blended(series, ratios, e_sp=e_sp, e_od=e_od, d_mix_window_days=30)

    # X is built in AWS `--commitment` space (list SP fee $/hr):
    #   X[h] = list_OD_demand[h] × (1 − d_raw)
    # so that quantile_{d_calibrated}(X) is directly the optimal `--commitment`
    # value — no post-hoc unit conversion needed at output time. See
    # references/formula-derivation.md for the derivation.
    sp_fee_factor = 1.0 - d_audit.d_raw
    hours_in_window = [h for h in chosen.window.hours if h in series.data]
    X = np.array(
        [series.data[h]["total_list_usd"] * sp_fee_factor for h in hours_in_window]
    )
    timestamps = np.array([h.timestamp() for h in hours_in_window], dtype=float)

    if len(X) == 0:
        raise NoDataError(
            code="empty_x_after_window_search",
            message=(
                f"compute_optimal_commit got empty X for window {chosen.window}; "
                f"this should be impossible if window_search ran correctly"
            ),
            context={
                "window_end": chosen.window.end.isoformat(),
                "window_days": chosen.window.days,
            },
        )

    buckets = chosen.trend_buckets
    trend_class = chosen.trend_classification
    branch = choose_formula_branch(trend_class)

    se: float | None
    ci_lo: float | None
    ci_hi: float | None
    if branch == "stationary":
        C_star = newsvendor_stationary(X, d_blended)  # NOSONAR: C* = optimal commitment per formula
        se, ci_lo, ci_hi = bootstrap_quantile_ci(X, d_blended)
        trend_audit = None
    else:
        C_star, trend_audit = newsvendor_trend_aware(X, timestamps, d_blended)  # NOSONAR
        se = None
        ci_lo = None
        ci_hi = None

    C_existing_eff, sp_audit = get_existing_sp_effective(  # NOSONAR: C = commitment per formula
        profile=profile,
        all_relevant_sps=all_relevant_sps,
        window_end=chosen.window.end,
        sp_coverage_share=sp_coverage_share,
    )
    C_new_star = max(0.0, C_star - C_existing_eff)  # NOSONAR: C_new = delta per formula

    baseline_stats = BaselineStats(
        sample_size_hours=len(X),
        expected_sample_size_hours=chosen.window.num_hours,
        completeness_pct=chosen.completeness_pct,
        p0=float(np.quantile(X, 0.00)),
        p5=float(np.quantile(X, 0.05)),
        p10=float(np.quantile(X, 0.10)),
        p17=float(np.quantile(X, 0.17)),
        p25=float(np.quantile(X, 0.25)),
        p50=float(np.quantile(X, 0.50)),
        p75=float(np.quantile(X, 0.75)),
        p83=float(np.quantile(X, 0.83)),
        p90=float(np.quantile(X, 0.90)),
        p95=float(np.quantile(X, 0.95)),
        p100=float(np.quantile(X, 1.00)),
        mean=float(np.mean(X)),
        std=float(np.std(X, ddof=1)) if len(X) > 1 else 0.0,
        min=float(X.min()),
        max=float(X.max()),
    )

    if chosen.trend_classification == "non_monotonic" and len(buckets) == 3:
        b1, b2, b3 = [b.p17 for b in buckets]
        nm_pattern = "peak_in_middle" if b2 > b1 and b2 > b3 else "valley_in_middle"
    else:
        nm_pattern = None

    return FinalResult(
        C_star_total=C_star,
        C_existing_effective=C_existing_eff,
        C_new_delta=C_new_star,
        d_blended=d_blended,
        formula_branch=branch,
        trend_classification=trend_class,
        trend_buckets=list(buckets),
        trend_b3_over_b1_ratio=chosen.trend_b3_over_b1_ratio,
        non_monotonic_swing_pct=chosen.non_monotonic_swing_pct,
        non_monotonic_pattern=nm_pattern,
        baseline_stats=baseline_stats,
        bootstrap_se=se,
        bootstrap_95ci=[ci_lo, ci_hi] if ci_lo is not None and ci_hi is not None else None,
        d_audit=d_audit,
        sp_audit=sp_audit,
        trend_aware_audit=trend_audit,
    )
