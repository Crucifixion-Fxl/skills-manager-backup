# Worked Example：SmartPopup 中期观测性审计

`:audit` 工作流的实战样例，来自 2026-04-12 SmartPopup session 中期的现状审计。展示"代码部分已实现、部分未实现"状态下，如何系统性地找出 gap 并推动闭环。

## 审计背景

SmartPopup v3 开发到中期：
- OTel SDK + 7 个 metric 代码已写完，提交到 `11-smartpopup-grafana-dashboard-alerts` 分支
- SentryReporter adapter 类已实现但 `main.go` 未接入
- Snowplow 埋点在 SDK 代码里已调用
- 数仓侧的 dwm 宽表、GrowthBook metric 都还没做
- 没有统一的 "observability 部署 checklist"

这时候要回答：**我们要上线了，观测性准备得怎么样？哪些 gap 必须上线前补？**

## 5 步执行

### Step 1 — 列三链路现状

```bash
# 链路 ① 业务指标链路
rg "snowplow|track\(" customer-care/app/smart_popup_sdk/
# 发现：SDK 里有 scene_entry / recipe_evaluation / popup_interaction 调用

# 查 Tracker Manager 平台
# 结果：3 个事件 schema 未注册，无法通过审核

# 查数仓
ls dbt/models/dwm/ | grep smart_popup
# 结果：无 smart_popup 相关 dwm 表

# 查 GrowthBook
ls customer-care/growthbook/
# 结果：已有 metrics.yml，但里面没有 SmartPopup 指标

# 链路 ② 错误链路
rg "sentry|SentryReporter" customer-care/internal/
# 发现：adapter 类 SentryReporter 已实现，但 main.go 没有初始化它

# 查 Sentry 控制台：smart-popup-backend project 未创建
# 查 Vault：SENTRY_DSN 未配

# 链路 ③ 系统指标链路
rg "otel|Counter|Histogram|Gauge" customer-care/internal/telemetry/
# 发现：7 个 metric 都已 instrument
# 发现：rules_version gauge 已接 callback，engine_version gauge 是 TODO 占位

ls customer-care/k8s/api/base/
# 结果：servicemonitor.yaml 和 prometheusrule.yaml 已存在但未 ArgoCD sync

ls customer-care/grafana/dashboards/
# 结果：smart-popup-overview.json 已存在，但没导入到 Grafana 实例
```

**现状矩阵（产出）：**

| 链路 | 数据源 | 通道 | 存储 | 消费端 | 状态 |
|------|-------|------|------|-------|------|
| ① 业务指标 | SDK 已有埋点，schema 未注册 | Snowplow（未启用） | 无 dwm 表 | GrowthBook/SLA/Superset 全未配 | 10% |
| ② 错误 | Adapter 已写，未接 main.go | Sentry SDK 未启用 | Sentry project 未建 | PagerDuty 路由未配 | 20% |
| ③ 系统指标 | OTel 7 metric 已 instrument（engine_version 是 TODO） | Prometheus scrape 就绪（未 sync） | TSDB | Grafana JSON 已写（未导入）、PrometheusRule 4 条已写（未 sync） | 80% |

### Step 2 — 列已规划但未完成的事项

```bash
ls customer-care/docs/plans/ | grep -i 'observ\|grafana\|sentry'
# 结果：
# - 2026-04-11-grafana-dashboard-alerts-design.md（设计文档）
# - 2026-04-11-grafana-dashboard-alerts-impl-plan.md（实施计划）

gh issue list --label 'area/observability'
# 结果：只有 #11（Grafana dashboard + alerts），已 merge
```

**结论：** 只有链路③ 的设计和实施计划，链路① 和链路② 的设计都缺失。

### Step 3 — 现状 vs 应有差距分析

对照方法论 §2-5 衡量：

