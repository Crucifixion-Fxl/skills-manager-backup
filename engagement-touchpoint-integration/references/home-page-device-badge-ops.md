# `home_page_device_badge` 运维、验收和交接

## 8. 线上观察

`OPEN_PAYWALL` 主验收入口：`https://superset-us.addx.live/superset/dashboard/paywall-p0-touchpoint-to-paywall-health/`。

AI agent 可以使用 `addx:superset` skill 查询这个看板的数据。前提是当前环境具备 `SUPERSET_URL=https://superset-us.addx.live`、`SUPERSET_USERNAME`、`SUPERSET_PASSWORD`，并且账号有看板权限。如果这些变量保存在 `~/.zshrc`，脚本里要显式 `source ~/.zshrc` 后再登录 Superset。查询方式是先通过 Superset login API 获取 JWT，再读取通用 P0 看板的 charts，必要时用 chart 的 `query_context` 调 `/api/v1/chart/data`，或通过 SQL Lab 查询 dataset 底层表，获取真实图表数据。

如果当前环境没有可用 Superset 凭据，或接口返回 `Not authorized`，agent 必须明确说明无法读取图表数据，并让用户补齐 Superset 登录配置；不要把“页面链接可打开”当成数据已验证。

在通用 P0 看板中筛选 `slot_name=home_page_device_badge` 和目标 `paywall_id`，观察触点机会、evaluate、曝光、点击、Paywall 打开及支付结果。新业务接入相同的触点和 Paywall 通用埋点后，也应通过 `slot_name + paywall_id` 复用这个看板，不默认创建独立业务看板。

Dashboard 527（`https://superset-us.addx.live/superset/dashboard/527/`）仅作为 SDK 基础链路排障入口，用于确认 `evaluate/impress/click/convert` 是否上报、`eval_result` 是否异常、实验归因字段是否完整。

只有通用 P0 看板无法表达目标指标时，才补充已有领域看板或申请新看板；申请前记录缺失的指标和维度并由 owner 确认。

上线后至少观察：

- `touchpoint_evaluated`：是否有 `slot_name = home_page_device_badge`。
- `eval_result`：`show`、`hide_targeting`、`hide_no_content_configured` 等比例是否符合预期。
- `touchpoint_impressed / touchpoint_evaluated`：show 后是否基本能接上曝光。
- `touchpoint_cta_clicked / touchpoint_impressed`：黄色 badge CTR。
- `touchpoint_converted / touchpoint_cta_clicked`：OPEN_PAYWALL 转化。
- `device_sn`：设备级触点必须有；为空说明 host 没传 sn 或 SDK 链路丢字段。
- `experience_key`、`variation_id`：实验归因是否完整。
- VIP native 盾牌的独立 `vip_shield` EXP 事件：只应在 `deviceInVip=true` 时发。

排障口径：

| 现象 | 优先排查 |
|---|---|
| evaluated 为 0 | 宿主硬过滤、slotName 不一致、SDK 未初始化、网关缺 `/en` 前缀 |
| evaluated 有但 impressed 低 | UI 容器未入屏、cell 复用、SDK type mismatch、图片/内容渲染失败 |
| 全部 locked | 未传 `sn`、PE device attrs 缺失、GB rule 没命中、default value 指向 locked |
| data 为空 | GB 返回 value 与 admin variantKey drift、cmsSlug 不存在、snapshot 未发布 |
| CTA 无跳转 | actionType/actionConfig 配错、host `onOpenAction` 分支未覆盖 |
| converted 缺失 | Paywall paymentSuccess 未 echo touchpoint attribution，未调用 `notifyConverted` |

## 9. 新触点接入检查清单

### A. 需求定型

- 明确触点 `slug`，全小写下划线。
- 明确触点所属 App。即使 VicoHome、KiwiBit、VicoNature 共用同一套代码，也优先按 App 拆分 slug，例如 VicoHome `home_page_device_badge`、KiwiBit `kb_home_page_device_badge`、VicoNature `vn_home_page_device_badge`。
- 不要把多个 App 混进同一个触点或同一个 GrowthBook feature，再靠 app 条件分流。
- 明确 `FEATURE_GATE` 还是 `PROACTIVE`。
- 明确是否设备级：设备级必须定义 `sn` 来源和宿主硬过滤。
- 明确 `locked=true/false` 或 proactive 展示的 UI 形态。
- 明确 CTA：`OPEN_PAYWALL`、`DEEP_LINK`、`DISMISS` 或无 CTA。
- 明确观测分母 `preludeEventKey`。

### B. CMS / admin / GB

- CMS 的新触点内容统一建在 `Engagement 触点（engagements）`，不再建到历史 `Promotions` 集合；触点内容必须先确认模板存在、`blockType` 被目标端 SDK 支持。
- `OPEN_PAYWALL` 只引用已发布的 `paywallId`；缺少合适 Paywall 时转交 H5 Paywall 流程，本 Skill 不修改 H5 模板或底层框架。
- admin 建触点、experience variants、actionConfig。
- `PROACTIVE` 额外建 fatigue variants。
- GB 建 `engagement_<slug>_experience`，rule value 对齐 admin variantKey。
- admin 创建 PublishOrder，预检无阻断项。

### C. 宿主 App

