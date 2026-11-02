---
name: engagement-touchpoint-integration
description: 新增、审查或排查 Engagement App 触点，接通宿主、PE/GrowthBook 准入、Admin Experience/Fatigue、CMS 文案与发布验证；支持 SDK 渲染组件和动态文案格式化扩展，保护公共 API、生命周期与业务决策边界。直接打开 H5 Paywall 时先验证数据链路；模板 UI 开发使用 engagement-h5-paywall-creation。
---

# engagement-touchpoint-integration

## Description

本 Skill 是 Engagement 触点接入的端到端工作流。它把 `home_page_device_badge` 的真实接入经验沉淀成可复用流程，目标是让人或 AI agent 在接入其他触点时少猜、少漏、少把历史命名和旧文档带回代码里。

本 Skill 负责四类任务：

| 场景 | 目标 |
|---|---|
| 新增触点 | 从需求参数到 CMS 模板 / 素材、宿主代码、admin、GrowthBook、发布、观测的完整接入 |
| 审查触点 | 检查现有接入是否漏 sn、slotName 漂移、GB/admin drift、埋点断链 |
| 编写文档 | 产出可给其他同学或 AI agent 复用的接入文档 |
| 排障 | 根据“触点不展示 / 全部 locked / 点击无跳转 / Superset 无数据”等症状定位层级 |

按当前触点类型选择对应样例：设备 Badge、Cloud Service、Library 或 Playback，不默认读取全部样例。直接 H5 模式读 `references/h5-paywall-data-flow.md`。涉及 `runtimeBundle`、`runtimeInputs`、文案占位符或宿主业务 API 取值时必读 `references/dynamic-copy-runtime.md`。MR 触及 Engagement SDK 公共 API、evaluate/render/cache、网络抽象或生命周期事件时，必须读 `references/sdk-core-change-guardrails.md`。PROACTIVE 或任何频控需求必须先读本文的疲劳度边界；Library 示例读 `references/library-cloud-promo-fatigue.md`。排障读 `references/touchpoint-troubleshooting.md`；运维验收读对应 `*-ops.md`。

## 核心心智

触点不是单一代码改动，而是多个系统的一致性工程：

| 层 | 负责什么 | 典型入口 |
|---|---|---|
| 宿主 App | SDK 初始化、slot 容器、sn / `runtimeInputs` 传递、鉴权网络适配、执行 service 已确定的 CTA、页面生命周期失效 | g0-ios / g0-android / Flutter 宿主 |
| 商品目录 / PE | 商品元数据、权威用户/设备属性、GrowthBook 评估上下文 | iot-service product / personalization-engine |
| marketing-cms | 区块触点素材、静态/动态文案和允许替换的展示字段；Paywall collection 管商品 / Offer 内容，不替触点选择路由 | staging CMS 的 engagements / paywalls collection |
| Engagement SDK | 按 CMS payload 渲染 service 已确定的内容和 action；可扩展 renderer/builder、动态文案解析与单复数等格式化；复用通用触点生命周期，不二次选择业务变体、样式或 Paywall | engagement sdk-ios / sdk-android / sdk-flutter |
| engagement-admin | 触点注册、Experience/Fatigue variant、actionConfig、疲劳度配置、规则发布 | `https://engagement-admin.addx.live` |
| GrowthBook | 受众、实验、强制规则，分别返回 Experience/Fatigue `variantKey` | `https://us-ab-management.addx.live` |
| engagement-service | 用 GB `variantKey` 匹配 Admin snapshot，执行 Fatigue，返回最终 Experience、CMS 内容和唯一 action / `paywallId` | `/engagement/v1/touchpoints/evaluate` |
| Superset / 数仓 | 复用通用触点漏斗，按触点和 Paywall 筛选；SDK 基础链路单独排障 | `https://superset-us.addx.live/superset/dashboard/paywall-p0-touchpoint-to-paywall-health/` |

相关代码仓库：

| 系统 | GitLab |
|---|---|
| engagement | `https://gitlab.addx.ai/services/value-added/engagement` |
| personalization-engine | `https://gitlab.addx.ai/services/personalization-engine` |
| marketing-cms | `https://gitlab.addx.ai/CLOUD/marketing-cms` |
| g0-ios | `https://gitlab.addx.ai/SWCLIEN/g0-ios` |
| g0-android | `https://gitlab.addx.ai/SWCLIEN/g0-android` |
| g0-flutter-module | `https://gitlab.addx.ai/SWCLIEN/g0-flutter-module` |

