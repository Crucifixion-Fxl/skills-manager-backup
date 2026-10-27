# 公共能力与薄业务 Controller

基于 Engagement `40a3358bafa37d75f3e4a45351193222e87c9e63`（含 !718）。执行时以目标 checkout 的 public 导出、类型、schema 和调用方为准。入口为 [application/public](https://gitlab.addx.ai/services/value-added/engagement/-/blob/40a3358bafa37d75f3e4a45351193222e87c9e63/paywall-h5/src/application/public/index.ts) 与 [ui/public](https://gitlab.addx.ai/services/value-added/engagement/-/blob/40a3358bafa37d75f3e4a45351193222e87c9e63/paywall-h5/src/ui/public/index.ts)，不直接导入它们内部的 payment/Bridge 实现。

## 公共能力复用表

| 能力 | 正确入口 | 业务方仍需决定什么 |
|---|---|---|
| 销售项/报价请求身份 | resolveSalesOptions、quoteRequests；常规场景由 usePaywallController 调用 | Policy 选择哪些 offerKeys、默认哪项；不能重造请求或混淆同 SKU 的不同 Offer |
| 选品、Native 报价、报价重试、交易执行 | usePaywallController(input, selection, capabilities?) | 将 quotedOptions/selectedKey/selectedPrice/priceStatus/priceError/retryPrices/canPurchase 映射 ViewModel；布局由 Page 实现 |
| 可购买条件与购买忙碌状态 | flow.canPurchase、flow.isBusy()、flow.busy | 结合场景导航切换、操作 pending 等附加限制；不能把公共校验改成“有价格就可买” |
| 购买 CTA 与框架弹层 | application 的 purchaseAction/frameworkOverlay；Page 的 ui/public.PurchaseAction | CTA 文案/样式、disabled 与原有漏斗展示语义；按契约呈现 overlay |
| 关闭、恢复购买、帮助与法律入口 | flow.close、restorePurchases、openSupportArticle、legalActions | X 是关闭还是先挽留、动作失败提示、法律标题；通过公共操作锁调用 |
| 互斥、等待、错误反馈、显式动作重试 | usePaywallActions({scopeKey, isBusy, onError?}) | 错误文案/类型、哪些动作进入同一锁、是否展示 retry；不自建锁或 timeout/replay |
| 提醒开关与 Native 状态/权限 | usePaywallController 的 trialReminder capability | 是否启用、是否在当前商品/页面显示；资格不由开关决定 |
| 参考/续费报价 | useReferencePrices | 参考哪些已验证商品、是否必须、失败如何降级，以及币种/周期匹配后的比较文案 |
| 当前套餐覆盖事实 | fetchServiceCoverage | 是否要查询、加载/失败状态、是否升级、目标商品；API 只返回事实，不自动做业务决策 |
| 报价相位、周期、金额基础处理 | getDisplayPhase/getRecurringPhase、microsToNumber、normalizeRecurringPeriod、billingDurationParts、approximatePeriodDays、formatQuoteMoney | 本次优惠/续费说明、折扣/每设备价格展示规则；近似天数不是扣款日期 |
| 现有语言与插值 | application/UI public 的 t/tf/getCmsLocale | 实例自己的完整翻译资源、加载与格式；这些入口不会请求 CMS |
| 实例绑定 | definePaywallInstance + PaywallConfig 类型 | 提供静态 config、私有 Page、已有或薄业务 Controller；不手改注册表 |

`usePaywallController` 主要封装逻辑层；UI 不放进 Hook。`PurchaseAction` 是 Page 可用的公共语义 UI 组件，消费 Hook 提供的不透明 handle 并维持公共埋点契约，不是另一份支付流程。商品卡片/单选图标/CSS 无须在多个实例间抽成公共 UI。

下面的能力由公共入口/容器自动负责，无需新增业务 Hook：本地注册表查找、租户核对、paywall-runtime 商品/用户事实加载、初始化错误页与重试、主页面 PV、触点归因基础字段、系统栏及公共购买结果。目标 checkout 若已在 `PaywallTemplate` 和 `IndependentPaywall` 接入 `usePaywallVisit`，主动触摸滚动与退出生命周期也由公共 recorder 负责，业务只需提供正确 `spmb` 并调用公共关闭/购买动作。不能在 Page/scene/config 再实现一套。用现有 InfoRegion、ProductRegion、ProductRetryAction、PurchaseRegion 等语义入口完成对应区域/操作契约；不自行发原始埋点。

## usePaywallController 的输入与边界

场景传入 `selection={offerKeys, defaultOfferKey, qualifiedTrialDays?}`。offers/groups 来自 config，权威设备/历史/资格事实来自 input.runtime；Policy 先校验并选组。该 Hook 不决定 NEVER/LAPSED、流失阶段、实验组或哪个 Paywall ID。

可选 capabilities：

- `trialReminder: true`：暴露 trialReminderEnabled、trialReminderPending、toggleTrialReminder(enabled)。默认不开启；Native 管理状态/权限，失败保留原值，是否推送由宿主/服务端负责。
- `quoteCompleteness: 'all' | 'selected'`：缺省 all，所需报价都有效才可购买；selected 由场景明确选择，选中项仍必须有效，失败默认项不能自动换为另一个报价成功的商品。
- `purchaseTracking: {buttonState, selectedPlanType}`：沿现有业务漏斗提供展示语义，不能覆盖商品身份、渠道或校验。

`quotedOptions` 是已进行身份/周期校验的展示候选；不要重新拼 offers 或绕开该结果。关闭和恢复返回 Promise，Native 拒绝/超时继续 reject；统一错误显示与互斥由 usePaywallActions 完成。提醒能力不意味着用户有试用资格，最终报价相位才是购买依据。

当前提醒 Hook 的 Native 失败处理是保留状态并内部捕获异常，不向业务动作传播错误。因此给 toggleTrialReminder 外层加 onError mapper 不代表已有提醒失败提示/重试；新需求若要求这种反馈，需要先评估公共 capability 的缺口，由 Owner 扩展，不在 Page 重调 Bridge。

## 薄 Controller 如何组合

常规业务只编排五件事：Policy 选品 → 公共 flow → 公共操作锁 → 页面 ViewModel → 语义 actions。没有 Controller 基类继承要求，不把 UI 或分群规则塞入公共 Hook。

下面摘取当前 LAPSED 接法，省略 ViewModel 的价格/文案映射；不是完整可发布 Controller：

```ts
const selection = selectLapsedOffers(input.config, input.runtime.context, defaultByHistory)
const flow = usePaywallController(input, selection)
const operations = usePaywallActions({
  scopeKey: input.config.paywallId,
  isBusy: flow.isBusy,
  onError: () => t('launch_lapsed_action_failed', 'Action failed. Please try again.')
})
const actions = {
  purchaseAction: wrapPurchaseAction(flow.purchaseAction, next => operations.run(next)),
  frameworkOverlay: flow.frameworkOverlay,
  selectOffer: (key: string) => { if (!operations.isLocked()) flow.selectOffer(key) },
  retryPrices: () => operations.run(flow.retryPrices),
  close: () => operations.run(flow.close),
  restorePurchases: () => operations.run(flow.restorePurchases)
}
```

Page 只消费 Controller 输出：商品用 viewModel 渲染，选择调用 actions.selectOffer，购买交给 PurchaseAction，关闭/恢复调用语义动作。新增业务需要新的默认策略、导航或模型时，研发可以写薄 Controller；页面行为完全一致时直接复用已有 Controller，运营不用重写交易逻辑。

操作互斥的接入约束：

- 同一场景购买、关闭、恢复、提醒、法律页和重试使用同一个 `run()`；`isBusy` 传公共 flow 的同步方法，不能只读 React 的 busy 布尔值。
- 选择和内部导航检查 `isLocked()`；仅用 disabled 或 locked 的渲染快照不能防同批次竞争。
- `onError` 由场景映射文案或错误类型，也可逐动作覆盖；无 mapper 时异常继续传播。pending/error/locked 映射已有 UI，不隐吞错误。
- `retry()` 只显式重试最后一个已映射失败的动作；开始其他动作、成功或 clearError 清除旧重试，不自动重放 Native 操作。
- scopeKey 使用当前 paywallId；作用域切换/卸载后的异步结果隔离由 Hook 负责，不能在实例自建全局锁。
- **不要嵌套同一个非重入锁。** LAPSED、首绑、绑定更多用 wrapPurchaseAction 接一次 run；现有 NEVER 用 Page 的 runPurchase 接一次 run，保留原 purchaseAction。不要同时套两种入口，否则内层被锁拒绝，购买不会执行。

场景仍拥有：页面 surface、返回主页面恢复选中项、组键约定、历史周期推荐、资格策略、参考价格是否可降级、错误文案、价格展示语义和 ViewModel。业务适配不是公共框架，允许按任务授权修改并回归共享调用方。

## 普通实例任务不能修改的代码

以下为 Paywall H5 根目录下的公共范围；普通产品运营/业务实例任务只消费，不改实现：

| 范围 | 不得为普通新页面修改的内容 |
|---|---|
| src/application/public、src/ui/public 及背后的共享实现 | 公共导出/类型/语义动作，usePaywallController/usePaywallActions/参考报价等实现，不添加需求分支 |
| src/application/paywallConfig*、instanceRegistry、runtime/services | schema、ID 发现/保护、初始化/租户/商品/用户事实协议、CMS 兼容来源与失败路径 |
| src/payment、bridge、state、tracking、renderer、template-kit | 报价/购买状态机、渠道/目标选择、Bridge 协议、全局状态、公共 PV/归因、`page_scroll`/`page_exit` 生命周期、旧模板契约 |
| scripts、Vite/构建配置、CI、部署 | catalog 生成、导入/静态配置/埋点门禁、保护用例、approved baseline 与发布契约 |
| Backend、Admin、SDK | evaluate/paywall-runtime/来源协议、投放与曝光接口；不能为新增 UI 另改热路径或重新加入静态站点依赖 |

具体文件名以目标 checkout 为准，不能利用改名或 private import 绕过保护。新实例自动注册，不改 glob/手工 ID 表；模板例外仅限已有个性化/标准页面，不向 CMS editor 加新需求字段。

公共能力确有缺口时，业务方先描述所需行为、现有 API 不足及受影响调用方，交对应 Owner review/授权后按公共能力任务扩展并补保护回归。这不是永久禁止平台演进，也不因缺口阻塞已可独立完成的 UI/配置。

日常允许修改：本实例 config、definition、私有 UI/语言/素材/测试；业务研发按授权修改本场景的薄 Controller/Policy/模型适配。diff 最后按 [template-boundary.md](template-boundary.md) 核对边界，再按 [verification.md](verification.md) 验证实际使用的能力。
