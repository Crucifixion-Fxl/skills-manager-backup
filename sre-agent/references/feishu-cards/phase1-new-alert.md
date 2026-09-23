# Phase 1 新告警卡片

## 时机
新告警到达时（poll 周期内，~1min），由 Dispatcher 直接发送。

## 发送方式
使用 `scripts/feishu_notify.py send-elements` 或 @lark/reply-feishu skill。

## 卡片内容

标题: `🔔 On-Call 告警（调查中）`
颜色: 按 PagerDuty urgency（high=red, low=yellow）

Elements:
- markdown: `**{N}** 条告警触发`
- markdown: `**服务:** {service_name}`
- markdown: `**环境:** {environment}`
- markdown: `**初步分类:** {classification}`
- markdown: `**状态:** 🔄 深度调查进行中...`
- hr
- markdown: `**告警列表:**`
- table: 告警 ID / 标题 / 触发时间 / 严重级别
- action: [查看 PagerDuty]({pagerduty_url})

## 渲染示例

```
🔔 On-Call 告警（调查中）

**3** 条告警触发
**服务:** thanos-query
**环境:** cn-prod
**初步分类:** 疑似 OOM 故障
**状态:** 🔄 深度调查进行中...

---

**告警列表:**
| 告警 ID | 标题 | 触发时间 | 严重级别 |
|--------|------|---------|---------|
| Q1ABC123 | [cn-prod] thanos-query OOMKilled | 2026-03-25 10:42 | critical |
| Q1DEF456 | [cn-prod] thanos-store restarting | 2026-03-25 10:43 | high |
| Q1GHI789 | [cn-prod] thanos-cn.addx.live 不可达 | 2026-03-25 10:45 | critical |

[查看 PagerDuty]
```

## 输入变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `{N}` | int | 告警数量 |
| `{service_name}` | string | 服务名（来自 PagerDuty service.summary） |
| `{environment}` | string | 环境（从告警标题解析，如 cn-prod） |
| `{classification}` | string | 初步分类结果，取值：`疑似 X 故障` / `匹配已知问题 KI-xxx` / `未知` |
| `{incidents_table}` | markdown table | 告警列表（ID / 标题 / 触发时间 / 严重级别） |
| `{pagerduty_url}` | url | PagerDuty 告警组链接 |
| `{urgency}` | string | PagerDuty urgency（`high` / `low`），决定卡片颜色 |
| `{cg_id}` | string | Correlation Group ID，如 CG-443258 |

## 颜色逻辑

```python
card_color = "red" if urgency == "high" else "yellow"
```
