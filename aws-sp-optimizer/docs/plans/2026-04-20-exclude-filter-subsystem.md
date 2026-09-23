# Exclude Filter Subsystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared exclude-filter subsystem to aws-sp-optimizer so users can compute C* excluding specific instance families (P1), member accounts (P2), or resource tags (P3). All three exclusion axes share the same filter object, SQL injection safety, `d_raw` consistency, and `C_existing_effective` adjustment.

**Architecture:** A single frozen `ExcludeFilter` dataclass flows from CLI/config down through 5 CUR SQL touchpoints + 1 new CUR helper that attenuates `C_existing_effective`. Tag column discovery is done once per run via `information_schema.columns`. Missing tag columns produce warnings, not errors. All filters compose with AND semantics; default filter is empty and preserves existing behavior byte-for-byte.

**Tech Stack:** Python ≥3.10 · pytest · AWS Athena (Presto SQL) · existing boto3 wrappers in `scripts/_common.py`.

---

## Scope & Non-Goals

**In scope (this plan):**
- P0: Shared filter infrastructure (dataclass + SQL builder + tag column discovery + wiring through 5 SQL sites + `C_existing_effective` attenuation + `d_raw` consistency)
- P1: `--exclude-usage-type-pattern` (e.g. `g4dn.`)
- P2: `--exclude-account-id` (e.g. sandbox accounts)
- P3: `--exclude-tag key=v1,v2` + `--include-untagged/--exclude-untagged`

**Out of scope (future plans):**
- Coverage-target mode
- 1y vs 3y term comparison
- What-if scenario mode (side-by-side multi-filter runs)
- Forward-looking usage adjustments
- Account-level breakdown reports
- EC2 Instance SP mix optimization

## File Structure

### New files
- `scripts/exclude_filter.py` — `ExcludeFilter` + `TagExclusion` dataclasses, validators, SQL clause builder, CUR tag column helper. Single responsibility: encapsulate "what to exclude" and emit injection-safe SQL.
- `tests/test_exclude_filter_dataclass.py`, `tests/test_exclude_filter_validators.py`, `tests/test_exclude_filter_sql.py`, `tests/test_exclude_filter_tag_columns.py`, `tests/test_sp_coverage_share.py`, `tests/test_end_to_end_with_filters.py`

### Modified files
- `scripts/cur_query.py` — thread filter through 4 SQL templates (`_REGION_DISCOVERY_SQL_TEMPLATE`, `_CUR_SQL_TEMPLATE`, `_EDP_OD_SQL`) and public functions (`discover_workload_regions`, `build_hourly_series`, `compute_edp_factors`)
- `scripts/_common.py` — thread filter through `compute_untagged_fraction`'s SQL; add new helper `compute_sp_coverage_share` (CUR query)
- `scripts/newsvendor.py` — extend `get_existing_sp_effective` with optional `sp_coverage_share` multiplier; extend `SPAudit` with `sp_coverage_share` field
- `scripts/config_loader.py` — parse YAML `exclude:` block into `ExcludeFilter`; extend `OrgConfig` dataclass
- `scripts/aws_sp_optimizer.py` — CLI flags, config-CLI merge, pass filter into run_optimizer
- `scripts/output_builder.py` — reflect applied filter in context + coverage_scope string
- `references/usage.md`, `references/output-interpretation.md`, `references/failure-handling.md`, `SKILL.md`, `references/first-run-setup.md`

### Not modified
- `scripts/pricing_cache.py` — pricing API cache is workload-type agnostic; filter does not alter ratios lookup
- `scripts/window_search.py` — `find_sp_transitions_in` operates on SPInfo objects (which are not filterable by this subsystem); filter does not alter its behavior
- `scripts/newsvendor.compute_d_blended` — already reads only from filtered `HourlySeries`; gets d_raw consistency for free (regression test only)

---

## Design Reference (read before starting)

### ExcludeFilter data model

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class TagExclusion:
    key: str                         # e.g. "lifecycle" (pre-normalization)
    values: tuple[str, ...]          # e.g. ("ephemeral", "experimental")

@dataclass(frozen=True)
class ExcludeFilter:
    usage_type_patterns: tuple[str, ...] = ()
    account_ids: tuple[str, ...] = ()
    tag_exclusions: tuple[TagExclusion, ...] = ()
    include_untagged: bool = True    # NULL tag value semantics

    def is_empty(self) -> bool:
        return not (self.usage_type_patterns or self.account_ids or self.tag_exclusions)
```

### CUR tag column naming rule

AWS normalizes user tag keys to column names by replacing any character outside `[A-Za-z0-9]` with `_` and prefixing with `resource_tags_user_`. So `cost-category` → `resource_tags_user_cost_category`, `team:data` → `resource_tags_user_team_data`.

### SQL clause generation

Given a filter and a set of available tag columns (from `information_schema`), emit a list of AND-joinable SQL fragments plus any warnings:

```python
def build_exclude_sql_clauses(
    flt: ExcludeFilter,
    available_tag_columns: frozenset[str],
) -> tuple[list[str], list[str]]:
    """Return (clauses, warnings).

    clauses: list of SQL predicates (no leading AND). Caller prepends ' AND ' and joins.
    warnings: human-readable warnings for skipped filters (e.g., missing tag column).
    """
```

For `usage_type_patterns`: `line_item_usage_type NOT LIKE '%{pattern}%'`
For `account_ids`: `line_item_usage_account_id NOT IN ('id1', 'id2')`
For tag with `include_untagged=True`: `(col IS NULL OR col NOT IN ('v1', 'v2'))`
For tag with `include_untagged=False`: `col IS NOT NULL AND col NOT IN ('v1', 'v2')`

### Injection safety (validators enforce before SQL build)

- `usage_type_pattern`: regex `^[A-Za-z0-9._-]+$`, length 1..64
- `account_id`: regex `^\d{12}$`
- `tag key` (pre-normalization): regex `^[A-Za-z0-9._:/@-]{1,128}$`
- `tag value`: regex `^[A-Za-z0-9._:/@ -]{1,256}$` — space allowed, quote/semicolon/backslash rejected

Any violation raises `ConfigError` with the offending value truncated for diagnostics.

### Glue between filter and `C_existing_effective`

Currently `get_existing_sp_effective` = `Σ sp.commitment × utilization_ce_api`. When a filter is active, a portion of existing SP coverage may apply to workload we are now excluding. Introduce:

```python
def compute_sp_coverage_share(
    config: OrgConfig,
    window_start: datetime,
    window_end: datetime,
    flt: ExcludeFilter,
    available_tag_columns: frozenset[str],
) -> tuple[float, dict]:
    """Return (share, audit).

    share: fraction in [0, 1] = SP-covered usage retained under filter /
           total SP-covered usage in window. 1.0 when filter is empty.
    audit: {retained_cost_usd, total_cost_usd, window_start, window_end, sql}
    """
```

Then: `effective_per_hour = compute_commitment × util × share`. When `filter.is_empty()`, `share = 1.0` and behavior is identical to today.

---

## Task 0: Pre-flight

**Files:**
- Workspace: `~/claude-home/gitlab/engineering/skills` (branch `feat/aws-sp-optimizer-v1`)

- [ ] **Step 1: Confirm clean baseline**

Run:
```bash
cd ~/claude-home/gitlab/engineering/skills/skills/aws-sp-optimizer && \
  git status -sb && \
  python -m pytest tests/ -x --tb=short 2>&1 | tail -20
```

Expected: all tests PASS. Working tree clean on `feat/aws-sp-optimizer-v1` (ignore `.hypothesis/` and `skills/sessions/` which are not part of aws-sp-optimizer).

- [ ] **Step 2: Cut feature branch**

Run:
```bash
cd ~/claude-home/gitlab/engineering/skills && \
  git checkout -b feat/aws-sp-optimizer-exclude-filters feat/aws-sp-optimizer-v1
```

Expected: new branch created from current tip.

- [ ] **Step 3: Commit plan file**

```bash
cd ~/claude-home/gitlab/engineering/skills && \
  git add skills/aws-sp-optimizer/docs/plans/2026-04-20-exclude-filter-subsystem.md && \
  git commit -m "docs(aws-sp-optimizer): add exclude-filter subsystem plan"
```

---

## Task 1: `TagExclusion` dataclass

**Files:**
- Create: `skills/aws-sp-optimizer/scripts/exclude_filter.py`
- Create: `skills/aws-sp-optimizer/tests/test_exclude_filter_dataclass.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_exclude_filter_dataclass.py`:
```python
import pytest
from scripts.exclude_filter import TagExclusion


def test_tag_exclusion_frozen_and_hashable():
    t = TagExclusion(key="lifecycle", values=("ephemeral",))
    assert t.key == "lifecycle"
    assert t.values == ("ephemeral",)
    with pytest.raises((AttributeError, TypeError)):
        t.key = "other"
    # hashable: can be placed in a set
    assert {t, TagExclusion(key="lifecycle", values=("ephemeral",))} == {t}


def test_tag_exclusion_rejects_list_values():
    # values must be tuple to be hashable/frozen
    with pytest.raises(TypeError):
        TagExclusion(key="k", values=["v1"])  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd ~/claude-home/gitlab/engineering/skills/skills/aws-sp-optimizer && \
  python -m pytest tests/test_exclude_filter_dataclass.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'scripts.exclude_filter'`.

- [ ] **Step 3: Implement `TagExclusion`**

Create `scripts/exclude_filter.py`:
```python
"""Exclude filter: dataclasses, validators, and SQL clause builder.

Encapsulates user intent to exclude portions of workload (by instance family,
account, or tag) from C* computation. Shared by all CUR SQL touchpoints.
"""

from __future__ import annotations

