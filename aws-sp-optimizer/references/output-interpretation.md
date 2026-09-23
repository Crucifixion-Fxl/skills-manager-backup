# Output Interpretation

## Units note

Commitment-space fields — `delta_per_hour_to_buy`, `C_star_total_per_hour`, `C_existing_effective_per_hour`, `annual_delta_usd`, `baseline_stats.p*`, `baseline_stats.mean/std/min/max` — are in **AWS `--commitment` units: USD/hr (or annualized) at list SP fee rate, pre-EDP**. `delta_per_hour_to_buy` in particular can be passed through verbatim to `aws savingsplans create-savings-plan --commitment`; see `references/purchase-execution.md` for the authoritative purchase-side contract.

Derived fields use distinct units, explicitly labeled:
- `expected_annual_sp_fee_usd` = `delta_per_hour_to_buy × 8760 × (1 − e_sp)` — **net** (post-EDP) SP fee you actually pay
- `expected_annual_savings_usd` = `delta_per_hour_to_buy × 8760 × [(1 − e_od)/(1 − d_raw) − (1 − e_sp)]` — **net** savings vs. the no-SP counterfactual for the same demand volume

To cross-check against an AWS Cost Explorer SP recommendation, compare `delta_per_hour_to_buy` directly — both are list SP fee $/hr, no conversion needed.

## Top-level schema

Every run emits exactly one JSON object with:
- `schema_version: 1`
- `generated_at` (ISO 8601 UTC with `+00:00` suffix — NOT `Z`)
- `script_version` (e.g. `0.1.0`)
- `elapsed_seconds`
- `status` — one of the values below

## Status routing

| status | Meaning | What to show the user |
|---|---|---|
| `ok` | Clean recommendation | `recommendation` + `purchase_instructions` |
| `ok_with_adjustment` | Window was auto-adjusted | Show `adjustments_made` FIRST, then `recommendation` |
| `needs_setup` | Config issue | Follow `instruction_for_llm`, usually → first-run-setup.md |
| `needs_validation_fix` | Environment blockers | List `blockers[].message`, follow each `llm_next_action` |
| `needs_human_decision` | No viable window — hard fail | READ `failure-handling.md` "硬失败处理" |
| `needs_cache_refresh` | Shipped cache stale / coverage low / git behind | READ `cache-refresh-handling.md`; present `actions[]` |
| `aws_api_error` | AWS call failed mid-run | Show `error_message`; follow `user_fix_options` |
| `validation_ok` | `--validate-only` success | Tell user config is usable |
| `validation_failed` | `--validate-only` found blockers | List blockers |
| `discovery_result` | `discover_orgs.py` output | Use `discovered_orgs[]` to build orgs.yaml |
| `script_bug` | Unhandled exception or invariant violation | Tell the user this is a bug; ask them to collect stderr |

## `recommendation` block (ok + ok_with_adjustment)

- `C_star_total_per_hour` — total optimal commitment (4 decimals)
- `C_existing_effective_per_hour` — existing Compute SP * utilization
- `delta_per_hour_to_buy` — **the number the user buys** (4 decimals)
- `annual_delta_usd` — delta × 8760 (list SP fee committed per year; 2 decimals)
- `expected_annual_sp_fee_usd` — delta × 8760 × (1 − e_sp) (net SP fee you pay; 2 decimals)
- `expected_annual_savings_usd` — delta × 8760 × [(1 − e_od)/(1 − d_raw) − (1 − e_sp)] (net savings vs no-SP counterfactual; 2 decimals)
- `coverage_after_purchase_pct` — (C_existing_eff + delta) / baseline.mean
- `formula_branch` — `stationary` or `trend_aware`
- `discount_rate_d` — workload-weighted (4 decimals)
- `bootstrap_95ci_on_C_star` — `[lo, hi]` or null (null when trend_aware)
- `bootstrap_se` — standard error (null when trend_aware)
- `trend_aware_audit` — only non-null when `formula_branch == "trend_aware"`
- `must_review_before_acting` — **the gate**: if true, `purchase_instructions` is null

## `baseline` block

