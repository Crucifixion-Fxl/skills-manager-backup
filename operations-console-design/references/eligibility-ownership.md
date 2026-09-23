# Eligibility Ownership Reference

本文件给 `operations-console-design` 提供一份"eligibility 判断逻辑归谁管"的决策框架。几乎所有带 targeting 或实验能力的 ops console 都会撞到这个问题——后台要不要自己做资格判断，还是把它让给外部 targeting 系统。

真实迁移样例见 [example-engagement-admin.md](example-engagement-admin.md)。

## 1. 问题

Ops console 做的事情本质上是"决定谁在什么场景下看到什么"。其中"谁"这一部分——也就是 eligibility / 资格判断——在大多数成熟业务里都有另一个候选宿主：GrowthBook / Optimizely / LaunchDarkly / 自建 targeting 服务。

所以问题不是"要不要做 eligibility"，而是"主权归谁"：

- 如果 admin 自己拥有 eligibility 的编辑、存储、评估路径，那么 eligibility 会随 admin 的 snapshot 一起发布、回滚、审计，但 admin 也要自己维护一套规则 DSL 和评估器。
- 如果外部 targeting 系统拥有 eligibility，admin 只做只读投影，那么 admin 变轻，但 admin 的 snapshot 不再是"一次触达决策的完整真源"——它缺了"谁命中"这一半。
- 如果两边各管一半，就要回答一个更难的问题：边界划在哪里、操作员怎么知道"这条规则到底在谁那里生效"。

这三种答案在不同项目里都出现过，没有一个是普适正确。下文给出每个候选的长相、打分、触发信号，以及 engagement 项目的实际选择和反向推导。

## 2. 三个候选方案

### (A) Admin owned — eligibility 规则存在 admin DB，backend 评估

结构：admin DB 里有一列 `condition Json?` 存 JsonLogic / Rule DSL AST。操作员在 admin UI 上编辑（可视化 rule builder + 原始 JSON 双通道 + scenario tester）。`snapshot-assembler` 把 `condition` 序列化进 snapshot；backend 在 dispatch 时跑一个内置 evaluator（例如 `TouchpointEvaluationService.evaluateCondition(ast)`）。

Engagement v1 用的就是这个模式。证据见：

- [`admin/src/lib/schemas.ts`](/home/jchen/engagement/admin/src/lib/schemas.ts) 的 `conditionSchema`——用 operator allow-list 把 JsonLogic 裁成 `entitlement.hasFeature` / `entitlement.tier` / `entitlement.hasUsedTrial` + 布尔组合的最小子集，保证 admin 接收的 AST 必然可被 runtime evaluator 执行。
- [`admin/src/lib/condition-translator.ts`](/home/jchen/engagement/admin/src/lib/condition-translator.ts) 的 `toJsonLogic` / `fromJsonLogic` 双向翻译——承担"可视化 rule group"和"JsonLogic AST"之间的往返。

收益：eligibility 和 content 在同一份 snapshot 里，回滚语义干净；backend 评估零外部依赖（SDK 挂了也能跑）；审计归属清晰——改规则的人是 admin 操作员，`AuditLog` 天然对上。

代价：admin 要自己维护一套规则 DSL（schema 白名单、translator、visual editor）；每新增一个 entitlement 维度都要同时改 admin 的 allow-list + visual editor + runtime evaluator 三处；和已有 targeting 系统的能力重叠。

### (B) External system owned — admin 只做只读投影

结构：eligibility 规则写在外部 targeting 系统（GrowthBook force rule、LaunchDarkly targeting rule 等）。admin DB 不再有 `condition` 列。admin UI 通过 `GET /api/growthbook/features/{slug}` 把规则拉回来，在触点详情页渲染成只读树；"编辑"按钮 deep-link 到外部系统 UI。Runtime 直接调外部 SDK（`gb.getFeatureValue(slug, userContext)`）拿到 `show/don't show + variantKey`，不再 evaluate 本地 AST。

