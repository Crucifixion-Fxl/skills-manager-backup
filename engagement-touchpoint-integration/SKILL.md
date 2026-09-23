---
name: engagement-touchpoint-integration
description: Engagement 触点接入全流程 Skill。用于新增、审查或编写触点接入文档，覆盖宿主 App 接 EngagementSDK、engagement-admin 触点录入、GrowthBook 实验配置、发布预检、设备级 sn 传递、Superset 线上观测和排障。当用户提到 home_page_device_badge、触点接入、slot 接入、FEATURE_GATE / PROACTIVE、或希望其他 AI agent 按文档快速接入新触点时触发。
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

完整样例文档先读：`references/home-page-device-badge.md`、`references/cloud-service-voucher-flutter.md`、`references/library-cloud-promo-fatigue.md`、`references/playback-cloud-promo.md`。排障先读 `references/touchpoint-troubleshooting.md`；运维验收读 `*-ops.md`。

## 核心心智

触点不是单一代码改动，而是四个系统的一致性工程：

| 层 | 负责什么 | 典型入口 |
|---|---|---|
| 宿主 App | SDK 初始化、slot 容器、宿主硬过滤、sn 传递、CTA 路由、埋点适配器 | g0-ios / g0-android / Flutter 宿主 |
| marketing-cms | 触点素材、可运营模板、媒体资源、多语言内容、`cmsSlug` | `https://marketing-cms-staging-us.addx.live/admin/collections/engagements` |
| engagement-admin | 触点注册、variant 绑定、actionConfig、fatigue、规则发布 | `https://engagement-admin.addx.live` |
| GrowthBook | 受众、实验、强制规则，返回 `variantKey` | `https://us-ab-management.addx.live` |
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
| admin `cmsSlug` | 决定运行时 `solution_id`，指向 CMS 内容 |
| `locked=true` | `FEATURE_GATE` 展示 SDK 渲染的 CMS 内容 |
| `locked=false` | `FEATURE_GATE` 透传宿主原生 child 视图 |
| 设备级触点 | 必须传 `EngagementContext(sn:)`，并在状态查询、点击、转化路径继续传同一个 sn |
| `host_spm` | 当前宿主不再新增，不要恢复 `EngagementHostSpm` 常量 |

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
hardGates:
  - <宿主侧硬过滤条件>
ui:
  locked: <CMS blockType / 文案 / 图片 / 样式>
  unlocked: <FEATURE_GATE 的原生 child 视图，可空>
action:
  type: OPEN_PAYWALL | DEEP_LINK | DISMISS | NONE
  paywallId: <OPEN_PAYWALL 必填>
  deepLink: <DEEP_LINK 必填>
growthbook:
  featureId: engagement_<slug>_experience
  variants:
    - variantKey: <GB 返回值>
      locked: true | false
      cmsSlug: <CMS 内容 slug>
preludeEventKey: <父容器曝光事件，作为数仓漏斗分母>
observability:
  supersetDashboard: <通用 P0；按 slotName + paywallId 筛选>
