---
name: engagement-touchpoint-integration
description: 新增、审查或排查 Engagement App 触点，接通宿主、PE 属性、CMS、Admin Experience、GrowthBook 与发布验证。直接打开 H5 Paywall 时先验证数据链路；模板 UI 开发使用 engagement-h5-paywall-creation。
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

按当前触点类型选择对应样例：设备 Badge、Cloud Service、Library 或 Playback，不默认读取全部样例。直接 H5 模式读 `references/h5-paywall-data-flow.md`。排障读 `references/touchpoint-troubleshooting.md`；运维验收读对应 `*-ops.md`。

## 核心心智

触点不是单一代码改动，而是多个系统的一致性工程：

| 层 | 负责什么 | 典型入口 |
|---|---|---|
| 宿主 App | SDK 初始化、slot 容器、宿主硬过滤、sn 传递、CTA 路由、埋点适配器 | g0-ios / g0-android / Flutter 宿主 |
| 商品目录 / PE | 商品元数据、权威用户属性及实验依赖加载 | iot-service product / personalization-engine |
| marketing-cms | 区块触点素材或 Paywall 商品 / Offer 配置 | staging CMS 的 engagements / paywalls collection |
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
| “帮我接入一个新触点” | 新增模式 | 按展示方式选择 CMS 区块或直接 H5 数据链路 |
| “帮我看这个触点为什么不展示” | 排障模式 | 先读 `references/touchpoint-troubleshooting.md`，按分层流程查代码 / evaluate / GB / admin / CMS / Superset |
| “帮我写接入文档 / skill.md” | 文档模式 | 先补齐信息卡，再按“产出契约”生成中文文档 |
| “review 这个触点接入” | 审查模式 | 只读检查，不擅自重构；输出问题清单 |
| “把 home_page_device_badge 当模板” | 样例模式 | 必读完整样例文档，提炼差异点，不照抄旧命名 |

## 新增模式：按展示方式选择流程

- SDK 渲染 CMS 素材、`FEATURE_GATE` 原生 child 或普通 `DEEP_LINK`：读 [CMS 区块触点流程](references/cms-block-touchpoint-workflow.md)，保留宿主、Admin API、staging 发布和埋点验收规则。
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
11. **不越界修改 H5 底层**：本 Skill 接通和校验 Paywall 数据，不修改 H5 模板或框架；同一授权任务中的模板需求转入 H5 Paywall 流程，底层修改仍按 owner 评审边界处理。
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
