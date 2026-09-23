# 执行失败通知卡片

## 时机
Execution Subagent 执行变更失败（操作失败或验证不通过）时发送，无论是否成功回滚。

## 发送方式
使用 `scripts/feishu_notify.py send-elements` 或 @lark/reply-feishu skill。

## 卡片内容

标题: `⚠️ 变更执行失败`
颜色: red（执行失败使用红色）

### Elements（顺序）

1. **失败概要**
2. **错误详情**
3. **回滚状态**
4. **下一步建议**

---

## 各 Element 详细格式

### 1. 失败概要

```
❌ **变更执行失败，需要人工介入。**

- **操作:** {change_desc_human}
- **关联:** CG-{cg_id}（Solution {solution_idx}）
- **失败时间:** {failed_at}
- **失败阶段:** {failed_step}（执行 / 验证）
```

### 2. 错误详情

```
**错误信息:**
{error_summary}
```

示例：
```
**错误信息:**
kubectl apply 返回非零退出码 (exit code 1)
Error: resource limits 中 cpu 值 "500m" 格式无效，期望格式为 "0.5" 或 "500m"（实际原因：namespace 配额已满）
```

### 3. 回滚状态

**已回滚：**
```
**回滚状态:** ✅ 已自动回滚
已从 backup/ 恢复原始配置，资源状态与变更前一致。
备份位置: `.sre-agent/executions/{cg_id}-solution-{solution_idx}/backup/`
```

**无法回滚：**
```
**回滚状态:** ⚠️ 无法自动回滚，需人工介入
原因: {rollback_failure_reason}
建议: 检查 `.sre-agent/executions/{cg_id}-solution-{solution_idx}/backup/` 中的备份手动恢复
```

### 4. 下一步建议

```
**下一步:**
1. 查看变更日志: `.sre-agent/executions/{cg_id}-solution-{solution_idx}/summary.md`
2. 排查错误原因后可重新触发审批: 在飞书回复 `重新触发 CG-{cg_id} solution-{solution_idx}`
3. 如需人工执行，完整 prompt 见: `.sre-agent/investigations/{cg_id}/report.yaml`（solution_idx: {solution_idx}）
```

---

## 完整渲染示例（已回滚）

```
⚠️ 变更执行失败

❌ **变更执行失败，需要人工介入。**

- **操作:** 给 thanos-query 添加 resource limits
- **关联:** CG-443258（Solution 1）
- **失败时间:** 2026-03-25 11:07
- **失败阶段:** 验证（执行成功但验证失败）

**错误信息:**
Pod 滚动重启后 thanos-query 仍处于 CrashLoopBackOff 状态
预期: Ready: 1/1，实际: Ready: 0/1（CrashLoopBackOff）
可能原因: resource limits 设置过低，Pod 启动时立即 OOMKilled

**回滚状态:** ✅ 已自动回滚
已从 backup/ 恢复原始配置，资源状态与变更前一致。
备份位置: `.sre-agent/executions/CG-443258-solution-1/backup/`

**下一步:**
1. 查看变更日志: `.sre-agent/executions/CG-443258-solution-1/summary.md`
2. 排查错误原因后可重新触发审批: 在飞书回复 `重新触发 CG-443258 solution-1`
3. 如需人工执行，完整 prompt 见: `.sre-agent/investigations/CG-443258/report.yaml`（solution_idx: 1）
```

## 完整渲染示例（无法回滚）

```
⚠️ 变更执行失败

❌ **变更执行失败，需要人工介入。**

- **操作:** 更新 thanos-query Ingress host 配置
- **关联:** CG-443258（Solution 2）
- **失败时间:** 2026-03-25 11:12
- **失败阶段:** 执行（kubectl apply 失败）

**错误信息:**
kubectl apply 返回 exit code 1
Error: namespaces "monitoring" is forbidden: namespace has been deleted

**回滚状态:** ⚠️ 无法自动回滚，需人工介入
原因: 命名空间已被删除，kubectl apply 无法恢复
建议: 检查 `.sre-agent/executions/CG-443258-solution-2/backup/` 中的备份手动恢复

**下一步:**
1. 查看变更日志: `.sre-agent/executions/CG-443258-solution-2/summary.md`
2. 手动恢复 monitoring 命名空间后重新触发
3. 完整 prompt 见: `.sre-agent/investigations/CG-443258/report.yaml`（solution_idx: 2）
```

---

## 输入变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `{change_desc_human}` | string | 人类可读操作描述 |
| `{change_desc}` | string | 英文小写+连字符目录名 |
| `{cg_id}` | string | Correlation Group ID |
| `{solution_idx}` | int | solution 索引 |
| `{failed_at}` | datetime | 失败时间 |
| `{failed_step}` | string | 失败阶段（`执行` / `验证`） |
| `{error_summary}` | string | 错误摘要（多行，不含敏感信息） |
| `{rollback_success}` | bool | 是否成功回滚 |
| `{rollback_failure_reason}` | string | 回滚失败原因（仅 rollback_success=false 时使用） |
| `{date}` | string | 日期（YYYY-MM-DD） |