```

### 必问用户的问题

只有这些问题不能从本地代码和平台页面确认时才问：

1. 这个触点是设备级还是用户级？如果是设备级，宿主哪个字段是权威 SN？
2. 这个触点面向哪个 App？如果同一套代码要覆盖 VicoHome / KiwiBit / VicoNature，是否需要按 App 拆成不同 slug？
3. 哪些条件属于宿主硬过滤，必须在调 SDK 前过滤？
4. `locked=false` 时是否需要宿主原生 child 视图？如果需要，设计稿或现有组件在哪里？
5. CTA 是 `OPEN_PAYWALL` 还是 `DEEP_LINK`？目标 `paywallId` / `deepLink` 是什么？
6. 哪个事件是触点出现机会的 `preludeEventKey`？`OPEN_PAYWALL` 默认复用通用 P0 看板，仅在它无法表达目标指标时才确认额外的业务看板。

## 路由

先判断用户意图，进入一个模式。不要混用模式。

| 用户意图 | 模式 | 做法 |
|---|---|---|
| “帮我接入一个新触点” | 新增模式 | 跑完整 9 阶段流程 |
| “帮我看这个触点为什么不展示” | 排障模式 | 先读 `references/touchpoint-troubleshooting.md`，按分层流程查代码 / evaluate / GB / admin / CMS / Superset |
| “帮我写接入文档 / skill.md” | 文档模式 | 先补齐信息卡，再按“产出契约”生成中文文档 |
| “review 这个触点接入” | 审查模式 | 只读检查，不擅自重构；输出问题清单 |
| “把 home_page_device_badge 当模板” | 样例模式 | 必读完整样例文档，提炼差异点，不照抄旧命名 |

## 新增模式：9 阶段流程

### Phase 0：读上下文

必须先读：

1. 当前触点需求或用户给的链接。
2. `references/home-page-device-badge.md`。
3. 宿主 App 中已有相邻触点接入代码。
4. marketing-cms 中目标 `cmsSlug` 的内容和 blockType。
5. `docs/architecture/sdk-ios/touchpoint-rendering-model.md` 或对应端 SDK 渲染模型。
6. `docs/architecture/admin/operations/touchpoint-creation.md`。
7. `docs/architecture/admin/operations/variant-naming.md`。

如果是 iOS，优先参考这些文件：

| 目的 | 文件 |
|---|---|
| SDK 初始化 | g0-ios: `AddxAi/A4xInitAppConfig+AppDelegate.swift` |
| 网络适配 | g0-ios: `AddxAi/Classes/Engagement/AppEngagementNetworkProvider.swift` |
| slot 常量 | g0-ios: `BaseUI/BaseUI/Engagement/EngagementSlotIdentifiers.swift` |
| 设备卡角标接入 | g0-ios: `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/Engagement/DeviceCardBadgeIntegration.swift` |
| 设备硬过滤 | g0-ios: `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/Engagement/DeviceBean+EngagementEligibility.swift` |
| 容器布局 | g0-ios: `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/View/SubView/LiveTopBarView.swift` |
| 原生 child 视图 | g0-ios: `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/Engagement/VipDeviceBadgeView.swift` |
| Snowplow adapter | g0-ios: `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/Engagement/SnowplowEngagementTracker.swift` |

### Phase 1：定义触点信息卡

输出并让用户确认或自行从平台验证：

```yaml
slug:
type:
deviceScope:
hostLocation:
hardGates:
lockedUi:
unlockedUi:
cms:
  slug:
  blockType:
  templateExists:
  sdkBuilderExists:
actionType:
paywallId:
deepLink:
gbFeatureIdExperience:
adminRelease:
observability:
  supersetDashboard: <通用 P0；按 slotName + paywallId 筛选>
```

如果 `slug`、`cms.slug`、`cms.blockType`、`gbFeatureIdExperience` 或 `variantKey` 不一致，先停下来修正，不进入代码。

slug 命名规则：

- 必须全小写下划线。
- `slug` 同时是 admin 主键、SDK `slotName`、默认 GB feature 的核心部分，不能为了复用代码而随意合并多个 App 的业务。
- VicoHome、KiwiBit、VicoNature 这类 App 即使底层代码基本通用，也优先拆成不同触点配置，例如 `home_page_device_badge`、`kb_home_page_device_badge`、`vn_home_page_device_badge`。
- 不推荐一个触点或一个 GB feature 里再用 app 条件区分三套 App；这种配置会让 rule、variant、数据分析都混在一起，后期很难理解和维护。`https://us-ab-management.addx.live/features/home_promo_banner` 是这种混合配置的反例。
- 拆成 App 级触点后，每个 App 可以独立配置 CMS 内容、admin variant、GrowthBook rule、发布节奏和回滚方案；数据验收优先在通用看板里按各自的 `slot_name` / `paywall_id` 筛选，不默认新建看板。

### Phase 2：宿主 SDK 基础接入检查

检查宿主是否已完成一次性 SDK 初始化：

- `EngagementSDK.configure(...)` 只在 App 启动配置一次。
- `networkProvider` 能把 SDK path 转成真实网关路径，例如 iOS 需要 `/en` 前缀。
- `onOpenAction` 同时覆盖 `DEEP_LINK` 和 `OPEN_PAYWALL`。
- `OPEN_PAYWALL` 透传 referrer attribution：`slotName`、`solutionId`、`experimentKey`、`variationId`、`experienceKey`、`fatigueKey`、设备 sn。
- paywall payment success 能回调 `EngagementTouchpoint.notifyConverted(...)`。
- `tracker` 已接 `touchpoint_evaluated / impressed / cta_clicked / dismissed / converted`。

