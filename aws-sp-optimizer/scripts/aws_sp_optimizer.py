"""Main entry point — parse CLI args, orchestrate modules, emit JSON.

See spec 5.10, 9.4, 9.5.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from scripts import config_loader, cur_query, pricing_cache
from scripts._common import (
    NeedsCacheRefreshError,
    NoDataError,
    OrgConfig,
    PricingCacheMeta,
    SPOptimizerError,
    ValidationBlockerError,
    Window,
    base_output,
    compute_sp_coverage_share,
    fetch_all_relevant_sps,
    parse_window_end,
    validation_context_to_public_dict,
)
from scripts.exclude_filter import (
    ExcludeFilter,
    TagExclusion,
    fetch_available_tag_columns,
    validate_account_id,
    validate_tag_key,
    validate_tag_value,
    validate_usage_type_pattern,
)
from scripts.newsvendor import compute_optimal_commit
from scripts.output_builder import build_output, make_warning, validate_output_invariants
from scripts.validate_environment import validate_environment
from scripts.window_search import search_best_window

logger = logging.getLogger(__name__)


class CliFilterError(ValueError):
    """Raised when a CLI filter flag has an unparseable or invalid value."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aws-sp-optimizer",
        description="Compute optimal 1y No Upfront Compute SP commitment for an AWS Org payer",
    )
    parser.add_argument(
        "--org", dest="org_alias", required=True, help="Config alias from orgs.yaml"
    )

    parser.add_argument("--window-days", type=int, help="Baseline window size [60-90]")
    parser.add_argument("--window-end", type=str, help="Window end date (YYYY-MM-DD) or 'today'")
    parser.add_argument(
        "--prefer",
        choices=["freshness", "sample-size", "balanced"],
        help="Preference when window_search picks among candidates",
    )

    # Inline config overrides (see 3.6)
    parser.add_argument("--profile", type=str, help="Override config profile")
    parser.add_argument("--cur-database", type=str, help="Override CUR Glue database")
    parser.add_argument("--cur-table", type=str, help="Override CUR table")
    parser.add_argument(
        "--athena-output", type=str, help="Override Athena query results S3 location"
    )
    parser.add_argument("--athena-workgroup", type=str, help="Override Athena workgroup")

    # Pricing cache
    parser.add_argument(
        "--refresh-cache", action="store_true", help="Force rebuild of pricing ratios cache"
    )
    parser.add_argument(
        "--skip-pricing-refresh",
        action="store_true",
        help="Use existing cache without version check (offline escape hatch)",
    )

    # Region discovery
    parser.add_argument(
        "--additional-regions",
        type=str,
        default=None,
        help="Comma-separated list of extra regions to union with discovered regions",
    )
    parser.add_argument(
        "--only-regions",
        type=str,
        default=None,
        help=(
            "Comma-separated list of regions to use EXCLUSIVELY, bypassing "
            "auto-discovery and --additional-regions. Power-user escape hatch "
            "for deterministic runs and when relying on a known shipped cache"
        ),
    )

    # Exclude filter axes (see references/usage.md#exclude-filters)
    parser.add_argument(
        "--exclude-usage-type-pattern", action="append", default=[], metavar="PATTERN",
        help="Exclude rows whose line_item_usage_type contains PATTERN (e.g. 'g4dn.'). "
             "Repeatable. Validated against [A-Za-z0-9._:-]+",
    )
    parser.add_argument(
        "--exclude-account-id", action="append", default=[], metavar="ACCOUNT_ID",
        help="Exclude rows billed to this 12-digit AWS account id. Repeatable.",
    )
    parser.add_argument(
        "--exclude-tag", action="append", default=[], metavar="KEY=V1,V2",
        help="Exclude rows where CUR user tag KEY is in {V1,V2,...}. Repeatable. "
             "Example: --exclude-tag lifecycle=ephemeral,experimental",
    )
    untagged = parser.add_mutually_exclusive_group()
    # default=None so merge_cli_into_config_filter can tell "user didn't pass
    # either flag" (keep config value) from "user explicitly asked for include
    # or exclude" (override config).
    untagged.add_argument(
        "--include-untagged", dest="include_untagged", action="store_true",
        default=None,
        help="Rows with NULL value for a filtered tag column are kept. "
             "Overrides orgs.yaml include_untagged=false when passed.",
    )
    untagged.add_argument(
        "--exclude-untagged", dest="include_untagged", action="store_false",
        help="Rows with NULL value for any filtered tag column are excluded. "
             "Overrides orgs.yaml include_untagged=true when passed.",
    )

    # Diagnostics
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--log-file", type=str, default=None, help="Write stderr logs to a file")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Run Phase A validation only and emit the result",
    )

    return parser.parse_args(argv)


