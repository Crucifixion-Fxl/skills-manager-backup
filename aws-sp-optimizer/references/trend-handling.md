# Trend Handling

## Classification ladder (spec 5.6)

The skill splits the baseline window into 3 equal buckets and classifies by `ratio = bucket3.p17 / bucket1.p17`:

| Classification | Rule | Formula branch | Extra output |
|---|---|---|---|
| `stable` | `|ratio - 1.0| < 0.02` (symmetric) | stationary | — |
| `increasing` | `ratio >= 1.02` | stationary (conservative under-commit) | — |
| `mild_decline` | `0.95 <= ratio < 0.98` | stationary | `mild_decline_ignored_in_formula` warning |
| `moderate_decline` | `0.85 <= ratio < 0.95` | **trend_aware** | trend_aware_audit |
| `severe_decline` | `ratio < 0.85` | — (window rejected) | — |
| `non_monotonic` | swing > 10% AND buckets not monotonic | stationary | **`non_monotonic_oscillation` risk (HIGH, gates output)** |

## Explaining non_monotonic to the user

If `baseline.trend_classification == "non_monotonic"`:
1. Show `baseline.non_monotonic_details.pattern` (`peak_in_middle` or `valley_in_middle`) and `swing_pct`
2. Show the three bucket p17 values
3. Explain: the newsvendor formula assumes a stable underlying distribution. A non-monotonic pattern (e.g. launch spike followed by normalization) means the "future" isn't well-modeled by the observed window
4. Offer to wait for stabilization or investigate the cause of the swing

## Explaining moderate_decline + trend_aware

When `formula_branch == "trend_aware"`:
1. The skill fit a robust linear trend (HuberT) and extrapolated forward 8760 hours
2. `trend_aware_audit.projection_end_mean_clamped` is the projected mean at +1 year
3. `trend_aware_audit.fraction_of_horizon_clamped` shows how often the projection had to be floor-clamped:
   - > 0.5 → high-severity risk `trend_aware_projection_clamped` (GATES purchase)
   - > 0.2 → warning only
4. Compare `stationary_C_star_comparison` to the trend_aware C*: if trend_aware is much lower, the user is committing less to avoid over-commitment as the workload declines

## Explaining mild_decline

When `formula_branch == "stationary"` but `trend_classification == "mild_decline"`:
1. The decline is small enough (≤5%) that the stationary formula is still appropriate
2. Show `mild_decline_ignored_in_formula` warning so the user knows
3. The result may slightly over-commit (~2-5%) vs a fully trend-aware fit; acceptable trade-off for v1

## Why window_search rejects severe_decline candidates

At b3/b1 < 0.85 the decline is so steep that:
1. Linear extrapolation would project into territory the data hasn't covered
2. The SP commitment horizon (1 year) dwarfs the observed window (60-90 days), amplifying the extrapolation error
3. The user almost certainly has a specific reason (migration, shutdown, re-architecture) — a single hourly percentile isn't the right answer

If every candidate is severe_decline, `status == "needs_human_decision"` and the dossier explains the hard fail. Read `failure-handling.md`.
