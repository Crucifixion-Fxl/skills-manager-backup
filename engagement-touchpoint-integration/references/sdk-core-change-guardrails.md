# Engagement SDK 基础能力变更门禁

本文用于审查或实施 Engagement SDK 展示扩展，以及公共 API、数据模型、evaluate/render/cache/network 或触点生命周期的改动。按代码的实际职责和影响评审；新增触点可以带来合理的 SDK 展示能力扩展，但不能把业务决策移进 SDK。

## 1. 默认决策

优先复用 SDK 现有能力；缺少视觉组件或文案处理时，允许在 SDK 扩展：

- 展示准入、实验和疲劳度：PE、GrowthBook、engagement-admin、engagement-service。
- 素材、静态/动态文案和允许替换的目标字段：marketing-cms。
- 最终 Experience、Banner 样式、action 和 `paywallId`：GrowthBook `variantKey` + Admin Experience + engagement-service。
- 页面参数、鉴权网络、执行既定导航和业务 UI 编排：宿主 adapter/业务模块。
- CMS payload 渲染、新增视觉 builder、动态文案解析和单复数等通用格式化：SDK；宿主不必重写这些能力。
- evaluate、cache、attribution 和通用触点事件：优先复用；改动其公共语义时做兼容性与全调用方影响评审。

新 builder 可以由一个触点首次使用，也可以暂时带有 `Library` 等历史或场景命名。名称和消费者数量只是审查线索；判断其是否按 CMS payload 展示、是否夹带业务判断，不据此直接阻断，也不为了改名而做无关重构。

## 2. 按实际影响分级

| 改动 | 评审重点 |
|---|---|
| 沿既有 registry 新增 `ContentViewBuilder` / renderer，增加 payload 展示字段 | 符合目标端渲染模型；字段校验、布局、无障碍及标准回调；不改变业务决策和原调度机制 |
| 新增/扩展通用动态文案 resolver、单复数、数字/日期/相对时间 formatter | CMS 文本字段契约、locale/时区、缺值策略、三端一致性；不选择营销业务变体或路由 |
| 改公开模型、evaluate、调度机制、network、cache 或生命周期 | 说明必要性和受影响调用方；SDK owner 审 API/跨端/兼容性、性能与回滚 |

新增 builder 注册项本身是既有扩展点的使用，不等于改变 registry/dispatcher 的公共机制。共享 resolver 若同时改动网络、缓存或 evaluate，应额外审查这些受影响部分，不因它属于文案能力而免除兼容性要求。

以下是需谨慎评审的公共 surface：

- 新增或改变公开 evaluate/gate/state/track/notify 方法及其同步、异步语义。
- 给 `EngagementContext`、公共 request/response 或 Touchpoint 模型增加字段。
- 改变 renderer/builder 注册机制、容器挂载、缓存 key/失效或静态/动态路径的共享语义。
- 改变 network provider、路由、鉴权适配或默认请求数量。
- 改变 `evaluated`、`impressed`、`cta_clicked`、`dismissed`、`converted` 的触发条件、顺序、次数或归因字段。
- 增加 observer、并行 evaluate 或状态机。展示否决、业务准入和本地 fatigue 仍按第 6 节阻断。

同一业务 MR 可以包含 SDK 展示扩展或通用能力，需分段说明 SDK/业务改动、关联测试并取得相应评审。较大的共享机制或生命周期调整可拆独立方案/MR 便于审查；不把“一律拆 MR”作为准入条件。

## 3. 合理扩展的条件

必须同时满足：

1. 说明现有组件/文案处理的具体缺口和为何适合由 SDK 承担；不要求为证明必要性把渲染或格式化先搬到宿主。
2. builder 按 CMS payload 构建 UI，formatter 处理展示语法；业务数据口径和取数由权威业务源/CMS 配置/宿主 provider 提供，不在 SDK 写会员、疲劳度、业务变体或 Paywall 选择逻辑。
3. 列出当前消费者和数据契约；可复用的数据驱动实现即可由首个触点引入，不强制两个已落地消费者。
4. 按改动范围完成 SDK 评审；公共 API、网络、缓存和生命周期变更需 SDK owner 审核其必要性、跨端一致性、版本策略和迁移范围。同 MR 不能代替评审，但也不自动构成越界。
5. 默认行为向后兼容；未启用新能力的旧调用方、静态触点和既有 CMS 内容不改变网络、缓存、渲染、事件或时延语义。
6. 按第 7 节提供与影响范围相称的测试和回滚/降级证据。

缺证据时补齐相应说明和验证；若代码实际承担了业务决策，则将该逻辑移回对应 owner。不能把所有 SDK 改动一概要求搬到宿主。

## 4. 生命周期保护

通用生命周期是平台契约，不因某个业务修改：

```text
evaluate -> render/mount -> impressed -> cta/dismissed -> converted
```

