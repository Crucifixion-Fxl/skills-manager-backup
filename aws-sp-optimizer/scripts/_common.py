"""Shared utilities: dataclasses, exception classes, AWS wrappers, small helpers.

Populated across Phase 1 (dataclasses, exceptions) and Phase 3 (AWS wrappers).
See spec 5.1b, 5.9, 5.9b, 5.9c, 9.4.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import cached_property

_logger = logging.getLogger(__name__)
from typing import NamedTuple, TYPE_CHECKING

from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from scripts.exclude_filter import ExcludeFilter


def _default_exclude_filter():
    """Lazy factory for OrgConfig.exclude. Late import avoids circular dep
    between scripts._common and scripts.exclude_filter."""
    from scripts.exclude_filter import ExcludeFilter
    return ExcludeFilter()


def _unpickle_sp_optimizer_error(cls, code, message, context, llm_next_action, user_fix_options):
    """Module-level helper so pickle can reconstruct SPOptimizerError subclasses.

    Must live at module level (not nested) so pickle can locate it by qualified name.
    """
    return cls(
        code=code,
        message=message,
        context=context,
        llm_next_action=llm_next_action,
        user_fix_options=user_fix_options,
    )


class SPOptimizerError(Exception):
    """Base exception with structured payload for JSON output.

    Module home: _common.py. All subclasses live here so every module can
    `from scripts._common import ...` without cycling through aws_sp_optimizer.py.

    Subclasses set `status` and `phase` as class attributes; instance-level
    `code`, `message`, `context`, `llm_next_action`, `user_fix_options`
    carry the structured detail.
    """

    status: str = "aws_api_error"
    phase: str = "unknown"

    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: dict | None = None,
        llm_next_action: str = "",
        user_fix_options: list | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context or {}
        self.llm_next_action = llm_next_action
        self.user_fix_options = user_fix_options or []

    def __reduce__(self) -> tuple:
        """Enable pickle round-trip (needed for ProcessPoolExecutor error propagation).

        Python's default Exception.__reduce__ passes args=(message,) to __init__,
        but our __init__ requires (code, message, ...). We use _unpickle_sp_optimizer_error
        as the reconstructor to pass keyword arguments directly.
        """
        return (
            _unpickle_sp_optimizer_error,
            (
                self.__class__,
                self.code,
                self.message,
                self.context,
                self.llm_next_action,
                self.user_fix_options,
            ),
        )

    def to_output_dict(self) -> dict:
        # Emits BOTH `llm_next_action` (legacy / internal name) AND
        # `instruction_for_llm` (the public schema field checked by
        # INV-8 / INV-11 / INV-12 / INV-13). Without this alias,
        # needs_setup / needs_validation_fix / validation_* / discovery_result
        # outputs built from SPOptimizerError would fail the invariant check
        # and get rewritten to script_bug in main()'s fallback.
        #
        # `instruction_for_llm` is ALWAYS non-empty even if llm_next_action
        # wasn't provided — we synthesize from code+message so INV asserts
        # (which reject empty strings via truthy check) always pass.
        instruction = (
            self.llm_next_action
            or f"Error {self.code}: {self.message}. "
            f"See references/troubleshooting.md for the {self.code} section."
        )
        return {
            "status": self.status,
            "phase": self.phase,
            "error_code": self.code,
            "error_message": self.message,
            "context": self.context,
            "llm_next_action": self.llm_next_action,
            "instruction_for_llm": instruction,
            "user_fix_options": self.user_fix_options,
        }


class ConfigError(SPOptimizerError):
    status = "needs_setup"
    phase = "config"


class CacheError(SPOptimizerError):
    status = "aws_api_error"
    phase = "pricing_cache"


class AthenaError(SPOptimizerError):
    status = "aws_api_error"
    phase = "cur_query"


class NoDataError(SPOptimizerError):
    """Raised when CUR returns insufficient or unmatched data for the formula.

    Maps to aws_api_error for v1 (see spec 9.4 for rationale).
    """

    status = "aws_api_error"
    phase = "cur_query"


class NeedsCacheRefreshError(SPOptimizerError):
    """Raised when shipped pricing cache is stale / missing regions / coverage-degraded.

    The orchestrator converts this to status='needs_cache_refresh' with a three-option
    actions[] payload. Do NOT auto-rebuild in the skill flow — the user must pick an action.
    """

    status = "needs_cache_refresh"
    phase = "pricing_cache"


class ValidationBlockerError(SPOptimizerError):
    """Raised when validate_environment returns blockers.

    Serializes to NeedsValidationFixOutput (spec 8.1). Overrides to_output_dict
    because the NeedsValidationFixOutput schema is structurally different from
    the base SPOptimizerError payload — it has `blockers[]` and
    `validation_context` instead of `error_code` / `error_message`.

    At the time Task 1.1 runs, `Blocker` and `ValidationContext` dataclasses
    don't exist yet (added in Task 1.2). The override below uses duck-typing
    and only calls `.to_dict()` / attribute access at runtime — when
    ValidationBlockerError is actually raised (Phase 3+), those types are
    already defined.
    """

    status = "needs_validation_fix"
    phase = "validation"

    def __init__(
        self, blockers: list, validation_context: ValidationContext | None, warnings: list
    ):
        super().__init__(
            code="validation_failed",
            message=f"{len(blockers)} validation blocker(s) — run cannot proceed",
        )
        self.blockers = blockers
        self.validation_context = validation_context
        self.warnings_list = warnings

    def to_output_dict(self) -> dict:
        # validation_context_to_public_dict is defined later in this same
        # module (Task 1.4). At method-call time the module namespace is
        # fully populated, so the direct reference resolves. No import needed.
        return {
            "status": self.status,
            "blockers": [b.to_dict() for b in self.blockers],
            "validation_context": validation_context_to_public_dict(self.validation_context),
            "warnings": self.warnings_list,
            "instruction_for_llm": (
                f"{len(self.blockers)} validation blocker(s) found. Inspect each "
                f"entry in blockers[] and follow its llm_next_action + "
                f"user_fix_options. Do NOT proceed until all blockers are resolved."
            ),
        }


@dataclass
class OrgConfig:
    """In-memory representation of one orgs.yaml entry after loading and
    CLI-override merging. See spec 4.1 / 5.1b."""

    alias: str
    description: str | None
    profile: str
    payer_account_id: str
    org_id: str
    cur_database: str
    cur_table: str
    athena_output: str  # must end with '/'
    athena_workgroup: str  # default 'primary'
    primary_region: str | None
    window_days: int
    window_end: str | datetime  # 'today' literal or datetime
    prefer: str  # freshness|sample-size|balanced
    exclude: "ExcludeFilter" = field(default_factory=_default_exclude_filter)


@dataclass
class Blocker:
    """One validation failure. See spec 5.1b / 6.2."""

    code: str
    severity: str = "blocker"
    message: str = ""
    context: dict = field(default_factory=dict)
    llm_next_action: str = ""
    user_fix_options: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "context": self.context,
            "llm_next_action": self.llm_next_action,
            "user_fix_options": self.user_fix_options,
        }


@dataclass
class ValidationContext:
    """Metadata extracted during validate_environment. See spec 5.1b."""

    caller_account_id: str
    org_id: str
    master_account_email: str
    member_account_ids: list[str]
    member_accounts_count: int
    primary_region: str
    cur_table_has_resource_tags_user_service: bool | None


@dataclass
class ValidationResult:
    """Return type for validate_environment. See spec 5.1b / 6.2."""

    blockers: list[Blocker] = field(default_factory=list)
    warnings: list = field(default_factory=list)
    context: ValidationContext | None = None


@dataclass
class Window:
    """Analytical window [start, end). See spec 5.1b."""

    end: datetime  # exclusive end
    days: int  # 60..90

    @cached_property
    def start(self) -> datetime:
        return self.end - timedelta(days=self.days)

    @cached_property
    def hours(self) -> list[datetime]:
        """Materialized once per Window instance; window_search iterates
        thousands of times over this list so caching pays off."""
        n_hours = self.days * 24
        return [self.start + timedelta(hours=i) for i in range(n_hours)]

    @property
    def num_hours(self) -> int:
        return self.days * 24


@dataclass
class HourlySeries:
    """Hourly gross OD-equivalent cost + mix key distribution. See spec 5.1.

    data[h] schema:
        "total_list_usd":  float  — Σ pricing_public_on_demand_cost for the hour
        "total_net_usd":   float  — real billed OD for the hour (post-EDP;
                                    SP-covered time reconstructed via
                                    sp_effective_cost × (1 - e_sp))
        "mix":             dict   — {mix_key: gross_list_cost}  list units, unchanged
    """

    hours: list[datetime]
    data: dict[datetime, dict]  # per-hour {total_list_usd, total_net_usd, mix}
    window_start: datetime
    window_end: datetime


def parse_window_end(value: str | datetime) -> datetime:
    """Normalize OrgConfig.window_end into a tz-aware UTC datetime.

    Accepts:
      - literal string 'today'  -> datetime.now(tz=UTC) truncated to hour
      - 'YYYY-MM-DD' string     -> midnight UTC that day
      - already-datetime         -> passthrough (adding UTC tzinfo if naive)

    Raises ConfigError on any other format with a friendly message.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if value == "today":
        now = datetime.now(timezone.utc)
        return now.replace(minute=0, second=0, microsecond=0)
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError) as err:
        raise ConfigError(
            code="config_invalid",
            message=(
                f"window_end value {value!r} is not valid. Use either the "
                f"literal 'today' or a YYYY-MM-DD date string."
            ),
            llm_next_action="Ask user to fix window_end in orgs.yaml or via --window-end CLI",
            user_fix_options=[
                "Set window_end: today (literal)",
                "Set window_end: 2026-04-13 (YYYY-MM-DD format)",
            ],
        ) from err
    return dt.replace(tzinfo=timezone.utc)


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string to a tz-aware UTC datetime. Normalize 'Z' to
    '+00:00' so Python 3.10's fromisoformat can handle it."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validation_context_to_public_dict(ctx: ValidationContext | None) -> dict | None:
    """Serialize ValidationContext for JSON output, EXCLUDING the
    member_account_ids list (can be 100+ entries in large Orgs).
    Only emits the count.
    """
    if ctx is None:
        return None
    return {
        "caller_account_id": ctx.caller_account_id,
        "org_id": ctx.org_id,
        "master_account_email": ctx.master_account_email,
        "member_accounts_count": ctx.member_accounts_count,
        "primary_region": ctx.primary_region,
        "cur_table_has_resource_tags_user_service": ctx.cur_table_has_resource_tags_user_service,
    }