没有这些基础能力时，先补基础能力，不要只接单个触点 UI。

### Phase 3：marketing-cms 素材与模板准备

engagement-admin 配置前必须先确认 CMS 内容存在。admin 的 `cmsSlug` 只是绑定引用，真正的素材、文案、图片和 blockType 在 marketing-cms 里维护。

检查顺序：

1. 在 `https://marketing-cms-staging-us.addx.live/admin/collections/engagements` 搜目标 `cmsSlug`；新触点只用 `Engagement 触点（engagements）`，`Promotions` 仅作存量兼容和迁移核对。
2. 确认内容已创建、Published、多语言进度符合上线要求。
3. 记录内容组件的 `blockType` 和关键 payload 字段。
4. 确认 marketing-cms 代码里已经定义这个模板 / block；如果平台没有对应模板，先改 marketing-cms。
5. 确认目标宿主端的 engagement-sdk 已注册同名 builder；如果 SDK 不支持该 `blockType`，先改 SDK，再接宿主触点。
6. 只有 CMS 内容和 SDK builder 都存在后，才进入 engagement-admin 绑定 `cmsSlug`。

`home_page_device_badge` 当前样例：

```yaml
cmsSlug: device_card_badge_awareness
collection: engagements
blockType: badge
payload:
  text: Get
  icon: Protection@3x-1.png
  bgColor: "#FFF8E3"
  textColor: "#6A3B07"
  borderColor: "#FFE69E"
```

CMS 平台当前可选模板包括 `Image`、`Badge`、`Icon Card`、`Chip Carousel`、`Text Banner`、`Free License Page`、`About Page`、`Hero Banner`、`Inline Banner`、`Card Banner`。不要只看 CMS UI 已有模板；还必须查目标端 SDK 是否支持同名 `blockType`。

当前代码里已看到的 SDK builder 注册情况：

| blockType | iOS | Android | Flutter |
|---|---|---|---|
| `image` | 支持 | 支持 | 支持 |
| `badge` | 支持 | 支持 | 支持 |
| `iconCard` | 支持 | 支持 | 支持 |
| `chipCarousel` | 支持 | 支持 | 支持 |
| `textBanner` | 支持 | 未在当前 checkout 看到 | 支持 |
| `heroBanner` | 支持 | 支持 | 未在当前 checkout 看到 |
| `inlineBanner` | 支持 | 支持 | 未在当前 checkout 看到 |
| `cardBanner` | 未在当前 checkout 看到 | 未在当前 checkout 看到 | 支持 |

这张表只代表当前本地 checkout；接入新触点时必须以目标 App 实际依赖的 SDK commit 为准。

**H5 Paywall 边界**：本 Skill 只选择和校验已有 `paywallId`，不创建或修改 H5 Paywall；没有匹配项时停止触点侧实现，读 `references/h5-paywall-boundary.md` 并转交 H5 流程，底层变更必须先提 issue 给 Engagement owner 评审。

### Phase 4：宿主 UI 接入

按触点类型选择 API：

| type | API | child | 说明 |
|---|---|---|---|
| `FEATURE_GATE` | `EngagementTouchpoint.gate(...)` | 必传 | `locked=false` 时显示宿主 child 视图 |
| `PROACTIVE` | `EngagementTouchpoint.proactive(...)` | 不传 | SDK 渲染 CMS 内容或留空 |

设备级触点必须传：

```swift
EngagementContext(sn: device.serialNumber)
```

宿主集成封装应统一处理：

- 宿主硬过滤：不符合条件时不调用 SDK。
- 清理：miss / ineligible 时清空 container。
- 布局：预留空间，防止长文案或 cell 复用把触点挤出屏幕。
- 缓存：语言切换、账号切换、权益变化时按需 invalidate。
- 重入：cell 重复配置风暴不能导致无意义 N+1 evaluate。
- 设备级 attribution：state / trackCta / notifyConverted 都传同一个 sn。

### Phase 5：engagement-admin 配置

先区分 **Admin 部署实例** 和 **规则发布目标环境**，两者不是一回事：

