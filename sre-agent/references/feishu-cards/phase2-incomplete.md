# Phase 2 不完整因果链卡片

## 时机
Investigation Subagent 完成调查，但因果链未能通过 4 条判据（不完整），或达到 3 轮调查深度限制时发送。

## 发送方式
使用 `scripts/feishu_notify.py send-elements` 或 @lark/reply-feishu skill。

## 与 phase2-complete.md 的区别

| 项目 | phase2-complete | phase2-incomplete |
|------|-----------------|-------------------|
| 因果链状态 | ✅ 完整 | ⚠️ 不完整 |
| 判据展示 | 无（通过） | 显示哪些判据 ❌ |
| depth_limit_reached | 无 | 可能显示（达到 3 轮限制） |
| 人工排查建议 | 无 | 显示具体建议 |
| 短期方案 | 可执行 | 基于现有数据（可信度较低） |

## 卡片内容

标题: `🔔 On-Call 告警诊断`
颜色: 按最高 urgency（high=red, low=yellow）

### Elements（顺序）

1. **告警概要 table**（同 complete）
2. **时间线 table**（同 complete）
3. **趋势数据 table**（如有，同 complete）
4. **不完整因果链 markdown**（含 ⚠️ 标记）
5. **判据不满足说明**（❌ 列出未通过的判据）
6. **depth_limit_reached 提示**（仅当达到 3 轮限制时）
7. **人工排查建议**
8. **影响范围 table**（同 complete）
9. **风险评估 table**（同 complete）
10. **基于现有数据的短期方案**（如有，可信度标注）
11. **长期方案**（如有）
12. **dimensions_not_available 列表**（如有）
13. **PagerDuty 按钮**

---

## 各 Element 详细格式

### 1-3. 与 phase2-complete.md 相同

### 4. 不完整因果链 markdown

```
**因果链** [{urgency}] ⚠️ 不完整

　↓ {known_node_1}（症状）
　↓ {known_node_2}（症状）
　🟡 {amplifier_node}（放大器）
　⚫ {impact_node}（{impact_detail}）
```

说明：不完整的链首节点类型为 `intermediate` 或 `symptom`，而非 `root_cause`。

### 5. 判据不满足说明

```
**判据不满足**

① 可行动 ❌ {reason_actionable_failed}
② 可解释 ❌ {reason_explainable_failed}（可选，仅当该判据失败时显示）
③ 最早点 ❌ {reason_earliest_failed}（可选）
④ 可预防 ❌ {reason_preventable_failed}（可选）
```

每条判据仅在未通过时显示 ❌ 行，通过则不渲染。

**判据含义**：
| 编号 | 名称 | 评估问题 |
|------|------|---------|
| ① | 可行动 | 存在具体修复动作？ |
| ② | 可解释 | 回答了 why 而不只是 what？ |
| ③ | 最早点 | 再追 why = 设计决策/外部因素（不可再深入）？ |
| ④ | 可预防 | 修复后能阻止重现？ |

### 6. depth_limit_reached 提示（条件渲染）

仅当 `depth_limit_reached=true` 时显示：

```
⚠️ 达到 3 轮调查深度限制。建议人工排查: {manual_investigation_hint}
```

### 7. 人工排查建议

```
**人工排查建议**

根据现有证据，以下方向尚未明确，建议人工介入：
- {investigation_direction_1}
- {investigation_direction_2}

可参考数据：
- {data_hint_1}（如：检查 {node_name} 的 resource limits 配置）
- {data_hint_2}（如：查看 {time_range} 内 Node 内存变化趋势）
```

### 8-9. 与 phase2-complete.md 相同

### 10. 基于现有数据的短期方案（条件渲染）

如果 Investigation 产出了 solutions（即使因果链不完整），以低可信度标注：

```
**短期方案（基于现有数据，⚠️ 可信度较低）**

{solution_idx}. **{solution_title}**
   - 操作: {action_description}
   - 目标节点: {target_causal_chain_node}
   - 预期效果: {expected_effect}
   - 风险: {risk_level}（{risk_detail}）
   - 层级: {L4 人工审批 / L5 自动修复}
   - ⚠️ 注意: 因果链不完整，此方案可能治标不治本
```

### 11-13. 与 phase2-complete.md 相同

