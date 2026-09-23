"""Single-Athena-query CUR pull that builds HourlySeries for window search.

See spec 5.1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts._common import HourlySeries, NoDataError, OrgConfig, athena_execute, generate_partition_filter
from scripts.exclude_filter import ExcludeFilter, build_exclude_sql_clauses

_AND_CLAUSE_JOINER = "\n  AND "

_REGION_DISCOVERY_SQL_TEMPLATE = """
SELECT
    product_region_code AS region,
    SUM(pricing_public_on_demand_cost) AS total_cost
FROM "{cur_database}"."{cur_table}"
WHERE {partition_filter}
  AND line_item_usage_start_date >= TIMESTAMP '{start}'
  AND line_item_usage_start_date <  TIMESTAMP '{end}'
  AND line_item_line_item_type IN ('Usage', 'SavingsPlanCoveredUsage')
  AND pricing_public_on_demand_cost > 0
  AND product_region_code IS NOT NULL
  AND product_region_code <> ''
  AND product_region_code <> 'global'
  AND product_region_code <> 'Any'
  AND (
        (line_item_product_code = 'AmazonEC2'
         AND line_item_usage_type LIKE '%BoxUsage%'
         AND line_item_usage_type NOT LIKE '%SpotUsage%')
        OR (line_item_product_code = 'AWSLambda'
            AND line_item_usage_type LIKE '%Lambda-%GB-Second%')
        OR (line_item_product_code IN ('AmazonECS', 'AmazonEKS')
            AND line_item_usage_type LIKE '%Fargate%')
      ){exclude_clauses}
GROUP BY product_region_code
ORDER BY total_cost DESC
"""


def discover_workload_regions(
    config: OrgConfig,
    window_start: datetime,
    window_end: datetime,
    exclude_filter: ExcludeFilter | None = None,
    available_tag_columns: frozenset[str] | None = None,
) -> list[tuple[str, float]]:
    """Query CUR to find SP-eligible regions in the analysis window.

    Returns a list of (region_code, cost_share_pct) sorted descending by share.
    cost_share_pct is a fraction in [0, 1].
    Raises whatever athena_execute raises on query failure — the orchestrator
    (run_optimizer) catches and falls back to {primary_region}.

    ``exclude_filter`` + ``available_tag_columns`` inject injection-safe AND
    clauses that strip excluded usage-type patterns / accounts / tag values
    from region cost attribution; defaults preserve pre-filter SQL byte-for-byte.
    """
    assert window_start.tzinfo is not None, "window_start must be tz-aware"
    assert window_end.tzinfo is not None, "window_end must be tz-aware"
    partition_filter = generate_partition_filter(window_start, window_end)
    start_lit = window_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_lit = window_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    exclude_filter = exclude_filter or ExcludeFilter()
    available_tag_columns = available_tag_columns or frozenset()
    clauses, _ = build_exclude_sql_clauses(exclude_filter, available_tag_columns)
    exclude_clauses = (
        _AND_CLAUSE_JOINER + _AND_CLAUSE_JOINER.join(clauses) if clauses else ""
    )
    query = _REGION_DISCOVERY_SQL_TEMPLATE.format(
        cur_database=config.cur_database,
        cur_table=config.cur_table,
        partition_filter=partition_filter,
        start=start_lit,
        end=end_lit,
        exclude_clauses=exclude_clauses,
    )
    rows = athena_execute(config, query)
    if not rows:
        return []
    totals = [(r["region"], float(r["total_cost"])) for r in rows if r.get("region")]
    grand_total = sum(cost for _, cost in totals)
    if grand_total <= 0:
        return []
    return [
        (region, round(cost / grand_total, 4))
        for region, cost in totals
    ]

_CUR_SQL_TEMPLATE = """
SELECT
    date_trunc('hour', line_item_usage_start_date)     AS usage_hour,
    line_item_line_item_type                           AS line_type,
    line_item_usage_account_id                         AS account_id,
    line_item_product_code                             AS product_code,
    product_region_code                                AS region,
    product_instance_type                              AS instance_type,
    line_item_operation                                AS operation,
    line_item_usage_type                               AS usage_type,
    SUM(pricing_public_on_demand_cost)                 AS gross_list_usd,
    SUM(line_item_net_unblended_cost)                  AS net_cost_usd,
    SUM(savings_plan_savings_plan_effective_cost)      AS sp_effective_cost_usd,
    SUM(line_item_usage_amount)                        AS usage_amount
