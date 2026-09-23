"""L3-Feishu — 构造飞书事件 payload 打 webhook

覆盖：FeishuAdapter 解析 + 签名验证 + URL Challenge + Card 回包结构 + 异步处理顺序
跳过：真实 WS 连接 + 真实飞书后台配置（由 L4 覆盖）

启动前置：make dev
运行：     make test-l3-feishu-regression

命名规范（建议）：
    test_fe2e_<MODULE>_<US_ID>_<behavior>
    例：test_fe2e_FI_010_message_receive_creates_session
"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from .feishu_event_factory import (
    build_card_action_event,
    build_feishu_message_event,
    build_url_verification_request,
)


# ────────────────────────────────────────────────────────────────
# 异步等待辅助
# ────────────────────────────────────────────────────────────────
async def wait_for_session(db_pool, chat_id: str, *, timeout: float = 60.0) -> dict:
    """轮询等业务 session/Workspace 落库（webhook 处理是异步的）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM <your_session_table> WHERE chat_id = $1",
                chat_id,
            )
            if row:
                return dict(row)
        await asyncio.sleep(1.0)
    pytest.fail(
        f"Session 未在 {timeout}s 内为 chat_id={chat_id} 创建。\n"
        "排查:\n"
        "  1. 业务日志: docker logs <svc> | grep FeishuAdapter\n"
        "  2. webhook 是否 200\n"
        "  3. 异步 worker 是否在跑"
    )


