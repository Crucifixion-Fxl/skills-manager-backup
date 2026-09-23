"""L3 — HTTP 直打业务自家 API（最常用打法）

覆盖：业务全链路（Gateway → 后端 → DB → LLM/工具）
跳过：飞书入口本身（由 L3-Feishu 单独覆盖）

启动前置：make dev
运行：     make test-l3
"""
from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest


def _unique_chat_id() -> str:
    """每个测试必须用唯一 chat_id，避免污染前一个测试的状态。"""
    return f"oc_l3test_{uuid4().hex[:12]}"


def _extract_content(payload: dict) -> str:
    return payload["choices"][0]["message"]["content"]


def _extract_sse_chunks(text: str) -> list[dict]:
    """SSE 流响应解析；记得过滤掉 metadata 事件（无 choices 字段）。"""
    chunks: list[dict] = []
    for line in text.splitlines():
        if not line.startswith("data: ") or line.endswith("[DONE]"):
            continue
        try:
            chunks.append(json.loads(line[6:]))
        except json.JSONDecodeError:
            continue
    # 关键：业务侧通常会在 [DONE] 前发 metadata 事件（含 conversation_id 等），
    # 这些事件没有 choices 字段，必须过滤
    return [c for c in chunks if "choices" in c]


# ────────────────────────────────────────────────────────────────
# 用例 1：首次消息触发会话/Workspace 自动创建
# ────────────────────────────────────────────────────────────────
@pytest.mark.l3_2
@pytest.mark.asyncio
async def test_L3_2_first_message_creates_session(
    api: httpx.AsyncClient,
    cleanup_sessions,
):
    chat_id = _unique_chat_id()

    resp = await api.post(
        "/v1/chat/completions",
        json={
            "model": "your-model",
            "messages": [{"role": "user", "content": "Hello, first message"}],
        },
        headers={"X-Chat-Id": chat_id},
        timeout=120.0,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert _extract_content(data), "Bot 应回复首次消息"

    # 持久化断言
    conv_id = data.get("conversation_id") or resp.headers.get("X-Conversation-Id")
    assert conv_id, "应持久化 conversation_id"


# ────────────────────────────────────────────────────────────────
# 用例 2：同 chat_id 上下文记忆
# ────────────────────────────────────────────────────────────────
@pytest.mark.l3_2
@pytest.mark.asyncio
async def test_L3_2_context_memory_within_chat(api: httpx.AsyncClient):
    chat_id = _unique_chat_id()
    headers = {"X-Chat-Id": chat_id}

    await api.post(
        "/v1/chat/completions",
        json={"model": "your-model",
              "messages": [{"role": "user", "content": "记住：我的猫叫小花"}]},
        headers=headers,
        timeout=120.0,
    )

    r2 = await api.post(
        "/v1/chat/completions",
        json={"model": "your-model",
              "messages": [{"role": "user", "content": "我的猫叫什么名字？"}]},
        headers=headers,
        timeout=120.0,
    )
    assert r2.status_code == 200
    assert "小花" in _extract_content(r2.json()), \
        f"期望记住事实，实际: {_extract_content(r2.json())}"


# ────────────────────────────────────────────────────────────────
# 用例 3：SSE 流式响应（验证 metadata 过滤）
# ────────────────────────────────────────────────────────────────
@pytest.mark.l3_2
@pytest.mark.asyncio
async def test_L3_2_sse_streaming(api: httpx.AsyncClient):
    chat_id = _unique_chat_id()

    async with api.stream(
        "POST", "/v1/chat/completions",
        json={
            "model": "your-model",
            "messages": [{"role": "user", "content": "say pong"}],
            "stream": True,
        },
        headers={"X-Chat-Id": chat_id},
        timeout=120.0,
    ) as resp:
        assert resp.status_code == 200
        body = "".join([chunk async for chunk in resp.aiter_text()])

    chunks = _extract_sse_chunks(body)
    assert chunks, "应有至少一条带 choices 的事件"
    assert any(c["choices"][0]["delta"].get("content") for c in chunks), \
        "应有非空 content delta"
