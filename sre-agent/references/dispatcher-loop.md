# Dispatcher Loop — 每轮 Cron 执行逻辑

每分钟触发一次。Context 增量 ~100 行/轮，长时间运行不膨胀。

## Subagent 派发协议（通用规则，适用于本文件所有 dispatch 示例）

dispatcher_loop.py 把每个 subagent 的完整 prompt 写入 `.sre-agent/pending-dispatches/{cg_id}-{type}{suffix}.md`，并在 action plan 里通过 `prompt_file` 字段返回路径。LLM cron 派发 subagent 时 **必须遵守以下两条**：

1. **禁止 Read + 全文透传**：禁止先用 Read tool 读取 `prompt_file` 再把完整内容作为 Agent tool 的 `prompt` 参数。20~40KB 的 prompt 经过 dispatcher LLM 输出通道会产生分钟级延迟（Opus ~50 tok/s），是"dispatching 耗时很长"的根本原因。

   使用 wrapper prompt 让 subagent 自己 Read：

   ```
   Agent tool:
     description: "<人类可读的任务描述>"
     prompt: "严格按 {prompt_file} 中的指令执行。第一步用 Read tool 读取该文件完整内容（文件可能 400+ 行，必要时分段读完），然后立即开始执行，不得总结、改写、跳步。"
     run_in_background: true
   ```

   subagent 在隔离 context 里加载 prompt 文件，dispatcher LLM 只输出 ~100 字符 wrapper，单次派发从分钟级降到秒级。

2. **强制并行派发**：同一轮 dispatcher_loop.py 输出的 action plan 中若包含 ≥2 个 dispatch action，**必须在同一条 assistant 消息里通过多个 Agent 工具调用并行发出**，禁止串行。串行派发会让 subagent 启动时间叠加，拖慢整轮 cron。

下文所有 "派发 X Subagent" 的描述，均默认按本协议执行，不再逐处重复示例。

## 执行步骤

### Step 1: 读状态

读 `.sre-agent/alert_state.json`。
如果文件不存在，调用 state_manager.py 初始化。

### Step 2: Poll PagerDuty

```bash
python3 scripts/pagerduty_api.py oncall-poll --since {last_poll_at} --json
```

如果 `last_poll_at` 为 null（首次），使用 `--since` 为当前时间减 10 分钟。
将返回的 incidents 列表与 `processed_incident_ids` 比对，过滤出新告警。
如果无新告警，跳到 Step 6。

### Step 3: 告警关联

对新告警调用 alert_correlator.py 的 `correlate_incidents(new_incidents, active_cgs, completed_cgs)` 逻辑。

**关联规则**：
1. 标题标准化后精确匹配（去数字/UUID/Pod后缀/时间戳）→ 合并
2. ±5min 内同故障实体/同环境 → 关联
3. 与 completed CGs 标题标准化匹配 → 标记为复发（`is_recurrence: true`，引用 `recurrence_of: CG-X`）

**故障实体定义**：从告警标题/描述中提取的核心资源标识，如 Deployment 名称、Pod 前缀（去除 ReplicaSet hash 和 Pod 随机后缀）、RDS 实例 ID、Redis 节点、ELB 名称等。同一故障实体 = 标准化后的实体标识完全一致。

**同环境定义**：环境从告警 labels 中的 `namespace`、`cluster`、`environment` 等字段提取，同一环境 = 环境标识完全一致（如 `us-prod`、`cn-main`）。

结果分三类：
- `correlates_to == null` 且 `is_recurrence == false` → 新告警组
- `correlates_to == "CG-X"` → 关联已有活跃 CG
- `correlates_to == null` 且 `is_recurrence == true` → 新告警组，但标记为历史 CG 的复发

### Step 4: 处理新告警组

对每个新告警组：

1. 调用 `state_manager.create_cg(incidents, service, environment)` → 获得 CG-{id}
2. 调用 `state_manager.mark_processed(incident_ids)`
3. 发 Phase 1 飞书通知（模板: `feishu-cards/phase1-new-alert.md`）：
   - 使用 `scripts/feishu_notify.py send-elements` 发送
   - 颜色按 PagerDuty urgency: high=red, low=yellow
4. 派发 Investigation subagent（按顶部"Subagent 派发协议"执行 wrapper prompt，禁止 Read 全文透传）。
5. 更新 CG status 为 "investigating"

### Step 5: 处理关联告警

对每个关联到已有 CG 的组：

1. 调用 `state_manager.mark_processed(incident_ids)`
2. 调用 `state_manager.save_pending_batch(cg_id, batch_num, incidents_data)`
3. 调用 `state_manager.add_incidents_to_cg(cg_id, incident_ids)`
4. 发 Phase 1 关联通知（模板: `feishu-cards/phase1-correlated.md`）

### Step 6: 检查已完成的 CG

