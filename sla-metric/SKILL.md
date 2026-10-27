---
name: sla-metric
description: SLA 指标监控配置。集成 Superset、Dagster 和 dapp API，完成上游依赖发现与校验及 SLA 指标的新增、修改、查看/搜索。当用户需要配置 SLA 监控、设置指标告警阈值、查看 SLA 指标状态时使用。
---

# sla-metric

通过 Superset 和 dapp API 协同完成 SLA 指标监控的端到端配置，覆盖新增、修改、查看/搜索三大工作流。

## Description

平台登录与认证 SSOT：[dapp](../dapp/SKILL.md)。本 Skill 保留业务流程与门禁，登录/Token事实只在平台 owner 维护；日常访问调用 `web-access`。

首次接入/变更扫描与日常认证入口见 [SaaS 接入](../dapp/references/saas-access.md)；已有平台业务契约与授权门禁仍在本 Skill 维护。

**系统架构：** Superset 创建图表（指标来源） → dapp API 管理 SLA 配置 → Grafana 监控告警（执行监控）

**涉及系统：**

| 系统 | 用途 | 入口（US 默认） | 认证方式 |
|------|------|----------------|----------|
| Superset | 指标图表创建、查询 | `superset-us.addx.live` | JWT（用户名/密码） |
| Dagster | 上游数据资产发现、分区粒度校验 | `dagster.addx.live` | 复用 Dagster owner 的目标实例认证 |
| dapp API | SLA 指标 CRUD | `dapp-api.addx.live` | JWT Bearer Token |
| dapp Web | 删除、Grafana 构建、通知 | `dapp.addx.live` | 飞书 OAuth |
| Grafana | 监控看板和告警规则 | `grafana-next.addx.live` | — |

**多区域：** 默认 US，用户明确指定时切换。EU 加 `-eu` 后缀，CN 加 `-cn` 后缀（如 `dapp-api-eu.addx.live`）。

**告警等级：** `warning`（WARN，普通告警）、`critical`（P3+，严重告警）

## Rules

### 权限校验（操作前必须完成）

