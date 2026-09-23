# 飞书集成测试断言模式（防假绿）

L3 / L3-Feishu 测试常见的假绿（vacuous test）原因：只看 HTTP 200，不看副作用；只看回复内容存在，不看持久化；只看 mock 被调用，不看调用参数对不对。

下面是按副作用维度组织的断言清单，每个 L3-Feishu 测试至少应覆盖 §1 + §3 + §4 三类。

## 1. HTTP 层（最低门槛）

```python
assert resp.status_code == 200
data = resp.json()

# Webhook ACK schema
assert data == {"code": 0, "msg": "ok"}

# 业务 API schema (OpenAI 兼容)
assert data["object"] == "chat.completion"
assert "choices" in data and data["choices"]
```

⚠️  只断 200 远远不够：Adapter 解析失败时业务通常仍返 200（异步处理把异常吞了）。

## 2. 业务响应内容

```python
content = data["choices"][0]["message"]["content"]
assert content, "回复不应为空"
assert "expected_keyword" in content, f"期望关键词，实际: {content}"

# 或用更宽松的语义断言（适合 LLM 输出）
assert any(kw in content.lower() for kw in ["pong", "yes", "ok"]), \
    f"期望肯定回复，实际: {content!r}"
```

## 3. 持久化（最重要）

```python
# Workspace / Session 落库
async with db_pool.acquire() as conn:
    row = await conn.fetchrow(
        "SELECT workspace_id, agent_id, chat_type FROM workspaces "
        "WHERE chat_id=$1", chat_id,
    )
    assert row, "Workspace 应在 webhook 处理后落库"
    assert row["agent_id"], "Agent 应被创建"

# Conversation 持久化（Letta / 其他）
conv_id = data.get("conversation_id") or resp.headers.get("X-Conversation-Id")
assert conv_id, "缺 conversation_id — 上游服务可能没收到"

# 多轮消息历史
conv = letta_client.conversations.retrieve(conv_id)
assert len(conv.messages) >= 2  # user + assistant
```

## 4. 飞书出口（仅 L3-Feishu）

```python
# Card 回包结构
sent_card = await wait_for_outbound_card(mock_recorder, chat_id, timeout=60)
assert sent_card["msg_type"] == "interactive", "默认应回 Card 而非 text"

payload = json.loads(sent_card["content"])
assert payload.get("schema") == "2.0" or "elements" in payload, \
    "Card 应符合 Card 2.0 schema"

# 流式 patch 次数（如果声称是流式）
patch_count = mock_recorder.patch_count(sent_card["message_id"])
assert patch_count >= 1, "流式 Card 应至少 patch 一次"

# 失败降级路径
# 触发后端异常 → 期望降级为纯文本错误卡
sent_after_error = mock_recorder.cards_for_chat(chat_id)[-1]
assert sent_after_error["msg_type"] in ("text", "interactive")
if sent_after_error["msg_type"] == "interactive":
    error_payload = json.loads(sent_after_error["content"])
    # 错误卡应能被识别（例如 header.template = "red"）
```

## 5. 副作用 / 审计

```python
# 业务事件总线 / 审计日志
assert mock_event_bus.has_event(
    name="feishu_message_received",
    chat_id=chat_id,
    user_id=open_id,
)

# 工具调用记录
tool_calls = await get_tool_calls(conv_id)
assert any(tc["name"] == "expected_tool" for tc in tool_calls)

# 外部 webhook 转发（如 PagerDuty / Slack 同步）
assert mock_pagerduty.incidents_created == 0  # 或 >= 1
```

## 6. 时序断言（防止异步 race）

```python
# 用 wait_for_X 而不是 sleep(N)
session = await wait_for_session(db_pool, chat_id, timeout=60)
# ↑ 内部带 deadline + 1s 轮询，比 await asyncio.sleep(60) 强百倍

# 验证消息处理顺序（同 chat 多条）
events = mock_recorder.outbound_for_chat(chat_id)
assert [e["kind"] for e in events] == ["create", "patch", "patch"]
# ↑ 创建占位卡 → 流式 patch → 终态 patch
```

## 7. 防假绿自检（写测试时强制）

写完测试后，**故意把生产代码改坏**，确认测试真的会 fail：

| 故意破坏 | 测试应该 fail 的原因 |
|---------|--------------------|
| 把 FeishuAdapter 的 chat_id 提取改错 | Workspace 不会落库 → §3 fail |
| 把 Card 回包改成 text | §4 fail (msg_type 不是 interactive) |
| 把 Letta 写库 commit 注释掉 | §3 fail (conv_id 拿不到 / messages 不增长) |
| 把签名验证关掉 | §4 (`test_fe2e_FI_040_rejects_invalid_signature`) fail |

如果上面任何一条改了之后测试还过，说明断言是**假绿**，必须补强。
