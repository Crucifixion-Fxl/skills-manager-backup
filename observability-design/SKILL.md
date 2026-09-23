---
name: observability-design
description: Use when designing observability, experiment metrics, monitoring audits, tracking plans, or gap analysis and the work must start from explicit business goals and decision questions before deriving metrics, metric definitions, collection plans, pipelines, alerts, dashboards, and issue tracking. Also use when existing monitoring feels tool-driven, metric-first, or disconnected from product decisions and user impact. Also use when designing metric config-as-code across multiple BI/experiment tools (GrowthBook + Superset + SLA + Grafana), enforcing cross-tool metric parity, preventing UI drift for BI configuration, or integrating AI review for metric consistency.
---

# observability-design

可观测性设计方法论 Skill — 从**业务目标和决策动作倒推**，产出完整的观测方案 + issue 跟踪 + 实施委派。强制顺序必须是：**业务目标 -> 决策问题 -> 指标 -> 数据模型/口径 -> 采集 -> 数据链路 -> 消费端/告警 -> Current / Target / Gap / Tracking -> issue**，禁止跳步或反向写作。

## Description

**为什么需要这个 skill：** 观测性做歪的最常见原因是"能采什么就采什么"——埋了一堆指标，但回答不出"这个功能上线有效果吗？""这次故障用户受了多少伤？""接下来应该停实验、调阈值还是找 oncall？" 这类决策问题。本 skill 强制先写业务目标和决策问题，再允许进入指标、数据模型/口径、采集、链路、现状/目标/差距/跟踪和 issue 设计，让观测方案对决策负责。

**适用项目状态：**
- 新功能/新系统还没上线 → `:design` 入口，从零设计
- 已上线但观测缺失或散乱 → `:audit` 入口，审计 + 补洞
- AB 实验启动前 → `:design` 入口聚焦 AB 四层指标

**不在范围：**
- 埋点工具本身的配置（委派 `tracker-manager`、`sentry-onboarding`）
- Dashboard / 告警平台的具体操作（委派 `grafana`、`sla-metric`、`superset`、`growthbook`、`prometheus`）
- issue 管理（委派 `gitlab-issue-sop`）、MR 创建（委派 `gitlab-mr`）
- AI metric review job 实现 → 见 `scripts/metrics-review.gitlab-ci.yml`

## 触发场景

| 场景 | 用户说法 | 入口 |
|------|---------|------|
| 新功能/新系统从零设计观测 | "给新功能 X 设计可观测性"、"X 要怎么监控"、"observability design for X" | `:design` |
| AB 实验专项指标设计 | "AB 实验怎么观测"、"实验指标怎么定"、"experiment metrics" | `:design`（聚焦 AB 章节） |
| 已上线系统审计 | "审计 Y 的可观测性"、"看看 Y 监控全不全"、"observability audit"、"监控 gap" | `:audit` |

## 职责边界

方法论自己做、工具链委派（详见 [delegation-map.md](references/delegation-map.md)）：

| 职责 | 自己做 | 复用 SOP（无交互） | 委派交互式工作流 |
|------|-------|------------------|----------------|
| 方法论引导（问题→指标→数据→链路） | ✅ | — | — |
| 审计现有代码/文档/issue/MR | ✅ | — | — |
| 写设计文档 + 架构文档 + checklist | ✅ | — | — |
| 创 GitLab issues | — | `gitlab-issue-sop` | — |
| 创 MR | — | `gitlab-mr` | — |
| 配 Grafana dashboard | — | — | `grafana` |
| 配 SLA 指标（dapp） | — | — | `sla-metric` |
| 配 GrowthBook metric | — | — | `growthbook` |
| 配 Superset chart / dashboard | — | — | `superset` |
| 创 Sentry project | — | — | `sentry-onboarding` |
| 注册埋点 schema | — | — | `tracker-manager` |
| 本地契约落地（scrape/alert/dashboard verify.sh） | — | — | `prom-grafana-dev` |
| metric config diff review（CI-sync 路径：YAML parity + 口径 SQL；Skill-driven 路径：docs 设计意图一致性）| — | — | `code-review`（以本 skill 第 6 节 + [metrics-config-as-code.md](references/metrics-config-as-code.md) D4 / D4' 为规范）|

