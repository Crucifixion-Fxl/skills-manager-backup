# Schema: dimension_report

由 Gather Subagent 产出，每个维度（k8s、prometheus、sentry 等）独立输出一份。

## YAML 模板

```yaml
type: "dimension_report"
dimension: "k8s"                  # 数据维度，见"维度标识"部分
incident_number: 443258            # 关联的 PD incident 编号（主告警）

dimension_causal_chain:
  - event: "thanos-query Pod OOMKilled (Exit Code 137)"
    type: "finding"
    evidence: "kubectl describe pod → Last State: Terminated, Reason: OOMKilled"
    source: "k8s"
    timestamp: "2026-03-24T10:41:38Z"
  - event: "Deployment 无 resource requests/limits, QoS=BestEffort"
    type: "finding"
    evidence: "pod spec resources: {}, QoS Class: BestEffort"
    source: "k8s"
    explains: "OOMKilled"

diagnosis_process:
  dimensions_investigated: [...]   # 已成功采集的子维度列表
  dimensions_not_available: [...]  # 无法访问的数据源及原因

timeline:
  - time: "2026-03-24T10:41:38Z"
    event: "thanos-query OOMKilled"
    source: "k8s"

trend_data: []                     # 数值型趋势序列（主要用于 prometheus 维度）
```

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"dimension_report"` |
| `dimension` | string | 是 | 数据维度标识，见下表 |
| `incident_number` | integer | 是 | 关联 PagerDuty 主告警编号 |
| `dimension_causal_chain` | list | 是 | 本维度发现的事件链，条目类型必须为 `"finding"` |
| `dimension_causal_chain[].event` | string | 是 | 事件描述 |
| `dimension_causal_chain[].type` | string | 是 | 固定为 `"finding"`（非 root_cause，非 symptom；由 Investigation 综合后确定） |
| `dimension_causal_chain[].evidence` | string | 是 | 原始数据证据，必须是命令输出或 API 响应原文，不能是自然语言概括 |
| `dimension_causal_chain[].source` | string | 是 | 数据来源（如 `k8s`、`prometheus`、`sentry`） |
| `dimension_causal_chain[].timestamp` | string | 否 | 事件发生时间（ISO 8601） |
| `dimension_causal_chain[].explains` | string | 否 | 该条目解释了哪个上游事件（填被解释的 event 摘要） |
| `diagnosis_process.dimensions_investigated` | list | 是 | 本轮成功采集的维度/子维度列表 |
| `diagnosis_process.dimensions_not_available` | list | 是 | 无法访问的数据源，格式：`{source, reason}` |
| `timeline` | list | 是 | 有明确时间戳的事件列表，按时间升序排列 |
| `timeline[].time` | string | 是 | ISO 8601 时间戳 |
| `timeline[].event` | string | 是 | 事件摘要 |
| `timeline[].source` | string | 是 | 数据来源 |
| `trend_data` | list | 是 | 数值型趋势序列，无趋势数据时为空列表 `[]` |

## 维度标识

| dimension | 含义 |
|-----------|------|
| `k8s` | Kubernetes 资源状态（Pod、Node、Ingress、Events） |
| `prometheus` | Prometheus/Thanos 指标趋势 |
| `sentry` | 应用错误和异常 |
| `cloud` | 云厂商资源状态（CLB、RDS、VPC 等） |
| `preceding` | 告警前后的变更/部署事件 |

## 规则

1. **evidence 必须是原始数据**：`evidence` 字段必须包含命令原始输出或 API 响应原文，禁止使用"内存使用偏高"等自然语言概括。
2. **findings 使用 `"finding"` 类型**：`dimension_causal_chain` 中所有条目的 `type` 必须为 `"finding"`。根因/症状类型由 Investigation Subagent 综合所有维度后确定，Gather Subagent 不做该判断。
3. **不可访问的数据源填入 `dimensions_not_available`**：如某子维度因权限、网络、服务不可用等原因无法采集，必须在 `dimensions_not_available` 中注明来源和原因，不得省略。
4. **只读操作**：Gather Subagent 只执行只读操作，不做任何变更。
5. **不构造根因、方案、影响**：Gather Subagent 只负责采集和结构化数据，不做推理和决策。
