#!/usr/bin/env python3
"""Owner timer entry point: run the fixed GitLab sync inventory without an LLM (ADR-0008).

A systemd --user timer starts this script under the Desk identity's owner-fixed
environment.  It accepts no arguments: the owner manifest (BUZZ_DESK_RUNNER_MANIFEST)
fixes release, sync/route configs and state.  One run executes the zero-argument
runner once (sync, then Canvas route), then publishes every claimed summary request
with a deterministic template line through the unchanged publisher gates.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import gitlab_buzz_desk_runner as runner  # noqa: E402
import gitlab_buzz_summary_publish as publish  # noqa: E402
import gitlab_buzz_sync as sync  # noqa: E402


MAX_SUMMARIES_PER_RUN = 20
MAX_REFS_IN_SUMMARY = 5
OBJECT_LABELS = publish.OBJECT_LABELS
OBJECT_ORDER = ("push", "pipeline", "deployment", "note", "milestone", "wiki", "member", "sync", "other")


def _safe_ref(ref: str) -> bool:
    if not ref or len(ref) > 120:
        return False
    try:
        publish._validated_summary(ref)
    except publish.PublishError:
        return False
    return True


def template_summary(facts: list[dict[str, Any]]) -> str:
    """One plain-text line whose every number and ref is derivable from the facts."""

    counts: dict[str, int] = {}
    for item in facts:
        counts[item["object"]] = counts.get(item["object"], 0) + 1
    parts = [f"{OBJECT_LABELS[obj]} {counts[obj]} 条" for obj in OBJECT_ORDER if obj in counts]
    prose = f"GitLab 活动汇总 {len(facts)} 条：" + "，".join(parts) + "。"

    pushes = [item for item in facts if item["object"] == "push" and _safe_ref(item["ref"])]
    details = [f"{item['ref']} {item['commits']} 个提交" for item in pushes[:MAX_REFS_IN_SUMMARY]]
    if details:
        candidate = prose + "推送分支：" + "，".join(details) + "。"
        try:
            publish._validated_summary(candidate)
            publish.verify_summary_against_facts(candidate, facts)
            return candidate
        except publish.PublishError:
            pass
    return prose


def claim_next_summary(*, manifest_path: Path) -> dict[str, Any] | None:
    """Claim the globally oldest pending summary request, exactly as the runner does after a sync."""

    manifest = runner.load_manifest(Path(manifest_path))
    entries = runner.inventory(manifest)
    scopes = [(entry["config"], Path(entry["step"]["state_dir"])) for entry in entries]
    handles = sync.acquire_scope_locks(scopes, "summary request inventory")
    try:
        selected = sync.select_summary_request(scopes, claim=True)
    finally:
        for handle in reversed(handles):
            handle.close()
    return None if selected is None else selected["request"]


def run_timer(
    *,
    manifest_path: Path,
    env: dict[str, str],
    run_once: Callable[..., dict[str, Any]] = runner.run_once,
    claim_next: Callable[..., dict[str, Any] | None] = claim_next_summary,
    execute: Callable[..., dict[str, str]] = publish.execute,
) -> dict[str, Any]:
    result = run_once(manifest_path=manifest_path, env=env)
    if result.get("status") not in {"ok", "degraded", "locked"}:
        return result
    pending = list(result.get("summary_requests") or [])
    published = 0
    while pending and published < MAX_SUMMARIES_PER_RUN:
        req = pending.pop(0)
        try:
            sync.validate_public_summary_request(req)
            outcome = execute(manifest_path=manifest_path, summary=template_summary(req["facts"]),
                              facts=req["facts_sha256"], env=env)
        except (publish.PublishError, sync.SyncError) as exc:
            return {**result, "status": "error", "stage": "summary", "error": str(exc),
                    "published": published}
        if outcome.get("status") not in {"sent", "duplicate", "idle"}:
            return {**result, "status": "error", "stage": "summary",
                    "error": str(outcome.get("error") or "publisher failed"), "published": published}
        published += 1
        if published >= MAX_SUMMARIES_PER_RUN:
            break  # leave the next request PENDING for the next run instead of claiming it here
        try:
            following = claim_next(manifest_path=manifest_path)
        except (runner.RunnerError, sync.SyncError) as exc:
            return {**result, "status": "error", "stage": "summary", "error": str(exc),
                    "published": published}
        if following is not None:
            pending.append(following)
    output = {key: value for key, value in result.items() if key != "summary_requests"}
    output["published"] = published
    return output


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    runtime_env = dict(os.environ if env is None else env)
    manifest = runtime_env.get(runner.MANIFEST_ENV)
    if not manifest:
        print(json.dumps({"status": "error", "error": f"{runner.MANIFEST_ENV} is required"}))
        return 2
    try:
        result = run_timer(manifest_path=Path(manifest), env=runtime_env)
    except (runner.RunnerError, sync.SyncError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("status") in {"ok", "degraded", "locked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
