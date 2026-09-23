"""Phase A environment validation — 4 startup blockers. See spec 6.2."""

from __future__ import annotations

from botocore.exceptions import ClientError, NoCredentialsError

from scripts._common import (
    Blocker,
    OrgConfig,
    ValidationContext,
    ValidationResult,
    athena_execute,
    aws_error_to_blocker,
    glue_get_table,
    organizations_describe_organization,
    organizations_list_accounts,
    sts_get_caller_identity,
)
from scripts.output_builder import make_warning


REQUIRED_CUR_COLUMNS = {
    "line_item_usage_start_date",
    "line_item_line_item_type",
    "line_item_product_code",
    "line_item_usage_type",
    "line_item_operation",
    "line_item_usage_account_id",
    "line_item_usage_amount",
    "line_item_resource_id",
    "pricing_public_on_demand_cost",
    "pricing_term",
    "product_instance_type",
    "product_region_code",
}


def _check_caller_identity(config: OrgConfig) -> tuple[object | None, Blocker | None]:
    """Check 1: STS + payer account match. Returns (caller, blocker)."""
    try:
        caller = sts_get_caller_identity(config.profile)
    except NoCredentialsError as e:
        return None, Blocker(
            code="profile_auth_failed",
            message=f"AWS profile '{config.profile}' has no credentials: {e}",
            context={"profile": config.profile, "error": str(e)},
            llm_next_action="READ references/troubleshooting.md section 'aws_auth'",
            user_fix_options=[
                "Run 'aws sso login --profile <profile>' if using SSO",
                "Verify ~/.aws/credentials and ~/.aws/config",
                "Check that role/SSO session hasn't expired",
            ],
        )
    except ClientError as e:
        return None, aws_error_to_blocker(e, "sts:GetCallerIdentity", config.profile)

    if caller.account_id != config.payer_account_id:
        return caller, Blocker(
            code="account_id_mismatch",
            message=(
                f"Profile '{config.profile}' resolved to account {caller.account_id}, "
                f"but config expects payer account {config.payer_account_id}."
            ),
            context={
                "profile": config.profile,
                "actual_account": caller.account_id,
                "expected_account": config.payer_account_id,
            },
            llm_next_action=(
                "Ask user: temporary SSO drift (re-login) or config needs update? "
                "Do NOT auto-fix without consent."
            ),
            user_fix_options=[
                "If SSO drift: re-authenticate with correct account",
                "If config outdated: edit ~/.config/aws-sp-optimizer/orgs.yaml",
            ],
        )
    return caller, None


def _check_org_master(config: OrgConfig, caller) -> tuple[object | None, Blocker | None]:
    """Check 2: Org membership + payer is master."""
    try:
        org = organizations_describe_organization(config.profile)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "AWSOrganizationsNotInUseException":
            return None, Blocker(
                code="not_in_organization",
                message=f"Account {caller.account_id} is not part of an AWS Organization",
                context={"account_id": caller.account_id},
                llm_next_action=(
                    "Tell user this skill requires an Org; standalone account not supported"
                ),
                user_fix_options=[],
            )
        return None, aws_error_to_blocker(
            e, "organizations:DescribeOrganization", config.profile
        )

    if org.id != config.org_id:
        return org, Blocker(
            code="org_id_mismatch",
            message=f"Profile's Org is {org.id} but config expects {config.org_id}",
            context={"actual_org_id": org.id, "expected_org_id": config.org_id},
            llm_next_action="Ask user: has Org been recreated? Re-run first-run-setup.md",
            user_fix_options=["Verify org_id in ~/.config/aws-sp-optimizer/orgs.yaml"],
        )

    if caller.account_id != org.master_account_id:
        return org, Blocker(
            code="not_payer",
            message=(
                f"Account {caller.account_id} is a member (not master) of Org {org.id}. "
                f"Org master is {org.master_account_id}."
            ),
            context={
                "member_account": caller.account_id,
                "master_account": org.master_account_id,
            },
            llm_next_action=(
                f"Tell user to switch to profile authenticating as account {org.master_account_id}"
            ),
            user_fix_options=[],
        )
    return org, None


