# Failure Handling

## 风险点处理 (when `risks[].must_review_before_acting: true`)

If ANY risk in the output has `must_review_before_acting: true`, `purchase_instructions` will be `null` (INV-5 hard gate). You cannot give the user a purchase command even if they ask for one.

Instead:
1. Show the user every risk's `user_facing_explanation` in order of severity (high → medium)
2. For each risk, apply `recommended_actions_for_llm[]`
3. Tell the user they MUST address the review items before re-running the optimizer
4. Offer to help investigate the root cause (typically via data exploration, not by bypassing the gate)

## 硬失败处理 (status: "needs_human_decision")

This status fires when `window_search` cannot find ANY viable candidate out of 75 — typically because:
- Severe decline across the whole 104-day MAX window
- Multiple SP transitions blanket every window
- Completeness below 90% everywhere (CUR gap)

The output contains a `failure_dossier` with:
- `reason_summary` — one-line diagnosis
- `rejection_summary` — counts and example rejections per category

**What to tell the user**:
1. The skill cannot recommend a number at this time
2. The dossier shows WHY (read `reason_summary` to them)
3. Offer three decision options:
   - **Do nothing**: wait for the workload to stabilize, re-run in a week
   - **Investigate the decline**: look at per-service costs to find the cause
   - **Buy conservatively**: use `aws ce get-savings-plans-purchase-recommendation` as a second opinion

Do NOT attempt to produce a `delta_per_hour_to_buy` by manually slicing the data — the whole point of the hard fail is that any single number would be misleading.

## `needs_validation_fix` (environment blockers)

The output has `blockers[]` with structured error codes. For each blocker:
1. Read `message` to the user
2. Apply `llm_next_action`
3. Show `user_fix_options[]` as a numbered list

Common blocker codes and fixes:
- `profile_auth_failed` → user runs `aws sso login --profile <name>`
- `account_id_mismatch` → SSO drift vs config drift; ask the user which
- `not_payer` → switch to a profile that authenticates as the Org master
- `cur_table_not_found` → CUR isn't configured, or the name in orgs.yaml is wrong
- `cur_schema_incomplete` → CUR was created without "Include resource IDs" or is CUR 2.0
- `cur_not_org_wide` → CUR must be recreated with Org-wide scope via Billing Console

## EDP anomaly (`needs_human_decision`, `error_code: edp_anomaly`)

Triggered when `e_sp` or `e_od` is ≥ 0.99 or < 0. These values indicate something is wrong with EDP factor extraction — either the CUR window predates EDP activation or the net/unblended ratio is inverted.

Steps:
1. Query CUR directly to verify SP RecurringFee and Usage net/unblended ratios for the last 30 days — confirm the window doesn't include periods before EDP was activated on the account
2. Check the contract with the AWS account team to confirm EDP start date and which line item types it covers
3. If the contract is unusual (e.g., SP fees fully excluded from EDP), fall back to `aws ce get-savings-plans-purchase-recommendation` as a second opinion rather than purchasing based on a miscalibrated `d_calibrated`

Do NOT attempt to manually override `e_sp` or `e_od` and re-derive `d_calibrated` — fix the underlying CUR window or contract interpretation instead.

## `d_calibrated` ≤ 0 (`error_code: d_calibrated_non_positive`)

SP would cost more than OD under the current PPA/EDP structure. The formula `d_calibrated = 1 − (1 − d_raw) · (1 − e_sp) / (1 − e_od)` evaluated to ≤ 0, meaning the SP fee (after EDP) equals or exceeds what you'd pay on OD.

Steps:
1. Do NOT purchase any SP under these conditions — the purchase would have negative expected savings
2. Review the contract: confirm SP RecurringFee EDP rate and OD Usage EDP rate
3. If the values look wrong, re-examine the CUR window (see "EDP anomaly" above)
4. If confirmed correct, the current EDP structure makes this SP type economically unattractive — escalate to the finance/AWS team for contract review

## `aws_api_error`

Shown when a ClientError / BotoCoreError escapes outside `validate_environment` (i.e. from CUR query, SP fetch, or CE call). Show `error_message` and run through `user_fix_options[]` with the user.

## Tag column missing (filter degraded)

When `context.applied_exclude_filter.skipped_filter_warnings` is non-empty, one
or more `--exclude-tag` filters referenced a CUR column that doesn't exist.
The filter was dropped silently from SQL for that key.

**What to tell the user:**
> 你 --exclude-tag 里的 key 'X' 在 CUR 里没有对应列（可能该 tag 从未被使用过，或
> tag key 归一化后和 CUR 里的列名不一致）。这个 tag 过滤被跳过了，其他 filter 轴
> 仍然生效。C* 结果可能偏离你的预期。

**Recovery options:**
1. Query `information_schema.columns WHERE table_schema='<cur_db>' AND column_name LIKE 'resource_tags_user_%'` to see what tag columns actually exist
2. Confirm the tag is really in use on resources (Resource Groups Tagging API)
3. If the tag is genuinely missing, remove it from `--exclude-tag` / config and
   re-run
