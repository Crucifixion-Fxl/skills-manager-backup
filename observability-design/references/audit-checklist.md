# 观测性审计 Checklist

`:audit` 工作流的 5 步详解 + 每链路逐项 checklist。

## 5 步详解

### 步骤 1：列三链路现状

**目标：** 把当前系统在三链路的所有落地情况摸清楚，不漏项。

**操作：**

```bash
# 链路 ① 业务指标链路
# - grep 代码里的埋点调用
rg "snowplow|track\(|trackEvent|logEvent" <repo>

# - 查 Tracker Manager 注册了哪些事件 schema
# - 查数仓哪些 dwd/dwm 表已生成
# - 查 GrowthBook / Superset / SLA 平台哪些配置已落

# 链路 ② 错误链路
rg "sentry|captureException|CaptureErr|ReportError" <repo>
# - 查 Sentry 控制台 project 是否创建、是否有事件流入

# 链路 ③ 系统指标链路
rg "otel|prometheus|Counter|Histogram|Gauge" <repo>
ls k8s/**/servicemonitor.yaml k8s/**/prometheusrule.yaml
ls grafana/dashboards/*.json
# - 查 Prometheus targets 有没有 scrape 成功
# - 查 Grafana dashboard 是否存在 + 是否有人看（last viewed）
# - 查 PrometheusRule 是否部署 + Alertmanager 路由是否配
```

**产出：三链路现状矩阵**

| 链路 | 数据源 | 通道 | 存储 | 消费端 | 状态 |
|------|-------|------|------|-------|------|
| ① | ... | ... | ... | ... | 部分落地 |
| ② | ... | ... | ... | ... | 未落地 |
| ③ | ... | ... | ... | ... | 已落地 |

### 步骤 2：列已规划但未完成的事项

**目标：** 避免重复开 gap、避免漏掉已有计划。

**操作：**

```bash
ls <repo>/docs/plans/ | grep -i 'observ\|monitor'
gh issue list --label 'area/observability' --state open  # 或 GitLab 等价命令
gh mr list --label 'area/observability' --state open
```

检查：
- `docs/plans/` 里已有的观测性设计文档和实施计划
- 打开的 issue（特别是 `status::in-progress` / `flag/blocked`）
- 未合并的 MR（跨仓库：app / admin / dbt / infra）
- launch-checklist / ops-runbook 里的 pending 项

### 步骤 3：现状 vs 应有差距分析

**目标：** 对照方法论的 4 层衡量现状，产出 gap 表。

**衡量维度（按 SKILL.md §2-5 展开）：**

| 方法论章节 | 衡量点 |
|-----------|-------|
| §2 两大块拆分 | 业务效果 vs 系统稳定是否都有覆盖？ |
| §3 三链路架构 | 三条链路是否独立 + 完整（四要素齐备）？ |
| §4 AB 四层 | 如有 AB 实验，四层是否齐？近远端是否配对？ |
| §5 安全护栏 | 极值/频次/降级三种护栏是否覆盖？用的是 P99 还是平均？ |

**Gap 分类：**

| Gap 类型 | 含义 | 修复路径 |
|---------|------|---------|
| **设计 gap** | 根本没想到要做 | 写设计文档 + 开 issue |
| **实施 gap** | 设计了但没写代码 | 开 issue + MR |
| **部署 gap** | 代码写了但没部署 / 没配 | 开 issue（委派运维 skill） |
| **运营 gap** | 部署了但没人看 / 告警没路由 | 开 issue + 指派责任人 |

### 步骤 4：创 issue 跟踪每个 gap

委派 `gitlab-issue-sop`。每个 gap 一个 issue，label 规范：

```
type::maintenance
priority::p0 或 priority::p1 或 priority::p2
status::backlog 或 status::ready 或 status::in-progress
area/backend、area/data、area/infra、area/observability（按需多选）
flag/blocked（仅存在外部阻塞时）
```

