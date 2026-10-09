# 检测：证据与错误签名

## 为什么不能靠进程退出码

额度耗尽时 buzz-acp 与 adapter **都不退出**：每次 prompt 失败只记 `agent_returned (application error — pipe intact)`，
按退避重试 10 次（`MAX_RETRIES=10`，无法配置），之后 `dead-lettering batch`，整条消息丢弃。2026-09-18 事故：07:36 首次 429，
08:30 丢弃，距账号恢复还有约 2 小时；从首次额度错误到人工切换完成约 15.5 小时。重启不能找回：回放水位默认是启动时刻，`--replay-floor` 最多前推 15 分钟。

## 已知签名（[../assets/signatures.json](../assets/signatures.json)）

| id | harness | kind | 文案特征 | 恢复时刻 | 证据 |
|:--|:--|:--|:--|:--|:--|
| `grok-402-balance` | grok | quota | `402 Payment Required` / `usage balance exhausted` | 无；取 billing `billingPeriodEnd` | 2026-09-19 实测（SuperGrok Plus，周额度 100%，恢复 09-25 01:03Z） |
| `glm-1308` | claude/glm | quota | `[1308]` 「已达到 5 小时使用上限，… 后可继续使用」 | 消息内，+08 | 2026-09-18 事故，1564 条 |
| `glm-1310` | claude/glm | quota | `[1310]` | 消息内，+08（观测到 2026-09-24 09:00） | 2026-09-18 15:04 起持续 |
| `glm-1302` | claude/glm | transient | `[1302]` 瞬时并发/速率限流 | — | `verified:false`，措辞未采样 |
| `claude-usage-limit` | claude | quota | `You've hit your (weekly \|5-hour \|…)limit · resets 12am (UTC)` | 消息内，`resets` 语法 | 2026-09-18 23:06Z，`.claude-buzz` 周限额 |
| `claude-auth` | claude | auth | `authentication failed … run claude /login` | — | buzz-acp 内置文案；需要人登录 |
| `codex-usage-limit` | codex | quota | `You’ve hit your usage limit. … try again at Oct 1st, 2026 12:13 AM.`（弯撇号 ’）；结构化 `codex_error_info: usage_limit_exceeded` | 消息内，`try again at <Mon> <D>th, <YYYY> <h>:<mm> AM\|PM`，**主机本地时区**（没有日期的写法不采信） | 2026-09-26 实测（`.codex-buzz`，`gpt-6-astra` 用完，恢复 10-01 00:13Z；同账号 `gpt-5.6-sol` 仍可用） |

三种 429 的 HTTP 状态与 `error.type` 完全相同，**只能靠 `code` 区分**（1302 短冷却 vs 1308/1310 长限额）。`transient` 不触发切换。

## 历史来源

- grok：`~/.grok/logs/unified.jsonl`（`billing: fetched credits config` 每 30s 一条，含 `creditUsagePercent`、`billingPeriodEnd`、`onDemandCap/Used`、`prepaidBalance`）。
- claude / glm：`<home>/projects/*/*.jsonl`，`type=system, subtype=api_error`（`error.status`、message 内 code 与恢复时刻）和 `isApiErrorMessage:true` 的 assistant 记录。
- codex：`<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-*.jsonl`，每行 `{"timestamp", "type", "payload"}`。`turn_context.payload.model` 是其后各 turn 的模型；
  一个 turn 以 `event_msg` / `payload.type=task_complete` 结束：带 `error`（`message` + `codex_error_info`）= 失败，无 `error` 且 `last_agent_message` 是非空字符串 = 成功（空串 / 缺失 / 非字符串都不算，免得掩盖更早的额度错误）。
  `token_count.rate_limits` 另有用量百分比（`limit_id: codex` 的 `primary.used_percent`；premium 用完时 `limit_id: premium`、`credits.has_credits:false`），**目前不参与判定**。
- 所有 agent 日志 `~/.config/buzz/agents/<name>.log`：`requeueing failed batch … channel_id= attempt= events=` / `dead-lettering batch`（**只有 channel、时间、数量，没有事件 ID**）。

## 判定要点

- 看**最新事件**，不是「窗口内出现过错误」：`.claude-buzz` 09-18 23:06 撞了周限额，00:00 重置后恢复，所以现在是可用。
- 只有 `quota` 计入；`transient`、`auth` 不算耗尽。
- grok：`402` 按结构化字段（`ctx.status_code` / 消息）判断，**不是**对整行做子串匹配；比最近一次 billing 快照更新的 402 优先（billing 只在 grok 进程在跑时才刷新）。billing 记录格式异常时判 `unknown`，不崩溃。
- codex 按**模型**隔离：不同模型额度不同（2026-09-26：钉住的 agent 在 `gpt-6-astra` 上用完，队列的 `codex-buzz`/`gpt-5.6-sol` 同账号仍在 94%），
  所以只统计前一个 `turn_context.model` 等于 profile 模型的 `task_complete`；`turn_context` 之前的 turn（只读末尾时被截断）不归属任何模型。
  读取开销：只看 mtime 在 48h 内的 rollout、每个只读末尾 4 MB，从最新文件往回读，遇到「最后写入早于已找到的最新事件」的文件就停
  （本机 193 个文件时常规约 0.1s，没有本模型记录的最坏情况约 1.3s）。
- 签名按 provider 隔离：`glm-*` 只用于 `provider: glm` 的 profile。`.claude-buzz` 曾用过 GLM key，那里遗留的 GLM 错误不能让 claude-buzz 显得耗尽。
- 只有「恢复时刻已知」或「30 分钟内」的额度错误才算耗尽；消息里的恢复时刻解析不了时（例如 `resets 3pm` 没有时区）只在 30 分钟内算数，之后降为 `unknown`。
- 无恢复时刻的错误只在 30 分钟内算数，之后降为 `unknown`。
- 未分类的可疑错误行（含 `limit|quota|balance|credit|exhaust|overloaded` 或独立的 402/429/529）会被记录到 `~/.config/buzz/harness-failover/unknown-errors.jsonl`（**已脱敏**），供 `learn --propose`。
  状态码必须独立成词：时间戳里的 `.741402Z` 不是 HTTP 402（真机上踩过）。