关键映射：

| 概念 | 规则 |
|---|---|
| admin `slug` | 就是 SDK `slotName`，运行时不能另造一套名字 |
| App 级 slug | 同一套宿主代码覆盖多个 App 时，触点也优先按 App 拆分，例如 VicoHome 用 `home_page_device_badge`，KiwiBit 用 `kb_home_page_device_badge`，VicoNature 用 `vn_home_page_device_badge` |
| GrowthBook feature | 默认命名 `engagement_<slug>_experience` |
| GrowthBook 返回值 | 必须是 admin 已注册的 `variantKey` |
| Experience 唯一性 | 一个 `variantKey` 必须唯一确定 admin Experience、`cmsSlug`/Banner 样式和 `actionType`/`paywallId`；SDK 不得在 evaluate 后按业务数据再次分流 |
| admin `cmsSlug` | 决定运行时 `solution_id`，指向 CMS 内容 |
| `locked=true` | `FEATURE_GATE` 展示 SDK 渲染的 CMS 内容 |
| `locked=false` | `FEATURE_GATE` 透传宿主原生 child 视图 |
| 设备级触点 | 必须传 `EngagementContext(sn:)`，并在状态查询、点击、转化路径继续传同一个 sn |
| Fatigue feature | PROACTIVE 优先用 `engagement_<slug>_fatigue`；GB value 必须匹配 admin fatigue `variantKey` |
| 疲劳度 SSOT | `maxImpressions`、曝光冷却、dismiss 冷却和永久抑制由 admin/GB/engagement-service 统一执行；App/SDK 不另建 Session/Day/关闭计数 |
| `runtimeBundle` | 表示 CMS 文案需运行时取值；允许变量替换、单复数、数字/日期/相对时间等展示格式化，不决定业务准入、业务文案变体、Banner `preset`/creative、CTA 或 Paywall 路由 |
| `runtimeInputs` | 宿主显式传入的 JSON/字典上下文，用于 CMS 参数绑定；不允许 SDK 猜测全局 App 状态 |
| `paywallId` SSOT | Admin Experience 的 `actionConfig.paywallId` 经 engagement-service 下发后即为最终值；禁止 `paywallIdsByCreative`、按 `creativeKey` 改写或任何客户端二次路由 |
| `presentation.shouldPresent` | 不是触点事件，而是宿主否决展示的决策回调；新触点和 SDK 基础能力不得新增或使用。evaluate 已下发可展示结果且内容可渲染时，宿主直接展示；页面销毁等问题由生命周期取消和挂载有效性处理 |
| SDK 基础能力边界 | 优先复用；允许沿既有渲染架构扩展 builder 和通用文案处理。公共模型、evaluate/cache/network 和生命周期改动按实际影响评审，不因业务命名、首个消费者或与业务同 MR 就直接阻断 |
| `host_spm` | 当前宿主不再新增，不要恢复 `EngagementHostSpm` 常量 |

### SDK 展示能力与业务决策的边界

触点渲染器和通用文案处理属于 SDK。已有组件不能表达目标视觉时，可以新增 `ContentViewBuilder` 并注册，按 CMS payload 展示；宿主提供页面数据、鉴权取数和容器，不必重新实现 renderer 或单复数逻辑。

`runtimeText.cases` 等分支按实际作用审查：`1 video` / `2 videos`、数字/日期本地化属于展示格式化；根据 N/D 选择 Snapshot/Rolling、不同营销卖点/优惠或 Paywall 属于业务变体决策，即使只改 `text` 也不能放进 runtime。格式化前后必须保持服务端确定的 Experience、样式、action、`paywallId`、疲劳度和标准事件语义。

SDK 改动按 [变更门禁](references/sdk-core-change-guardrails.md) 分级说明与验证。新增展示组件不要求先凑齐两个消费者；不能仅因类型含 `Library`、注册 builder 或使用文案条件分支就判为越界。

## 使用前必须确认的信息

如果用户没有提供以下信息，先从代码、admin 或 GB 链接中查；仍查不到时再问用户。不要在关键字段上臆造。

