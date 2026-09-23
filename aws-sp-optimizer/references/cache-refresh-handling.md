# Cache Refresh Handling

This page is for when `status == "needs_cache_refresh"`. Present the three options in `actions[]` verbatim. Wait for explicit user choice. Never silently run `--refresh-cache` or `git pull`.

---

## Why this fires

| `reason` code | Meaning | Typical fix |
|---|---|---|
| `cache_stale_over_1year` | Shipped `data/ratios.json.gz` is older than 1 year | `rebuild_locally` or `pull_from_git` |
| `cache_missing_regions` | Pricing cache has no entries for one or more regions in the workload | `rebuild_locally` |
| `coverage_below_threshold` | d_blended matched fewer than the required fraction of cost entries | `rebuild_locally` (adds new regions) |
| `shipped_data_behind_git` | Local `data/` is behind the remote branch; a fresher cache was already shipped | `pull_from_git` |

The full human-readable explanation is in `reason_detail`. The `context` field shows specific details (e.g., cache age in days, missing region names, current coverage percentage).

---

## Option A: `pull_from_git`

**When to pick it**: The remote branch already has an updated cache (`reason == shipped_data_behind_git`). Fastest path — no local rebuild needed.

**What it does**: Runs `git pull` (or equivalent) so the local `data/ratios.json.gz` is replaced with the current shipped version.

**After**: Re-run the optimizer with the same `--org` and flags. The cache is now current; the status should move past `needs_cache_refresh`.

---

## Option B: `rebuild_locally`

**When to pick it**: The cache is stale or missing regions and no updated version exists on the remote (`cache_stale_over_1year`, `cache_missing_regions`, `coverage_below_threshold`).

**What it does**: Runs `python -m scripts.aws_sp_optimizer --org <alias> --refresh-cache`. Downloads fresh SP/OD rate sheets from the AWS Pricing API for all configured regions (~5–15 minutes depending on region count), then writes the result to `data/ratios.json.gz`. After rebuilding, commit `data/` to the repository so the next user gets the warm cache.

**After**: Re-run the optimizer without `--refresh-cache`. Then `git add data/ && git commit -m "refresh pricing cache"` to ship the updated cache.

**Permission errors**: If this option fails with `shipped_data_not_writable`, the `data/` directory is owned by a different user (read-only install). You cannot fix this yourself — escalate to the user or a maintainer who has write access to run `--refresh-cache` and commit the result.

---

## Option C: `skip_refresh_once`

**When to pick it**: The user accepts the risk of stale pricing data and wants a result now. Useful for a quick directional estimate when the refresh can wait.

**What it does**: Re-runs the optimizer with `--skip-pricing-refresh`, which bypasses the staleness check and uses the existing cache as-is. The recommendation may use pricing ratios that are up to N days old (shown in `reason_detail`).

**After**: The optimizer proceeds normally. The cache remains stale — schedule a `rebuild_locally` when convenient. Do not use `skip_refresh_once` permanently.
