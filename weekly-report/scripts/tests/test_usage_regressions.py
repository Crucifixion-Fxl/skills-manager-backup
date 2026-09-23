"""Weekly counters must be bounded evidence, never lifetime estimates."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect_codex_usage as codex
from test_collect_codex_usage import _make_state_db, _write_session


def event(ts, total=120, cumulative=120):
    return {"timestamp": ts, "type": "event_msg", "payload": {
        "type": "token_count", "info": {
            "last_token_usage": {"total_tokens": total, "input_tokens": total},
            "total_token_usage": {"total_tokens": cumulative},
        }}}


def collect(home, since="2026-06-29", until="2026-06-29"):
    return codex.collect_codex_usage(home, since, until)


def test_utc8_boundary_and_daily_bucket(tmp_path):
    _make_state_db(tmp_path)
    _write_session(tmp_path, "thread-a",
                   event("2026-06-28T16:30:00Z", 120, 120),
                   event("2026-06-29T16:30:00Z", 240, 360))
    r = collect(tmp_path)
    assert r["usage"]["total_tokens"] == 120
    assert r["usage"]["daily"][0]["date"] == "2026-06-29"
    assert r["period"]["timezone"] == "UTC+08:00"


def test_missing_events_never_substitute_lifetime_usage(tmp_path):
    _make_state_db(tmp_path)
    r = collect(tmp_path)
    assert r["available"] is True  # Metadata can still be useful.
    assert r["usage"]["total_threads"] == 2
    assert r["usage"]["tokens_available"] is False
    assert r["usage"]["total_tokens"] is None
    assert r["usage"]["model_breakdown"] == []
    assert r["usage"]["daily"] == []
    assert r["insights"]["workspace_focus"] == []


def test_historical_active_thread_survives_later_update(tmp_path):
    db = _make_state_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE threads SET updated_at_ms=1783382400000 WHERE id='thread-a'")
    _write_session(tmp_path, "thread-a", event("2026-06-29T03:00:00Z"))
    r = collect(tmp_path)
    assert r["usage"]["total_threads"] == 2
    assert r["usage"]["top_workspace"]["name"] == "gen3"
    assert r["usage"]["model_breakdown"][0]["model"] == "gpt-5.5"


def test_repeated_snapshot_does_not_repeat_usage_or_turn(tmp_path):
    _make_state_db(tmp_path)
    _write_session(tmp_path, "thread-a", event("2026-06-29T03:00:00Z"),
                   event("2026-06-29T03:00:01Z"))
    r = collect(tmp_path)
    assert r["usage"]["total_tokens"] == 120
    assert r["usage"]["daily"][0]["turns"] == 1


def test_preperiod_snapshot_prevents_boundary_recount(tmp_path):
    _make_state_db(tmp_path)
    _write_session(tmp_path, "thread-a", event("2026-06-28T15:59:00Z"),
                   event("2026-06-28T16:00:01Z"),
                   event("2026-06-28T16:01:00Z", 60, 180))
    assert collect(tmp_path)["usage"]["total_tokens"] == 60


def test_counter_reset_does_not_discard_new_usage(tmp_path):
    _make_state_db(tmp_path)
    _write_session(tmp_path, "thread-a", event("2026-06-29T03:00:00Z", 120, 1000),
                   event("2026-06-29T03:01:00Z", 60, 60),
                   event("2026-06-29T03:01:01Z", 60, 60))
    assert collect(tmp_path)["usage"]["total_tokens"] == 180


def test_rate_limit_only_event_is_not_a_usage_turn(tmp_path):
    _make_state_db(tmp_path)
    e = event("2026-06-29T03:00:00Z")
    e["payload"]["info"] = None
    e["payload"]["rate_limits"] = {"primary": {"used_percent": 85}}
    _write_session(tmp_path, "thread-a", e)
    r = collect(tmp_path)
    assert r["usage"]["total_tokens"] is None
    assert r["usage"]["daily"] == []
    assert r["usage"]["latest_rate_limit"]["primary_used_percent"] == 85


@pytest.mark.parametrize("with_usage", [False, True])
def test_duplicate_token_snapshot_keeps_latest_rate_limit(tmp_path, with_usage):
    _make_state_db(tmp_path)
    events = []
    for percent in (10, 85):
        e = event("2026-06-29T03:00:00Z")
        if not with_usage:
            e["payload"]["info"] = None
        e["payload"]["rate_limits"] = {"primary": {"used_percent": percent}}
        events.append(e)
    _write_session(tmp_path, "thread-a", *events)
    r = collect(tmp_path)
    assert r["usage"]["latest_rate_limit"]["primary_used_percent"] == 85
    assert any(s["key"] == "rate_limit_pressure" for s in r["insights"]["signals_top"])
    assert r["usage"]["total_tokens"] == (120 if with_usage else None)
    assert r["usage"]["token_samples"] == (1 if with_usage else 0)


def test_rate_limit_outside_period_cannot_replace_in_period_snapshot(tmp_path):
    _make_state_db(tmp_path)
    events = []
    for timestamp, percent in (("2026-06-28T15:59:59Z", 99),
                               ("2026-06-29T03:00:00Z", 10),
                               ("2026-06-29T16:00:00Z", 95)):
        e = event(timestamp)
        e["payload"]["info"] = None
        e["payload"]["rate_limits"] = {"primary": {"used_percent": percent}}
        events.append(e)
    _write_session(tmp_path, "thread-a", *events)
    assert collect(tmp_path)["usage"]["latest_rate_limit"]["primary_used_percent"] == 10


def test_jsonl_only_metadata_and_model_context(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    events = [
        {"type": "session_meta", "payload": {"id": "t", "cwd": "/private/project"}},
        {"timestamp": "2026-06-29T03:00:00Z", "type": "turn_context",
         "payload": {"model": "model-at-event"}},
        event("2026-06-29T03:01:00Z"),
    ]
    (sessions / "t.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    r = collect(tmp_path)
    assert r["usage"]["total_threads"] == 1
    assert r["usage"]["top_workspace"]["name"] == "project"
    assert r["usage"]["model_breakdown"][0]["model"] == "model-at-event"
    assert "/private/" not in json.dumps(r)


@pytest.mark.parametrize("since,until", [
    ("2026-06-29T12:00:00", "2026-06-30"),
    ("2026-07-01", "2026-06-30"),
])
def test_rejects_noncanonical_or_reversed_dates(tmp_path, since, until):
    with pytest.raises(ValueError):
        collect(tmp_path, since, until)
