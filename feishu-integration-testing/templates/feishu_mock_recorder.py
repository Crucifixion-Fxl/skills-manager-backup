"""测试时 patch 掉 lark-oapi 出口，让 Bot 的回包写到本地 recorder。

用法（pytest fixture）:
    @pytest.fixture
    def feishu_mock_recorder(monkeypatch):
        recorder = FeishuMockRecorder()
        monkeypatch.setattr(
            "your_pkg.channels.feishu._lark_client.im.v1.message.create",
            recorder.record_create,
        )
        monkeypatch.setattr(
            "your_pkg.channels.feishu._lark_client.im.v1.message.reply",
            recorder.record_reply,
        )
        monkeypatch.setattr(
            "your_pkg.channels.feishu._lark_client.im.v1.message.patch",
            recorder.record_patch,
        )
        yield recorder

更彻底的方案: 在生产代码里把 `_lark_client` 改成依赖注入 (Protocol)，
测试直接注入 FakeLarkClient，避免 monkeypatch 路径漂移。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class FeishuMockRecorder:
    """记录所有出站到飞书的消息（create / reply / patch）。"""

    outbound: list[dict[str, Any]] = field(default_factory=list)
    patches: list[dict[str, Any]] = field(default_factory=list)

    # ────────────────────────────────────────────────
    # lark-oapi message.create — 主动发新消息
    # ────────────────────────────────────────────────
    def record_create(self, request, option=None):
        body = request.request_body if hasattr(request, "request_body") else request
        record = {
            "kind": "create",
            "chat_id": getattr(body, "receive_id", None),
            "msg_type": getattr(body, "msg_type", None),
            "content": getattr(body, "content", None),
            "ts": time.time(),
            "message_id": f"om_{uuid4().hex}",
        }
        self.outbound.append(record)
        return _fake_lark_response(record["message_id"])

    # ────────────────────────────────────────────────
    # lark-oapi message.reply — 在话题/线程内回复
    # ────────────────────────────────────────────────
    def record_reply(self, request, option=None):
        body = request.request_body if hasattr(request, "request_body") else request
        record = {
            "kind": "reply",
            "reply_to_message_id": getattr(request, "message_id", None),
            "chat_id": _extract_chat_id_from_reply(request),
            "msg_type": getattr(body, "msg_type", None),
            "content": getattr(body, "content", None),
            "ts": time.time(),
            "message_id": f"om_{uuid4().hex}",
        }
        self.outbound.append(record)
        return _fake_lark_response(record["message_id"])

    # ────────────────────────────────────────────────
    # lark-oapi message.patch — 流式更新已发出的卡片
    # ────────────────────────────────────────────────
    def record_patch(self, request, option=None):
        body = request.request_body if hasattr(request, "request_body") else request
        record = {
            "kind": "patch",
            "message_id": getattr(request, "message_id", None),
            "content": getattr(body, "content", None),
            "ts": time.time(),
        }
        self.patches.append(record)
        return _fake_lark_response(record["message_id"])

    # ────────────────────────────────────────────────
    # 测试断言辅助
    # ────────────────────────────────────────────────
    def cards_for_chat(self, chat_id: str) -> list[dict[str, Any]]:
        return [
            r for r in self.outbound
            if r["chat_id"] == chat_id and r["msg_type"] == "interactive"
        ]

    def assert_card_replied(self, chat_id: str) -> dict[str, Any]:
        cards = self.cards_for_chat(chat_id)
        assert cards, f"未捕获 chat_id={chat_id} 的 Card 回包"
        return cards[-1]

    def patch_count(self, message_id: str) -> int:
        return sum(1 for p in self.patches if p["message_id"] == message_id)


def _extract_chat_id_from_reply(request) -> str | None:
    """从 reply 请求里推断 chat_id（lark-oapi 不直接给，但生产代码通常会传）。"""
    return getattr(request, "_meta_chat_id", None)


def _fake_lark_response(message_id: str):
    """模拟 lark-oapi 的 response 对象。"""

    class _Resp:
        code = 0
        msg = "success"

        def success(self):
            return True

        @property
        def data(self):
            class _Data:
                pass
            d = _Data()
            d.message_id = message_id
            return d

    return _Resp()


# ────────────────────────────────────────────────
# 替代方案: FakeLarkClient (DI 模式)
# ────────────────────────────────────────────────
class FakeLarkClient:
    """如果生产代码把 lark client 抽象成 Protocol，这个 fake 可以直接注入，
    避免 monkeypatch 路径漂移问题。"""

    def __init__(self):
        self.recorder = FeishuMockRecorder()
        self.im = _FakeIM(self.recorder)


class _FakeIM:
    def __init__(self, recorder):
        self.v1 = _FakeIMv1(recorder)


class _FakeIMv1:
    def __init__(self, recorder):
        self.message = _FakeMessage(recorder)


class _FakeMessage:
    def __init__(self, recorder: FeishuMockRecorder):
        self._recorder = recorder

    def create(self, request, option=None):
        return self._recorder.record_create(request, option)

    def reply(self, request, option=None):
        return self._recorder.record_reply(request, option)

    def patch(self, request, option=None):
        return self._recorder.record_patch(request, option)
