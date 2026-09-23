#!/usr/bin/env python3
"""Collect local Claude Code usage for weekly-report.

Two sources, tried in order:

1. ``ccusage`` CLI (daily + session), discovered beyond ``PATH`` because the
   binary commonly lives in an nvm node bin that agent shells do not inherit.
2. Raw ``~/.claude/projects/**/*.jsonl`` transcript usage records — the same
   records ccusage reads — parsed directly when no binary is usable.

Privacy: emits only timestamps, model names, session ids and token counters.
Never emits prompts, message text, tool arguments, or full local paths.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC8 = timezone(timedelta(hours=8))

CCUSAGE_EXTRA_GLOBS = (
    "~/.nvm/versions/node/*/bin/ccusage",
    "~/.local/bin/ccusage",
    "~/.volta/bin/ccusage",
    "~/.bun/bin/ccusage",
    "/opt/homebrew/bin/ccusage",
    "/usr/local/bin/ccusage",
    "~/bin/ccusage",
)


def _version_key(path: str) -> tuple[int, ...]:
    """Numeric-aware sort key so v25 beats v9 when several node versions coexist."""

    import re
    return tuple(int(x) for x in re.findall(r"\d+", path))


def discover_ccusage(explicit: str | None = None) -> str | None:
    """Return a usable ccusage path, or None.

    ``shutil.which`` alone is not enough: agent shells often miss the nvm node
    bin directory, which made past reports silently drop Claude Code usage.
    """

    if explicit:
        return explicit if os.path.isfile(explicit) and os.access(explicit, os.X_OK) else None
    found = shutil.which("ccusage")
    if found:
        return found
    for pattern in CCUSAGE_EXTRA_GLOBS:
        candidates = [p for p in glob.glob(os.path.expanduser(pattern)) if os.access(p, os.X_OK)]
        if candidates:
            return max(candidates, key=_version_key)
    return None


def week_bounds(since: str, until: str) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(since).replace(tzinfo=UTC8)
    end = datetime.fromisoformat(until).replace(tzinfo=UTC8) + timedelta(days=1)
    return start, end


def _empty_usage() -> dict[str, Any]:
    return {
        "sessions": 0,
        "subagent_sessions": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "daily": [],
        "model_breakdown": [],
    }


def collect_via_ccusage(bin_path: str, since: str, until: str) -> dict[str, Any] | None:
    """Run ccusage daily+session; return usage dict or None on any failure."""

    compact_since = since.replace("-", "")
    compact_until = until.replace("-", "")
    base = ["-j", "--since", compact_since, "--until", compact_until,
            "--timezone", "Asia/Singapore", "--offline"]
    payloads = {}
    # ccusage is typically a `#!/usr/bin/env node` script: prepend its own bin
    # dir to PATH so the child can resolve `node` even when the caller's
    # environment lacks the nvm/global bin directories.
    child_env = {**os.environ,
                 "PATH": os.path.dirname(os.path.abspath(bin_path)) + os.pathsep
                         + os.environ.get("PATH", "")}
    for sub in ("daily", "session"):
        try:
            proc = subprocess.run([bin_path, sub, *base], capture_output=True,
                                  text=True, timeout=180, env=child_env)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None
        # ccusage 18.x prints a bare `[]` (exit 0) for weeks with no data;
        # anything non-dict is treated as failure so the raw fallback runs.
        if not isinstance(payload, dict):
            return None
        payloads[sub] = payload

    daily = payloads["daily"].get("daily") or []
    totals = payloads["daily"].get("totals") or {}
    session_rows = payloads["session"].get("sessions") or []
    usage = _empty_usage()
    usage["input_tokens"] = totals.get("inputTokens") or 0
    usage["output_tokens"] = totals.get("outputTokens") or 0
    usage["cache_read_tokens"] = totals.get("cacheReadTokens") or 0
    usage["cache_creation_tokens"] = totals.get("cacheCreationTokens") or 0
    usage["sessions"] = len(session_rows)
    usage["subagent_sessions"] = sum(
        1 for row in session_rows if row.get("sessionId") == "subagents")
    usage["daily"] = [
        {
            "date": row.get("date"),
            "input_tokens": row.get("inputTokens") or 0,
            "output_tokens": row.get("outputTokens") or 0,
            "cache_read_tokens": row.get("cacheReadTokens") or 0,
            "cache_creation_tokens": row.get("cacheCreationTokens") or 0,
        }
        for row in sorted(daily, key=lambda r: r.get("date") or "")
    ]

    models: dict[str, dict[str, int]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                 "cache_creation_tokens": 0})
    for row in daily:
        for model in row.get("modelBreakdowns") or []:
            bucket = models[model.get("modelName") or "unknown"]
            bucket["input_tokens"] += model.get("inputTokens") or 0
            bucket["output_tokens"] += model.get("outputTokens") or 0
            bucket["cache_read_tokens"] += model.get("cacheReadTokens") or 0
            bucket["cache_creation_tokens"] += model.get("cacheCreationTokens") or 0
    usage["model_breakdown"] = sorted(
        ({"model": name, **counts} for name, counts in models.items()),
        key=lambda m: -(m["input_tokens"] + m["output_tokens"] + m["cache_read_tokens"]))
    return usage


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


def collect_raw_transcripts(projects_dir: Path, since: str, until: str) -> dict[str, Any]:
    start, end = week_bounds(since, until)
    usage = _empty_usage()
    totals_by_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                 "cache_creation_tokens": 0})
    models: dict[str, dict[str, int]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                 "cache_creation_tokens": 0})
    sessions: set[str] = set()

    # Recursive: Claude Code nests session transcripts (sidechains, agent
    # exports) below the project dir; a flat */*.jsonl glob misses most files.
    # Counting is per usage-bearing entry — no cross-entry dedup. Streaming
    # snapshots repeat (requestId, message.id) with partial counters, and the
    # per-entry sum is exactly what ccusage reports (verified against
    # ccusage 18.x on real transcripts); deduping would under-report.
    files = sorted(projects_dir.glob("**/*.jsonl")) if projects_dir.is_dir() else []
    for path in files:
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue  # broken symlinks / unreadable files must not kill the scan
        with fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(entry.get("timestamp"))
                if ts is None or not (start <= ts < end):
                    continue
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                counters = message.get("usage")
                if not isinstance(counters, dict):
                    continue
                sessions.add(entry.get("sessionId") or path.stem)
                day = totals_by_day[ts.strftime("%Y-%m-%d")]
                bucket = models[message.get("model") or "unknown"]
                inp = counters.get("input_tokens") or 0
                out = counters.get("output_tokens") or 0
                cache_read = counters.get("cache_read_input_tokens") or 0
                cache_create = counters.get("cache_creation_input_tokens") or 0
                for target in (day, bucket):
                    target["input_tokens"] += inp
                    target["output_tokens"] += out
                    target["cache_read_tokens"] += cache_read
                    target["cache_creation_tokens"] += cache_create
                usage["input_tokens"] += inp
                usage["output_tokens"] += out
                usage["cache_read_tokens"] += cache_read
                usage["cache_creation_tokens"] += cache_create

    usage["sessions"] = len(sessions)
    usage["daily"] = [
        {"date": day, **counts} for day, counts in sorted(totals_by_day.items())
    ]
    usage["model_breakdown"] = sorted(
        ({"model": name, **counts} for name, counts in models.items()),
        key=lambda m: -(m["input_tokens"] + m["output_tokens"] + m["cache_read_tokens"]))
    return usage


def week_activity_observed(projects_dir: Path, since: str, until: str) -> bool:
    """Recursive mtime heuristic bounded to the week: did any transcript change inside it?"""

    if not projects_dir.is_dir():
        return False
    try:
        start, end = week_bounds(since, until)
    except ValueError:
        return False
    for path in projects_dir.glob("**/*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if start.timestamp() <= mtime < end.timestamp():
            return True
    return False


def collect_claude_usage(projects_dir: Path, since: str, until: str,
                         ccusage_bin: str | None = None,
                         allow_ccusage: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": "claude-code",
        "available": False,
        "source": None,
        "period": {"since": since, "until": until, "timezone": "UTC+08:00"},
        "usage": _empty_usage(),
        "evidence": {
            "projects_dir": projects_dir.name,
            "week_activity_observed": week_activity_observed(projects_dir, since, until),
        },
        "notes": [],
    }

    usage = None
    if allow_ccusage:
        bin_path = discover_ccusage(ccusage_bin)
        if bin_path:
            usage = collect_via_ccusage(bin_path, since, until)
            if usage is not None:
                result["source"] = "ccusage"
                result["available"] = True
            else:
                result["notes"].append("ccusage_discovered_but_failed")
        else:
            result["notes"].append("ccusage_not_found")
    else:
        result["notes"].append("ccusage_disabled")

    if usage is None:
        usage = collect_raw_transcripts(projects_dir, since, until)
        result["source"] = "raw_transcripts"
        result["available"] = True

    result["usage"] = usage
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="week start YYYY-MM-DD (UTC+8)")
    parser.add_argument("--until", required=True, help="week end YYYY-MM-DD (UTC+8)")
    parser.add_argument("--projects-dir", default="~/.claude/projects",
                        help="Claude Code projects dir (default ~/.claude/projects)")
    parser.add_argument("--ccusage-bin", default=None,
                        help="explicit ccusage binary; default auto-discovers")
    parser.add_argument("--no-ccusage", action="store_true",
                        help="skip ccusage and parse raw transcripts only")
    args = parser.parse_args(argv)

    try:
        datetime.strptime(args.since, "%Y-%m-%d")
        datetime.strptime(args.until, "%Y-%m-%d")
    except ValueError:
        print(json.dumps({"available": False, "reason": "invalid_date_argument"}))
        return 0

    result = collect_claude_usage(
        Path(os.path.expanduser(args.projects_dir)),
        args.since, args.until,
        ccusage_bin=args.ccusage_bin,
        allow_ccusage=not args.no_ccusage,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