from dataclasses import dataclass


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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_exclude_filter_dataclass.py -v 2>&1 | tail -10
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/exclude_filter.py tests/test_exclude_filter_dataclass.py
git commit -m "feat(aws-sp-optimizer): add TagExclusion dataclass"
```

---

## Task 2: `ExcludeFilter` dataclass

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/exclude_filter.py`
- Modify: `skills/aws-sp-optimizer/tests/test_exclude_filter_dataclass.py`

- [ ] **Step 1: Extend the failing test**

Append to `tests/test_exclude_filter_dataclass.py`:
```python
from scripts.exclude_filter import ExcludeFilter


def test_exclude_filter_defaults_empty():
    f = ExcludeFilter()
    assert f.usage_type_patterns == ()
    assert f.account_ids == ()
    assert f.tag_exclusions == ()
    assert f.include_untagged is True
    assert f.is_empty() is True


def test_exclude_filter_not_empty_when_any_axis_set():
    f1 = ExcludeFilter(usage_type_patterns=("g4dn.",))
    f2 = ExcludeFilter(account_ids=("123456789012",))
    f3 = ExcludeFilter(
        tag_exclusions=(TagExclusion(key="lifecycle", values=("ephemeral",)),)
    )
    assert f1.is_empty() is False
    assert f2.is_empty() is False
    assert f3.is_empty() is False


def test_exclude_filter_frozen_hashable():
    f = ExcludeFilter(usage_type_patterns=("g4dn.",))
    assert {f, ExcludeFilter(usage_type_patterns=("g4dn.",))} == {f}
    with pytest.raises((AttributeError, TypeError)):
        f.usage_type_patterns = ("x",)
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_exclude_filter_dataclass.py -v 2>&1 | tail -10
```

Expected: `ImportError: cannot import name 'ExcludeFilter'`.

- [ ] **Step 3: Implement `ExcludeFilter`**

Append to `scripts/exclude_filter.py`:
```python
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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_exclude_filter_dataclass.py -v 2>&1 | tail -10
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/exclude_filter.py tests/test_exclude_filter_dataclass.py
git commit -m "feat(aws-sp-optimizer): add ExcludeFilter dataclass"
```

---

## Task 3: Input validators (injection-safe)

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/exclude_filter.py`
- Create: `skills/aws-sp-optimizer/tests/test_exclude_filter_validators.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_exclude_filter_validators.py`:
```python
import pytest
from scripts.exclude_filter import (
    validate_usage_type_pattern,
    validate_account_id,
    validate_tag_key,
    validate_tag_value,
    cur_tag_column_name,
)


@pytest.mark.parametrize("p", ["g4dn.", "p4d.24xlarge", "BoxUsage:m5.large", "ABC_123-x.y"])
def test_usage_type_pattern_accepts_safe(p):
    validate_usage_type_pattern(p)  # no raise


@pytest.mark.parametrize(
    "p", ["", "a" * 65, "has space", "has'quote", "has;semi", "has%percent", "a\\b"]
)
def test_usage_type_pattern_rejects_unsafe(p):
    with pytest.raises(ValueError):
        validate_usage_type_pattern(p)


@pytest.mark.parametrize("a", ["002497567426", "123456789012"])
def test_account_id_accepts_12_digits(a):
    validate_account_id(a)


@pytest.mark.parametrize("a", ["12345", "12345678901234", "abcdefghijkl", "002-497-567-426"])
def test_account_id_rejects_non_12_digits(a):
    with pytest.raises(ValueError):
        validate_account_id(a)


@pytest.mark.parametrize("k", ["lifecycle", "cost-category", "team:data", "env/stage"])
def test_tag_key_accepts_safe(k):
    validate_tag_key(k)


@pytest.mark.parametrize("k", ["", "has space", "has'quote", "a" * 129])
def test_tag_key_rejects_unsafe(k):
    with pytest.raises(ValueError):
        validate_tag_key(k)


@pytest.mark.parametrize("v", ["ephemeral", "prod", "team alpha", "ns/app"])
def test_tag_value_accepts_safe(v):
    validate_tag_value(v)


@pytest.mark.parametrize("v", ["", "has'quote", "has;semi", "a" * 257])
def test_tag_value_rejects_unsafe(v):
    with pytest.raises(ValueError):
        validate_tag_value(v)


@pytest.mark.parametrize(
    "key,expected",
    [
        ("lifecycle", "resource_tags_user_lifecycle"),
        ("cost-category", "resource_tags_user_cost_category"),
        ("team:data", "resource_tags_user_team_data"),
        ("Env/Stage", "resource_tags_user_env_stage"),
        ("A.B.C", "resource_tags_user_a_b_c"),
    ],
)
def test_cur_tag_column_name(key, expected):
    assert cur_tag_column_name(key) == expected
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_exclude_filter_validators.py -v 2>&1 | tail -10
```

Expected: ImportError on `validate_usage_type_pattern`.

- [ ] **Step 3: Implement validators**

Append to `scripts/exclude_filter.py`:
```python
import re

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
    if not isinstance(a, str) or not _ACCOUNT_ID_RE.match(a):
        raise ValueError(f"invalid account_id {a!r}: must be 12 digits")


def validate_tag_key(k: str) -> None:
    if not isinstance(k, str) or not _TAG_KEY_RE.match(k):
        raise ValueError(
            f"invalid tag key {k!r}: must be 1-128 chars matching [A-Za-z0-9._:/@-]"
        )


def validate_tag_value(v: str) -> None:
    if not isinstance(v, str) or not _TAG_VALUE_RE.match(v):
        raise ValueError(
            f"invalid tag value {v!r}: must be 1-256 chars matching "
            f"[A-Za-z0-9._:/@ -]"
        )


def cur_tag_column_name(key: str) -> str:
    """AWS normalization rule: replace non-alphanumeric with _, prefix with resource_tags_user_."""
    normalized = re.sub(r"[^A-Za-z0-9]", "_", key).lower()
    return f"resource_tags_user_{normalized}"
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_exclude_filter_validators.py -v 2>&1 | tail -10
```

Expected: all parametrized cases pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/exclude_filter.py tests/test_exclude_filter_validators.py
git commit -m "feat(aws-sp-optimizer): add injection-safe validators + tag column name helper"
```

---

## Task 4: SQL clause builder — usage_type & account_id axes

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/exclude_filter.py`
- Create: `skills/aws-sp-optimizer/tests/test_exclude_filter_sql.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_exclude_filter_sql.py`:
```python
from scripts.exclude_filter import (
    ExcludeFilter,
    TagExclusion,
    build_exclude_sql_clauses,
)


def test_empty_filter_emits_no_clauses():
    clauses, warnings = build_exclude_sql_clauses(ExcludeFilter(), frozenset())
    assert clauses == []
    assert warnings == []


def test_usage_type_patterns_emit_not_like_clauses():
    f = ExcludeFilter(usage_type_patterns=("g4dn.", "p4d."))
    clauses, warnings = build_exclude_sql_clauses(f, frozenset())
    assert clauses == [
        "line_item_usage_type NOT LIKE '%g4dn.%'",
        "line_item_usage_type NOT LIKE '%p4d.%'",
    ]
    assert warnings == []


def test_account_ids_emit_single_not_in_clause():
    f = ExcludeFilter(account_ids=("123456789012", "987654321098"))
    clauses, _ = build_exclude_sql_clauses(f, frozenset())
    assert clauses == [
        "line_item_usage_account_id NOT IN ('123456789012', '987654321098')"
    ]


def test_multiple_axes_all_appear():
    f = ExcludeFilter(
        usage_type_patterns=("g4dn.",),
        account_ids=("123456789012",),
    )
    clauses, _ = build_exclude_sql_clauses(f, frozenset())
    assert "line_item_usage_type NOT LIKE '%g4dn.%'" in clauses
    assert "line_item_usage_account_id NOT IN ('123456789012')" in clauses
    assert len(clauses) == 2
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_exclude_filter_sql.py -v 2>&1 | tail -10
```

Expected: ImportError on `build_exclude_sql_clauses`.

- [ ] **Step 3: Implement builder (tag branch intentionally absent until Task 5)**

Append to `scripts/exclude_filter.py`:
```python
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

    # Tag branch implemented in Task 5
    for tag_excl in flt.tag_exclusions:
        warnings.append(f"tag filter for key '{tag_excl.key}' not yet wired")

    return clauses, warnings
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_exclude_filter_sql.py -v 2>&1 | tail -10
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/exclude_filter.py tests/test_exclude_filter_sql.py
git commit -m "feat(aws-sp-optimizer): build SQL clauses for usage_type & account_id axes"
```

---

## Task 5: SQL clause builder — tag axis with column discovery awareness

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/exclude_filter.py`
- Modify: `skills/aws-sp-optimizer/tests/test_exclude_filter_sql.py`

- [ ] **Step 1: Extend the failing test**

