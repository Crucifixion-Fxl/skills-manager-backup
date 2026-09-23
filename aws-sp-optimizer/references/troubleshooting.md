# Troubleshooting

## Config issues

### `config_missing`
The file `~/.config/aws-sp-optimizer/orgs.yaml` doesn't exist. Run `first-run-setup.md`.

### `config_invalid`
YAML parse failure. Run the error message through a YAML linter, fix the file, re-run.

### `config_schema_invalid`
The file is valid YAML but doesn't have the expected structure. Compare against the schema in spec 4.1 or the template in `first-run-setup.md`.

### `org_alias_not_found`
The `--org <alias>` value isn't in the file. The error's `context.available_aliases` lists what IS there.

## AWS auth issues

### `profile_auth_failed` (NoCredentialsError)
1. Run `aws sso login --profile <name>` if using SSO
2. Check `~/.aws/credentials` and `~/.aws/config` for the profile
3. Confirm role/session hasn't expired

### `account_id_mismatch`
The profile now resolves to a different account than `orgs.yaml` expects. Ask the user whether:
- **SSO drift**: they re-logged into a different account — re-authenticate with the right one
- **Config drift**: the Org master changed — edit `orgs.yaml`

### `not_in_organization`
The profile authenticates as a standalone account. This skill needs an AWS Organization. Tell the user v1 doesn't support standalone accounts.

### `not_payer`
The profile is a member account, not the Org master. Switch to a profile that authenticates as the Org's `master_account_id` (shown in the error context).

## CUR issues

### `cur_table_not_found`
Either `cur_database`/`cur_table` in `orgs.yaml` are wrong, or CUR isn't configured in this account. Verify via AWS Console → Billing → Cost and Usage Reports.

### `cur_schema_incomplete`
The table exists but is missing required columns. Two common causes:
1. CUR was created WITHOUT "Include resource IDs" — recreate with that option checked
2. This is CUR 2.0 (new schema) — v1 only supports CUR v1; wait for v2 or manually downgrade

### `cur_not_org_wide`
CUR exists but covers fewer than 50% of the Org's active members. The report must be recreated at Org management scope.

### `athena_probe_failed`
The schema check query failed. Verify:
1. `athena_output` S3 path is writable by the profile
2. Athena workgroup exists and the profile can use it

## Pricing cache issues

### `pricing_api_unreachable`
Network error fetching `pricing.us-east-1.amazonaws.com`. Check connectivity. If offline, use `--skip-pricing-refresh` with an existing cache.

### `pricing_json_corrupt`
The shipped `data/ratios.json.gz` is corrupted. Run `--refresh-cache` to rebuild it from the AWS Pricing API, then commit the result to the repository.

### `term_not_found`
The expected "1 year No Upfront Compute Savings Plan" term isn't in the downloaded SP rate sheet. This means AWS changed the term naming — file an issue.

### `insufficient_d_coverage` (NoDataError: insufficient_matched_entries_for_d)
Fewer than 10 CUR mix entries matched the pricing cache. Usually:
1. The CUR mix includes regions the cache hasn't downloaded yet → run with `--refresh-cache`
2. The workload uses very recent instance types not yet in the pricing cache → retry in a day; AWS usually backfills
3. The profile's `primary_region` doesn't match the workload's actual region mix — verify

### `skip_refresh_but_no_cache`
You passed `--skip-pricing-refresh` but no cache exists for the required regions. Drop the flag and run once online to warm the cache.

### `shipped_data_not_writable`
Root cause: the `data/` directory in the skill installation is owned by a different user (read-only install). `--refresh-cache` cannot write to `data/ratios.json.gz`.

Fix: a maintainer with write access to the repository must run `--refresh-cache` and commit the updated `data/` to the branch. The user running the optimizer does not need write access — they pull the updated cache via `git pull`.

### EDP factors look wrong

Symptoms: `e_sp` or `e_od` in `discount_audit` are 0 when you have EDP, or are unexpectedly different from each other.

