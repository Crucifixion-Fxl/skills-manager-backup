---
name: operations-console-design
description: 为新 C 端业务或新功能设计“领域治理台”型管理后台的方法论 Skill。用于从业务目标出发，识别业务治理边界、决策点、方案对象、外部系统绑定、发布闭环，以及与 CMS、GrowthBook/实验平台、审批系统、运行时服务之间的职责分工。适用于用户提到“设计管理后台”“运营后台怎么做”“给 C 端应用配一个 admin”“梳理 admin 与 CMS/GrowthBook 边界”“设计 scene/recipe 与多系统集成模型”等场景，尤其适合不是普通 CRUD、而是需要校验、发布、审计、回滚的后台。
---

# operations-console-design

## Pain Points

这类后台要解决的核心问题不是“配置太多”，而是 `变更风险过高`。

在很多 C 端业务里，触达相关逻辑往往分散在代码、GrowthBook、CMS、Push/渠道系统以及运行时服务中。一次看似简单的变更，实际往往需要跨多个系统手工对齐。只要其中任一环节失配，就可能直接变成线上触达错误、实验错配、内容失效、错误下发、无法快速回滚，或者出了问题却很难定位到底是哪一层出了错。

更深一层的问题是 `主权和边界不清`。哪些对象应该由管理后台治理，哪些应该继续由 GrowthBook、CMS、Dittofeed 或运行时系统持有，很多团队并没有稳定模型。结果通常是：后台变成多个专业系统的半成品复制品，或者反过来只是一个录配置的 CRUD 页面，既无法降低风险，也无法形成清晰的发布、审计和回滚闭环。

这个 skill 要解决的，正是这类 `多系统配置失配 + 发布风险高 + 边界不清` 的问题。它把后台定义为一个 `operations console / domain governance console`：用统一的领域模型来承接业务决策对象，用明确的 binding 连接 GrowthBook、CMS 和渠道系统，并把校验、发布、审计、回滚收进一个受控治理闭环里。

## Description

把新 C 端业务的配套后台视为 `领域治理台`，而不是普通 CRUD 页面。

这个 skill 的目标不是帮用户列几个菜单，而是帮用户形成一套完整判断：
- 这个后台到底在治理什么
- 哪些对象由后台拥有，哪些只是在外部系统里被引用
- 页面结构如何服务建模、校验、发布和审计
- 控制面、运行时和外部系统如何分层

详细概念和样例放在 references 中，本文件只保留方法论主流程。

## Rules

- 先用抽象概念建模，再落具体术语；默认先写 `Domain / DecisionPoint / Strategy / *Binding`，不要直接把 `Scene / Recipe` 当通用模型。
- 不要把外部主实体硬建成本地治理对象；主权在 CMS、GrowthBook、Dittofeed 等系统时，优先建 `Binding`。
- 不要把 `ContentBinding` 和 `DeliveryBinding` 混成一个模糊引用字段。
- 不要为了形式统一而强造 `Strategy`；当渠道主权在外部系统时，只建 `DecisionPoint + Binding`。
- 页面结构必须服务校验、发布、审计和回滚；只有 CRUD 没有 `PublishOrder` 的方案不算治理台。
- 运行时不应跨 DC 同步强依赖控制面；默认消费区域内可读的发布结果或缓存。
- 跨系统的 variant 身份用**人话字符串 slug**（`summer_banner_v2`），admin 拥有它，外部系统按 string 存。禁用 DB 主键或 JSON 结构体作为跨系统身份。见 `references/cross-system-variant-key.md`。
- A/B 正交维度不过度拆分：**业务上总是一起改的两块要并成一个对象**（例如 banner 内容 + paywall 路由）。每多一个独立 A/B 对象翻倍 drift 风险与运维复杂度。
- 跨系统强校验只放在**发布 preflight**；combobox、提示、copy 按钮是润滑剂，真正的兜底在 `POST /publish-orders` 上的 `detectDrift()`。
- Schema 演进按 **5 阶段相位**走：doc → additive read → 并行表 backfill → consumer 切换 → (可选) 写穿 → drop 旧表。每阶段独立可回滚。见 `references/migration-phasing.md`。

## Method

### Step 1 - 判断是不是领域治理台

先判断当前要设计的是不是本 skill 处理的对象。

判断表：

| 类型 | 典型特征 | 是否适合本 skill |
|------|---------|----------------|
| 数据维护台 | 维护孤立主数据，无复杂生命周期 | 否 |
| 工作流操作台 | 以人工处理、审批流转、工单操作为中心 | 部分适合 |
| 领域治理台 | 以业务决策对象为中心，需要校验、发布、审计、回滚 | 是 |

只要满足以下多数条件，就按领域治理台设计：
- 配置会影响 C 端用户体验或运行时行为
- 存在规则、场景、方案、实验、内容引用、投放对象等概念
- 配置不能“改完即生效”，而要经过验证、审批或发布
- 同时依赖 CMS、实验平台、审批系统、渠道系统中的两类及以上