## 强制工作流

所有 `:design` 和 `:audit` 输出都必须按以下顺序展开，后一步不得早于前一步：

1. **业务目标** - 明确这套观测最终要保护或提升什么业务结果，写清成功/失败分别意味着什么
2. **决策问题** - 明确要支持哪些产品 / 运营 / oncall 决策，问题必须可回答、可追踪、可行动
3. **指标定义** - 每个指标必须回链到至少一个决策问题，说明公式、分母、维度、阈值方向
4. **数据模型 / 口径** - 先定义粒度、窗口、分母、切片维度和回填规则，再谈采集
5. **采集设计** - 明确需要哪些事件、字段、上下文、配置维度；每个采集项都必须服务某个指标或故障定位问题
6. **数据链路** - 再说明数据源、通道、存储、消费端和告警路径
7. **消费端 / 告警** - 说明谁看、何时看、阈值是什么、异常时谁被叫醒
7.5 **本地契约化** — 把本步产出的 scrape config / alert rules / dashboard PromQL 写成本地可执行契约（`make test-observability-local` 样板），加入 CI。Target 不再是"配了 dashboard"而是"本地回归测试绿 + CI 里跑绿"。委派 `prom-grafana-dev` skill 落地。
8. **Current / Target / Gap / Tracking** - 区分当前状态、目标状态、差距和对应 issue
9. **Issue 跟踪** - 把每个 gap 连接到已存在 issue；没有 issue 的 gap 才新建 issue
10. **Dashboard + Metric as-Code 回归守护** — dashboard JSON / alert rules YAML / 业务指标 YAML（GrowthBook/Superset/SLA...）必须进 Git。CI 每次 MR 跑：
    - Dashboard 层：`verify.sh` 4 类断言（scrape/alert/provisioning/panel-promql round-trip）
    - Metric 层：set parity 双向 diff + AI 一致性 review（include [scripts/metrics-review.gitlab-ci.yml](scripts/metrics-review.gitlab-ci.yml)）
    禁止"手动在 BI UI 改指标**不走任何治理**"——改动必须：(a) CI-sync 路径 → 先改 YAML → sync 下发 → UI 验证；或 (b) Skill-driven 路径 → 先改设计意图 docs（MR review）→ 跑 skill register → UI 验证。(Metric-as-Code 详见第 6 节 + [metrics-config-as-code.md](references/metrics-config-as-code.md))

**硬规则：**
- 不能先写指标再补业务目标或决策问题
- 不能先写采集再补数据模型 / 口径
- 不能先写工具再补链路
- 不能把目标态写成现状
- 不能把 gap 混进实施步骤而不标记 tracking
- 不能让文档停留在“有方案但没人跟进”的状态

## 方法论核心

### 1. 结果导向推导

**不要"能采什么就采什么"。**从业务问题倒推：

```
业务问题 → 监控指标 → 所需数据 → 采集端
```

**落地规则：** 每个指标必须能回答一个具体的业务/运维问题，否则不采。每次新增指标，先写下它回答的问题。

示例（SmartPopup）：
- 问题："弹窗有没有真的提高工单提交率？" → 指标 `click_primary / scene_entry` per variation → 数据 `popup_interaction.action` + `scene_entry` 埋点 → 采集端 App SDK
- 问题："单用户 5 分钟内最多被弹多少次？" → 指标 `single_user_5min_popup_p99` → 数据 `popup_interaction` 按 user_id + 时间窗 → 采集端同上

### 2. 两大块拆分