完整命名和层级以 [GitLab Label 治理规范](../../../docs/standards/gitlab-label-governance.md) 为准；实施阶段写入 issue checklist，不使用 `lifecycle::*`。

issue 标题建议格式：`[<链路>] <gap 简述>`，如：
- `[链路①] Tracker Manager 注册 3 个事件 schema`
- `[链路②] Backend Sentry 部署`
- `[链路③] PrometheusRule ServiceEntrySurge 规则部署`

### 步骤 5：更新设计/架构/checklist

修订以下文档形成 SSOT：

- `docs/architecture/<system>/observability.md` — 加入新 gap 及其解决方案
- `docs/deployment/launch-checklist.md` — 在"可观测性部署清单"章节加勾选项
- `docs/plans/YYYY-MM-DD-<system>-observability-audit.md` — 新建一份审计快照

## 链路 ① 业务指标链路审计 checklist

### 数据源（埋点）

- [ ] 埋点事件 schema 已注册（Tracker Manager / Iglu / Segment schema registry）
- [ ] schema 版本号明确（vicohome 1-0-46 类似）
- [ ] App 端 SDK 触发点正确（entryEvent / user action / lifecycle hook）
- [ ] 关键字段覆盖：
  - [ ] `device_sn`（设备级关联）
  - [ ] `user_id`（用户级关联）
  - [ ] `session_id`
  - [ ] AB 分组字段（`experiment_id` / `variation_key`）
  - [ ] 版本字段（`rules_version` / `engine_version` / app build）
- [ ] Snowplow collector / Kafka topic 已配 + 数据流入

### 传输通道（ETL）

- [ ] Dagster / Airflow 任务已创建 + 跑通
- [ ] DB 同步粒度正确（全量 vs CDC vs scheduled partition）
- [ ] 同步频率满足业务时效（hourly / daily）

### 存储（数仓）

- [ ] dwd 明细表生成（auto-parser）
- [ ] dwm 宽表 JOIN 正确（按 SSOT 粒度一行一事件）
- [ ] backfill 任务跑通（D7/D30 回填）
- [ ] 数据质量校验（dbt test / Great Expectations）

### 消费端

- [ ] AB 平台（GrowthBook）metric 定义通过 config-as-code 同步，标 Official
- [ ] SLA 平台指标配好 + 阈值告警已触发测试
- [ ] BI（Superset）dashboard 存在 + 有人定期看（last viewed 记录）
- [ ] 告警路由到正确人群（业务效果 → PM/运营；系统稳定 → SRE）

## 链路 ② 错误链路审计 checklist

### 数据源

- [ ] Sentry SDK 初始化（backend `main.go` / app 启动处）
- [ ] DSN 通过 ExternalSecret / Vault 注入，不硬编码
- [ ] 4 类主动错误上报覆盖：
  - [ ] 配置/规则加载失败
  - [ ] 外部依赖调用失败（API / DB / CDN）
  - [ ] 下游资源不可用
  - [ ] 业务逻辑约束违反

### 传输通道

- [ ] Sentry release tracking（部署时上报 release 版本，关联 commit SHA）
- [ ] Source map / DSYM 上传（前端 / iOS）
- [ ] 采样率合理（production 不能 100% 除非量小）

### 存储

- [ ] Sentry project 在 staging + production 都已创建
- [ ] 项目命名规范（如 `<service>-backend`、`<service>-app-android`）
- [ ] retention 期限符合合规要求

### 消费端

- [ ] Alert 规则配置（First Seen / Spike / Regression）
- [ ] PagerDuty 集成 + routing key 正确
- [ ] oncall 轮值表已配
- [ ] 每周 / 每月 issue 审查会议（减少僵尸 issue）

## 链路 ③ 系统指标链路审计 checklist

### 数据源（OTel instrumentation）

- [ ] MeterProvider 初始化（`cmd/server/main.go` 或等价入口）
- [ ] HTTP middleware 覆盖（request count + duration histogram）
- [ ] 业务层关键操作 instrumented（外部调用 / 核心业务逻辑）
- [ ] 版本 / 健康 Gauge（`<service>_rules_version` / `<service>_build_info`）
- [ ] metric 命名遵循规约（前缀统一 + 单位后缀）
- [ ] labels 低基数（≤ 20 种值）