| 方法论章节 | 衡量点 | 现状 | gap |
|-----------|-------|------|----|
| §2 两大块拆分 | 业务效果 vs 系统稳定是否都覆盖？ | 只有系统稳定（链路③） | 业务效果缺失 |
| §3 三链路 | 三条独立链路完整否？ | 链路③ 80%，链路② 20%，链路① 10% | 2 条链路大量 gap |
| §4 AB 四层 | 近远端、漏斗、交互、护栏是否齐？ | 完全没有 AB 指标 | 链路①产出 12 个指标 |
| §5 安全护栏 | 极值/频次/降级是否覆盖？ | 只有系统侧 OTel，没有业务侧 P99 护栏 | 单用户 5min P99 指标缺失 |

**Gap 表（9 项）：**

| # | Gap | 链路 | 分类 | 优先级 |
|---|-----|------|------|-------|
| 1 | device_sn 是否在 Snowplow contexts 里未确认 | ① | 设计 gap | p1 |
| 2 | Backend Sentry 未部署到 6 个环境（ArgoCD sync + SENTRY_DSN ExternalSecret） | ② | 部署 gap | p0 |
| 3 | App 端 Sentry 注入未确认（宿主 App g0-android/g0-ios） | ② | 部署 gap | p1 |
| 4 | PrometheusRule + ServiceMonitor 未 ArgoCD sync | ③ | 部署 gap | p0 |
| 5 | Grafana Dashboard 未导入 Grafana 实例 | ③ | 部署 gap | p0 |
| 6 | Alertmanager 路由 `team: smart-popup` → PagerDuty 未配 | ③ | 部署 gap | p0 |
| 7 | Tracker Manager 3 个事件 schema 未注册 + auto-parser 未生成 dwd 表 | ① | 实施 gap | p0 |
| 8 | `dwm_smart_popup_funnel_hi` 宽表 + D7/D30 backfill 未建 | ① | 实施 gap | p0 |
| 9 | GrowthBook 5 个 metric via config-as-code 未同步 | ① | 实施 gap | p1 |
| 10 | SLA 4 个指标（含安全护栏 5min P99）+ Superset 4 个 chart 未配 | ① | 实施 gap | p1 |
| 11 | `engine_version` gauge observable callback 未接（TODO 占位） | ③ | 实施 gap | p1 |
| 12 | `rules_version` gauge 有 callback 但没写测试 | ③ | 实施 gap | p2 |

### Step 4 — 创 issue 跟踪每个 gap

委派 `gitlab-issue-sop`。

**策略：** 把 gap 按链路合并成 7 个 issue（避免 issue 泛滥）：

| issue | 合并 gap | label |
|-------|---------|-------|
| #15 | 父 issue（总体规划） | `type::maintenance` `priority::p1` `status::backlog` `area/observability` |
| #16 | Gap 1（device_sn 确认） | `type::maintenance` `area/data` `area/observability` `priority::p1` `status::ready` |
| #17 | Gap 4 + 5 + 6 + 11 + 12（链路③ 部署 + 补完） | `type::maintenance` `area/infra` `area/observability` `priority::p0` `status::ready` |
| #18 | Gap 2 + 3（链路② 部署） | `type::maintenance` `area/backend` `area/observability` `priority::p0` `status::ready` |
| #19 | Gap 7（Tracker Manager + auto-parser） | `type::maintenance` `area/data` `area/observability` `priority::p0` `status::ready` `flag/blocked`（等 Tracker Manager 审核） |
| #20 | Gap 8（dwm 宽表 + backfill） | `type::maintenance` `area/data` `area/observability` `priority::p0` `status::in-progress`（MR dbt!2988 已发） |
| #21 | Gap 9（GrowthBook config-as-code） | `type::maintenance` `area/data` `area/observability` `priority::p1` `status::ready` |
| #22 | Gap 10（SLA + Superset） | `type::maintenance` `area/data` `area/observability` `priority::p1` `status::ready` |

**linked items**：
- #15 relates_to #16 #17 #18 #19 #20 #21 #22（父子关系在 CE 用 relates_to 表达）
- #20 relates_to #22（宽表先就位才能配 SLA）

### Step 5 — 更新设计/架构/checklist

