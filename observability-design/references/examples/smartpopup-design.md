# Worked Example：SmartPopup 可观测性设计

本文档是 `:design` 工作流的完整实战样例，来自 2026-04-12 SmartPopup 可观测性 session。展示从业务问题到 MR 落地的全流程 7 步。

## 项目背景

SmartPopup 是 customer-care 仓库的弹窗系统，目标：**在特定 scene（场景）触发时决策是否弹窗**，通过 recipe（方案）驱动配置。v3 升级后引入 AB 实验能力（GrowthBook）和 fatigue 频控（OncePerSession / MinInterval 等）。

PM 关心：弹窗是否驱动了工单提交率？是否保护了用户留存？是否带来负面抵触？
SRE 关心：后端健康度、外部依赖可用性、单用户是否被异常弹窗。

## 7 步执行

### Step 1 — 探索上下文

**操作：**

```bash
# 读 PRD / 架构
cat customer-care/docs/product/user-stories/smart-popup-admin.md
cat customer-care/docs/architecture/smart_popup/overview.md
cat customer-care/docs/architecture/smart_popup/domain-model.md

# grep 已有埋点 / OTel / Sentry
rg "snowplow|Snowplow|track\(" customer-care/app/
rg "otel|Counter|Histogram|Gauge" customer-care/internal/
rg "sentry|captureException" customer-care/internal/
```

**发现：**
- 已有 OTel SDK + 7 个 metric（HTTP + GrowthBook + Resolver + Version Gauge）→ 链路③ 部分就位
- 已有 SentryReporter adapter，但 main.go 未接入 → 链路② 设计就位、部署缺失
- App SDK 调用了 Snowplow 但 schema 未注册 → 链路① 数据源缺失
- 数仓侧无 dwm 宽表、GrowthBook metric 未 config-as-code、SLA 未配

**产出：** 现状摘要写入临时 working doc。

### Step 2 — 业务问题清单

通过苏格拉底追问：

**业务效果（PM / 运营）：**
1. 弹窗上线后，目标 scene 的工单提交率是否上升？
2. treatment 用户的 7 天 / 30 天设备留存是否受影响（正或负）？
3. 每个 scene 的展示率是否在合理区间？
4. 漏斗从 scene_entry 到 click_primary 的损耗在哪一步？
5. 用户是否高频点击"不再提醒"（never_remind rate）→ 弹窗在赶人？
6. Control 组 vs Treatment 组的跨组对比是否显著？

**系统稳定（SRE / oncall）：**
7. `getSmartPopupConfig` API 的 P95 latency 是多少？
8. GrowthBook / CMS / DB 三个依赖的可用性如何？是否降级？
9. rules_package 和 engine .evc 的版本号，运行时能否实时查到？
10. 单用户 5 分钟内最多被弹多少次（OncePerSession 是否失效）？

### Step 3 — 推导指标

**应用 AB 四层 + 安全护栏方法论**（详见 [ab-metric-taxonomy.md](../ab-metric-taxonomy.md) + [safety-guardrail-patterns.md](../safety-guardrail-patterns.md)）：

业务效果 12 个指标，详见 `customer-care/docs/architecture/smart_popup/observability.md §1.6`：

| 层 | 指标 | 回答问题 |
|----|------|---------|
| 北极星 近端 | Ticket Submission Rate | Q1 |
| 北极星 远端 | Device D7 Retention | Q2 |
| 北极星 远端 | Device D30 Retention | Q2 |
| 漏斗 | Condition Pass Rate | Q4 |
| 漏斗 | Fatigue Block Rate | Q4 |
| 漏斗 | Show Rate | Q3, Q4 |
| 交互 | Primary CTA Rate | Q1 |
| 交互 | Secondary CTA Rate | — |
| 交互 | Dismiss Rate | — |
| 交互 | Auto Dismiss Rate | — |
| 护栏 | Never Remind Rate | Q5 |
| 系统安全护栏 | 单用户 5min 弹窗次数 P99 | Q10 |

系统稳定 7 个 metric（链路③，已存在）：

| metric | 回答问题 |
|--------|---------|
| `smart_popup_http_request_duration_seconds` | Q7 |
| `smart_popup_growthbook_query_duration_seconds` | Q7, Q8 |
| `smart_popup_growthbook_query_errors_total` | Q8 |
| `smart_popup_resolver_recipes_returned` | Q8（=0 意味降级） |
| `smart_popup_rules_version` (gauge) | Q9 |
| `smart_popup_engine_version` (gauge) | Q9 |
| `smart_popup_http_requests_total` | Q7 |