### 传输通道

- [ ] Prometheus exporter `/metrics` 端点暴露
- [ ] ServiceMonitor CRD 在 K8s 生效
- [ ] scrape interval 合理（通常 15s）
- [ ] Prometheus targets UP = 1

### 存储

- [ ] Prometheus TSDB retention 足够（本地 15d + Thanos/Mimir 长期）
- [ ] 多集群场景下 Prometheus federation 已配

### 消费端

- [ ] Grafana dashboard JSON 存 git（`grafana/dashboards/<service>.json`）
- [ ] Dashboard 导入 + 数据源变量（`$datasource` / `$namespace`）可切换
- [ ] PrometheusRule 覆盖：
  - [ ] 流量突增 / 骤降
  - [ ] 错误率超阈值
  - [ ] 延迟 P95/P99 超阈值
  - [ ] 关键 metric 降级触发（等于 0 / null 占比）
  - [ ] 安全护栏（极值 / 频次）
- [ ] Alertmanager 路由 label（如 `team: smart-popup`）到 PagerDuty
- [ ] 模拟告警触发 → PagerDuty 收到（end-to-end 验证）

## Gap 分类示例

### 设计 gap

**特征：** 方法论章节没覆盖，团队根本没意识到要做。

例：
- 只有业务指标，没设计安全护栏（§5 方法论缺失）
- AB 实验只定义了近端北极星，没有远端（§4 方法论缺失）
- 三链路只做了链路③（业务效果和错误监控没做）

### 实施 gap

**特征：** 设计文档里写了，但代码没写。

例：
- 设计文档里列了 `single_user_5min_popup_p99` 但 SLA 平台上没配
- `getPersonalizationEngine` 的 engine_version 应该有 gauge callback，但代码里是 TODO 占位

### 部署 gap

**特征：** 代码写了，但没部署/没配。

例：
- OTel 代码已合入 master，但 K8s ServiceMonitor YAML 还没 argocd sync
- Grafana dashboard JSON 已提交仓库，但没导入到 Grafana 实例
- Sentry project 已创建，但 DSN ExternalSecret 没注入 pod env

### 运营 gap

**特征：** 部署完了，但实际没用起来。

例：
- Dashboard 存在但 6 个月没人看（`last viewed` 过期）
- Alert 规则 firing 但 PagerDuty 路由到离职员工
- Sentry issue 堆积 500 个没人处理（噪音告警被静音）

## 实例：SmartPopup 审计 gap 表

来自 [customer-care launch-checklist.md §5](../../customer-care/docs/deployment/launch-checklist.md) + issue #15-22 的映射。

| # | Gap | 链路 | 分类 | issue |
|---|-----|------|------|-------|
| 1 | device_sn 是否在 Snowplow contexts 里没确认 | ① | 设计 gap | #16 |
| 2 | Backend Sentry 未部署到 6 个环境 | ② | 部署 gap | #18 |
| 3 | PrometheusRule + Grafana 未 ArgoCD sync | ③ | 部署 gap | #17 |
| 4 | Tracker Manager 3 个事件 schema 未注册 | ① | 实施 gap | #19 |
| 5 | `dwm_smart_popup_funnel_hi` 宽表未建（dbt MR 待合并） | ① | 实施 gap | #20 |
| 6 | GrowthBook 5 个 metric config-as-code 未同步 | ① | 实施 gap | #21 |
| 7 | SLA 4 个指标 + Superset 4 个 chart 未配 | ① | 实施 gap | #22 |
| 8 | `rules_version` gauge observable callback 未接 | ③ | 实施 gap | 合并到 #17 |
| 9 | `engine_version` gauge observable callback 未接 | ③ | 实施 gap | 合并到 #17 |

全流程审计示例见 [examples/smartpopup-audit.md](examples/smartpopup-audit.md)。
