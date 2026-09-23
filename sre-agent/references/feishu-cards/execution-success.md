# 执行成功通知卡片

## 时机
Execution Subagent 完成变更执行并验证通过后，发送此通知。

## 发送方式
使用 `scripts/feishu_notify.py send-elements` 或 @lark/reply-feishu skill。

## 卡片内容

标题: `🔧 变更已执行`
颜色: green（执行成功使用绿色）

### Elements（顺序）

1. **变更概要**
2. **验证结果**
3. **变更记录路径**
4. **关联告警引用**

---

## 各 Element 详细格式

### 1. 变更概要

```
✅ **变更执行成功**

- **操作:** {change_desc_human}
- **关联:** CG-{cg_id}（Solution {solution_idx}）
- **执行时间:** {executed_at}
- **执行环境:** {cloud} / {account} / {cluster} / {namespace}
```

### 2. 验证结果

```
**验证结果:**
{verification_summary}
```

示例：
```
**验证结果:**
✓ Pod QoS Class = Burstable
✓ thanos-query Ready: 1/1
✓ 内存使用降至 650Mi（limits: 2Gi）
```

### 3. 变更记录路径

```
**变更记录:** `.sre-agent/executions/{cg_id}-solution-{solution_idx}/`
（含 summary.md 和 backup/ 目录）
```

### 4. 关联告警引用（可选）

```
**告警组:** CG-{cg_id}
如需查看完整诊断报告，请访问 `.sre-agent/investigations/{cg_id}/report.yaml`
```

---

## 完整渲染示例

```
🔧 变更已执行

✅ **变更执行成功**

- **操作:** 给 thanos-query 添加 resource limits
- **关联:** CG-443258（Solution 1）
- **执行时间:** 2026-03-25 11:05
- **执行环境:** 腾讯云 / tencent-100014919455 / cn-main / monitoring

**验证结果:**
✓ Pod QoS Class = Burstable
✓ thanos-query Ready: 1/1
✓ 内存使用降至 650Mi（limits: 2Gi）

**变更记录:** `.sre-agent/executions/CG-443258-solution-1/`
（含 summary.md 和 backup/ 目录）

**告警组:** CG-443258
如需查看完整诊断报告，请访问 `.sre-agent/investigations/CG-443258/report.yaml`
```

---

## 输入变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `{change_desc_human}` | string | 人类可读的操作描述（如"给 thanos-query 添加 resource limits"） |
| `{change_desc}` | string | 英文小写+连字符目录名（如 `thanos-query-limits`） |
| `{cg_id}` | string | Correlation Group ID |
| `{solution_idx}` | int | solution 索引 |
| `{executed_at}` | datetime | 执行完成时间 |
| `{cloud}` | string | 云平台 |
| `{account}` | string | 云账户 |
| `{cluster}` | string | K8s 集群 |
| `{namespace}` | string | K8s 命名空间 |
| `{verification_summary}` | string | 验证结果摘要（多行，每行 ✓ 开头） |
| `{date}` | string | 日期（YYYY-MM-DD） |
