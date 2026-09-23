---
name: dagster
description: Dagster 数据编排平台操作。查询 Pipeline/Job 运行状态、Asset 物化情况、Schedule/Sensor 调度状态、启动 Job 执行时使用。
---

# dagster

通过 Dagster GraphQL API 管理数据编排平台。API 用法通过 Context7 MCP 查询。

## Description

适用场景：数据 Pipeline 运行状态查询、Job 失败排查、Asset 物化监控、Schedule/Sensor 管理、手动触发 Job 执行。

Dagster 使用 **GraphQL API（非 REST）**，所有操作通过 `/graphql` 端点发送 POST 请求。

| 变量 | 说明 | 必需 |
|------|------|------|
| `DAGSTER_URL` | 内部地址：`https://dagster.addx.live` | 是 |
| `DAGSTER_TOKEN` | API Token（Settings → Tokens） | 是 |

认证方式（二选一，首次使用请两种都试）：

- **Dagster Cloud**：`-H "Dagster-Cloud-Api-Token: $DAGSTER_TOKEN"`
- **OSS 实例**：`-H "Authorization: Bearer $DAGSTER_TOKEN"`

## Rules

### 操作红线

- **启动 Job 前必须告知用户并等待确认**，禁止静默触发生产 Pipeline
- **terminateRun 是不可逆操作**，终止前需确认 runId 和影响
- 时间戳为 Unix epoch 秒，展示时转换为可读格式

### 常见工作流

- **Pipeline 失败排查：** 查失败 runs → 获取 runId → 到 `$DAGSTER_URL/runs/<runId>` 查看详细日志
- **Asset 物化监控：** 列出所有 Asset → 检查最近物化时间 → 对比预期更新频率，发现过期 Asset
- **调度健康检查：** 查 repositories 获取 location/repo → 查 schedules/sensors 状态 → 检查最近 runs 是否正常
- **DataHub 联动：** Asset 元数据应同步到 DataHub，详见 AGENTS.md 数据工具章节

## Examples

### Bad

```
直接启动生产 Job，未告知用户确认
→ 问题：生产 Pipeline 未经确认，可能触发大规模数据处理
```

### Good

```
1. 先查询目标 Job 最近运行状态（通过 GraphQL 查 runs）
2. 告知用户："daily_sync 最近 3 次运行均成功，将启动新的运行，请确认"
3. 用户确认后才执行 launchRun mutation
4. 启动后立即查询验证运行状态
```

> GraphQL 查询语法和 Schema 请通过 Context7 MCP 查询 Dagster 官方文档。
