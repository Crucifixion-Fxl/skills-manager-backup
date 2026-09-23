# Schema: Auto-Remediation Pattern

存储位置：`.sre-agent/knowledge/auto-remediation-patterns.yaml`

L5 自动修复模式库。每条 pattern 代表一类经过审批、可自动执行的修复操作。

## YAML 模板

```yaml
patterns:
  - pattern_id: AR-001
    name: "清理 Evicted/Failed 僵死 Pod"
    risk: low
    reversible: true
    match:
      finding_pattern: "Pod.*(Evicted|Failed|ContainerStatusUnknown)"
      causal_chain_type: "intermediate"  # 不是 root_cause，是附带发现
    action_template: |
      kubectl --context {context} -n {namespace} delete pod {pod_name}
    verify_template: |
      kubectl --context {context} -n {namespace} get pod {pod_name}
      期望: NotFound
    cooldown: 300  # 同一资源 5min 内不重复执行
    approved_at: "2026-03-25"
    approved_by: "feishu:user@example.com"
    evidence: "CG-443258 成功执行，验证通过"

  - pattern_id: AR-002
    name: "重启 CrashLoopBackOff Pod（非 Prod）"
    risk: low
    reversible: true
    match:
      finding_pattern: "CrashLoopBackOff"
      causal_chain_type: null            # 不限制节点类型
      environment_pattern: "staging-.*|dev-.*"  # 仅限非 Prod
    action_template: |
      kubectl --context {context} -n {namespace} delete pod {pod_name}
    verify_template: |
      kubectl --context {context} -n {namespace} get pod {pod_name} -o jsonpath='{.status.phase}'
      期望: Running
    cooldown: 600
    approved_at: "2026-03-25"
    approved_by: "feishu:user@example.com"
    evidence: ""
```

## 字段说明

### 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pattern_id` | string | 是 | 唯一标识，格式 `AR-{3位数字}`，如 `AR-001` |
| `name` | string | 是 | 人类可读的 pattern 名称，说明操作内容和适用范围 |
| `risk` | string | 是 | 风险等级：`low`/`medium`/`high`。**只有 `low` 的 pattern 可自动执行** |
| `reversible` | boolean | 是 | 操作是否可回滚。**只有 `true` 的 pattern 可自动执行** |

### match 子字段

用于匹配 incident_report 中 causal_chain 的节点，所有非 null 条件均需满足。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `finding_pattern` | string | 是 | 正则表达式，匹配 causal_chain 节点的 `event` 字段 |
| `causal_chain_type` | string/null | 否 | 匹配节点的 `type`（`root_cause`/`intermediate`/`symptom`/`amplifier`/`impact`）。`null` 表示不限制 |
| `environment_pattern` | string/null | 否 | 正则表达式，匹配 alert_summary 中的 `environment`。`null` 表示不限制环境 |

### action_template

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `action_template` | string | 是 | 操作命令模板，包含 `{context}`、`{namespace}`、`{pod_name}` 等占位符，由 Dispatcher 填充后传给 Execution Subagent |

占位符约定：

| 占位符 | 来源 |
|--------|------|
| `{context}` | alert_summary.cluster |
| `{namespace}` | 告警关联的 namespace |
| `{pod_name}` | finding 中匹配到的 Pod 名称 |
| `{service_name}` | alert_summary.entity |

### verify_template

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `verify_template` | string | 是 | 验证命令模板，执行后用于确认操作成功。必须包含期望输出说明。验证失败则自动回滚并升级为 L4 人工审批 |

### cooldown 与 approved metadata

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `cooldown` | integer | 是 | 冷却时间（秒），同一资源在此时间内不重复执行该 pattern |
| `approved_at` | string | 是 | 人工审批通过日期（ISO 8601 日期格式，如 `"2026-03-25"`） |
| `approved_by` | string | 是 | 审批人标识，格式 `feishu:{email}` |
| `evidence` | string | 是 | 历史执行证据，说明该 pattern 已被验证有效（如"CG-443258 成功执行，验证通过"）。新 pattern 可为空字符串，审批后补充 |

## 自动执行安全约束

Dispatcher 在路由 L5 时，必须同时满足以下全部条件才能自动派发 Execution Subagent：

| 约束 | 条件 |
|------|------|
| 风险等级 | `risk == "low"` |
| 可回滚性 | `reversible == true` |
| 环境匹配 | `environment_pattern` 匹配（或为 null） |
| Cooldown | 同一资源在 cooldown 秒内未执行过同一 pattern |
| 并发控制 | 同一集群/namespace 当前无其他自动修复在执行 |

任一条件不满足 → 走 L4 人工审批流程。

## Pattern 生命周期

```
L4 执行成功 + 验证通过
  → Dispatcher 触发 Pattern Extraction Subagent
  → 评估 risk=low + reversible + 可重复
  → 全部满足 → 构造候选 AR pattern
  → 飞书审批卡片（[通过] [拒绝]）
  → 审批通过 → 写入 auto-remediation-patterns.yaml
  → 审批拒绝 → 记录原因，丢弃
```

Pattern 一旦写入文件，即在下一轮 Dispatcher cron 中生效。