def _check_cur_schema(config: OrgConfig, result: ValidationResult) -> Blocker | None:
    """Check 3: CUR table exists, has required columns. Side-effects result.warnings
    and result.context.cur_table_has_resource_tags_user_service."""
    try:
        table = glue_get_table(config.profile, config.cur_database, config.cur_table)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "EntityNotFoundException":
            return Blocker(
                code="cur_table_not_found",
                message=f"CUR table {config.cur_database}.{config.cur_table} does not exist",
                context={
                    "profile": config.profile,
                    "database": config.cur_database,
                    "table": config.cur_table,
                },
                llm_next_action="READ references/troubleshooting.md section 'cur_not_found'",
                user_fix_options=[
                    "Verify cur_database and cur_table in config",
                    "Confirm CUR is configured in this account",
                ],
            )
        return aws_error_to_blocker(e, "glue:GetTable", config.profile)

    actual_cols = {c.name for c in table.columns}
    missing = REQUIRED_CUR_COLUMNS - actual_cols
    blocker = None
    if missing:
        blocker = Blocker(
            code="cur_schema_incomplete",
            message=f"CUR table missing required columns: {sorted(missing)}",
            context={
                "table": f"{config.cur_database}.{config.cur_table}",
                "missing": sorted(missing),
            },
            llm_next_action="READ references/troubleshooting.md section 'cur_schema'",
            user_fix_options=[
                "Reconfigure CUR with 'Include resource IDs' enabled",
                "Check if CUR is Legacy (v1) vs CUR 2.0 — this skill requires v1 schema",
            ],
        )

    has_service_tag = "resource_tags_user_service" in actual_cols
    result.context.cur_table_has_resource_tags_user_service = has_service_tag
    if not has_service_tag:
        result.warnings.append(
            make_warning(
                "resource_tags_user_service_missing",
                context={"table": f"{config.cur_database}.{config.cur_table}"},
            )
        )
    return blocker


def _check_athena_coverage(config: OrgConfig, context: ValidationContext) -> Blocker | None:
    """Check 4: Athena probe + Org-wide CUR coverage."""
    if not context.member_account_ids:
        return Blocker(
            code="org_has_no_active_members",
            message=f"Org {context.org_id} has no ACTIVE member accounts",
            context={"org_id": context.org_id},
            llm_next_action="Tell user to verify the Org state",
            user_fix_options=[],
        )

    from datetime import datetime as _dt
    from datetime import timezone as _tz

    _now = _dt.now(_tz.utc)
    # Probe current + previous month to ensure ≥30 days of data and avoid
    # month-start/delayed-billing false positives. CUR partitions are
    # year/month so we OR two windows.
    _cur_y, _cur_m = _now.year, _now.month
    _prev_m = 12 if _cur_m == 1 else _cur_m - 1
    _prev_y = _cur_y - 1 if _cur_m == 1 else _cur_y
    distinct_accounts_query = f"""
        SELECT DISTINCT line_item_usage_account_id
        FROM "{config.cur_database}"."{config.cur_table}"
        WHERE (
                (year = '{_cur_y}' AND month IN ('{_cur_m}','{_cur_m:02d}'))
             OR (year = '{_prev_y}' AND month IN ('{_prev_m}','{_prev_m:02d}'))
              )
    """
    try:
        rows = athena_execute(config, distinct_accounts_query)
        cur_account_ids = {r["line_item_usage_account_id"] for r in rows}
    except ClientError as e:
        return aws_error_to_blocker(e, "athena:StartQueryExecution", config.profile)
    except Exception as e:  # AthenaError from wrapper
        return Blocker(
            code="athena_probe_failed",
            message=f"Athena probe query failed: {e}",
            context={"error": str(e)},
            llm_next_action="READ references/troubleshooting.md section 'athena_error'",
            user_fix_options=[
                "Verify athena_output S3 path is writable",
                "Confirm Athena workgroup exists",
            ],
        )

    member_set = set(context.member_account_ids)
    coverage = len(cur_account_ids & member_set) / len(member_set)
    if coverage < 0.5:
        return Blocker(
            code="cur_not_org_wide",
            message=(
                f"CUR contains {len(cur_account_ids)} accounts but Org has "
                f"{len(member_set)} ACTIVE members ({coverage:.0%} coverage). "
                f"CUR must be configured at Org management scope."
            ),
            context={
                "cur_accounts": sorted(cur_account_ids),
                "missing_accounts": sorted(member_set - cur_account_ids)[:20],
                "coverage_pct": round(coverage, 4),
            },
            llm_next_action=(
                "Explain to user: CUR needs to be recreated with 'Include all Org "
                "member data' option in Billing Console"
            ),
            user_fix_options=[
                "Recreate CUR with Org-wide scope via AWS Console → Billing → Cost and Usage Reports"
            ],
        )
    return None


def validate_environment(config: OrgConfig) -> ValidationResult:
    """Run startup blockers. Accumulates where possible, early-exits on hard prereqs."""
    result = ValidationResult()

    caller, blocker = _check_caller_identity(config)
    if blocker:
        result.blockers.append(blocker)
        return result

    org, blocker = _check_org_master(config, caller)
    if blocker:
        result.blockers.append(blocker)
        return result

    all_accounts = organizations_list_accounts(config.profile)
    member_ids = [a.id for a in all_accounts if a.status == "ACTIVE"]
    result.context = ValidationContext(
        caller_account_id=caller.account_id,
        org_id=org.id,
        master_account_email=org.master_account_email,
        member_account_ids=member_ids,
        member_accounts_count=len(member_ids),
        primary_region=config.primary_region or "us-east-1",
        cur_table_has_resource_tags_user_service=None,
    )

    blocker = _check_cur_schema(config, result)
    if blocker:
        result.blockers.append(blocker)
        return result

    blocker = _check_athena_coverage(config, result.context)
    if blocker:
        result.blockers.append(blocker)

    return result
