# Usage

## Main command

```bash
python -m scripts.aws_sp_optimizer --org <alias>
```

Emits exactly one JSON object to stdout. Logs go to stderr.

## All flags (spec 3.6)

| Flag | Default | Description |
|---|---|---|
| `--org <alias>` | (required) | Config alias from `orgs.yaml` |
| `--window-days N` | from config or 90 | Baseline window size, [60, 90] |
| `--window-end YYYY-MM-DD` | from config or `today` | Right edge of baseline (exclusive) |
| `--prefer freshness\|sample-size\|balanced` | `balanced` | Window search preference |
| `--profile <name>` | from config | Override AWS profile |
| `--cur-database <name>` | from config | Override CUR Glue database |
| `--cur-table <name>` | from config | Override CUR table |
| `--athena-output s3://...` | from config | Override Athena results location |
| `--athena-workgroup <name>` | from config | Override Athena workgroup |
| `--refresh-cache` | false | Force rebuild of pricing ratios cache |
| `--skip-pricing-refresh` | false | Use existing cache without version check (offline) |
| `--verbose` | false | Enable DEBUG logging |
| `--log-file PATH` | none | Redirect stderr to file |
| `--validate-only` | false | Run Phase A validation only and emit the result |

## Environment variables

- `AWS_PROFILE` / `AWS_DEFAULT_PROFILE` / `AWS_SSO_SESSION` — standard AWS SDK vars; only consulted if `orgs.yaml` profile resolution fails

Log level is controlled via `--verbose` (CLI flag), not via env var.

## Typical runs

**Default**: 90-day window ending now, balanced preference.
```bash
python -m scripts.aws_sp_optimizer --org a4x-us
```

**Conservative**: shorter window, prefer larger sample.
```bash
python -m scripts.aws_sp_optimizer --org a4x-us --window-days 60 --prefer sample-size
```

**Validate config after editing**:
```bash
python -m scripts.aws_sp_optimizer --org a4x-us --validate-only
```

**Debug with verbose logs**:
```bash
python -m scripts.aws_sp_optimizer --org a4x-us --verbose 2>/tmp/sp-opt.log
```

## Exclude filters

Compute C* excluding portions of workload you don't want covered by the new SP
(e.g. instance families being migrated to Spot / another cloud, sandbox
accounts, tagged-as-ephemeral resources).

| Flag | Example | Notes |
|---|---|---|
| `--exclude-usage-type-pattern` | `g4dn.` | Excludes any line_item_usage_type containing the pattern. Repeatable. Validated against `[A-Za-z0-9._:-]+`. |
| `--exclude-account-id` | `123456789012` | Excludes rows billed to this 12-digit AWS account id. Repeatable. |
| `--exclude-tag` | `lifecycle=ephemeral,experimental` | Excludes rows whose CUR user tag `lifecycle` is `ephemeral` or `experimental`. Repeatable. Key is normalized per AWS rules (non-alphanumeric → `_`). |
| `--include-untagged` | (default) | Rows with NULL value for a filtered tag column are **kept**. |
| `--exclude-untagged` | | Rows with NULL value for any filtered tag column are **excluded**. |

All filters compose with AND semantics. CLI flags are additive on top of any
`exclude:` block configured in `orgs.yaml`.

### YAML config equivalent

```yaml
orgs:
  a4x-us:
    # ...existing fields...
    exclude:
      usage_type_patterns: ["g4dn.", "p4d."]
      account_ids: ["123456789012"]
      tags:
        - key: lifecycle
          values: [ephemeral, experimental]
        - key: cost-category
          values: [research]
      include_untagged: true   # default
```

### Effect on C_existing_effective

When a filter is active, existing SP's effective coverage is attenuated by
`sp_coverage_share` = (SP-covered cost retained under filter) / (total
SP-covered cost) — both measured over the **same CUR window the baseline
uses** (`requested_end − 14 − 90` to `requested_end`, i.e. up to ~104 days,
matching `max_window_start`). This deliberately does **not** share the 30-day
window used by `utilization_30d` on each SP: share is a retention ratio, so
it needs enough history to be stable; utilization is an instantaneous steady-
state rate that keys off the live Cost Explorer utilization API. They are
independently computed and reported; do not mix them in downstream math.
This prevents double-counting the portion of existing SP that was covering
filtered-away workload. See `applied_exclude_filter.sp_coverage_share` in
the output.

### When a tag column is missing

If a filtered tag key has no column in the CUR table (tag was never used),
the filter for that key is **skipped with a warning**. Other filter axes still
apply. Check `applied_exclude_filter.skipped_filter_warnings` in the output.

## Expected runtime

- Warm (cache hit): <15s
- Cold (single region cache rebuild): <200s
- Cold (3+ regions): <400s

## Cache refresh flow

The pricing ratios cache (`data/ratios.json.gz`) is shipped with the repository and covers the regions included at build time. When `status == "needs_cache_refresh"`, use `--refresh-cache` to rebuild it.

```bash
python -m scripts.aws_sp_optimizer --org <alias> --refresh-cache
```

- Downloads fresh SP/OD rate sheets from the AWS Pricing API for all configured regions
- Overwrites `data/ratios.json.gz` in place
- Takes ~5–15 minutes depending on region count (single region ~200s, 3+ regions ~400s)
- After rebuilding, commit `data/` to the repository so subsequent users get the warm cache:

```bash
git add data/ratios.json.gz data/version.json
git commit -m "refresh pricing cache"
```

If `data/` is read-only (write permission denied), see `references/troubleshooting.md` → `shipped_data_not_writable`.

## Exit codes (spec 3.4)

| Code | Meaning |
|---|---|
| 0 | `ok` / `ok_with_adjustment` / `validation_ok` / `discovery_result` |
| 1 | Uncaught exception or invariant violation (`script_bug`) |
| 2 | `needs_setup` / `needs_validation_fix` / `needs_human_decision` / `needs_cache_refresh` / `validation_failed` |
| 3 | `aws_api_error` |
