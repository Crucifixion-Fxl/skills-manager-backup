# Schema: incident_report

由 Investigation Subagent 产出，综合所有维度的 dimension_report，构造完整因果链和解决方案。

## YAML 模板

```yaml
type: "incident_report"

alert_summary:
  incidents: [...]                          # 本 CG 包含的所有 PD incident 编号列表
  entity: "thanos-cn"                       # 受影响的主实体名
  service: "grafana-ai"                     # PagerDuty service 名
  cloud_account: "tencent-100014919455"     # 云账户 ID
  cluster: "tencent-100014919455-cn-main"   # K8s cluster context
  environment: "cn-prod + cn-staging"       # 环境（可多个，用 + 连接）
  region: "ap-beijing"                      # 部署区域
  severity: "P1"                            # 严重程度（P1/P2/P3/P4）
  triggered_at: "2026-03-24T10:42:50Z"      # 第一条告警触发时间
  diagnosed_at: "2026-03-24T10:55:00Z"      # 因果链完成评估时间
  pagerduty_url: "https://..."              # 主告警 PD URL

diagnosis_process:
  investigation_rounds: 2                   # 总调查轮次
  dimensions_investigated: [...]            # 所有已调查的维度列表
  dimensions_not_available: [...]           # 所有无法访问的数据源（汇总自各 dimension_report）
  cross_validation: "..."                   # 跨维度交叉验证说明

trend_data: [...]                           # 汇总自各 dimension_report 的趋势数据

timeline: [...]                             # 汇总并去重排序后的完整时间线

causal_chain:
  confidence: "high"                        # 置信度（high/medium/low）
  reasoning: "..."                          # 因果链推理过程说明
  investigation_rounds: 2                   # 本链耗费的调查轮次
  depth_limit_reached: false                # 是否达到 3 轮调查深度限制

  # ━━━ 因果链完整性评估 ━━━
  evaluation:
    complete: true                          # true=完整，false=不完整
    criteria:
      - id: "①"
        name: "可行动"
        passed: true
        detail: "加 resource limits + 统一 Ingress host"
      - id: "②"
        name: "可解释"
        passed: true
        detail: "完整解释了 WHY（OOM + CertError 阻塞恢复）"
      - id: "③"
        name: "最早点"
        passed: true
        detail: "再追 why = 创建时遗漏（设计决策 → long_term）"
      - id: "④"
        name: "可预防"
        passed: true
        detail: "加 limits 防 OOM，修 host 防 CertError"
    # 不完整时填写（complete=false 时必填）:
    # incomplete_reason: "达到 3 轮调查深度限制，链首仍为 symptom"
    # next_investigation_hint: "需调查 thanos-query Pod 自身的资源配置和 Node 状态"

  chain:
    - event: "thanos-query Deployment 未设置 resource requests/limits"
      node_type: "root_cause"
      actionable: true
      source: "k8s"
      introduced: "创建时"
    - event: "Ingress http-rules host 不一致"
      node_type: "root_cause"
      actionable: true
      source: "k8s"
      introduced: "2024-07-22"
    - event: "OOMKilled → CertError 阻塞 CLB"
      node_type: "intermediate"
      caused_by: [0, 1]            # chain 数组中的索引（从 0 开始）
      source: "k8s"
    - event: "thanos-cn 不可达 (25min)"
      node_type: "symptom"
      duration: "25min"
      source: "curl"
    - event: "executionErrorState=alerting"
      node_type: "amplifier"
      actionable: true
      source: "grafana"
    - event: "35+ 假告警"
      node_type: "impact"
      source: "pagerduty"

impact:
  affected_services: [...]          # 受影响服务列表
  affected_users: "..."             # 受影响用户群体描述
  blast_radius: "..."               # 影响范围（如"cn-prod 所有依赖 thanos 的 Grafana 面板"）
  data_loss_risk: false             # 是否存在数据丢失风险

risk_assessment:
  current_risk: "high"             # 当前风险等级（high/medium/low）
  trend: "stable"                  # 趋势（stable/worsening/improving）
  eta_to_critical: null            # 预计到达临界状态的时间（null=不适用或已临界）
  single_point_of_failure: true    # 是否为单点故障
  auto_recovery_possible: false    # 是否可能自愈

solutions:
  short_term:
    - title: "给 thanos-query 添加 resource limits"
      action: "kubectl patch 添加 CPU/内存 requests/limits"
      addresses: "causal_chain[0]"
      expected_effect: "Pod 进入 Burstable QoS,避免 OOM 触发告警"
      risk: "low"
      reversible: true
      blast_radius: "单 Deployment 1 Pod 滚动重启(<30s 抖动)"
      verify_cmd: "kubectl get deployment thanos-query -n prometheus --context tencent-100014919455-cn-main -o json | jq '.spec.template.spec.containers[0].resources'"
      prompt: |
        使用 k8s-ops skill 对 tencent-100014919455-cn-main 集群
        prometheus namespace 的 thanos-query Deployment 执行以下操作:

        1. 备份当前 Deployment 配置:
           kubectl --context tencent-100014919455-cn-main -n prometheus \
             get deployment thanos-query -o yaml > backup.yaml

        2. 添加 resource requests/limits:
           kubectl --context tencent-100014919455-cn-main -n prometheus \
             patch deployment thanos-query --type=json -p='[...]'

        3. 等待 Pod 滚动更新完成
        4. 验证 QoS Class = Burstable
      relation:                            # 互斥/并行关系,默认 parallel
        mode: parallel
  long_term:
    - title: "thanos-query 增加到 2 副本"
      action: "将 replicas 从 1 改为 2,配合 PDB 保证可用性"
      addresses: "causal_chain[0]"
      expected_effect: "单 Pod 故障不再影响服务可用性"
```