Engagement v2（plan doc D1）选的就是这个模式。见 [2026-04-22-engagement-admin-architecture-v2.md](/home/jchen/engagement/docs/plans/2026-04-22-engagement-admin-architecture-v2.md) 的决策表：

> D1. Eligibility → GrowthBook force rules. Same domain as targeting (by user attribute). Unifies "who sees this" in one system. Admin becomes read-only projection of GB rules.

收益：admin 代码面缩小一大块（schema / translator / visual editor / evaluator 全删）；eligibility 的表达力自动跟上外部系统（不用等 admin 加新 operator）；改规则的人可以是运营 / 增长团队而不是 admin 操作员。

代价：admin snapshot 不再自包含——回滚只回滚内容，不回滚 eligibility；backend 多一个 runtime 依赖（GB SDK 挂了要有 fallback）；审计要跨两个系统对账。

### (C) Hybrid — admin 管"业务硬约束"，外部管"人群 targeting"

结构：eligibility 被拆成两层。admin DB 保留一个窄口径的硬约束字段（例如 `requiredEntitlement: 'premium'`、`minSubscriptionDays: 7`），表达"没这条业务前提，不管外部怎么 targeting 都不该命中"。外部 targeting 系统管剩下的一切——人群切分、地域、设备、灰度百分比。Runtime 先 check admin 硬约束，通过后再调外部 SDK。

收益：对"一个触点绝不能发给免费用户"这种跨 targeting 维度恒成立的业务不变量，admin 有了存放的地方；外部 targeting 系统不需要理解"什么叫权益"，只管"这个 user attribute 是 X 的人命中"。

代价：两套真源并存，必须有严格的边界定义文档 + 操作员培训，否则"这条规则到底写哪里"会变成反复出现的工单；很容易滑向下文的"半个真源"反模式。

## 3. 决策矩阵

每格记 `+` / `0` / `-`（优 / 中 / 劣），后接一行要点。

| 关注点 | (A) Admin owned | (B) External owned | (C) Hybrid |
|---|---|---|---|
| 快照原子性 | `+` 回滚连 eligibility 一起回滚 | `-` snapshot 只含 content，eligibility 在外部系统，回滚半截 | `-` 两处状态必须配对回滚 |
| 审计归属 | `+` actor 都是 admin 操作员 | `0` 跨 admin + 外部系统对账 | `-` 每条变更要追到正确系统 |
| Runtime 依赖 | `+` backend 评估零外部依赖 | `-` 依赖外部 SDK / API，要 fallback | `0` 仍依赖外部 SDK，硬约束由 backend 本地判 |
| 操作员工作流 | `0` 可视化 editor 够用，但复杂规则要 raw JSON | `+` 改规则的人本来就在外部系统 | `-` 要记住"这条改哪里" |
| Admin 代码复杂度 | `-` schema + translator + visual editor + evaluator 四套 | `+` 删光，换一个只读渲染 | `0` 窄口径硬约束 schema，可控 |
| 跨环境对齐 | `+` snapshot 跨环境复制即可 | `-` 外部系统的 env/project 映射要自己对齐 | `-` 两个系统的 env 都要对齐 |
| 表达力扩展 | `-` 加一个维度改三处 | `+` 跟随外部系统 | `0` 硬约束侧加维度仍要改 |
| 真源清晰度 | `+` 单一真源 | `+` 单一真源 | `-` 最易出真源分裂 |

观察：A 和 B 各自在一组 axis 上占优，C 在"真源清晰度"这一项比另两个都差——这是 C 最大的风险。

## 4. 触发信号：什么场景选哪个

**选 A（admin owned）的信号**：

- eligibility 必须随 config snapshot 一起原子回滚——"回滚 v12 到 v11" 必须把"谁能看到"也一起退回。
- backend 在离线 / 边缘环境 evaluate，没法调外部 SDK（例如 edge worker、no-network device）。
- 规则的 actor 和 content 的 actor 是同一批人（admin 操作员），审计归到 admin 更顺。
- 规则维度窄、稳定——就几个固定的 entitlement check，不需要频繁扩展。
- 还没有成熟的外部 targeting 系统，或现有系统的 targeting 能力达不到业务要求。