- `EngagementSlotIdentifiers.swift` 加 slot 常量。
- 新增或复用集成封装，集中处理宿主硬过滤、布局、缓存、语言切换。
- `FEATURE_GATE` 调 `EngagementTouchpoint.gate` 并提供 child 视图。
- `PROACTIVE` 调 `EngagementTouchpoint.proactive`。
- 设备级触点传 `EngagementContext(sn:)`；点击、state、converted 也传相同 sn。
- CTA 依赖 `onOpenAction` 时确认 actionType 分支和 Paywall attribution。

### D. 测试

- host 单测：ineligible 不调用 SDK / 不预留 stale layout。
- host 单测：eligible 调用 slot 常量且传 sn。
- SDK 或集成测试：locked=true 渲染 CMS，locked=false 渲染 child 视图。
- CTA 测试：OPEN_PAYWALL / DEEP_LINK 参数完整。
- 埋点测试：5 事件包含 slot、solution、experience、variation、device_sn。
- 埋点平台扫码验证：用 `addx:tracking-lifecycle` 或埋点管理平台 `/check/dashboard/` 生成配置，App 扫码后真实触发触点曝光、点击、关闭、转化路径，确认事件已发布且字段与触点归因一致。

### E. 发布和观测

- staging 实机验证。
- 埋点管理平台扫码验收通过后，再进入线上漏斗观察。
- Superset 看 evaluated/impressed/clicked/converted。
- Sentry 看 `feature=engagement` warning。
- 观察至少一个完整漏斗窗口后再扩大流量。

## 10. 给 AI agent 的最小任务输入

让另一个 agent 快速接入新触点时，至少给它这些参数：

```yaml
slug: <admin slug / SDK slotName>
appKey: vicohome | kiwibit | viconature | <other app>
type: FEATURE_GATE | PROACTIVE
deviceScope: true | false
hostLocation: <宿主页面和 view 文件>
hardGates:
  - <例如 shared device excluded>
ui:
  locked: <CMS blockType / 文案 / 图片>
  unlocked: <宿主 child 视图，仅 FEATURE_GATE 需要>
cms:
  slug: <marketing-cms engagements 内容唯一标识>
  blockType: <SDK 已支持的 blockType>
action:
  type: OPEN_PAYWALL | DEEP_LINK | DISMISS | NONE
  paywallId: <if OPEN_PAYWALL>
  deepLink: <if DEEP_LINK>
growthbook:
  featureId: engagement_<slug>_experience
  variants:
    - variantKey: <key>
      locked: true | false
      cmsSlug: <cms slug>
preludeEventKey: <analytics denominator>
observability:
  touchpointPaywallDashboard: https://superset-us.addx.live/superset/dashboard/paywall-p0-touchpoint-to-paywall-health/
  sdkDiagnosticDashboard: https://superset-us.addx.live/superset/dashboard/527/
  filters:
    slotName: <同 slug>
    paywallId: <同 action.paywallId>
```

## 11. 参考入口

- g0-ios：`https://gitlab.addx.ai/SWCLIEN/g0-ios`
- iOS SDK 初始化：g0-ios `AddxAi/A4xInitAppConfig+AppDelegate.swift`
- iOS network adapter：g0-ios `AddxAi/Classes/Engagement/AppEngagementNetworkProvider.swift`
- iOS slot 常量：g0-ios `BaseUI/BaseUI/Engagement/EngagementSlotIdentifiers.swift`
- `home_page_device_badge` 集成封装：g0-ios `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/Engagement/DeviceCardBadgeIntegration.swift`
- 设备硬过滤：g0-ios `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/Engagement/DeviceBean+EngagementEligibility.swift`
- 容器布局：g0-ios `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/View/SubView/LiveTopBarView.swift`
- VIP 原生 child 视图：g0-ios `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/Engagement/VipDeviceBadgeView.swift`
- Snowplow adapter：g0-ios `AddxAi/Classes/SmartLiveSDK/LiveVideoUIKit/LiveVideo/Engagement/SnowplowEngagementTracker.swift`
- engagement：`https://gitlab.addx.ai/services/value-added/engagement`
- SDK rendering model：engagement `docs/architecture/sdk-ios/touchpoint-rendering-model.md`
- admin 触点创建 SOP：engagement `docs/architecture/admin/operations/touchpoint-creation.md`
- variant 命名：engagement `docs/architecture/admin/operations/variant-naming.md`
- personalization-engine：`https://gitlab.addx.ai/services/personalization-engine`
- marketing-cms：`https://gitlab.addx.ai/CLOUD/marketing-cms`
- g0-android：`https://gitlab.addx.ai/SWCLIEN/g0-android`
- g0-flutter-module：`https://gitlab.addx.ai/SWCLIEN/g0-flutter-module`
- GrowthBook：`https://us-ab-management.addx.live/features/engagement_home_page_device_badge_experience`
- admin release：`https://engagement-admin.addx.live/releases/8`
- Superset 触点 + Paywall 通用 P0 看板：`https://superset-us.addx.live/superset/dashboard/paywall-p0-touchpoint-to-paywall-health/`
- Superset SDK 排障看板：`https://superset-us.addx.live/superset/dashboard/527/`
- marketing-cms Engagement 内容：`https://marketing-cms-staging-us.addx.live/admin/collections/engagements`