```yaml
slug: <admin slug，也是 SDK slotName>
appKey: vicohome | kiwibit | viconature | <other app>
type: FEATURE_GATE | PROACTIVE
hostApp: iOS | Android | Flutter | Web
hostLocation: <页面 / view / cell / manager 文件>
deviceScope: true | false
snSource: <设备级触点必填，例如 device.serialNumber>
technicalGates:
  - <仅当前页面无法展示、缺必要请求参数等技术硬条件；稳定业务资格应进 PE/GB>
eligibilityAttributes:
  - <会员/设备/内容资格的权威 PE 属性及口径>
ui:
  locked: <CMS blockType / 文案 / 图片 / 样式>
  unlocked: <FEATURE_GATE 的原生 child 视图，可空>
action:
  type: OPEN_PAYWALL | DEEP_LINK | DISMISS | NONE
  paywallId: <OPEN_PAYWALL 必填>
  deepLink: <DEEP_LINK 必填>
growthbook:
  experienceFeatureId: engagement_<slug>_experience
  experienceVariants:
    - variantKey: <GB 返回值>
      locked: true | false
      cmsSlug: <CMS 内容 slug>
      presentationKey: <唯一 Banner preset/creative；由该 Experience 固定>
      actionType: OPEN_PAYWALL | DEEP_LINK | DISMISS | NONE
      paywallId: <OPEN_PAYWALL 时由该 Experience 唯一确定>
  fatigueFeatureId: engagement_<slug>_fatigue | null
fatigue:
  enabled: true | false
  disabledReason: <仅 enabled=false 时记录；不得用 App 本地频控代替>
  variants:
    - variantKey: <GB fatigue value>
      strategy: none | fixed | phased | dismissEscalating | <当前平台已验证策略>
      maxImpressions: <number | null>
dynamicCopy:
  enabled: true | false
  sdkRevision: <目标 App 实际依赖且已包含 runtime 能力的 revision>
  runtimeInputs:
    - key: <例如 library.query>
      source: <页面已有查询参数>
  variables:
    - key: <例如 todayVideoVisitCount>
      source: context | api
      allowedTargets: [<仅 text/subtitle/highlightText 等文案展示字段>]
  formatting: [<变量替换 / 单复数 / 数字日期本地化 / 相对时间；不承载业务变体选择>]
  forbiddenTargets: [blockType, preset, creativeKey, cmsSlug, actionType, actionContext, paywallId, solutionId]
  failureBehavior: <缺值/超时时收起受影响 block，不展示旧值或占位符>
sdkImpact:
  level: none | host_adapter | sdk_presentation | sdk_core
  existingCapabilityReuse: <优先复用的现有 API/renderer/event/fatigue/runtime 能力>
  affectedSurfaces: [<SDK 有改动时填写：builder/formatter/public API/evaluate/render/cache/network/lifecycle>]
  consumers: [<当前消费者；已有其他消费者可列出，不强制两个>]
  compatibilityProof: <按实际影响记录组件、格式化、旧调用方、静态触点、事件与性能回归证据>
preludeEventKey: <父容器曝光事件，作为数仓漏斗分母>
observability:
  supersetDashboard: <通用 P0；按 slotName + paywallId 筛选>
```

### 必问用户的问题

只有这些问题不能从本地代码和平台页面确认时才问：

1. 这个触点是设备级还是用户级？如果是设备级，宿主哪个字段是权威 SN？
2. 这个触点面向哪个 App？如果同一套代码要覆盖 VicoHome / KiwiBit / VicoNature，是否需要按 App 拆成不同 slug？
3. 哪些只是页面/容器/必要参数的技术硬条件，哪些是应进 PE/GB 的稳定业务准入？
4. `locked=false` 时是否需要宿主原生 child 视图？如果需要，设计稿或现有组件在哪里？
5. CTA 是 `OPEN_PAYWALL` 还是 `DEEP_LINK`？目标 `paywallId` / `deepLink` 是什么？
6. 哪个事件是触点出现机会的 `preludeEventKey`？`OPEN_PAYWALL` 默认复用通用 P0 看板，仅在它无法表达目标指标时才确认额外的业务看板。
7. PROACTIVE 触点应命中哪个 fatigue feature / variant？曝光上限、冷却、dismiss 和永久抑制是否已能由平台表达？
8. 文案是否含运行时变量？如果有，变量来自页面上下文还是鉴权 API，需要哪些 `runtimeInputs`，目标 App 是否已依赖包含该能力的 SDK revision？
9. 每个 GrowthBook `variantKey` 是否已经在 Admin 唯一绑定 Banner 样式、action 和 `paywallId`？是否存在客户端按 N/D、`creativeKey` 或其他业务值再次选择样式/路由？
10. SDK 已有组件和文案能力能否复用？需要新增的是展示组件/格式化，还是公共基础能力？涉及哪些 API/生命周期，如何验证兼容性？业务资格、变体和路由是否仍由平台决定？

