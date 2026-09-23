# Phase 2 完整因果链卡片

## 时机
Investigation Subagent 完成调查，因果链评估通过 4 条判据（完整）时发送（~10-15min）。

## 发送方式
使用 `scripts/feishu_notify.py send-elements` 或 @lark/reply-feishu skill。

## 卡片内容

标题: `🔔 On-Call 告警诊断`
颜色: 按最高 urgency（high=red, low=yellow）

### Elements（顺序）

1. **告警概要 table**
2. **时间线 table**
3. **趋势数据 table**（如有 trend_data）
4. **因果链 markdown**（可视化渲染，见下方格式）
5. **影响范围 table**
6. **风险评估 table**
7. **短期方案**
8. **长期方案**
9. **dimensions_not_available 列表**（如有）
10. **PagerDuty 按钮**

---

## 各 Element 详细格式

### 1. 告警概要 table

```
**告警概要**
| CG | 服务 | 环境 | 告警数 | 首次触发 | 持续时间 |
|----|------|------|-------|---------|---------|
| CG-{cg_id} | {service} | {environment} | {alert_count} | {first_triggered_at} | {duration} |
```

### 2. 时间线 table

```
**时间线**
| 时间 | 事件 | 来源 |
|------|------|------|
| {time} | {event} | {source} |
```

来源取值：`PagerDuty` / `ArgoCD` / `Prometheus` / `K8s` / `Sentry` / `AWS` / `GCP` / `腾讯云`

### 3. 趋势数据 table（可选，仅当 trend_data 非空时渲染）

```
**趋势数据**
| 指标 | 时间窗口 | 基线 | 当前 | 变化 |
|------|---------|------|------|------|
| {metric_name} | {window} | {baseline} | {current} | {change_pct} |
```

### 4. 因果链 markdown（完整）

```
**因果链** [{urgency}] ✅ 完整

🔴 {root_cause_node}（{root_cause_detail}）
🔴 {contributing_factor}（{introduced_at}）
　↓ {intermediate_step_1}
　↓ {intermediate_step_2}
　🟡 {amplifier_node}（放大器）
　⚫ {impact_node}（{impact_detail}）
```

**节点颜色说明**：
| 符号 | 类型 | 含义 |
|------|------|------|
| 🔴 | root_cause / contributing_factor | 根因或重要贡献因素 |
| 🟠 | intermediate | 中间传播链节点 |
| 🟡 | amplifier | 放大器（加剧了影响但非根因） |
| ⚫ | impact | 最终影响/症状 |
| ↓ | 箭头 | 因果传播方向 |

### 5. 影响范围 table

```
**影响范围**
| 服务 | 影响类型 | 影响用户/流量 | 持续时间 |
|------|---------|-------------|---------|
| {service} | {impact_type} | {affected_scope} | {duration} |
```

### 6. 风险评估 table

```
**风险评估**
| 维度 | 评估 | 说明 |
|------|------|------|
| 当前风险 | {高/中/低} | {reason} |
| 重现概率 | {高/中/低} | {reason} |
| 修复紧迫性 | {高/中/低} | {reason} |
```

### 7. 短期方案

```
**短期方案（立即执行）**

{solution_idx}. **{solution_title}**
   - 操作: {action_description}
   - 目标节点: {target_causal_chain_node}
   - 预期效果: {expected_effect}
   - 风险: {risk_level}（{risk_detail}）
   - 层级: {L4 人工审批 / L5 自动修复}
```

### 8. 长期方案

```
**长期方案（后续跟进）**

{solution_idx}. **{solution_title}**
   - 操作: {action_description}
   - 目标节点: {target_causal_chain_node}
   - 预期效果: {expected_effect}
```

### 9. dimensions_not_available（可选，仅当列表非空时渲染）

```
**⚠️ 以下数据维度采集失败（结论基于现有数据）**
- {dimension}: {failure_reason}（{超时 / 不可访问 / 权限不足}）
```

### 10. PagerDuty 按钮

```
[查看 PagerDuty]({pagerduty_url})
```

---

## 完整渲染示例

