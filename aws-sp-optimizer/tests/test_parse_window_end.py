"""Tests for scripts._common.parse_window_end."""

from datetime import datetime, timezone

import pytest

from scripts._common import ConfigError, parse_window_end


def test_literal_today_returns_truncated_now():
    result = parse_window_end("today")
    assert result.tzinfo == timezone.utc
    # Truncated to the hour boundary
    assert result.minute == 0
    assert result.second == 0
    assert result.microsecond == 0


def test_yyyy_mm_dd_string():
    result = parse_window_end("2026-04-13")
    assert result == datetime(2026, 4, 13, tzinfo=timezone.utc)


def test_datetime_passthrough_aware():
    dt = datetime(2026, 4, 13, 14, 0, 0, tzinfo=timezone.utc)
    assert parse_window_end(dt) == dt


def test_datetime_passthrough_naive_gets_utc():
    dt = datetime(2026, 4, 13, 14, 0, 0)  # naive
    result = parse_window_end(dt)
    assert result.tzinfo == timezone.utc
    assert result.year == 2026


def test_invalid_format_raises_config_error():
    with pytest.raises(ConfigError) as excinfo:
        parse_window_end("04/13/2026")
    assert excinfo.value.code == "config_invalid"
    assert "window_end" in excinfo.value.message