## 路由

先判断用户意图，进入一个模式。不要混用模式。

| 用户意图 | 模式 | 做法 |
|---|---|---|
| “帮我接入一个新触点” | 新增模式 | 按展示方式选择 CMS 区块或直接 H5 数据链路 |
| “帮我看这个触点为什么不展示” | 排障模式 | 先读 `references/touchpoint-troubleshooting.md`，按分层流程查代码 / evaluate / GB / admin / CMS / Superset |
| “帮我写接入文档 / skill.md” | 文档模式 | 先补齐信息卡，再按“产出契约”生成中文文档 |
| “review 这个触点接入” | 审查模式 | 只读检查，不擅自重构；输出问题清单 |
| “给触点增加动态文案 / N-D 变量” | 动态文案模式 | 必读 `references/dynamic-copy-runtime.md`，先分开文案取值、业务准入和疲劳度 |
| “配频控 / 关闭冷却 / 每天展示 N 次” | 疲劳度模式 | 先查 admin/GB/service 已有策略；平台缺能力时补平台，不在 App/SDK 新建本地状态机 |
| “为了这个触点要改 SDK / review SDK MR” | SDK 变更审查 | 必读 `references/sdk-core-change-guardrails.md`；区分展示扩展、公共能力变更和业务决策，按影响审兼容性 |
| “把 home_page_device_badge 当模板” | 样例模式 | 必读完整样例文档，提炼差异点，不照抄旧命名 |

## 新增模式：按展示方式选择流程

- SDK 渲染 CMS 素材、`FEATURE_GATE` 原生 child 或普通 `DEEP_LINK`：读 [CMS 区块触点流程](references/cms-block-touchpoint-workflow.md)，保留宿主、Admin API、staging 发布和埋点验收规则。
- CMS 文案含 `{{variable}}`、`runtimeText` 或 `runtimeBundle`：再读 [动态文案 runtime 契约](references/dynamic-copy-runtime.md)，并确认目标 App 实际依赖已包含该能力；未合入或未固定 revision 不算可用。
- `slotType=paywall` / `OPEN_PAYWALL` 直接打开 H5：读 [H5 Paywall 数据链路](references/h5-paywall-data-flow.md)，先确认商品、属性和路由，再取得真实 `cms-content`，最后进入 H5 开发。
- 参考 VicoHome 首绑：按需读 [首绑实施案例](references/vicohome-first-bind-case.md)。案例中的商品、条件和实验结构不是其他需求的默认值。

直接 H5 模式的输入卡还应记录：

```yaml
slotType: paywall
runtime: h5
sceneKey: <已确认的页面方案>
segmentKey: <Experience 选定的内容分群>
cmsCollection: paywalls
reviewPolicy: <沿用当前任务已约定的 review 节点>
```

GB 返回 Admin 已注册的 Experience key；Admin 快照保存 scene / segment / Paywall 映射；CMS 管商品和 Offer；H5 场景逻辑选品和导航，价格、购买、Bridge 复用框架。先核对 PE 现有属性及口径，缺失时才扩展；不要为了每个 PRD 新写一套分类代码。

用户要求逐阶段 review 时，交付该阶段可检查的配置草案、diff、接口结果或页面预览，取得该节点确认后再推进依赖工作；已有明确授权不重复询问。两个 Skill 可在同一任务中依次使用，不要求另建 Agent 窗口。

## 审查模式

审查触点接入时，按严重程度输出 findings。

### 阻断级问题