Append to `tests/test_exclude_filter_sql.py`:
```python
def test_tag_filter_with_include_untagged_true():
    f = ExcludeFilter(
        tag_exclusions=(
            TagExclusion(key="lifecycle", values=("ephemeral", "experimental")),
        ),
        include_untagged=True,
    )
    cols = frozenset({"resource_tags_user_lifecycle"})
    clauses, warnings = build_exclude_sql_clauses(f, cols)
    assert clauses == [
        "(resource_tags_user_lifecycle IS NULL OR "
        "resource_tags_user_lifecycle NOT IN ('ephemeral', 'experimental'))"
    ]
    assert warnings == []


def test_tag_filter_with_include_untagged_false():
    f = ExcludeFilter(
        tag_exclusions=(TagExclusion(key="lifecycle", values=("ephemeral",)),),
        include_untagged=False,
    )
    cols = frozenset({"resource_tags_user_lifecycle"})
    clauses, _ = build_exclude_sql_clauses(f, cols)
    assert clauses == [
        "resource_tags_user_lifecycle IS NOT NULL",
        "resource_tags_user_lifecycle NOT IN ('ephemeral')",
    ]


def test_tag_filter_skips_missing_column_and_warns():
    f = ExcludeFilter(
        tag_exclusions=(
            TagExclusion(key="lifecycle", values=("ephemeral",)),
            TagExclusion(key="nonexistent", values=("x",)),
        ),
    )
    cols = frozenset({"resource_tags_user_lifecycle"})
    clauses, warnings = build_exclude_sql_clauses(f, cols)
    assert len(clauses) == 1
    assert "resource_tags_user_lifecycle" in clauses[0]
    assert len(warnings) == 1
    assert "nonexistent" in warnings[0]
    assert "resource_tags_user_nonexistent" in warnings[0]


def test_tag_key_is_normalized_before_column_lookup():
    f = ExcludeFilter(
        tag_exclusions=(TagExclusion(key="cost-category", values=("research",)),),
    )
    cols = frozenset({"resource_tags_user_cost_category"})
    clauses, warnings = build_exclude_sql_clauses(f, cols)
    assert len(clauses) == 1
    assert "resource_tags_user_cost_category" in clauses[0]
    assert warnings == []
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_exclude_filter_sql.py -v 2>&1 | tail -20
```

Expected: 4 new tests FAIL (placeholder warning from Task 4).

- [ ] **Step 3: Replace the placeholder tag branch**

In `scripts/exclude_filter.py`, replace the `# Tag branch implemented in Task 5` block:
```python
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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_exclude_filter_sql.py -v 2>&1 | tail -10
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/exclude_filter.py tests/test_exclude_filter_sql.py
git commit -m "feat(aws-sp-optimizer): add tag-axis SQL clauses with column-presence awareness"
```

---

## Task 6: Tag column discovery via information_schema

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/exclude_filter.py`
- Create: `skills/aws-sp-optimizer/tests/test_exclude_filter_tag_columns.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_exclude_filter_tag_columns.py`:
```python
from unittest.mock import patch

from scripts._common import OrgConfig
from scripts.exclude_filter import fetch_available_tag_columns


def _cfg():
    return OrgConfig(
        alias="t",
        description="t",
        profile="p",
        payer_account_id="002497567426",
        org_id="o-test",
        cur_database="db",
        cur_table="cur",
        athena_output="s3://x/",
        athena_workgroup="primary",
        primary_region="us-east-1",
    )


def test_fetch_returns_frozenset_of_matching_columns():
    rows = [
        {"column_name": "resource_tags_user_lifecycle"},
        {"column_name": "resource_tags_user_cost_category"},
        {"column_name": "line_item_usage_type"},  # non-tag should not leak
    ]
    with patch("scripts.exclude_filter.athena_execute", return_value=rows):
        cols = fetch_available_tag_columns(_cfg())
    assert cols == frozenset(
        {"resource_tags_user_lifecycle", "resource_tags_user_cost_category"}
    )
    assert isinstance(cols, frozenset)


def test_fetch_returns_empty_when_no_tag_columns():
    with patch("scripts.exclude_filter.athena_execute", return_value=[]):
        cols = fetch_available_tag_columns(_cfg())
    assert cols == frozenset()


def test_fetch_is_tolerant_of_athena_failure():
    """information_schema should be best-effort: on error, return empty + warn."""
    with patch(
        "scripts.exclude_filter.athena_execute", side_effect=RuntimeError("boom")
    ):
        cols = fetch_available_tag_columns(_cfg())
    assert cols == frozenset()
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_exclude_filter_tag_columns.py -v 2>&1 | tail -10
```

Expected: ImportError on `fetch_available_tag_columns`.

- [ ] **Step 3: Implement fetch**

Append to `scripts/exclude_filter.py`:
```python
import logging

from scripts._common import OrgConfig, athena_execute

_log = logging.getLogger(__name__)

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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_exclude_filter_tag_columns.py -v 2>&1 | tail -10
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/exclude_filter.py tests/test_exclude_filter_tag_columns.py
git commit -m "feat(aws-sp-optimizer): discover CUR tag columns via information_schema"
```

---

## Task 7: Thread filter through `discover_workload_regions`

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/cur_query.py:12-74`
- Modify: `skills/aws-sp-optimizer/tests/test_discover_workload_regions.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_discover_workload_regions.py` (after existing tests):
```python
from scripts.exclude_filter import ExcludeFilter


def test_discover_workload_regions_injects_exclude_filter_clauses(monkeypatch):
    captured_queries: list[str] = []

    def fake_athena_execute(_cfg, q):
        captured_queries.append(q)
        return [{"region": "us-east-1", "total_cost": "100.0"}]

    monkeypatch.setattr("scripts.cur_query.athena_execute", fake_athena_execute)

    from scripts.cur_query import discover_workload_regions
    from datetime import datetime, timezone

    flt = ExcludeFilter(
        usage_type_patterns=("g4dn.",),
        account_ids=("123456789012",),
    )
    discover_workload_regions(
        _make_cfg(),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 10, tzinfo=timezone.utc),
        exclude_filter=flt,
        available_tag_columns=frozenset(),
    )
    assert len(captured_queries) == 1
    q = captured_queries[0]
    assert "NOT LIKE '%g4dn.%'" in q
    assert "NOT IN ('123456789012')" in q


def test_discover_workload_regions_default_empty_filter(monkeypatch):
    """Default call (no filter arg) must preserve existing SQL byte-for-byte."""
    captured: list[str] = []
    monkeypatch.setattr(
        "scripts.cur_query.athena_execute",
        lambda cfg, q: captured.append(q) or [],
    )

    from scripts.cur_query import discover_workload_regions
    from datetime import datetime, timezone

    discover_workload_regions(
        _make_cfg(),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 10, tzinfo=timezone.utc),
    )
    assert "NOT LIKE" not in captured[0] or captured[0].count("NOT LIKE") == 1
    # one existing NOT LIKE for SpotUsage; no extras
    assert captured[0].count("NOT LIKE") == 1
```

Add helper `_make_cfg()` at top of file if missing (copy pattern from existing test).

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_discover_workload_regions.py -v 2>&1 | tail -15
```

Expected: `TypeError: discover_workload_regions() got an unexpected keyword argument 'exclude_filter'`.

- [ ] **Step 3: Extend `discover_workload_regions` + SQL template**

In `scripts/cur_query.py`, update `_REGION_DISCOVERY_SQL_TEMPLATE` to add `{exclude_clauses}` placeholder after the workload whitelist block:
```python
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
      )
  {exclude_clauses}
GROUP BY product_region_code
ORDER BY total_cost DESC
"""
```

Update `discover_workload_regions`:
```python
from scripts.exclude_filter import ExcludeFilter, build_exclude_sql_clauses


def discover_workload_regions(
    config: OrgConfig,
    window_start: datetime,
    window_end: datetime,
    exclude_filter: ExcludeFilter | None = None,
    available_tag_columns: frozenset[str] | None = None,
) -> list[tuple[str, float]]:
    """... (keep existing docstring; add line describing filter) ..."""
    assert window_start.tzinfo is not None, "window_start must be tz-aware"
    assert window_end.tzinfo is not None, "window_end must be tz-aware"
    partition_filter = generate_partition_filter(window_start, window_end)
    start_lit = window_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_lit = window_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    exclude_filter = exclude_filter or ExcludeFilter()
    available_tag_columns = available_tag_columns or frozenset()
    clauses, _ = build_exclude_sql_clauses(exclude_filter, available_tag_columns)
    exclude_clauses = (
        "\n  AND " + "\n  AND ".join(clauses) if clauses else ""
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
    # ... rest of body unchanged ...
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_discover_workload_regions.py -v 2>&1 | tail -10
```

Expected: all pass (existing tests still green — default-arg path preserves behavior).

- [ ] **Step 5: Commit**

```bash
git add scripts/cur_query.py tests/test_discover_workload_regions.py
git commit -m "feat(aws-sp-optimizer): thread ExcludeFilter through discover_workload_regions"
```

---

## Task 8: Thread filter through `build_hourly_series`

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/cur_query.py:76-193`
- Modify: `skills/aws-sp-optimizer/tests/test_cur_query.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cur_query.py`:
```python
from scripts.exclude_filter import ExcludeFilter, TagExclusion


def test_build_hourly_series_injects_filter_clauses(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(
        "scripts.cur_query.athena_execute",
        lambda cfg, q: captured.append(q) or [],
    )

    from scripts.cur_query import build_hourly_series
    from datetime import datetime, timezone

    flt = ExcludeFilter(
        usage_type_patterns=("g4dn.",),
        account_ids=("123456789012",),
        tag_exclusions=(TagExclusion(key="lifecycle", values=("ephemeral",)),),
    )
    build_hourly_series(
        _make_cfg(),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 1, 10, 5, tzinfo=timezone.utc),
        e_sp=0.09,
        exclude_filter=flt,
        available_tag_columns=frozenset({"resource_tags_user_lifecycle"}),
    )
    q = captured[0]
    assert "NOT LIKE '%g4dn.%'" in q
    assert "NOT IN ('123456789012')" in q
    assert "resource_tags_user_lifecycle" in q
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_cur_query.py -k build_hourly_series -v 2>&1 | tail -10
```

Expected: `TypeError: build_hourly_series() got an unexpected keyword argument 'exclude_filter'`.

- [ ] **Step 3: Thread filter through `_CUR_SQL_TEMPLATE` + function**

Add `{exclude_clauses}` placeholder to `_CUR_SQL_TEMPLATE` after the workload whitelist:
```python
_CUR_SQL_TEMPLATE = """
SELECT
    ...