class CallerIdentity(NamedTuple):
    """Normalized sts.get_caller_identity() response. See spec 5.9b."""

    account_id: str  # from "Account"
    arn: str  # from "Arn"
    user_id: str  # from "UserId"


class Organization(NamedTuple):
    """Normalized organizations.describe_organization() response."""

    id: str  # from "Organization.Id"
    master_account_id: str  # from "Organization.MasterAccountId"
    master_account_email: str  # from "Organization.MasterAccountEmail"


class OrgAccount(NamedTuple):
    """Normalized entry from organizations.list_accounts()."""

    id: str
    name: str
    status: str  # e.g. "ACTIVE" | "SUSPENDED"


def sts_get_caller_identity(profile: str) -> CallerIdentity:
    """Return normalized STS caller identity. Phase 3 fleshes out
    _retry_throttling — for now this is a thin converter. Importable by
    discover_orgs so tests can patch it."""
    import boto3

    client = boto3.Session(profile_name=profile).client("sts")
    resp = client.get_caller_identity()
    return CallerIdentity(
        account_id=resp["Account"],
        arn=resp["Arn"],
        user_id=resp["UserId"],
    )


def organizations_describe_organization(profile: str) -> Organization:
    import boto3

    client = boto3.Session(profile_name=profile).client("organizations")
    resp = client.describe_organization()
    o = resp["Organization"]
    return Organization(
        id=o["Id"],
        master_account_id=o["MasterAccountId"],
        master_account_email=o["MasterAccountEmail"],
    )