- SDK slot 常量值与 admin `slug` 不一致。
- 设备级触点没有传 sn。
- `FEATURE_GATE` 没有 child 视图，或 child 视图表示的是 locked 状态而不是 unlocked 状态。
- 页面已销毁、容器不存在或缺必要请求参数时没有 fail-closed，导致无法安全展示或发出未限定请求。
- GB feature id 与 admin 不一致。
- GB value 找不到 admin `variantKey`。
- `OPEN_PAYWALL` 变体没有 `paywallId`。
- 一个 GB/Admin Experience 对应多个 Banner `preset`/creative 或多个 `paywallId`，由 App/SDK/runtime 数据在 evaluate 后二次选择。
- SDK、CMS runtime 或宿主通过 `creativeKey`、`paywallIdsByCreative`、动态变量或 action map 覆盖 engagement-service 下发的 `actionType`/`paywallId`。
- `DEEP_LINK` 变体没有 `deepLink`。
- PROACTIVE 在 App、Flutter module 或 SDK 新建 `maxPerSession`、`maxPerDay`、dismiss cooldown、永久关闭或素材切换冷却，与 engagement-service fatigue 并行判定。
- 已有/应有 admin fatigue variant 却没有配 `gbFeatureIdFatigue`，或 GB fatigue value 找不到 admin fatigue `variantKey`。
- 新增、暴露或使用 `presentation.shouldPresent` 一类宿主展示否决回调，使 service 下发可展示结果后业务方仍能隐藏触点。
- 在 SDK 公共 API、`EngagementContext`、evaluate/render/cache/network 或生命周期中硬编码某个 App/页面的业务数据口径、endpoint、准入/频控/变体/路由判断；通用展示输入和能力扩展按变更门禁评审，不因首次用于一个业务就阻断。
- 改变 `evaluated/impressed/cta_clicked/dismissed/converted` 的触发条件、顺序、次数或归因字段，却没有平台契约、全调用方影响分析和兼容性回归证据。
- 绕过既有机制另建平行 evaluate、渲染调度器、事件或状态机；沿现有 registry 增加 builder、扩展通用 formatter 不属于此项。
- 会员资格、实验准入、曝光上限或关闭冷却放进动态文案解析、Controller、Widget 或宿主本地 policy。
- 动态文案绕过 CMS `runtimeBundle`/宿主网络适配，在通用 SDK 硬编码业务 API、N/D 业务计算口径或 Library 准入/变体判断；CMS 声明的变量名和通用 formatter 不属于此项。
- 动态变量的 `allowedTargets` 包含 `blockType`、`preset`、`creativeKey`、`cmsSlug`、`actionType`、`actionContext`、`paywallId`、`solutionId` 等投放、样式或路由控制字段。
- 缺 `runtimeInputs` 时猜测全局 App 状态或发出未限定范围的业务请求。
- 动态文案、用户值或鉴权 API 结果被写入跨账号/语言/页面的 SDK 快照缓存。
- 目标 App 没有固定包含动态文案能力的 SDK revision，却已按该能力配置 CMS 或声称可上线。
- 发布预检有阻断项仍准备上线。
- 新增 `EngagementHostSpm` / `host_spm` 旧链路。

### 警告级问题

- 没有集中集成封装，调用点散落。
- 不符合资格时只隐藏不清空 container，cell 复用可能残留。
- 长文案 / 小屏 / RTL / 多语言没有布局保护。
- 语言切换没有处理 SDK cache。
- 静态触点因动态文案改造而多发业务请求，或没有回归“无 `runtimeBundle` 时不增加 runtime network”。
- 动态取值没有超时、失效 epoch、缺值收起和请求去重测试，或没有记录请求率/错误率/延迟的验收口径。
- SDK 变更没有按受影响范围提供兼容性、跨端一致性、回滚或 SDK owner 评审证据；仅有首个消费者、业务命名或与业务同 MR 不单独构成问题。
- 没有记录通用看板的 `slot_name` / `paywall_id` 筛选口径，或误把 Dashboard 527 当作 `OPEN_PAYWALL` 的完整验收看板。
- 文档没有记录 GB/admin/CMS 三方标识符。

输出格式：

```markdown
### Engagement 触点接入审查
- 范围: <文件 / 平台 / 链接>
🔴 <文件或平台>: <问题> — <影响> — <建议>
⚠️ <文件或平台>: <问题> — <建议>
```

## 排障模式

先读 `references/touchpoint-troubleshooting.md`，按“宿主是否发起 evaluate -> engagement evaluate 结果 -> GB/admin/CMS 配置 -> SDK 渲染曝光 -> CTA/converted -> Superset 数据”逐层缩小范围。不要只看一个平台页面后直接下结论。