FROM "{cur_database}"."{cur_table}"
WHERE {partition_filter}
  AND ...
  AND (
        (line_item_product_code = 'AmazonEC2' AND ...)
        OR ...
      )
  {exclude_clauses}
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
"""
```

Update `build_hourly_series`:
```python
def build_hourly_series(
    config: OrgConfig,
    window_start: datetime,
    window_end: datetime,
    e_sp: float,
    exclude_filter: ExcludeFilter | None = None,
    available_tag_columns: frozenset[str] | None = None,
) -> HourlySeries:
    assert window_start.tzinfo is not None
    assert window_end.tzinfo is not None
    partition_filter = generate_partition_filter(window_start, window_end)
    start_lit = window_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_lit = window_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    exclude_filter = exclude_filter or ExcludeFilter()
    available_tag_columns = available_tag_columns or frozenset()
    clauses, _ = build_exclude_sql_clauses(exclude_filter, available_tag_columns)
    exclude_clauses = (
        "\n  AND " + "\n  AND ".join(clauses) if clauses else ""
    )
    query = _CUR_SQL_TEMPLATE.format(
        cur_database=config.cur_database,
        cur_table=config.cur_table,
        partition_filter=partition_filter,
        max_window_start=start_lit,
        max_window_end=end_lit,
        exclude_clauses=exclude_clauses,
    )
    # ... rest unchanged ...
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_cur_query.py -v 2>&1 | tail -10
```

Expected: all existing + new tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/cur_query.py tests/test_cur_query.py
git commit -m "feat(aws-sp-optimizer): thread ExcludeFilter through build_hourly_series"
```

---

## Task 9: Thread filter through `compute_edp_factors` (OD side only)

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/cur_query.py:196-311`

**Rationale:** `_EDP_OD_SQL` computes e_od = EDP discount on OD Usage rows. This rate should reflect the **retained** workload, because d_calibrated = f(d_raw, e_sp, e_od) must be consistent with the filtered X. The SP fee query `_EDP_SP_SQL` does NOT get the filter — SP fee rows don't have a usage_type/instance_type/tag dimension in CUR (they are org-level recurring fees), so filter is a no-op there.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cur_query.py`:
```python
def test_compute_edp_factors_applies_filter_to_od_only(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(
        "scripts.cur_query.athena_execute",
        lambda cfg, q: captured.append(q) or [
            {"e_od_unblended": "100", "e_od_net": "91"},
            {"e_sp_unblended": "100", "e_sp_net": "91"},
        ][len(captured) - 1 : len(captured)],
    )

    from scripts.cur_query import compute_edp_factors
    from datetime import datetime, timezone

    compute_edp_factors(
        _make_cfg(),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 10, tzinfo=timezone.utc),
        exclude_filter=ExcludeFilter(usage_type_patterns=("g4dn.",)),
        available_tag_columns=frozenset(),
    )
    assert len(captured) == 2
    # OD query has filter:
    assert "NOT LIKE '%g4dn.%'" in captured[0]
    # SP fee query does NOT have filter:
    assert "NOT LIKE '%g4dn.%'" not in captured[1]
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_cur_query.py -k compute_edp_factors_applies_filter -v 2>&1 | tail -10
```

Expected: TypeError on unexpected kwarg.

- [ ] **Step 3: Extend `_EDP_OD_SQL` with `{exclude_clauses}` and update `compute_edp_factors`**

In `_EDP_OD_SQL`, add `{exclude_clauses}` at end:
```python
_EDP_OD_SQL = """
SELECT ...
FROM ...
WHERE ...
  AND (
        (line_item_product_code = 'AmazonEC2' AND ...)
        OR ...
      )
  {exclude_clauses}