## 字段说明

### alert_summary

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `incidents` | list | 是 | 本 CG 包含的所有 PD incident 编号列表 |
| `entity` | string | 是 | 受影响的主实体名（如服务名、主机名） |
| `service` | string | 是 | PagerDuty service 名 |
| `cloud_account` | string | 是 | 云账户 ID（如 `tencent-100014919455`） |
| `cluster` | string | 否 | K8s cluster context（非 K8s 告警可省略） |
| `environment` | string | 是 | 环境标识（可多个） |
| `region` | string | 是 | 部署区域 |
| `severity` | string | 是 | 严重程度（P1/P2/P3/P4） |
| `triggered_at` | string | 是 | 第一条告警触发时间（ISO 8601） |
| `diagnosed_at` | string | 是 | 因果链完成评估时间（ISO 8601） |
| `pagerduty_url` | string | 是 | 主告警 PagerDuty URL |

### diagnosis_process

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `investigation_rounds` | integer | 是 | 总调查轮次，上限为 3 |
| `dimensions_investigated` | list | 是 | 已调查的所有维度列表 |
| `dimensions_not_available` | list | 是 | 无法访问的数据源汇总 |
| `cross_validation` | string | 是 | 跨维度交叉验证结论说明 |

### causal_chain

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `confidence` | string | 是 | 因果链置信度（high/medium/low） |
| `reasoning` | string | 是 | 推理过程说明 |
| `investigation_rounds` | integer | 是 | 构建本链耗费的调查轮次 |
| `depth_limit_reached` | boolean | 是 | 是否达到 3 轮调查深度限制 |
| `evaluation.complete` | boolean | 是 | 因果链是否通过完整性评估 |
| `evaluation.criteria` | list | 是 | 4 个评估判据，见下方"评估判据"部分 |
| `evaluation.incomplete_reason` | string | 条件必填 | `complete=false` 时必填，说明不完整原因 |
| `evaluation.next_investigation_hint` | string | 条件必填 | `complete=false` 时必填，建议人工排查方向 |
| `chain[].event` | string | 是 | 事件描述 |
| `chain[].type` | string | 是 | 节点类型：`root_cause`/`contributing_factor`/`intermediate`/`symptom`/`amplifier`/`impact` |
| `chain[].actionable` | boolean | 否 | 是否有对应的修复动作 |
| `chain[].source` | string | 是 | 数据来源 |
| `chain[].caused_by` | list | 否 | 前置节点在 chain 数组中的索引（从 0 开始） |
| `chain[].introduced` | string | 否 | 问题引入时间或场景 |
| `chain[].duration` | string | 否 | 影响持续时长 |

### 评估判据（causal_chain.evaluation.criteria）

4 个判据全部 `passed: true` 时 `complete: true`：

| id | name | 含义 |
|----|------|------|
| ① | 可行动 | 链首有明确可执行的修复动作 |
| ② | 可解释 | 因果链完整解释了 WHY（不只是"什么"，还要"为什么"） |
| ③ | 最早点 | 链首是追溯的合理终点（再往上追是不可控的外部因素或设计决策） |
| ④ | 可预防 | 知道了根因后能设计预防措施 |