1. **Superset** — 要求用户提供凭据，完成登录验证
2. **dapp API** — 获取会话 Token（详见 [references/dapp-api.md](references/dapp-api.md#认证)）或使用环境变量 `DAPP_TOKEN`，再调用 `POST /api/v1/sla_metric/list` 验证可用性

- 两个系统都验证通过后才允许进入工作流
- **凭据仅用于当前会话，严禁写入文件或日志**

### 操作流程

根据用户意图选择工作流，详细步骤参见 [references/sla-config-flow.md](references/sla-config-flow.md)。

**dapp API 基地址：** `https://dapp-api.addx.live/api/v1/sla_metric`

完整 API 端点和数据模型参见 [references/dapp-api.md](references/dapp-api.md)。

### Superset 图表规则

- 通过本 skill 创建的图表，**名称必须以 `sla_metric_` 为前缀**
- **图表类型必须为折线图（line）**— dapp 仅支持解析 line chart，其他类型（bar、pie、table 等）无法被 dapp 使用
- 图表 `params.adhoc_filters` **必须包含时间列的 TEMPORAL_RANGE 过滤器**（如 `dt`）
- **metrics 必须使用聚合函数** — `sqlExpression` 必须包裹聚合函数（如 `MAX(match_rate)`、`AVG(value)`），裸列名（如 `match_rate`）会导致 `EXPRESSION_NOT_AGGREGATE` 错误。使用 `expressionType: 'SIMPLE'` 时必须指定 `aggregate` 字段
- **SQL 数据集必须返回多日数据** — 数据集 SQL 禁止硬编码 `WHERE dt = current_date` 等单日过滤，必须返回至少 15 天的历史数据（如 `WHERE dt >= date_add('day', -15, current_date)`），由 TEMPORAL_RANGE 过滤器控制展示范围
- 仅允许只读查询（SELECT），禁止 DDL/DML
- 详细操作参见 [references/superset-ops.md](references/superset-ops.md)

### 创建必填项

- **`biz_domain_id`（必填）、`biz_process_id`（必填）** — 创建前必须先查询分类选项（`GET /biz_domains`、`GET /biz_processes`）展示给用户选择
- `app_domain_id`（选填）— 建议填写以便分类
- 无匹配分类时，提示用户前往 [dapp Web UI](https://dapp.addx.live) 新增
- **阈值必填** — 至少配置一个告警等级（warning/critical）
- `metric_id` 必须设值 — 自定义指标填 `0`，预定义指标填 `metric.id`
- **`deps` 必须显式提供** — 使用下述发现结果序列化为 JSON 字符串；后端缺省为 `"[]"` 不代表已完成依赖检查

### 上游数据依赖（创建前必须完成）

`deps` 控制数仓上游完成后再查询 SLA 指标，与 `upstream_app_monitor_configs`（上游应用版本监控）用途不同，不能相互替代。

1. 按 [上游依赖发现与校验](references/upstream-dependencies.md)，取得与最终 `metric_config`、`time_grain` 一致的 Superset 查询 SQL，解析实际引用的物理表。
2. 复用 [Dagster](../../data/dagster/SKILL.md) 的认证契约，只读查询同区域、同环境的资产及分区信息，匹配并保留完整 asset key（如 `analytics/ads_order_count_di`）。检查每个依赖的分区窗口不大于 SLA 时间窗口。
3. **发现失败不等于无依赖。** SQL 获取/解析失败、权限不足、资产未匹配或匹配歧义、分区粒度无法核实，均须停止创建并说明缺口；禁止静默回退到 `deps="[]"`。只有已查明无适用 Dagster 依赖并在配置摘要中获得用户确认，才允许空数组。
4. 配置摘要必须包含源表 → 完整资产 key、粒度检查、排除项及原因；将确认后的去重依赖列表 JSON 序列化到 `deps`。
5. 修改指标来源、查询、过滤条件或 `time_grain` 时重新发现，并展示新旧依赖差异；仅修改阈值、标题、分类、通知群时保留原依赖，不在更新 payload 中补空值。
6. 创建或更新依赖后，使用 `GET /api/v1/sla_metric/get?name={name}` 回读，解析 `deps` 并核对集合与确认结果一致。回读失败或不一致时不能宣称配置完成，也不能盲目重试创建。

### 飞书通知群

创建/修改后引导用户选择自定义飞书通知群（`GET /feishu_group_chats`），不设置则使用业务域默认群。
- **前提：** 需先将「积加自助数据平台」机器人拉入群
- 通过 `POST /update` 的 `feishu_group_chats` 字段设置，传 `"null"` 恢复默认

### 操作红线

1. **禁止 DELETE** — 删除只能通过 dapp Web UI
2. **操作需确认** — 新增/修改前必须展示配置摘要并获得用户确认
3. **创建/修改后必须提示构建 Grafana** — 输出详情页链接 `https://dapp.addx.live/sla_metric?sub=metric&id={Id}&action=view`，提示用户点击「构建 Grafana 资源」
4. **凭据不落盘** — 严禁写入文件、环境变量文件或日志
5. **NocoDB 直接访问** — 仅限只读查询：`BI.sla_metric`、`BI.sla_metric_dev`、`BI.sla_metric_query_event`

### 监控维度

| 维度 | 说明 | 前提条件 |
|------|------|----------|
| 指标监控 | 主 SLA 指标阈值监控 | 必须配置 |
| 版本监控 | 按应用版本维度监控 | 数据集中需有版本字段 |
| 灰度监控 | 灰度版本偏离关系 | 需版本监控 + 飞书项目版本绑定 |
| 上游应用监控 | 上游应用版本影响 | 需配置上游应用绑定 |

## Examples

### Bad

```
# 未做权限校验就直接操作
用户: "帮我创建一个 SLA 指标"
Agent: 直接开始在 Superset 创建图表...
# ❌ 必须先完成 Superset + dapp API 双系统权限校验

# 图表命名不规范
create_chart(slice_name="daily_order_count", ...)
# ❌ 必须以 sla_metric_ 为前缀 → "sla_metric_daily_order_count"

# 图表类型不是折线图
create_chart(slice_name="sla_metric_order_count", viz_type="dist_bar", ...)
# ❌ dapp 仅支持 line chart，使用 bar/pie/table 等类型会导致 dapp 无法读取数据

# 图表缺少时间过滤器
create_chart(slice_name="sla_metric_order_count", adhoc_filters=[])
# ❌ adhoc_filters 必须包含 TEMPORAL_RANGE 过滤器

# metrics 使用裸列名，缺少聚合函数
create_chart(metrics=[{"expressionType": "SQL", "sqlExpression": "match_rate", ...}])
# ❌ 裸列名会导致 EXPRESSION_NOT_AGGREGATE 错误 → 必须用 "MAX(match_rate)" 或 "AVG(match_rate)"

# SQL 数据集只返回单日数据
create_dataset(sql="SELECT current_date as dt, ... WHERE dt = current_date")
# ❌ 时序图需要多日数据 → 必须返回 ≥15 天历史数据，让 TEMPORAL_RANGE 控制范围

# 尝试通过 API 删除指标
curl -X DELETE .../sla_metric/123
# ❌ 严禁通过 API 删除，必须去 dapp Web UI

# 没有发现上游依赖就照抄 API 默认值
Agent: Dagster 查询失败，先传 deps="[]" 创建，之后再补
# ❌ 发现失败必须停止创建；API 不会自动发现依赖

# 不确认就执行修改
Agent: 已为您修改了 SLA 指标阈值为 0.8
# ❌ 修改前必须展示配置摘要并获得用户确认
```

### Good

```
用户: "帮我创建一个每日订单增量的 SLA 指标"

Agent:
1. 验证 Superset 权限 → ✅
2. 生成 dapp API Token → ✅
3. 在 Superset 中创建图表 sla_metric_daily_order_count，获取 slice_id
4. 查询分类选项展示给用户选择（业务域、业务过程、应用域）
5. 用最终指标配置生成 SQL，匹配同区域、同环境的 Dagster 完整资产 key，校验上游分区粒度
6. 展示摘要（name、指标来源、阈值、分类、deps、粒度检查与排除原因），获得用户确认
7. 调用 POST /create，显式传递 JSON 序列化的 deps
8. 调用 GET /get 回读并核对依赖 → ✅
9. 引导选择飞书通知群（可选）
10. 输出详情页链接，提示构建 Grafana 资源
```

## References

- [SLA 配置操作流程](references/sla-config-flow.md)
- [dapp API 数据模型](references/dapp-api.md)
- [Superset 操作规范](references/superset-ops.md)
- [上游依赖发现与校验](references/upstream-dependencies.md)
- [SLA 指标配置文档](https://a4x-paas.feishu.cn/wiki/LKJaw2KVziiP2Kku3KpcRQy0nze)
- [SLA 告警配置帮助](https://a4x-paas.feishu.cn/wiki/PfFRwGgDCiyrbDkC6WSc1NFEndf)
- 源码: `/project/dbt/a4x_backend/api/sla_metric.py`（API 端点）
- 源码: `/project/dbt/utils/sla/metric.py`（SLAMetricModel）