| 概念 | 正确入口 | 何时使用 |
|---|---|---|
| 日常触点管理 / 发布 | `https://engagement-admin.addx.live`，helper 参数为 `prod` | 创建、修改、查重、创建 PublishOrder；默认入口 |
| Admin 应用自身的预发实例 | `https://engagement-admin-staging.addx.live`，helper 参数为 `staging` | 仅验证 engagement-admin 新版本；必须由用户明确指定 |
| staging rules package | 从 production Admin 创建 PublishOrder 后生成 | 宿主 staging 联调；不是去 Admin staging 实例创建触点 |
| production rules package | staging 验证通过后，经 production Admin 审批推进 | 需要单独、明确的 prod 发布授权 |

除非用户明确要求验证 **engagement-admin 应用自身的预发版本**，所有触点创建、修改、查重和发布单操作一律使用：

```bash
node scripts/admin_api.mjs prod <METHOD> <PATH> ...
```

`admin_api.mjs` 的 `prod` 表示 production Admin 主机，不表示请求会直接发布 production rules。首次创建 PublishOrder 只生成 staging rules package。

所有 engagement-admin 写操作必须走 API。禁止通过 UI 表单创建或修改触点、变体、发布单；API 认证或调用失败时应报告真实阻塞并停止，不得静默回退到 UI 操作。UI 仅用于首次 OAuth 和写后只读复核。当前可用 API 包括：

| 操作 | 方法 | 路径 |
|---|---|---|
| 查询触点 | `GET` | `/api/touchpoints?q=<slug>` |
| 创建触点 | `POST` | `/api/touchpoints` |
| 更新触点 | `PATCH` | `/api/touchpoints/<slug>` |
| 新增 experience variant | `POST` | `/api/touchpoints/<slug>/experience-variants` |
| 更新 experience variant | `PATCH` | `/api/touchpoints/<slug>/experience-variants/<variantKey>` |
| 新增 fatigue variant | `POST` | `/api/touchpoints/<slug>/fatigue-variants` |
| 重跑 GB skeleton provisioning | `POST` | `/api/touchpoints/<slug>/gb-provision` |
| 创建发布单 | `POST` | `/api/publish-orders` |

Admin CLI Bearer 刻意不具备 prod 推进权限：`approve-prod`、
`refresh-approval`、`rollback`、审批 callback 和审计导出均不在 helper 的
API allowlist 内。生产发布必须由用户在 production Admin 中单独确认并走浏览器
会话/飞书审批流程；不得把触点 API 授权扩大成生产发布授权。

API 创建或修改后的 UI 只读复核是必做步骤：

- 打开 engagement-admin 触点详情页 `/touchpoints/<slug>`。
- 核对 `slug`、`type`、`preludeEventKey`、`gbFeatureIdExperience`。
- 核对每个 experience variant 的 `variantKey`、`cmsSlug`、`slotType`、`locked`、`isSystem`、`sortOrder`。
- 核对 `actionType` 和 `actionConfig` 是否匹配，例如 `OPEN_PAYWALL` 必须有正确 `paywallId`，`DEEP_LINK` 必须有正确链接。
- `FEATURE_GATE` 必须确认系统 `unlocked` 变体存在且不可编辑，运营变体是 `locked=true`。
- `PROACTIVE` 必须确认 fatigue variant 存在，且 fatigue JSON 符合打扰频控预期。
- 在 Releases 创建发布单前，再看一次 pending diff / snapshot 预检，避免配错后发布到 staging 再返工。

鉴权与 API 自动化：

- Admin 网页使用 Feishu OAuth 后由自身签发的 httpOnly `admin_session`；helper 使用 Admin 专用 CLI Bearer Token，不得与 troubleshooting JWT 混用。
- API 操作使用 `scripts/admin_api.mjs`：首次通过系统默认浏览器和 `127.0.0.1` PKCE 回调完成授权，后续复用本地 `0600` Token 缓存；Cookie、OAuth code、Token 不输出到 shell 或错误日志。
- 默认浏览器只负责同源授权；触点读取和写入仍由 helper 直接请求 `/api/...`。不得因为授权失败改走 Admin UI 表单。
- 写操作前必须 GET 查重、展示完整 payload 并取得明确授权；写后再次 GET 逐字段复核。
- 自动化显式带 `x-engagement-admin-operator`；helper 使用 CLI 授权响应里的已验证用户身份。
- 完整用法、安全边界和错误处理见 `references/admin-api-auth.md`。执行 Admin API 任务时必须先读该文件。

