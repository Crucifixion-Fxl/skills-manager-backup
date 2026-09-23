#!/usr/bin/env python3
"""Run the owner-fixed GitLab sync inventory, then the local route scanner.

This entry point intentionally accepts no business arguments.  The Desk
runtime injects one owner-controlled manifest path; tick, GitLab and Canvas
text can never select a release, config, state directory or child environment.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import gitlab_buzz_route_reply as route  # noqa: E402
import gitlab_buzz_sync as sync  # noqa: E402


DEFAULT_MANIFEST = Path("/etc/buzz-agent/gitlab-buzz-desk-runner.json")
MANIFEST_ENV = "BUZZ_DESK_RUNNER_MANIFEST"
MANIFEST_KEYS = frozenset({"version", "release_dir", "sync", "route"})
STEP_KEYS = frozenset({"config", "state_dir"})
SYNC_TIMEOUT_SECONDS = sync.GITLAB_RUN_BUDGET_SECONDS + 30
ROUTE_TIMEOUT_SECONDS = 120


class RunnerError(RuntimeError):
    """Owner configuration is invalid; no child may be started."""


def _fixed_path(raw: Any, label: str, *, may_not_exist: bool = False) -> Path:
    if not isinstance(raw, str) or not raw:
        raise RunnerError(f"{label} must be an absolute path")
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise RunnerError(f"{label} must be an absolute normalized path")
    if not may_not_exist:
        try:
            if path.resolve(strict=True) != path:
                raise RunnerError(f"{label} must not contain symlinks")
        except OSError as exc:
            raise RunnerError(f"cannot resolve {label}: {type(exc).__name__}") from None
    elif path.exists() or path.is_symlink():
        try:
            if path.resolve(strict=True) != path:
                raise RunnerError(f"{label} must not contain symlinks")
        except OSError as exc:
            raise RunnerError(f"cannot resolve {label}: {type(exc).__name__}") from None
    return path


def _release_script(release_dir: Path, name: str) -> Path:
    script = release_dir / "scripts" / name
    try:
        metadata = script.lstat()
        resolved = script.resolve(strict=True)
    except OSError as exc:
        raise RunnerError(f"fixed release is missing {name}: {type(exc).__name__}") from None
    if (
        resolved != script
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RunnerError(f"fixed release script {name} is not an owner-controlled regular file")
    return script


def _step(value: Any, label: str) -> dict[str, Path]:
    if not isinstance(value, dict) or set(value) != STEP_KEYS:
        raise RunnerError(f"{label} must contain exactly config and state_dir")
    return {
        "config": _fixed_path(value.get("config"), f"{label}.config"),
        "state_dir": _fixed_path(value.get("state_dir"), f"{label}.state_dir", may_not_exist=True),
    }


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = sync.load_config(path)
    except sync.SyncError as exc:
        raise RunnerError(str(exc)) from None
    if set(value) != MANIFEST_KEYS or value.get("version") != 1:
        raise RunnerError("runner manifest keys or version do not match the contract")
    release_dir = _fixed_path(value.get("release_dir"), "release_dir")
    try:
        metadata = release_dir.lstat()
    except OSError as exc:
        raise RunnerError(f"cannot inspect release_dir: {type(exc).__name__}") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RunnerError("release_dir must be an owner-controlled real directory")
    sync_values = value.get("sync")
    if not isinstance(sync_values, list) or not 1 <= len(sync_values) <= 32:
        raise RunnerError("sync inventory must contain 1..32 fixed steps")
    steps = [_step(item, f"sync[{index}]") for index, item in enumerate(sync_values)]
    route_step = _step(value.get("route"), "route")
    state_dirs = [item["state_dir"] for item in steps] + [route_step["state_dir"]]
    if len(set(state_dirs)) != len(state_dirs):
        raise RunnerError("sync and route state directories must be distinct")
    return {
        "release_dir": release_dir,
        "sync_script": _release_script(release_dir, "gitlab_buzz_sync.py"),
        "route_script": _release_script(release_dir, "gitlab_buzz_route_reply.py"),
        "sync": steps,
        "route": route_step,
    }


def inventory(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate all fixed configs and reject sequential overlap before any child."""

    claimed: dict[tuple[str, int], int] = {}
    entries: list[dict[str, Any]] = []
    channels: set[str] = set()
    for index, step in enumerate(manifest["sync"]):
        try:
            config = sync.load_config(step["config"])
            sync.validate_config(config)
        except sync.SyncError as exc:
            raise RunnerError(f"sync[{index}] config is invalid: {exc}") from None
        channel_id = config["channel_id"]
        channels.add(channel_id)
        for project_id in config["gitlab"]["projects"]:
            key = (channel_id, project_id)
            if key in claimed:
                raise RunnerError(
                    f"sync config inventory overlap for channel {channel_id} project {project_id}"
                )
            claimed[key] = index
        entries.append({"step": step, "config": config})
    try:
        route_config = route.load_config(manifest["route"]["config"], mode="scan")
    except route.ConfigError as exc:
        raise RunnerError(f"route config is invalid: {exc}") from None
    route_channel = next(iter(route_config["channels"]))
    if channels != {route_channel}:
        raise RunnerError("every sync config and the route config must bind the same channel")
    return entries