**选 B（external owned）的信号**：

- targeting 维度频繁改，且主要是"人群切分 / 地域 / 设备 / AB 灰度" 这类外部系统本来就擅长的维度。
- 改规则的人不是 admin 操作员——是增长团队、数据团队，他们已经在外部系统里工作。
- 已有成熟外部系统承担 targeting + 实验，admin 再做一份等于重复造轮子。
- 规则和实验是同一套（admin 本来就要和外部系统打通实验），再多一套 eligibility 纯属分裂。

**选 C（hybrid）的信号**：

- eligibility 天然分两类——业务硬门槛（权益 / 订阅 / 合规限制）一定不能给外部系统随便关掉；运营可变 targeting（人群切分、灰度）又一定要让外部系统管。
- 有明确的、跨所有 targeting 维度恒成立的业务不变量，放错地方会导致合规 / 计费事故。
- 团队愿意付"两个真源"的维护成本——写清边界 doc、做交叉校验、培训操作员。

## 5. Engagement 的选择：从 A 迁到 B

Engagement v1 选的是 A，v2（plan doc D1）迁到 B。被压倒的几件事：

**重复表达**。v1 的 `conditionSchema` 只认 `entitlement.hasFeature` / `tier` / `hasUsedTrial` + 布尔组合——这恰好是 GrowthBook force rule 基于 user attribute 也能表达的东西。同一件事在 admin JsonLogic 和 GB targeting 里被写两遍，操作员还得手工保持一致。

**半个真源**。v1 只把 `condition` 写进 admin snapshot，但 GB 已经在管 variant 分流；结果 eligibility 在 admin snapshot 里，targeting + variant 在 GB rule 里，两边都不是完整真源。回滚 admin snapshot 回滚不动 GB 规则；回滚 GB 规则又回滚不动 admin condition。

**可视化 JsonLogic 编辑器的维护成本 > 收益**。`condition-translator.ts` 的 `toJsonLogic` / `fromJsonLogic` 要承担"所有支持的 AST ↔ QB group 双向无损映射"——任何手写的合法 JsonLogic 一旦落在 translator 的可视化子集之外，UI 就只能降级到 code-only 模式。`ConfigureTab` 里还要塞 visual editor + raw JSON editor + scenario tester 三块。代价很高，但产出仅是"图形化"本身——逻辑能力并没有比直接写 JsonLogic 更强。

**扩展一个维度要改四处**。加一个 `entitlement.hasEntitlementEndingWithin(days)`：改 `ALLOWED_CONDITION_OPERATORS` 白名单、改 `condition-translator` 的 `atomToRule` / `ruleToJsonLogic`、改 visual editor 的 `QB_FIELDS`、改 runtime evaluator。GB 只要在 targeting 里加一个 user attribute 就完事。

于是 plan doc 把 D1 写成"eligibility → GB force rules"，admin 退成只读投影，`condition Json?` 列在 phase 3 被移除。

## 6. 反向推导：engagement 什么场景会选回 A

"v1 错了"这个判断依赖几个具体条件。如果这些条件不成立，A 反而更合理：

- **没有 GrowthBook 可用**。创业项目早期、内网隔离环境、合规禁外连——没有成熟外部 targeting 系统承接，自己造一个还不如复用 JsonLogic evaluator。
- **eligibility 强绑定实时 entitlement 事件**。假设 admin 直连 entitlement 事件流（例如订阅状态 websocket），用户买完 premium 后 500ms 内能更新；而 GB SDK 靠 user attribute 缓存，分钟级。对"订阅状态变化后立刻能看到 upsell"这种场景，admin 本地判 > 外部 SDK。
- **Runtime 在离线环境 evaluate**。edge worker、on-device agent、no-network kiosk——backend 根本不能调 GB SDK，只能带本地 AST 跑。这时 A 是唯一选项。
- **Config 必须跨"snapshot 时刻"重放**。如果要支持"把 v8 snapshot 拿到三个月后的环境里重新 evaluate 应该长什么样"，eligibility 必须是 snapshot 的一部分；GB 规则随时间漂移，外部 owned 撑不住这种 offline replay。

