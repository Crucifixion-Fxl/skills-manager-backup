---
name: feishu-integration-testing
description: 飞书 (Lark) IM 集成的分层测试方法论 — 覆盖飞书 WebSocket 长连接与 Webhook 双通道入口、Card 2.0 回包、消息回路、签名校验的 L1→L2→L3→L3-Feishu→L4 端到端测试策略。当用户需要为接入了飞书 IM 的服务设计测试、验证飞书消息回路、构造飞书事件 payload 打 webhook、用 mock 替代 lark-oapi 客户端、在 staging 跑真实飞书 UAT、或落地 "每个 User Story 必须有飞书 E2E" 这类强约束时必须使用此 skill。即便用户只说"加个飞书测试"、"飞书集成测了吗"、"smoke 跑不过"、"L3-Feishu 失败"、"webhook 签名怎么测"，也要触发。
---

# 飞书 (Lark) IM 集成分层测试方法论

## 适用范围

适用于**任何把飞书 IM 作为消息入口或回包通道的服务**：聊天机器人、AI Agent、群审批、值班通知、CS 工单接入等。本 skill 是 [`testing-strategy`](../testing-strategy/SKILL.md) 在「飞书 IM 通道」场景下的具体落地。

继承自父规约：测试左移、测试下沉、Bug 右侧追溯、防假绿测试、用例金字塔（L1:L2:L3:L4 ≈ 70:20:9:1）。

> **本 skill 在 ClawPlex 项目里的具体应用**见 §10 "项目案例"——ClawPlex 把 §5 的工作流写进 `CLAUDE.md` 规约五（每个 User Story 必须有 L3-Feishu 测试）作为强约束。其他项目可以选用其中的子集。

---

## 描述

本 skill 提供飞书 IM 集成的分层测试方法论，覆盖 WebSocket / Webhook 双通道、Card 2.0 回包、签名校验、L1→L2→L3→L3-Feishu→L4 端到端策略，并配套 4 个可复用模板（事件工厂、HTTP/Webhook/真飞书测试样例、Mock Recorder）和 1 份断言模式参考。下面分章节展开。

## 核心原则：飞书集成的三个关键事实（写测试前必须理解）

### 事实 1：消息入口是双通道，不是单通道

```
飞书服务器
   │
   ├─[主]─→ WebSocket 长连接 ──┐    (lark-oapi.ws.Client，长连模式)
   │                          │
   └─[备]─→ POST /<your-svc>/  │    (老式 webhook 模式，含签名)
            feishu/webhook ────┤
                               ▼
                       FeishuAdapter (反腐层 / ACL)
                               │
                               ▼
                       Domain Envelope (内部领域模型)
                               │
                               ▼
                       业务 Pipeline → 后续服务
```

- **WebSocket（主，推荐）**：`lark-oapi` 的 `lark_oapi.ws.Client`，跑在独立线程，事件需要跨线程回到主 asyncio loop（用 `call_soon_threadsafe`）。
- **Webhook（备 / 兼容）**：HTTP POST，含签名 + URL Challenge，立即 ACK 后异步处理。

**测试含义**：L3 飞书 E2E 不必起真实 WS — 可以**构造飞书原生事件 payload 直接 POST 打 webhook**，效果等价（都走同一段 `FeishuAdapter`）。真实 WS 留给 L4 UAT。

### 事实 2：飞书的 chat_id / thread_id 是测试隔离的边界

```
Feishu chat_id   ←→  你的业务"会话" / Workspace / Session
Feishu thread_id ←→  你的业务"话题" (可选)
                     同 chat 不同 thread 通常要求消息历史隔离
```

**测试含义**：
- **每个测试必须用唯一 chat_id**（推荐 `_unique_chat_id()` 工厂），否则会读到上一次测试残留的状态
- 验证「同 chat 上下文」要在同一 chat_id 内多轮发消息
- 验证「跨 thread 隔离」要同 chat_id + 不同 thread_id

### 事实 3：回包是 Card 2.0 流式更新，不是单条文本

