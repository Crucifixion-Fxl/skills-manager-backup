# Purchase Execution

## Units note

`delta_per_hour_to_buy` and `purchase_instructions.summary.commitment_per_hour_usd` are both in **AWS `--commitment` units: USD/hr at list SP fee rate (pre-EDP)**. This is the same quantity AWS `savingsplans create-savings-plan --commitment` expects — pass the number through verbatim. AWS bills the commitment at list; any Enterprise Discount Program credit is applied later as a separate line item, which is why `expected_annual_sp_fee_usd` multiplies by `(1 − e_sp)` to surface the net amount you will actually pay.

`expected_annual_savings_usd` compares that net SP fee against the counterfactual net on-demand cost for the same demand volume (`delta × 8760 × (1 − e_od) / (1 − d_raw)`), giving `delta × 8760 × [(1 − e_od)/(1 − d_raw) − (1 − e_sp)]`. See `references/formula-derivation.md` for the derivation.

## 5 MUST rules

Before relaying any purchase command to the user, you MUST:

1. **Verify `must_review_before_acting` is false** — if `true`, `purchase_instructions` is `null` (INV-5). Tell the user they need to address the review items first. Do NOT craft the command manually.
2. **Show the `headline_warning` verbatim** — the ⚠️ irreversibility disclaimer is not optional.
3. **Walk through `safety_checklist_before_running[]` one by one** — each check is a command or condition the user must satisfy BEFORE pasting step 2.
4. **Never auto-execute `aws savingsplans create-savings-plan`** — the user must paste it manually, even if they say "just do it".
5. **Present alternatives** — show `alternatives_if_not_confident[]` as options, especially the "buy half first" and "cross-check with AWS CE" strategies.

## Pre-flight checklist (spec 8.4)

For each entry in `safety_checklist_before_running[]`:
- `check` — one-line description
- `command` — if present, a shell command to run
- `expected` — the expected output
- `condition` — if present, a human-judgment check
- `if_older` — for the age check: what to do if the recommendation is stale
- `note` — for informational entries

Walk through them in order. Don't skip any. If a check fails, STOP and tell the user what went wrong before they paste step 2.

## Step 1: lookup offering ID

Show `step_1_lookup_offering_id.command` to the user. Expected output is a single offering ID string (UUID-like, ~36 chars). Common failures:
- Empty output → offering not available in this region; unlikely but possible during AWS maintenance
- Permission denied → profile lacks `savingsplans:DescribeSavingsPlansOfferings`
- Multiple results → the query filter needs tightening; escalate

## Step 2: create the SP

Show `step_2_create_savings_plan.command_template` — the user must replace `<PASTE_OFFERING_ID_FROM_STEP_1>` with the offering ID from step 1.

Show `what_aws_does_when_executed[]` and `what_aws_does_NOT_do[]` in full. The second list is especially important — AWS does NOT send an email, does NOT allow cancellation, does NOT prorate the first hour.

## Post-purchase verification

After the user reports success:
1. Run `post_purchase_verification.immediate_check.command` to confirm the SP is `active` (or `payment-pending`)
2. Tell the user that Cost Explorer will reflect the purchase within 24h and CUR within 24-48h
3. Remind them to run `post_purchase_verification.utilization_check_at_week_1` after a week

## Alternatives if not confident

Always show `alternatives_if_not_confident[]` at the end. The four v1 options are:
1. **Buy half first** — lower risk; re-evaluate in 30 days
2. **Wait 1 week and re-run** — if the trend signal was marginal
3. **Purchase via AWS console** — visual confirmation before commit
4. **Cross-check with AWS's own recommendation** — if AWS CE gives a similar number (±20%), confidence is high

If the user is new to SPs, nudge them toward option 1 (buy half) regardless of confidence level.