def organizations_list_accounts(profile: str) -> list[OrgAccount]:
    import boto3

    client = boto3.Session(profile_name=profile).client("organizations")
    paginator = client.get_paginator("list_accounts")
    accounts: list[OrgAccount] = []
    for page in paginator.paginate():
        for a in page["Accounts"]:
            accounts.append(OrgAccount(id=a["Id"], name=a["Name"], status=a["Status"]))
    return accounts


@dataclass
class RatioEntry:
    """Python representation of one entry in ratios.json. See spec 7.4."""

    sp_rate_usd: float
    od_rate_usd: float
    ratio: float
    discount_pct: float
    sku: str | None = None
    os: str | None = None
    tenancy: str | None = None


@dataclass
class Ratios:
    """In-memory representation of ~/.cache/aws-sp-optimizer/ratios.json."""

    schema_version: int
    term: str
    built_at: datetime
    ec2: dict[str, dict[str, RatioEntry]]  # {region: {"itype|op": RatioEntry}}
    lambda_rates: dict[str, RatioEntry]  # {usage_type: RatioEntry} — global
    fargate: dict[str, dict[str, RatioEntry]]  # {region: {usage_type: RatioEntry}}
    built_from_versions: dict
    primary_region: str

    @property
    def version(self) -> str:
        """SP version of the primary region. Appears in DiscountAudit /
        Context.pricing_cache_version."""
        return str(self.built_from_versions["sp_by_region"][self.primary_region])

    @property
    def cached_at(self) -> datetime:
        """Alias for built_at, matching the cache-meta naming in spec 7.3."""
        return self.built_at

    def lookup(
        self,
        product_code: str,
        region: str,
        instance_type: str | None,
        operation: str,
        usage_type: str,
    ) -> RatioEntry | None:
        """Route to the right sub-table by product_code. See spec 5.1b line 1234."""
        if product_code == "AmazonEC2":
            if instance_type is None:
                return None
            region_table = self.ec2.get(region)
            if region_table is None:
                return None
            return region_table.get(f"{instance_type}|{operation}")
        elif product_code == "AWSLambda":
            return self.lambda_rates.get(usage_type)
        elif product_code in ("AmazonECS", "AmazonEKS"):
            region_table = self.fargate.get(region)
            if region_table is None:
                return None
            return region_table.get(usage_type)
        return None


