# aws-sp-optimizer

Compute the optimal 1-year No Upfront Compute Savings Plan commitment for an AWS Org payer account.

## Overview

Applies the **newsvendor formula** `C* = quantile_d(X)` to hourly gross on-demand-equivalent CUR data, where:
- `X` = hourly gross usage across EC2 BoxUsage (non-Spot) + Lambda + Fargate
- `d` = workload-weighted discount rate from AWS public pricing API

The skill is **read-only on AWS**. Purchase execution is strictly manual via the printed CLI command block.

## Install

```bash
cd skills/aws-sp-optimizer
python -m pip install -e ".[test,dev]"
```

Python ≥3.10 required.

## Configuration

Create `~/.config/aws-sp-optimizer/orgs.yaml`. Run the discovery probe to auto-generate candidates:
```bash
python -m scripts.discover_orgs
```

See `references/first-run-setup.md` for the full dialog.

## Usage

```bash
python -m scripts.aws_sp_optimizer --org a4x-us
```

Full CLI flags: see `references/usage.md`.

## Pricing cache

The skill ships with a pre-built pricing cache at
`skills/aws-sp-optimizer/data/ratios.json.gz` (gzipped) covering common
regions (currently `us-east-1` + `eu-central-1`, ~10 MB). On first run,
the skill loads from this shipped cache rather than downloading 800 MB
of raw AWS rate sheets per region.

**Cache location**: the skill reads exclusively from
`skills/aws-sp-optimizer/data/ratios.json.gz` + `data/version.json` —
there is no `~/.cache/aws-sp-optimizer/` directory or per-user cache.
All state lives inside the skill repo and is version-controlled.

**When user runs in a region not in the shipped set**: auto-discovery
fires, the skill detects the missing region, and rebuilds for that
region only. The first such run pays a one-time download cost
(~5-15 minutes per missing region) and overwrites `data/ratios.json.gz`
in place.

**To refresh the shipped cache (maintainer only):**
```bash
python -m scripts.aws_sp_optimizer --org <alias> --refresh-cache
# --refresh-cache writes directly to data/ratios.json.gz + data/version.json
git add data/ratios.json.gz data/version.json
git commit -m "refresh: pricing cache @ $(date -u +%Y-%m-%d)"
```

**Power-user override**: `--only-regions us-east-1,eu-central-1` bypasses
discovery entirely, using only the listed regions. Useful for deterministic
runs against the shipped cache.

## Known v1 design debts

See `docs/plans/2026-04-21-skill-v2-introduction.md` for the full v2 scope and known debts; the short list:

1. Ignores EC2 Instance SP effective coverage (Compute SP only)
2. Linear trend only (no Monte Carlo)
3. Hardcoded 30-day mix window for d_blended
4. Bootstrap CI only for stationary branch
5. No per-service baseline
6. Org-aggregate utilization per SP
7. Tenancy not included in mix key (OS is)

## Dev

```bash
make check    # ruff + mypy + pytest
make test-cov # coverage report
```