```
🔔 On-Call 告警诊断

**告警概要**
| CG | 服务 | 环境 | 告警数 | 首次触发 | 持续时间 |
|----|------|------|-------|---------|---------|
| CG-443258 | thanos-query | cn-prod | 3 | 2026-03-25 10:42 | 25min |

**时间线**
| 时间 | 事件 | 来源 |
|------|------|------|
| 10:20 | 部署 thanos-query v0.31.1（无 resource limits） | ArgoCD |
| 10:42 | thanos-store 内存使用超 90% | Prometheus |
| 10:43 | thanos-store OOMKilled，Pod 重启 | K8s |
| 10:44 | 重试积压，thanos-query OOMKilled | K8s |
| 10:45 | thanos-cn.addx.live 不可达 | PagerDuty |
| 10:55 | CLB 同步恢复 | 腾讯云 |

**趋势数据**
| 指标 | 时间窗口 | 基线 | 当前 | 变化 |
|------|---------|------|------|------|
| thanos-query 内存使用 | 24h | 512Mi | 1.8Gi | +251% |
| thanos-query 重启次数 | 1h | 0 | 7 | +700% |

**因果链** [high] ✅ 完整

🔴 无 resource limits (BestEffort QoS)（2026-03-25 10:20 部署引入）
🔴 Ingress host 配置不一致（2024-07-22 引入，CLB 同步未生效）
　↓ thanos-store 重启 → 重试积压 → OOMKilled
　↓ Pod 重启 + CertError 阻塞 CLB 同步（25min）
　↓ thanos-cn.addx.live 不可达
　🟡 executionErrorState=alerting（放大器，Grafana 放大告警数量）
　⚫ 35+ 假告警风暴（大量 Grafana 告警无实际故障）

**影响范围**
| 服务 | 影响类型 | 影响用户/流量 | 持续时间 |
|------|---------|-------------|---------|
| thanos-query | 不可用 | 监控查询失败 | 25min |
| Grafana | 告警风暴 | 所有仪表盘告警 | 25min |

**风险评估**
| 维度 | 评估 | 说明 |
|------|------|------|
| 当前风险 | 低 | 已自动恢复 |
| 重现概率 | 高 | resource limits 仍未设置 |
| 修复紧迫性 | 高 | 下次重启仍会 OOM |

**短期方案（立即执行）**

1. **给 thanos-query 添加 resource limits**
   - 操作: 设置 requests/limits（CPU: 500m/2000m，内存: 512Mi/2Gi）
   - 目标节点: 🔴 无 resource limits
   - 预期效果: Pod QoS Class 升为 Burstable，OOM 风险降低
   - 风险: 低（Pod 滚动重启，服务短暂抖动 <30s）
   - 层级: L4 人工审批

2. **修复 Ingress host 配置**
   - 操作: 更新 Ingress host 与 CLB 配置一致
   - 目标节点: 🔴 Ingress host 配置不一致
   - 预期效果: CLB 同步时间从 25min 降低到 <1min
   - 风险: 低（仅配置变更，无服务中断）
   - 层级: L4 人工审批

**长期方案（后续跟进）**

1. **为所有 thanos 组件设置 VPA**
   - 操作: 部署 VPA，自动推荐 resource limits
   - 目标节点: 🔴 无 resource limits
   - 预期效果: 自动跟随负载调整资源配置

2. **修复 executionErrorState 放大器**
   - 操作: 将 Grafana 全局 executionErrorState 改为 keep_state
   - 目标节点: 🟡 executionErrorState=alerting
   - 预期效果: 消除未来告警风暴放大效应

[查看 PagerDuty]
```

---

## 输入变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `{cg_id}` | string | Correlation Group ID |
| `{service}` | string | 主要受影响服务 |
| `{environment}` | string | 环境 |
| `{alert_count}` | int | 告警总数 |
| `{first_triggered_at}` | ISO8601 | 首次触发时间 |
| `{duration}` | string | 持续时间（如 25min） |
| `{timeline}` | list | 时间线事件列表 |
| `{trend_data}` | list | 趋势数据（可为空） |
| `{causal_chain}` | object | 因果链节点列表（含 type: root_cause/contributing_factor/intermediate/amplifier/impact） |
| `{urgency}` | string | 紧急程度（high/medium/low） |
| `{impact_table}` | list | 影响范围列表 |
| `{risk_assessment}` | object | 风险评估（当前风险/重现概率/修复紧迫性） |
| `{short_term_solutions}` | list | 短期方案列表（含 prompt、目标节点、风险、层级） |
| `{long_term_solutions}` | list | 长期方案列表 |
| `{dimensions_not_available}` | list | 采集失败的维度列表（可为空） |
| `{pagerduty_url}` | url | PagerDuty 链接 |