业务效果和系统稳定性是**两种时效、两批人、两种数据链路**，混在一起会导致实时告警淹没业务洞察、业务报表延迟拖累故障排查。

| 维度 | 关注者 | 时效 | 数据特征 | 典型消费端 |
|------|--------|------|---------|---------|
| **业务效果** | PM / 数据 / 运营 | 延迟（小时-天-周） | 用户行为、漏斗、留存、转化 | BI (Superset/Metabase)、AB 平台 (GrowthBook)、SLA |
| **系统稳定性** | SRE / oncall / 发布负责人 | 实时（秒-分钟） | API 健康、错误、依赖可用性、版本 gauge | Prometheus / Grafana / Sentry / PagerDuty |

设计时先分两栏，每栏独立推导。

### 3. 三链路架构

两大块铺开后落到三条独立链路（详见 [three-pipeline-architecture.md](references/three-pipeline-architecture.md)）：

```
① 业务指标链路：业务事件埋点 + 配置/维度数据 → 数仓(批/实时) → BI / AB / SLA 消费
② 错误链路：    前后端异常 → 错误聚合服务 (Sentry) → 告警通知 (PagerDuty)
③ 系统指标链路：服务运行时指标 → 时序数据库 (Prometheus) → Dashboard (Grafana) + 告警
```

**每条链路四要素**：`数据源 → 传输通道 → 存储 → 消费端`。

**链路独立但有交叉佐证**：例如 GrowthBook 不可用时，链路① 静默（无埋点），链路② 必须有 Sentry event，链路③ 必须有 metric spike。三条链路互相反向校验，任一缺失就是一个 gap。

### 4. AB 实验指标四层分类

### Metric Layer 分类（7 层完整体系）

所有业务/系统 metric 必须声明一个 `layer`。7 层：`near-star`、`far-star`、`funnel`、`interaction`、`guardrail`、`diagnostic`、`system`。每层决定了 AB 平台位置、阈值方向、dashboard 布局、alert 路由。完整定义 + 创建新 metric 的决策流程见 [metric-layer-taxonomy.md](references/metric-layer-taxonomy.md)。

**常混淆**：Metric Layer（业务决策分类）≠ Metric Type（技术聚合模式 proportion/ratio/mean/...）。一个 metric 必须同时声明两者。

### AB 实验场景（Metric Layer 的子集）

AB 实验指标按"时效 × 用途"分四层（详见 [ab-metric-taxonomy.md](references/ab-metric-taxonomy.md)）：

| 层 | 用途 | 时效 | 对比方式 | 必要性 |
|---|------|------|---------|-------|
| **北极星 - 近端** | 即时反馈，实验是否驱动了直接行为 | 实验当日可看 | 跨组 | ✅ |
| **北极星 - 远端** | 终极业务结果，实验是否带来真实价值 | 7-30 天后回填 | 跨组 | ✅ |
| **漏斗效率** | 诊断 treatment 组内部各步骤损耗 | 实验当日 | 组内（treatment） | ✅ |
| **交互质量** | 内容/时机质量评估 | 实验当日 | 组内（shown=true 为分母） | 推荐 |
| **护栏** | 防止短期收益伤长期 | 实验全程 | 跨组 | ✅ |

**近远端配对原则**：近端涨了不代表远端涨。只看近端 → 局部最优陷阱（例：CTA 率涨了但 D7 留存跌 = 用户在被骚扰）。必须两端都上。

### 5. 系统安全护栏

业务指标好看 ≠ 用户没受伤。业务指标之外必须有"系统出错保护用户"的护栏指标（详见 [safety-guardrail-patterns.md](references/safety-guardrail-patterns.md)）：

| 模式 | 检测什么 | 计算方式 | 为什么不用平均值 |
|------|---------|---------|----------------|
| **极值检测** | 单用户/单实体被异常对待 | P99 / Max / Top-N | 极端 case 被 1000 个正常 case 稀释，平均值永远正常 |
| **频次上限** | 单位时间内重复行为爆发 | 滑动窗口内 COUNT | 需要看瞬时峰值而非累计 |
| **降级触发** | 服务降级是否在发生 | 关键 metric 等于 0/null 的占比 | 降级本来就是 silent，平均/总量看不出 |

