# First-Run Setup

When `~/.config/aws-sp-optimizer/orgs.yaml` is missing, invalid, or doesn't contain the requested alias, run this flow.

The pricing ratios cache is shipped as `data/ratios.json.gz` inside the repository — there is no `~/.cache/aws-sp-optimizer/` directory. The only file the user needs to configure is `orgs.yaml`.

## Step 1: Probe AWS profiles

```bash
python -m scripts.discover_orgs
```

This emits a JSON `DiscoveryOutput` with `discovered_orgs[]`. Each entry has:
- `profile` — the AWS CLI profile name
- `account_id` — 12-digit account number
- `can_use` — true iff the profile authenticates AND is the Org master
- `reason` — if `can_use` is false: `auth_failed` / `not_in_organization` / `not_payer`
- `suggested_alias` — heuristic alias (e.g. `a4x` from `yfeng@a4x.io`)
- `sanity_check_fields.{payer_account_id, org_id, master_account_email}` — for the user to eyeball
- `cur_candidates[]` / `athena{}` / `athena_output_suggestions[]` — reserved for a future Glue/Athena probe; **in v0.1 these are always empty placeholders**, so skip to Step 2's manual prompts

## Step 2: Present candidates to the user

For each `can_use=True` entry, ask the user to confirm:

1. **Alias**: the short key you'll use with `--org <alias>`. Default to `suggested_alias`.
2. **CUR database / table**: ask the user directly (e.g. `cur-db` / `a4x_report`). Since `cur_candidates[]` is an empty stub in v0.1, do not rely on it.
3. **Athena output**: ask for the S3 path where Athena query results go (e.g. `s3://<bucket>/sp-optimizer/`). `athena.primary_workgroup_default_output` is not yet populated, so prompt the user directly.
4. **Athena workgroup**: default to `primary` unless the user says otherwise.

## Step 3: Show the final YAML for approval

Before writing the file, display it exactly as it will be written. Example:

```yaml
schema_version: 1

orgs:
  a4x-us:
    description: "A4X US production / consolidated billing payer"
    profile: aws-002497567426-us-tech-service
    payer_account_id: "002497567426"
    org_id: o-4bw6d7ou28
    cur_database: cur-db
    cur_table: a4x_report
    athena_output: s3://a4x-cur-query/sp-optimizer/
    athena_workgroup: primary
    primary_region: us-east-1
    defaults:
      window_days: 90
      window_end: today
      prefer: balanced
```

**Ask**: "This is what I'll write to `~/.config/aws-sp-optimizer/orgs.yaml`. Proceed?"

## Step 4: Write and verify

After the user confirms:
```bash
mkdir -p ~/.config/aws-sp-optimizer
# write the file
python -m scripts.aws_sp_optimizer --org a4x-us --validate-only
```

Expected: `validation_ok` status. If `validation_failed`, follow the `blockers[].llm_next_action` hints.

## Appending to an existing file

If `orgs.yaml` already exists and the user is adding a second Org:
1. Parse the current file
2. Add the new alias under `orgs:`
3. Write it back — **do not overwrite** other aliases