admin 必查字段：

```yaml
slug: <等于 SDK slotName>
type: FEATURE_GATE | PROACTIVE
preludeEventKey: <父容器曝光事件>
gbFeatureIdExperience: engagement_<slug>_experience
cms:
  slug: <marketing-cms engagements 内容唯一标识>
  blockType: <SDK 已支持的 blockType>
experienceVariants:
  - variantKey: unlocked
    locked: false
    isSystem: true
    owner: FEATURE_GATE 自动创建，不能人工编辑
  - variantKey: <GB value>
    cmsSlug: <marketing-cms engagements 内容唯一标识>
    locked: true
    actionType: OPEN_PAYWALL | DEEP_LINK
    actionConfig:
      paywallId: <OPEN_PAYWALL>
      deepLink: <DEEP_LINK>
fatigueVariants: <PROACTIVE 必填，FEATURE_GATE 不需要>
```

规则：

- `FEATURE_GATE` 的 `locked` 是体验分支的关键，不要依赖宿主自己判断权益。
- `FEATURE_GATE` 创建时 admin 自动插入系统 `unlocked` 变体，作为 `locked=false` 的宿主 child 透传分支。
- 运营新增的 experience variant 固定是 `locked=true`，必须有可渲染 CMS 内容。
- GrowthBook 可以返回 `unlocked`，但不要尝试在 admin 再创建第二个 `locked=false` 变体。
- `PROACTIVE` 必须配置 fatigue，否则可能无限打扰。
- `cmsSlug` / `paywallId` 应从 CMS 下拉或代理确认，不手敲未知值；`cmsSlug` 指向的内容必须已在 marketing-cms 发布，且目标端 SDK 支持对应 `blockType`。

发布单和环境流转：

- 保存 touchpoint / variant 只是在 admin DB 留草稿或配置，不会自动进入运行时。
- 必须在 Releases 创建发布单，发布单会抓取当前 active 触点配置，组装 `rules_package` snapshot，跑 snapshot validation、GB drift preflight、NO_CHANGES gate。
- 创建发布单成功后会写入 staging rules package，发布单状态通常是 `staging_ok`；这时 staging 环境已经能拉到这份规则。
- staging 测试必须覆盖宿主实机、CMS 内容、GB 规则、CTA、埋点、Superset 漏斗，不要只看 admin 页面状态。
- 只有 staging 完全验证通过，并且 prod 依赖都齐了，才允许走 prod 发布。
- prod 依赖包括：CMS 内容已同步到 prod 可读环境、Paywall / deeplink 目标在 prod 可用、GrowthBook prod rule 已配置、宿主端版本已覆盖目标用户、回滚方案明确。
- 当前不要假设 prod 已有真实飞书审批闭环；如果 deployment 还未接真实飞书审批，prod 发布必须按人工审批 / owner 确认执行。
- 未来 prod 发布一定要接入飞书审批；接入后 `approve-prod` 应进入 `pending_prod_approval`，审批通过后才进入 `prod_ok`，审批拒绝或取消应进入 `failed`。

### Phase 6：GrowthBook 配置

GrowthBook 变更必须 staging-first：

1. 新增触点或修改触点实验配置时，先在 GrowthBook `staging` 环境新增 / 修改 rule。
2. staging 规则、admin release、宿主实机和埋点都验证通过后，才把同等语义配置推进到 `production`。
3. 如果使用 GrowthBook API 改规则，必须先读取现有 feature rules，再写回完整 rules 数组；GB API 的环境规则更新不是 patch 单条 rule，而是替换整个环境 rules。
4. prod 修改前必须确认 prod 依赖：CMS 内容、Paywall / deeplink、PE attribute、宿主版本、admin 发布单、回滚方案都已就绪。

GrowthBook 必查：

