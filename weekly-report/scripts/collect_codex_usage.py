#!/usr/bin/env python3
"""Collect local Codex usage for weekly-report.

The collector reads only local Codex metadata and token counters. It never emits
raw prompts, message text, or command arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import re
from typing import Any


WORKSPACE_THEME_BOOST = {
    "debugging": ("bugfix", "issue", "issues", "fix", "debug"),
    "implementation": ("impl", "implement", "feature", "feat", "module"),
    "code_review": ("review", "pr", "mr", "cr", "qa"),
    "testing": ("test", "pytest", "e2e", "ci", "coverage"),
    "documentation": ("doc", "docs", "wiki", "report"),
    "planning": ("plan", "roadmap", "milestone", "sprint"),
    "data_analysis": ("analysis", "insight", "metrics", "telemetry", "stats"),
}

THEME_LABELS = {
    "debugging": "问题排查",
    "implementation": "功能实现",
    "code_review": "代码审查",
    "testing": "测试验证",
    "documentation": "文档撰写",
    "planning": "方案规划",
    "data_analysis": "数据分析",
}

THEME_KEYWORDS = {
    "debugging": (
        "debug",
        "debugging",
        "troubleshoot",
        "troubleshooting",
        "bug",
        "bugfix",
        "exception",
        "crash",
        "stacktrace",
        "error",
        "fail",
        "失败",
        "错误",
        "故障",
        "排查",
        "修复",
    ),
    "implementation": (
        "implement",
        "implementation",
        "develop",
        "开发",
        "feature",
        "feat",
        "support",
        "新增",
        "添加",
        "改造",
        "create",
        "新增",
    ),
    "code_review": (
        "review",
        "code review",
        "code-review",
        "复核",
        "评审",
        "审查",
    ),
    "testing": ("test", "pytest", "e2e", "验证", "测试", "q&a"),
    "documentation": ("doc", "docs", "documentation", "report", "周报", "文档", "说明", "说明书"),
    "planning": ("plan", "design", "方案", "设计", "计划", "进度", "排期"),
    "data_analysis": ("analysis", "stats", "insight", "usage", "统计", "分析", "洞察", "趋势"),
}

THEME_WEIGHT_BASE = 1.0
WORKSPACE_THEME_WEIGHT = 0.6
MIN_WEIGHT_THRESHOLD = 0.05

TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

HIGHLIGHT_SIGNAL_PERCENTILE = 0.90
HIGHLIGHT_MIN_THREADS = 4
HIGHLIGHT_FALLBACK_MIN_THRESHOLD = 50000
REPORT_TZ = timezone(timedelta(hours=8))


def collect_codex_usage(
    codex_home: str | Path | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
    if not home.exists():
        return _unavailable("codex_home_not_found")

    start_dt, end_dt = _date_range(since, until)
    state_db = _find_state_db(home)
    threads = _load_threads(state_db, start_dt, end_dt) if state_db else {}
    event_summary = _load_jsonl_events(home, start_dt, end_dt, threads)
    # Load metadata without a latest-update filter: a thread active during an
    # old report period may have been updated again after that period ended.
    threads = {
        sid: thread for sid, thread in threads.items()
        if sid in event_summary["active_threads"]
        or start_dt <= thread["updated_at"] <= end_dt
    }

    if not threads and event_summary["token_events"] == 0:
        return _unavailable("codex_usage_not_found")

    usage = _build_usage(threads, event_summary)
    insights = _build_insights(threads, event_summary)
    return {
        "provider": "codex",
        "available": True,
        "period": {
            "since": start_dt.date().isoformat(),
            "until": end_dt.date().isoformat(),
            "timezone": "UTC+08:00",
        },
        "usage": usage,
        "insights": insights,
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "provider": "codex",
        "available": False,
        "reason": reason,
        "usage": {},
        "insights": {},
    }


def _date_range(since: str | None, until: str | None) -> tuple[datetime, datetime]:
    def strict(value: str) -> date:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("dates must use canonical YYYY-MM-DD format")
        return parsed

    if since:
        start = datetime.combine(strict(since), time.min, tzinfo=REPORT_TZ)
    else:
        start = datetime.min.replace(tzinfo=REPORT_TZ)
    if until:
        end = datetime.combine(strict(until), time.max, tzinfo=REPORT_TZ)
    else:
        end = datetime.max.replace(tzinfo=REPORT_TZ)
    if start > end:
        raise ValueError("since must be before or equal to until")
    return start, end


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, AttributeError):
        return None


def _find_state_db(codex_home: Path) -> Path | None:
    sqlite_home = Path(os.environ.get("CODEX_SQLITE_HOME", codex_home)).expanduser()
    candidates = list(sqlite_home.glob("state_*.sqlite"))
    candidates.extend(codex_home.glob("state_*.sqlite"))
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.stat().st_mtime, p.name))


def _load_threads(db_path: Path, start_dt: datetime, end_dt: datetime) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        query = """
            SELECT
              id, cwd, title, source, model, tokens_used, archived,
              COALESCE(NULLIF(updated_at_ms, 0), updated_at * 1000) AS updated_ms,
              COALESCE(NULLIF(created_at_ms, 0), created_at * 1000) AS created_ms
            FROM threads
        """
        for row in conn.execute(query):
            updated_at = datetime.fromtimestamp(row["updated_ms"] / 1000, tz=timezone.utc)
            rows[row["id"]] = {
                "id": row["id"],
                "cwd": row["cwd"] or "",
                "workspace": _workspace_name(row["cwd"] or ""),
                "title": row["title"] or "",
                "source": row["source"] or "",
                "model": row["model"] or "unknown",
                "tokens_used": int(row["tokens_used"] or 0),
                "archived": bool(row["archived"]),
                "updated_at": updated_at,
            }
    except sqlite3.Error:
        return {}
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
    return rows


def _workspace_name(cwd: str) -> str:
    if not cwd:
        return "unknown"
    name = Path(cwd).name
    return name or cwd


def _load_jsonl_events(
    codex_home: Path,
    start_dt: datetime,
    end_dt: datetime,
    threads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    token_totals = Counter()
    daily: dict[str, Counter] = defaultdict(Counter)
    workspace_tokens = Counter()
    model_tokens = Counter()
    tool_calls = Counter()
    token_events = 0
    token_samples = 0
    active_threads: set[str] = set()
    thread_tokens = Counter()
    previous_totals: dict[str, tuple] = {}
    seen_events: set[tuple] = set()
    latest_rate_limit: dict[str, Any] | None = None
    latest_rate_limit_ts: datetime | None = None

    for path in _iter_jsonl_paths(codex_home):
        session_id = ""
        session_meta: dict[str, Any] = {}
        event_model = ""
        try:
            fh = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "session_meta":
                    payload = event.get("payload") or {}
                    session_id = payload.get("session_id") or payload.get("id") or session_id
                    session_meta = payload
                    event_model = payload.get("model") or ""
                    continue

                ts = _parse_ts(event.get("timestamp"))
                if ts is None or ts > end_dt:
                    continue

                payload = event.get("payload") or {}
                if event.get("type") == "turn_context":
                    event_model = payload.get("model") or event_model
                in_period = start_dt <= ts
                if in_period and session_id:
                    active_threads.add(session_id)
                    threads.setdefault(session_id, {
                        "id": session_id, "workspace": _workspace_name(session_meta.get("cwd") or ""),
                        "title": "", "source": "jsonl", "model": event_model or "unknown",
                        "tokens_used": 0, "archived": False, "updated_at": ts,
                    })
                if event.get("type") == "event_msg" and payload.get("type") == "token_count":
                    # Quota observations are independent of token samples. A
                    # repeated usage snapshot can carry a newer quota value,
                    # including another observation at the same timestamp.
                    rate_limits = payload.get("rate_limits")
                    if in_period and isinstance(rate_limits, dict) and (latest_rate_limit_ts is None or ts >= latest_rate_limit_ts):
                        latest_rate_limit_ts = ts
                        latest_rate_limit = _flatten_rate_limit(rate_limits)
                    info = payload.get("info") or {}
                    last = info.get("last_token_usage") or {}
                    total = info.get("total_token_usage") or {}
                    signature = tuple(total.get(k) for k in TOKEN_KEYS) if total else None
                    # Include pre-period snapshots in the baseline, but never
                    # their tokens. Equal cumulative snapshots are rate updates,
                    # not a new model call. A decrease can be a context reset.
                    event_key = (session_id, ts, signature, tuple(last.get(k) for k in TOKEN_KEYS))
                    if event_key in seen_events:
                        continue
                    seen_events.add(event_key)
                    repeated = signature is not None and previous_totals.get(session_id) == signature
                    if signature is not None:
                        previous_totals[session_id] = signature
                    if not in_period:
                        continue
                    token_events += 1
                    if repeated or "total_tokens" not in last:
                        continue
                    day = ts.astimezone(REPORT_TZ).date().isoformat()
                    for key in TOKEN_KEYS:
                        token_totals[key] += int(last.get(key) or 0)
                        daily[day][key] += int(last.get(key) or 0)
                    daily[day]["turns"] += 1
                    token_samples += 1
                    thread = threads.get(session_id, {})
                    workspace_tokens[thread.get("workspace") or "unknown"] += int(last.get("total_tokens") or 0)
                    model_tokens[event_model or thread.get("model") or "unknown"] += int(last.get("total_tokens") or 0)
                    thread_tokens[session_id] += int(last.get("total_tokens") or 0)
                elif in_period and event.get("type") == "response_item" and payload.get("type") == "function_call":
                    name = payload.get("name")
                    if name:
                        tool_calls[str(name)] += 1

    return {
        "token_totals": token_totals,
        "daily": daily,
        "workspace_tokens": workspace_tokens,
        "model_tokens": model_tokens,
        "tool_calls": tool_calls,
        "token_events": token_events,
        "token_samples": token_samples,
        "active_threads": active_threads,
        "thread_tokens": thread_tokens,
        "latest_rate_limit": latest_rate_limit,
    }


def _iter_jsonl_paths(codex_home: Path) -> list[Path]:
    paths = list((codex_home / "sessions").glob("**/*.jsonl"))
    paths.extend((codex_home / "archived_sessions").glob("*.jsonl"))
    return sorted({p for p in paths if p.is_file()})


def _flatten_rate_limit(rate_limits: dict[str, Any]) -> dict[str, Any]:
    primary = rate_limits.get("primary") or {}
    secondary = rate_limits.get("secondary") or {}
    return {
        "limit_id": rate_limits.get("limit_id"),
        "plan_type": rate_limits.get("plan_type"),
        "primary_used_percent": primary.get("used_percent"),
        "primary_window_minutes": primary.get("window_minutes"),
        "secondary_used_percent": secondary.get("used_percent"),
        "secondary_window_minutes": secondary.get("window_minutes"),
    }


def _build_usage(threads: dict[str, dict[str, Any]], event_summary: dict[str, Any]) -> dict[str, Any]:
    token_totals = event_summary["token_totals"]
    workspace_tokens = event_summary["workspace_tokens"]
    model_tokens = event_summary["model_tokens"]

    top_workspace_name, top_workspace_tokens = _top_counter(workspace_tokens)
    tokens_available = event_summary["token_samples"] > 0
    def token_value(key: str) -> int | None:
        return int(token_totals.get(key) or 0) if tokens_available else None
    return {
        "total_threads": len(threads),
        "token_events": event_summary["token_events"],
        "tokens_available": tokens_available,
        "token_samples": event_summary["token_samples"],
        "token_basis": "deduplicated_in_period_last_usage" if tokens_available else "unavailable",
        "total_tokens": token_value("total_tokens"),
        "input_tokens": token_value("input_tokens"),
        "cached_input_tokens": token_value("cached_input_tokens"),
        "output_tokens": token_value("output_tokens"),
        "reasoning_output_tokens": token_value("reasoning_output_tokens"),
        "active_workspaces": len({t["workspace"] for t in threads.values()}),
        "top_workspace": {
            "name": top_workspace_name,
            "tokens": int(top_workspace_tokens) if tokens_available else None,
            "threads": sum(1 for t in threads.values() if t["workspace"] == top_workspace_name),
        },
        "model_breakdown": [
            {"model": model, "tokens": int(tokens)}
            for model, tokens in model_tokens.most_common()
        ],
        "daily": [
            {"date": date, **{key: int(counter.get(key) or 0) for key in (*TOKEN_KEYS, "turns")}}
            for date, counter in sorted(event_summary["daily"].items())
        ],
        "latest_rate_limit": event_summary["latest_rate_limit"],
    }


def _build_insights(threads: dict[str, dict[str, Any]], event_summary: dict[str, Any]) -> dict[str, Any]:
    theme_scores: Counter = Counter()
    for thread in threads.values():
        for theme, score in _classify_themes(thread["title"], thread["workspace"]).items():
            theme_scores[theme] += score

    workspace_tokens = event_summary["workspace_tokens"]
    signals = Counter()
    token_values = [v for v in event_summary["thread_tokens"].values() if v > 0]
    if len(token_values) >= HIGHLIGHT_MIN_THREADS:
        high_token_threshold = max(
            _percentile(token_values, HIGHLIGHT_SIGNAL_PERCENTILE),
            HIGHLIGHT_FALLBACK_MIN_THRESHOLD,
        )
        high_token_count = sum(1 for value in token_values if value >= high_token_threshold)
        if high_token_count:
            signals["high_token_threads"] = high_token_count
    latest = event_summary["latest_rate_limit"] or {}
    if (latest.get("primary_used_percent") or 0) >= 80:
        signals["rate_limit_pressure"] += 1
    if event_summary["token_samples"] == 0 and threads:
        signals["token_snapshots_missing"] += 1

    return {
        "themes_top": [
            {"key": key, "label": THEME_LABELS.get(key, key), "count": int(round(count))}
            for key, count in sorted(
                ((theme, score) for theme, score in theme_scores.items() if score >= MIN_WEIGHT_THRESHOLD),
                key=lambda item: (-item[1], item[0]),
            )[:5]
        ],
        "workspace_focus": [
            {"name": name, "tokens": int(tokens)}
            for name, tokens in workspace_tokens.most_common(5)
        ],
        "tool_calls_top": [
            {"name": name, "count": int(count)}
            for name, count in event_summary["tool_calls"].most_common(8)
        ],
        "signals_top": [
            {"key": key, "label": _signal_label(key), "count": int(count)}
            for key, count in signals.most_common(5)
        ],
    }


def _normalize_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", value.lower()).strip()


def _classify_themes(title: str, workspace: str) -> dict[str, float]:
    text = _normalize_text(f"{title} {workspace}")
    workspace_text = _normalize_text(workspace)
    scores: dict[str, float] = {}

    for theme, keywords in THEME_KEYWORDS.items():
        score = 0.0
        for keyword in keywords:
            token = _normalize_text(keyword)
            if not token:
                continue
            if token in text:
                score += THEME_WEIGHT_BASE
            elif token in workspace_text:
                score += THEME_WEIGHT_BASE * 0.5
        if score > 0:
            scores[theme] = score

    for theme, workspace_hints in WORKSPACE_THEME_BOOST.items():
        for hint in workspace_hints:
            if hint in workspace_text:
                scores[theme] = scores.get(theme, 0.0) + WORKSPACE_THEME_WEIGHT
                break

    if not scores:
        return {"implementation": THEME_WEIGHT_BASE}
    return scores


def _percentile(values: list[int], quantile: float) -> float:
    values = sorted(int(v) for v in values)
    if not values:
        return 0.0
    if quantile <= 0:
        return float(values[0])
    if quantile >= 1:
        return float(values[-1])
    idx = (len(values) - 1) * quantile
    left = int(idx)
    right = min(left + 1, len(values) - 1)
    fraction = idx - left
    return values[left] * (1 - fraction) + values[right] * fraction


def _signal_label(key: str) -> str:
    return {
        "high_token_threads": "高 token 线程",
        "rate_limit_pressure": "额度压力",
        "token_snapshots_missing": "缺少 token 快照",
    }.get(key, key)


def _top_counter(counter: Counter) -> tuple[str, int]:
    if not counter:
        return "unknown", 0
    name, value = counter.most_common(1)[0]
    return str(name), int(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect local Codex usage for weekly-report")
    parser.add_argument("--codex-home", default=None)
    parser.add_argument("--since", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--until", required=True, help="End date, YYYY-MM-DD")
    args = parser.parse_args()
    print(
        json.dumps(
            collect_codex_usage(args.codex_home, args.since, args.until),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
