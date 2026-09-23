# Phase 1 关联告警卡片

## 时机
新告警到达，且与已有活跃 CG 关联时，由 Dispatcher 发送。
新告警会暂存到 `.sre-agent/investigations/{id}/pending-alerts/batch-{n}.json`，等待前序 Investigation 完成后 merge。

## 发送方式
使用 `scripts/feishu_notify.py send-elements` 或 @lark/reply-feishu skill。

## 卡片内容

标题: `🔔 On-Call 告警更新`
颜色: 按 PagerDuty urgency（high=red, low=yellow）

Elements:
- markdown: `**{N}** 条新告警关联 CG-{cg_id}`
- markdown: `**与已知调查 CG-{cg_id} 为同一根因**`
- markdown: `**服务:** {service_name}`
- markdown: `**环境:** {environment}`
- markdown: `**状态:** 🔄 深度调查进行中...`
- hr
- markdown: `**新增告警:**`
- table: 告警 ID / 标题 / 触发时间 / 严重级别
- action: [查看 PagerDuty]({pagerduty_url})

## 渲染示例

```
🔔 On-Call 告警更新

**2** 条新告警关联 CG-443258
**与已知调查 CG-443258 为同一根因**
**服务:** thanos-query
**环境:** cn-prod
**状态:** 🔄 深度调查进行中...

---

**新增告警:**
| 告警 ID | 标题 | 触发时间 | 严重级别 |
|--------|------|---------|---------|
| Q1JKL012 | [cn-prod] thanos-ruler alert evaluation error | 2026-03-25 10:50 | high |
| Q1MNO345 | [cn-prod] AlertManager 无法连接 thanos-query | 2026-03-25 10:51 | warning |

[查看 PagerDuty]
```

## 输入变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `{N}` | int | 新增告警数量 |
| `{cg_id}` | string | 已有 Correlation Group ID，如 CG-443258 |
| `{service_name}` | string | 服务名 |
| `{environment}` | string | 环境 |
| `{new_incidents_table}` | markdown table | 新增告警列表（ID / 标题 / 触发时间 / 严重级别） |
| `{pagerduty_url}` | url | PagerDuty 告警组链接 |
| `{urgency}` | string | PagerDuty urgency（`high` / `low`） |

## 与 phase1-new-alert.md 的区别

| 项目 | phase1-new-alert | phase1-correlated |
|------|-----------------|-------------------|
| 标题 | 🔔 On-Call 告警（调查中） | 🔔 On-Call 告警更新 |
| 关联信息 | 无（新 CG） | 关联已有 CG-{id} |
| 语义 | 全新调查开始 | 已知调查的补充告警 |
| 状态处理 | 新建 CG + dispatch investigation | 暂存 pending，等前序 CG 完成 |