### impact

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `affected_services` | list | 是 | 受影响的服务列表 |
| `affected_users` | string | 是 | 受影响用户群体描述 |
| `blast_radius` | string | 是 | 影响范围描述 |
| `data_loss_risk` | boolean | 是 | 是否存在数据丢失风险 |

### risk_assessment

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `current_risk` | string | 是 | 当前风险等级（high/medium/low） |
| `trend` | string | 是 | 风险趋势（stable/worsening/improving） |
| `eta_to_critical` | string/null | 是 | 预计到达临界状态的时间，不适用时为 `null` |
| `single_point_of_failure` | boolean | 是 | 是否为单点故障 |
| `auto_recovery_possible` | boolean | 是 | 是否可能自愈 |

### solutions

**⚠️ 重要**：solution 字段的**唯一事实来源**是 `scripts/report_builder.py` 的 `SOLUTION_SCHEMA` 常量。本表仅作为可读文档，若本表与 `SOLUTION_SCHEMA` 不一致，以后者为准。`tests/test_report_builder.py::TestSolutionSchema` 守护两者对齐。

| 字段 | 类型 | short 必填 | long 必填 | 说明 |
|------|------|----------|----------|------|
| `title` | string | ✅ | ✅ | 方案简短标题(显示在审批卡片标题) |
| `action` | string | ✅ | ✅ | 操作描述(一句话) |
| `addresses` | string | ✅ | ✅ | 解决的因果链节点引用,如 `causal_chain[0]` |
| `expected_effect` | string | ✅ | ✅ | 预期效果(执行后世界会变成什么样) |
| `risk` | string | ✅ | — | 操作风险等级 (critical/high/medium/low) |
| `reversible` | bool | ✅ | — | 是否可回滚 (审批人核心决策依据) |
| `blast_radius` | string | ✅ | — | 操作影响的实例/服务/资源数量描述 |
| `verify_cmd` | string | ✅ | — | Execution 执行前验证问题仍存在的命令 |
| `prompt` | string | ✅ | — | Zero-context 完整执行步骤,见 spec §4.3 |
| `relation` | dict | 可选 | — | 互斥/并行关系: `{mode: parallel\|exclusive, group: id}` |

**字段语义:**
- **`addresses`**: 必须能在 `causal_chain.chain[]` 中索引到真实节点,用于反向追溯"这个修复对应哪个根因"
- **`reversible`**: L4 审批的核心决策依据。`false` 的变更需要审批人更谨慎(如删除数据、数据库 DDL)
- **`blast_radius`**: 必须量化(如"18 个 Pod 重启"),不能写"小范围"这种定性词
- **`relation.mode=parallel`** (默认): 所有 parallel solution 独立审批、独立执行
- **`relation.mode=exclusive`**: 同一 `group` 内的多个 solution 二选一,dispatcher 批准其一后自动撤销同组其他

## 规则

1. **`short_term[].prompt` 必须是 zero-context 可执行的完整指南**,包含云厂商+账户 ID、K8s context、namespace、资源类型+名称、当前值、目标值、每步命令、验证步骤+期望输出、引用的 skill、备份步骤。
2. **`short_term[].addresses` 必须指向真实存在的因果链节点**,禁止写 `causal_chain[99]` 这种越界引用。
3. **`short_term[].reversible` 必须准确填写**,严禁把实际不可回滚的操作标记为 `true`。错误标注会误导审批人批准危险操作。
4. **互斥 solution 必须共享同一 `relation.group`**,审批卡片会自动提示"二选一"语义,批准一个会自动撤销同组其他。
5. 因果链完整性评估的 4 个判据必须全部评估,任一不满足则 `complete: false`。
6. `complete: false` 时必须填写 `incomplete_reason` 和 `next_investigation_hint`。
7. `chain` 的节点类型中,`root_cause` 表示可操作的根本原因,`symptom` 表示表面现象,`amplifier` 表示放大因素,`impact` 表示最终影响。

## Schema 变更流程

修改 solution 字段需同步更新:
1. `scripts/report_builder.py` 的 `SOLUTION_SCHEMA` (单一事实来源)
2. `scripts/report_builder.py::add_solution()` 签名
3. 本文档(字段表 + YAML 示例)
4. `references/investigation-prompt-template.md` 的 FINALIZE 示例
5. `scripts/dispatcher_loop.py::_build_approval_detail()` 渲染逻辑
6. `scripts/completion_gate.py::validate_report()`
7. `scripts/tests/test_report_builder.py::TestSolutionSchema.EXPECTED_FIELDS`

`TestSolutionSchema` 守护 schema 稳定性,会在缺少任一同步时失败。
