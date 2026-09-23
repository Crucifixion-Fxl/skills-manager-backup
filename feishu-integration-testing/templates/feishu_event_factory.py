"""飞书事件 payload 工厂 — L3-Feishu 测试基石

把这个文件复制到你的 tests/e2e/ 目录下，用于构造打 webhook 的事件 payload。

⚠️  关键陷阱：event.message.content 必须是 JSON 字符串，不是嵌套对象。
    漏 json.dumps 会让 FeishuAdapter 抛错且测试看不到。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import uuid4


def _unique_chat_id(prefix: str = "oc_test") -> str:
    """每个测试用唯一 chat_id，避免污染前一个测试的状态。"""
    return f"{prefix}_{uuid4().hex[:12]}"


def _now_ms() -> str:
    return str(int(time.time() * 1000))


def build_feishu_message_event(
    *,
    chat_id: str,
    text: str,
    message_type: str = "text",
    chat_type: str = "group",      # "group" | "p2p"
    open_id: str = "ou_test_user_001",
    union_id: str | None = None,
    thread_id: str | None = None,
    parent_id: str | None = None,
    root_id: str | None = None,
    mentions: list[dict] | None = None,
    app_id: str | None = None,
) -> dict[str, Any]:
    """构造 im.message.receive_v1 事件（飞书事件 schema 2.0）。

    参数对应飞书官方字段：
    - chat_id:       群/私聊 ID (oc_xxx)
    - text:          消息文本
    - message_type:  text / post / interactive / image / file ...
    - thread_id:     话题 ID（可选，启用话题模式后才有）
    - parent_id:     父消息 ID（话题/回复时用）
    - root_id:       根消息 ID（话题用）
    - mentions:      [{"key": "@_user_1", "id": {"open_id": "ou_xxx"}, "name": "X"}]
    """
    msg: dict[str, Any] = {
        "message_id": f"om_{uuid4().hex}",
        "chat_id": chat_id,
        "chat_type": chat_type,
        "message_type": message_type,
        "content": json.dumps({"text": text}, ensure_ascii=False),  # ← JSON 字符串！
        "create_time": _now_ms(),
    }
    if thread_id:
        msg["thread_id"] = thread_id
    if parent_id:
        msg["parent_id"] = parent_id
    if root_id:
        msg["root_id"] = root_id
    if mentions:
        msg["mentions"] = mentions

    return {
        "schema": "2.0",
        "header": {
            "event_id": str(uuid4()),
            "event_type": "im.message.receive_v1",
            "create_time": _now_ms(),
            "token": os.environ.get("FEISHU_VERIFICATION_TOKEN", "test-token"),
            "app_id": app_id or os.environ.get("FEISHU_APP_ID", "cli_test_app"),
            "tenant_key": "test_tenant_key",
        },
        "event": {
            "sender": {
                "sender_id": {
                    "open_id": open_id,
                    "union_id": union_id or f"on_{open_id}",
                    "user_id": f"u_{open_id}",
                },
                "sender_type": "user",
                "tenant_key": "test_tenant_key",
            },
            "message": msg,
        },
    }


def build_card_action_event(
    *,
    chat_id: str,
    message_id: str,
    open_id: str = "ou_test_user_001",
    action_value: dict | None = None,
    app_id: str | None = None,
) -> dict[str, Any]:
    """构造 card.action.trigger 事件（用户点击 Card 上按钮时飞书回调）。"""
    return {
        "schema": "2.0",
        "header": {
            "event_id": str(uuid4()),
            "event_type": "card.action.trigger",
            "create_time": _now_ms(),
            "token": os.environ.get("FEISHU_VERIFICATION_TOKEN", "test-token"),
            "app_id": app_id or os.environ.get("FEISHU_APP_ID", "cli_test_app"),
            "tenant_key": "test_tenant_key",
        },
        "event": {
            "operator": {
                "open_id": open_id,
                "tenant_key": "test_tenant_key",
            },
            "token": str(uuid4()),
            "action": {
                "tag": "button",
                "value": action_value or {"action": "test"},
            },
            "context": {
                "open_message_id": message_id,
                "open_chat_id": chat_id,
            },
        },
    }


def build_url_verification_request(challenge: str | None = None) -> dict[str, Any]:
    """飞书首次配置 webhook 时发的 URL Challenge。Bot 必须原样回显 challenge。"""
    return {
        "type": "url_verification",
        "challenge": challenge or uuid4().hex,
        "token": os.environ.get("FEISHU_VERIFICATION_TOKEN", "test-token"),
    }