换句话说，plan doc D1 的结论依赖三个前提：GB 可用、runtime 能调 GB SDK、不需要 offline replay eligibility。任何一个前提打破，回退到 A 都是合理的。

## 7. "半个真源"反模式

最容易踩的错是：admin 写一部分 eligibility 规则，外部系统写另一部分，两边独立运行，runtime 先 check admin 再 check 外部系统——但没有人维护"边界定义"。症状：

- 同一个 targeting 需求在两个系统里都能实现，操作员看心情决定写哪边。
- 调试"用户 X 为什么没命中"时，要拉两个系统的日志对着看。
- 回归事故高发——在 admin 里加了一条 `tier != 'free'` 规则，忘了 GB 里还有一条 `tier in [pro, enterprise]` 的 force rule，两条不一致时命中逻辑等同于两者交集，没人能一眼看出。
- 审计报告拆成两份，合规团队骂街。

**禁止模式**：同一个 eligibility 维度同时写在两个系统。例如"tier"既在 admin `condition` 里判，又在 GB force rule 里判。

**允许的 C-hybrid**：维度严格互斥。admin 只判"业务硬约束"（权益 / 订阅 / 合规）；GB 只判"运营 targeting"（人群 / 地域 / 灰度）。边界写进架构文档，代码层面用 schema 强制——admin 的 `condition` 白名单不能包含任何 targeting 维度，GB 的 force rule 评审禁止引用 entitlement 维度。

任何时候只要出现"这条规则写哪里都行"的分歧，这就是滑向反模式的前兆，必须回到边界定义文档重新裁决。

## 8. 从 A 迁到 B 的注意事项

engagement v1 → v2 的 phase 3（condition removal）不是一次开关切换，是一个分阶段迁移。照抄时需要留意三点：

**(a) 外部系统得先能承接所有 admin 现有 rule 语义**。engagement v1 的 `ALLOWED_CONDITION_OPERATORS` 包括 `entitlement.hasFeature` / `entitlement.tier` / `entitlement.hasUsedTrial`——迁到 GB 前必须先确认 GB 的 targeting 能读到这三个 user attribute。plan doc 的 Q2 就是在问这个："GB force rules can return `false` to gate a feature, but can they return a specific `variantKey` based on entitlement attribute? Need to confirm this matches the eligibility model we're committing to." 不要等到迁移开跑才发现外部系统的表达力盖不住。

**(b) Admin UI 不能一天变空**。operator 习惯了在触点详情页看到"展示条件 (L1 Condition)"卡片，一夜之间整块消失会造成"规则丢了"的错觉。plan doc 的做法是 phase 3 把编辑器换成只读 "Eligibility (read-only)" 卡片——渲染从 `GET /api/growthbook/features/<slug>_experience` 拉回来的 rule 摘要，并配 deep-link "在 GrowthBook 中打开"。操作员眼里规则还在，只是编辑入口换了地方。

**(c) 旧 AST 要有冻结日期，runtime 在那之后不再 evaluate**。`condition Json?` 列在 phase 3 停止写入但不立刻删，保留到 phase 5。backend 需要明确一个时间点——在此之前的 snapshot 走 JsonLogic evaluator，之后走 GB SDK。plan doc 的处理：snapshot v4 兼容窗口 ≥ 7 天（"Phase 3 → 4: Backend running v5 in prod for ≥ 7 days with no fallback to v4"），之后才 drop 列。否则旧快照重放会命中一个已经不存在的 evaluator。

**(d) Translator 代码是最后删的**。`condition-translator.ts` 只要还有一个旧 snapshot 在 replay，就不能删——它的 `fromJsonLogic` 是 admin 能否读懂旧规则的唯一路径。plan doc phase 3 步骤 5 明确列出"Remove `src/lib/condition-translator.ts` + `src/components/wizard/StepCondition.tsx` + all tests"，放在 column drop 之前一步而非之后，是因为 UI 侧不再需要编辑就已经不需要 translator 了；但 DB column 要等更长窗口。