示例（SmartPopup）：单用户 5min 弹窗次数 P99 > 2 警告、P99 > 5 critical。正常值 = 1（OncePerSession fatigue 保证），P99 > 1 说明频控系统层面失效。

### 6. 指标配置即代码（Config-as-Code）

业务指标、口径 SQL、虚拟数据集定义**必须受治理**——要么 Git 是 SSOT（CI-sync 路径，如 GrowthBook），要么工具自己是 SSOT + Git 只放设计意图 docs（Skill-driven 路径，如 Superset）。**禁止"UI 里直接 free-form 建"不走任何 governance**，否则口径漂移 = 业务决策失真。

**通用核心决策**（两条路径都适用，详见 [metrics-config-as-code.md](references/metrics-config-as-code.md)）：

- **D1 范围边界**：稳定口径（dataset schema、filter SQL、metric 公式）入 code；展示层（chart、dashboard 布局、viz 参数）留 UI。按变化频率划线，把最多变的那层留给 UI 自由发挥。
- **D2 消费者数量决定共享策略**：消费者 ≤ 2（如 GrowthBook + SLA）→ 各自表达；消费者 ≥ 3 → 才考虑 dbt Semantic Layer 等共享语义层。早抽象 = 维护三处 + 增加 adapter bug 面积。
- **D3 SSOT 定位选择（根本分水岭）**：两条路径二选一，不能混搭——
  - **CI-sync / Git-SSOT**：Git YAML 是唯一真相，CI 监听 YAML 变更调工具 API 同步。适合口径错代价高、变更低频、多消费者需 parity 的场景。典型：**GrowthBook**。
  - **Skill-driven / Tool-native SSOT**：工具自己 state 是唯一真相，Git 只有 human-readable 设计意图 docs，skill 读 docs 按需注册。适合变更频繁、独占消费者、需要灵活迭代的场景。典型：**Superset dataset**。
  - 混合 = two-state 漂移（YAML 和 tool state 谁对不清）。**选定一条路径后坚持它**。
- **D4 一致性审查**：Git-SSOT 路径用 **Set parity 强规约**（deterministic lint，缺一方 block MR）；Skill-driven 路径用 **设计意图 docs review**（docs 改动走 MR review，实施靠 skill session 对话 review）。
- **D5 UI drift lock（通用）**：工具必须有 externally-managed 机制（Superset `is_managed_externally=true` / GrowthBook "Official" / Grafana provisioning）。`external_url` 指回 git blob（CI-sync path → YAML blob；Skill-driven path → docs blob，带 commit sha）。
- **D6 环境切换（通用）**：运行时 native 模板（Superset Jinja `filter_values('env')`）在工具侧处理，sync 时 / skill register 时**不要 substitute**——避免 staging/prod YAML 分叉。

**路径专属实施细节**（按 D3 选到的路径看对应章节）：

- **CI-sync 路径**：CI trigger 拓扑（分支 × 实例矩阵 / 单实例无缓冲警示）、Service user + Vault（`<project>_sync_bot` 专账号 + `secret/<service>/<env>/<category>` 路径）、CI AI review（advisory job，[scripts/metrics-review.gitlab-ci.yml](scripts/metrics-review.gitlab-ci.yml) 可 include）
- **Skill-driven 路径**：设计意图 docs 的 Metric Catalog 章节作为 canonical 描述 + `/<tool>` skill 读取协议（register 前先 diff tool state vs docs 设计态 + user 确认 + 打 externally_managed + 记 session transcript）+ 定期漂移检测

详见 [metrics-config-as-code.md](references/metrics-config-as-code.md) 里"CI-sync 路径实施细节" 和 "Skill-driven 路径实施细节" 两个 subsection。

