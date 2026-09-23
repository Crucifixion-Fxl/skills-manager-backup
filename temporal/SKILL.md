---
name: temporal
description: 通过 Temporal CLI 管理定时任务（CronSchedule）。当用户需要创建、查询、暂停、恢复、删除定时任务，或提及定时巡检、定时报告、Cron、Schedule 时使用本 Skill。
---

# temporal

通过 `temporal` CLI（预装在 Sandbox 镜像中）管理 Temporal CronSchedule 定时任务。CLI 用法通过 Context7 MCP 查询官方文档（`resolve-library-id temporalio` → `get-library-docs`），此处只记录公司特有规则。

## Description

适用场景：创建定时巡检、定时报告、管理已有定时任务（查询/暂停/恢复/删除）。

| 变量 | 说明 | 必需 |
|------|------|------|
| `TEMPORAL_ADDRESS` | Temporal Server 地址（默认 `localhost:7233`） | 是 |
| `TEMPORAL_NAMESPACE` | Temporal Namespace（ClawPlex 独立 namespace） | 是 |

认证：Sandbox 内直连，无额外 auth。CLI 命令自动读取 `TEMPORAL_ADDRESS` 和 `TEMPORAL_NAMESPACE` 环境变量。

## Rules

### Schedule ID 命名规范

格式：`sched-{简短描述}-{序号}`，全小写，连字符分隔。

| 场景 | Schedule ID 示例 |
|------|-----------------|
| K8s Pod 巡检 | `sched-pod-check-01` |
| 每日系统报告 | `sched-daily-report-01` |
| Redis 内存监控 | `sched-redis-mem-01` |

### Memo 元数据结构

每个 Schedule 需要设置两层 memo：

1. **`--memo`**（Workflow 级别）：携带完整元数据，Workflow 执行时可读取
2. **`--schedule-memo`**（Schedule 级别）：携带 `chat_id` 和 `notify_rule`，用于 `schedule list` 筛选

> CLI memo 格式为 `KEY="VALUE"`（值必须是 JSON，字符串需要双引号）。

| 字段 | `--memo` | `--schedule-memo` | 说明 |
|------|----------|-------------------|------|
| `chat_id` | 是 | 是 | 群聊/私聊 ID，用于按群筛选和通知路由 |
| `agent_id` | 是 | 否 | Letta Agent ID |
| `task_prompt` | 是 | 否 | Agent 执行的任务描述（用户原文精炼版） |
| `notify_rule` | 是 | 是 | `on_error`（默认）或 `always` |
| `source` | 是 | 否 | 渠道来源（`feishu`） |

### 通知策略（notify_rule）

| notify_rule | 行为 | 典型场景 |
|-------------|------|---------|
| `on_error` | 正常时静默，有异常时才通知 | 系统巡检、服务健康检查 |
| `always` | 每次执行都推送结果 | 定时报告、数据汇总 |

**判断规则**：用户说"有问题再说/有异常再通知" → `on_error`；用户说"每天发给我/定时发一份" → `always`。

### HEARTBEAT_OK 抑制协议

Agent 在响应末尾添加 `[HEARTBEAT_OK]` 表示"一切正常，无需通知"：

- 含 `[HEARTBEAT_OK]` → 抑制通知（无论 notify_rule）
- `notify_rule=on_error` 且无输出 → 抑制
- `notify_rule=always` 且有输出 → 通知

创建 Schedule 时，在 task_prompt 末尾注入：`If everything is normal, end your response with [HEARTBEAT_OK].`

### 按群筛选任务

查询当前群的 Schedule：`temporal schedule list --output json` 输出包含 `schedule-memo` 中的字段（base64 编码），可用 jq + base64 解码过滤 `chat_id`。也可通过 `schedule describe` 逐个获取完整 memo。

### 数量限制

建议每个 `chat_id` 不超过 **50 个 Schedule**。创建前先 `list` + `jq` 计数，超过 50 个时提醒用户清理，但不强制拒绝。

### Conversation 隔离

每次定时执行创建独立 Conversation：`conv-cron-{schedule_id}-{timestamp}`，不干扰用户对话。

### Active Hours（活跃时段）

支持全局默认和单任务覆盖：

- 全局默认：环境变量 `PROACTIVE_ACTIVE_HOURS_START`/`END`/`TIMEZONE`（默认 `Asia/Shanghai`）
- 单任务覆盖：创建时 `--active-hours-start/end/tz` 参数
- 不在活跃时段内 → 跳过执行，返回 `{status: "skipped_inactive"}`

### 操作红线

- 删除 Schedule 前**必须用户确认**，不可静默删除
- 修改已有 Schedule 的 cron 表达式前**必须用户确认**
- 创建前先检查是否已有同名或相似任务，避免重复

### 常见工作流

1. **创建定时任务**：解析用户意图 → 生成 cron + notify_rule → `temporal schedule create` + memo → 回复确认
2. **查询任务列表**：`temporal schedule list --output json` → jq 过滤当前 chat_id → 格式化输出
3. **暂停/恢复**：`temporal schedule pause/unpause --schedule-id {id}`
4. **删除任务**：确认后 `temporal schedule delete --schedule-id {id}`
5. **手动触发**：`temporal schedule trigger --schedule-id {id}`

### 自然语言 → 参数映射参考

| 用户表达 | cron | notify_rule |
|---------|------|-------------|
| "每 5 分钟检查 X，有问题再说" | `*/5 * * * *` | `on_error` |
| "每天 9 点发报告" | `0 9 * * *` | `always` |
| "每周一 10 点汇总上周数据" | `0 10 * * 1` | `always` |
| "盯着 X，超阈值就告警" | `*/5 * * * *` | `on_error` |

### schedules_registry CoreMemory Block

Agent 维护一个 `schedules_registry` CoreMemory Block，创建/删除 Schedule 后自行更新，用于快速查阅当前管理的任务列表（无需每次 CLI 查询）。

## Examples

### Bad

```bash
# 没有 memo 元数据 — 无法按群筛选和控制通知
temporal schedule create --schedule-id sched-pod-01 \
  --cron '*/5 * * * *' \
  --type ScheduledTaskWorkflow

# 超过 50 个 Schedule 也不提醒用户
temporal schedule create ...

# 不确认直接删除
temporal schedule delete --schedule-id sched-daily-report-01
```

### Good

```bash
# 完整的创建命令（含 memo + schedule-memo）
temporal schedule create \
  --schedule-id sched-pod-check-01 \
  --cron '*/5 * * * *' \
  --type ScheduledTaskWorkflow \
  --task-queue agent-task-queue \
  --input '{}' \
  --memo 'chat_id="oc_xxx"' \
  --memo 'agent_id="agent-001"' \
  --memo 'task_prompt="检查 K8s Pod 状态。If everything is normal, end your response with [HEARTBEAT_OK]."' \
  --memo 'notify_rule="on_error"' \
  --memo 'source="feishu"' \
  --schedule-memo 'chat_id="oc_xxx"' \
  --schedule-memo 'notify_rule="on_error"'

# 列出所有 Schedule（schedule-memo 字段可见）
temporal schedule list --output json

# 查看单个 Schedule 完整 memo
temporal schedule describe --schedule-id sched-pod-check-01 --output json

# 删除前先确认
# → "确认删除 sched-daily-report-01？" → 用户确认后执行
temporal schedule delete --schedule-id sched-daily-report-01
```
