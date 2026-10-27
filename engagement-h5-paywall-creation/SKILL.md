---
name: engagement-h5-paywall-creation
description: Create or update Engagement H5 Paywalls from Figma as independent paywallId instances with private UI and reusable business logic. Keep only existing personalized and standard Paywalls on their CMS template path. Includes launch NEVER/LAPSED and binding examples, configuration and public capability boundaries. Do not use for only connecting an existing Paywall to a touchpoint.
---

# Engagement H5 Paywall Creation

## Description

平台登录与认证 SSOT：[engagement-admin](../engagement-admin/SKILL.md)。本 Skill 保留业务流程与门禁，登录/Token 事实只在平台 owner 维护；日常访问调用 `web-access`。

把 Figma 和业务输入转换为可验收的 H5 Paywall。以后所有新增业务 Paywall 和商品实验都采用独立实例：`paywallId` 定位页面，`paywall.config.ts` 保存静态商品配置，Page 自行实现 UI，薄业务 Controller 组合公共逻辑。只有已有个性化 Paywall 和标准模板 Paywall 保留 CMS 模板链路。

## 路由

- 新增 Paywall 页面、商品实验实例，或根据 Figma 调整页面：使用本 Skill。
- 只维护已有个性化/标准 CMS Paywall 的组件、文案或商品：使用 `marketing-cms`。
- 只生成和上传头图：使用 `paywall-hero`。
- 已有 Paywall 只需接入触点：使用 `engagement-touchpoint-integration`。