FROM "{cur_database}"."{cur_table}"
WHERE {partition_filter}
  AND line_item_usage_start_date >= TIMESTAMP '{max_window_start}'
  AND line_item_usage_start_date <  TIMESTAMP '{max_window_end}'
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
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
"""


def _parse_usage_hour(h_raw) -> datetime:
    """Parse a usage_hour value (str or datetime) to a tz-aware UTC datetime."""
    if isinstance(h_raw, str):
        h_clean = h_raw.split(".")[0].replace(" ", "T")
        return datetime.fromisoformat(h_clean).replace(tzinfo=timezone.utc)
    if isinstance(h_raw, datetime):
        return h_raw if h_raw.tzinfo else h_raw.replace(tzinfo=timezone.utc)
    raise ValueError(f"Unexpected usage_hour type: {type(h_raw)}")


def _net_contribution_for_row(row: dict, sp_fee_net_factor: float) -> float | None:
    """Return the net cost contribution for a CUR row, or None to skip the row."""
    line_type = row["line_type"]
    if line_type == "Usage":
        return float(row.get("net_cost_usd") or 0)
    if line_type == "SavingsPlanCoveredUsage":
        # SP covers usage; real bill is the SP fee share (sp_effective_cost is list-unit)
        # scaled by EDP on SP fee.
        sp_eff = float(row.get("sp_effective_cost_usd") or 0)
        return sp_eff * sp_fee_net_factor
    return None


def build_hourly_series(
    config: OrgConfig,
    window_start: datetime,
    window_end: datetime,
    e_sp: float,
    exclude_filter: ExcludeFilter | None = None,
    available_tag_columns: frozenset[str] | None = None,
) -> HourlySeries:
    """Execute the CUR query over [window_start, window_end) and aggregate rows.

    Both datetimes must be tz-aware UTC.
    e_sp is the SP EDP discount factor (e.g. 0.09 for 9% EDP on SP fees).

    ``exclude_filter`` + ``available_tag_columns`` inject injection-safe AND
    clauses that strip excluded usage-type patterns / accounts / tag values
    from the hourly aggregation; defaults preserve pre-filter SQL byte-for-byte.
    """
    assert window_start.tzinfo is not None
    assert window_end.tzinfo is not None

    partition_filter = generate_partition_filter(window_start, window_end)
    start_lit = window_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_lit = window_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    exclude_filter = exclude_filter or ExcludeFilter()
    available_tag_columns = available_tag_columns or frozenset()
    clauses, _ = build_exclude_sql_clauses(exclude_filter, available_tag_columns)
    exclude_clauses = (
        _AND_CLAUSE_JOINER + _AND_CLAUSE_JOINER.join(clauses) if clauses else ""
    )
    query = _CUR_SQL_TEMPLATE.format(
        cur_database=config.cur_database,
        cur_table=config.cur_table,
        partition_filter=partition_filter,
        max_window_start=start_lit,
        max_window_end=end_lit,
        exclude_clauses=exclude_clauses,
    )
    rows = athena_execute(config, query)

    n_hours = int((window_end - window_start).total_seconds() / 3600)
    hours = [window_start + timedelta(hours=i) for i in range(n_hours)]
    data: dict[datetime, dict] = {
        h: {"total_list_usd": 0.0, "total_net_usd": 0.0, "mix": {}} for h in hours
    }

    sp_fee_net_factor = 1.0 - e_sp

    for row in rows:
        h = _parse_usage_hour(row["usage_hour"])
        if h not in data:
            continue

        net_contribution = _net_contribution_for_row(row, sp_fee_net_factor)
        if net_contribution is None:
            continue

        gross_list = float(row["gross_list_usd"] or 0)
        data[h]["total_list_usd"] += gross_list
        data[h]["total_net_usd"] += net_contribution

        mix_key = (
            row["product_code"],
            row["region"],
            row["instance_type"],
            row["operation"],
            row["usage_type"],
        )
        data[h]["mix"][mix_key] = data[h]["mix"].get(mix_key, 0.0) + gross_list

    return HourlySeries(
        hours=hours, data=data,
        window_start=window_start, window_end=window_end,
    )


_EDP_OD_SQL = """
SELECT
    SUM(line_item_unblended_cost)      AS e_od_unblended,
    SUM(line_item_net_unblended_cost)  AS e_od_net
FROM "{cur_database}"."{cur_table}"
WHERE {partition_filter}
  AND line_item_usage_start_date >= TIMESTAMP '{start}'
  AND line_item_usage_start_date <  TIMESTAMP '{end}'
  AND line_item_line_item_type = 'Usage'
  AND line_item_unblended_cost > 0
  AND (
        (line_item_product_code = 'AmazonEC2'
         AND line_item_usage_type LIKE '%BoxUsage%'
         AND line_item_usage_type NOT LIKE '%SpotUsage%')
        OR (line_item_product_code = 'AWSLambda'
            AND line_item_usage_type LIKE '%Lambda-%GB-Second%')
        OR (line_item_product_code IN ('AmazonECS', 'AmazonEKS')
            AND line_item_usage_type LIKE '%Fargate%')
      ){exclude_clauses}