@dataclass
class RegionVersionInfo:
    """Per-region pricing version info. Mirrors version.json regions[<r>]."""

    sp_version: str
    od_version: str
    sp_refreshed_at: datetime
    od_refreshed_at: datetime
    ratio_entries_count: int


@dataclass
class VersionMetadata:
    """In-memory representation of ~/.cache/aws-sp-optimizer/version.json.

    Used inside pricing_cache.ensure_cache_fresh to compare against upstream.
    """

    schema_version: int
    last_refreshed_at: datetime
    aws_sp_optimizer_version: str
    regions: dict[str, RegionVersionInfo]
    lambda_refreshed_at: datetime
    fargate_refreshed_at: datetime


@dataclass
class PricingCacheMeta:
    """Passed to build_output via the orchestrator. Slimmer than VersionMetadata."""

    version: str  # primary region SP version
    age_hours: float
    built_at: datetime
    regions: list[str]


VERSION: str = "0.1.0"

_START_TIME: float = time.time()

_RETRYABLE_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "RequestLimitExceeded",
        "ProvisionedThroughputExceededException",
        "RequestThrottledException",
        "Throttling",
    }
)


def _retry_throttling(call, max_attempts: int = 3):
    """Run `call`. On retryable AWS codes, retry with exponential backoff.
    Non-retryable errors re-raise immediately. See spec 5.9b."""
    for attempt in range(max_attempts):
        try:
            return call()
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in _RETRYABLE_ERROR_CODES and attempt < max_attempts - 1:
                time.sleep(2**attempt)
                continue
            raise


def aws_error_to_blocker(e: ClientError, operation: str, profile: str) -> Blocker:
    """Convert a ClientError caught inside validate_environment into a Blocker.

    Called inline at each AWS wrapper call site so accumulation is preserved.
    """
    code = e.response.get("Error", {}).get("Code", "")
    if code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation"):
        return Blocker(
            code="permissions_missing",
            message=f"Profile '{profile}' lacks permission for {operation}",
            context={"operation": operation, "error_code": code, "aws_message": str(e)},
            llm_next_action="READ references/troubleshooting.md section 'aws_auth'",
            user_fix_options=[
                f"Add {operation} permission to the profile's IAM policy (see Section 4.5)",
            ],
        )
    return Blocker(
        code="unspecified_aws_error",
        message=f"AWS error during {operation}: {code or str(e)}",
        context={"operation": operation, "error_code": code, "aws_message": str(e)},
        llm_next_action="READ references/troubleshooting.md section 'aws_error'",
        user_fix_options=[],
    )


class Column(NamedTuple):
    name: str
    type: str


class TableSchema(NamedTuple):
    name: str
    database: str
    columns: list[Column]


@dataclass
class SPInfo:
    """Slim representation of one SP from AWS. See spec 5.1b."""

    id: str
    type: str  # Compute|EC2Instance|SageMaker
    ec2_instance_family: str | None
    region: str | None
    commitment: float  # $/hr
    start: datetime
    end: datetime
    state: str
    payment_option: str


def glue_get_table(profile: str, database: str, table: str) -> TableSchema:
    import boto3

    client = boto3.Session(profile_name=profile).client("glue")
    resp = _retry_throttling(lambda: client.get_table(DatabaseName=database, Name=table))
    t = resp["Table"]
    cols = [
        Column(name=c["Name"], type=c["Type"])
        for c in t.get("StorageDescriptor", {}).get("Columns", [])
    ]
    return TableSchema(name=t["Name"], database=t.get("DatabaseName", database), columns=cols)