Sentry 4 个错误事件（链路②）：

- `rules_load_failed`（对应 Q8 DB 降级）
- `growthbook_query_failed`（对应 Q8 GrowthBook 降级）
- `cms_fetch_failed`（对应 Q8 CMS 降级）
- `recipe_id_not_in_scene`（对应逻辑错误，metric 无法捕获）

### Step 4 — 推导链路

**应用三链路方法论**（详见 [three-pipeline-architecture.md](../three-pipeline-architecture.md)）：

#### 链路 ①（业务指标）

- **数据源：** App SmartPopup SDK 3 个 Snowplow 事件（`scene_entry` / `recipe_evaluation` / `popup_interaction`）+ customer-care MySQL smart_popup_* 维度表 + 已有 `dwd_ab_user_experiments_f` + `dws_device_active_stats_hi`
- **通道：** Snowplow（埋点）+ Dagster ETL（DB → 数仓）
- **存储：** dbt Athena/Iceberg，核心宽表 `dwm_smart_popup_funnel_hi` + 每日回填表 `dwm_smart_popup_funnel_backfill`
- **消费端：** GrowthBook config-as-code（5 metric）+ SLA dapp（4 指标）+ Superset（4 chart）

#### 链路 ②（错误）

- **数据源：** Go backend 4 类主动错误 + App SDK 异常
- **通道：** Sentry SDK（`internal/smart_popup/adapter/sentry_reporter.go`）
- **存储：** Sentry Issues
- **消费端：** Sentry → PagerDuty

#### 链路 ③（系统指标）

- **数据源：** Go backend OTel SDK（`internal/telemetry/`）
- **通道：** Prometheus exporter `/metrics` + ServiceMonitor 15s scrape
- **存储：** Prometheus TSDB
- **消费端：** Grafana `grafana/dashboards/smart-popup-overview.json`（4 行布局）+ PrometheusRule 4 条告警 → Alertmanager → PagerDuty

#### 链路交叉佐证表

| 故障场景 | 链路① | 链路② | 链路③ |
|---------|-------|-------|-------|
| GrowthBook 不可用 | 静默（无 recipes） | `growthbook_query_failed` | `growthbook_query_errors_total` ↑ |
| CMS 不可用 | 静默 | `cms_fetch_failed` | — |
| DB 规则加载失败 | 静默 | `rules_load_failed` | — |
| App 网络不可用 | 有缓存正常；无缓存静默 | — | — |

### Step 5 — 写设计文档

产出文档：`customer-care/docs/plans/2026-04-12-observability-data-pipeline-design.md`（业务指标链路详细设计）。

结构按 [design-doc-template.md](../design-doc-template.md) §"文档结构" 8 段：

1. 目标：端到端业务指标链路，SSOT = `dwm_smart_popup_funnel_hi`
2. 业务问题：从 PM / SRE 两侧列的 10 个问题
3. 指标定义：12 个 AB 指标 + 1 安全护栏（见 Step 3）
4. 数据链路：三链路 mermaid + 埋点 schema + 数仓分层（dwd → dwm）
5. 实施方案：
   - 新文件：dbt model、auto-parser config、GrowthBook metrics.yml、SLA SQL
   - 修改：`internal/telemetry/business_metrics.go`（版本 gauge）、`internal/smart_popup/handler`（埋点上下文）
   - 不改：已有 auto-parser 流程、已有 `dwd_ab_user_experiments_f`
6. 依赖：Tracker Manager schema 注册、dbt MR、SLA dapp 访问、Sentry project 创建
7. 验收：18 条 checkbox（launch-checklist §5 引用）
8. 降级：4 个降级场景 + 操作（launch-checklist §4 引用）

同时归档到 `customer-care/docs/architecture/smart_popup/observability.md`，作为长期维护 SSOT。

### Step 6 — 创 issue 群