| 症状 | 优先排查 |
|---|---|
| Superset 没有 evaluated | 宿主技术 gate、slotName 拼错、SDK 未初始化、网关路径缺 `/en`、请求被网络层拦截 |
| evaluated 有但 data 为空 | GB value 与 admin variant drift、cmsSlug 不存在、snapshot 未发布、GB env 没规则 |
| evaluate 命中但动态 block 被收起 | `runtimeInputs` 缺失、CMS `allowedTargets`/JSON path 不匹配、业务 API 失败/超时、结果过期 |
| 动态文案出现旧值或占位符 | resolved content 被缓存、失败时回退旧文案、账号/语言/页面 epoch 没有失效 |
| service 返回 show 但 App 仍隐藏 | 宿主展示否决回调或本地 policy 重复准入/频控；删除第二决策源并回到 PE/GB/fatigue |
| 所有用户都 locked | 设备级没传 sn、PE 属性缺失、GB rule 条件没命中、default 指向 locked |
| VIP 设备仍显示黄色 Get | `locked=false` 变体没命中、sn 错、宿主原生 child 视图未绑定或被清空 |
| 非 VIP 设备显示绿色 shield | SDK cache 未按 `(slot, sn)` 命中、cell 复用残留、宿主自己绕过 locked |
| 点击没反应 | actionType / actionConfig 不匹配、`onOpenAction` 分支漏、deepLink 不可路由 |
| converted 缺失 | Paywall success 没 echo attribution、host 未调 `notifyConverted`、slot/sn 不一致 |
| impressed 低 | 容器未入屏、type mismatch、渲染失败、图片资源失败、view 被布局挤出 |
| 语言切换后文案旧 | SDK cache key 不含语言，宿主未 invalidate |

## 文档模式产出契约

写接入文档时，必须用中文，并包含这些章节：

1. 背景和结论。
2. 标识符表：slug、Experience/Fatigue GB feature、variantKey、cmsSlug、paywallId、preludeEventKey。
3. 宿主 SDK 初始化链路。
4. 宿主 UI 接入链路。
5. admin 配置。
6. GrowthBook 配置。
7. 疲劳度配置和宿主无本地频控说明。
8. 如果有动态文案：`runtimeInputs`、数据源、仅限文案的变量/目标字段、格式化与业务变体边界、禁止控制字段、缺值与缓存策略。
9. 如果修改 SDK：展示扩展或公共能力变更的归属、payload/格式化契约、受影响公共/生命周期方法、与风险相称的兼容及回滚证据。
10. 运行时时序图。
11. 测试清单。
12. 发布和 Superset 验收。
13. 常见故障和排查表。
14. 给其他 AI agent 的最小输入卡。
15. 参考文件和平台链接。

如果要生成项目级 skill，必须包含：

- 中文 frontmatter。
- `## Description` 或 `## 描述`。
- `## 核心心智` 或等价章节。
- `## 使用前必须确认的信息`。
- `## 路由`。
- `## 新增模式`。
- `## 审查模式`。
- `## 排障模式`。
- `## 文档模式产出契约`。
- `## 示例`，且包含反例和正例。

## 规则