---

## 完整渲染示例

```
🔔 On-Call 告警诊断

**告警概要**
| CG | 服务 | 环境 | 告警数 | 首次触发 | 持续时间 |
|----|------|------|-------|---------|---------|
| CG-443260 | thanos-query | cn-prod | 2 | 2026-03-25 14:20 | 持续中 |

**时间线**
| 时间 | 事件 | 来源 |
|------|------|------|
| 14:18 | thanos-query Pod 频繁重启（已重启 5 次） | K8s |
| 14:20 | thanos-cn.addx.live 不可达 | PagerDuty |
| 14:22 | AlertManager 无法连接 thanos-query | PagerDuty |

**因果链** [high] ⚠️ 不完整

　↓ thanos-query Pod 重启（原因未知）
　↓ thanos-cn.addx.live 不可达（25min）
　🟡 executionErrorState=alerting（放大器）
　⚫ 35+ 假告警风暴

**判据不满足**

① 可行动 ❌ 链首"thanos-query 重启"无具体修复动作（"重启"是现象不是方案）
② 可解释 ❌ 未回答 WHY 重启（OOM？配置变更？Node 故障？）
③ 最早点 ❌ "为什么重启？"是有意义且必须回答的问题
④ 可预防 ❌ 不知道重启原因无法预防

⚠️ 达到 3 轮调查深度限制。建议人工排查: thanos-query Pod 的 resource limits 和 Node 内存状态

**人工排查建议**

根据现有证据，以下方向尚未明确，建议人工介入：
- thanos-query 重启的直接原因（OOM / 配置变更 / Node 故障）
- 是否有 resource limits 未设置导致 BestEffort QoS

可参考数据：
- 检查 thanos-query Deployment 的 resource limits 配置（当前 gather-k8s 超时未返回）
- 查看 14:00-14:20 内 thanos-query Pod 内存使用趋势

**影响范围**
| 服务 | 影响类型 | 影响用户/流量 | 持续时间 |
|------|---------|-------------|---------|
| thanos-query | 不可用 | 监控查询失败 | 持续中 |

**风险评估**
| 维度 | 评估 | 说明 |
|------|------|------|
| 当前风险 | 高 | 故障持续中，根因未明确 |
| 重现概率 | 未知 | 根因未确定无法评估 |
| 修复紧迫性 | 高 | 需人工介入确定根因 |

**短期方案（基于现有数据，⚠️ 可信度较低）**

1. **重启 thanos-query Pod（临时缓解）**
   - 操作: kubectl rollout restart deployment/thanos-query -n monitoring
   - 目标节点: ↓ thanos-query Pod 重启
   - 预期效果: 临时恢复可用性，但不解决根因
   - 风险: 低（服务短暂中断 <30s）
   - 层级: L4 人工审批
   - ⚠️ 注意: 因果链不完整，此方案可能治标不治本

**⚠️ 以下数据维度采集失败（结论基于现有数据）**
- gather-k8s: 超时（300s 后仍未返回，可能是 K8s API Server 负载高）
- gather-prometheus: 权限不足（无法访问 cn-prod metrics）

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
| `{duration}` | string | 持续时间 |
| `{timeline}` | list | 时间线事件列表 |
| `{trend_data}` | list | 趋势数据（可为空） |
| `{causal_chain}` | object | 不完整因果链节点列表（链首为 intermediate/symptom） |
| `{urgency}` | string | 紧急程度（high/medium/low） |
| `{failed_criteria}` | list | 未通过的判据列表，每项含编号(①②③④)和原因 |
| `{depth_limit_reached}` | bool | 是否达到 3 轮调查深度限制 |
| `{manual_investigation_hint}` | string | 人工排查方向提示（depth_limit_reached=true 时必填） |
| `{investigation_directions}` | list | 人工排查建议列表 |
| `{impact_table}` | list | 影响范围列表 |
| `{risk_assessment}` | object | 风险评估 |
| `{short_term_solutions}` | list | 短期方案列表（可为空） |
| `{long_term_solutions}` | list | 长期方案列表（可为空） |
| `{dimensions_not_available}` | list | 采集失败的维度列表（可为空） |
| `{pagerduty_url}` | url | PagerDuty 链接 |