委派 `gitlab-issue-sop`（见 [delegation-map.md](../delegation-map.md#gitlab-issue-sop)）。

**gap 列表（8 个）：**

| # | Gap | 链路 | 分类 | 优先级 |
|---|-----|------|------|-------|
| #15 | 审计与规划总 issue | — | 父 issue | p1 |
| #16 | device_sn 在 Snowplow contexts 未确认 | ① | 设计 gap | p1 |
| #17 | Prometheus + Grafana 部署 + 配 | ③ | 部署 gap | p0 |
| #18 | Sentry Backend + App 部署 | ② | 部署 gap | p0 |
| #19 | Tracker Manager 注册 3 个事件 schema + auto-parser | ① | 实施 gap | p0 |
| #20 | dwm_smart_popup_funnel_hi 宽表 + 回填 | ① | 实施 gap | p0 |
| #21 | GrowthBook 5 个 metric via config-as-code | ① | 实施 gap | p1 |
| #22 | SLA 4 个指标 + Superset 4 个 chart | ① | 实施 gap | p1 |

**标签规范：**
- `type::maintenance`
- `area/data` / `area/backend` / `area/infra` + `area/observability`
- `priority::p0` / `priority::p1`
- `status::ready`（设计完成可开工）

实施阶段通过 issue checklist 跟踪，不使用 lifecycle label。完整规则见 [GitLab Label 治理规范](../../../../docs/standards/gitlab-label-governance.md)。

产出：8 个 GitLab issue iid + URL 列表，回填到设计文档和 launch-checklist §5。

### Step 7 — 创 MR

两条 MR：

**customer-care !18**（backend 侧实施）
- `cmd/server/main.go`：接入 Sentry + OTel shutdown
- `internal/telemetry/business_metrics.go`：engine_version gauge observable callback
- `internal/smart_popup/logic/resolver.go`：recipes_returned histogram
- `k8s/api/base/servicemonitor.yaml`、`k8s/api/base/prometheusrule.yaml`
- `grafana/dashboards/smart-popup-overview.json`
- `growthbook/metrics.yml`（5 metric）+ CI job
- `docs/architecture/smart_popup/observability.md`
- `docs/deployment/launch-checklist.md` §5 §6

关联：`Closes #17 #18 #21`

**dbt !2988**（数仓侧实施）
- `models/dwm/dwm_smart_popup_funnel_hi.sql`
- `models/dwm/dwm_smart_popup_funnel_backfill.sql`
- `models/dwd/dwd_app_scene_entry_hi.yml`（auto-parser schema 声明）
- 同样对 `recipe_evaluation` / `popup_interaction` 的 dwd schema

关联：`Closes #19 #20`

委派 `gitlab-mr`，产出 MR URL + pipeline status。

## 产出映射

| Session 产物 | 文件路径 |
|------------|---------|
| 业务指标链路设计 | `customer-care/docs/plans/2026-04-12-observability-data-pipeline-design.md` |
| 实施计划 | `customer-care/docs/plans/2026-04-12-observability-data-pipeline-impl-plan.md` |
| 架构文档（SSOT） | `customer-care/docs/architecture/smart_popup/observability.md` |
| 上线 checklist | `customer-care/docs/deployment/launch-checklist.md` §5 §6 |
| Grafana dashboard 设计 | `customer-care/docs/plans/2026-04-11-grafana-dashboard-alerts-design.md` |
| Issues | customer-care #15-#22 |
| MRs | customer-care !18 + dbt !2988 |
| Grafana Dashboard JSON | `customer-care/grafana/dashboards/smart-popup-overview.json` |
| PrometheusRule | `customer-care/k8s/api/base/prometheusrule.yaml` |
| ServiceMonitor | `customer-care/k8s/api/base/servicemonitor.yaml` |
| GrowthBook config-as-code | `customer-care/growthbook/metrics.yml` |

## 从这个案例学到的要点

1. **业务问题必须先列再推指标**：本 session 开始时从"工单提交率"、"D7 留存"、"never_remind 率"、"单用户 5min 弹窗次数"这 4 个真实业务问题直接推出核心指标，否则容易变成"加 OTel 采一堆 metric 然后问要不要加"。
2. **近远端配对的真实价值**：SmartPopup v2 之前只看 CTA 点击率，容易误判"实验有效"，v3 加了 D7/D30 留存才能判断是否长期伤用户。
3. **安全护栏独立于业务指标**：OncePerSession fatigue 是硬约束。如果只看平均弹窗次数看不出问题，P99 才是那个"用户真实体验"的指标。
4. **链路② ③ 可以先行落地**：系统稳定链路（OTel + Sentry）优先级高于业务指标链路，因为它是"保护用户" 的最后一道防线。业务指标链路依赖数仓 + BI 通道，上线时间更长。
5. **父子 issue + 设计文档双链路跟踪**：#15 作为父 issue 跟踪整体进度，#16-#22 作为 sub-gap。launch-checklist §5 直接 checkbox 化每个 issue，发布时一眼看出阻塞项。