典型的飞书 Bot 回包模式：
1. 收到消息 → 立刻发「⏳ 思考中」占位卡
2. 处理过程中 → `PatchMessageRequest` 更新同一张卡
3. 完成 → 替换为最终结果卡（含可折叠思考过程）
4. 失败 → 降级为纯文本

**测试含义**：断言不能只看「是否回了文本」，至少要验证：
- `msg_type == "interactive"`（Card 而非纯文本）
- Card 2.0 schema (`schema: "2.0"` 或含 `elements`)
- 流式 patch 次数（如果声称是流式）
- 失败降级路径（异常时仍能回纯文本）

---

## 规则

下面 §二（测试分层）+ §四（必备 Make Targets）+ §五（每个 US 必须有 L3-Feishu）+ §六（多侧断言）+ §八（调试 checklist）共同构成强约束：违反任意一条都不能 merge。具体规则编号化展开。

## 二、测试分层

| 层级 | 名称 | 入口 | 外部依赖 | 典型耗时 | pytest marker |
|------|------|------|---------|---------|---------------|
| **L1** | 单元测试 | 函数直调（mock 一切） | 无 | < 1s | `l1` |
| **L2** | 单服务集成 | 真实中间件 | DB / Redis / 业务依赖 | < 5s | `l2_*` |
| **L3** | 跨服务 E2E | 业务自家 HTTP API | docker-compose 全栈 | < 30s | `l3_1`/`l3_2` |
| **L3-Feishu** | 飞书事件 E2E | 构造飞书事件 payload 打 webhook | + 飞书 App 凭据（test app） | < 60s | `l3_feishu` |
| **L4** | UAT 验收 | **真实**飞书 WS + 真实测试群 | Staging + 测试群 + 驱动账号 | 无限 | `l4` |
| **smoke** | 关键路径 | 同 L3 但只跑最关键 | 同 L3 | < 5min 全套 | `smoke` |

**为什么要 L3-Feishu 单独一层**：L3 通常打业务自家 HTTP API（绕过飞书入口），覆盖业务逻辑；但 `FeishuAdapter`、签名验证、Card 构造、URL Challenge 这些**只有从飞书入口进来才会走到的代码**需要单独覆盖，否则线上接入新群时容易在签名 / Adapter 上翻车。

**为什么 L4 也是必须的**（不只 L3-Feishu）：L3-Feishu 用构造的 event 打 webhook，绕过了**真实 WS 长连接**和**飞书 App 配置正确性**的验证。下面这些只能 L4 抓到：
- App secret / verification token 在 staging 配错
- 公网回调地址在飞书后台没填（或填错）
- WS 心跳被网络中间件 kill
- Card 在真实飞书客户端里渲染异常（颜色 / 图标 / 按钮回调）
- IP 白名单 / 加密签名算法升级

---

## 示例

下面 §三给出四种发消息打法的对比矩阵 + 反例 / 正例代码块；§六给出多侧断言模板；模板目录 `templates/` 含 4 份可直接套用的 pytest 文件骨架。

## 三、四种发消息打法（按场景选）

| 你想验证 | 用哪种 | Marker | 启动成本 |
|---------|--------|--------|---------|
| Adapter 解析 / Card 构造 / 工具函数 | L1 直调 | `l1` | 无 |
| Workspace ↔ Session 落库 | L2 真中间件 | `l2_2` | 起本地 infra |
| 业务全链回路 | **L3 HTTP 直打**（推荐默认） | `l3_2` | 起本地 infra |
| FeishuAdapter + 卡片回包 + 签名 | **L3-Feishu Webhook 模拟** | `l3_feishu` | 起本地 infra |
| 真实 WebSocket + 真实群回包 | **L4 真飞书** | `l4` | Staging + 真群 + 驱动账号 |

### 打法 A：L3 — HTTP 直打业务 API（最常用）

**适用**：覆盖业务全链路（你的 Gateway → 后端 → DB → LLM/工具），跳过飞书入口本身。

**为什么这是默认**：CI 友好（不依赖飞书 token）、快、稳；飞书入口的解析逻辑由 L3-Feishu 单独覆盖即可。