**更新 `customer-care/docs/architecture/smart_popup/observability.md`**：
- §"链路总览" mermaid 图（加入 dwm_smart_popup_funnel_hi 节点）
- §"链路①" 新增 1.5 数仓建模章节
- §"链路②" 加入 4 个 Sentry error event 表格
- §"链路③" 补齐 OTel 7 个 metric 完整列表

**更新 `customer-care/docs/deployment/launch-checklist.md`**（新建 §5 §6 章节）：

```markdown
## 5. 可观测性部署清单

详见 [observability.md](../architecture/smart_popup/observability.md) 三条链路设计。

### 5.1 链路 ② Sentry（错误监控）— issue #18
- [ ] Backend Sentry 部署（合入 master + ArgoCD 同步）
- [ ] 验证 SENTRY_DSN ExternalSecret 在 6 个环境注入成功
- [ ] staging 触发模拟错误，确认 Sentry project 收到 event
- [ ] 配置 Sentry → PagerDuty 告警路由
- [ ] App 端 Sentry 注入确认（宿主 App g0-android/g0-ios）

### 5.2 链路 ③ Prometheus + Grafana（系统指标）— issue #17
- [ ] Backend OTel 部署（合入 master + ArgoCD 同步）
- [ ] ServiceMonitor + PrometheusRule 在 K8s 生效
- [ ] Prometheus 能 scrape `/metrics`
- [ ] Grafana Dashboard 导入（建议 CI Job 自动化）
- [ ] Alertmanager 路由 `team: smart-popup` → PagerDuty
- [ ] 模拟告警触发 → PagerDuty 收到通知

### 5.3 链路 ① 业务指标链路（数仓）— issues #19 #20 #21 #22
...

### 5.4 device_sn 关联确认 — issue #16
- [ ] 确认 SmartPopup 事件的 Snowplow contexts 中包含 device_sn
- [ ] 如未包含，决定改动方案：EventDispatcher 显式传 vs App 层注入 context

## 6. 上线后监控（前 48h）
...
```

**新建 `customer-care/docs/plans/2026-04-12-observability-data-pipeline-design.md`**（把 Step 3 gap 分析的解决方案落成设计文档）。

## 产出映射

| 产物 | 文件 / URL |
|------|----------|
| 审计快照（临时）| session notes（未独立归档） |
| 架构 SSOT 更新 | `customer-care/docs/architecture/smart_popup/observability.md` |
| Launch checklist 更新 | `customer-care/docs/deployment/launch-checklist.md` §5 §6 |
| 新设计文档 | `customer-care/docs/plans/2026-04-12-observability-data-pipeline-design.md` |
| Issues | customer-care #15-#22 |
| 跨仓库 MR | dbt!2988 |

## 从这个 audit 案例学到的要点

1. **审计不是"有没有"，是"有没有用"**：链路③ 的 80% 其实只是"代码写了"，真正用起来（ArgoCD sync + Grafana 导入 + PagerDuty 路由）只有 20%。一定要算到部署 + 运营 gap。
2. **Gap 分 4 类很重要**：
   - 设计 gap（device_sn 根本没想过） → 先要讨论和决策
   - 实施 gap（代码没写） → 直接 MR
   - 部署 gap（代码写了但没 sync） → 运维 skill 承接
   - 运营 gap（部署了没人看） → 需要指派责任人 + 定期 review
3. **gap 合并成 issue，而非 1 对 1**：Gap 4+5+6+11+12 都是链路③ 的收尾，合并到 #17 一个 issue 里用 checklist 跟踪更清楚。
4. **launch-checklist 是 audit 闭环的关键产物**：审计结论直接 checkbox 化到 launch-checklist §5，发布时一眼看出阻塞项。否则 gap 列表只是一次性产出，过两周就被遗忘。
5. **跨仓库 MR 的依赖顺序要明确**：#19（Tracker Manager）是 #20（dwm 宽表）的前置条件，因为 dwd 表要通过 schema 注册后才能通过 auto-parser 生成。这种顺序必须在 audit 时识别出来，否则实施阶段会阻塞。
