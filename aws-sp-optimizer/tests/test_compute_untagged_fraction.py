"""Tests for compute_untagged_fraction — separate Athena query, SP-eligibility filter."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from scripts._common import OrgConfig, ValidationContext, Window, compute_untagged_fraction


def _cfg() -> OrgConfig:
    return OrgConfig(
        alias="t",
        description=None,
        profile="prof",
        payer_account_id="111111111111",
        org_id="o-abc",
        cur_database="d",
        cur_table="t",
        athena_output="s3://b/p/",
        athena_workgroup="primary",
        primary_region="us-east-1",
        window_days=60,
        window_end="today",
        prefer="balanced",
    )


def _ctx(has_col: bool | None = True) -> ValidationContext:
    return ValidationContext(
        caller_account_id="111111111111",
        org_id="o-abc",
        master_account_email="root@example.com",
        member_account_ids=[],
        member_accounts_count=0,
        primary_region="us-east-1",
        cur_table_has_resource_tags_user_service=has_col,
    )


def _window() -> Window:
    end = datetime(2026, 4, 13, tzinfo=timezone.utc)
    return Window(end=end, days=60)


def test_returns_zero_when_column_missing():
    ctx = _ctx(has_col=False)
    result = compute_untagged_fraction(_cfg(), ctx, _window())
    assert result == pytest.approx(0.0, abs=1e-9)


def test_returns_zero_when_column_none():
    ctx = _ctx(has_col=None)
    result = compute_untagged_fraction(_cfg(), ctx, _window())
    assert result == pytest.approx(0.0, abs=1e-9)


def test_returns_fraction_when_column_present():
    with patch("scripts._common.athena_execute") as mock_athena:
        mock_athena.return_value = [{"untagged_cost": "123.45", "total_cost": "1000.00"}]
        result = compute_untagged_fraction(_cfg(), _ctx(True), _window())
    assert abs(result - 0.12345) < 1e-9


def test_returns_zero_on_empty_result():
    with patch("scripts._common.athena_execute") as mock_athena:
        mock_athena.return_value = []
        result = compute_untagged_fraction(_cfg(), _ctx(True), _window())
    assert result == pytest.approx(0.0, abs=1e-9)


def test_returns_zero_on_zero_total():
    with patch("scripts._common.athena_execute") as mock_athena:
        mock_athena.return_value = [{"untagged_cost": "0", "total_cost": "0"}]
        result = compute_untagged_fraction(_cfg(), _ctx(True), _window())
    assert result == pytest.approx(0.0, abs=1e-9)


from scripts.exclude_filter import ExcludeFilter


def test_compute_untagged_fraction_applies_filter(monkeypatch):
    captured: list[str] = []

    def fake(_cfg, q):
        captured.append(q)
        return [{"untagged_cost": "10", "total_cost": "100"}]

    monkeypatch.setattr("scripts._common.athena_execute", fake)

    compute_untagged_fraction(
        _cfg(),
        _ctx(True),
        _window(),
        exclude_filter=ExcludeFilter(usage_type_patterns=("g4dn.",)),
        available_tag_columns=frozenset(),
    )
    assert "NOT LIKE '%g4dn.%'" in captured[0]


def test_timestamp_literal_format_matches_athena_grammar(monkeypatch):
    """Regression guard (MR !290 P1-3): the Athena TIMESTAMP literal must be
    `YYYY-MM-DD HH:MM:SS` — no `T`, no timezone suffix. datetime.isoformat()
    emits `2026-02-12T00:00:00+00:00`, which Athena rejects with
    INVALID_LITERAL; cur_query.build_hourly_series was fixed for this, but
    compute_untagged_fraction used isoformat() and silently failed every run.
    """
    captured: list[str] = []
    monkeypatch.setattr(
        "scripts._common.athena_execute",
        lambda _cfg, q: captured.append(q) or [{"untagged_cost": "0", "total_cost": "100"}],
    )
    compute_untagged_fraction(_cfg(), _ctx(True), _window())
    assert len(captured) == 1, f"expected exactly 1 Athena query, got {len(captured)}"
    # Iterate instead of indexing so Sonar's flow analyzer does not flag the
    # (guaranteed-non-empty) access as a possible IndexError (S6466/S6465).
    for q in captured:
        # Correct Athena grammar present:
        assert "TIMESTAMP '2026-02-12 00:00:00'" in q  # window.start = end - 60d
        assert "TIMESTAMP '2026-04-13 00:00:00'" in q  # window.end
        # Reject forms with the T separator or +00:00 suffix that Athena rejects:
        assert "2026-02-12T00:00:00+00:00" not in q
        assert "2026-04-13T00:00:00+00:00" not in q


def test_athena_failure_logs_and_returns_zero(monkeypatch, caplog):
    """If the Athena query itself throws, we still return 0.0 (non-fatal),
    but the exception must surface via logging so ops can notice that the
    untagged-fraction risk is being under-reported.
    """
    import logging

    def fake_boom(_cfg, _q):
        raise RuntimeError("Athena INVALID_LITERAL")

    monkeypatch.setattr("scripts._common.athena_execute", fake_boom)
    with caplog.at_level(logging.WARNING, logger="scripts._common"):
        result = compute_untagged_fraction(_cfg(), _ctx(True), _window())
    assert result == pytest.approx(0.0, abs=1e-9)
    assert any("compute_untagged_fraction" in r.message for r in caplog.records)
    assert any("Athena INVALID_LITERAL" in r.message for r in caplog.records)
