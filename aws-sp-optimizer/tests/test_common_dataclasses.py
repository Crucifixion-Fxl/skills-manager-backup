"""Tests for dataclasses in scripts._common — field presence and defaults only.

Behavior tests for dataclass methods (like Window.hours caching) live in
their own phase's test file.
"""

from datetime import datetime, timedelta, timezone

from scripts._common import (
    Blocker,
    HourlySeries,
    OrgConfig,
    ValidationResult,
    Window,
)


def test_orgconfig_minimal():
    c = OrgConfig(
        alias="a4x-us",
        description=None,
        profile="aws-prod-us",
        payer_account_id="002497567426",
        org_id="o-4bw6d7ou28",
        cur_database="cur-db",
        cur_table="a4x_report",
        athena_output="s3://bucket/prefix/",
        athena_workgroup="primary",
        primary_region="us-east-1",
        window_days=90,
        window_end="today",
        prefer="balanced",
    )
    assert c.alias == "a4x-us"
    assert c.window_days == 90


def test_blocker_to_dict_roundtrip():
    b = Blocker(code="x", message="y", context={"a": 1})
    d = b.to_dict()
    assert d["code"] == "x"
    assert d["severity"] == "blocker"
    assert d["message"] == "y"
    assert d["context"] == {"a": 1}
    assert d["llm_next_action"] == ""
    assert d["user_fix_options"] == []


def test_validation_result_defaults_to_empty():
    r = ValidationResult()
    assert r.blockers == []
    assert r.warnings == []
    assert r.context is None


def test_window_derived_fields_and_cache():
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    w = Window(end=end, days=60)
    assert w.start == end - timedelta(days=60)
    assert w.num_hours == 60 * 24
    hours = w.hours
    assert len(hours) == 1440
    assert hours[0] == w.start
    assert hours[-1] == end - timedelta(hours=1)
    # cached_property: second access returns the same list object
    assert w.hours is hours


def test_hourlyseries_stores_passed_fields():
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    end = datetime(2026, 4, 3, tzinfo=timezone.utc)
    hours = [start + timedelta(hours=i) for i in range(48)]
    data = {h: {"total_usd": 1.0, "total_list_usd": 1.0, "total_net_usd": 0.91, "mix": {}} for h in hours}
    s = HourlySeries(hours=hours, data=data, window_start=start, window_end=end)
    assert len(s.hours) == 48
    assert s.window_start == start
    assert s.window_end == end