| 检查项 | 要求 |
|---|---|
| feature id | 与 admin `gbFeatureIdExperience` 一致，默认 `engagement_<slug>_experience` |
| value type | string |
| rule value | 每个 force rule / experiment variation 返回值都是 admin 已存在的 `variantKey` |
| default value | 语义明确：空串代表不展示，某个 variantKey 代表兜底展示 |
| environment | 先 staging，后 production；不要只配 prod |
| 设备属性 | 设备级规则使用的属性必须能通过 `(userId, sn)` 解析出来 |

如果产品需求里的条件在 GrowthBook attributes 里找不到，不要在规则里硬写不存在的字段。按下面流程新增 PE attribute：

1. 在 personalization-engine 仓库的 `schema.yaml` 定义字段，仓库地址：`https://gitlab.addx.ai/services/personalization-engine`。确认 `group`、`entity_type`、`required_context`、`type`、`description`。
2. 需要暴露给 GrowthBook 条件时，字段必须标记 `ab_eligible: true`，并补 `ab_description`。
3. 如果是已有 source 能提供的字段，只改 schema；如果是新 source 或非通用逻辑，补 personalization-engine loader / client / tests。
4. 合入 personalization-engine 的 staging 分支并部署 staging。PE 的 GrowthBook attribute sync 会把 scalar 且 `ab_eligible: true` 的字段注册到 GrowthBook attributes；`map<>` 这类 GB 无 datatype 的字段不能按普通 attribute 同步。
5. 在 GrowthBook staging attributes 页面确认新字段已出现，再用它配置 rule。
6. staging 分支上做黑盒验证，确认实际用户和设备能命中预期 rule，再推进 prod。

两个常见设备属性的赋值链路：

| attribute | PE group | 赋值来源 | 关键语义 |
|---|---|---|---|
| `vipCovered` | `device_entitlements` | PE 直查 camera DB：`user_tier_device` join `user_vip` | `userId + sn` 对应设备存在当前有效的付费 VIP，条件包括 `tier_id % 10 != 0`、`order_id > 0`、`effective_time <= now`、`end_time > now` |
| `activeFlTierId` | `device_fl` | PE 调 iot-service `/cluster-api/free-license/device-info` | iot 基于 `user_device_free_tier` 当前 active 记录选择 tier，例如 `1001/1002/1003/1005/1006/1007/1008`；无有效 FL 时为 `0`，PE 只透传 iot 计算结果 |

staging 命中验证建议：

1. 用 qa-tools 创建测试账号和测试设备，必要时绑定 mock device 或设置 free tier。
2. 调 staging API `/account/login` 获取 bearer token。
3. 调 `/device/listuserdevices/v4` 查看账号下测试设备，确认目标 `sn`。
4. 调 `/en/engagement/v1/touchpoints/evaluate`，body 带 `slugs`、`sn`、`language`、`app`，检查返回 variant / experience 是否等于预期。
5. 如果 evaluate 偶发返回 `-1024 ACCOUNT_GET_KICKED`，重新 login 后重试。
6. 命中不符合预期时，按顺序查：请求是否带同一个 sn、用户是否有目标属性、PE attribute 是否可查、GB staging rule 顺序 / default、admin release snapshot 是否最新。

可参考 PE issue 的 AI agent 黑盒验证步骤：`https://gitlab.addx.ai/services/personalization-engine/-/issues/58#note_336668`。

`home_page_device_badge` 的关键经验：

- 设备级权益属性如 `vipCovered`、`activeFlTierId` 依赖 sn。
- 不传 sn 时 GB 条件看到 missing attribute，常见结果是全量落入默认 locked 分支。

### Phase 7：发布

发布必须通过 engagement-admin PublishOrder，不要只改 GB 或 admin DB 后认为线上已生效。

状态口径：

| 状态 | 含义 |
|---|---|
| `pending_staging` | 发布单正在组装 / 上传 staging snapshot |
| `staging_ok` | staging rules package 已生成，staging 可测试 |
| `pending_prod_approval` | prod 发布等待审批；未来接入飞书审批后应停在这里 |
| `prod_ok` | prod rules package 已生成，prod 生效 |
| `failed` | 校验、GB drift、审批、S3 上传等任一环节失败 |
| `cancelled` | 未进入 prod 的发布单被取消 |

发布前看三类 gate：

