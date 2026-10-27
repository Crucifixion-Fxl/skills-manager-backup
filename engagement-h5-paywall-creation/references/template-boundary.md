# H5 实例、旧模板与 Owner 边界

文件名保留以兼容已有引用；新实例与旧模板分别遵循各自导入/贡献契约。

## 事实来源

先读目标 Engagement checkout 的：

- `paywall-h5/AGENTS.md`；
- 新模式：贡献指南 `docs/design/paywall/h5-instance-contribution.md`、ADR-0003、实例 README、application/UI public、config schema 和实例门禁；
- 旧模式：Template Kit README/public、template keys、manifest、renderer registry 和 ADR-0002。

公共基础由 [Engagement #169](https://gitlab.addx.ai/services/value-added/engagement/-/issues/169) 跟踪，实例基础见 [MR !692](https://gitlab.addx.ai/services/value-added/engagement/-/merge_requests/692)，Controller 复用入口见 [MR !718](https://gitlab.addx.ai/services/value-added/engagement/-/merge_requests/718)，访问生命周期见 [MR !733](https://gitlab.addx.ai/services/value-added/engagement/-/merge_requests/733)。链接不代替目标 checkout/部署核对；公共 API 使用说明见 [reusable-capabilities.md](reusable-capabilities.md)。

本次同步的固定契约为 [d45baca3 的实例接入指南](https://gitlab.addx.ai/services/value-added/engagement/-/blob/d45baca31d486c93ca0d730d1ecfac5be8c71563/docs/design/paywall/h5-instance-contribution.md)。后续执行仍核对目标 checkout，不能以该版本链接推断环境已具备能力。

## 新实例修改分区

| 位置 | 职责与范围 |
|---|---|
| `src/instances/<id>/Page.tsx`、私有组件/样式/素材/文案/测试 | 默认实例范围；布局、状态呈现和纯显示状态 |
| `paywall.config.ts` | 静态 ID/tenant/必填 spmb、商品/Offer/组/默认项及可选内容，不执行函数 |
| `definition.ts` | 默认实例范围；只组合 config/Page/Controller，不实现逻辑 |
| `src/scenes/<scene>/` | 业务 Controller/Policy/Adapter，不是公共框架；按需求授权和场景 Owner review 实施，运营默认引用已有 Controller |
| `src/ui/public` 及共享 UI | 组件与语义交互契约，由对应 Owner review，不含分群/SKU/交易编排 |
| `src/application/public` 及共享应用能力 | 通用数据/报价/交易，由对应 Owner review，不含首绑/更多设备规则 |
| schema、registry、runtime、services、payment、bridge、state、tracking、renderer | Owner 公共基础；新实例无需手工注册表修改 |
| `e2e/` 业务 spec/fixture | 默认实例范围；复用现有集中 Playwright config、npm 命令和 `@ci-*` family，并证明 CI 会选中 |
| scripts、`playwright*.config.*`、测试 runner、构建/门禁、CI、部署 | Owner 范围；普通实例不新增专属配置/端口或弱化门禁放行页面 |

新增稳定 ID 与 definition 是实例贡献，不属于新增框架模板。注册发现和发布目录由基础能力自动处理。每个实例的商品卡片、布局、CSS 可以独立实现；不要求抽共享商品容器 UI，公共语义动作与逻辑层契约仍须复用。

```text
definition -> config + Page + scene Controller + application/public
Page / 私有 UI -> 本实例展示文件 + ui/public
scene Controller / Policy -> 本场景 + application/public
共享基础 -> 底层服务/Native；不反向依赖实例/scene
```

允许的外部包按当前 AST 门禁确认，当前新 Page/scene 允许 React；不套用旧模板 npm 包规则。Page 不读取配置、分群或实验，不导入 Controller，也不跨实例导入另一实例的 Page/私有 UI。复制实验的私有展示代码可行，不能复制底层报价/支付/互斥实现。配置 content 可引用纯数据，路由/商品/分组保持静态，不用环境分支/函数生成。

definition 只包含 import 与默认工厂导出，参数为三个导入绑定，不内联函数、展开、额外执行或 IO。

## 状态与规则

| 事项 | 位置 |
|---|---|
| 布局、间距、滚动、动画、权益展开 | Page/UI |
| 设备档位选组、历史周期默认项、资格判断 | 纯 Policy，使用权威事实 |
| 选择、重试、主/挽留/All Plans 导航与恢复 | 场景 Controller |
| 精确报价、交易状态机、设备选择、结果、Bridge、基础埋点 | 公共应用能力/基础设施 |
| 展示哪个分群/阶段/实验实例 | 上游 evaluate/实验/Experience |

同实例 DUAL/MULTI 选择不是重新投放分群。共享 Controller 修改要检查实际调用方；不要通过固定 SKU 条件实现实例私有配置。

## 访问生命周期埋点边界

先在目标 checkout 确认公共 `App` 已对 CMS `PaywallTemplate` 和实例 `IndependentPaywall` 两条路径统一接入 `usePaywallVisit`，并确认双端 SDK 已提供 Native 退出/可见性通知。满足后，现有标准模板、个性化模板和新增独立实例都会复用同一套 `page_scroll`、`page_exit` 结算；普通页面不增加业务埋点代码。

业务页面只承担以下接入契约：

- 配置合法、准确的 `spmb`；它同时标识 PV 和访问生命周期页面。
- 关闭走公共 `closePaywall`/语义 action，支付走公共 Purchase Controller 与成功跳转，不直接关闭 WebView 或调原始 Bridge。需要挽留页时复用已具备生命周期结算的公共语义 action；public 未提供该入口时交 Owner 补齐，不由 scene 导入内部 recorder。
- 普通 All Plans、支付弹窗和实例内部展示状态沿用当前访问。若独立详情/对比页需要单独 PV、滚动深度或退出率，属于新的访问边界与公共事件语义，交 Tracking/框架 Owner 评审。

公共层负责：只采集触摸触发的主动垂直滚动及惯性、退出时按最大深度结算一次、未滚动不发 `page_scroll`、支付成功不发 `page_exit`、后台/取消手势/React teardown 不算退出、重复关闭去重；使用已接入的公共挽留 action 时，进入前结算，返回后建立新访问并重发 PV。修改事件名、字段、触发条件、250ms 有界提交、Native 信号或上述边界均属于 Owner 范围。

目标 checkout 或 App SDK 缺少任一层时，标记为能力/部署缺口；不能在模板或实例里手发事件来制造“已覆盖”结论。验收见 [verification.md#埋点与访问生命周期](verification.md#埋点与访问生命周期)。

## 产品运营、业务研发与平台职责

| 角色/任务 | 可以完成的范围 | 需要交对应 Owner 的情况 |
|---|---|---|
| 产品运营/页面生成 | 确认 Figma、商品和 Offer、默认策略、行为与语言；生成实例私有 UI/配置/素材并组合已有 Controller | 现有 Controller 不支持所需业务行为；公共 API 不支持所需宿主能力 |
| 业务研发 | 新增薄 Controller/Policy、适配 ViewModel/actions、场景测试；从 application/public 组合现有能力 | 需要新增 Native 方法、改变交易/公共事件/配置协议，或影响其他场景的共享实现 |
| 平台 Owner | 公共能力、schema/registry、Bridge/支付/runtime、门禁与发布契约 | 对公共修改盘点调用方并提供保护回归，另行明确实施范围 |

这些是代码贡献边界，不是所有页面都要额外请求审批。已有任务授权覆盖的业务研发和 Owner 修改继续完成；普通实例任务不得为了凑出页面擅自扩展公共范围。框架内部文件、不可绕过的契约和正确调用入口详见 [公共能力与薄 Controller](reusable-capabilities.md#普通实例任务不能修改的代码)。

## CMS 兼容

新实例的 `paywallId` 不得与任何已有 CMS Paywall 业务 ID/key 重名；具体核对见 [Paywall ID 互斥检查](cms-configuration.md#paywall-id-互斥检查)。以下是额外受保护页面，不是完整 CMS ID 清单。

新实例不得占用/迁移：

- `vip_never_subscribed_offer_page_v1`
- `vip_lapsed_offer_page_v1`
- `vh_nature_vip_purchase_paywall-copy-freetrialexp`
- `vh_nature_free_paywall-retain-copy-freetrialexp`

`vn_standard_h5_qa_20260915` 是已有标准页链路变体 key；核对 Action 实际 CMS ID，不当作待迁移实例 ID。标准模板继续解释 CMS 组件、多语言、文案、素材与商品。

旧模板仍只依赖同模板、允许 npm 包与 `template-kit/public`；manifest/key、CMS 数据结构与模板公共契约修改走 Owner review。共享旧模板修改先盘点发布影响。

旧模板固定文案的英文源、generated locale 资源及测试在 `src/i18n/`；按已确认语言范围同步维护。`cmsI18n.ts` 仅允许 `messagesFor()` 数据源的一行注册，不改别名/回退/RTL。新实例纯语言数据放实例目录，可由配置的 `content` 或私有展示模块引用；不强制添加 `content`，不为业务页面改共享语言解析或弱化门禁。

## Owner 评审 issue

评审针对共享行为/公共契约：新 Native 方法、报价/支付语义、公共事件、runtime/schema/catalog 协议、门禁/部署，或改变其他已发布页面的代码。

普通新实例、静态配置、复用 action、业务选品 Policy 不因新增需求自动申请模板审批。共享场景与公共框架分别评审；已有明确授权/结论覆盖时沿用，避免重复 issue。

未覆盖的公共缺口记录业务目标、frame、现有 public API、扩展语义、路径/调用方、保护测试和回滚方案，交对应 Owner。外部 issue 写入按会话授权和项目流程执行。只暂缓依赖缺口的部分。

最终检查全部 changed files 与授权；门禁不替代行为/调用方回归和真机验收。发现未授权改动保留现场，不自动回退他人文件。