def athena_execute(config, query: str) -> list[dict]:
    """Execute an Athena query and return rows as list[dict] with snake_case keys.

    Uses config.athena_output / config.athena_workgroup / config.cur_database.
    Blocks until completion, polling every 1s for up to 60s.
    Raises AthenaError on query failure or timeout.
    """
    import boto3

    client = boto3.Session(profile_name=config.profile).client("athena")

    start_resp = _retry_throttling(
        lambda: client.start_query_execution(
            QueryString=query,
            QueryExecutionContext={"Database": config.cur_database},
            WorkGroup=config.athena_workgroup,
            ResultConfiguration={"OutputLocation": config.athena_output},
        )
    )
    qid = start_resp["QueryExecutionId"]

    # Poll for up to 60s (spec 9.3 hard limit for the CUR query).
    for _ in range(60):
        status = _retry_throttling(lambda: client.get_query_execution(QueryExecutionId=qid))
        state = status["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise AthenaError(
                code="query_failed",
                message=status["QueryExecution"]["Status"].get("StateChangeReason", state),
                context={"query_id": qid, "state": state},
            )
        time.sleep(1)
    else:
        raise AthenaError(
            code="query_timeout",
            message="Athena query did not complete within 60s",
            context={"query_id": qid},
        )

    # Pull results via paginator
    paginator = client.get_paginator("get_query_results")
    rows: list[dict] = []
    header: list[str] = []
    for page in paginator.paginate(QueryExecutionId=qid):
        data = page["ResultSet"]["Rows"]
        for row in data:
            cells = [c.get("VarCharValue") for c in row["Data"]]
            if not header:
                header = cells  # first row of first page is the header
                continue
            rows.append(dict(zip(header, cells, strict=False)))
    return rows


def savingsplans_describe_savings_plans(profile: str, states: list[str]) -> list[SPInfo]:
    import boto3

    client = boto3.Session(profile_name=profile).client("savingsplans")
    result: list[SPInfo] = []
    next_token: str | None = None
    # describe_savings_plans supports nextToken/maxResults but boto3 does not
    # register it as a paginator, so loop manually.
    while True:
        kwargs: dict = {"states": states, "maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        page = _retry_throttling(lambda k=kwargs: client.describe_savings_plans(**k))
        for sp in page.get("savingsPlans", []):
            result.append(
                SPInfo(
                    id=sp["savingsPlanId"],
                    type=sp["savingsPlanType"],
                    ec2_instance_family=sp.get("ec2InstanceFamily"),
                    region=sp.get("region"),
                    commitment=float(sp["commitment"]),
                    start=_parse_iso(sp["start"]),
                    end=_parse_iso(sp["end"]),
                    state=sp["state"],
                    payment_option=sp["paymentOption"],
                )
            )
        next_token = page.get("nextToken")
        if not next_token:
            break
    return result


def savingsplans_describe_savings_plans_offerings(profile: str, **filters) -> dict:  # type: ignore[type-arg]
    """Returns raw boto3 dict — the only caller is purchase_instructions step 1
    which reads searchResults[0].offeringId directly."""
    import boto3

    client = boto3.Session(profile_name=profile).client("savingsplans")
    result: dict = _retry_throttling(  # type: ignore[assignment]
        lambda: client.describe_savings_plans_offerings(**filters)
    )
    return result


def ce_get_savings_plans_utilization(
    profile: str, start: str, end: str, granularity: str = "DAILY"
) -> dict:  # type: ignore[type-arg]
    """Returns raw boto3 dict. start/end are YYYY-MM-DD strings (see spec 5.7)."""
    import boto3

    client = boto3.Session(profile_name=profile).client("ce")
    result: dict = _retry_throttling(  # type: ignore[assignment]
        lambda: client.get_savings_plans_utilization(
            TimePeriod={"Start": start, "End": end},
            Granularity=granularity,
        )
    )
    return result


def base_output() -> dict:
    """Common fields present in every ScriptOutput object (spec 8.1 OutputBase)."""
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "script_version": VERSION,
        "elapsed_seconds": round(time.time() - _START_TIME, 2),
    }


def generate_partition_filter(start: datetime, end: datetime) -> str:
    """Build the explicit OR-form partition filter for the Athena CUR query.

    CUR v1 partitions may be zero-padded (month='01') or unpadded (month='1')
    depending on how the CUR was created (AWS-provided CF template uses
    unpadded; some users create zero-padded). This filter matches BOTH forms
    so the skill works on any CUR v1 layout.

    Example (start=2025-11-01, end=2026-02-15):
        ((year='2025' AND month IN ('11','11'))
         OR (year='2025' AND month IN ('12','12'))
         OR (year='2026' AND month IN ('1','01'))
         OR (year='2026' AND month IN ('2','02')))
    """
    clauses = []
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    end_month = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    while cursor <= end_month:
        m = cursor.month
        clauses.append(
            f"(year='{cursor.year}' AND month IN ('{m}','{m:02d}'))"
        )
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return "(" + " OR ".join(clauses) + ")"


