# L5 自动修复安全约束

## 匹配条件

对每个 short_term solution，依次检查：

1. 遍历 `auto-remediation-patterns.yaml` 中所有 pattern
2. 用 pattern.match.finding_pattern（正则）匹配 solution 关联的 causal_chain findings
3. 检查 pattern.match.environment_pattern（如有）是否匹配当前环境
4. 全部匹配 → 候选

## 安全约束

| 约束 | 规则 |
|------|------|
| 风险等级 | 仅 `risk: low` 的 pattern 可自动执行 |
| 可回滚性 | 必须 `reversible: true` |
| 环境限制 | 部分 pattern 限定环境（如仅非 Prod） |
| 并发控制 | 同一集群/namespace 同时只允许 1 个自动修复 |
| Cooldown | 同一资源在 cooldown 秒内不重复执行 |
| 验证失败 | 自动回滚 → 升级为 L4 人工审批 → 飞书通知 |

## Dispatcher 匹配流程

solution 匹配到 AR pattern？
- 命中 + risk=low + reversible=true + cooldown 未命中 + 同集群无并发执行
  → 自动派发 Execution Subagent
  → 发飞书通知 auto-remediation-notify.md
  → state_manager.save_approval(cg_id, idx, {status: "auto_executed"})
- 未命中
  → 走 L4 审批
