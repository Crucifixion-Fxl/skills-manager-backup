#!/usr/bin/env python3
"""Collect local ZCode usage for weekly-report.

Reads only ``~/.zcode/cli/rollout/model-io-*.jsonl`` metadata: ``startedAt``,
``model.modelId``, ``sessionId`` and ``response.usage`` token counters.
Never emits request/response payloads, prompts, tool arguments, or full paths.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC8 = timezone(timedelta(hours=8))


def week_bounds(since: str, until: str) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(since).replace(tzinfo=UTC8)
    end = datetime.fromisoformat(until).replace(tzinfo=UTC8) + timedelta(days=1)
    return start, end


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:  # naive stamps are read as UTC, never host-local
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(UTC8)


def _model_name(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("modelId") or "unknown"
    return "unknown"


def collect_zcode_usage(rollout_dir: Path, since: str, until: str) -> dict[str, Any]:
    start, end = week_bounds(since, until)
    result: dict[str, Any] = {
        "provider": "zcode",
        "available": False,
        "source": "rollout_model_io",
        "period": {"since": since, "until": until, "timezone": "UTC+08:00"},
        "usage": {
            "sessions": 0,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "daily": [],
            "model_breakdown": [],
        },
        "evidence": {"rollout_dir": rollout_dir.name},
    }
    if not rollout_dir.is_dir():
        result["reason"] = "rollout_dir_missing"
        return result

    totals_by_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_tokens": 0, "cache_write_tokens": 0})
    models: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                 "cache_read_tokens": 0, "cache_write_tokens": 0})
    sessions: set[str] = set()
    calls = 0
    seen: set[tuple[Any, ...]] = set()

    for path in sorted(rollout_dir.glob("model-io-*.jsonl")):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(entry.get("startedAt") or entry.get("completedAt"))
                if ts is None or not (start <= ts < end):
                    continue
                usage = ((entry.get("response") or {}).get("usage"))
                if not isinstance(usage, dict):
                    continue
                key = (entry.get("requestId"), entry.get("turnId"), entry.get("attempt"))
                if all(v is None for v in key):
                    key = (path.name, ts.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                model = _model_name(entry.get("model"))
                inp = usage.get("inputTokens") or 0
                out = usage.get("outputTokens") or 0
                cache_read = usage.get("cacheReadTokens") or 0
                cache_write = usage.get("cacheWriteTokens") or 0
                day = totals_by_day[ts.strftime("%Y-%m-%d")]
                bucket = models[model]
                bucket["calls"] += 1
                for target in (day, bucket):
                    target["input_tokens"] += inp
                    target["output_tokens"] += out
                    target["cache_read_tokens"] += cache_read
                    target["cache_write_tokens"] += cache_write
                calls += 1
                sessions.add(entry.get("sessionId") or path.stem)
                result["usage"]["input_tokens"] += inp
                result["usage"]["output_tokens"] += out
                result["usage"]["cache_read_tokens"] += cache_read
                result["usage"]["cache_write_tokens"] += cache_write

    result["usage"]["calls"] = calls
    result["usage"]["sessions"] = len(sessions)
    result["usage"]["daily"] = [
        {"date": day, **counts} for day, counts in sorted(totals_by_day.items())
    ]
    result["usage"]["model_breakdown"] = sorted(
        ({"model": name, **counts} for name, counts in models.items()),
        key=lambda m: -m["input_tokens"])
    result["available"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="week start YYYY-MM-DD (UTC+8)")
    parser.add_argument("--until", required=True, help="week end YYYY-MM-DD (UTC+8)")
    parser.add_argument("--rollout-dir", default="~/.zcode/cli/rollout",
                        help="ZCode rollout dir (default ~/.zcode/cli/rollout)")
    args = parser.parse_args(argv)

    try:
        datetime.strptime(args.since, "%Y-%m-%d")
        datetime.strptime(args.until, "%Y-%m-%d")
    except ValueError:
        print(json.dumps({"available": False, "reason": "invalid_date_argument"}))
        return 0

    result = collect_zcode_usage(Path(os.path.expanduser(args.rollout_dir)),
                                 args.since, args.until)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