转交触点流程时附上已部署目标 ID、来源/runtime 与本 Skill 的 [实例接入契约](references/instance-recipes.md#直接-paywall-与-blocks-cta-的接入)。若触点 Skill 的旧参考要求新建 templateKey/CMS Paywall 或将薄业务适配一概视为底层修改，这些模板说明仅适用于已有 CMS 模板维护；新实例以目标仓库 ADR-0003/实例贡献指南及本契约为准，只复用触点流程中的 Admin/实验、归因和接口验收步骤。

## Rules

1. **先确认目标 checkout 的能力。** 方案已确认不代表分支已合入或环境已部署；按启用门槛核对，不把规划路径当作可用 API。
2. **新业务统一用实例。** 启动 NEVER/LAPSED、首绑、绑定更多设备及后续新需求均不新增 CMS Paywall、模板或需求商品编辑器；不新增 `templateKey`、`contentSource`、`configSchemaVersion` 或 `instanceVersion`。CMS 的 blocks 素材仍可独立使用，不能与目标 Paywall 来源混为一谈。
3. **UI 私有，逻辑复用。** 每个实例可以按 Figma 自行实现商品卡片、布局和 CSS，不要求抽公共业务 UI。Page 渲染只读 ViewModel、消费公共语义 actions；薄业务 Controller 组合 `usePaywallController`、`usePaywallActions` 等公共能力，Policy 处理纯选品规则。不复制交易、Bridge、报价、互斥锁、运行时加载或基础埋点；目标 checkout 已具备公共访问生命周期时，也不在 Page/scene 重发 `page_scroll`、`page_exit`。
4. **上游决定最终 `paywallId`。** 实例不按用户大分群、流失阶段或实验组改选页面。设备档位、历史代际/周期等当前实例内选品由 Controller/Policy 处理。
5. **保留旧 CMS 链路，两个来源的 ID 不重名。** 新实例的 `paywallId` 不得复用 CMS 已有 Paywall 的业务 ID/key；受保护 ID 清单不是全部 CMS ID。现有两份个性化页面和标准模板不因新增实例而迁移；标准模板继续通过 CMS 管理组件、商品、文案、素材与多语言。
6. **配置不代替权威事实。** 产品/Offer 引用随实例发布；商品详情、用户事实来自服务端，实际价格、资格和购买结果沿 Native/渠道契约获取。
7. **按授权范围实现。** 普通实例注册和配置属于实例交付；业务 Controller 不等于框架。公共 API/schema/registry/交易/构建/部署仍需对应 Owner review；已有明确授权或结论沿用，不重复申请同一范围。
8. **证据分层。** 浏览器、Mock Bridge、视觉 candidate 不代替真机、支付、权益或数仓；不自动覆盖已批准视觉 baseline。
9. **多语言是交付条件。** 从目标 checkout/投放范围确定支持语言，未单独限定时覆盖项目全部支持语言。调用 `t/tf` 不等于已翻译：新增/变更 key 必须有对应词条、实际加载并通过完整性检查。支持语言的缺词条/英文回退不能作为验收通过；不支持语言的既有 fallback 行为保留。详见 [references/localization.md](references/localization.md)。
10. **业务实例不扩测试框架。** E2E 写入现有 suite，复用仓库已有 Playwright config、npm 命令和 CI family；普通实例任务不得新建 `playwright*.config.*`、私有端口或测试 runner。需要新的集中 family/config 时先交框架 Owner 评审，不用业务专属配置绕过现有门禁。

## 产品运营提供什么

先从设计稿和现有配置推断，只补齐无法可靠推断的信息：

- Figma、目标 frame、采用区域及状态；指定 `Design by DW` 时只以该区域为基线。
- 场景、App/tenant、目标用户、入口和新建/更新的 Paywall ID。
- 已确认 Product ID、Offer 类型/完整 ID、商品组及默认项。
- 关闭、All Plans、挽留、恢复购买、成功跳转等差异，以及特殊支付渠道。
- 源文案、目标语言和素材来源。

不要求运营设计 Bridge payload、事件字段、CMS 原始 JSON 或新模板标识。缺少商品依据时按[商品建档与实例配置](references/product-onboarding.md)区分复用 SKU、复用 SKU 的新 Offer 与新建 SKU；先找线上同类商品、记录字段差异和渠道证据，不猜 SKU、拼 Offer ID 或把 Figma 金额作为真实价格。页面开发授权不自动包含商品库写入；未获明确授权时只准备和 review SQL。

## 启用门槛与模式

先读目标仓库 `paywall-h5/AGENTS.md`，列 planned changed files，检查重叠改动。用仓库根目录与相对路径，不写死个人目录。

普通实例的 planned changed files 默认限制在 `src/instances/<id>/**`、已授权的 `src/scenes/<scene>/**` 和业务 E2E/fixture。出现 `src/application`、`src/ui/public`、runtime/renderer/tracking/payment/bridge、`scripts/`、`playwright*.config.*`、构建、CI 或部署文件时，先停下并按 [修改分区](references/template-boundary.md#新实例修改分区)确认是否属于单独的 Owner 任务；不得为了当前页面顺手修改框架。

**新增独立实例**核对：

- `docs/design/paywall/h5-instance-contribution.md`、ADR-0003；
- `src/instances/README.md`、`src/application/public/index.ts`、`src/ui/public/index.ts`；
- 实际 config schema、`definePaywallInstance`、注册发现及目录生成契约；
- 实例、旧模板和 tracking 门禁，以及 package 中的 lint/build/实例测试 harness。

基础缺失时可完成设计分析、商品清单和实现计划，说明缺失能力，暂缓依赖它的生产接入。不要临时把实例塞进旧模板或补 CMS schema；已授权的架构升级按对应 Owner 工作流实施。

**已有个性化/标准 CMS 页面**读取 Template Kit README/public、template keys、manifest、renderer registry 和 CMS 实际字段。旧模板导入限制仅适用于旧模板；不能以兼容性为由给新业务继续创建 CMS 模板。

**访问生命周期埋点**按目标 checkout 条件启用，不把未合入方案当作已有能力：

- 核对公共 `App` 是否在 CMS `PaywallTemplate` 与实例 `IndependentPaywall` 两条渲染路径接入 `usePaywallVisit`，并存在 `tracking/PaywallVisitRecorder`、`tracking/paywallVisit` 及对应保护测试；
- 核对 Android/iOS 宿主 SDK 是否会通知已提交的系统返回/手势退出和 Native 可见性；只有 H5 改动、宿主仍使用旧 SDK 时，不能宣称系统返回路径已完整覆盖；
- 能力缺失时记录 Owner 缺口，可以继续页面与配置工作，但不在业务 Page/scene 临时实现原始事件。当前公共实现与语义由 [MR !733](https://gitlab.addx.ai/services/value-added/engagement/-/merge_requests/733) 提供，是否可用仍以目标分支和目标环境制品为准。

修改分区见 [references/template-boundary.md](references/template-boundary.md)；两条数据链路见 [references/cms-configuration.md](references/cms-configuration.md)。

新增业务时先读 [实例创建示例](references/instance-recipes.md)，按启动流失、启动未购买或绑定场景选择已有 Controller；编写/修改业务逻辑时再读 [公共能力与薄 Controller](references/reusable-capabilities.md)。不要把示例商品直接当作新 PRD 的配置。

## 工作流

### 1. 固定设计与验收面

先读 PRD 和用户最新确认的业务规则；Figma 约束 UI。业务行为冲突记录差异，按已确认需求处理，不通过写死价格掩盖。

读取目标 frame 的结构、字体、颜色、间距、尺寸、素材和交互，记录 file key、node ID、读取时间及采用区域。映射页面、选中状态、All Plans/挽留和 loading/error。无法访问原稿时说明缺失并请求可读取的导出稿/素材，不从模糊截图推断完整页面。

整理输入表：tenant、Paywall ID、frames、商品/Offer/组、默认策略、行为、语言、渠道。敏感账号与凭证不进入该表或代码。

### 2. 确定实例边界

新增实例以独立投放页面为单位，不以整个 PRD 或每个商品/状态为单位：

- 布局/交互呈现不同，或独立商品替换实验：新 ID，可复制私有页面/配置并复用组件/Controller。
- 启动流失 0–7 天与 8–30 天展示不同页面：分别建实例，上游决定 ID。启动 NEVER 的阶段是另一套边界，见实例示例。
- 双设备/多设备只商品不同、页面与交互相同：同一实例多商品组，Policy 选品。
- 语言、币种、Native 本地化报价或普通选择/loading 状态变化：不因此拆 ID；要让对照组与实验组同时使用不同商品/Offer，使用两个 ID。

不要求多个需求共用完整模板。复用组件/逻辑不意味着共用 ID。A/B 由上游 Experience 指向实例；A/A 可指向同一 ID，实例不按实验组换商品。

更新旧模板前核对同模板已发布调用范围，保留其 CMS 配置和行为。选定新实例 ID 前，按 [Paywall ID 互斥检查](references/cms-configuration.md#paywall-id-互斥检查)核对 staging/prod CMS 和已有 H5 实例；已有 CMS Paywall 迁入实例时也使用新 ID，不以部署同名实例替换旧入口。

### 2.5. 动态数据准备与交接

动态商品、挽留和升级比价实现前，读 [运行时数据输入](references/runtime-data-input.md)。商品尚未确认、需要新增 SKU/Offer，或不知道 `paywall.config.ts` 应如何填写时，再读[商品建档与实例配置](references/product-onboarding.md)。新实例使用已核对的静态配置与真实 paywall-runtime；旧页面取得真实 App cms-content；两种模式都核对 evaluate / 宿主上下文、候选、元数据、默认项和报价身份。缺字段先定位数据路径，不拼本地商品目录伪装成服务返回。设计分析和不依赖数据的准备可继续。

需要建立触点、PE / GB 条件和 Experience 映射时，在同一授权任务中使用 `engagement-touchpoint-integration`，先确认它的流程支持当前实例模式；旧 CMS 数据指南不能当作新实例必须录入 CMS 的依据。不要求新建 Agent 窗口，也不自动扩大页面开发授权。

用户已约定逐阶段 review 时，交付可检查的配置、接口结果或预览再推进；沿用当前任务授权，不额外要求所有任务逐阶段审批。

### 3. 准备静态配置与组合

按当前贡献契约建立 `src/instances/<id>/`：

```text
paywall.config.ts   静态配置，唯一声明 ID/tenant
definition.ts      连接 config、Page、useController
Page.tsx           页面布局与状态呈现
assets/            私有素材
locales.ts         或当前契约允许的语言数据
```

配置默认导出静态对象，字段严格以当前 schema 为准：ID、tenant、可选内部 `description`、必填 `spmb`、可选 `payType`、`offers`、`groups` 与可选 `content`。`spmb` 使用 schema 允许的四个值之一，没有缺省值；省略 `payType` 沿宿主默认，显式 `0` 有效。字段结构和分组约束见[配置契约](references/cms-configuration.md#新实例配置)；实际值的来源、Offer 类型判断、试用语义和填写示例见[商品建档与实例配置](references/product-onboarding.md#paywallconfigts-字段来源)。

页面文案/素材已在私有 UI 和语言文件维护时可以省略 `content`；它不是每个需求必填的 CMS 内容副本。`offers` 定义可售项，`groups` 按业务用途组织这些可售项；分组命名和选择策略由场景决定。

不加入真实金额、资格、设备事实或购买结果。商品实验复制后必须使用新 ID，不只改文件名。配置、页面与素材随同一 H5 制品发布。

`definition.ts` 只导入绑定并默认导出 `definePaywallInstance({ config, Page, useController })`，不内联 Controller、IO 或条件流程。注册表和 `dist/instance-catalog.json` 自动从同一批配置/定义生成，不改 backend 手工 ID 清单。

### 4. 实现 UI 与业务适配

- Page/私有 UI 只依赖本实例展示文件、允许的 React API 和 `src/ui/public`；不导入配置、definition 或 Controller。
- definition 经 `src/application/public` 连接 Controller；Controller/Policy 放在 `src/scenes/<scene>/`，不放入 Template Kit、公共 UI 或 Page。
- 优先复用已有场景 Controller；当前行为不适用时新增薄 Controller/Policy，仅写业务默认项、导航、文案与模型适配。共享场景修改检查全部调用方；报价、可购买条件、恢复、互斥和错误重试使用公共 API，具体入口见 [公共能力与薄 Controller](references/reusable-capabilities.md)。
- Controller 返回 `{ viewModel, actions }`；购买 handle 交给 `ui/public.PurchaseAction`，按契约呈现 `actions.frameworkOverlay`，不漏掉公共设备选择/结果弹窗。
- 选择、重试、购买中、导航与选择恢复由 Controller 管理；展开/动画等纯显示状态可由 UI 本地维护。
- close/restore 等公共动作保留拒绝/超时异常；Controller 捕获并提供动作错误/重试模型，Page 呈现，不吞错或伪装成购买结果。
- 关闭必须走公共 `closePaywall`/语义 action，购买必须走公共 Purchase Controller 与成功跳转；使用挽留页时也走已具备生命周期结算的公共语义 action，若 public 未暴露则记录 Owner 缺口。当公共访问生命周期已启用时，框架在退出时统一结算 `page_scroll`、`page_exit`，支付成功只结算滚动并排除主动退出。Page/scene 不直接调用 lifecycle recorder、原始 tracking 或 Native exit Bridge。
- 每个页面提供正确 `spmb` 作为 PV 和生命周期页面身份。普通 All Plans、支付弹窗等仍属于当前访问；若产品要求 Paywall 内部子页面拥有独立 PV、滚动深度或退出率，这是新的访问边界和公共事件语义，先交 Tracking/框架 Owner 评审，不能仅在私有 UI 中手发事件。
- 按 Figma 还原 CSS、字体、素材与文案，避免全局污染；金额/折扣/周期来自精确报价与模型。处理长文案、小屏、安全区、空数据与错误态。
- 将 Figma 尺寸视为设计基准，明确页面与头图的满宽/留白、等比缩放/裁切规则；满宽设计不得直接锁死为画板宽度。布局实现须符合目标 App 支持的 WebView 版本，具体尺寸、兼容性和截图要求统一见[头图与多屏适配验收](references/verification.md#头图与多屏适配验收)。
- 用公共语义组件托管曝光和操作；Page/scene 不调用原始 Bridge、网络、全局状态或 TrackingService。
- 所有交互控件提供可读名称和稳定测试标识；键盘/焦点与触摸反馈按当前公共组件契约验证。

**多语言必须与页面一起交付。** 新实例使用私有纯语言数据（如 `locales.ts`）和已公开的语言能力；可由配置的 `content` 引用，也可由私有 Page/展示模块直接引用，不能因此强制添加 `content`。Controller 提供语言与必要的展示模型，Page 仍只处理展示。沿用现有语言别名、英文回退和 RTL，不在 Page/Policy 新造解析器；若公共能力不能表达，记录 Owner 缺口。

旧模板固定文案仍走 `template-kit/public` 的 `t` / `tf`，维护 `src/i18n/<template>LocaleData.ts` 英文源、`src/i18n/generated/<template>LocaleData.ts` 全 locale 资源和对应测试；只允许在 `cmsI18n.ts` 的 `messagesFor()` 合并链新增一行数据源注册，不修改别名/回退/RTL 解析。新实例不套用该旧模板注册步骤。

全部要求语言的 key、命名占位符与英文源一致。金额、设备数量、价差等运行时值由模型注入；局部强调拆成语义 label token，不把整段动态短语翻译为不可控 token。机器译文在 Production 开放前需产品/本地化复核，不当作已批准内容。

文案清单覆盖主页面、商品卡、挽留/All Plans、法律入口、试用提醒、loading/error/retry 和购买/恢复状态；同时处理 aria-label/alt。复用语义一致的已有翻译，新增文案补齐目标语言；不以英文 Figma 或默认字符串代替翻译。计费说明使用完整本地化模板及周期/复数规则，不手拼 `for/then/total/mo`；插值保持价格、币种、优惠周期和续费语义。

扩展公共能力时只暂缓依赖扩展的部分，记录缺口与方案，继续可独立完成的页面、配置和验证。

### 5. 核对数据与投放准备

命中注册表后，由公共入口读取本地配置并请求 `/engagement/v1/paywall-runtime`，Page 不发请求。接口收集 Product IDs 获取元数据，不传 Offer ID；Offer 身份用于后续报价/购买。宿主/认证 tenant 必须与实例一致。

新实例不创建 CMS Paywall 草稿，不改模板选项/商品编辑器。仅维护已有个性化/标准页面时使用 `marketing-cms`；blocks 入口自身的 CMS 素材沿原流程维护。

先核对目标环境制品与目录已部署，再配置 Experience/触点引用。`OPEN_PAYWALL` 目标设置 `runtime=h5`、`paywallSource=h5_instance`，适用于直接 paywall 和 blocks CTA 两种入口。Admin 读目录做候选/发布校验，evaluate 不请求 H5 静态目录，不复制实例的 tenantId/spmb 到规则包。具体接入交给 `engagement-touchpoint-integration`。

若本次验收包含 `page_scroll`/`page_exit`，还要核对目标 App 使用包含 Native lifecycle 通知的 Engagement SDK；H5 制品已部署不等于系统返回/手势路径已覆盖。显式 H5 关闭、Native 返回和数仓入库分别保存证据。

开发、MR、部署与实验投放是不同事实，按用户授权执行；页面开发不自动授权投放或生产发布。

### 6. 验证

按 checkout 执行必要检查。新模式运行 lint、相关 unit/component、build、实例契约及业务交互/视觉 suite；旧模式运行对应模板检查。lint 已含 architecture/tracking 时不重复同一检查。

业务 Playwright 用例放入现有 `e2e/`，复用 `playwright.instance.config.ts`、`playwright.ci.config.ts` 或 checkout 已有的集中配置；先用现有 npm 命令执行，并通过 CI list/selection 证明确实会被流水线选中。已有 `@ci-*` family 能表达时沿用；需要新增 family 或修改集中 runner/config 时作为 Owner 变更单独评审，不能为一个 Paywall 新建专属配置。

运行完整 `npm run lint`，确认 architecture 中的 Playwright 配置 allowlist 以及 checkout 已有的素材等门禁均通过；不单独运行 TypeScript 后就宣称 lint 通过。

检查完整主页面与关键区域、选择/挽留/All Plans/loading/error/购买状态，按[头图与多屏适配验收](references/verification.md#头图与多屏适配验收)覆盖 iOS/Android 的多种屏宽与长短屏，并结合语言与安全区验证。验证商品组/默认项、运行时事实、报价/购买身份、渠道和归因。

目标 checkout 已启用访问生命周期时，按 [埋点与访问生命周期矩阵](references/verification.md#埋点与访问生命周期)验证现有标准 CMS、个性化 CMS 和新增独立实例三类页面；覆盖主动触摸滚动、未滚动、显式关闭、Native 返回/手势、后台/取消手势、挽留页往返、支付成功、去重及归因。Mock Bridge 只证明调用序列，不代替双端 SDK 和数仓证据。

检查全部要求语言的 key/占位符、语言别名、非支持语言英文回退、ar/he RTL，以及代表语种长文案；CMS 翻译仅覆盖旧 CMS 字段，不能代替代码侧语言资源。

执行轻量词条/占位符检查及实际加载、动态计费文案的 unit/component 回归；按 [多语言验收矩阵](references/localization.md)保存非英文整页截图和语言/实例/状态记录。缺词条、意外英文回退、残留占位符、周期混用或文案溢出均失败。多语言检查不要求每个业务 UI 重跑支付 E2E，也不能只检查 CTA 已翻译。

输出 candidate 与差异，不自动批准 baseline。浏览器契约通过不等于“高保真完成”，须有对应 frame 对比证据和差异说明。详见 [references/verification.md](references/verification.md)。

### 7. 交付

核对 diff 与授权范围。输出/MR 包含：实例 ID/旧模式、Figma/frame 映射、配置商品摘要、Controller/Policy 复用/改动、实际文件、验证结果、截图差异、多语言覆盖/占位符结果及译文审批状态。

新模式交付实例及制品目录证据，旧模式交付 CMS 状态。区分实现、部署、视觉、真机、支付、权益与埋点；生命周期埋点注明 H5 公共能力、宿主 SDK 和数仓三层状态，缺项写 `NOT_RUN`，不推断已上线。列具体 Owner 缺口和已有评审，不为普通新实例注册申请模板审批。

多语言交付列目标语言、文案来源/复用、检查结果、实际加载证据、截图矩阵和待确认译文；机器翻译/新增译文不得标为已获产品或专业语言审核。

## Examples

**Good:** 绑定更多 NEVER 的双/多设备 UI 相同，一个配置保存两组商品；Policy 按权威设备数量选组，Controller 调公共交易，Page 渲染模型。无需新 CMS schema 或模板 key。

**Good:** 商品实验复制实例并调整新 ID/配置，复用 Controller/组件；发布后实验返回不同 Experience/ID。实例不读取 variation 换商品。

四个已落地需求的实际 ID、definition、分组与创建步骤见 [实例创建示例](references/instance-recipes.md)；不要为每个商品、设备档位或交互状态复制一套支付逻辑。

**Bad:** 为每个需求向 Template Kit 添加 NEVER/LAPSED/SKU 分支，Page 判断实验并调用 Bridge，再要求 CMS 增加需求商品编辑器。

**Bad:** 把 CMS 已有的 `pw_old` 用作新实例 ID，认为未修改 Admin 变体就不会影响旧 CMS H5 入口。应另选经过互斥核对的新 ID，再让上游投放引用它。

## 禁止事项

- 不为新实例套用旧模板/CMS 必经流程，不复用 CMS Paywall ID，不迁移受保护页面。
- 不将业务规则放入公共基础，或从公共层反向导入实例/scene。
- 不在 Page、配置或 composition 重做报价、购买、分桶和基础埋点。
- 不在实例/模板手发 `page_scroll`、`page_exit`，也不为绕过旧 SDK 伪造 Native 返回；缺少公共生命周期能力时交 Owner 补齐。
- 不将目录试用天数当作用户资格，不静默切换错误商品/渠道。
- 命中实例后失败走错误/重试，不切 CMS；仅未命中走旧链路。
- 不覆盖用户改动或批准视觉 baseline，不用 Mock 宣称真实交易完成。
