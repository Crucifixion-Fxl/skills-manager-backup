# 审批过期通知卡片

## 时机
`approval_sent_at + 48h` 到达后，Dispatcher 自动将 solution 状态更新为 `expired`，并发送此通知。

## 发送方式
使用 `scripts/feishu_notify.py send-elements` 或 @lark/reply-feishu skill。

## 卡片内容

标题: `⏰ 审批已过期`
颜色: grey（过期类通知使用灰色）

### Elements（顺序）

1. **过期信息**
2. **关联告警引用**
3. **方案归档说明**
4. **重新触发说明**

---

## 各 Element 详细格式

### 1. 过期信息

```
**审批请求已超过 48 小时，自动过期。**

- **关联告警:** CG-{cg_id}
- **方案编号:** Solution {solution_idx}
- **操作摘要:** {solution_title}
- **审批发出时间:** {approval_sent_at}
- **过期时间:** {expired_at}
```

### 2. 方案归档说明

```
**方案已归档**

方案详情已保存至：
`.sre-agent/investigations/{cg_id}/report.yaml`（solution_idx: {solution_idx}）

状态已更新为 `expired`，不会自动执行。
```

### 3. 重新触发说明

```
**如需重新审批：**
在飞书回复 `重新触发 CG-{cg_id} solution-{solution_idx}`，Dispatcher 将重新发送审批卡片。
```

---

## 完整渲染示例

```
⏰ 审批已过期

**审批请求已超过 48 小时，自动过期。**

- **关联告警:** CG-443258
- **方案编号:** Solution 1
- **操作摘要:** 给 thanos-query 添加 resource limits
- **审批发出时间:** 2026-03-25 10:55
- **过期时间:** 2026-03-27 10:55

**方案已归档**

方案详情已保存至：
`.sre-agent/investigations/CG-443258/report.yaml`（solution_idx: 1）

状态已更新为 `expired`，不会自动执行。

**如需重新审批：**
在飞书回复 `重新触发 CG-443258 solution-1`，Dispatcher 将重新发送审批卡片。
```

---

## 相关通知

- **24h 提醒**（非独立卡片，更新现有审批卡片）：在原审批卡片下追加提醒文字 `⏰ 审批将在 {remaining_hours}h 后自动过期`
- **审批过期**：发送此独立卡片 `approval-expired.md`

## 输入变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `{cg_id}` | string | Correlation Group ID |
| `{solution_idx}` | int | solution 索引 |
| `{solution_title}` | string | 方案操作摘要 |
| `{approval_sent_at}` | datetime | 审批发出时间 |
| `{expired_at}` | datetime | 过期时间（= approval_sent_at + 48h） |