### Step 2 - 先拆主权

后台设计最容易失败的原因，是把所有系统的能力糊成一团。

先把对象按主权拆开：
- **治理对象**
  后台自己定义、编辑、发布、审计
- **实验对象**
  由 GrowthBook 等实验平台拥有，后台只做映射和校验
- **内容对象**
  由 CMS 拥有，后台只做引用和状态检查
- **执行对象**
  由 Dittofeed 或其他渠道系统拥有，后台只做绑定和追踪
- **运行时对象**
  最终被 backend / app / channel runtime 消费的对象

最重要的判断是：
- 后台自己创建、编辑、发布的，优先建成本地治理对象
- 主权在外部系统、后台只需要知道“它对应谁”的，优先建成 binding

### Step 3 - 先建抽象模型，再决定项目术语

先用抽象模型思考，不要一开始就被 `Project / Scene / Recipe` 之类术语绑住。

默认抽象模型：
- `Domain`
  业务治理边界
- `DecisionPoint`
  一个需要做触达决策的业务时点
- `Strategy`
  一个决策点下的候选业务方案；仅在后台拥有方案定义主权时出现
- `ExperimentBinding`
  后台对象与实验系统的映射
- `ContentBinding`
  后台对象与内容主实体的映射
- `DeliveryBinding`
  后台对象与执行主实体的映射
- `PublishOrder`
  受控发布动作
- `AuditLog`
  责任追踪

常见落地映射：
- `Domain -> Project`
- `DecisionPoint -> Scene`
- `Strategy -> Recipe`

但这些只是映射方式，不是通用模型本身。

### Step 4 - 统一 DecisionPoint，不强行统一 Strategy

多触达类型系统里，优先统一 `DecisionPoint`，不要强行统一所有渠道的 `Strategy`。

默认做法：
- popup：常见为 `DecisionPoint + Strategy`
- touchpoint：视主权决定是否存在 `Strategy`
- push：若 Dittofeed `user journey` 已拥有 trigger / fatigue / flow 主权，常见为 `DecisionPoint + DeliveryBinding`

所以：
- `Scene` 往往可以统一
- `Recipe` 只是一种常见落地，不是所有场景都必须有

### Step 5 - 用 Binding 把外部系统隔开

至少区分两类 binding：
- `ContentBinding`
  指向 CMS 等内容主实体，回答“展示什么”
- `DeliveryBinding`
  指向 Dittofeed journey、placement config 等执行主实体，回答“由谁来执行 / 编排”

不要用一个模糊字段同时承载内容和执行语义。

同时把实验系统也显式放进来：
- `ExperimentBinding`
  指向 GrowthBook feature / variant 等实验对象

### Step 6 - 把 condition 和 fatigue 收进 Strategy

`condition` 和 `fatigue` 不建议升成一级治理对象，而应作为 `Strategy` 的内部结构：

- `condition` = `EligibilityRule`
  回答“这个方案现在是否有资格命中”
- `fatigue` = `ExposurePolicy`
  回答“即使命中，现在是否允许再次投放”

这一步的目标，是把“是否命中”和“能否再次投放”分开，而不是都笼统地叫规则。

### Step 7 - 用页面结构承载角色任务

页面结构应该围绕以下任务组织：
- 定义治理对象
- 管理 binding
- 做试运行和校验
- 发起发布
- 查看审计和历史

默认角色：
- `operator`
- `admin`
- `reviewer/approver`
- `auditor/observer`

默认页面模块：
- 业务边界 / 项目页
- 决策点列表页
- 决策点详情页
- Strategy 或 Binding 编辑区
- Preview / Dry Run
- Publish
- Audit

如果某场景是 `DeliveryBinding` 主导，例如 push，就不要强造 `Recipe 编辑器`。

### Step 8 - 把 Dry Run / Preview 设计成独立治理能力

`Dry Run / Preview` 不只是一个辅助页面，而是这类治理台在发布前降低风险的关键能力。

它至少应验证四件事：
- `DecisionPoint` 是否会在给定上下文下被触发
- `Strategy` 是否会命中，或为何未命中
- `ExperimentBinding / ContentBinding / DeliveryBinding` 是否有效
- 最终会形成什么 `ExecutableTarget`

推荐至少设计两种模式：
- `即时模拟`
  给定单次事件、用户上下文或输入参数，解释一次决策过程
- `历史回放`
  用历史样本或离线数据批量验证命中逻辑和配置影响

Dry Run 的标准输出不应只有 true / false，至少应包含：
- 命中结论
- `EligibilityRule` 结果
- `ExposurePolicy` 结果
- binding 有效性
- 最终执行对象摘要

### Step 9 - 把发布闭环作为控制面的中心

完整设计必须覆盖：