1. **不明确就问**：特别是 sn 来源、业务准入属性、Fatigue 策略、动态文案数据源、CTA 目标、preludeEventKey、variantKey 语义。
2. **不凭变量名判断 slot**：以常量值和 admin slug 为准；变量名可能是历史遗留。
3. **不把多个 App 混进一个触点**：同一套代码覆盖 VicoHome / KiwiBit / VicoNature 时，优先拆成 App 级 slug 和 GB feature，不在一个触点里靠 app 条件分流。
4. **不恢复旧 host_spm 链路**：新触点不加 `EngagementHostSpm`。
5. **不增加宿主展示决策**：`shouldPresent` 不是 impressed/converted 一类观察事件，而是会否决服务端结果的第二决策源；新触点和 SDK 基础能力不得引入。页面离开、容器销毁和异步结果过期由订阅取消、生命周期 token 和安全挂载处理，不对业务方开放展示判断。
6. **不混淆技术硬条件和业务准入**：页面没有可挂载容器或缺少发起请求所需参数时，不启动本次接入流程；会员、设备权益、是否有可读录像等稳定准入条件应进 PE/GB，不在 Controller、Widget 或本地 policy 重做。
7. **不在 App/SDK 复制疲劳度**：曝光计数、每日/会话上限、关闭冷却、永久抑制是 Engagement 平台能力；现有策略表达不了就扩展 admin/service，不新建宿主状态机。
8. **动态文案允许展示格式化**：SDK 可按 CMS 模板处理 N/D、单复数、数字/日期/相对时间和同一业务含义的语法分支；不得用这些分支选择业务资格、营销方案、`blockType`、`preset`、`creativeKey`、Experience、CTA、`actionType` 或 `paywallId`。缺值或类型错误按统一技术失败策略处理，不据此改选业务内容或路由。
9. **服务端结果是最终决策**：PE 提供 GB 属性，GrowthBook 返回最终 `variantKey`，Admin Experience 唯一绑定内容和 action，engagement-service 返回最终 Experience/`paywallId`。SDK 只渲染和上报，不使用 `paywallIdsByCreative` 或业务数据二次分流。
10. **允许 SDK 展示扩展，保护业务决策和公共语义**：优先复用已有能力；缺少视觉组件或文案能力时，可在 SDK 新增 builder、resolver 或 formatter，不因首个业务使用就强制移到宿主。涉及公共模型、evaluate/cache/network 或生命周期时，按 SDK guardrail 说明必要性、评审影响并验证兼容性；不得借此内置业务准入、疲劳度、变体或路由决策。
11. **不跳过发布预检**：admin / GB 修改不等于运行时生效。
12. **不混淆 Admin 实例和发布环境**：日常操作使用 production Admin；staging-first 指发布 staging rules package，不是操作 Admin staging 实例。
13. **不回退 UI 写入**：Admin API 失败时报告并停止，禁止自动改用页面表单创建、修改或发布。
14. **不为触点 + Paywall 默认新建看板**：优先复用通用 P0 看板并筛选 `slot_name` / `paywall_id`；只有指标缺口经 owner 确认后才新增。
15. **不把 Superset 链接当成已验证数据**：优先用 `addx:superset` skill 查询 dashboard / chart 数据；如果凭据缺失或未授权，明确说明无法读取数据。
16. **不越界修改 H5 底层**：本 Skill 接通和校验 Paywall 数据，不修改 H5 模板或框架；同一授权任务中的模板需求转入 H5 Paywall 流程，底层修改仍按 owner 评审边界处理。
17. **不扩张范围**：不长篇复制公开 API 语法；审查或写文档时只读，只有新增接入任务才修改相关代码。

## 示例

### Bad

```
用户：在 staging 验证一个新触点
AI：调用 admin_api.mjs staging POST /api/touchpoints。
结果：把“发布 staging rules”误解成“写入 Admin staging 实例”，触点没有进入日常 production Admin 的发布链路。
```

```
用户：参考 home_page_device_badge 接一个新设备触点
AI：只在 cell 里写 EngagementTouchpoint.gate(slotName: "new_badge", ...)
结果：没传 sn，GB 设备权益规则全部 miss，所有设备都显示 locked 文案。
```

```
用户：帮我写触点接入 skill
AI：写一个 80 行英文检查清单，没有记录 admin / GB / 发布 / Superset，也没说明不明确要问什么。
结果：其他 agent 仍然需要重新追代码，无法独立接入。
```

```
用户：触点不展示
AI：直接说是 GB 配错。
结果：实际是宿主页面生命周期 gate 阻止了 evaluate，方向错。
```

```
用户：这个 PROACTIVE Banner 每天最多展示一次，关闭后 7 天再展示。
AI：在 Android、iOS 和 Flutter 分别写本地计数器和冷却时间。
结果：客户端与 engagement-service 出现两个疲劳度判定源，端间不一致，服务端曝光与真实展示也无法对齐。
```

```
用户：文案要展示“今天 N 个录像将在 D 天后过期”。
AI：把 N/D 当成会员准入，在宿主渲染前回调里查 API 并决定是否展示。
结果：文案取值、投放准入和疲劳度被耦合，无法用 PE/GB/Admin 统一运营。
```

```
用户：Library Banner 需要支付后隐藏和每天展示一次。
AI：给 EngagementContext 增加业务字段，在 SDK core 新增展示回调、本地计数和一套新的 evaluate 方法。
结果：单一业务改变所有触点的公共模型、生命周期和性能，并与服务端准入、Fatigue 形成平行实现。
```

