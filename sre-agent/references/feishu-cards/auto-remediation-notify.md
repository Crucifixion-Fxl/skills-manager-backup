# 飞书卡片：自动修复通知（L5）

## 卡片规格

| 字段 | 值 |
|------|----|
| Title | 🤖 自动修复已执行 |
| Color | blue |

## 卡片内容

```
🤖 自动修复已执行

**关联 CG：** {cg_id}
**匹配 Pattern：** {pattern_id}（{pattern_name}）

---

**操作摘要**
{operation_summary}

**验证预期**
{verify_expectation}

**回滚方法**
{rollback_method}

---
> 此操作由预定义的低风险 auto-remediation pattern 自动触发，无需人工审批
```

## 输入变量

| 变量 | 说明 |
|------|------|
| `{cg_id}` | 关联的 CG 编号，如 `CG-20240325-001` |
| `{pattern_id}` | 匹配到的 pattern ID，如 `AR-001` |
| `{pattern_name}` | Pattern 名称描述 |
| `{operation_summary}` | 本次执行的操作摘要（集群/namespace/资源/具体操作） |
| `{verify_expectation}` | 验证预期结果描述 |
| `{rollback_method}` | 如验证失败的回滚方式 |