Common causes and checks:
1. **Window predates EDP activation** — if your EDP start date is within the baseline window, the net/unblended ratio will be diluted by pre-EDP periods where net = unblended. Narrow the window (`--window-end`) to start after EDP activation.
2. **SP type mix** — `e_sp` is derived from SP RecurringFee line items across all SP types (Compute + EC2Instance + SageMaker). If one type dominates with a different EDP structure, the blended `e_sp` may look off. This is acceptable because all SP types receive the same EDP rate on RecurringFee under standard contracts.
3. **Insufficient SP history** — if `e_sp_inferred_from_e_od` is `true`, there were too few SP RecurringFee records to compute a stable `e_sp`, so the system fell back to using `e_od`. This is safe when EDP applies uniformly.

## Athena / CUR query issues

### `query_failed`
Athena returned a `FAILED` state. `error_message` has the AWS-returned reason. Common causes:
- Partition mismatch — if the month/year partition keys are non-standard, the spec's `generate_partition_filter` won't find anything (month must be zero-padded to 2 digits)
- Unknown column — CUR schema shifted; re-run validate_environment to see the schema diff

### `query_timeout`
Athena took longer than 60s. Either increase the timeout in `_common.py::athena_execute` or investigate why the query is slow (usually missing partition pruning or huge uncompressed tables).

## Output issues

### `script_bug` (exit 1)
Either an uncaught exception or an output invariant violation. Collect stderr and file an issue. The `error_type` field distinguishes:
- Python exception class name — an unhandled runtime error
- `InvariantViolation` — the output dict failed `validate_output_invariants`

### INV-7 failing (annual_delta_usd != delta_per_hour_to_buy * 8760)
Only fires if someone edits `build_output` and forgets to use `round(final.C_new_delta, 4)` as the input for both `delta_per_hour_to_buy` AND the annual calculations. Fix: re-derive everything from the rounded value.

## Required IAM permissions (spec 4.5)

When you hit `permissions_missing` blockers, the profile's IAM policy needs
the permissions below. Copy-paste this sample policy into the profile's
role and re-run.

**Phase A (startup validation)**:
```
sts:GetCallerIdentity
organizations:DescribeOrganization
organizations:ListAccounts
glue:GetDatabase
glue:GetTable
glue:GetPartitions
athena:StartQueryExecution
athena:GetQueryExecution
athena:GetQueryResults
athena:GetWorkGroup
```

**Phase C (CUR query)** — Phase A permissions plus S3 for Athena query results:
```
s3:GetBucketLocation
s3:PutObject                 # on athena_output bucket prefix
s3:GetObject                 # on athena_output bucket prefix
s3:ListBucket                # on athena_output bucket
```

**Phase F (Savings Plans)**:
```
savingsplans:DescribeSavingsPlans
savingsplans:DescribeSavingsPlansOfferings
ce:GetSavingsPlansUtilization
ce:GetCostAndUsage
```

### Sample IAM policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SPOptimizerReadOnly",
      "Effect": "Allow",
      "Action": [
        "sts:GetCallerIdentity",
        "organizations:DescribeOrganization",
        "organizations:ListAccounts",
        "glue:GetDatabase",
        "glue:GetTable",
        "glue:GetPartitions",
        "athena:StartQueryExecution",
        "athena:GetQueryExecution",
        "athena:GetQueryResults",
        "athena:GetWorkGroup",
        "savingsplans:DescribeSavingsPlans",
        "savingsplans:DescribeSavingsPlansOfferings",
        "ce:GetSavingsPlansUtilization",
        "ce:GetCostAndUsage"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SPOptimizerAthenaResults",
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::<athena-output-bucket>",
        "arn:aws:s3:::<athena-output-bucket>/*"
      ]
    }
  ]
}
```

**NOT granted to the skill profile**: `savingsplans:CreateSavingsPlan`. The
skill is strictly read-only on AWS; purchase execution is manual. The user
must have `CreateSavingsPlan` on their OWN profile (separate from the skill's
profile) to run the command block the skill prints.

## When in doubt

1. Re-run with `--verbose 2>/tmp/sp-opt.log` and inspect the stderr
2. Re-run with `--validate-only` to isolate config vs AWS env issues
3. Re-run with `--refresh-cache` to force pricing cache rebuild
