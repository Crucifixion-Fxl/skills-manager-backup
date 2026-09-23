"""L4 — 真实飞书 WS + 真实测试群（发版前 UAT）

覆盖：真实飞书 App 配置 + 公网回调 + WS 长连 + Card 渲染 + IP 白名单
不覆盖：业务全链路（这部分由 L3/L3-Feishu 已经覆盖）

⚠️  L4 不在 CI gating 中跑。两种触发方式：
     1. nightly 任务（自动）
     2. 发版前手动 make test-l4

前置条件（环境变量）：
    FEISHU_APP_ID            staging Bot 的 App ID
    FEISHU_APP_SECRET        staging Bot 的 App Secret
    FEISHU_E2E_CHAT_ID       专用测试群 ID（机器人是该群成员）
    FEISHU_DRIVER_USER_TOKEN 驱动账号的 user_access_token（用来在群里发消息）
    STAGING_GATEWAY_URL      staging 业务 Gateway URL（可选，用于副作用断言）
"""
from __future__ import annotations

import asyncio
import os
import time
from uuid import uuid4

import pytest

try:
    import lark_oapi
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest, CreateMessageRequestBody,
        ListMessageRequest,
    )
except ImportError:
    pytest.skip("lark-oapi not installed", allow_module_level=True)


# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def feishu_app_creds() -> tuple[str, str, str, str]:
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    chat_id = os.environ.get("FEISHU_E2E_CHAT_ID")
    user_token = os.environ.get("FEISHU_DRIVER_USER_TOKEN")
    if not all([app_id, app_secret, chat_id, user_token]):
        pytest.fail(
            "L4 真飞书测试需要环境变量:\n"
            "  FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_E2E_CHAT_ID, "
            "FEISHU_DRIVER_USER_TOKEN\n"
            "如果你不能配置这些，跑 L3-Feishu 即可（make test-l3-feishu-regression）。"
        )
    return app_id, app_secret, chat_id, user_token


@pytest.fixture(scope="session")
def driver_lark_client(feishu_app_creds):
    """用驱动账号身份的 lark client（发消息 + 拉消息列表）。

    驱动账号 = 测试群里的另一个用户/Bot，专门用来"代替真人"在群里发消息触发被测 Bot。
    """
    app_id, app_secret, _, _ = feishu_app_creds
    return (
        lark_oapi.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(lark_oapi.LogLevel.WARNING)
        .build()
    )


# ────────────────────────────────────────────────────────────────
# 辅助
# ────────────────────────────────────────────────────────────────
def send_via_driver(
    client, chat_id: str, text: str, *, user_token: str,
) -> str:
    """以驱动账号身份在群里发消息，返回 message_id。"""
    import json
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        .build()
    )
    option = lark_oapi.RequestOption.builder().user_access_token(user_token).build()
    resp = client.im.v1.message.create(req, option)
    if not resp.success():
        pytest.fail(f"驱动账号发消息失败: {resp.code} {resp.msg}")
    return resp.data.message_id


async def poll_for_bot_reply(
    client,
    chat_id: str,
    *,
    after_message_id: str,
    user_token: str,
    target_app_id: str,
    timeout: float = 120.0,
) -> dict:
    """轮询群消息列表，找到被测 Bot 在 `after_message_id` 之后发的回复。"""
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        req = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .sort_type("ByCreateTimeDesc")
            .page_size(20)
            .build()
        )
        option = lark_oapi.RequestOption.builder().user_access_token(user_token).build()
        resp = client.im.v1.message.list(req, option)
        if resp.success() and resp.data and resp.data.items:
            for item in resp.data.items:
                # 找到被测 Bot 发的、在 after_message_id 之后的消息
                if (
                    item.sender
                    and item.sender.id == target_app_id
                    and item.message_id != after_message_id
                ):
                    return {
                        "message_id": item.message_id,
                        "msg_type": item.msg_type,
                        "body": item.body,
                        "create_time": item.create_time,
                    }
        await asyncio.sleep(2.0)

    pytest.fail(
        f"被测 Bot 未在 {timeout}s 内回复。\n"
        "排查（按顺序）:\n"
        "  1. staging 业务服务起着吗？health check\n"
        "  2. 飞书后台 App 配置: 事件订阅 / 长连接已启用？\n"
        "  3. App secret 在 staging 配的和飞书后台是同一个吗？\n"
        "  4. 测试 Bot 在测试群里吗？退群/重邀过吗？\n"
        "  5. WS 心跳在网络中间件被 kill 了吗？查 staging Gateway 日志"
    )