def compute_untagged_fraction(
    config,  # OrgConfig — typed as object to avoid circular import hints
    validation_context,  # ValidationContext
    chosen_window,  # Window
    exclude_filter=None,  # ExcludeFilter | None — typed as object to avoid circular import hints
    available_tag_columns=None,  # frozenset[str] | None
) -> float:
    """Return fraction of SP-eligible gross cost that is untagged in the window.

    Runs a separate small Athena query — cannot be derived from HourlySeries
    because the mix key doesn't include tag info. See spec 5.9c.

    Returns 0.0 if the CUR table lacks resource_tags_user_service OR on query error.

    When exclude_filter is active, its clauses are applied to the SQL so the
    untagged fraction reflects the retained workload only.
    """
    if not validation_context.cur_table_has_resource_tags_user_service:
        return 0.0

    from scripts.exclude_filter import (  # noqa: E402 — late import avoids circular dep
        ExcludeFilter,
        build_exclude_sql_clauses,
    )

    partition_filter = generate_partition_filter(chosen_window.start, chosen_window.end)
    exclude_filter = exclude_filter or ExcludeFilter()
    available_tag_columns = available_tag_columns or frozenset()
    clauses, _ = build_exclude_sql_clauses(exclude_filter, available_tag_columns)
    exclude_clauses = (
        "\n          AND " + "\n          AND ".join(clauses) if clauses else ""
    )
    # Match cur_query.build_hourly_series's Athena TIMESTAMP format: plain
    # `YYYY-MM-DD HH:MM:SS`, no `T`, no tz suffix. datetime.isoformat() on a
    # tz-aware value emits `T`+offset, which Athena rejects (INVALID_LITERAL)
    # — same bug cur_query.py fixed for its own pull; missing this here made
    # every compute_untagged_fraction call silently error out to 0.0, which
    # then suppressed the untagged_fraction risk (output_builder.py:346).
    start_lit = chosen_window.start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_lit = chosen_window.end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    query = f"""
        SELECT
            SUM(CASE
                WHEN resource_tags_user_service IS NULL
                  OR resource_tags_user_service = ''
                THEN pricing_public_on_demand_cost
                ELSE 0
            END) AS untagged_cost,
            SUM(pricing_public_on_demand_cost) AS total_cost
        FROM "{config.cur_database}"."{config.cur_table}"
        WHERE {partition_filter}
          AND line_item_usage_start_date >= TIMESTAMP '{start_lit}'
          AND line_item_usage_start_date <  TIMESTAMP '{end_lit}'
          AND line_item_line_item_type IN ('Usage', 'SavingsPlanCoveredUsage')
          AND pricing_public_on_demand_cost > 0
          AND (
                (line_item_product_code = 'AmazonEC2'
                 AND line_item_usage_type LIKE '%BoxUsage%'
                 AND line_item_usage_type NOT LIKE '%SpotUsage%')
                OR
                (line_item_product_code = 'AWSLambda'
                 AND line_item_usage_type LIKE '%Lambda-%GB-Second%')
                OR
                (line_item_product_code IN ('AmazonECS', 'AmazonEKS')
                 AND line_item_usage_type LIKE '%Fargate%')
              ){exclude_clauses}
    """
    try:
        rows = athena_execute(config, query)
    except Exception as e:  # noqa: BLE001 — non-fatal by design
        # Log the failure: swallowing it silently (previous behavior) hid
        # Athena-side errors and let the untagged_fraction risk disappear
        # unnoticed. Callers still get 0.0, so output shape is unchanged,
        # but operators now see the Athena error in stderr.
        _logger.warning(
            "compute_untagged_fraction: athena query failed, treating as 0.0 "
            "(risk may be under-reported): %s", e,
        )
        return 0.0
    if not rows:
        return 0.0
    row = rows[0]
    try:
        total = float(row.get("total_cost") or 0)
        untagged = float(row.get("untagged_cost") or 0)
    except (TypeError, ValueError):
        return 0.0
    if total == 0:
        return 0.0
    return untagged / total


