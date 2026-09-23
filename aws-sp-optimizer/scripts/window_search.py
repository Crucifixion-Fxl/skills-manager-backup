"""75-candidate window search, trend classification, rejection tiers.

See spec 5.6, 6.3.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from scripts._common import TrendBucket, Window


def compute_trend_buckets(X: np.ndarray, window: Window) -> list[TrendBucket]:  # NOSONAR: X = random variable per formula
    """Split window into 3 equal-length buckets and return TrendBucket objects."""
    n = len(X)
    boundaries = [0, n // 3, 2 * n // 3, n]
    buckets: list[TrendBucket] = []
    bucket_seconds = (window.end - window.start).total_seconds() / 3
    for i in range(3):
        sub = X[boundaries[i] : boundaries[i + 1]]
        if len(sub) == 0:
            buckets.append(
                TrendBucket(
                    period_start=window.start + timedelta(seconds=bucket_seconds * i),
                    period_end=window.start + timedelta(seconds=bucket_seconds * (i + 1)),
                    sample_size=0,
                    p17=0.0,
                    mean=0.0,
                    min=0.0,
                    max=0.0,
                )
            )
            continue
        buckets.append(
            TrendBucket(
                period_start=window.start + timedelta(seconds=bucket_seconds * i),
                period_end=window.start + timedelta(seconds=bucket_seconds * (i + 1)),
                sample_size=len(sub),
                p17=float(np.quantile(sub, 0.17)),
                mean=float(np.mean(sub)),
                min=float(sub.min()),
                max=float(sub.max()),
            )
        )
    return buckets


def classify_trend(
    buckets: list[TrendBucket],
    mild: float = 1.0,
    moderate: float = 0.95,
    severe: float = 0.85,
) -> tuple[str, float, float | None]:
    """Classify trend from 3 buckets. See spec 5.6.

    Thresholds (b3.p17 / b1.p17):
      |ratio - 1.0| < 0.02  -> stable (symmetric around 1.0)
      ratio >= 1.02         -> increasing
      0.95 <= ratio < 0.98  -> mild_decline
      0.85 <= ratio < 0.95  -> moderate_decline
      ratio < 0.85          -> severe_decline
    """
    b1, b2, b3 = buckets[0].p17, buckets[1].p17, buckets[2].p17
    if b1 <= 0:
        return "severe_decline", 0.0, None
    ratio = b3 / b1

    # Non-monotonic detection
    is_monotonic_up = b1 <= b2 <= b3
    is_monotonic_down = b1 >= b2 >= b3
    is_monotonic = is_monotonic_up or is_monotonic_down
    swing_pct = None
    if not is_monotonic:
        p17_values = [b1, b2, b3]
        swing_pct = (max(p17_values) - min(p17_values)) / (sum(p17_values) / 3)
        if swing_pct > 0.10:
            return "non_monotonic", ratio, swing_pct

    # Symmetric stable check — R22-1 fix
    if abs(ratio - 1.0) < 0.02:
        return "stable", ratio, None
    if ratio >= mild:
        return "increasing", ratio, None
    if ratio >= moderate:
        return "mild_decline", ratio, None
    if ratio >= severe:
        return "moderate_decline", ratio, None
    return "severe_decline", ratio, None


def compute_deviation_score(cand: Window, requested: Window, prefer: str) -> int:
    """Score how far a candidate window is from the user-requested window.

    Lower is better. `prefer` weights the trade-off between freshness loss
    and duration loss.
    """
    freshness_loss = (requested.end - cand.end).days
    duration_loss = max(0, requested.days - cand.days)
    if prefer == "freshness":
        return freshness_loss * 3 + duration_loss
    if prefer == "sample-size":
        return freshness_loss + duration_loss * 3
    return freshness_loss + duration_loss  # balanced


from scripts._common import (  # noqa: E402
    HourlySeries,
    RejectedCandidate,
    SPInfo,
    ValidatedCandidate,
    WindowSearchResult,
    find_sp_transitions_in,
)


def _reject_sp_transition(cand: Window, transitions) -> RejectedCandidate:
    return RejectedCandidate(
        window=cand,
        reason="sp_transition_in_window",
        transition_events=[
            {"sp_id": t.sp_id, "event": t.event_type, "date": t.date.isoformat()}
            for t in transitions
        ],
    )


def _validate_candidate(
    cand: Window,
    series: HourlySeries,
    all_relevant_sps: list[SPInfo],
    requested: Window,
    prefer: str,
) -> tuple[str, ValidatedCandidate | RejectedCandidate]:
    """Apply the three checks to a single candidate window.

    Returns (bucket_key, result) where bucket_key is one of
    'passed' | 'sp_transition' | 'severe_data' | 'severe_trend'.
    """
    transitions = find_sp_transitions_in(all_relevant_sps, cand)
    if transitions:
        return "sp_transition", _reject_sp_transition(cand, transitions)

    # An hour counts as "observed" only if it has actual usage — the CUR pull
    # pre-seeds the data dict for every hour in the window, so membership alone
    # is always true and would make this gate unreachable.
    expected_hours = cand.num_hours
    actual_hours = sum(
        1 for h in cand.hours
        if h in series.data and (
            series.data[h].get("total_list_usd", 0.0) > 0 or series.data[h].get("mix")
        )
    )
    completeness = actual_hours / expected_hours
    if completeness < 0.90:
        return "severe_data", RejectedCandidate(
            window=cand,
            reason="incomplete_data",
            completeness_pct=round(completeness, 4),
        )

    X_slice = np.array(  # NOSONAR: X = random variable per formula
        [series.data[h]["total_net_usd"] for h in cand.hours if h in series.data]
    )
    trend_buckets = compute_trend_buckets(X_slice, cand)
    trend_class, b3_b1_ratio, non_monotonic_swing = classify_trend(trend_buckets)

    if trend_class == "severe_decline":
        return "severe_trend", RejectedCandidate(
            window=cand,
            reason="severe_decline",
            b3_b1_ratio=round(b3_b1_ratio, 4),
            buckets=[b.p17 for b in trend_buckets],
        )

    return "passed", ValidatedCandidate(
        window=cand,
        trend_classification=trend_class,
        trend_buckets=trend_buckets,
        trend_b3_over_b1_ratio=round(b3_b1_ratio, 4),
        non_monotonic_swing_pct=(
            round(non_monotonic_swing, 4) if non_monotonic_swing is not None else None
        ),
        sample_size_hours=actual_hours,
        completeness_pct=round(completeness, 4),
        deviation_score=compute_deviation_score(cand, requested, prefer),
    )


def search_best_window(
    series: HourlySeries,
    requested: Window,
    all_relevant_sps: list[SPInfo],
    prefer: str = "balanced",
) -> WindowSearchResult:
    """75-candidate window search. See spec 6.3."""
    candidates = [
        Window(end=requested.end - timedelta(days=i), days=d)
        for i in range(15)
        for d in [60, 67, 74, 81, 90]
    ]

    buckets: dict[str, list] = {
        "passed": [],
        "sp_transition": [],
        "severe_data": [],
        "severe_trend": [],
    }
    for cand in candidates:
        bucket_key, result = _validate_candidate(cand, series, all_relevant_sps, requested, prefer)
        buckets[bucket_key].append(result)

    passed: list[ValidatedCandidate] = buckets["passed"]
    rejected_sp_transition: list[RejectedCandidate] = buckets["sp_transition"]
    rejected_severe_data: list[RejectedCandidate] = buckets["severe_data"]
    rejected_severe_trend: list[RejectedCandidate] = buckets["severe_trend"]

    summary = _build_search_summary(
        total_candidates=len(candidates),
        passed=passed,
        rejected_severe_trend=rejected_severe_trend,
        rejected_severe_data=rejected_severe_data,
        rejected_sp_transition=rejected_sp_transition,
    )

    if not passed:
        return WindowSearchResult(
            status="failed",
            failure_reason="no_viable_window",
            rejection_summary=summary["rejected_by_category"],
            full_rejection_log=[
                rejected_severe_trend,
                rejected_severe_data,
                rejected_sp_transition,
            ],
            search_summary=summary,
        )

    passed.sort(key=lambda c: c.deviation_score)
    chosen = passed[0]

    chosen_window_risks: list = []
    if chosen.trend_classification == "non_monotonic":
        b1, b2, b3 = [b.p17 for b in chosen.trend_buckets]
        pattern = "peak_in_middle" if b2 > b1 and b2 > b3 else "valley_in_middle"
        chosen_window_risks.append(
            {
                "severity": "high",
                "code": "non_monotonic_oscillation",
                "must_review_before_acting": True,
                "summary": "Baseline oscillates — recommendation may be unreliable",
                "details": {
                    "pattern": pattern,
                    "swing_pct": chosen.non_monotonic_swing_pct,
                    "buckets_p17": [b1, b2, b3],
                    "bucket_periods": [
                        {"start": b.period_start.isoformat(), "end": b.period_end.isoformat()}
                        for b in chosen.trend_buckets
                    ],
                },
                "interpretation": "Non-monotonic workload; trend-aware branch cannot fit a line",
                "user_facing_explanation": "工作负载出现波动（非单调），formula 的假设不满足",
                "recommended_actions_for_llm": [
                    "Ask the user to investigate the cause of the swing before purchasing",
                ],
            }
        )

    adjustments_made: list = []
    if chosen.window.end != requested.end or chosen.window.days != requested.days:
        requested_rejections = [
            r
            for r in (rejected_sp_transition + rejected_severe_data + rejected_severe_trend)
            if r.window.end == requested.end and r.window.days == requested.days
        ]
        if requested_rejections:
            primary_reason = requested_rejections[0].reason
            adjustments_made.append(
                {
                    "reason": f"{primary_reason}: requested window failed validation",
                    "action": (
                        f"Selected candidate (end={chosen.window.end.date()}, "
                        f"days={chosen.window.days}) with deviation_score={chosen.deviation_score}"
                    ),
                    "trade_off": {
                        "freshness_loss_days": (requested.end - chosen.window.end).days,
                        "duration_loss_days": max(0, requested.days - chosen.window.days),
                        "deviation_score": chosen.deviation_score,
                    },
                    "requested_window": {"end": requested.end.isoformat(), "days": requested.days},
                    "actual_window": {
                        "end": chosen.window.end.isoformat(),
                        "days": chosen.window.days,
                    },
                }
            )

    return WindowSearchResult(
        status="success",
        chosen=chosen,
        top_5=passed[:5],
        chosen_window_risks=chosen_window_risks,
        adjustments_made=adjustments_made,
        search_summary=summary,
    )


def _build_search_summary(
    total_candidates: int,
    passed: list[ValidatedCandidate],
    rejected_severe_trend: list[RejectedCandidate],
    rejected_severe_data: list[RejectedCandidate],
    rejected_sp_transition: list[RejectedCandidate],
) -> dict:
    """Minimal summary for Task 4.12; Task 4.13 fleshes it out with rejection details."""
    rejected_by_category: dict = {}
    if rejected_severe_trend:
        rejected_by_category["severe_decline"] = {
            "count": len(rejected_severe_trend),
            "samples_omitted_from_output": True,
        }
    if rejected_severe_data:
        rejected_by_category["incomplete_data"] = {
            "count": len(rejected_severe_data),
            "samples_omitted_from_output": True,
        }
    if rejected_sp_transition:
        rejected_by_category["sp_transition_in_window"] = {
            "count": len(rejected_sp_transition),
        }

    passed_sorted = sorted(passed, key=lambda c: c.deviation_score)
    return {
        "total_candidates": total_candidates,
        "passed": len(passed_sorted),
        "rejected_by_category": rejected_by_category,
        "top_5_alternatives": [
            {
                "rank": i + 1,
                "window": {"end": c.window.end.isoformat(), "days": c.window.days},
                "deviation_score": c.deviation_score,
                "trend_classification": c.trend_classification,
                "completeness_pct": round(c.completeness_pct, 4),
                "sample_size_hours": c.sample_size_hours,
            }
            for i, c in enumerate(passed_sorted[:5])
        ],
    }
