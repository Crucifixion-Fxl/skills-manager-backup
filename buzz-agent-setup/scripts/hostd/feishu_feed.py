"""One child process per Feishu app: holds that app's long connection and writes one JSON line per event to stdout.

lark-oapi's ws client binds a module-level event loop, so a process can hold only one connection (P0-2). The child
only reports which chat an event belongs to and its type; the parent decides what to do. Card callbacks get an
immediate toast here because Feishu wants an answer within 3 seconds (P0-3).
"""
from __future__ import annotations

import json
import hashlib
import logging
import sys
import time
from pathlib import Path

import lark_oapi as lark
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

EVENTS = ("im.message.receive_v1", "im.message.reaction.created_v1", "im.message.reaction.deleted_v1",
          "im.chat.member.bot.added_v1", "im.chat.member.bot.deleted_v1", "im.chat.member.user.added_v1",
          "im.chat.member.user.deleted_v1", "im.chat.member.user.withdrawn_v1")


def emit(row: dict) -> None:
    sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def chat_of(raw: dict) -> str:
    ev = raw.get("event") or {}
    return ev.get("chat_id") or (ev.get("message") or {}).get("chat_id") or (ev.get("context") or {}).get("open_chat_id") or ""


def handler(app_id: str, *, evidence_wire=None):
    b = lark.EventDispatcherHandler.builder("", "")

    def on(etype):
        def f(data):
            raw = json.loads(lark.JSON.marshal(data))
            ev = raw.get("event") or {}
            msg = ev.get("message") or {}
            row = {"t": time.time(), "app": app_id, "type": etype, "chat_id": chat_of(raw),
                  "message_id": msg.get("message_id") or ev.get("message_id"),
                  "root_id": msg.get("root_id") or "", "thread_id": msg.get("thread_id") or "",
                  "event_id": (raw.get("header") or {}).get("event_id") or raw.get("event_id")}
            if etype in ("im.message.reaction.created_v1", "im.message.reaction.deleted_v1"):
                # Preserve actual typed SDK fields without moving operator PII
                # across stdout. Open/user identifiers are receiver-app scoped.
                reaction = ev.get("reaction_type")
                row["reaction_type"] = reaction.get("emoji_type") if isinstance(reaction, dict) else None
                row["operator_type"] = ev.get("operator_type")
                row["action_time"] = ev.get("action_time")
                operator = ev.get("user_id")
                row["_operator_app_id"] = ev.get("app_id")
                row["_operator_user_present"] = operator is not None
                if isinstance(operator, dict):
                    for key, namespace in (("union_id", "user-union"), ("open_id", "user-open"), ("user_id", "user-id")):
                        value = operator.get(key)
                        if isinstance(value, str) and value:
                            scope = "" if key == "union_id" else app_id + "\0"
                            row["operator_" + key + "_hash"] = hashlib.sha256((namespace + "\0" + scope + value).encode()).hexdigest()
                    if row.get("operator_union_id_hash"):
                        row["actor_namespace"] = "user-union"
                        row["actor_id_hash"] = row["operator_union_id_hash"]
                if ev.get("operator_type") == "app" and isinstance(ev.get("app_id"), str) and ev["app_id"]:
                    row["actor_namespace"] = "bot-app"
                    row["actor_app_id"] = ev["app_id"]
                    row["actor_id_hash"] = hashlib.sha256(("bot-app\0" + ev["app_id"]).encode()).hexdigest()
            if etype == "im.chat.member.bot.added_v1":
                # Actual SDK field is UserId operator_id, not a synthetic actor.
                # Trusted IPC only: the parent must verify membership/identity;
                # none of these identifiers belongs in console/status logs.
                actor = ev.get("operator_id")
                if isinstance(actor, dict):
                    row["operator_id"] = {key: actor[key] for key in ("open_id", "union_id", "user_id")
                                          if isinstance(actor.get(key), str) and actor[key]}
            emit(evidence_wire.attach(row,raw) if evidence_wire is not None else row)
        return f

    for etype in EVENTS:
        b = getattr(b, "register_p2_" + etype.replace(".", "_"))(on(etype))

    def card(data):
        raw = json.loads(lark.JSON.marshal(data))
        ev = raw.get("event") or {}
        # Internal trusted IPC only; these fields must never be copied into diagnostic/status logs.
        row = {"t": time.time(), "app": app_id, "type": "card.action.trigger", "chat_id": chat_of(raw),
               "event_id": (raw.get("header") or {}).get("event_id") or raw.get("event_id")}
        for key in ("operator", "token", "action", "context", "current_message"):
            row[key] = ev.get(key)
        emit(evidence_wire.attach(row,raw) if evidence_wire is not None else row)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "操作已收到并排队处理，结果会另行通知"}})
    return b.register_p2_card_action_trigger(card).build()


class EventClient(lark.ws.Client):
    """SDK adapter: _conn is assigned only after the WebSocket handshake succeeds.

    _connect/_disconnect are private SDK hooks, so offline tests exercise the installed
    implementation with only the transport mocked. Do not infer connection from start().
    """
    def __init__(self,*args,evidence_wire=None,**kwargs):
        super().__init__(*args,**kwargs);self.evidence_wire=evidence_wire

    async def _connect(self):
        was_connected = self._conn is not None
        await super()._connect()
        if not was_connected and self._conn is not None:
            row={"t": time.time(), "app": self._app_id, "type": "_connected"}
            if self.evidence_wire is not None:
                self.evidence_wire.connected(self);row=self.evidence_wire.attach(row)
            emit(row)

    async def _disconnect(self):
        was_connected = self._conn is not None
        try:
            await super()._disconnect()
        finally:
            if was_connected:
                row={"t": time.time(), "app": self._app_id, "type": "_disconnected"}
                if self.evidence_wire is not None:
                    if self._conn is None:row=self.evidence_wire.attach(row)
                    self.evidence_wire.closed()
                emit(row)


def main() -> int:
    app_id, config_dir, data_dir = sys.argv[1:4]
    evidence_wire=None
    if len(sys.argv)!=4:
        if len(sys.argv)!=8 or sys.argv[4]!='--sdk-evidence-run-id' or sys.argv[6]!='--sdk-evidence-nonce':raise ValueError()
        from hostd.sdk_evidence import FeedEvidenceWire
        evidence_wire=FeedEvidenceWire(app_id,sys.argv[5],sys.argv[7])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from secrets_store import app_secret
    secret = app_secret(app_id, config_dir, data_dir)
    emit({"t": time.time(), "app": app_id, "type": "_connecting"})
    # SDK error logs may contain payloads and credential-bearing URLs. Never forward them.
    from lark_oapi.core.log import logger
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    EventClient(app_id, secret, event_handler=handler(app_id,evidence_wire=evidence_wire),
        evidence_wire=evidence_wire,log_level=lark.LogLevel.WARNING).start()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except (Exception, SystemExit):
        # Never let SDK/credential exceptions print a token or payload in a traceback.
        sys.stderr.write("飞书应用连接启动失败；怎么解决：检查配置、应用权限与网络。复制给 AI：检查 hostd 飞书子进程启动。\n")
        code = 1
    sys.exit(code)