详见模板 [`templates/test_l3_http.py`](templates/test_l3_http.py)。

### 打法 B：L3-Feishu — 构造飞书事件 payload 打 webhook（核心打法）

**适用**：验证 `FeishuAdapter` + 签名验证 + URL Challenge + Card 回包结构 + 异步处理顺序。

**为什么不直接用 WS**：WS 需要真实 App + 真群 + 网络稳定；webhook 接受同样的 payload，走同一段 `FeishuAdapter` 代码，覆盖率等价但 CI 可跑。

测试用的飞书事件 payload 工厂见 [`templates/feishu_event_factory.py`](templates/feishu_event_factory.py)，模板用例见 [`templates/test_l3_feishu_webhook.py`](templates/test_l3_feishu_webhook.py)。

**关键陷阱**：`event.message.content` 必须是 **JSON 字符串**（不是嵌套对象），漏 `json.dumps` 会让 adapter 抛错且测试看不到。

### 打法 C：L4 — 真实飞书 WS + 真实群（发版前必走）

**前置条件**：
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（staging 应用，非 prod）
- `FEISHU_E2E_CHAT_ID`（专用测试群，机器人是该群成员）
- 一个能发消息的「驱动账号」（用户 user_access_token，或第二个 Bot），用于在群里发消息触发被测 Bot

**两种 L4 子模式**：

**L4-a 自驱（推荐 CI nightly 跑）**：测试用一个独立的 lark-oapi client（驱动账号 token）→ 调 `im.v1.message.create` 在测试群发消息 → 等被测 Bot 通过 WS 接收并回包 → 用驱动账号身份再调 `im.v1.message.list` 拉取最新回复 → 断言。

**L4-b 人工**：CLI 提示「请在测试群 @ 机器人发送 'ping'，30s 内」，工具读 staging 服务日志/DB 验证收到 + 回包。适合不方便给驱动账号或第一次接入新群时。

详见模板 [`templates/test_l4_real_feishu.py`](templates/test_l4_real_feishu.py)。

### 打法 D：smoke — 最关键路径的 L2-3 子集

跑 < 5 分钟，每次提交前 / `make dev` 后必跑。断言：
1. HTTP 200 + 业务响应 schema
2. 关键持久化（Conversation / Workspace / 工单）已落
3. 内容非空

### ❌ Bad — 假绿测试（典型反例）

```python
# 反例 1：只断言 HTTP 200，业务可能完全没跑通
@pytest.mark.l3_feishu
async def test_feishu_bad(client):
    resp = await client.post("/feishu/webhook", json=event)
    assert resp.status_code == 200   # 飞书要求立即 ACK，永远是 200

# 反例 2：用 pytest.skip 规避基础设施问题
@pytest.mark.l3_feishu
async def test_feishu_skip_bad():
    if not os.getenv("FEISHU_APP_ID"):
        pytest.skip("没配凭据")     # CI 永远绿但什么也没测
    ...

# 反例 3：复用同一个 chat_id，会读到上一次测试残留状态
CHAT_ID = "oc_fixed_id"            # 多个测试共享 → 串扰
```

### ✅ Good — 多侧断言 + 隔离（推荐范式）

```python
@pytest.mark.l3_feishu
@pytest.mark.asyncio
async def test_fe2e_chat_us01_basic_reply(api, db_pool, feishu_mock_recorder):
    """US-01-1: 用户在群里 @ 机器人发消息 → 收到 Card 回包并落 DB。"""
    chat_id = _unique_chat_id()                     # 1) 隔离
    event = build_feishu_message_event(chat_id=chat_id, text="ping")

    resp = await api.post("/feishu/webhook", json=event)
    assert resp.status_code == 200                   # 2) HTTP 层

    sent = await wait_for_outbound_card(            # 3) 飞书回包层
        feishu_mock_recorder, chat_id, timeout=60
    )
    assert sent["msg_type"] == "interactive"
    payload = json.loads(sent["content"])
    assert payload.get("schema") == "2.0" or "elements" in payload

    async with db_pool.acquire() as conn:           # 4) 持久化层
        row = await conn.fetchrow(
            "SELECT * FROM conversations WHERE chat_id=$1", chat_id
        )
        assert row and row["agent_id"]
```