async def wait_for_outbound_card(
    feishu_mock_recorder, chat_id: str, *, timeout: float = 60.0,
) -> dict:
    """等业务通过 lark-oapi 发出的 Card 被 mock recorder 捕获。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        for record in feishu_mock_recorder.outbound:
            if record["chat_id"] == chat_id:
                return record
        await asyncio.sleep(0.5)
    pytest.fail(f"业务未在 {timeout}s 内回包给 chat_id={chat_id}")


# ────────────────────────────────────────────────────────────────
# US-FI-010: 飞书消息进入应自动创建 session
# ────────────────────────────────────────────────────────────────
@pytest.mark.l3_feishu
@pytest.mark.asyncio
async def test_fe2e_FI_010_message_receive_creates_session(
    api: httpx.AsyncClient,
    db_pool,
):
    """US-FI-010: 飞书新群第一条消息 → 业务 session 自动创建。

    Given: chat_id 在 DB 中不存在
    When:  飞书 webhook 收到 im.message.receive_v1
    Then:  Session 落库
    """
    chat_id = f"oc_l3f_{uuid4().hex[:10]}"
    event = build_feishu_message_event(chat_id=chat_id, text="ping")

    resp = await api.post("/channels/feishu/webhook", json=event)

    # webhook 立即 ACK（处理是异步的）
    assert resp.status_code == 200
    assert resp.json() == {"code": 0, "msg": "ok"}

    session = await wait_for_session(db_pool, chat_id, timeout=60)
    assert session is not None


# ────────────────────────────────────────────────────────────────
# US-FI-020: 话题（thread）独立会话
# ────────────────────────────────────────────────────────────────
@pytest.mark.l3_feishu
@pytest.mark.asyncio
async def test_fe2e_FI_020_thread_creates_separate_conversation(
    api: httpx.AsyncClient,
    db_pool,
):
    """US-FI-020: 同 chat_id 不同 thread → 不同业务 conversation。"""
    chat_id = f"oc_l3f_{uuid4().hex[:10]}"
    thread_a = f"omt_{uuid4().hex[:8]}"
    thread_b = f"omt_{uuid4().hex[:8]}"

    for thread_id in (thread_a, thread_b):
        await api.post(
            "/channels/feishu/webhook",
            json=build_feishu_message_event(
                chat_id=chat_id, text="hello", thread_id=thread_id,
            ),
        )

    await asyncio.sleep(3)  # 等异步处理
    async with db_pool.acquire() as conn:
        convs = await conn.fetch(
            "SELECT DISTINCT thread_id FROM <conversations_table> "
            "WHERE chat_id = $1",
            chat_id,
        )
    assert len(convs) >= 2, f"期望至少 2 个 thread，实际 {len(convs)}"


# ────────────────────────────────────────────────────────────────
# US-FI-030: 回包是 Card 2.0 格式
# ────────────────────────────────────────────────────────────────
@pytest.mark.l3_feishu
@pytest.mark.asyncio
async def test_fe2e_FI_030_reply_is_interactive_card(
    api: httpx.AsyncClient,
    feishu_mock_recorder,    # fixture: patch 掉 lark-oapi 出口
):
    """US-FI-030: Bot 默认用 Card 2.0 回包。"""
    chat_id = f"oc_l3f_{uuid4().hex[:10]}"
    await api.post(
        "/channels/feishu/webhook",
        json=build_feishu_message_event(chat_id=chat_id, text="say pong"),
    )

    card = await wait_for_outbound_card(feishu_mock_recorder, chat_id)
    assert card["msg_type"] == "interactive", \
        f"应回 Card 而非纯文本，实际 msg_type={card['msg_type']}"

    payload = json.loads(card["content"])
    assert payload.get("schema") == "2.0" or "elements" in payload, \
        "Card 应符合 Card 2.0 schema"


# ────────────────────────────────────────────────────────────────
# US-FI-040: 签名错误必须拒绝
# ────────────────────────────────────────────────────────────────
@pytest.mark.l3_feishu
@pytest.mark.asyncio
async def test_fe2e_FI_040_rejects_invalid_signature(api: httpx.AsyncClient):
    """US-FI-040: 缺签名 / 签名错误的 webhook 必须返回 401/403。"""
    chat_id = f"oc_l3f_{uuid4().hex[:10]}"
    event = build_feishu_message_event(chat_id=chat_id, text="ping")

    resp = await api.post(
        "/channels/feishu/webhook",
        json=event,
        headers={
            "X-Lark-Signature": "definitely-wrong",
            "X-Lark-Request-Timestamp": "1700000000",
            "X-Lark-Request-Nonce": "nonce",
        },
    )
    assert resp.status_code in (401, 403), \
        f"错误签名应被拒绝，实际 {resp.status_code}"


# ────────────────────────────────────────────────────────────────
# US-FI-050: URL Verification Challenge
# ────────────────────────────────────────────────────────────────
@pytest.mark.l3_feishu
@pytest.mark.asyncio
async def test_fe2e_FI_050_url_verification_challenge(api: httpx.AsyncClient):
    """US-FI-050: 飞书首次配置 webhook 时必须回显 challenge。"""
    challenge = uuid4().hex
    resp = await api.post(
        "/channels/feishu/webhook",
        json=build_url_verification_request(challenge),
    )
    assert resp.status_code == 200
    assert resp.json()["challenge"] == challenge


# ────────────────────────────────────────────────────────────────
# US-FI-060: Card 按钮点击触发回调
# ────────────────────────────────────────────────────────────────
@pytest.mark.l3_feishu
@pytest.mark.asyncio
async def test_fe2e_FI_060_card_action_trigger(
    api: httpx.AsyncClient,
    feishu_mock_recorder,
    db_pool,
):
    """US-FI-060: 用户点击 Card 按钮 → card.action.trigger 事件应被处理。"""
    chat_id = f"oc_l3f_{uuid4().hex[:10]}"
    msg_id = f"om_{uuid4().hex}"
    event = build_card_action_event(
        chat_id=chat_id,
        message_id=msg_id,
        action_value={"action": "approve", "ticket_id": "T-123"},
    )

    resp = await api.post("/channels/feishu/webhook", json=event)
    assert resp.status_code == 200

    # 断言副作用：业务应记录这次操作
    await asyncio.sleep(2)
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM <audit_log_table> "
            "WHERE chat_id = $1 AND action = 'approve'",
            chat_id,
        )
    assert row, "Card 按钮点击应被记录"