"""
```

Update `compute_edp_factors`:
```python
def compute_edp_factors(
    config: OrgConfig,
    window_start: datetime,
    window_end: datetime,
    exclude_filter: ExcludeFilter | None = None,
    available_tag_columns: frozenset[str] | None = None,
) -> tuple[float, float, dict]:
    """... (keep existing docstring) ..."""
    assert window_start.tzinfo is not None
    assert window_end.tzinfo is not None
    partition_filter = generate_partition_filter(window_start, window_end)
    start_lit = window_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_lit = window_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    exclude_filter = exclude_filter or ExcludeFilter()
    available_tag_columns = available_tag_columns or frozenset()
    clauses, _ = build_exclude_sql_clauses(exclude_filter, available_tag_columns)
    exclude_clauses = (
        "\n  AND " + "\n  AND ".join(clauses) if clauses else ""
    )
    fmt = dict(
        cur_database=config.cur_database,
        cur_table=config.cur_table,
        partition_filter=partition_filter,
        start=start_lit,
        end=end_lit,
        exclude_clauses=exclude_clauses,
    )
    od_rows = athena_execute(config, _EDP_OD_SQL.format(**fmt))
    # SP fee query intentionally skips exclude filter (no usage-level dimension)
    sp_rows = athena_execute(
        config,
        _EDP_SP_SQL.format(
            cur_database=config.cur_database,
            cur_table=config.cur_table,
            partition_filter=partition_filter,
            start=start_lit,
            end=end_lit,
        ),
    )
    # ... rest unchanged ...
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_cur_query.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/cur_query.py tests/test_cur_query.py
git commit -m "feat(aws-sp-optimizer): apply ExcludeFilter to e_od query (SP fee query exempt)"
```

---

## Task 10: Thread filter through `compute_untagged_fraction`

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/_common.py:772-830`
- Modify: `skills/aws-sp-optimizer/tests/test_compute_untagged_fraction.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_compute_untagged_fraction.py`:
```python
from scripts.exclude_filter import ExcludeFilter


def test_compute_untagged_fraction_applies_filter(monkeypatch):
    captured: list[str] = []

    def fake(_cfg, q):
        captured.append(q)
        return [{"untagged_cost": "10", "total_cost": "100"}]

    monkeypatch.setattr("scripts._common.athena_execute", fake)

    from scripts._common import compute_untagged_fraction
    # build a ValidationContext with cur_table_has_resource_tags_user_service=True
    # and a ChosenWindow — use existing fixture pattern from other tests in this
    # file (copy minimum scaffolding)

    # ... construct vc, cw (see existing tests in this file for the pattern) ...

    compute_untagged_fraction(
        cfg=_cfg,
        validation_context=vc,
        chosen_window=cw,
        exclude_filter=ExcludeFilter(usage_type_patterns=("g4dn.",)),
        available_tag_columns=frozenset(),
    )
    assert "NOT LIKE '%g4dn.%'" in captured[0]
```

(Copy scaffolding for `vc`, `cw`, `_cfg` from the top of the same test file.)

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_compute_untagged_fraction.py -v 2>&1 | tail -10
```

Expected: `TypeError: compute_untagged_fraction() got an unexpected keyword argument`.

- [ ] **Step 3: Extend `compute_untagged_fraction` signature + SQL**

In `scripts/_common.py`, add the filter kwargs and inject `{exclude_clauses}` into the inline query (the f-string around line 789):
```python
from scripts.exclude_filter import (  # noqa: E402 — late import avoids cycle
    ExcludeFilter,
    build_exclude_sql_clauses,
)


def compute_untagged_fraction(
    cfg: "OrgConfig",
    validation_context,
    chosen_window,
    exclude_filter: ExcludeFilter | None = None,
    available_tag_columns: frozenset[str] | None = None,
) -> float:
    """... existing docstring unchanged ..."""
    if not validation_context.cur_table_has_resource_tags_user_service:
        return 0.0

    partition_filter = generate_partition_filter(chosen_window.start, chosen_window.end)
    exclude_filter = exclude_filter or ExcludeFilter()
    available_tag_columns = available_tag_columns or frozenset()
    clauses, _ = build_exclude_sql_clauses(exclude_filter, available_tag_columns)
    exclude_clauses = (
        "\n          AND " + "\n          AND ".join(clauses) if clauses else ""
    )
    query = f"""
        SELECT
            ...  -- unchanged body ...
          AND (
                (line_item_product_code = 'AmazonEC2' AND ...)
                OR ...
              ){exclude_clauses}
    """
    # rest unchanged
```

(Paste the full existing SQL body, inserting the `{exclude_clauses}` interpolation after the closing paren of the workload whitelist.)

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_compute_untagged_fraction.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/_common.py tests/test_compute_untagged_fraction.py
git commit -m "feat(aws-sp-optimizer): apply ExcludeFilter to compute_untagged_fraction"
```

---

## Task 11: New helper `compute_sp_coverage_share` (CUR query)

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/_common.py`
- Create: `skills/aws-sp-optimizer/tests/test_sp_coverage_share.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sp_coverage_share.py`:
```python
from datetime import datetime, timezone
from unittest.mock import patch

from scripts._common import compute_sp_coverage_share, OrgConfig
from scripts.exclude_filter import ExcludeFilter


def _cfg():
    return OrgConfig(
        alias="t", description="t", profile="p",
        payer_account_id="002497567426", org_id="o-t",
        cur_database="db", cur_table="cur",
        athena_output="s3://x/", athena_workgroup="primary",
        primary_region="us-east-1",
    )


def _ts(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def test_empty_filter_returns_one():
    """No filter ⇒ share = 1.0, no CUR query executed."""
    with patch("scripts._common.athena_execute") as mock:
        share, audit = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            ExcludeFilter(), frozenset(),
        )
    assert share == 1.0
    assert audit["total_cost_usd"] is None  # short-circuit
    mock.assert_not_called()


def test_filter_shrinks_share_proportionally():
    rows = [{"retained_cost": "600", "total_cost": "1000"}]
    with patch("scripts._common.athena_execute", return_value=rows):
        share, audit = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            ExcludeFilter(account_ids=("123456789012",)),
            frozenset(),
        )
    assert share == 0.6
    assert audit["retained_cost_usd"] == 600.0
    assert audit["total_cost_usd"] == 1000.0


def test_zero_sp_covered_usage_returns_one():
    """No SP consumption in window ⇒ share = 1.0 (no basis for reduction)."""
    rows = [{"retained_cost": "0", "total_cost": "0"}]
    with patch("scripts._common.athena_execute", return_value=rows):
        share, audit = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            ExcludeFilter(account_ids=("123456789012",)),
            frozenset(),
        )
    assert share == 1.0
    assert audit["total_cost_usd"] == 0.0


def test_share_clamped_to_01():
    rows = [{"retained_cost": "1200", "total_cost": "1000"}]  # pathological
    with patch("scripts._common.athena_execute", return_value=rows):
        share, _ = compute_sp_coverage_share(
            _cfg(), _ts(2026, 3, 11), _ts(2026, 4, 10),
            ExcludeFilter(account_ids=("123456789012",)),
            frozenset(),
        )
    assert share == 1.0
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_sp_coverage_share.py -v 2>&1 | tail -10
```

Expected: ImportError on `compute_sp_coverage_share`.

- [ ] **Step 3: Implement**

Append to `scripts/_common.py`:
```python
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
    rows = athena_execute(config, query)
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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_sp_coverage_share.py -v 2>&1 | tail -10
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/_common.py tests/test_sp_coverage_share.py
git commit -m "feat(aws-sp-optimizer): compute_sp_coverage_share attenuator helper"
```

---

## Task 12: Wire `sp_coverage_share` into `get_existing_sp_effective`

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/newsvendor.py:33-100` (approx)
- Modify: `skills/aws-sp-optimizer/scripts/_common.py` (SPAudit dataclass)
- Modify: `skills/aws-sp-optimizer/tests/test_get_existing_sp_effective.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_get_existing_sp_effective.py`:
```python
def test_sp_coverage_share_attenuates_effective():
    """effective = commit × util × share."""
    sps = [_sp(kind="Compute", commitment=10.0)]
    with patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce:
        mock_ce.return_value = {
            "Total": {"Utilization": {"UtilizationPercentage": "100.0"}}
        }
        effective, audit = get_existing_sp_effective(
            profile="p",
            all_relevant_sps=sps,
            window_end=_ts(2026, 4, 10),
            sp_coverage_share=0.6,
        )
    assert effective == 6.0  # 10 × 1.0 × 0.6
    assert audit.sp_coverage_share == 0.6


def test_sp_coverage_share_default_is_one():
    """No arg ⇒ share defaults to 1.0, behavior identical to pre-filter."""
    sps = [_sp(kind="Compute", commitment=10.0)]
    with patch("scripts.newsvendor.ce_get_savings_plans_utilization") as mock_ce:
        mock_ce.return_value = {
            "Total": {"Utilization": {"UtilizationPercentage": "100.0"}}
        }
        effective, audit = get_existing_sp_effective(
            profile="p", all_relevant_sps=sps, window_end=_ts(2026, 4, 10),
        )
    assert effective == 10.0
    assert audit.sp_coverage_share == 1.0
```

(Use existing `_sp`, `_ts` scaffolding at top of file.)

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_get_existing_sp_effective.py -v 2>&1 | tail -10
```

Expected: TypeError on unexpected kwarg.

- [ ] **Step 3: Extend `SPAudit` and `get_existing_sp_effective`**

In `scripts/_common.py`, add field to `SPAudit`:
```python
@dataclass
class SPAudit:
    count: int
    compute_count: int
    non_compute_count: int
    total_commitment_all: float
    compute_commitment: float
    utilization_30d: float
    utilization_source: str
    effective: float
    has_non_compute_sps: bool
    active_sp_list: list[dict]
    sp_coverage_share: float = 1.0  # NEW: fraction of SP effective coverage retained under filter
```

In `scripts/newsvendor.py`, update `get_existing_sp_effective`:
```python
def get_existing_sp_effective(
    profile: str,
    all_relevant_sps: list[SPInfo],
    window_end: datetime,
    sp_coverage_share: float = 1.0,
) -> tuple[float, SPAudit]:
    """Compute effective SP coverage. See spec 5.7.

    When an exclude filter is active, sp_coverage_share < 1.0 attenuates the
    effective coverage: effective = compute_commitment × utilization_pct × share.
    Caller must pass the share computed from compute_sp_coverage_share().
    """
    # ... existing body ...

    effective = compute_commitment * utilization_pct * sp_coverage_share

    # ... when building SPAudit, pass sp_coverage_share=sp_coverage_share ...
    return effective, SPAudit(
        count=...,
        ...existing fields...,
        sp_coverage_share=sp_coverage_share,
    )
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_get_existing_sp_effective.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/newsvendor.py scripts/_common.py tests/test_get_existing_sp_effective.py
git commit -m "feat(aws-sp-optimizer): attenuate C_existing_effective by sp_coverage_share"
```

---

## Task 13: d_raw consistency regression test

**Files:**
- Modify: `skills/aws-sp-optimizer/tests/test_compute_d_blended.py`

**Rationale:** `compute_d_blended` reads only from `series.data[h]["mix"]`, which is already populated from filtered CUR rows. So d_raw consistency is automatic — but verify with an explicit test so a future refactor doesn't regress.

- [ ] **Step 1: Write the regression test**

Append to `tests/test_compute_d_blended.py`:
```python
def test_d_blended_reads_only_from_series_so_filter_transparent():
    """Sanity: compute_d_blended uses series.data[...]['mix'] only.

    If future refactor adds a separate CUR fetch inside compute_d_blended, this
    test should fail by making the test visibly dependent on a second data
    source. Keep this marker in the suite.
    """
    from scripts.newsvendor import compute_d_blended
    import inspect

    src = inspect.getsource(compute_d_blended)
    # Guard: no new CUR fetching primitives sneaking in
    assert "athena_execute" not in src
    assert "SELECT" not in src.upper()  # no embedded SQL
    # Guard: still reads from series (the filtered input)
    assert "series.hours" in src or "series.data" in src
```

- [ ] **Step 2: Run test**

```bash
python -m pytest tests/test_compute_d_blended.py -v 2>&1 | tail -10
```

Expected: all pass (no code change needed).

- [ ] **Step 3: Commit**

```bash
git add tests/test_compute_d_blended.py
git commit -m "test(aws-sp-optimizer): regression — d_raw stays filter-transparent"
```

---

## Task 14: `ExcludeFilter` in `OrgConfig`

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/_common.py` (OrgConfig)
- Modify: `skills/aws-sp-optimizer/scripts/config_loader.py`
- Modify: `skills/aws-sp-optimizer/tests/test_config_loader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config_loader.py`:
```python
import textwrap


def test_config_loader_parses_exclude_block(tmp_path):
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
            exclude:
              usage_type_patterns: ["g4dn.", "p4d."]
              account_ids: ["123456789012"]
              tags:
                - key: lifecycle
                  values: [ephemeral, experimental]
              include_untagged: false
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)

    from scripts.config_loader import load_config
    cfg = load_config(str(p), "t")
    assert cfg.exclude.usage_type_patterns == ("g4dn.", "p4d.")
    assert cfg.exclude.account_ids == ("123456789012",)
    assert len(cfg.exclude.tag_exclusions) == 1
    assert cfg.exclude.tag_exclusions[0].key == "lifecycle"
    assert cfg.exclude.tag_exclusions[0].values == ("ephemeral", "experimental")
    assert cfg.exclude.include_untagged is False


def test_config_loader_defaults_exclude_to_empty(tmp_path):
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)

    from scripts.config_loader import load_config
    cfg = load_config(str(p), "t")
    assert cfg.exclude.is_empty() is True


def test_config_loader_rejects_invalid_pattern(tmp_path):
    cfg_text = textwrap.dedent("""\
        schema_version: 1
        orgs:
          t:
            description: test
            profile: p
            payer_account_id: "002497567426"
            org_id: o-test
            cur_database: db
            cur_table: cur
            athena_output: s3://x/
            athena_workgroup: primary
            primary_region: us-east-1
            exclude:
              usage_type_patterns: ["has space"]
    """)
    p = tmp_path / "orgs.yaml"
    p.write_text(cfg_text)

    from scripts.config_loader import load_config, ConfigError
    with pytest.raises(ConfigError):
        load_config(str(p), "t")
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_config_loader.py -v 2>&1 | tail -10
```

Expected: `AttributeError: OrgConfig has no attribute 'exclude'`.

- [ ] **Step 3: Extend `OrgConfig` + config_loader**

In `scripts/_common.py`, extend `OrgConfig`:
```python
from scripts.exclude_filter import ExcludeFilter  # put at top of file


@dataclass
class OrgConfig:
    alias: str
    description: str
    profile: str
    payer_account_id: str
    org_id: str
    cur_database: str
    cur_table: str
    athena_output: str
    athena_workgroup: str
    primary_region: str
    defaults: dict = field(default_factory=dict)
    exclude: ExcludeFilter = field(default_factory=ExcludeFilter)  # NEW
```

In `scripts/config_loader.py`, add a parsing helper + wire it into `load_config`:
```python
from scripts.exclude_filter import (
    ExcludeFilter,
    TagExclusion,
    validate_usage_type_pattern,
    validate_account_id,
    validate_tag_key,
    validate_tag_value,
)


class ConfigError(ValueError):
    pass


def _parse_exclude(raw: dict | None) -> ExcludeFilter:
    if not raw:
        return ExcludeFilter()
    try:
        patterns = tuple(raw.get("usage_type_patterns", []) or [])
        for p in patterns:
            validate_usage_type_pattern(p)
        account_ids = tuple(raw.get("account_ids", []) or [])
        for a in account_ids:
            validate_account_id(a)
        tag_entries = raw.get("tags", []) or []
        tag_exclusions = []
        for entry in tag_entries:
            k = entry["key"]
            vals = tuple(entry.get("values", []) or [])
            validate_tag_key(k)
            for v in vals:
                validate_tag_value(v)
            tag_exclusions.append(TagExclusion(key=k, values=vals))
        include_untagged = bool(raw.get("include_untagged", True))
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"invalid exclude block: {e}") from e
    return ExcludeFilter(
        usage_type_patterns=patterns,
        account_ids=account_ids,
        tag_exclusions=tuple(tag_exclusions),
        include_untagged=include_untagged,
    )
```

Wire into `load_config` body — pass `exclude=_parse_exclude(raw_org.get("exclude"))` to `OrgConfig(...)`.

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_config_loader.py -v 2>&1 | tail -10
```

Expected: all 3 new + existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/_common.py scripts/config_loader.py tests/test_config_loader.py
git commit -m "feat(aws-sp-optimizer): parse exclude: block in orgs.yaml"
```

---

## Task 15: CLI flags for exclude axes

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/aws_sp_optimizer.py` (parse_args)
- Modify: `skills/aws-sp-optimizer/tests/test_parse_args.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parse_args.py`:
```python
def test_parse_exclude_flags():
    from scripts.aws_sp_optimizer import parse_args

    args = parse_args([
        "--org", "a4x-us",
        "--exclude-usage-type-pattern", "g4dn.",
        "--exclude-usage-type-pattern", "p4d.",
        "--exclude-account-id", "123456789012",
        "--exclude-account-id", "987654321098",
        "--exclude-tag", "lifecycle=ephemeral,experimental",
        "--exclude-tag", "cost-category=research",
        "--exclude-untagged",
    ])
    assert args.exclude_usage_type_pattern == ["g4dn.", "p4d."]
    assert args.exclude_account_id == ["123456789012", "987654321098"]
    assert args.exclude_tag == [
        "lifecycle=ephemeral,experimental",
        "cost-category=research",
    ]
    assert args.include_untagged is False


def test_default_include_untagged_is_true():
    from scripts.aws_sp_optimizer import parse_args
    args = parse_args(["--org", "a4x-us"])
    assert args.exclude_usage_type_pattern == []
    assert args.exclude_account_id == []
    assert args.exclude_tag == []
    assert args.include_untagged is True
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_parse_args.py -v 2>&1 | tail -10
```

Expected: `AttributeError: Namespace has no attribute 'exclude_usage_type_pattern'`.

- [ ] **Step 3: Add flags to parser**

In `scripts/aws_sp_optimizer.py` `parse_args`:
```python
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
    untagged.add_argument(
        "--include-untagged", dest="include_untagged", action="store_true",
        default=True,
        help="(Default) rows with NULL value for a filtered tag column are kept.",
    )
    untagged.add_argument(
        "--exclude-untagged", dest="include_untagged", action="store_false",
        help="Rows with NULL value for any filtered tag column are excluded.",
    )
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_parse_args.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/aws_sp_optimizer.py tests/test_parse_args.py
git commit -m "feat(aws-sp-optimizer): CLI flags for exclude axes"
```

---

## Task 16: Merge CLI flags with config-level filter

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/aws_sp_optimizer.py`
- Create: `skills/aws-sp-optimizer/tests/test_merge_cli_filter.py`

**Rule:** CLI is **additive** on top of config. Both config and CLI axes combine with set-union. `include_untagged` from CLI wins if either flag is explicitly passed (default preserves config).

- [ ] **Step 1: Write the failing test**

Create `tests/test_merge_cli_filter.py`:
```python
import argparse
from scripts.aws_sp_optimizer import merge_cli_into_config_filter
from scripts.exclude_filter import ExcludeFilter, TagExclusion


def _args(**kw):
    defaults = dict(
        exclude_usage_type_pattern=[],
        exclude_account_id=[],
        exclude_tag=[],
        include_untagged=True,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_empty_cli_returns_config_unchanged():
    cfg_flt = ExcludeFilter(usage_type_patterns=("g4dn.",))
    merged = merge_cli_into_config_filter(cfg_flt, _args())
    assert merged == cfg_flt


def test_cli_patterns_union_with_config_patterns():
    cfg_flt = ExcludeFilter(usage_type_patterns=("g4dn.",))
    merged = merge_cli_into_config_filter(
        cfg_flt, _args(exclude_usage_type_pattern=["p4d."]),
    )
    assert set(merged.usage_type_patterns) == {"g4dn.", "p4d."}


def test_cli_tag_parsing_key_equals_comma_values():
    merged = merge_cli_into_config_filter(
        ExcludeFilter(),
        _args(exclude_tag=["lifecycle=ephemeral,experimental"]),
    )
    assert merged.tag_exclusions == (
        TagExclusion(key="lifecycle", values=("ephemeral", "experimental")),
    )


def test_cli_tag_rejects_malformed():
    import pytest
    from scripts.aws_sp_optimizer import CliFilterError
    with pytest.raises(CliFilterError):
        merge_cli_into_config_filter(
            ExcludeFilter(), _args(exclude_tag=["nokeyequalssign"]),
        )


def test_cli_dedups_when_unioning():
    cfg_flt = ExcludeFilter(
        usage_type_patterns=("g4dn.",),
        account_ids=("123456789012",),
    )
    merged = merge_cli_into_config_filter(
        cfg_flt,
        _args(
            exclude_usage_type_pattern=["g4dn."],
            exclude_account_id=["123456789012"],
        ),
    )
    assert merged.usage_type_patterns == ("g4dn.",)  # deduped
    assert merged.account_ids == ("123456789012",)


def test_cli_include_untagged_false_overrides_config_true():
    cfg_flt = ExcludeFilter(include_untagged=True)
    merged = merge_cli_into_config_filter(cfg_flt, _args(include_untagged=False))
    assert merged.include_untagged is False
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_merge_cli_filter.py -v 2>&1 | tail -10
```

Expected: ImportError on `merge_cli_into_config_filter`.

- [ ] **Step 3: Implement merge helper**

In `scripts/aws_sp_optimizer.py`, add:
```python
from scripts.exclude_filter import (
    ExcludeFilter,
    TagExclusion,
    validate_usage_type_pattern,
    validate_account_id,
    validate_tag_key,
    validate_tag_value,
)


class CliFilterError(ValueError):
    pass


def _parse_cli_tag(spec: str) -> TagExclusion:
    if "=" not in spec:
        raise CliFilterError(
            f"--exclude-tag {spec!r} malformed: expected KEY=V1,V2,..."
        )
    key, _, vals_raw = spec.partition("=")
    validate_tag_key(key)
    vals = tuple(v for v in (s.strip() for s in vals_raw.split(",")) if v)
    if not vals:
        raise CliFilterError(f"--exclude-tag {spec!r} has no values")
    for v in vals:
        validate_tag_value(v)
    return TagExclusion(key=key, values=vals)


def merge_cli_into_config_filter(
    config_filter: ExcludeFilter,
    args,
) -> ExcludeFilter:
    """Union config-level filter with CLI args. CLI is additive."""
    cli_patterns = list(args.exclude_usage_type_pattern or [])
    for p in cli_patterns:
        validate_usage_type_pattern(p)
    cli_accounts = list(args.exclude_account_id or [])
    for a in cli_accounts:
        validate_account_id(a)
    cli_tags = [_parse_cli_tag(s) for s in (args.exclude_tag or [])]

    # Dedup while preserving order (config first, CLI appended)
    def _dedup(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                out.append(x)
                seen.add(x)
        return tuple(out)

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
        include_untagged=args.include_untagged,
    )
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_merge_cli_filter.py -v 2>&1 | tail -10
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/aws_sp_optimizer.py tests/test_merge_cli_filter.py
git commit -m "feat(aws-sp-optimizer): merge CLI flags into config ExcludeFilter"
```

---

## Task 17: Wire filter through `run_optimizer` orchestrator

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/aws_sp_optimizer.py` (run_optimizer)

**Pattern:** At the top of run_optimizer, resolve the effective filter (config ∪ CLI), discover tag columns once, then thread `(filter, tag_columns)` into every CUR-touching call. Compute `sp_coverage_share` once and pass into `get_existing_sp_effective`.

- [ ] **Step 1: Write the end-to-end test (uses heavy mocking)**

Append to `tests/test_run_optimizer.py`:
```python
def test_run_optimizer_threads_filter_through_all_sql_sites(monkeypatch):
    """Smoke: run_optimizer with a non-empty filter causes all wired CUR fns
    to receive exclude_filter + available_tag_columns."""
    captured = {
        "discover_workload_regions": None,
        "build_hourly_series": None,
        "compute_edp_factors": None,
        "compute_untagged_fraction": None,
        "compute_sp_coverage_share": None,
    }

    def _record(key):
        def fn(*a, **kw):
            captured[key] = (a, kw)
            return _fake_return_for(key, a, kw)
        return fn

    # ... use existing run_optimizer test scaffolding to mock out all data
    # sources and let the real orchestrator flow through the filter threading.
    # The assertion is on captured kwargs.

    from scripts.aws_sp_optimizer import run_optimizer
    # call with args carrying --exclude-usage-type-pattern g4dn.
    # then assert captured[...][1].get('exclude_filter').usage_type_patterns == ('g4dn.',)
```

(Build this on top of the existing `test_run_optimizer.py` fixtures — they already mock CUR + boto3. Fill in `_fake_return_for` to return minimal valid shapes that let the pipeline complete.)

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_run_optimizer.py -k threads_filter -v 2>&1 | tail -15
```

Expected: assertion on captured filter fails (filter not yet threaded).

- [ ] **Step 3: Thread filter through run_optimizer**

In `scripts/aws_sp_optimizer.py` `run_optimizer`:
```python
    # ----- Resolve effective filter ------------------------------------------
    effective_filter = merge_cli_into_config_filter(config.exclude, args)
    if effective_filter.tag_exclusions:
        available_tag_columns = fetch_available_tag_columns(config)
    else:
        available_tag_columns = frozenset()

    # ----- Thread through CUR calls ------------------------------------------
    regions = discover_workload_regions(
        config, window_start, window_end,
        exclude_filter=effective_filter,
        available_tag_columns=available_tag_columns,
    )
    # (same pattern for build_hourly_series, compute_edp_factors,
    #  compute_untagged_fraction)

    # ----- Existing-SP attenuation -------------------------------------------
    sp_coverage_share, share_audit = compute_sp_coverage_share(
        config, window_start, window_end,
        effective_filter, available_tag_columns,
    )
    effective, sp_audit = get_existing_sp_effective(
        profile=config.profile,
        all_relevant_sps=all_sps,
        window_end=window_end,
        sp_coverage_share=sp_coverage_share,
    )
```

Import `fetch_available_tag_columns`, `compute_sp_coverage_share`, and `merge_cli_into_config_filter` at top of file.

- [ ] **Step 4: Run full test suite to confirm pass + no regressions**

```bash
python -m pytest tests/ --tb=short 2>&1 | tail -15
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/aws_sp_optimizer.py tests/test_run_optimizer.py
git commit -m "feat(aws-sp-optimizer): thread ExcludeFilter through run_optimizer"
```

---

## Task 18: Output reflection — `applied_exclude_filter` in context

**Files:**
- Modify: `skills/aws-sp-optimizer/scripts/output_builder.py` (context builder)
- Modify: `skills/aws-sp-optimizer/tests/test_build_context_baseline.py` or `test_build_output.py`

- [ ] **Step 1: Write the failing test**

Append appropriate test file:
```python
def test_context_includes_applied_exclude_filter():
    """Context JSON includes applied_exclude_filter with axes + share audit."""
    # build_context with a populated ExcludeFilter
    # assert result["context"]["applied_exclude_filter"] has:
    #   - usage_type_patterns, account_ids, tag_exclusions (normalized list-of-dicts),
    #     include_untagged, sp_coverage_share, sp_coverage_share_audit,
    #     skipped_filter_warnings (from build_exclude_sql_clauses)
    pass  # fill with existing build_context test scaffolding
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_build_output.py tests/test_build_context_baseline.py -v 2>&1 | tail -10
```

Expected: KeyError on `applied_exclude_filter`.

- [ ] **Step 3: Add `applied_exclude_filter` section to context**

In `scripts/output_builder.py` where the `context` dict is built, add:
```python
    if not effective_filter.is_empty():
        _, filter_warnings = build_exclude_sql_clauses(
            effective_filter, available_tag_columns,
        )
        context["applied_exclude_filter"] = {
            "usage_type_patterns": list(effective_filter.usage_type_patterns),
            "account_ids": list(effective_filter.account_ids),
            "tag_exclusions": [
                {"key": t.key, "values": list(t.values)}
                for t in effective_filter.tag_exclusions
            ],
            "include_untagged": effective_filter.include_untagged,
            "sp_coverage_share": sp_coverage_share,
            "sp_coverage_share_audit": share_audit,
            "skipped_filter_warnings": filter_warnings,
        }
    else:
        context["applied_exclude_filter"] = None
```

Also update the `coverage_scope` string (`output_builder.py:567`):
```python
    if effective_filter.is_empty():
        scope = (
            "All EC2 BoxUsage (non-Spot) + Fargate + Lambda across all member "
            f"accounts in Org {org_id}"
        )
    else:
        axes = []
        if effective_filter.usage_type_patterns:
            axes.append(
                "excl. usage_type patterns ["
                + ", ".join(effective_filter.usage_type_patterns)
                + "]"
            )
        if effective_filter.account_ids:
            axes.append(
                "excl. accounts [" + ", ".join(effective_filter.account_ids) + "]"
            )
        if effective_filter.tag_exclusions:
            axes.append(
                "excl. tags ["
                + ", ".join(
                    f"{t.key}={','.join(t.values)}"
                    for t in effective_filter.tag_exclusions
                )
                + "]"
            )
        scope = (
            "EC2 BoxUsage (non-Spot) + Fargate + Lambda across Org "
            f"{org_id}, filtered: {'; '.join(axes)}"
        )
```

Thread `effective_filter`, `available_tag_columns`, `sp_coverage_share`, `share_audit` into `build_output` from `run_optimizer`.

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_build_output.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/output_builder.py tests/test_build_output.py tests/test_build_context_baseline.py
git commit -m "feat(aws-sp-optimizer): reflect applied filter in context + coverage_scope"
```

---

## Task 19: End-to-end integration test (all 3 axes together)

**Files:**
- Create: `skills/aws-sp-optimizer/tests/test_end_to_end_with_filters.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_end_to_end_with_filters.py`:
```python
"""End-to-end: all 3 exclude axes active simultaneously.