---

## 四、必备的 Make Targets

任何测试操作走 make。如果 target 不存在，**先在 Makefile 添加，再用**。

```makefile
test-unit:                    # L1 + 部分 L2-1
	cd <svc> && uv run --extra dev pytest tests/ -m "l1 or l2_1" -v

test-l2:                      # L2-2 + L2-3
	@bash scripts/dev.sh --no-feishu
	cd <svc> && uv run --extra dev pytest tests/ -m "l2_2 or l2_3" -v

test-l3:                      # L3 (HTTP 直打)
	@bash scripts/dev.sh --no-feishu
	cd <svc> && uv run --extra dev pytest tests/ -m "l3_1 or l3_2" -v --timeout=180

test-l3-feishu-regression:    # L3-Feishu 专用
	@bash scripts/dev.sh --no-feishu
	cd <svc> && uv run --extra dev pytest tests/e2e/test_feishu_e2e.py \
	    -m l3_feishu -v --timeout=180

test-l4:                      # L4 真飞书 (CI 不跑，发版前手动 / nightly)
	cd <svc> && uv run --extra dev pytest tests/ -m l4 -v --timeout=300

smoke:                        # 关键路径
	cd <svc> && uv run --extra dev pytest tests/e2e/test_smoke.py \
	    -m smoke -v --timeout=180

regression:                   # L1 + L2 + L3 + L3-Feishu + smoke
	cd <svc> && uv run --extra dev pytest tests/ \
	    -m "l1 or l2_1 or l2_2 or l2_3 or l3_1 or l3_2 or l3_feishu or smoke" \
	    -v --timeout=300
```

**注意**：如果 pytest 在 `optional-dependencies.dev` 里，`--extra dev` 必须加，否则找不到 pytest。

---

## 五、推荐工作流：「每个 User Story 必须有 L3-Feishu」

如果你的项目以 User Story 为开发单元，强烈建议落地这条强约束（ClawPlex 写在 `CLAUDE.md` 规约五）。流程：

### Step 1 — 写 US 文档
含验收标准（Gherkin Given/When/Then）。

### Step 2 — 同 MR 内创建 L3-Feishu 测试占位（不能延后）

```python
@pytest.mark.l3_feishu
@pytest.mark.asyncio
async def test_fe2e_<MODULE>_<US_ID>_<behavior>(api, db_pool):
    """US-XX-N: <一句话>

    Given: <前置>
    When:  <动作>
    Then:  <预期>
    """
    pytest.fail(
        "TODO(US-XX-N): 实现 L3-Feishu 测试。\n"
        "修复步骤: 1. 实现功能 2. 替换 fail 为真实断言 "
        "3. 跑 make test-l3-feishu-regression"
    )
```

> 用 `pytest.fail()` 而**不是** `pytest.skip()`。skip 让 CI 绿但测试压根没跑；fail 强制人看到。

### Step 3 — TDD：实现测试 + 实现功能直到测试通过

按 [`templates/test_l3_feishu_webhook.py`](templates/test_l3_feishu_webhook.py) 写真实断言。

### Step 4 — Merge 前 checklist

- [ ] `make test-l3` 通过
- [ ] `make test-l3-feishu-regression` 通过且包含新测试
- [ ] 新测试函数名匹配 `test_fe2e_<MODULE>_<US_ID>_<behavior>`
- [ ] 没有 `pytest.skip()`，没有 `pytest.fail("TODO")`
- [ ] US 文档 → 测试函数有双向引用（docstring 写 US ID，US 文档写测试文件路径）
- [ ] 如果改了 Feishu 入口/Adapter/Card 代码，发版前补 `make test-l4` 真飞书验证

---

## 六、断言要测的不止 HTTP 200（防假绿）

L3 / L3-Feishu 测试经常假绿。多侧断言模板：