遍历 `completed_correlation_groups`：

对每个刚完成（`solutions_status` 为空）的 CG：

**6a. 检查 pending batch**：
```
pending = state_manager.load_pending_batches(cg_id)
if pending:
  → dispatcher_loop.py 将完整 merged prompt 写入 pending-dispatches/{cg_id}-investigation-merged.md
  → 派发 merged Investigation subagent（wrapper 协议，run_in_background: true）
  → state_manager.clear_pending(cg_id)
```

**6b. 路由 solutions（无 pending 时）**：
读 `state_manager.load_cg_result(cg_id)` → `solutions.short_term[]`

对每个 solution：
```
匹配 auto-remediation-patterns.yaml？
├─ 命中 + risk=low + reversible=true + cooldown 未命中 + 同集群无并发执行
│   → dispatcher_loop.py 将完整 execution prompt 写入 pending-dispatches/{cg_id}-execution-s{idx}.md
│   → 派发 Execution Subagent（wrapper 协议，run_in_background: true）
│   → 发飞书通知 auto-remediation-notify.md
│   → state_manager.save_approval(cg_id, idx, {status: "auto_executed"})
│
└─ 未命中
    → 调用 feishu_approval.py create 创建飞书审批实例，获得 instance_id
    → 发飞书通知卡片 approval-request.md（含审批实例链接）
    → state_manager.save_approval(cg_id, idx, {status: "pending_approval", approval_sent_at: now, instance_id: instance_id})
```

### Step 7: 检查审批状态

**7a. 审批状态轮询**：
每轮遍历 `.sre-agent/approvals/` 下所有 `pending_approval` 状态的文件。

对每个 pending approval，用 instance_id 调用:
```
python3 scripts/feishu_approval.py get-status --instance-id {instance_id}
```

根据返回状态处理：
```
APPROVED → update to "approved" → dispatch Execution Subagent
REJECTED → update to "rejected" → notify "已取消"
PENDING  → skip
```

**7b. 超时检查**：
```
expired = state_manager.list_expired_approvals(now, ttl_hours=48)
for (cg_id, idx) in expired:
  → 发飞书通知 approval-expired.md
  → state_manager.update_approval_status(cg_id, idx, "expired")

approaching = state_manager.list_approaching_expiry(now, warn_hours=24, ttl_hours=48)
for (cg_id, idx) in approaching:
  → 发飞书提醒"审批即将过期，剩余 {remaining}h"
```

### Step 8: 检查执行完成 → 触发 Pattern Extraction

遍历 approvals/ 中 status == "executed_success" 的 solution：

```
if not already_extracted:
  → 派发 Pattern Extraction Subagent（wrapper 协议，run_in_background: true）
    输入: pending-dispatches/{cg_id}-pattern-s{idx}.md（含 CG result + 执行记录引用）
  → 更新 status 为 "pattern_extracted"
```

### Step 9: 更新状态

```
state_manager.update_last_poll(now)
```

## CompletionGate 验证、分级超时与重试

### CompletionGate 验证

Dispatcher 加载 subagent 产出物后，通过 `CompletionGate` 校验结构完整性：
- **report.yaml** — `CompletionGate.validate_report()` 校验必填字段、solutions 格式
- **review.yaml** — `CompletionGate.validate_review()` 校验 verdict、reasoning
- **result.json** — `CompletionGate.validate_result()` 校验执行结果
- **pattern** — `CompletionGate.validate_pattern()` 校验模式提取结果

校验失败时，产出物被视为不存在（`report = None`），等待 subagent 重新产出或触发 crash recovery。

### 分级超时（Tiered Timeouts）

不同阶段和 investigation path 使用不同超时阈值：

| 阶段 | 路径 | 超时 |
|------|------|------|
| Investigation | default（首次 checkpoint 未出现） | 30min |
| Investigation | transient_event | 8min |
| Investigation | self_healed | 15min |
| Review | - | 10min |
| Execution | - | 15min |
| Pattern Extraction | - | 5min |

Investigation 超时由 `_get_investigation_timeout()` 根据 `02_validate` checkpoint 中的 `status` 字段动态决定。Crash recovery 清除 checkpoint 后，fallback 到 default（30min），属有意的安全方向 fallback。

### Investigation 最大重试

`MAX_INVESTIGATION_RETRIES = 2`。Investigation subagent 崩溃或超时后，CG 状态设为 `dispatch_pending` 并递增 `investigation_retry_count`。超过 2 次后，CG 状态设为 `investigation_failed`（终态），移入 `completed_correlation_groups`。

## Context 大小控制

每轮只处理增量，不重读历史。预期每轮 context:
- alert_state.json 读写: ~50 行
- 新告警处理: ~20 行/告警
- 审批检查: ~10 行
- 总计: ~100 行/轮
