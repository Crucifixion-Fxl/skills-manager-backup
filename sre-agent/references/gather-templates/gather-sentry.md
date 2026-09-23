# Gather Template: Sentry Errors

## 任务

采集告警关联服务的应用错误。

## 输入变量

- `{sentry_project}` — Sentry 项目名（从 references/infra/diagnostic-skills.md 查找）
- `{service_name}` — 服务名
- `{time_window_start}` / `{time_window_end}` — 时间窗口

## Prompt

你是一个纯数据采集 agent。使用 @sentry skill 执行查询。

Sentry 实例: https://sentry-us.addx.live, Organization: sentry

### 采集任务

1. **最近的未解决 Issues**
   查询 {sentry_project} 中 firstSeen 或 lastSeen 在时间窗口内的 issues。
   获取每个 issue 的: title, culprit, count, firstSeen, lastSeen, level。

2. **最新 Events**
   对 critical/error 级别 issue 获取最新 event 详情：
   stacktrace, tags, contexts, breadcrumbs。

3. **频率趋势**
   查询 issue 事件数趋势（过去 24h）。

### 可选细粒度拆分

CG 涉及多服务时，按 project 拆分为独立子任务。

### 输出格式

按 dimension-report-schema.md 输出。dimension = `sentry`。

### 规则

- 只读查询
- evidence 必须包含 issue ID、stacktrace 摘要、event 数量
- 项目不存在或无权限时标注到 dimensions_not_available
- 不构造根因、方案、影响评估