**Monorepo 目录约定**：BI config-as-code 所有工具（grafana / growthbook / superset / sla ...）统一放 `observability/` 伞下；`scripts/observability/` 留 scripts（本地 dev tooling），`k8s/**/prometheusrule.yaml` 留 k8s（K8s CRD）。详见 [metrics-config-as-code.md](references/metrics-config-as-code.md)。

**参考实现（canonical）**：customer-care 仓
- `growthbook/metrics.yml` + `sync.mjs` + `sync-lib.mjs` — CI-sync 路径已 merge，包含顶层 `factTableFilters` 复用注册表 + `proportion` vs `ratio` 正确使用（D7/D30 留存必须 ratio，分母 `IS NOT NULL` 排除未到期样本）
- `docs/architecture/smart_popup/observability.html §8.6 + §8.7` — "指标定义 → 消费端接入"分层写法（§8.6 metric catalog / §8.7 per-consumer 映射 GrowthBook/SLA/Superset）；对应本 skill `design-doc-template.md §6.5` crosswalk 模板
- `observability/growthbook/` + `observability/superset/` 伞目录结构 + `scripts/lint-metrics-parity.mjs` set-parity lint + 配套 Superset config-as-code —— **尚未 merge**（GitLab MR !222/!225 路上），引用时请到 MR 核对，master 上目前仍是根目录 `growthbook/` 的单消费者形态。

## 执行流程

两条工作流入口：`:design`（新功能/新系统/AB 实验从零设计）、`:audit`（现有系统审计 + 补洞）。

### `:design` 入口（11 步）

1. **探索上下文** — 读 README、docs/product/、docs/architecture/、PRD；grep 现有埋点和 metrics；**并调 `/datahub-schema-search` 盘点 ads / dwm / dwd 已有表**（避免重复建宽表；数仓分层和命名规范由 DataHub 反映）；把当前实现状态摸清楚
2. **业务目标** — 先写这次观测要保护或提升的业务目标，以及失败时会造成什么伤害
3. **决策问题清单** — 苏格拉底追问，列出 5-10 个"上线后我们要支持什么决策？"的问题。每个问题都要能对应到实际动作
4. **推导指标** — 应用第 4 节（AB 四层）+ 第 5 节（安全护栏）方法论。每个指标必须写明它回答哪个决策问题、公式、分母、维度、阈值方向
5. **推导数据模型 / 口径** — 先定义粒度、窗口、维度、回填逻辑和统一分母，再决定采集。**硬规则：动手前必跑 `/datahub-schema-search`** 查现有 `ads_*` / `dwm_*` / `dwd_*` 表，按"复用优先、扩展次之、新建兜底"的顺序推导：① 下游汇总层（留存 / ARPU / 订阅）几乎肯定已有 `ads_*_di/mi` 可 JOIN；② 明细层埋点事件走埋点平台自动落 `tracking.dwd_analytics_page_<event>_hi`；③ session 化复用 `tracking.dwm_event_session_di`；④ AB 归因复用 `abtest.dwd_ab_user_experiments_*`；只有业务特有的 funnel 中间层才需要新建 `dwm_<domain>_funnel`。在 design doc 里**列出一张"复用 vs 新建"盘点表**，明确每张表的粒度 / 分区键 / JOIN 键 / 回填窗口。
6. **推导采集设计** — 写清事件、字段、context、配置维度；每个采集项必须能回链到指标公式或故障定位需要
7. **推导数据链路和消费端** — 应用第 3 节（三链路），每个指标落到哪条链路、数据源在哪、通道是谁、存储在哪、消费端是谁、谁被告警
8. **本地契约化** — 把 scrape config / alert rules / dashboard PromQL 写成本地可执行契约（`make test-observability-local` 样板），加入 CI。Target 不再是"配了 dashboard"而是"本地回归测试绿 + CI 里跑绿"。委派 `prom-grafana-dev` skill 落地
9. **写 Current / Target / Gap / Tracking** — 把现状、目标态、差距和 issue 跟踪显式拆开，禁止混写
10. **写设计文档并链接 issue** — 按 [design-doc-template.md](references/design-doc-template.md) 的内容结构产出 `docs/plans/YYYY-MM-DD-<feature>-observability-design.html`，已有 issue 直接回链；缺失项再委派 `gitlab-issue-sop`
11. **Dashboard + Metric as-Code 回归守护** — dashboard JSON / alert rules YAML / 业务指标 YAML（GrowthBook/Superset/SLA...）必须进 Git。CI 每次 MR 跑：
    - Dashboard 层：`verify.sh` 4 类断言（scrape/alert/provisioning/panel-promql round-trip）
    - Metric 层：set parity 双向 diff + AI 一致性 review（include [scripts/metrics-review.gitlab-ci.yml](scripts/metrics-review.gitlab-ci.yml)）
    禁止"手动在 BI UI 改指标**不走任何治理**"——改动必须走 CI-sync 或 Skill-driven 治理流程（见第 6 节 D3）。**Monorepo 场景目录约定**：dashboard JSON 放 `observability/grafana/dashboards/`，alert rules 放 `observability/prometheus/rules/`；契约验证委派 `prom-grafana-dev`（参考 `docs/architecture/tdd-infra/overview.html §1.0` 的 as-code 清单）。(Metric-as-Code 详见第 6 节 + [metrics-config-as-code.md](references/metrics-config-as-code.md))

