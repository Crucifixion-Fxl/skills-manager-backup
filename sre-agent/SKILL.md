---
name: sre-agent
description: >-
  告警分析处理自动化。Dispatcher 轮询 PagerDuty，关联告警，派发 Investigation/Execution subagent，
  两阶段飞书通知，L4 审批执行，L5 自动修复。
  当用户说"启动告警自动化"、"start alert automation"、"sre-agent"时使用。
argument-hint: "[start | status | stop | debug]"
---

# sre-agent (Alert Automation)

## Description

告警分析处理自动化系统。覆盖 L1（分析）到 L5（自动修复）。

## 启动

根据 `$ARGUMENTS` 路由：

| 输入 | 动作 |
|------|------|
| `start` / 默认 | 初始化状态 + 启动 cron 轮询 |
| `status` | 读取 .sre-agent 展示当前状态 |
| `stop` | 取消 cron |
| `debug` | Debug 模式：设置 env + reset 状态 + 启动 cron（与生产相同执行模型） |

## 启动流程

1. 读取 `references/dispatcher-loop.md`，严格遵循
2. 初始化状态目录（调用 `scripts/state_manager.py`）
3. CronCreate: `*/1 * * * *`，每分钟执行 dispatcher loop

## Debug 模式

Debug 模式与生产共享完全相同的执行模型（cron 驱动、subagent 后台派发、状态管理、超时检测、review 流程、execution 流程）。区别仅通过 `SRE_AGENT_DEBUG=true` 环境变量控制：

- PD poll：`limit=1, statuses=["triggered","acknowledged"]`
- 飞书通知：发送到 debug webhook（`ONCALL_FEISHU_WEBHOOK_URL_DEBUG`）

### 前置条件

- 环境变量 `PAGERDUTY_API_TOKEN` 已设置
- 环境变量 `ONCALL_FEISHU_WEBHOOK_URL_DEBUG` 已设置
- 环境变量 `ONCALL_FEISHU_WEBHOOK_SECRET_DEBUG` 已设置

如果缺少任一环境变量，报错并终止。

### 启动步骤

1. `export SRE_AGENT_DEBUG=true`
2. `python3 scripts/state_manager.py --state-dir .sre-agent-debug init`
3. 启动 cron（与生产相同）：CronCreate `*/1 * * * *`，仅 `--state-dir` 指向 `.sre-agent-debug`

### 与生产的区别

- 状态目录：`.sre-agent-debug/`（不影响生产状态 `.sre-agent/`）
- PD poll：仅获取 1 条 triggered/acknowledged 告警
- 飞书通知：发送到 debug webhook
- 其余逻辑与生产完全一致

### 停止

与生产相同：取消 cron

## Rules

### 两层架构

- `scripts/dispatcher_loop.py`（Python）处理所有确定性逻辑：poll PD、告警关联、状态管理、飞书通知、产出物检测
- LLM cron 只执行 action plan 中的 subagent 派发（需要 Agent tool 的操作）
- Subagent 只写产出文件（report.yaml / result.json / pattern_candidate.yaml），不更新状态，不发通知

### Dispatcher 职责边界

**dispatcher_loop.py 做**：
- Poll PagerDuty
- 告警关联（故障实体匹配 + 复发检测）
- 状态管理（create_cg、complete_cg、更新 approval status）
- 飞书通知（Phase 1、Phase 2、审批卡片、执行结果）
- 产出物检测（report.yaml、result.json、pattern_candidate.yaml）
- 审批状态轮询（飞书审批 API）
- 输出 action plan（JSON）

**LLM cron 做**：
- 运行 dispatcher_loop.py
- 读 action plan，对每个 action 用 Agent tool 派发 subagent。**严禁先用 Read 读 `prompt_file` 再把全文作为 prompt 参数透传** —— 这会让 dispatcher LLM 把 20~40KB 的 prompt 作为输出 token 再吐一遍（Opus 输出速度 ~50 tok/s，单次派发 2~4 分钟，一轮多个派发就会塞爆 cron interval）。正确做法是用 wrapper prompt 让 subagent 自己 Read：

  ```
  Agent(
    description: "Investigate CG-{id}" / "Review CG-{id}" / ...,
    prompt: "严格按 {action['prompt_file']} 中的指令执行。第一步用 Read tool 读取该文件完整内容（文件可能 400+ 行，必要时分段读完），然后立即开始执行，不得总结、改写、跳步。",
    run_in_background: true,
  )
  ```

  wrapper 只有 ~100 字符，完整内容由 subagent 在其隔离 context 中自行加载 —— 语义与原做法等价，但不再经过 dispatcher LLM 的输出通道。**禁止缩写/摘要/改写** 的约束仍然成立，只是落点从"dispatcher LLM 必须原样吐出"变成"subagent 必须原样执行 prompt_file"。
- 同一轮 action plan 里的多个 dispatch action **必须在同一条消息中并行发出**（一条 assistant 消息里多个 Agent 工具调用），不得串行。串行派发会让多个 CG 的 subagent 启动时间叠加，严重拖慢一轮 cron。

### 无持久 Agent

所有 subagent 通过 Agent tool 派发，全新 context，一次性。不使用 SendMessage。

### 状态外置

所有状态写入 `.sre-agent/`，Dispatcher context 恒定小。

### 密钥

所有密钥通过环境变量获取，禁止在命令行中 export/echo 密钥值，脚本应从 `os.environ` 读取。

- PagerDuty Token: 环境变量 `PAGERDUTY_API_TOKEN`
- 飞书 Webhook: 环境变量 `ONCALL_FEISHU_WEBHOOK_URL`、`ONCALL_FEISHU_WEBHOOK_SECRET`
- 飞书审批: 环境变量 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_APPROVAL_CODE`、`FEISHU_APPROVER_OPEN_ID`（用应用身份 `tenant_access_token`，脚本自动换取并缓存到 `~/.sre-agent/feishu_tenant_token.json`）
- Debug 飞书 Webhook: 环境变量 `ONCALL_FEISHU_WEBHOOK_URL_DEBUG`、`ONCALL_FEISHU_WEBHOOK_SECRET_DEBUG`（仅 debug 模式需要）
- Debug 模式开关: 环境变量 `SRE_AGENT_DEBUG`（由 debug 流程自动设置，无需手动配置）

### 环境与端点

所有基础设施上下文见 `references/infra/` 目录（prometheus.md / cloud-accounts.md / k8s-contexts.md / diagnostic-skills.md），禁止猜测。

## Examples

### Bad

```
/sre-agent
```

不指定动作时默认 `start`，但生产环境启动前应先确认 PagerDuty token 和飞书 webhook 环境变量已就绪，否则 cron 会反复失败。

### Good

```
/sre-agent debug
```

先用 debug 模式验证链路：独立状态目录 `.sre-agent-debug/`，PD 只拉 1 条告警，通知发到 debug webhook，跑通 Investigation → Review → Phase 2 通知后再切 `/sre-agent start` 接生产流。
