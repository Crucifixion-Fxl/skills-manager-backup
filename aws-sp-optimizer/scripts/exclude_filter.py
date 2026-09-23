"""Exclude filter: dataclasses, validators, and SQL clause builder.

Encapsulates user intent to exclude portions of workload (by instance family,
account, or tag) from C* computation. Shared by all CUR SQL touchpoints.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from scripts._common import OrgConfig, athena_execute

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TagExclusion:
    """One tag key with its excluded values.

    Values must be a tuple (hashable) so this dataclass is itself hashable and
    can live inside a frozen ExcludeFilter.
    """

    key: str
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple):
            raise TypeError(
                f"TagExclusion.values must be tuple, got {type(self.values).__name__}"
            )


@dataclass(frozen=True)
class ExcludeFilter:
    """User intent: portions of workload to exclude from C* computation.

    All axes default to empty; is_empty() short-circuits SQL no-ops and
    preserves pre-filter behavior byte-for-byte.
    """

    usage_type_patterns: tuple[str, ...] = ()
    account_ids: tuple[str, ...] = ()
    tag_exclusions: tuple[TagExclusion, ...] = ()
    include_untagged: bool = True

    def is_empty(self) -> bool:
        return not (
            self.usage_type_patterns
            or self.account_ids
            or self.tag_exclusions
        )


# Regex patterns for injection-safe validation
_PATTERN_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")
_TAG_KEY_RE = re.compile(r"^[A-Za-z0-9._:/@-]{1,128}$")
_TAG_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/@ -]{1,256}$")


def validate_usage_type_pattern(p: str) -> None:
    """Raise ValueError if p contains SQL-unsafe characters or is empty/too long."""
    if not isinstance(p, str) or not (1 <= len(p) <= 64) or not _PATTERN_RE.match(p):
        raise ValueError(
            f"invalid usage_type_pattern {p!r}: must be 1-64 chars matching "
            f"[A-Za-z0-9._:-]"
        )


def validate_account_id(a: str) -> None:
    """Raise ValueError if a is not exactly 12 digits."""
    if not isinstance(a, str) or not _ACCOUNT_ID_RE.match(a):
        raise ValueError(f"invalid account_id {a!r}: must be 12 digits")


def validate_tag_key(k: str) -> None:
    """Raise ValueError if tag key is invalid (length, charset)."""
    if not isinstance(k, str) or not _TAG_KEY_RE.match(k):
        raise ValueError(
            f"invalid tag key {k!r}: must be 1-128 chars matching [A-Za-z0-9._:/@-]"
        )


def validate_tag_value(v: str) -> None:
    """Raise ValueError if tag value is invalid (length, charset)."""
    if not isinstance(v, str) or not _TAG_VALUE_RE.match(v):
        raise ValueError(
            f"invalid tag value {v!r}: must be 1-256 chars matching "
            f"[A-Za-z0-9._:/@ -]"
        )


def cur_tag_column_name(key: str) -> str:
    """AWS normalization rule: replace non-alphanumeric with _, prefix with resource_tags_user_."""
    normalized = re.sub(r"[^A-Za-z0-9]", "_", key).lower()
    return f"resource_tags_user_{normalized}"


def build_exclude_sql_clauses(
    flt: ExcludeFilter,
    available_tag_columns: frozenset[str],
) -> tuple[list[str], list[str]]:
    """Emit injection-safe SQL AND-clauses for the filter.

    Returns (clauses, warnings). Clauses are meant to be prepended with ' AND '
    and joined into an existing WHERE. Warnings report skipped filters
    (e.g., missing tag columns).

    Validators MUST have been run before values reach this function. This
    function assumes input is already safe and performs no further escaping.
    """
    clauses: list[str] = []
    warnings: list[str] = []

    for pattern in flt.usage_type_patterns:
        clauses.append(f"line_item_usage_type NOT LIKE '%{pattern}%'")

    if flt.account_ids:
        id_list = ", ".join(f"'{a}'" for a in flt.account_ids)
        clauses.append(f"line_item_usage_account_id NOT IN ({id_list})")

    for tag_excl in flt.tag_exclusions:
        col = cur_tag_column_name(tag_excl.key)
        if col not in available_tag_columns:
            warnings.append(
                f"tag column {col!r} (for key {tag_excl.key!r}) not present in "
                f"CUR — filter skipped"
            )
            continue
        val_list = ", ".join(f"'{v}'" for v in tag_excl.values)
        if flt.include_untagged:
            clauses.append(f"({col} IS NULL OR {col} NOT IN ({val_list}))")
        else:
            clauses.append(f"{col} IS NOT NULL")
            clauses.append(f"{col} NOT IN ({val_list})")

    return clauses, warnings


_INFORMATION_SCHEMA_SQL = """
SELECT column_name
FROM information_schema.columns
WHERE table_schema = '{cur_database}'
  AND table_name = '{cur_table}'
  AND column_name LIKE 'resource_tags_user_%'
"""


def fetch_available_tag_columns(config: OrgConfig) -> frozenset[str]:
    """Query information_schema for user-tag columns present in the CUR table.

    Best-effort: on Athena failure, log a warning and return an empty set. Tag
    exclusions will then be skipped-with-warning downstream (see
    build_exclude_sql_clauses).
    """
    query = _INFORMATION_SCHEMA_SQL.format(
        cur_database=config.cur_database,
        cur_table=config.cur_table,
    )
    try:
        rows = athena_execute(config, query)
    except Exception as e:  # noqa: BLE001 — best-effort
        _log.warning("tag column discovery failed: %s", e)
        return frozenset()
    cols = {
        r["column_name"]
        for r in rows
        if isinstance(r.get("column_name"), str)
        and r["column_name"].startswith("resource_tags_user_")
    }
    return frozenset(cols)