def _parse_cli_tag(spec: str) -> TagExclusion:
    """Parse one --exclude-tag KEY=V1,V2,... into a TagExclusion.

    Raises CliFilterError on malformed spec: missing '=', empty values, or
    validator-rejected key/values.
    """
    if "=" not in spec:
        raise CliFilterError(
            f"--exclude-tag {spec!r} malformed: expected KEY=V1,V2,..."
        )
    key, _, vals_raw = spec.partition("=")
    try:
        validate_tag_key(key)
    except ValueError as e:
        raise CliFilterError(str(e)) from e
    vals = tuple(v for v in (s.strip() for s in vals_raw.split(",")) if v)
    if not vals:
        raise CliFilterError(f"--exclude-tag {spec!r} has no values")
    for v in vals:
        try:
            validate_tag_value(v)
        except ValueError as e:
            raise CliFilterError(str(e)) from e
    return TagExclusion(key=key, values=vals)


def merge_cli_into_config_filter(
    config_filter: ExcludeFilter,
    args,
) -> ExcludeFilter:
    """Union config-level filter with CLI args. CLI is additive.

    Dedups while preserving order (config first, CLI appended). `include_untagged`
    defers to the config value unless the user explicitly passed
    --include-untagged or --exclude-untagged on the command line
    (argparse default=None for that pair so merge can detect 'not passed').
    """
    cli_patterns = list(args.exclude_usage_type_pattern or [])
    for p in cli_patterns:
        try:
            validate_usage_type_pattern(p)
        except ValueError as e:
            raise CliFilterError(str(e)) from e
    cli_accounts = list(args.exclude_account_id or [])
    for a in cli_accounts:
        try:
            validate_account_id(a)
        except ValueError as e:
            raise CliFilterError(str(e)) from e
    cli_tags = [_parse_cli_tag(s) for s in (args.exclude_tag or [])]

    def _dedup(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                out.append(x)
                seen.add(x)
        return tuple(out)

    include_untagged = (
        args.include_untagged
        if args.include_untagged is not None
        else config_filter.include_untagged
    )
    return ExcludeFilter(
        usage_type_patterns=_dedup(
            tuple(config_filter.usage_type_patterns) + tuple(cli_patterns)
        ),
        account_ids=_dedup(
            tuple(config_filter.account_ids) + tuple(cli_accounts)
        ),
        tag_exclusions=_dedup(
            tuple(config_filter.tag_exclusions) + tuple(cli_tags)
        ),
        include_untagged=include_untagged,
    )


def _validate_only_output(config: OrgConfig, validation) -> dict:
    """Return validate-only output dict (blockers or ok)."""
    if validation.blockers:
        return {
            **base_output(),
            "status": "validation_failed",
            "config_alias": config.alias,
            "blockers": [b.to_dict() for b in validation.blockers],
            "validation_context": validation_context_to_public_dict(validation.context),
            "warnings": validation.warnings,
            "instruction_for_llm": (
                f"Validation failed for alias {config.alias!r} with "
                f"{len(validation.blockers)} blocker(s). Inspect blockers[] and "
                f"resolve each before re-running."
            ),
        }
    return {
        **base_output(),
        "status": "validation_ok",
        "config_alias": config.alias,
        "blockers": [],
        "validation_context": validation_context_to_public_dict(validation.context),
        "warnings": validation.warnings,
        "instruction_for_llm": (
            f"Validation passed for alias {config.alias!r}. "
            f"You may now run the optimizer without --validate-only."
        ),
    }


def _resolve_required_regions(
    args,
    config: OrgConfig,
    validation,
    max_window_start,
    requested_end,
    exclude_filter,
    available_tag_columns: frozenset[str],
) -> tuple[set[str], list[tuple[str, float]], bool]:
    """Return (required_regions_for_cache, discovered, discovery_failed)."""
    discovered: list[tuple[str, float]] = []
    discovery_failed = False

    only_region_set: set[str] = set()
    if getattr(args, "only_regions", None):
        only_region_set = {r.strip() for r in args.only_regions.split(",") if r.strip()}

    if only_region_set:
        required_regions_for_cache = only_region_set | {validation.context.primary_region}
        return required_regions_for_cache, discovered, discovery_failed

    try:
        discovered = cur_query.discover_workload_regions(
            config=config,
            window_start=max_window_start,
            window_end=requested_end,
            exclude_filter=exclude_filter,
            available_tag_columns=available_tag_columns,
        )
    except Exception as e:  # noqa: BLE001 — degrade gracefully, surface via warning
        logger.warning("Region discovery failed: %s", e)
        discovery_failed = True

    additional_region_set: set[str] = set()
    if getattr(args, "additional_regions", None):
        additional_region_set = {
            r.strip() for r in args.additional_regions.split(",") if r.strip()
        }

    discovered_regions_set = {r for r, _ in discovered}
    required_regions_for_cache = (
        discovered_regions_set | {validation.context.primary_region} | additional_region_set
    )
    return required_regions_for_cache, discovered, discovery_failed


def _resolve_skill_git_root() -> str:
    """Absolute repo root hosting this skill, so cache-refresh commands don't
    emit the unexecutable `<skill_git_root>` literal. scripts/aws_sp_optimizer.py
    → scripts/ → aws-sp-optimizer/ → skills/ → repo root.
    """
    from pathlib import Path
    return str(Path(__file__).resolve().parents[3])


def _build_cache_refresh_actions(org_alias: str) -> list[dict]:
    """Build the three-action payload for NeedsCacheRefreshError responses."""
    skill_git_root = _resolve_skill_git_root()
    return [
        {
            "key": "pull_from_git",
            "label": "从 git 拉最新 shipped data（别人可能已经刷新过）",
            "command": (
                # Quote the path so spaces / non-ASCII chars don't break `cd`
                # on users whose repo lives under e.g. /Users/张三/claude-home.
                f'cd "{skill_git_root}" && git fetch origin main && '
                "git checkout origin/main -- skills/aws-sp-optimizer/data"
            ),
        },
        {
            "key": "rebuild_locally",
            "label": "本地重建并提交（首次触发者做）",
            "command": f"python -m scripts.aws_sp_optimizer --org {org_alias} --refresh-cache",
        },
        {
            "key": "skip_refresh_once",
            "label": "本次忽略继续跑（不推荐）",
            "command": (
                f"python -m scripts.aws_sp_optimizer --org {org_alias} --skip-pricing-refresh"
            ),
        },
    ]


def _compute_edp_or_error(
    config: OrgConfig,
    max_window_start,
    requested_end,
    exclude_filter,
    available_tag_columns: frozenset[str],
):
    """Return (e_sp, e_od, edp_audit) or a dict output on no-data error."""
    try:
        return cur_query.compute_edp_factors(
            config,
            max_window_start,
            requested_end,
            exclude_filter=exclude_filter,
            available_tag_columns=available_tag_columns,
        )
    except NoDataError as exc:
        if exc.code == "no_od_rows_for_edp":
            return {
                **base_output(),
                "status": "aws_api_error",
                "phase": "cur_query",
                "error_code": exc.code,
                "error_message": (
                    "No EC2/Fargate/Lambda usage rows found in the analysis window — "
                    "cannot compute EDP discount factors. "
                    "Verify the CUR table covers the requested date range."
                ),
                "context": exc.context,
                "instruction_for_llm": (
                    "The CUR table returned no Usage-type rows for the window. "
                    "Check that the CUR table name and date range are correct."
                ),
                "user_fix_options": [
                    "Verify CUR table name and database in orgs.yaml",
                    "Check that the window end date is within the CUR data range",
                    "Confirm the AWS account has EC2/Fargate/Lambda activity",
                ],
            }
        raise  # unexpected NoDataError code — let main() catch it


def run_optimizer(args: argparse.Namespace) -> dict:
    """Full pipeline from CLI args to final JSON output dict. See spec 5.10."""
    # STEP 1: Load config with CLI overrides
    config: OrgConfig = config_loader.load_config(
        args.org_alias,
        cli_overrides={
            "window_days": args.window_days,
            "window_end": args.window_end,
            "prefer": args.prefer,
            "profile": args.profile,
            "cur_database": args.cur_database,
            "cur_table": args.cur_table,
            "athena_output": args.athena_output,
            "athena_workgroup": args.athena_workgroup,
        },
    )

    # STEP 2: Validate environment
    validation = validate_environment(config)

    if args.validate_only:
        return _validate_only_output(config, validation)

    if validation.blockers:
        raise ValidationBlockerError(
            blockers=validation.blockers,
            validation_context=validation.context,
            warnings=validation.warnings,
        )

    # Narrow: validate_environment guarantees context is non-None when no blockers
    assert validation.context is not None

    # STEP 2.5: Resolve effective filter (config ∪ CLI), discover tag columns once
    effective_filter = merge_cli_into_config_filter(config.exclude, args)
    available_tag_columns = (
        fetch_available_tag_columns(config)
        if effective_filter.tag_exclusions
        else frozenset()
    )

    # STEP 3: Compute window bounds + fetch SPs
    requested_end = parse_window_end(config.window_end)
    requested_window = Window(end=requested_end, days=config.window_days)
    max_window_start = requested_end - timedelta(days=14 + 90)

    all_relevant_sps = fetch_all_relevant_sps(
        profile=config.profile, max_window_start=max_window_start
    )

    # STEP 3.5: Determine the region set for pricing cache
    required_regions_for_cache, discovered, discovery_failed = _resolve_required_regions(
        args, config, validation, max_window_start, requested_end,
        effective_filter, available_tag_columns,
    )

    # Inject discovery warnings directly into validation.warnings (mutable list)
    if discovery_failed:
        validation.warnings.append(
            make_warning(
                "region_discovery_failed_fallback_to_primary",
                context={"primary_region": validation.context.primary_region},
            )
        )
    if len(required_regions_for_cache) >= 4:
        validation.warnings.append(
            make_warning(
                "region_discovery_many_regions_slow_cold_start",
                context={
                    "region_count": len(required_regions_for_cache),
                    "estimated_cold_start_seconds": len(required_regions_for_cache) * 300,
                    "regions": sorted(required_regions_for_cache),
                },
            )
        )

    discovered_regions_struct = [
        {"region": r, "cost_share_pct": pct} for r, pct in discovered
    ]

    # STEP 4: Pricing cache — now with multi-region support
    # Build the three-action payload once; reused by NeedsCacheRefreshError
    # handler here AND by the post-newsvendor coverage gate below.
    _cache_refresh_actions = _build_cache_refresh_actions(args.org_alias)

    try:
        ratios = pricing_cache.ensure_cache_fresh(
            required_regions=required_regions_for_cache,
            primary_region=validation.context.primary_region,
            refresh_cache=args.refresh_cache,
            skip_refresh=args.skip_pricing_refresh,
        )
    except NeedsCacheRefreshError as exc:
        return {
            **base_output(),
            "status": "needs_cache_refresh",
            "reason": exc.code,
            "reason_detail": exc.message,
            "context": exc.context,
            "actions": _cache_refresh_actions,
            "instruction_for_llm": (
                "Do NOT proceed to recommendation. Present actions[] to user, await explicit "
                "choice, then execute the chosen command. See references/cache-refresh-handling.md."
            ),
        }

    pricing_cache_meta = PricingCacheMeta(
        version=ratios.version,
        age_hours=(datetime.now(timezone.utc) - ratios.cached_at).total_seconds() / 3600,
        built_at=ratios.cached_at,
        regions=list(ratios.ec2.keys()),
    )

    # STEP 4.5: Compute EDP factors — needed before build_hourly_series (e_sp)
    # and before compute_optimal_commit (e_sp, e_od).
    edp_result = _compute_edp_or_error(
        config, max_window_start, requested_end,
        effective_filter, available_tag_columns,
    )
    if isinstance(edp_result, dict):
        return edp_result
    e_sp, e_od, edp_audit = edp_result

    # Dirty-data gate: EDP factors must be in [0, 0.99)
    if e_sp >= 0.99 or e_od >= 0.99 or e_sp < 0 or e_od < 0:
        return {
            **base_output(),
            "status": "needs_human_decision",
            "error_code": "edp_anomaly",
            "error_message": (
                f"EDP factors out of range: e_sp={e_sp:.4f}, e_od={e_od:.4f}"
            ),
            "context": edp_audit,
            "failure_dossier": {
                "reason_summary": "edp_anomaly",
                "rejection_summary": edp_audit,
            },
            "instruction_for_llm": (
                "READ references/failure-handling.md 'EDP anomaly' section."
            ),
        }

    # STEP 5: CUR pull — pass real e_sp from EDP computation
    series = cur_query.build_hourly_series(
        config=config,
        window_start=max_window_start,
        window_end=requested_end,
        e_sp=e_sp,
        exclude_filter=effective_filter,
        available_tag_columns=available_tag_columns,
    )

    # STEP 6: Window search
    search = search_best_window(
        series=series,
        requested=requested_window,
        all_relevant_sps=all_relevant_sps,
        prefer=config.prefer,
    )
    # Narrow: validation.context is non-None (asserted above; re-stated for the
    # failed-search block which branches before the post-search assert below)
    if search.status == "failed":
        # v1 DELIBERATELY emits a minimal failure dossier. The spec section
        # 8.2 FailureDossier defines four richer fields: trend_curve_downsampled,
        # trend_aware_fallback, decision_options_for_llm, and
        # data_for_root_cause_investigation. Populating those requires:
        #   - a rolling 7-day downsample over the full MAX window
        #   - a fallback trend_aware run on a default (now, 90d) diagnostic window
        #   - two extra Athena queries for top-10 services by decline and cost
        # That's ~150 lines of bespoke glue logic per spec 10.5 known debts.
        # v1 ships the minimal dossier; v2 can expand via a dedicated
        # build_failure_dossier_output helper in output_builder.py.
        # References/failure-handling.md documents the v1 experience for LLMs.
        return {
            **base_output(),
            "status": "needs_human_decision",
            "context": {
                "org_alias": config.alias,
                "account_id": validation.context.caller_account_id,
            },
            "failure_dossier": {
                "reason_summary": search.failure_reason or "no_viable_window",
                "rejection_summary": search.rejection_summary,
            },
            "risks": [],
            "warnings": validation.warnings,
        }

    # STEP 7: Formula — pass e_sp and e_od from EDP computation
    # Narrow: search.chosen is non-None when status != 'failed'
    assert search.chosen is not None

    # STEP 6.5: Compute SP coverage share for filter-aware C_existing_effective attenuation.
    # Window intentionally matches max_window_start (up to ~104d, not 30d):
    # share is a retention *ratio*, needs enough history to be stable, and
    # reuses the CUR window already pulled by the baseline. utilization_30d
    # is a distinct steady-state rate from a different API; do not conflate.
    # See references/usage.md "Effect on C_existing_effective".
    sp_coverage_share, sp_share_audit = compute_sp_coverage_share(
        config, max_window_start, requested_end,
        effective_filter, available_tag_columns,
    )

    final = compute_optimal_commit(
        profile=config.profile,
        series=series,
        ratios=ratios,
        all_relevant_sps=all_relevant_sps,
        chosen=search.chosen,
        e_sp=e_sp,
        e_od=e_od,
        sp_coverage_share=sp_coverage_share,
    )

    # Post-newsvendor coverage gate: refresh if coverage too low to trust d
    if final.d_audit.coverage_pct < 0.95:
        return {
            **base_output(),
            "status": "needs_cache_refresh",
            "reason": "coverage_below_threshold",
            "reason_detail": (
                f"coverage={final.d_audit.coverage_pct:.4f}; threshold=0.95"
            ),
            "context": {
                "unmatched_top10": final.d_audit.unmatched_top10_by_cost,
            },
            "actions": _cache_refresh_actions,
            "instruction_for_llm": (
                "Pricing cache coverage is below 95% — too many CUR rows could not be matched. "
                "Present actions[] to user and await explicit choice before retrying. "
                "See references/cache-refresh-handling.md."
            ),
        }

    # d_calibrated dirty-data gate
    if final.d_audit.d_calibrated <= 0:
        return {
            **base_output(),
            "status": "needs_human_decision",
            "error_code": "d_calibrated_non_positive",
            "error_message": (
                "Calibrated discount ≤ 0 — SP would be more expensive than OD under your PPA"
            ),
            "context": {
                "d_calibrated": final.d_audit.d_calibrated,
                "d_raw": final.d_audit.d_raw,
            },
            "failure_dossier": {
                "reason_summary": "d_calibrated_non_positive",
                "rejection_summary": {
                    "d_calibrated": final.d_audit.d_calibrated,
                    "d_raw": final.d_audit.d_raw,
                },
            },
        }

    # STEP 8: Build output
    output = build_output(
        config=config,
        validation=validation,
        final=final,
        search=search,
        all_relevant_sps=all_relevant_sps,
        requested_window=requested_window,
        pricing_cache_meta=pricing_cache_meta,
        discovered_regions=discovered_regions_struct,
        exclude_filter=effective_filter,
        available_tag_columns=available_tag_columns,
        sp_coverage_share=sp_coverage_share,
        sp_share_audit=sp_share_audit,
    )
    validate_output_invariants(output)
    return output


def setup_logging(verbose: bool = False, log_file: str | None = None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    # Remove any handlers set by earlier calls
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = "%(asctime)s  %(levelname)s  %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)
    if log_file:
        handler: logging.Handler = logging.FileHandler(log_file)
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root.addHandler(handler)


def main() -> None:
    import requests.exceptions
    from botocore.exceptions import BotoCoreError, ClientError

    args = parse_args()
    setup_logging(verbose=args.verbose, log_file=args.log_file)

    result = None
    try:
        result = run_optimizer(args)
        output = result
    except SPOptimizerError as e:
        output = {**base_output(), **e.to_output_dict()}
    except (ClientError, BotoCoreError) as e:
        if isinstance(e, ClientError):
            code = e.response.get("Error", {}).get("Code", "")
        else:
            code = type(e).__name__
        output = {
            **base_output(),
            "status": "aws_api_error",
            "phase": "post_validation",
            "error_code": code or "unknown_aws_error",
            "error_message": str(e),
            "context": {},
            "llm_next_action": "READ references/troubleshooting.md section 'aws_error'",
            "user_fix_options": [
                "Check profile permissions for the failing operation",
                "Re-authenticate if SSO session expired (NoCredentialsError)",
                "Check network connectivity (EndpointConnectionError)",
                "Inspect stderr for the operation that triggered this error",
            ],
        }
    except requests.exceptions.RequestException as e:
        output = {
            **base_output(),
            "status": "aws_api_error",
            "phase": "pricing_cache",
            "error_code": type(e).__name__,
            "error_message": str(e),
            "context": {},
            "llm_next_action": "READ references/troubleshooting.md section 'pricing_api_unreachable'",
            "user_fix_options": [
                "Check internet connectivity to pricing.us-east-1.amazonaws.com",
                "Retry the run; AWS pricing API is usually highly available",
                "Use --skip-pricing-refresh if offline (requires existing cache)",
            ],
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("Uncaught exception")
        output = {
            **base_output(),
            "status": "script_bug",
            "error_type": type(e).__name__,
            "error_message": str(e),
            "traceback_hint": "See stderr for full traceback",
            "llm_next_action": (
                "This is a script bug, not a user error. "
                "Collect stderr and file an issue at the skill repo."
            ),
        }
        print(json.dumps(output), flush=True)
        sys.exit(1)

    # Final invariant check with fallback
    try:
        validate_output_invariants(output)
    except AssertionError as e:
        logger.error("Output invariant violation: %s", e)
        output = {
            **base_output(),
            "status": "script_bug",
            "error_type": "InvariantViolation",
            "error_message": str(e),
            "traceback_hint": "The output that failed the invariant is logged to stderr",
            "llm_next_action": (
                "This is a script bug — the output produced by run_optimizer "
                "violated a schema invariant. Collect stderr and file an issue."
            ),
        }

    print(json.dumps(output), flush=True)

    # Exit code contract (3.4)
    status = output["status"]
    if status in {"ok", "ok_with_adjustment", "validation_ok", "discovery_result"}:
        sys.exit(0)
    elif status in {
        "needs_setup",
        "needs_validation_fix",
        "needs_human_decision",
        "needs_cache_refresh",
        "validation_failed",
    }:
        sys.exit(2)
    elif status == "aws_api_error":
        sys.exit(3)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