Mocks all boto3 + Athena calls; asserts:
  1. All 5 CUR SQL sites receive combined filter clauses
  2. information_schema is queried for tag columns exactly once
  3. Output JSON exposes applied_exclude_filter section
  4. C_existing_effective is attenuated by sp_coverage_share
  5. When a tag column is missing, warning appears in output, SQL omits that
     filter, other axes still apply
"""
import json
from unittest.mock import patch

import pytest


@pytest.fixture
def mocked_athena():
    """Capture all Athena queries; return canned responses keyed by SQL fingerprint."""
    captured = []

    def fake(cfg, query):
        captured.append(query)
        if "information_schema.columns" in query:
            return [{"column_name": "resource_tags_user_lifecycle"}]
        if "total_cost" in query and "retained_cost" in query:
            # sp_coverage_share
            return [{"retained_cost": "700", "total_cost": "1000"}]
        if "product_region_code AS region" in query:
            return [{"region": "us-east-1", "total_cost": "1000"}]
        if "usage_hour" in query:
            # HourlySeries rows
            return _fake_cur_rows()
        if "e_od_unblended" in query:
            return [{"e_od_unblended": "100", "e_od_net": "91"}]
        if "e_sp_unblended" in query:
            return [{"e_sp_unblended": "100", "e_sp_net": "91"}]
        if "untagged_cost" in query:
            return [{"untagged_cost": "5", "total_cost": "1000"}]
        return []

    with patch("scripts._common.athena_execute", side_effect=fake), \
         patch("scripts.cur_query.athena_execute", side_effect=fake), \
         patch("scripts.exclude_filter.athena_execute", side_effect=fake):
        yield captured


def test_all_three_axes_active(mocked_athena, mock_boto3_sps):
    """..."""
    # Build CLI args: --exclude-usage-type-pattern g4dn.
    #                 --exclude-account-id 123456789012
    #                 --exclude-tag lifecycle=ephemeral
    #                 --exclude-tag nonexistent=x    (will be skipped)
    # Run run_optimizer
    # Parse output JSON
    # Assert context.applied_exclude_filter matches expectations
    # Assert sp_coverage_share = 0.7
    # Assert recommendation.C_existing_effective_per_hour = raw × 0.7
    # Assert skipped_filter_warnings contains "resource_tags_user_nonexistent"
    # Assert every captured CUR SQL (except SP fee + info_schema) contains
    #   "NOT LIKE '%g4dn.%'" AND "NOT IN ('123456789012')" AND
    #   "resource_tags_user_lifecycle" clauses
    ...  # flesh out using test_end_to_end.py + test_run_optimizer.py scaffolding
```

(Use the same scaffolding style as the existing `test_end_to_end.py`. Keep it small — one passing happy-path assertion per bullet above.)

- [ ] **Step 2: Run**

```bash
python -m pytest tests/test_end_to_end_with_filters.py -v 2>&1 | tail -20
```

Expected: passes after some iteration on mock shapes.

- [ ] **Step 3: Commit**

```bash
git add tests/test_end_to_end_with_filters.py
git commit -m "test(aws-sp-optimizer): end-to-end integration with all 3 exclude axes"
```

---

## Task 20: Update `references/usage.md`

**Files:**
- Modify: `skills/aws-sp-optimizer/references/usage.md`

- [ ] **Step 1: Add CLI flag documentation**

Insert a new `## Exclude filters` section after `## Typical runs`:
````markdown
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
SP-covered cost over last 30 days). This prevents double-counting the portion
of existing SP that was covering filtered-away workload. See
`applied_exclude_filter.sp_coverage_share` in the output.

### When a tag column is missing

If a filtered tag key has no column in the CUR table (tag was never used),
the filter for that key is **skipped with a warning**. Other filter axes still
apply. Check `applied_exclude_filter.skipped_filter_warnings` in the output.
````

- [ ] **Step 2: Commit**

```bash
git add references/usage.md
git commit -m "docs(aws-sp-optimizer): document exclude filter CLI flags + YAML config"
```

---

## Task 21: Update `references/output-interpretation.md`

**Files:**
- Modify: `skills/aws-sp-optimizer/references/output-interpretation.md`

- [ ] **Step 1: Add `applied_exclude_filter` section**

Append a section describing the new context field, the `sp_coverage_share`
concept, and how to interpret `skipped_filter_warnings`:
````markdown
## `context.applied_exclude_filter`

When any exclude filter was active, this section describes what was filtered:

```json
"applied_exclude_filter": {
  "usage_type_patterns": ["g4dn."],
  "account_ids": ["123456789012"],
  "tag_exclusions": [{"key": "lifecycle", "values": ["ephemeral"]}],
  "include_untagged": true,
  "sp_coverage_share": 0.82,
  "sp_coverage_share_audit": {
    "retained_cost_usd": 820.5,
    "total_cost_usd": 1000.0,
    "note": "share = retained / total"
  },
  "skipped_filter_warnings": []
}
```

- `sp_coverage_share`: in [0, 1]. The existing SP's effective coverage has been
  multiplied by this factor before being reported as `C_existing_effective_per_hour`.
  Empty filter ⇒ 1.0 (no attenuation).
- `skipped_filter_warnings`: non-empty when a filter referenced a tag column
  not present in CUR. The filter was dropped silently from SQL; other axes
  unaffected.

When `applied_exclude_filter` is `null`, no filter was active and behavior is
identical to pre-v2 runs.
````

- [ ] **Step 2: Commit**

```bash
git add references/output-interpretation.md
git commit -m "docs(aws-sp-optimizer): document applied_exclude_filter in output"
```

---

## Task 22: Update `SKILL.md`

**Files:**
- Modify: `skills/aws-sp-optimizer/SKILL.md`

- [ ] **Step 1: Add exclude-filter mention to overview + Step 2 instructions**

In `SKILL.md`:
1. Add a bullet in the triggers section: "用户想排除某些机型/账户/tag 的 SP 增购评估（如 'g4dn 要切 spot，不要算进 SP'）"
2. Extend Step 1 / Step 2 / Step 3 flow to mention that a non-empty exclude filter produces `context.applied_exclude_filter` in output and that `sp_coverage_share < 1.0` is **expected and correct** behavior (not a bug).
3. Add a new bullet under "重要边界": "**不要**在用户没明确要求的情况下主动加 `--exclude-*` 参数。filter 是用户显式意图，不是推断。"

- [ ] **Step 2: Commit**

```bash
git add SKILL.md
git commit -m "docs(aws-sp-optimizer): document exclude-filter triggers in SKILL.md"
```

---

## Task 23: Update `references/failure-handling.md`

**Files:**
- Modify: `skills/aws-sp-optimizer/references/failure-handling.md`

- [ ] **Step 1: Add tag-column-missing guidance**

Append a subsection:
````markdown
## Tag column missing (filter degraded)

When `context.applied_exclude_filter.skipped_filter_warnings` is non-empty, one
or more `--exclude-tag` filters referenced a CUR column that doesn't exist.
The filter was dropped silently from SQL for that key.

**What to tell the user:**
> 你 --exclude-tag 里的 key 'X' 在 CUR 里没有对应列（可能该 tag 从未被使用过，或
> tag key 归一化后和 CUR 里的列名不一致）。这个 tag 过滤被跳过了，其他 filter 轴
> 仍然生效。C* 结果可能偏离你的预期。

**Recovery options:**
1. Query `information_schema.columns WHERE table_schema='<cur_db>' AND column_name LIKE 'resource_tags_user_%'` to see what tag columns actually exist
2. Confirm the tag is really in use on resources (Resource Groups Tagging API)
3. If the tag is genuinely missing, remove it from `--exclude-tag` / config and
   re-run
````

- [ ] **Step 2: Commit**

```bash
git add references/failure-handling.md
git commit -m "docs(aws-sp-optimizer): add tag-column-missing failure handling"
```

---

## Task 24: Update presentation template

**Files:**
- Modify: `skills/aws-sp-optimizer/references/presentation-template.md`

- [ ] **Step 1: Extend §3 metadata table to include filter**

In `presentation-template.md` §3 step 1 (the metadata table), add rows:
````markdown
| Exclude filter | non-empty ⇒ list axes; else "none" | 来自 `context.applied_exclude_filter` |
| SP coverage share | `applied_exclude_filter.sp_coverage_share` ×100 | 现有 SP 在 filter 后保留的有效覆盖比例；empty filter = 100% |
````

Add a new §3 sub-step after the metadata table when `applied_exclude_filter != null`:
````markdown
**第 4 步：已应用的排除（仅当 `applied_exclude_filter != null`）**

| 轴 | 值 |
|---|---|
| Usage type patterns | `applied_exclude_filter.usage_type_patterns` |
| Account IDs | `applied_exclude_filter.account_ids` |
| Tags | `applied_exclude_filter.tag_exclusions` 格式化 |
| Untagged rows | include / exclude (based on `include_untagged`) |
| Skipped filters | `applied_exclude_filter.skipped_filter_warnings`（若非空，警示用户）|

> 本次报告**只包含 filter 后的剩余 workload**。现有 SP effective 已按
> `sp_coverage_share = {share}` 衰减；若该值显著 <1.0，说明原 SP 正在覆盖被排除
> 的那部分 workload（双计已避免）。
````

- [ ] **Step 2: Commit**

```bash
git add references/presentation-template.md
git commit -m "docs(aws-sp-optimizer): surface applied filter in §3 of report template"
```

---

## Task 25: Final regression + MR prep

**Files:**
- None (validation only)

- [ ] **Step 1: Run full test suite**

```bash
cd ~/claude-home/gitlab/engineering/skills/skills/aws-sp-optimizer && \
  python -m pytest tests/ --tb=short 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 2: Run with empty filter as byte-for-byte regression guard**

```bash
python -m scripts.aws_sp_optimizer --org a4x-us --validate-only 2>&1 | tail -5
```

Expected: `status: "validation_ok"` — prove empty-filter default path still reaches same validation outcome.

- [ ] **Step 3: Dry-run smoke with filter flags against a mock or cached run**

```bash
python -m scripts.aws_sp_optimizer --help 2>&1 | grep -A1 exclude
```

Expected: all four `--exclude-*` flags plus `--include-untagged/--exclude-untagged` visible in help.

- [ ] **Step 4: Push branch and open MR**

```bash
cd ~/claude-home/gitlab/engineering/skills && \
  git push -u origin feat/aws-sp-optimizer-exclude-filters
```

Then (per user's CLAUDE.md: all repo changes go through MR):
```bash
# gh cli isn't set up for gitlab; use glab or the web UI
glab mr create \
  --title "feat(aws-sp-optimizer): exclude filter subsystem (P0+P1+P2+P3)" \
  --description "$(cat <<'EOF'
## Summary

Add `ExcludeFilter` subsystem to aws-sp-optimizer covering three exclusion
axes — instance-family / usage_type pattern (P1), member account id (P2),
and resource tag (P3) — plus the shared infrastructure (P0):

- `ExcludeFilter` + `TagExclusion` dataclasses
- Injection-safe validators
- SQL clause builder with tag-column-presence awareness
- Tag column auto-discovery via `information_schema.columns`
- Filter threading through all 5 CUR SQL touchpoints
- New `compute_sp_coverage_share` helper attenuating `C_existing_effective`
- CLI flags + YAML config block + merge semantics (CLI additive)
- Output reflection: `context.applied_exclude_filter` section
- Docs updates: usage, output-interpretation, failure-handling, SKILL.md,
  presentation-template

## Plan

See `docs/plans/2026-04-20-exclude-filter-subsystem.md`.

## Test plan

- [ ] All existing unit tests still green (regression guard)
- [ ] `tests/test_exclude_filter_dataclass.py` — dataclass semantics
- [ ] `tests/test_exclude_filter_validators.py` — injection safety
- [ ] `tests/test_exclude_filter_sql.py` — SQL clause generation (all 3 axes)
- [ ] `tests/test_exclude_filter_tag_columns.py` — info_schema discovery + failure tolerance
- [ ] `tests/test_sp_coverage_share.py` — attenuator helper
- [ ] `tests/test_end_to_end_with_filters.py` — integration across all 5 SQL sites
- [ ] Empty-filter default path preserves pre-PR behavior byte-for-byte
- [ ] `--validate-only` still returns validation_ok
EOF
)" \
  --target-branch feat/aws-sp-optimizer-v1 \
  --remove-source-branch
```

- [ ] **Step 5: Commit anything left** (should be nothing at this point)

---

## Self-Review Checklist

**Spec coverage:**
- P0 shared infra → Tasks 1–13, 17
- P1 usage_type pattern → Tasks 3, 4, 7–10 (via shared pattern), 15–16
- P2 account_id → Tasks 3, 4, 7–10 (via shared pattern), 15–16
- P3 tag → Tasks 3, 5, 6, 15–16
- C_existing_effective attenuation → Tasks 11, 12
- d_raw consistency → Task 13 (regression only; naturally consistent)
- Output reflection → Task 18
- Docs → Tasks 20–24
- Regression guard → Task 25

**Placeholder scan:** Tasks 17 and 19 contain "…flesh out using existing scaffolding" notes where test boilerplate is too repo-specific to reproduce verbatim. The implementer should look at `tests/test_end_to_end.py` and `tests/test_run_optimizer.py` for the existing mock pattern and mirror it. This is the only allowable reference by pattern rather than literal code in the plan; it's acceptable because the tests depend on existing fixture design that only the maintainer can best adapt.

**Type consistency:**
- `ExcludeFilter.is_empty()` — used in Tasks 2, 11, 18 ✓
- `build_exclude_sql_clauses(filter, tag_columns) -> (clauses, warnings)` — Tasks 4, 5, 11, 18 ✓
- `fetch_available_tag_columns(config) -> frozenset[str]` — Tasks 6, 17 ✓
- `compute_sp_coverage_share(config, start, end, filter, tag_cols) -> (share, audit)` — Tasks 11, 12, 17, 18 ✓
- `get_existing_sp_effective(..., sp_coverage_share=1.0) -> (effective, SPAudit)` — Tasks 12, 17 ✓
- `merge_cli_into_config_filter(config_filter, args) -> ExcludeFilter` — Tasks 16, 17 ✓
- All CUR functions: `exclude_filter: ExcludeFilter | None = None, available_tag_columns: frozenset[str] | None = None` — Tasks 7, 8, 9, 10 ✓

---
