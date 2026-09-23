# Formula Derivation

The skill implements the **newsvendor model** from operations research, applied to the SP commitment decision.

## Setup

The decision variable `A` is AWS `--commitment` in **list SP fee $/hr** (the value you pass to `aws savingsplans create-savings-plan --commitment`, which AWS bills at list before EDP credits are applied). Working in this space means the quantile returned by the formula is directly usable with the AWS API — no post-hoc unit conversion.

- `A` = AWS `--commitment` ($/hr, list SP fee; pre-EDP)
- `X_gross` = hourly gross on-demand list cost ($/hr), the counterfactual "what you'd pay at OD rates if no SP existed"
- `Y = X_gross · (1 − d_raw)` = hourly demand projected into AWS `--commitment` space
- `d_raw` = workload-weighted SP-vs-OD discount from the AWS public pricing API
- `e_sp`, `e_od` = EDP (Enterprise Discount Program) fractions on SP RecurringFee and OD Usage respectively, derived from CUR: `e = 1 − net_unblended / unblended`

## Per-hour net cost with commitment `A`

Paying list SP fee `A/hr`, the SP covers up to `A/(1 − d_raw)` of gross OD per hour. Any excess spills to OD and is billed net of EDP:

```
cost_net(A, X_gross) = A · (1 − e_sp)                       [SP fee, net of EDP]
                    + max(0, X_gross − A/(1 − d_raw))
                        · (1 − e_od)                        [residual OD, net]
```

Substituting `Y = X_gross · (1 − d_raw)`:

```
cost_net(A, Y) = A · (1 − e_sp)
             + max(0, Y − A) · (1 − e_od) / (1 − d_raw)
```

## First-order optimality

Differentiate w.r.t. `A`:

```
∂ cost_net / ∂A = (1 − e_sp) − (1 − e_od)/(1 − d_raw) · P(Y > A) = 0
⟹ P(Y > A*) = (1 − e_sp) · (1 − d_raw) / (1 − e_od)
⟹ F_Y(A*) = 1 − (1 − e_sp)(1 − d_raw) / (1 − e_od)
          = d_calibrated
⟹ A* = quantile_{d_calibrated}(Y)
```

The **calibrated discount rate** absorbs the differential EDP treatment of SP vs OD:

```
d_calibrated = 1 − (1 − d_raw) · (1 − e_sp) / (1 − e_od)
```

## Implementation note

The code computes `Y[h] = total_list_usd[h] · (1 − d_raw)` per hour (see `scripts/newsvendor.py::compute_optimal_commit`). `total_list_usd` comes from the CUR column `pricing_public_on_demand_cost`, which AWS populates on both `Usage` and `SavingsPlanCoveredUsage` rows with the counterfactual list OD price — so existing-SP hours still contribute the "demand they would have had without SP coverage", not zero.

## Sanity checks

- Uniform EDP (`e_sp = e_od`): `d_calibrated = d_raw`; the calibration is a no-op
- SP fee not discounted (`e_sp = 0, e_od > 0`): `d_calibrated < d_raw` (SP less attractive → commit less)
- SP fee discounted more (`e_sp > e_od`): `d_calibrated > d_raw` (SP more attractive → commit more)
- `d_calibrated = 0` → `A* = min(Y)` (commit nothing)
- `d_calibrated = 0.5` → `A* = median(Y)`
- `d_calibrated = 1` → `A* = max(Y)` (commit up to peak)

## Annual flows (derived from `A*`)

Once `A*` (the recommended AWS `--commitment`) is known:

- **`annual_delta_usd = A* · 8760`** — list SP fee you commit to annually (what shows up as `SavingsPlanRecurringFee` unblended cost in CUR)
- **`expected_annual_sp_fee_usd = A* · 8760 · (1 − e_sp)`** — net amount you actually pay (list SP fee after EDP credit)
- **`expected_annual_savings_usd = A* · 8760 · [(1 − e_od)/(1 − d_raw) − (1 − e_sp)]`** — net net-OD-cost saved vs. the no-SP counterfactual for the same demand volume

## Why we compute `d` from AWS public pricing + workload mix

`d` varies by instance family (e.g. c5 vs r6g), by OS (Linux vs Windows), and by region. The skill weights the per-instance discount by each instance's share of recent 30-day cost:

```
d_blended = 1 − Σ_i (cost_i × ratio_i) / Σ_i cost_i
```

where `ratio_i = sp_rate_i / od_rate_i` from the pricing cache. This gives a discount that matches the user's actual workload rather than a textbook average.

## Trend-aware extension

When the baseline is declining moderately (b3/b1 in [0.85, 0.95)), the stationary assumption breaks. The trend-aware branch solves:

```
(1/T) Σ_{t=1}^{T} F_t(A*) = d_calibrated
```

where `F_t` is the CDF at future hour `t`, derived from a robust linear fit + weekly seasonality + stationary residuals on the same `Y` demand series. Under the fit, the residual distribution matches the stationary case — only the mean shifts over time.

This collapses back to `F(A*) = d_calibrated` when `F_t = F` (no drift).

## Why 1y No Upfront Compute SP specifically (v1 scope)

- **1y** — 3y has higher discount but longer lock-in; out of scope for v1
- **No Upfront** — matches the newsvendor cash-flow assumption (pay equal rate every hour)
- **Compute SP** — covers EC2 + Lambda + Fargate; EC2 Instance SP has family-lock that v1 doesn't handle

Other variants (3y, Partial/All Upfront, EC2 Instance SP) are v2+ candidates per spec 10.3.