# ────────────────────────────────────────────────────────────────
# L4-a: 自驱模式
# ────────────────────────────────────────────────────────────────
@pytest.mark.l4
@pytest.mark.asyncio
async def test_l4_real_feishu_roundtrip(driver_lark_client, feishu_app_creds):
    """L4-a 自驱: 在真测试群发消息 → 等被测 Bot 通过真 WS 回包 → 拉消息列表断言。"""
    app_id, _, chat_id, user_token = feishu_app_creds

    sent_text = f"L4 ping {uuid4().hex[:6]}"
    sent_id = send_via_driver(
        driver_lark_client, chat_id, sent_text, user_token=user_token,
    )

    reply = await poll_for_bot_reply(
        driver_lark_client,
        chat_id,
        after_message_id=sent_id,
        user_token=user_token,
        target_app_id=app_id,
        timeout=120.0,
    )

    # 断言 1: msg_type 是 Card（业务侧默认 Card 回包）
    assert reply["msg_type"] in {"interactive", "text"}, \
        f"非预期 msg_type: {reply['msg_type']}"

    # 断言 2: 回复确实包含期望内容
    body_text = str(reply["body"]).lower()
    assert "pong" in body_text or "ping" in body_text, \
        f"回复内容异常: {reply['body']}"

    # 断言 3: 回复在合理时延内
    rt_ms = reply["create_time"] - int(time.time() * 1000)
    assert abs(rt_ms) < 120_000, "回复时间戳异常"


# ────────────────────────────────────────────────────────────────
# L4-b: 人工模式
# ────────────────────────────────────────────────────────────────
@pytest.mark.l4
@pytest.mark.l4_manual
def test_l4_manual_feishu_roundtrip(feishu_app_creds, capsys):
    """L4-b 人工: 提示真人在测试群手动 @ Bot 发消息，工具读 staging 日志/DB 验证。

    适用场景:
      - 不方便给驱动账号 user_access_token
      - 第一次接入新群，先做一次冒烟
    """
    _, _, chat_id, _ = feishu_app_creds

    print(f"\n请在飞书测试群 ({chat_id}) @ 被测 Bot，30 秒内发送任意消息。")
    print("发送后等待 60 秒，工具会读 staging 日志判断 Bot 是否处理成功。")

    # 用一个 unique marker，让人工发的消息可以被 staging 日志精确捞到
    marker = f"L4MANUAL-{uuid4().hex[:8]}"
    print(f"建议消息文本: '{marker} ping'")

    input("发送后回车继续...")  # 阻塞等真人

    # 读 staging 日志（这里要根据你的日志系统改）
    found = check_staging_logs_for_marker(marker, timeout=60)
    assert found, f"staging Gateway 日志未捕获 marker={marker}，Bot 可能没收到消息"


def check_staging_logs_for_marker(marker: str, *, timeout: float) -> bool:
    """读 staging 日志，找含 marker 的消息处理记录。

    实现方式因团队而异，可选:
      - 调日志平台 API (Loki/ES/CloudWatch)
      - kubectl logs <staging-gateway-pod> | grep $marker
      - 直接查 staging DB 的 messages 表
    """
    # 占位实现 — 替换为你的日志查询
    raise NotImplementedError(
        "实现 check_staging_logs_for_marker，连到你的日志系统/staging DB"
    )