- 服务端 evaluate 已完成 Experience/Fatigue 判定；宿主不得通过 `shouldPresent` 等回调再次决定展示。
- 服务端返回的 Experience、CMS 内容和 `paywallId` 是最终决策；SDK/runtime/宿主不得根据 N/D、`creativeKey` 或其他业务数据再次选择 Banner、CTA 或 Paywall。
- 页面销毁、容器换世代或异步结果过期是资源安全问题，通过任务取消、生命周期 token 和安全挂载解决，不新增业务可调用的展示决策方法。
- impressed 仍由既有“实际可见”契约触发；不能为业务自定义可见时长而替换或重复上报通用 impressed。
- dismissed、converted 继续使用既有 attribution；不能因新页面跳转而丢失 slot、solution、experiment、variation、experience、fatigue 或 sn。
- 不为动态文案另建一套 evaluate 或事件链。runtime resolution 是 evaluate 命中后的内容准备阶段，静态触点应保持原快速路径。

如仓库存在更具体的正式生命周期 contract，以该 contract 为准；业务 MR 不得自行改写。

## 5. 变更归属判断

| 需求 | 应修改的位置 | 不应修改 SDK core 的方式 |
|---|---|---|
| 会员/设备/录像资格 | PE 属性 + GB Experience | `EngagementContext` 业务字段、宿主展示回调 |
| 次数、曝光/关闭冷却 | Admin/GB/engagement-service Fatigue | App/SDK 本地计数器和 timer |
| N/D 等动态文案 | CMS `runtimeBundle` + `runtimeInputs` + SDK 通用 resolver/provider，可按需扩展 | SDK 硬编码业务数据口径、endpoint、资格或变体选择 |
| 单复数、数字/日期/相对时间 | CMS 模板/格式化配置 + SDK 通用 formatter | 为语法分支复制宿主实现，或借格式化切换营销方案 |
| Snapshot/Rolling 等不同样式或 Paywall | PE 属性 + GB 返回不同 Experience variant + Admin 分别绑定 CMS/action | `creativeKey`、`paywallIdsByCreative`、runtime action map 或 SDK 二次路由 |
| 页面查询参数和鉴权 | 宿主 adapter | SDK 保存 Token、域名或业务 service 路由 |
| CMS 触点新视觉 | 优先复用；缺能力时可在 SDK 新增 builder 并注册 | builder 按触点 slug/会员/录像状态决定业务准入、变体或路由 |
| 页面离开后的异步结果 | 宿主/SDK 内部取消与安全挂载 | 暴露 `shouldPresent` 给业务决定 |

## 6. Review 阻断项

以下按实际行为判断；仅有场景命名、一个消费者、builder 注册或 `runtimeText.cases` 不构成阻断：

- 在 SDK 硬编码触点 slug、会员/录像/套餐条件来决定准入、业务变体、疲劳度或路由。
- 公共上下文新增字段实际承载资格/频控/业务变体决策，而非数据驱动的展示输入。
- 新增回调让业务方改变服务端 evaluate 结果、通用事件或疲劳度消费。
- 根据动态值、`creativeKey` 或 CMS payload 改写 service 下发的 `actionType`/`paywallId`，或让一个 Experience 在客户端分成多个 Banner/Paywall。
- 动态变量允许写入 `blockType`、`preset`、`creativeKey`、`cmsSlug`、`actionType`、`actionContext`、`paywallId`、`solutionId` 等控制字段。
- 老 `evaluate`/renderer 被新路径替换，但没有证明旧调用方行为不变。
- 无 `runtimeBundle` 的静态触点增加请求、等待、缓存变化或失败点。
- 新旧 API 长期并存，却没有兼容策略、迁移期限和删除计划。
- 共享机制变更没有覆盖受影响的 FEATURE_GATE、PROACTIVE、静态/动态内容和事件链；纯新增 builder/formatter 按第 7 节验证。
- 三端实现的默认行为、错误策略或事件时序不一致。

## 7. 最低评审证据

SDK 改动按适用项记录，不适用的 surface 不要求虚构消费者或全链路测试：

```yaml
sdkCoreChange:
  kind: presentation_extension | shared_core_change
  problem: <具体视觉/文案能力缺口或共享机制问题>
  rejectedExistingOptions:
    - option: <现有 SDK/Admin/GB/PE/CMS/host adapter 能力>
      reason: <可验证的不足>
  consumers:
    - <当前真实消费者；可以只有一个>
  affectedPublicSurfaces:
    - <builder/formatter/API/model/evaluate/render/cache/network/lifecycle；仅实际受影响项>
  defaultBehaviorChanged: false
  compatibility:
    oldCallers: <证据>
    staticTouchpoints: <0 额外 runtime 请求/相同行为证据>
    events: <次数、顺序、归因不变证据>
    routing: <runtime 前后 Experience、Banner preset 与 paywallId 不变证据>
    performance: <基线与新版本对比>
  rollout: <版本固定、灰度、监控>
  rollback: <回滚或能力关闭方式>
  sdkOwnerReview: <链接或记录>
```

新增 builder 至少验证注册/分发、payload 字段和非法输入、布局及标准 CTA/dismiss/impression 接线，并做受影响旧组件回归。formatter 验证单复数、locale、日期/时区边界、缺值和非法 target，且格式化前后 Experience/preset/action/paywallId 不变。

触及共享 evaluate/network/cache/lifecycle 时，再覆盖旧公开调用、新能力 opt-in、静态触点零额外 runtime 请求、缓存隔离、并发/取消、五类标准事件与性能。按实际影响补集成/实机证据，不因仅新增一个 builder 就要求全调用方全套重测；也不以“CI 通过”代替公共语义的兼容性判断。