| gate | 失败含义 |
|---|---|
| snapshot validation | admin 配置本身不完整，如无变体、actionConfig 无效、cmsSlug 空 |
| GB 漂移预检 | GB 返回值与 admin variantKey 不一致，或环境没有规则 |
| NO_CHANGES | 本次 snapshot 与上次 staging 完全一致，没有实际变更 |

发布后等待 engagement-service poll 新 rules.json，再做 staging 实机验证。

### Phase 8：测试

最低测试清单：

| 层 | 要测什么 |
|---|---|
| 宿主单测 | ineligible 不调用 SDK，container 被清空，布局约束恢复 |
| 宿主单测 | eligible 传正确 slotName 和 sn |
| SDK / 集成 | `locked=true` 渲染 CMS，`locked=false` 渲染 child 视图 |
| CTA | `DEEP_LINK` / `OPEN_PAYWALL` 路由参数完整 |
| conversion | payment success 后 `notifyConverted` 带完整 attribution |
| 埋点 | 5 事件带 `slot_name`、`solution_id`、`experience_key`、`variation_id`、设备级 `device_sn`；接入完成后用 `addx:tracking-lifecycle` 到埋点管理平台 `/check/dashboard/` 生成配置，App 扫码验证触点埋点已发布且字段正确 |
| 回归 | cell 复用、语言切换、长设备名、权益变化 |

### Phase 9：线上观测

优先使用 `addx:superset` skill 查询看板和 chart 数据。agent 在具备 `SUPERSET_URL`、`SUPERSET_USERNAME`、`SUPERSET_PASSWORD` 时，可以通过 Superset REST API 获取 dashboard charts、chart query_context，并调用 `/api/v1/chart/data` 或 SQL Lab 查询真实图表数据。如果这些变量保存在 `~/.zshrc`，脚本里要显式 `source ~/.zshrc` 后再登录 Superset，避免非交互 shell 没加载配置导致误判。

`OPEN_PAYWALL` 默认使用 `https://superset-us.addx.live/superset/dashboard/paywall-p0-touchpoint-to-paywall-health/`，按业务触点 `slot_name` 和目标 `paywall_id` 筛选触点机会、evaluate、曝光、点击、Paywall 打开和支付结果；接入通用埋点契约后不重复建设业务看板。
Dashboard 527（`https://superset-us.addx.live/superset/dashboard/527/`）仅用于 SDK 基础链路排障。`DEEP_LINK` 等非 Paywall 结果优先使用已有领域看板；确有指标或维度缺口时，先记录缺口并让 owner 确认，再申请新看板。

Superset 重点看：

- `touchpoint_evaluated` 是否有流量。
- `eval_result` 分布是否符合预期。
- `touchpoint_impressed / evaluated` 是否接近预期。
- `touchpoint_cta_clicked / impressed` 是否异常低。
- `touchpoint_converted / cta_clicked` 是否能闭环。
- 设备级触点是否有 `device_sn`。
- 实验维度是否有 `experience_key` / `variation_id`。

如果当前环境没有可用 Superset 凭据或返回 `Not authorized`，agent 必须明确说明无法读取图表数据，并让用户补齐 Superset 登录配置；不要把“页面链接可打开”当成数据已验证。

## 审查模式

审查触点接入时，按严重程度输出 findings。

### 阻断级问题

- SDK slot 常量值与 admin `slug` 不一致。
- 设备级触点没有传 sn。
- `FEATURE_GATE` 没有 child 视图，或 child 视图表示的是 locked 状态而不是 unlocked 状态。
- 宿主硬过滤缺失，导致不可能展示的设备仍打到 SDK。
- GB feature id 与 admin 不一致。
- GB value 找不到 admin `variantKey`。
- `OPEN_PAYWALL` 变体没有 `paywallId`。
- `DEEP_LINK` 变体没有 `deepLink`。
- 发布预检有阻断项仍准备上线。
- 新增 `EngagementHostSpm` / `host_spm` 旧链路。

### 警告级问题