"""

_EDP_SP_SQL = """
SELECT
    SUM(line_item_unblended_cost)      AS e_sp_unblended,
    SUM(line_item_net_unblended_cost)  AS e_sp_net
FROM "{cur_database}"."{cur_table}"
WHERE {partition_filter}
  AND line_item_usage_start_date >= TIMESTAMP '{start}'
  AND line_item_usage_start_date <  TIMESTAMP '{end}'
  AND line_item_line_item_type = 'SavingsPlanRecurringFee'
  AND line_item_unblended_cost > 0
"""


def _edp_ratio(rows: list, num_key: str, den_key: str) -> float | None:
    """Compute 1 - num/den from the first row; return None if data is absent or invalid."""
    if not rows:
        return None
    den_raw = rows[0].get(den_key)
    num_raw = rows[0].get(num_key)
    if den_raw in (None, "", "0", "0.0"):
        return None
    try:
        den = float(den_raw)
        num = float(num_raw or 0)
    except (TypeError, ValueError):
        return None
    if den <= 0:
        return None
    return 1.0 - num / den


def _safe_float(rows: list, key: str) -> float:
    """Safely extract a float from the first row of an Athena result, defaulting to 0.0."""
    try:
        return float(rows[0].get(key) or 0) if rows else 0.0
    except (TypeError, ValueError):
        return 0.0


def compute_edp_factors(
    config: OrgConfig,
    window_start: datetime,
    window_end: datetime,
    exclude_filter: ExcludeFilter | None = None,
    available_tag_columns: frozenset[str] | None = None,
) -> tuple[float, float, dict]:
    """Return (e_sp, e_od, audit) where e_* = 1 - net_unblended / unblended.

    audit keys:
      - uniform (bool): |e_sp - e_od| <= 0.005
      - delta (float): |e_sp - e_od|
      - e_sp_sample_usd (float): Σ unblended for SP fee rows
      - e_od_sample_usd (float): Σ unblended for Usage rows
      - e_sp_inferred_from_e_od (bool): True iff account has no SP fee yet

    The ``exclude_filter`` is applied to the OD (Usage) query only so that
    ``e_od`` reflects the retained workload and stays consistent with the
    filtered hourly series ``X``. The SP fee query is intentionally exempt:
    ``SavingsPlanRecurringFee`` rows are org-level recurring fees with no
    usage_type / instance_type / tag dimension, so a workload predicate there
    would either filter everything out or be a no-op — either way semantically
    wrong.

    Raises NoDataError(code='no_od_rows_for_edp') when there are no Usage rows
    in the window (cannot infer EDP).
    """
    assert window_start.tzinfo is not None
    assert window_end.tzinfo is not None
    partition_filter = generate_partition_filter(window_start, window_end)
    start_lit = window_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_lit = window_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    exclude_filter = exclude_filter or ExcludeFilter()
    available_tag_columns = available_tag_columns or frozenset()
    clauses, _ = build_exclude_sql_clauses(exclude_filter, available_tag_columns)
    exclude_clauses = (
        _AND_CLAUSE_JOINER + _AND_CLAUSE_JOINER.join(clauses) if clauses else ""
    )

    od_fmt = dict(
        cur_database=config.cur_database,
        cur_table=config.cur_table,
        partition_filter=partition_filter,
        start=start_lit,
        end=end_lit,
        exclude_clauses=exclude_clauses,
    )
    sp_fmt = dict(
        cur_database=config.cur_database,
        cur_table=config.cur_table,
        partition_filter=partition_filter,
        start=start_lit,
        end=end_lit,
    )

    od_rows = athena_execute(config, _EDP_OD_SQL.format(**od_fmt))
    # SP fee query intentionally skips exclude filter (no usage-level dimension)
    sp_rows = athena_execute(config, _EDP_SP_SQL.format(**sp_fmt))

    e_od = _edp_ratio(od_rows, "e_od_net", "e_od_unblended")
    e_sp = _edp_ratio(sp_rows, "e_sp_net", "e_sp_unblended")

    if e_od is None:
        raise NoDataError(
            code="no_od_rows_for_edp",
            message="No EC2/Lambda/Fargate Usage rows in window; cannot infer EDP.",
        )

    sp_inferred = False
    if e_sp is None:
        e_sp = e_od
        sp_inferred = True

    delta = abs(e_sp - e_od)
    audit = {
        "uniform": delta <= 0.005,
        "delta": round(delta, 6),
        "e_sp_sample_usd": _safe_float(sp_rows, "e_sp_unblended"),
        "e_od_sample_usd": _safe_float(od_rows, "e_od_unblended"),
        "e_sp_inferred_from_e_od": sp_inferred,
    }
    return e_sp, e_od, audit