def count_origin_fallbacks(result: dict[str, Any]) -> int:
    """How many objects a sync child reports as having synced without an unusable origin (ADR-0014). Only the count
    travels up: the reasons are GitLab-derived text and stay in the child's own report."""

    value = result.get("origin_fallbacks")
    return len(value) if isinstance(value, list) else 0


def _child_env(env: dict[str, str], token_env: str | None = None) -> dict[str, str]:
    child = {key: env[key] for key in sync.CHILD_ENV_KEYS if key in env}
    if token_env is not None and token_env in env:
        child[token_env] = env[token_env]
    return child


def _invoke(
    run: Any, argv: list[str], env: dict[str, str], timeout: int
) -> tuple[int, dict[str, Any]]:
    try:
        result = run(
            argv,
            input=None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"fixed child could not run: {type(exc).__name__}") from None
    try:
        body = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return int(result.returncode), {"status": "error", "error": "child returned no JSON result"}
    if not isinstance(body, dict) or not isinstance(body.get("status"), str):
        return int(result.returncode), {"status": "error", "error": "child returned an invalid result"}
    return int(result.returncode), body


def run_once(
    *, manifest_path: Path, env: dict[str, str] | None = None, run: Any = subprocess.run
) -> dict[str, Any]:
    runtime_env = dict(os.environ if env is None else env)
    manifest = load_manifest(Path(manifest_path))
    entries = inventory(manifest)
    sync_statuses: list[str] = []
    origin_fallbacks = 0
    request_ids: set[str] = set()
    for index, entry in enumerate(entries):
        step, config = entry["step"], entry["config"]
        argv = [
            sys.executable,
            str(manifest["sync_script"]),
            "--config",
            str(step["config"]),
            "--state-dir",
            str(step["state_dir"]),
        ]
        returncode, result = _invoke(
            run,
            argv,
            _child_env(runtime_env, config["gitlab"]["token_env"]),
            SYNC_TIMEOUT_SECONDS,
        )
        status = result.get("status")
        if returncode != 0 or status not in {"ok", "degraded", "locked"}:
            return {
                "status": "error",
                "stage": "sync",
                "index": index,
                "error": str(result.get("error") or "fixed sync child failed"),
            }
        raw_requests = result.get("summary_requests", [])
        if not isinstance(raw_requests, list):
            return {"status": "error", "stage": "sync", "index": index,
                    "error": "sync child returned invalid summary requests"}
        try:
            for request in raw_requests:
                sync.validate_public_summary_request(request)
                if request["request_id"] in request_ids:
                    raise sync.SyncError("sync inventory returned a duplicate summary request")
                request_ids.add(request["request_id"])
        except sync.SyncError as exc:
            return {"status": "error", "stage": "sync", "index": index, "error": str(exc)}
        sync_statuses.append(status)
        origin_fallbacks += count_origin_fallbacks(result)

    route_step = manifest["route"]
    argv = [
        sys.executable,
        str(manifest["route_script"]),
        "--config",
        str(route_step["config"]),
        "--state-dir",
        str(route_step["state_dir"]),
        "--scan-once",
    ]
    returncode, result = _invoke(
        run,
        argv,
        _child_env(runtime_env),
        ROUTE_TIMEOUT_SECONDS,
    )
    if returncode != 0 or result.get("status") != "ok":
        return {
            "status": "error",
            "stage": "route",
            "error": str(result.get("error") or "fixed route child failed"),
        }
    overall = "degraded" if "degraded" in sync_statuses else "locked" if "locked" in sync_statuses else "ok"
    result = {"status": overall, "sync": sync_statuses, "route": "ok"}
    if origin_fallbacks:
        result["origin_fallbacks"] = origin_fallbacks
    if overall == "locked":
        return result
    scopes = [(entry["config"], Path(entry["step"]["state_dir"])) for entry in entries]
    try:
        handles = sync.acquire_scope_locks(scopes, "summary request inventory")
    except sync.SyncError as exc:
        if str(exc).endswith(" is locked"):
            return {"status": "locked", "sync": sync_statuses, "route": "ok"}
        return {"status": "error", "stage": "summary", "error": str(exc)}
    try:
        selected = sync.select_summary_request(scopes, claim=True)
    except sync.SyncError as exc:
        return {"status": "error", "stage": "summary", "error": str(exc)}
    finally:
        for handle in reversed(handles):
            handle.close()
    if selected is not None:
        result["summary_requests"] = [selected["request"]]
    return result


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    run: Any = subprocess.run,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    runtime_env = dict(os.environ if env is None else env)
    manifest_path = Path(runtime_env.get(MANIFEST_ENV, str(DEFAULT_MANIFEST)))
    try:
        result = run_once(manifest_path=manifest_path, env=runtime_env, run=run)
    except RunnerError as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("status") in {"ok", "degraded", "locked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