> 如果设计需要代码 / 配置改动，再继续委派 `gitlab-mr`。没有明确的 gap tracking 前，不要直接跳到 MR。

### `:audit` 入口（6 步）

1. **列业务目标和决策问题** — 先明确这个系统当前应该支持哪些业务结果和决策，再判断现有观测是否回答得出来
2. **列现有指标与口径** — 先看现有指标是否定义清楚粒度、分母、窗口和维度，避免把“有埋点”误当成“可观测”
3. **列采集与三链路现状** — grep + 检查现有配置，对事件、字段、四要素逐一核对：数据源是否接入、通道是否配了、存储是否在落、消费端是否有人看
4. **列现状 / 目标 / 差距** — 对照业务目标、决策问题与指标，产出 Current / Target / Gap 表，不能只写“有没有”
5. **写 Tracking 并链接 issue，更新 SSOT** — 现有 issue 直接回链；未覆盖 gap 再委派 `gitlab-issue-sop`，并把结论写入架构文档和 launch-checklist。**Audit 时的数仓复用审计**：跑 `/datahub-schema-search` 核对现有 ads / dwm 表是否被设计文档引用。若有 `ads_payment_overview_di` / `abtest.dwd_ab_user_experiments_*` 等表可复用但另起炉灶建了重复宽表，按 gap 项记录（重复建 = 口径漂移风险）。
6. **验证本地契约是否存在，不存在则委派 prom-grafana-dev 补齐** — 检查仓库是否有 `make test-observability-local` / verify.sh 4 类断言（scrape/alert/provisioning/panel-promql round-trip）；若缺失或 CI 未跑绿，委派 `prom-grafana-dev` skill 落地

详见 [audit-checklist.md](references/audit-checklist.md)。

## Examples

### Good（按方法论从业务问题推导）

1. PM 问："这个弹窗推广能让更多用户提工单吗？"
2. skill 引导识别北极星（近端 = 工单提交率 per variation，远端 = D7 留存 per variation），漏斗（condition/fatigue/show rate），交互（click_primary/dismiss/never_remind），安全护栏（5min P99 弹窗次数）
3. 每个指标回链到业务问题，并写清公式、分母、阈值方向
4. 每个指标落链路：业务指标走 Snowplow + 数仓、系统指标走 OTel + Prometheus、错误走 Sentry
5. 每条链路四要素写清楚，且明确 Current / Target / Gap / Tracking
6. 产出设计文档 + 6 个 issue（每链路 2 个）+ 关联到 launch-checklist