```
用户：Snapshot 和 Rolling 使用不同 Banner 与 Paywall，N/D 来自 runtime API。
AI：GB 只返回一个通用 treatment，SDK 再按 N/D 生成 `creativeKey`，通过 `paywallIdsByCreative` 选择 Paywall。
结果：实验不知道最终展示和路由，Experience 与 Paywall 归因断开，通用看板无法准确比较两个变体。
```

### Good

```
用户：在 staging 验证一个新触点
AI：先调用 admin_api.mjs prod POST /api/touchpoints 在 production Admin 创建草稿；用户单独授权后，再通过 production Admin 的 POST /api/publish-orders 生成 staging rules package。全程不使用 Admin UI 表单写入。
```

```
用户：参考 home_page_device_badge 接一个新设备触点
AI：先填信息卡，确认 deviceScope=true、sn=device.serialNumber，把页面/容器/缺参数的技术 gate 与会员/设备资格分开，后者用 PE/GB 属性表达。
再检查 SDK 初始化、slot 常量、集成封装、admin variant、GB feature、发布预检。
最后给出测试和 Superset 验收清单。
```

```
用户：给 PROACTIVE Banner 加动态文案和关闭冷却。
AI：先确认 Experience/Fatigue feature 和 admin fatigue variant，把准入与冷却留在 PE/GB/engagement-service。再根据 `runtimeBundle` 定义变量、`allowedTargets` 和缺值收起，宿主只通过 `runtimeInputs` 提供页面查询参数并复用鉴权网络。验证静态触点不增加 runtime 请求、动态值不进入快照缓存，通用 impressed/dismissed/converted 语义不变。
```

```
用户：Snapshot 和 Rolling 使用不同 Banner 与 Paywall，文案包含 N/D。
AI：把 Snapshot/Rolling 条件建成 PE 属性和 GrowthBook 规则，分别返回不同 Admin Experience；每个 Experience 唯一绑定 CMS Banner 和 `paywallId`。SDK 只把 N/D 替换进该 Banner 的文本字段，不改变 preset、action 或路由，并用通用 P0 看板按 Experience + Paywall 验收。
```

```
用户：这个新触点需要 SDK 没有的能力。
AI：先区分缺少的是展示能力还是业务决策。CMS payload 需要新视觉时，沿 SDK 既有 registry 增加 builder；单复数等由通用 formatter 处理。按实际改动验证组件、旧调用方、事件和路由兼容性，公共模型、网络、缓存和生命周期变更由 SDK owner 审查，不要求先有两个消费者或一律拆 MR。
```

```
用户：Library 卡片文案要随 N 显示 1 video / 2 videos，日期显示 today / tomorrow。
AI：保留 service 选定的 Experience、Banner preset 和 Paywall，在 SDK 通过 CMS 配置的文案分支/formatter 完成单复数与相对时间本地化。检查 locale、时区和日期边界；这些展示分支不判断触点准入、不消费疲劳度，也不改变营销方案或标准事件。
```

```
用户：帮我写触点接入 skill
AI：读取 skill-creator、公司 skill-contribution / tool-skill-creator、cicd-developer 的结构。
产出中文 skill：路由、输入卡、流程、排障、审查、产出契约、反例/正例齐全。
```

## 参考

- 完整样例文档：`references/home-page-device-badge.md`；运维验收见 `references/home-page-device-badge-ops.md`
- PROACTIVE 疲劳度边界与 Library 示例：`references/library-cloud-promo-fatigue.md`；验收见 `references/library-cloud-promo-fatigue-ops.md`
- CMS 动态文案、`runtimeBundle`、`runtimeInputs` 与宿主鉴权取数：`references/dynamic-copy-runtime.md`
- Engagement SDK 公共 API、生命周期和基础能力变更门禁：`references/sdk-core-change-guardrails.md`
- 触点展示异常排障手册：`references/touchpoint-troubleshooting.md`
- iOS 渲染模型：`docs/architecture/sdk-ios/touchpoint-rendering-model.md`
- admin 触点创建 SOP：`docs/architecture/admin/operations/touchpoint-creation.md`
- variant 命名：`docs/architecture/admin/operations/variant-naming.md`
- 系统级创建 Skill 指南：`skill-creator`
- 公司 Skill 贡献流程：`addx:skill-contribution`
- 公司工具 Skill 创建流程：`addx:tool-skill-creator`
- 公司详细 skill 示例：`addx:cicd-developer`
