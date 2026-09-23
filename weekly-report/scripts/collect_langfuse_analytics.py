#!/usr/bin/env python3
"""Collect GitLab-attributed coding-process analytics from Langfuse.

Only derived workflow metadata and evaluator labels are returned. Raw prompts,
model responses, tool arguments, and tool results are never included.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "skill-analytics"))

from skill_analytics.identity import GitLabIdentityResolver  # noqa: E402
from skill_analytics.langfuse_client import LangfuseClient  # noqa: E402
from skill_analytics.weekly import collect_weekly_analytics  # noqa: E402
from skill_analytics.weekly_source import LangfuseWeeklySource  # noqa: E402


def _period(since: str, until: str) -> tuple[str, str]:
    start = date.fromisoformat(since)
    end_exclusive = date.fromisoformat(until) + timedelta(days=1)
    if end_exclusive <= start:
        raise ValueError("until must not be earlier than since")
    return f"{start.isoformat()}T00:00:00Z", f"{end_exclusive.isoformat()}T00:00:00Z"


def collect_langfuse_analytics(
    *,
    since: str,
    until: str,
    env: Mapping[str, str] = os.environ,
    identity_resolver: GitLabIdentityResolver | None = None,
    source: Any | None = None,
) -> dict[str, Any]:
    host = env.get("LANGFUSE_HOST")
    public_key = env.get("LANGFUSE_PUBLIC_KEY")
    secret_key = env.get("LANGFUSE_SECRET_KEY")
    if not all((host, public_key, secret_key)):
        return {"available": False, "reason": "langfuse_not_configured"}

    identity = (identity_resolver or GitLabIdentityResolver()).resolve()
    if identity.source != "gitlab" or not identity.username:
        return {"available": False, "reason": "gitlab_identity_unavailable"}
    start, end = _period(since, until)
    if source is None:
        client = LangfuseClient(str(host), str(public_key), str(secret_key))
        source = LangfuseWeeklySource(client)

    analytics = collect_weekly_analytics(
        langfuse_source=source,
        fallback=lambda **_: {},
        user_id=identity.user_id,
        since=start,
        until=end,
    )
    if analytics.get("source") != "langfuse":
        return {
            "available": False,
            "reason": "langfuse_unavailable",
            "error": analytics.get("langfuse_unavailable"),
        }
    return {
        "available": True,
        "source": "langfuse",
        "user": {"id": identity.user_id, "username": identity.username},
        "analytics": analytics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Langfuse coding-process analytics")
    parser.add_argument("--since", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--until", required=True, help="End date, YYYY-MM-DD, inclusive")
    args = parser.parse_args()
    try:
        result = collect_langfuse_analytics(since=args.since, until=args.until)
    except ValueError as error:
        result = {"available": False, "reason": "invalid_period", "error": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