```text
定义
  -> 校验
  -> 试运行 / 预览
  -> 冻结快照
  -> staging
  -> 审批
  -> prod
  -> 审计
  -> 回滚
```

如果没有 `PublishOrder`、审计和回滚，这更像编辑器，不像治理台。

### Step 10 - 部署架构优先保证运行时不跨 DC 依赖控制面

部署设计不能只写 K8s、ArgoCD 和服务列表，还必须回答：
- 控制面在哪个 DC
- 运行时在哪些 DC
- 发布态结果如何分发
- 是否允许最终用户请求跨 DC 依赖控制面或外部系统

默认原则：

`运行时请求不跨 DC 同步依赖控制面；运行时消费区域内可读的已发布结果或区域缓存。`

## Output

一个合格的设计产出，至少应包含：

1. 一句话定义这个后台的本质
2. 主权拆分：Admin / CMS / GrowthBook / Dittofeed / Runtime
3. 抽象模型：`Domain / DecisionPoint / Strategy / *Binding`
4. 项目映射：哪些概念落成 `Scene / Recipe / Journey`
5. 页面信息架构和角色任务
6. 发布闭环
7. 部署拓扑、DC 和网络原则
8. 明确的不做项

如果项目正在写文档，推荐落到：
- `docs/architecture/<topic>/admin.html` 或 `overview.html`
- `docs/product/user-stories/<topic>-admin.html`
- `docs/plans/YYYY-MM-DD-<topic>-admin-design.html`

## References

- [references/domain-model.md](references/domain-model.md) — 完整参考方案：概念总表、关系总览、主权判断、三个例子映射
- [references/admin-ia-and-flows.md](references/admin-ia-and-flows.md) — 页面信息架构、角色任务、核心操作流
- [references/dry-run-and-preview.md](references/dry-run-and-preview.md) — Dry Run / Preview 的目标、输入输出、两种模式与反模式
- [references/deployment-architecture.md](references/deployment-architecture.md) — 控制面 / 运行时 / 多 DC / 网络依赖原则
- [references/example-smartpopup.md](references/example-smartpopup.md) — SmartPopup 作为 `DecisionPoint + Strategy` 样例
- [references/cross-system-variant-key.md](references/cross-system-variant-key.md) — admin ↔ GrowthBook (或其他外部实验系统) 的边界身份、drift detection、content-first 授权流程、反向 nav 升级路径
- [references/migration-phasing.md](references/migration-phasing.md) — 已上线治理台的 schema 演进 5 阶段相位、回滚语义、snapshot 版本加性 bump、常见失败模式
- [references/example-engagement-admin.md](references/example-engagement-admin.md) — Engagement Admin 从 v1 (3-way A/B + JsonLogic condition) 迁到 v2 (2-way Experience + GB force rules) 的实战复盘
- [references/async-publish-state-machine.md](references/async-publish-state-machine.md) — 长链路发布的 compensation-worker 模式：执行过程状态机、authoritative 边界、idempotency、观测与反模式
- [references/change-detection-and-canonical-diff.md](references/change-detection-and-canonical-diff.md) — canonical JSON（sorted-keys、稳定 checksum）+ new / synced / draft 三态 badge：draft 判据、scope 切分、UI 呈现与反模式
- [references/eligibility-ownership.md](references/eligibility-ownership.md) — eligibility 判断逻辑归后台 / 外部 targeting 系统的决策框架、真实迁移样例
- [references/growthbook-integration.md](references/growthbook-integration.md) — ops console ↔ GrowthBook 的可复用集成模式：write-through、drift detection、force rules
- [references/publish-flow-state-machine.md](references/publish-flow-state-machine.md) — 草稿 → staging 快照 → 生产发布 → 回滚 → 归档 整条链路的业务流程状态机方法论

## Examples

### Bad

用户说“给这个 C 端产品设计一个管理后台”，结果直接输出：
- 菜单：用户管理、内容管理、配置管理、报表管理
- 页面：列表页、详情页、编辑页
- 默认所有场景都建同构 `recipe`
- 把 CMS 文案、GrowthBook 流量、Push journey 配置都塞进一个后台
- 没有发布、审计、回滚设计

问题：
- 没有先判断是不是治理台
- 没有先拆主权
- 没有先建 `DecisionPoint / Strategy / Binding`

### Good

用户说“为新的 C 端触达体系设计配套后台，包含 popup、touchpoint、push”。

先按方法论推进：

1. 定义 `Domain`
2. 识别三个 `DecisionPoint`
3. 为 popup 建 `Strategy`
4. 为 push 建 `DeliveryBinding`
5. 为内容建立 `ContentBinding`
6. 收口到 `PublishOrder`

最后得到：
- popup：`DecisionPoint + Strategy + ContentBinding`
- push：`DecisionPoint + DeliveryBinding`
- GrowthBook：`ExperimentBinding`
- Runtime：消费已发布结果，而不是直接强依赖控制面
