from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from collect_codex_usage import collect_codex_usage  # noqa: E402


def _make_state_db(codex_home: Path, rows: list[tuple] | None = None) -> Path:
    db_path = codex_home / "state_5.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            source TEXT NOT NULL,
            model_provider TEXT NOT NULL,
            cwd TEXT NOT NULL,
            title TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            model TEXT,
            created_at_ms INTEGER,
            updated_at_ms INTEGER
        )
        """
    )
    rows = rows or [
        (
            "thread-a",
            1782700000,
            1782700300,
            "vscode",
            "openai",
            "/work/gen3",
            "排查 issue95 工具设计",
            9000,
            0,
            "gpt-5.5",
            1782700000000,
            1782700300000,
        ),
        (
            "thread-b",
            1782700400,
            1782700500,
            "vscode",
            "openai",
            "/work/skills",
            "更新 weekly-report 支持 Codex",
            4000,
            0,
            "gpt-5.5",
            1782700400000,
            1782700500000,
        ),
        (
            "old-thread",
            1780000000,
            1780000100,
            "vscode",
            "openai",
            "/work/old",
            "old work",
            1000000,
            0,
            "gpt-5.4",
            1780000000000,
            1780000100000,
        ),
    ]
    conn.executemany(
        """
        INSERT INTO threads (
            id, created_at, updated_at, source, model_provider, cwd, title,
            tokens_used, archived, model, created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def _write_session(codex_home: Path, thread_id: str, *events: dict) -> None:
    session_dir = codex_home / "sessions" / "2026" / "06" / "29"
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"rollout-2026-06-29T10-00-00-{thread_id}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp": "2026-06-29T03:19:00.000Z",
                    "type": "session_meta",
                    "payload": {"session_id": thread_id, "id": thread_id},
                }
            )
            + "\n"
        )
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        fh.write("{malformed json\n")


def _token_event(thread_id: str, total: int, ts: str = "2026-06-29T03:20:00.000Z") -> dict:
    return {
        "timestamp": ts,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {"total_tokens": total},
                "last_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 5,
                    "total_tokens": 120,
                },
                "model_context_window": 258400,
            },
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": 23.0, "window_minutes": 300},
                "secondary": {"used_percent": 4.0, "window_minutes": 10080},
                "plan_type": "prolite",
            },
        },
    }


def test_collects_codex_usage_from_local_state(tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    _make_state_db(codex_home)
    _write_session(
        codex_home,
        "thread-a",
        _token_event("thread-a", 9000),
        {
            "timestamp": "2026-06-29T03:21:00.000Z",
            "type": "response_item",
            "payload": {"type": "function_call", "name": "exec_command"},
        },
    )
    _write_session(codex_home, "thread-b", _token_event("thread-b", 4000))

    result = collect_codex_usage(
        codex_home=codex_home,
        since="2026-06-29",
        until="2026-06-29",
    )

    assert result["available"] is True
    assert result["provider"] == "codex"
    assert result["usage"]["total_threads"] == 2
    assert result["usage"]["token_events"] == 2
    assert result["usage"]["total_tokens"] == 240
    assert result["usage"]["input_tokens"] == 200
    assert result["usage"]["cached_input_tokens"] == 80
    assert result["usage"]["output_tokens"] == 40
    assert result["usage"]["reasoning_output_tokens"] == 10
    assert result["usage"]["active_workspaces"] == 2
    assert result["usage"]["top_workspace"]["name"] == "gen3"
    assert result["usage"]["latest_rate_limit"]["primary_used_percent"] == 23.0
    assert result["usage"]["model_breakdown"][0]["model"] == "gpt-5.5"
    assert result["insights"]["themes_top"][0]["key"] == "debugging"
    assert result["insights"]["tool_calls_top"] == [{"name": "exec_command", "count": 1}]


def test_missing_codex_state_degrades_without_error(tmp_path):
    result = collect_codex_usage(
        codex_home=tmp_path / "missing",
        since="2026-06-29",
        until="2026-06-29",
    )

    assert result["available"] is False
    assert result["reason"] == "codex_home_not_found"


def test_workspace_hints_influence_theme_classification(tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    _make_state_db(
        codex_home,
        [
            (
                "bugfix-thread",
                1782700000,
                1782700300,
                "vscode",
                "openai",
                "/work/bugfix-auth",
                "同步周报流程",
                5000,
                0,
                "gpt-5.5",
                1782700000000,
                1782700300000,
            ),
            (
                "thread-b",
                1782700400,
                1782700500,
                "vscode",
                "openai",
                "/work/bugfix-auth",
                "同步周报流程",
                4500,
                0,
                "gpt-5.5",
                1782700400000,
                1782700500000,
            ),
            (
                "thread-c",
                1782700600,
                1782700700,
                "vscode",
                "openai",
                "/work/bugfix-auth",
                "同步周报流程",
                4800,
                0,
                "gpt-5.5",
                1782700600000,
                1782700700000,
            ),
            (
                "thread-d",
                1782700800,
                1782700900,
                "vscode",
                "openai",
                "/work/bugfix-auth",
                "同步周报流程",
                100000,
                0,
                "gpt-5.5",
                1782700800000,
                1782700900000,
            ),
        ],
    )

    result = collect_codex_usage(
        codex_home=codex_home,
        since="2026-06-29",
        until="2026-06-29",
    )

    assert result["available"] is True
    assert result["insights"]["themes_top"][0]["key"] == "debugging"


def test_high_token_signal_uses_weekly_quantile(tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    _make_state_db(
        codex_home,
        [
            ("t1", 1782700000, 1782700300, "vscode", "openai", "/work/gen3", "task one", 5000, 0, "gpt-5", 1782700000000, 1782700300000),
            ("t2", 1782700400, 1782700500, "vscode", "openai", "/work/gen3", "task two", 12000, 0, "gpt-5", 1782700400000, 1782700500000),
            ("t3", 1782700600, 1782700700, "vscode", "openai", "/work/gen3", "task three", 15000, 0, "gpt-5", 1782700600000, 1782700700000),
            ("t4", 1782700800, 1782700900, "vscode", "openai", "/work/gen3", "task four", 100000, 0, "gpt-5", 1782700800000, 1782700900000),
        ],
    )

    for thread_id, tokens in (("t1", 5000), ("t2", 12000), ("t3", 15000), ("t4", 100000)):
        event = _token_event(thread_id, tokens)
        event["payload"]["info"]["last_token_usage"]["total_tokens"] = tokens
        _write_session(codex_home, thread_id, event)

    result = collect_codex_usage(
        codex_home=codex_home,
        since="2026-06-29",
        until="2026-06-29",
    )

    signals = {signal["key"]: signal["count"] for signal in result["insights"]["signals_top"]}
    assert signals.get("high_token_threads") == 1