- `requested_window` / `actual_window` — differ only when `ok_with_adjustment`
- `sample_size_hours` / `expected_sample_size_hours` / `completeness_pct`
- `raw_stats` — full percentile set (p0..p100, mean, std, min, max)
- `trend_classification` — `stable`, `increasing`, `mild_decline`, `moderate_decline`, `non_monotonic`. `severe_decline` never appears in baseline (rejected by window_search).
- `trend_buckets` — 3 equal-length periods with per-bucket p17/mean/min/max
- `trend_b3_over_b1_ratio` — the single-number health check
- `non_monotonic_details` — populated only when classification is non_monotonic
- `discount_audit` — EDP-calibrated discount computation (see below)

## `discount_audit` block (inside `baseline`)

The `discount_audit` block documents how `d_calibrated` was computed. All 15 fields:

| Field | Description |
|---|---|
| `d_raw` | List-price SP/OD discount from public pricing API, workload-weighted |
| `d_calibrated` | EDP-adjusted discount used as the actual quantile level in the formula |
| `e_sp` | EDP fraction on SP RecurringFee line items (`1 − net/unblended` from CUR) |
| `e_od` | EDP fraction on OD Usage line items (`1 − net/unblended` from CUR) |
| `edp_uniform` | `true` when `e_sp ≈ e_od` (info-level signal; d_calibrated ≈ d_raw) |
| `calibration_applied` | `true` when EDP factors were applied (i.e. EDP is present and non-trivial) |
| `e_sp_inferred_from_e_od` | `true` when SP RecurringFee history was insufficient; `e_sp` was set equal to `e_od` |
| `source` | Source of the ratios data (e.g. `"aws_public_pricing_api_weighted"`) |
| `pricing_cache_version` | Version string of the pricing ratios shipped cache used for this run |
| `pricing_cache_fetched_at` | ISO timestamp when the cache was built |
| `mix_window_days` | Length of the mix window used to weight ratios (default 30) |
| `coverage_pct` | Fraction of workload cost matched to pricing cache entries (0–1) |
| `matched_entries` | Count of `instance_family × region × os` entries successfully matched |
| `unmatched_entries` | Count of entries with no pricing cache match |
| `unmatched_top10_by_cost` | Top-10 unmatched entries by cost (for diagnostics; may be empty) |

## `current_sps` block

Array of active SPs (Compute + EC2Instance + SageMaker). Only Compute SPs count toward the gap calculation — EC2Instance/SageMaker are tracked but shown with `counted_toward_gap: false` and `effective_coverage_per_hour: 0.0` (v1 simplification).

## `risks` vs `warnings`

**Risks** affect numeric correctness. A risk with `must_review_before_acting: true` GATES `purchase_instructions` to `null` (INV-5).

**Warnings** are process/environment signals that don't invalidate the math. They never gate output.

For each risk:
- Show `user_facing_explanation` (Chinese) verbatim to the user
- Apply `recommended_actions_for_llm[]` in order

## `purchase_instructions` (when not gated)

Contains three blocks: `step_1_lookup_offering_id`, `step_2_create_savings_plan`, `safety_checklist_before_running`. When the user says "I want to buy", READ `references/purchase-execution.md` for the full protocol.

## `search_summary`

Always present for `ok*` statuses. Contains:
- `total_candidates: 75` (always)
- `passed` — number that survived validation
- `rejected_by_category` — severe_decline / incomplete_data / sp_transition_in_window
- `top_5_alternatives` — sorted by `deviation_score`

## `context.applied_exclude_filter`

When any exclude filter was active, this section describes what was filtered:

```json
"applied_exclude_filter": {
  "usage_type_patterns": ["g4dn."],
  "account_ids": ["123456789012"],
  "tag_exclusions": [{"key": "lifecycle", "values": ["ephemeral"]}],
  "include_untagged": true,
  "sp_coverage_share": 0.82,
  "sp_coverage_share_audit": {
    "retained_cost_usd": 820.5,
    "total_cost_usd": 1000.0,
    "note": "share = retained / total"
  },
  "skipped_filter_warnings": []
}
```

- `sp_coverage_share`: in [0, 1]. The existing SP's effective coverage has been
  multiplied by this factor before being reported as `C_existing_effective_per_hour`.
  Empty filter ⇒ 1.0 (no attenuation).
- `skipped_filter_warnings`: non-empty when a filter referenced a tag column
  not present in CUR. The filter was dropped silently from SQL; other axes
  unaffected.

When `applied_exclude_filter` is `null`, no filter was active and behavior is
identical to pre-v2 runs.
