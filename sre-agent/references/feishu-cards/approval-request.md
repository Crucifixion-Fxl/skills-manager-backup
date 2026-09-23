# L4 审批通知卡片

## 时机
Dispatcher 创建飞书审批实例后，同时发此通知卡片到群里。

## 发送方式
使用 `scripts/feishu_notify.py send-elements` 发送通知卡片（无交互按钮）。

## 卡片内容

标题: `🔧 变更审批请求 CG-{cg_id} ST-{solution_idx}`
颜色: blue（审批类卡片固定蓝色）

### Elements（顺序）

1. **关联告警信息**
2. **资源变更审批表**（CLAUDE.md 格式）
3. **因果链节点引用**
4. **操作详情**
5. **审批有效期提示**
6. **前往审批链接按钮**

---

## 各 Element 详细格式

### 1. 关联告警信息

```
**关联告警:** CG-{cg_id}
**方案编号:** Solution {solution_idx}
**服务:** {service_name}
**环境:** {environment}
```

### 2. 资源变更审批表

符合 CLAUDE.md 中的资源变更审批清单格式：

```
**资源变更详情**

| 云 | 账户 | 集群 | 命名空间 | 环境 | 服务 | 资源 | 操作 | 影响 |
|---|------|------|---------|------|------|------|------|------|
| {cloud} | {account} | {cluster} | {namespace} | {env} | {service} | {resource} | {action} | {impact} |
```

示例行：
```
| 腾讯云 | tencent-100014919455 | cn-main | monitoring | cn-prod | thanos-query | Deployment | 添加 resource limits (CPU:500m/2000m, Mem:512Mi/2Gi) | Pod 滚动重启，服务短暂抖动 <30s |
```

### 3. 因果链节点引用

```
**因果链节点:** {causal_chain_ref}
（此方案针对因果链中的 {node_type} 节点）
```

示例：`**因果链节点:** 🔴 无 resource limits (BestEffort QoS)`

### 4. 操作详情

```
**操作详情:**
{prompt_summary}
```

`prompt_summary` 为 solution_prompt 的摘要（非完整 prompt），包含：
- 操作目标
- 具体步骤概述（3-5 步）
- 验证方式

### 5. 审批有效期提示

```
**⏰ 审批有效期:** 48 小时（{expiry_time} 过期）
如需查看完整执行 prompt，请回复查看 CG-{cg_id} solution-{solution_idx}
```

### 6. 前往审批链接按钮

```
[前往审批]({approval_instance_url})
```

审批操作在飞书审批中心完成（非卡片按钮）。
Dispatcher 每轮 cron 通过 `feishu_approval.py get-status` 轮询审批结果。

---

## 完整渲染示例

```
🔧 变更审批请求 CG-443258 ST-1

**关联告警:** CG-443258
**方案编号:** Solution 1
**服务:** thanos-query
**环境:** cn-prod

**资源变更详情**
| 云 | 账户 | 集群 | 命名空间 | 环境 | 服务 | 资源 | 操作 | 影响 |
|---|------|------|---------|------|------|------|------|------|
| 腾讯云 | tencent-100014919455 | cn-main | monitoring | cn-prod | thanos-query | Deployment | 添加 resource limits (CPU:500m/2000m, Mem:512Mi/2Gi) | Pod 滚动重启，服务短暂抖动 <30s |

**因果链节点:** 🔴 无 resource limits (BestEffort QoS)
（此方案针对因果链中的 root_cause 节点）

**操作详情:**
1. 获取当前 thanos-query Deployment 配置（备份到 .sre-agent/executions/ 目录）
2. 设置 resources.requests: {cpu: 500m, memory: 512Mi}
3. 设置 resources.limits: {cpu: 2000m, memory: 2Gi}
4. 验证 Pod QoS Class = Burstable
5. 验证 Pod 重启后正常运行（Ready: 1/1）

**⏰ 审批有效期:** 48 小时（2026-03-27 10:55 过期）
如需查看完整执行 prompt，请回复查看 CG-443258 solution-1

[前往审批](https://applink.feishu.cn/client/mini_program/open?appId=xxx&path=approval/detail&instance_id=feishu_approval_instance_xxx)
```

---

## 审批闭环说明

审批在飞书审批中心完成，卡片仅用于通知和快速跳转。禁止使用卡片交互按钮完成审批。

## 审批超时逻辑（Dispatcher 检查）

每轮 cron 检查所有 `pending_approval` 状态的 solution：

```
检查逻辑（伪代码）:
for solution in pending_approval_solutions:
    elapsed = now - solution.approval_sent_at

    if elapsed >= 48h:
        # 自动过期，调用 feishu_approval.py cancel 撤销审批实例
        python3 scripts/feishu_approval.py cancel \
          --instance-id {instance_id} \
          --user-id {approver_user_id}
        solution.status = "expired"
        send_card(approval-expired.md, solution)

    elif elapsed >= 24h and not solution.reminder_sent:
        # 发 24h 提醒（仅发一次）
        solution.reminder_sent = True
        send_reminder_card(
            title="⏰ 审批即将过期",
            message=f"CG-{cg_id} Solution-{idx} 审批将在 {remaining_hours}h 后自动过期",
            cg_id=cg_id,
            solution_idx=idx
        )
```

超时后 solutions 持久化在 `.sre-agent/investigations/{id}/report.yaml` 中，可手动重新触发审批。

---

## 输入变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `{cg_id}` | string | Correlation Group ID |
| `{solution_idx}` | int | solution 索引（0-based） |
| `{service_name}` | string | 主要受影响服务 |
| `{environment}` | string | 环境 |
| `{cloud}` | string | 云平台（腾讯云 / AWS / GCP） |
| `{account}` | string | 云账户 ID |
| `{cluster}` | string | K8s 集群名 |
| `{namespace}` | string | K8s 命名空间 |
| `{env}` | string | 环境标识 |
| `{service}` | string | 服务名 |
| `{resource}` | string | 资源类型（如 Deployment） |
| `{action}` | string | 操作描述 |
| `{impact}` | string | 影响说明 |
| `{causal_chain_ref}` | string | 因果链节点引用文字（含 emoji 符号） |
| `{node_type}` | string | 节点类型（root_cause/contributing_factor/etc） |
| `{prompt_summary}` | string | 执行 prompt 摘要（非完整 prompt） |
| `{expiry_time}` | datetime | 审批过期时间（approval_sent_at + 48h） |
| `{approval_instance_url}` | string | 飞书审批实例跳转链接 |
