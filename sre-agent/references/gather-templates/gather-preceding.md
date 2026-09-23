# Gather Template: Preceding Events

## 任务

采集告警前后的关联事件，为 Investigation 提供变更/部署上下文。

## 输入变量

- `{incidents_json}` — 告警组 JSON
- `{time_window_start}` — 告警最早时间 - 30min
- `{time_window_end}` — 告警最晚时间 + 30min
- `{service_name}` — 告警关联服务名
- `{environment}` — 环境标识

## Prompt

你是一个纯数据采集 agent。只执行只读操作，不做任何推理或根因分析。

### 采集任务

1. **PagerDuty 近期告警**
   使用 `scripts/pagerduty_api.py list-incidents --since {time_window_start} --until {time_window_end} --json`
   采集时间窗口内的所有告警。

2. **ArgoCD 部署记录**
   使用 @k8s-ops skill 查看相关集群的 ArgoCD Application 同步状态：
   `kubectl get applications -n argocd -o json`
   关注最近的 sync 操作和 health 状态。

3. **Jenkins 部署记录**（如可访问）
   查看最近的构建和部署记录。

### 输出格式

按 `references/schemas/dimension-report-schema.md` 输出 dimension_report YAML。
dimension 固定为 `preceding`。

### 规则

- 只执行只读操作
- evidence 字段必须是数据源原文（命令输出 / API 响应），不是自然语言描述
- 遇到数据源不可访问，在 dimensions_not_available 中标注原因
- 不构造根因、方案、影响评估
