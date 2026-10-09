#!/usr/bin/env python3
"""Record sanitized, run-scoped Feishu events from hostd test bots only.

Pass a schema_version 2 manifest created for this probe run. Event bodies, card
values, secrets, and operator identifiers are never written; the recorder keeps
only identifiers and fields needed to correlate the exact probe operations.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import multiprocessing as mp
import os
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logging.getLogger("Lark").disabled = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feishu_creds as fc
import check_feishu_events as checker

DEFAULT_EVENTS = Path.home() / "skills/.worktree/.feishu-browser/events.jsonl"
DEFAULT_MANIFEST = Path.home() / "skills/.worktree/.feishu-browser/manifest.json"
LOCK = mp.Lock()


def _marshal_data(data: Any) -> str:
    """Only live SDK callbacks need the optional Feishu runtime dependency."""
    import lark_oapi as lark
    return lark.JSON.marshal(data)


def _object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        return {}
    checker._require(isinstance(value, dict), "callback contains a malformed nested object")
    return value


def safe_event(etype: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Return a validated allowlist, never recursively serialize callback payloads."""
    checker._require(isinstance(raw, dict), "callback must be a JSON object")
    event = _object(raw, "event")
    message = _object(event, "message")
    context = _object(event, "context")
    action = _object(event, "action")
    value = _object(action, "value")
    result: dict[str, Any] = {
        "event_id": _object(raw, "header").get("event_id"),
        "chat_id": event.get("chat_id") or message.get("chat_id") or context.get("open_chat_id"),
    }
    if etype == "im.message.receive_v1":
        result.update(message_id=message.get("message_id"), create_time=message.get("create_time"))
        mentions = message.get("mentions")
        if mentions is None:
            mentions = []
        checker._require(isinstance(mentions, list) and all(isinstance(m, dict) for m in mentions),
                         "callback contains malformed mentions")
        result["mentions"] = [{k: v for k, v in {
            "id": _object(m, "id").get("open_id"),
            "mentioned_type": m.get("mention_type") or m.get("mentioned_type")}.items() if v is not None}
            for m in mentions]
    elif etype.startswith("im.message.reaction."):
        reaction_type = _object(event, "reaction_type")
        user_id = _object(event, "user_id")
        operator_type = event.get("operator_type")
        checker._require(operator_type is None or isinstance(operator_type, str), "callback operator type is malformed")
        namespace, actor_id, actor_app_id = None, None, None
        if operator_type == "user":
            # union_id has the same namespace across our applications; open_id does not.
            namespace, actor_id = "user-union", user_id.get("union_id")
        elif operator_type in {"app", "bot"}:
            namespace, actor_id = "bot-app", event.get("app_id")
            actor_app_id = actor_id
        if actor_id is not None:
            checker._require(isinstance(actor_id, str) and bool(actor_id), "callback actor identifier is malformed")
        canonical_actor = f"{namespace}\0{actor_id}" if actor_id else None
        result.update(message_id=event.get("message_id"), reaction_type=reaction_type.get("emoji_type"),
                      actor_id_hash=hashlib.sha256(canonical_actor.encode("utf-8")).hexdigest() if canonical_actor else None,
                      actor_namespace=namespace if canonical_actor else None, operator_type=operator_type,
                      actor_app_id=actor_app_id, action_time=event.get("action_time"))
    elif etype == "card.action.trigger":
        operator_union = _object(event, "operator").get("union_id")
        checker._require(operator_union is None or isinstance(operator_union, str), "callback operator union identifier is malformed")
        result.update(message_id=event.get("open_message_id") or context.get("open_message_id"),
                      card_id=value.get("card_id"),
                      action_id=action.get("action_id") or value.get("action_id"), probe_marker=value.get("probe"),
                      operator_has_union_id=bool(operator_union))
    elif etype.startswith("im.chat.member."):
        users = event.get("users")
        if users is None:
            users = []
        checker._require(isinstance(users, list) and all(isinstance(u, dict) for u in users), "callback members are malformed")
        for key in ("app_id", "member_id"):
            checker._require(all(u.get(key) is None or isinstance(u[key], str) for u in users), "callback member identifier is malformed")
        result["member_app_ids"] = sorted({u["app_id"] for u in users if u.get("app_id")})
        result["member_ids"] = sorted({u["member_id"] for u in users if u.get("member_id")})
    result = {key: val for key, val in result.items() if val is not None}
    checker.validate_row(result)
    return result