```python
# 1. HTTP 层 — schema 正确
assert resp.status_code == 200
data = resp.json()
assert data["object"] == "chat.completion"

# 2. 业务层 — 内容非空且符合预期
content = data["choices"][0]["message"]["content"]
assert content and "expected_keyword" in content

# 3. 持久化层 — DB 落了
async with db_pool.acquire() as conn:
    row = await conn.fetchrow("SELECT ... FROM ... WHERE chat_id=$1", chat_id)
    assert row and row["agent_id"]

# 4. 飞书回包层（仅 L3-Feishu）— Card 结构正确
sent_card = await wait_for_outbound_card(mock_recorder, chat_id, timeout=60)
assert sent_card["msg_type"] == "interactive"
payload = json.loads(sent_card["content"])
assert payload.get("schema") == "2.0" or "elements" in payload

# 5. 副作用 — 工具调用 / 通知 / 审计日志
assert mock_audit_log.has_event(action="feishu_message_received", chat_id=chat_id)
```

详见 [`references/assertion-patterns.md`](references/assertion-patterns.md)。

---

## 七、Mock lark-oapi 出口（L3-Feishu 必备）

测试环境下必须 patch 掉 lark-oapi 客户端的 `message.create / reply / patch`，让它们写到本地 recorder 而不是真打飞书。两种方式：

**方式 1：pytest fixture + monkeypatch**
```python
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
```

**方式 2：DI / channel adapter 层替换**（推荐 — 解耦更彻底）
让 `FeishuChannel` 接受一个 `LarkClient` protocol，生产用真 lark-oapi，测试注入 `FakeLarkClient`。

模板见 [`templates/feishu_mock_recorder.py`](templates/feishu_mock_recorder.py)。

---

## 八、调试 checklist（测试失败时按这个走）

按规约二（不规避基础设施问题），失败时按顺序排查：

1. `make dev` 起来了吗？`docker compose ps` 所有服务 healthy？
2. `.env.local` 的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 配了吗？
3. 业务服务日志：`docker logs <gateway-container> | grep FeishuAdapter` 有报错吗？
4. webhook 异步处理是不是没等够？Adapter 抛错通常静默吞掉，要看日志才能发现
5. chat_id 是不是和上一个测试重叠了？换 `_unique_chat_id()`
6. 测试用 SSE 拿 conversation_id 时记得过滤 metadata 事件：`[c for c in chunks if "choices" in c]`
7. L4 失败但 L3-Feishu 通：99% 是飞书后台 App 配置 / 公网回调 / IP 白名单 / 凭据问题，不是代码

修不掉根因前**不要** merge —— 不允许 `pytest.skip` 规避基础设施。

---

## 九、参考与延伸

- 通用测试方法论：[`testing-strategy`](../testing-strategy/SKILL.md)
- 飞书 OpenAPI 探索（找官方文档）：[`lark-openapi-explorer`](../lark-openapi-explorer/SKILL.md)
- 飞书 IM SDK 用法：[`lark-im`](../lark-im/SKILL.md)
- L1 unit pattern + 防假绿：见父 skill `testing-strategy` 的 Step 7.6

---

## 十、项目案例：ClawPlex

ClawPlex 把本 skill 落地为强约束（`CLAUDE.md` 规约五）：每个 `docs/product/user-stories/US-*` 必须有至少一个 L3-Feishu 测试，否则 US 视为未完成。

- 入口实现：`services/gateway/src/channels/feishu.py`
- 反腐层：`services/gateway/src/channels/feishu_adapter.py`
- Card 构造：`services/gateway/src/channels/feishu_cards.py`
- L3-Feishu 测试：`services/gateway/tests/e2e/test_feishu_e2e.py`
- L3 HTTP 测试：`services/gateway/tests/e2e/test_f1_feishu_integration.py`
- smoke：`services/gateway/tests/e2e/test_smoke_feishu_roundtrip.py`
- conftest（共享 fixture）：`services/gateway/tests/e2e/conftest.py`

新项目接入飞书时可直接参考这一组文件作为骨架。