- 没有集中集成封装，调用点散落。
- 不符合资格时只隐藏不清空 container，cell 复用可能残留。
- 长文案 / 小屏 / RTL / 多语言没有布局保护。
- 语言切换没有处理 SDK cache。
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
| Superset 没有 evaluated | 宿主硬过滤、slotName 拼错、SDK 未初始化、网关路径缺 `/en`、请求被网络层拦截 |
| evaluated 有但 data 为空 | GB value 与 admin variant drift、cmsSlug 不存在、snapshot 未发布、GB env 没规则 |
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
2. 标识符表：slug、GB feature、variantKey、cmsSlug、paywallId、preludeEventKey。
3. 宿主 SDK 初始化链路。
4. 宿主 UI 接入链路。
5. admin 配置。
6. GrowthBook 配置。
7. 运行时时序图。
8. 测试清单。
9. 发布和 Superset 验收。
10. 常见故障和排查表。
11. 给其他 AI agent 的最小输入卡。
12. 参考文件和平台链接。

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

1. **不明确就问**：特别是 sn 来源、宿主硬过滤、CTA 目标、preludeEventKey、variantKey 语义。
2. **不凭变量名判断 slot**：以常量值和 admin slug 为准；变量名可能是历史遗留。
3. **不把多个 App 混进一个触点**：同一套代码覆盖 VicoHome / KiwiBit / VicoNature 时，优先拆成 App 级 slug 和 GB feature，不在一个触点里靠 app 条件分流。
4. **不恢复旧 host_spm 链路**：新触点不加 `EngagementHostSpm`。
5. **不把宿主硬过滤放进 GB**：shared / 4G-only / feeder 这类业务不可能展示的场景应在宿主过滤。
6. **不跳过发布预检**：admin / GB 修改不等于运行时生效。
7. **不混淆 Admin 实例和发布环境**：日常操作使用 production Admin；staging-first 指发布 staging rules package，不是操作 Admin staging 实例。
8. **不回退 UI 写入**：Admin API 失败时报告并停止，禁止自动改用页面表单创建、修改或发布。
9. **不为触点 + Paywall 默认新建看板**：优先复用通用 P0 看板并筛选 `slot_name` / `paywall_id`；只有指标缺口经 owner 确认后才新增。
10. **不把 Superset 链接当成已验证数据**：优先用 `addx:superset` skill 查询 dashboard / chart 数据；如果凭据缺失或未授权，明确说明无法读取数据。
11. **不越界修改 H5 底层**：本 Skill 只引用已有 `paywallId`；模板或框架需求转交 H5 Paywall 流程，底层修改先提 issue 给 owner 评审。
12. **不扩张范围**：不长篇复制公开 API 语法；审查或写文档时只读，只有新增接入任务才修改相关代码。

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
结果：实际是宿主硬过滤把所有设备过滤了，方向错。
```

### Good

```
用户：在 staging 验证一个新触点
AI：先调用 admin_api.mjs prod POST /api/touchpoints 在 production Admin 创建草稿；用户单独授权后，再通过 production Admin 的 POST /api/publish-orders 生成 staging rules package。全程不使用 Admin UI 表单写入。
```

```
用户：参考 home_page_device_badge 接一个新设备触点
AI：先填信息卡，确认 deviceScope=true、sn=device.serialNumber、宿主硬过滤=shared/4G/feeder。
再检查 SDK 初始化、slot 常量、集成封装、admin variant、GB feature、发布预检。
最后给出测试和 Superset 验收清单。
```

```
用户：帮我写触点接入 skill
AI：读取 skill-creator、公司 skill-contribution / tool-skill-creator、cicd-developer 的结构。
产出中文 skill：路由、输入卡、流程、排障、审查、产出契约、反例/正例齐全。
```

## 参考

- 完整样例文档：`references/home-page-device-badge.md`；运维验收见 `references/home-page-device-badge-ops.md`；疲劳度验收见 `references/library-cloud-promo-fatigue-ops.md`
- 触点展示异常排障手册：`references/touchpoint-troubleshooting.md`
- iOS 渲染模型：`docs/architecture/sdk-ios/touchpoint-rendering-model.md`
- admin 触点创建 SOP：`docs/architecture/admin/operations/touchpoint-creation.md`
- variant 命名：`docs/architecture/admin/operations/variant-naming.md`
- 系统级创建 Skill 指南：`skill-creator`
- 公司 Skill 贡献流程：`addx:skill-contribution`
- 公司工具 Skill 创建流程：`addx:tool-skill-creator`
- 公司详细 skill 示例：`addx:cicd-developer`