_SP_COVERAGE_SHARE_SQL = """
SELECT
    SUM(CASE WHEN {retained_predicate} THEN savings_plan_savings_plan_effective_cost
             ELSE 0 END) AS retained_cost,
    SUM(savings_plan_savings_plan_effective_cost) AS total_cost
FROM "{cur_database}"."{cur_table}"
WHERE {partition_filter}
  AND line_item_usage_start_date >= TIMESTAMP '{start}'
  AND line_item_usage_start_date <  TIMESTAMP '{end}'
  AND line_item_line_item_type = 'SavingsPlanCoveredUsage'
  AND savings_plan_savings_plan_effective_cost > 0
  AND (
        (line_item_product_code = 'AmazonEC2'
         AND line_item_usage_type LIKE '%BoxUsage%'
         AND line_item_usage_type NOT LIKE '%SpotUsage%')
        OR (line_item_product_code = 'AWSLambda'
            AND line_item_usage_type LIKE '%Lambda-%GB-Second%')
        OR (line_item_product_code IN ('AmazonECS', 'AmazonEKS')
            AND line_item_usage_type LIKE '%Fargate%')
      )
"""


def compute_sp_coverage_share(
    config: "OrgConfig",
    window_start: datetime,
    window_end: datetime,
    exclude_filter: "ExcludeFilter",
    available_tag_columns: frozenset[str],
) -> tuple[float, dict]:
    """Fraction of existing SP's effective coverage that applies to retained workload.

    Share is used to attenuate C_existing_effective when the user excludes
    portions of workload their SPs were covering. Examples:
      - Empty filter                 → share = 1.0 (no attenuation)
      - Exclude g4dn consuming 40%   → share ≈ 0.60
      - Zero SP consumption in window → share = 1.0 (no basis to reduce)

    Returns (share, audit). Share is clamped to [0.0, 1.0].
    """
    if exclude_filter.is_empty():
        return 1.0, {
            "retained_cost_usd": None,
            "total_cost_usd": None,
            "note": "filter empty; share short-circuited to 1.0",
        }

    from scripts.exclude_filter import build_exclude_sql_clauses

    clauses, _ = build_exclude_sql_clauses(exclude_filter, available_tag_columns)
    if not clauses:
        return 1.0, {
            "retained_cost_usd": None,
            "total_cost_usd": None,
            "note": "filter emitted no clauses (all skipped); share = 1.0",
        }

    retained_predicate = " AND ".join(clauses)
    partition_filter = generate_partition_filter(window_start, window_end)
    query = _SP_COVERAGE_SHARE_SQL.format(
        retained_predicate=retained_predicate,
        cur_database=config.cur_database,
        cur_table=config.cur_table,
        partition_filter=partition_filter,
        start=window_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        end=window_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    try:
        rows = athena_execute(config, query)
    except Exception as e:  # noqa: BLE001 — non-fatal by design; mirrors compute_untagged_fraction
        return 1.0, {
            "retained_cost_usd": None,
            "total_cost_usd": None,
            "note": f"athena query failed ({type(e).__name__}); share = 1.0 (neutral)",
        }
    if not rows:
        return 1.0, {
            "retained_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "note": "no SP-covered usage rows in window; share = 1.0",
        }

    retained = float(rows[0].get("retained_cost") or 0)
    total = float(rows[0].get("total_cost") or 0)
    if total <= 0:
        return 1.0, {
            "retained_cost_usd": retained,
            "total_cost_usd": total,
            "note": "total SP-covered cost is zero; share = 1.0",
        }
    share = max(0.0, min(1.0, retained / total))
    return share, {
        "retained_cost_usd": retained,
        "total_cost_usd": total,
        "note": "share = retained / total",
    }


def fetch_all_relevant_sps(profile: str, max_window_start: datetime) -> list[SPInfo]:
    """Return active + recently retired SPs for window_search transition detection.

    See spec 5.7. The extra 7-day lookback on retired SPs supports the
    recent_sp_expiry_near_window risk case (a).
    """
    active = savingsplans_describe_savings_plans(profile, states=["active", "payment-pending"])
    retired_all = savingsplans_describe_savings_plans(profile, states=["retired"])
    cutoff = max_window_start - timedelta(days=7)
    recently_retired = [sp for sp in retired_all if sp.end >= cutoff]
    return active + recently_retired


@dataclass
class SPAudit:
    """Result of get_existing_sp_effective. See spec 5.1b."""

    count: int
    compute_count: int
    non_compute_count: int
    total_commitment_all: float
    compute_commitment: float
    utilization_30d: float  # fraction in [0, 1]
    utilization_source: str  # 'ce_api' | 'fallback_assumed_100pct'
    effective: float
    has_non_compute_sps: bool
    active_sp_list: list[dict]
    sp_coverage_share: float = 1.0  # fraction of SP effective coverage retained under exclude filter


@dataclass
class TransitionEvent:
    sp_id: str
    event_type: str  # 'start' | 'end'
    date: datetime


def find_sp_transitions_in(all_relevant_sps: list, window) -> list[TransitionEvent]:
    """Detect SP start or end events inside the window. See spec 5.9."""
    transitions: list[TransitionEvent] = []
    for sp in all_relevant_sps:
        if window.start <= sp.start < window.end:
            transitions.append(TransitionEvent(sp_id=sp.id, event_type="start", date=sp.start))
        if window.start <= sp.end < window.end:
            transitions.append(TransitionEvent(sp_id=sp.id, event_type="end", date=sp.end))
    return transitions


@dataclass
class DiscountAudit:
    """Audit trail for the discount rate. See spec 5.2."""

    d_raw: float                    # workload-weighted d from pricing API ratios
    d_calibrated: float             # d_raw adjusted for EDP differential
    e_sp: float                     # 1 - SPRecurringFee.net / .unblended
    e_od: float                     # 1 - Usage.net / .unblended
    edp_uniform: bool               # |e_sp - e_od| <= 0.005
    calibration_applied: bool       # True iff edp_uniform is False
    source: str
    pricing_cache_version: str
    pricing_cache_fetched_at: datetime
    mix_window_days: int
    coverage_pct: float
    matched_entries: int
    unmatched_entries: int
    unmatched_top10_by_cost: list[dict]
    e_sp_inferred_from_e_od: bool = False   # True when account has no existing SP


@dataclass
class TrendModel:
    """Thin wrapper around a fitted statsmodels.RLM result. See spec 5.1b."""

    slope: float
    intercept: float
    r2: float
    _statsmodels_result: object = None

    def predict(self, ts):
        return self._statsmodels_result.predict(ts)


@dataclass
class SeasonalityModel:
    r2: float
    _statsmodels_result: object = None

    def predict(self, ts):
        return self._statsmodels_result.predict(ts)


@dataclass
class TrendAwareAudit:
    slope_per_hour: float
    trend_r2: float
    seasonality_r2: float
    residual_std: float
    projection_now_mean: float
    projection_end_mean_raw: float
    projection_end_mean_clamped: float
    min_future_mean_floor: float
    fraction_of_horizon_clamped: float
    horizon_hours: int
    # NOSONAR: field name matches the JSON output schema key; changing it would break the public contract
    stationary_C_star_comparison: float  # NOSONAR
    delta_pct_vs_stationary: float


@dataclass
class TrendBucket:
    """Rich bucket stats for trend classification. See spec 5.1b."""

    period_start: datetime
    period_end: datetime
    sample_size: int
    p17: float
    mean: float
    min: float
    max: float


@dataclass
class ValidatedCandidate:
    window: Window
    trend_classification: str
    trend_buckets: list[TrendBucket]
    trend_b3_over_b1_ratio: float
    non_monotonic_swing_pct: float | None
    sample_size_hours: int
    completeness_pct: float
    deviation_score: int


@dataclass
class RejectedCandidate:
    window: Window
    reason: str  # severe_decline|incomplete_data|sp_transition_in_window
    b3_b1_ratio: float | None = None
    buckets: list[float] | None = None
    completeness_pct: float | None = None
    transition_events: list[dict] | None = None


@dataclass
class WindowSearchResult:
    status: str  # 'success' | 'failed'
    chosen: ValidatedCandidate | None = None
    top_5: list[ValidatedCandidate] = field(default_factory=list)
    chosen_window_risks: list = field(default_factory=list)
    adjustments_made: list = field(default_factory=list)
    search_summary: dict | None = None
    failure_reason: str | None = None
    rejection_summary: dict | None = None
    full_rejection_log: list | None = None


@dataclass
class BaselineStats:
    sample_size_hours: int
    expected_sample_size_hours: int
    completeness_pct: float
    p0: float
    p5: float
    p10: float
    p17: float
    p25: float
    p50: float
    p75: float
    p83: float
    p90: float
    p95: float
    p100: float
    mean: float
    std: float
    min: float
    max: float


@dataclass
class FinalResult:
    # NOSONAR on these three: field names are part of the JSON output schema
    # (output_builder.py:613-615) and match the newsvendor formula notation.
    # Renaming would break the public contract and severe the mapping to
    # formula-derivation.md §2 (C*, C_existing, ΔC).
    C_star_total: float  # NOSONAR
    C_existing_effective: float  # NOSONAR
    C_new_delta: float  # NOSONAR
    d_blended: float
    formula_branch: str
    trend_classification: str
    trend_buckets: list[TrendBucket]
    trend_b3_over_b1_ratio: float
    non_monotonic_swing_pct: float | None
    non_monotonic_pattern: str | None
    baseline_stats: BaselineStats
    bootstrap_se: float | None
    bootstrap_95ci: list[float] | None
    d_audit: DiscountAudit
    sp_audit: SPAudit
    trend_aware_audit: TrendAwareAudit | None
