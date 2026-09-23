# LLM 模型 Key 余额与用量探针

对已验证有效（verified）的 LLM 平台凭证，进一步判断财务风险等级。

## 适用场景

- "这个 OpenAI key 还有余额吗"
- "批量扫描结果里的 LLM key 哪些有实际消费"
- "离职员工的 DeepSeek key 是否还在产生费用"

## 前置条件

仅对同时满足以下条件的凭证执行：

1. 连通性验证结果为 **verified**（参见 [credential-verify.md](credential-verify.md)）
2. 凭证类型属于 LLM 平台（OpenAI、DeepSeek、Gemini、Anthropic 等）

不满足条件时跳过，按原 verified 状态处理。

## 各平台探针定义

### OpenAI（支持）

用量查询 API（需指定用量类型，使用 Unix 时间戳）：

```bash
export CHECK_TOKEN="<token>"
# 查询近 30 天 completions 用量（start_time 为 Unix 秒级时间戳）
START_TS=$(date -d '-30 days' +%s 2>/dev/null || date -v-30d +%s)
curl -s -H "Authorization: Bearer $CHECK_TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.openai.com/v1/organization/usage/completions?start_time=$START_TS"
unset CHECK_TOKEN START_TS
```

> 端点格式：`/v1/organization/usage/{type}`，`type` 可选值：
> `completions`、`embeddings`、`images`、`audio`、`moderations` 等。
> 参数 `start_time`（必填）和 `end_time`（可选）均为 Unix 秒级时间戳。

- 成功返回用量数据 → 有余额有用量（P0）或有余额无用量（P1）
- `429`：触发限流，说明 key 活跃，按 P0 处理
- `401`：已吊销（P3）
- `403`：权限不足，无法判断余额，按原 verified 状态处理

### DeepSeek（支持）

余额查询 API：

```bash
export CHECK_TOKEN="<token>"
curl -s -H "Authorization: Bearer $CHECK_TOKEN" \
  https://api.deepseek.com/user/balance
unset CHECK_TOKEN
```

- 返回 `balance_infos` 数组，`total_balance > 0` → 有余额
- 结合 `granted_balance` 与 `topped_up_balance` 判断来源
- `total_balance == 0` → 无余额（P2）
- `401`：已吊销（P3）

### Google Gemini AI Studio（有限支持）

无直接余额/用量查询 API。通过最小请求探测配额状态：

```bash
export CHECK_TOKEN="<token>"
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://generativelanguage.googleapis.com/v1beta/models?key=$CHECK_TOKEN"
unset CHECK_TOKEN
```

- `200`：key 有效且有配额，无法确定具体余额 → 按 verified 状态处理
- `429`：配额耗尽或限流
- `400`/`403`：key 无效或受限

> Gemini AI Studio 为免费层 + 按量付费模式，无公开余额 API，
> 探针结果仅能确认 key 是否可用，不能精确判定余额。

### Anthropic（预留）

当前无公开的余额或用量查询 API。

连通性验证通过后，按原 verified 状态处理。待 Anthropic 开放相关 API 后补充探针。

### LangSmith（不适用）

LangSmith 为 tracing/observability 服务，非计费型 LLM 调用平台。
API key 用于推送 trace 数据，不涉及模型调用余额。

跳过余额探针，按原 verified 状态处理。

### Azure OpenAI（不适用）

Azure OpenAI 的计费绑定到 Azure 订阅，API key 无法直接查询余额。
需 Azure 订阅级权限（Cost Management API），超出单 key 探针范围。

跳过余额探针，按原 verified 状态处理。

## 结果分级

| 状态码 | 含义 | 处置优先级 | 处置建议 |
|---|---|---|---|
| P0 | 有余额有用量 | 最高 | 立即吊销并排查用量来源 |
| P1 | 有余额无用量 | 高 | 吊销并确认余额归属 |
| P2 | 无余额 | 低 | 吊销，无财务风险 |
| P3 | 已吊销 | 仅清理 | 从代码/配置中移除残留 |
| N/A | 平台不支持查询 | 按 verified 处理 | 人工判断或联系平台确认 |

## 安全约束

- **只读操作**：所有探针仅使用 GET 请求，不触发任何写操作或模型调用
- **凭证不落盘**：使用环境变量传递，探测后立即 `unset`，不写入 shell history
- **报告脱敏**：仅展示余额金额、用量摘要和状态码，不展示完整 key
- **最小探测**：优先使用专用余额/用量 API，不通过实际模型调用来试探
- 与 [credential-verify.md](credential-verify.md) 的凭证安全约束保持一致

## 身份溯源（可选后续步骤）

余额探针确认了财务风险等级，但无法回答"这个 key 是谁的"。

如需确认 key 归属（特别是来源不明或疑似离职员工的 key），
参见 [identity-probe.md](identity-probe.md)。