完整样例见 [examples/smartpopup-design.md](references/examples/smartpopup-design.md)。

### Bad（反模式）

- **指标列表不问业务问题**："给后端加 5 个 OTel metric"，没写每个 metric 回答哪个问题 → 后续没人看、Dashboard 成垃圾场
- **只看业务指标不设安全护栏**：AB 实验 CTA 涨了 20% 判定成功，没注意 never_remind 率涨了 3 倍 → 半年后用户流失才发现
- **混合实时和延迟数据通道**：把漏斗率塞进 Prometheus、把 API latency 塞进数仓 → 业务问题查不到及时数据，SRE 问题查不到准确数据
- **链路不独立**：错误事件走 Snowplow、业务埋点走 Sentry → 错误告警被业务事件淹没，业务分析被错误噪音干扰
- **审计时只看"有没有"不看"能否回答业务问题"**：列了一堆 Dashboard 但没人定期看、告警有但没路由到 oncall → 审计通过但实际盲区依旧

## References

- [metric-layer-taxonomy.md](references/metric-layer-taxonomy.md) — **Metric Layer 7 层完整分类**（near/far-star / funnel / interaction / guardrail / diagnostic / system）+ 决策流程 + 下游影响映射（AB 场景是其子集）
- [ab-metric-taxonomy.md](references/ab-metric-taxonomy.md) — AB 实验 4 层指标深入细节、近远端配对、跨组 vs 组内、SmartPopup 12 指标分布
- [safety-guardrail-patterns.md](references/safety-guardrail-patterns.md) — 极值/频次/降级三种模式、阈值设计、为什么用 P99 而非平均
- [three-pipeline-architecture.md](references/three-pipeline-architecture.md) — 三链路详细参考实现、每链路四要素、链路交叉佐证
- [design-doc-template.md](references/design-doc-template.md) — 设计文档模板、mermaid 必含图、指标/链路表标准列
- [audit-checklist.md](references/audit-checklist.md) — `:audit` 5 步详解、每链路审计 checklist、gap 分类
- [delegation-map.md](references/delegation-map.md) — 委派哪个 skill 干什么、input/output 约定、触发条件
- [event-parsing-pipeline.md](references/event-parsing-pipeline.md) — Snowplow → schema 注册 → auto-parser → dwd 表的技术链路（链路①的第一公里）
- [dwm-wide-table-pattern.md](references/dwm-wide-table-pattern.md) — 业务主事件粒度宽表 + JOIN 模式 + 回填 job 模板（GrowthBook/SLA/Superset 共用消费）
- [growthbook-gitlab-cicd-pattern.md](references/growthbook-gitlab-cicd-pattern.md) — 单消费者场景的 GrowthBook metric config-as-code + GitLab CI 同步 job（多消费者模式见 metrics-config-as-code.md）
- [metrics-config-as-code.md](references/metrics-config-as-code.md) — Config-as-Code 完整决策目录：通用 D1-D6 + CI-sync 路径实施（trigger / service user / CI AI review）+ Skill-driven 路径实施（docs SSOT / skill register 协议）
- [scripts/metrics-review.gitlab-ci.yml](scripts/metrics-review.gitlab-ci.yml) — 可 include 的 GitLab CI template，**仅 CI-sync 路径**的 MR 上自动挂 AI metric-parity review job
- [examples/smartpopup-design.md](references/examples/smartpopup-design.md) — SmartPopup session 完整设计产出（7 步）
- [examples/smartpopup-audit.md](references/examples/smartpopup-audit.md) — SmartPopup 中期审计产出（5 步）
- `prom-grafana-dev` skill — 把方案落为本地可回归契约的方法论 + customer-care verify.sh 20 项断言参考实现