def _write(path: Path, row: dict[str, Any]) -> None:
    checker.validate_row(row)
    checker.write_owned(path, (json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"), append=True)


def record(path: Path, run_id: str, bot: str, app_id: str, session_state: dict[str, str], etype: str, data: Any) -> None:
    try:
        raw = json.loads(_marshal_data(data))
    except Exception:
        # Never stringify SDK exceptions or objects: they can contain event bodies.
        raw = None
    row = {"schema_version": checker.SCHEMA_VERSION, "run_id": run_id, "t_recv": time.time(),
           "bot": bot, "app_id": app_id, "session_id": session_state.get("session_id", "unconnected"), "type": etype}
    try:
        row.update(safe_event(etype, raw))
    except checker.EvidenceError:
        # Preserve a fixed rejection marker, never a malformed payload or exception.
        row["type"] = "_event_rejected"
    if etype == "im.chat.member.bot.added_v1":
        # This event is delivered to the app that was added; bind receipt to the authenticated WS app.
        row["target_app_id"] = app_id
    with LOCK:
        _write(path, row)


def handler_for(path: Path, run_id: str, bot: str, app_id: str, session_state: dict[str, str]):
    import lark_oapi as lark
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
    b = lark.EventDispatcherHandler.builder("", "")
    for etype, register in (
        ("im.message.receive_v1", "register_p2_im_message_receive_v1"),
        ("im.message.reaction.created_v1", "register_p2_im_message_reaction_created_v1"),
        ("im.message.reaction.deleted_v1", "register_p2_im_message_reaction_deleted_v1"),
        ("im.chat.member.bot.added_v1", "register_p2_im_chat_member_bot_added_v1"),
        ("im.chat.member.bot.deleted_v1", "register_p2_im_chat_member_bot_deleted_v1"),
        ("im.chat.member.user.added_v1", "register_p2_im_chat_member_user_added_v1"),
        ("im.chat.member.user.deleted_v1", "register_p2_im_chat_member_user_deleted_v1"),
        ("im.chat.member.user.withdrawn_v1", "register_p2_im_chat_member_user_withdrawn_v1"),
    ):
        b = getattr(b, register)(lambda data, _type=etype: record(path, run_id, bot, app_id, session_state, _type, data))

    def on_card(data):
        record(path, run_id, bot, app_id, session_state, "card.action.trigger", data)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已收到探针卡片操作"}})

    return b.register_p2_card_action_trigger(on_card).build()


def finalize_manifest(path: Path) -> None:
    """Bind the exact recorder stop time after the operator ends this run."""
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from hostd.safety import read_owned
    raw = read_owned(path)
    manifest = json.loads(raw.decode("utf-8"))
    checker.validate_recorder_manifest(manifest)
    manifest["window"]["end"] = max(time.time(), float(manifest["window"]["start"]) + 0.001)
    checker.write_owned(path, (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def run(path: Path, run_id: str, bot: str, app_id: str) -> None:
    try:
        import lark_oapi as lark
        from lark_oapi.ws.client import loop as sdk_loop
    except ImportError:
        raise SystemExit("怎么解决：使用安装了 hostd SDK 依赖的独立虚拟环境重新运行探针。\n"
                         "复制给 AI：检查 hostd 探针虚拟环境的 Feishu SDK 安装并重跑，不展示凭据。") from None
    actual_app_id, secret = fc.app_credentials(bot)
    if actual_app_id != app_id:
        raise SystemExit("Probe rejected: declared test bot app does not match its local profile")
    session_state: dict[str, str] = {"session_id": "unconnected"}

    stop_requested = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_requested.set())
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop_requested.set())

    class RecordingClient(lark.ws.Client):
        def start(self):
            async def wait_for_stop():
                await self._connect()
                receive_task = getattr(self, "_probe_receive_task", None)
                ping_task = sdk_loop.create_task(self._ping_loop())
                try:
                    while not stop_requested.is_set():
                        await asyncio.sleep(0.1)
                finally:
                    self._auto_reconnect = False
                    ping_task.cancel()
                    if receive_task is not None:
                        receive_task.cancel()
                        await asyncio.gather(receive_task, return_exceptions=True)
                    await asyncio.gather(ping_task, return_exceptions=True)
                    await self._disconnect()
            sdk_loop.run_until_complete(wait_for_stop())

        async def _connect(self):
            had_connection = self._conn is not None
            prior_tasks = asyncio.all_tasks(sdk_loop)
            await super()._connect()
            if not had_connection and self._conn is not None:
                new_tasks = asyncio.all_tasks(sdk_loop) - prior_tasks
                self._probe_receive_task = next((task for task in new_tasks if task is not asyncio.current_task()), None)
                # This runs only after the SDK's WebSocket handshake has returned a live _conn.
                session_id = f"{self._conn_id}:{uuid.uuid4()}"
                session_state["session_id"] = session_id
                _write(path, {"schema_version": checker.SCHEMA_VERSION, "run_id": run_id,
                              "t_recv": time.time(), "bot": bot, "app_id": app_id,
                              "session_id": session_id, "type": "_connection_started"})

        async def _disconnect(self):
            session_id = session_state.get("session_id", "unconnected")
            had_connection = self._conn is not None
            try:
                await super()._disconnect()
            finally:
                if had_connection:
                    _write(path, {"schema_version": checker.SCHEMA_VERSION, "run_id": run_id,
                                  "t_recv": time.time(), "bot": bot, "app_id": app_id,
                                  "session_id": session_id, "type": "_connection_closed"})
                session_state["session_id"] = "unconnected"

    client = RecordingClient(app_id, secret, event_handler=handler_for(path, run_id, bot, app_id, session_state),
                             log_level=lark.LogLevel.WARNING)
    try:
        client.start()
    except KeyboardInterrupt:
        raise
    except Exception:
        _write(path, {"schema_version": checker.SCHEMA_VERSION, "run_id": run_id, "t_recv": time.time(),
                      "bot": bot, "app_id": app_id, "session_id": session_state.get("session_id", "unconnected"),
                      "type": "_recorder_failed"})
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record sanitized events from declared hostd test bots")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    args = parser.parse_args(argv)
    try:
        scripts = str(Path(__file__).resolve().parents[2] / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from hostd.safety import read_owned
        manifest = json.loads(read_owned(args.manifest).decode("utf-8"))
        run_id, _chat, apps = checker.validate_recorder_manifest(manifest)
    except (OSError, json.JSONDecodeError, checker.EvidenceError) as exc:
        message = str(exc) if isinstance(exc, checker.EvidenceError) else "manifest is missing or unreadable"
        print(f"Probe recorder rejected: {message}", file=sys.stderr)
        return 2
    if set(apps) - set(fc.TEST_BOTS):
        print("Probe recorder rejected: only declared hostd test bots may connect", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": run_id, "target_chat_id": _chat, "apps": apps, "events_path": str(args.events)}, ensure_ascii=False))
    children = [mp.Process(target=run, args=(args.events, run_id, bot, app_id), daemon=True, name=bot)
                for bot, app_id in apps.items()]
    for child in children:
        child.start()
    try:
        for child in children:
            child.join()
    except KeyboardInterrupt:
        for child in children:
            if child.is_alive() and child.pid:
                os.kill(child.pid, signal.SIGTERM)
        for child in children:
            child.join(timeout=10)
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
    try:
        finalize_manifest(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError):
        print("Probe recorder could not finalize the run window. Remedy: verify the owner-only manifest path and set its end time to the recorder stop time.\n"
              "Copy to AI: Help me safely finalize this hostd probe manifest window without displaying credentials.", file=sys.stderr)
        return 1
    ok = all(p.exitcode == 0 for p in children)
    if not ok:
        print("Probe recorder could not keep every declared test-bot connection open. Remedy: verify the test-bot app configuration and event-subscription permission, then retry.\n"
              "Copy to AI: Help me repair the isolated hostd test-bot WebSocket setup and rerun the sanitized probe without exposing credentials.", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
