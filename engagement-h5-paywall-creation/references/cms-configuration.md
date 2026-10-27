# 实例配置、运行时数据与旧 CMS 兼容

## 两条加载链路

```text
最终 paywallId -> H5 公共入口查注册表
  命中 -> 本地 config + definition/Page/Controller
       -> POST /engagement/v1/paywall-runtime：商品元数据 + 认证用户事实
       -> 公共 Native/渠道报价、购买、Bridge 与埋点
  未命中 -> 原 /engagement/v1/cms-content -> 原 CMS 配置/模板
```

公共入口选择数据来源，Page/Controller 不重新判断 CMS。已命中后加载、租户核对或 runtime 失败，显示错误/重试，不切同 ID 的 CMS。路由不需要 contentSource。

直接实例的 evaluate 消费规则中的 `actionConfig.paywallSource=h5_instance`，返回目标 ID、动作、runtime 与归因，不从 H5 静态站点读取目录，不嵌入完整商品/页面配置，`content` 不提供实例页面的 `spmb`。`spmb` 由 H5 当前实例配置作为 PV 和访问生命周期的页面身份；当前双端 H5 直开路径不再将它与 evaluate 的页面名比较，仍校验有效主页面 PV、目标/触点归因并按本次打开去重。旧 Flutter 匹配保留，投放前须确认宿主已有相应 SDK 能力。

普通 Banner/弹窗的 `slotType=blocks` 仍读取 CMS 素材及素材 `spmb`；其 `OPEN_PAYWALL` 目标可以是独立实例。`paywallSource` 表示目标 Paywall 来源，不改变 blocks 素材来源。缺省或 `cms` 沿旧 CMS 解析，即使 `runtime=h5` 也不能自动当作实例。

## Paywall ID 互斥检查

CMS Paywall 与 H5 实例的业务 ID 必须不重名。比较的是 CMS 的 Paywall ID/key，不是数据库文档 `_id`。现有 CMS ID 留给旧链路；允许迁移的业务改用新实例 ID，并在实例部署后更新上游引用。

选定 ID 前核对 staging/prod 的现有 CMS Paywall ID/key、目标 checkout 的实例配置，以及两个环境已部署的实例目录。新 ID 不得命中 CMS 集合或占用其他 H5 实例；更新已有 H5 实例可以沿用其自身 ID，商品实验仍使用新 ID。记录核对环境、时间、数据来源和结果，不能只检查当前投放或四个受保护 ID。

无法读取所需 ID 清单时，将互斥核对标记为待验证，可以继续设计和实现草案；核对完成前不部署含该 ID 的实例制品、不发布其投放。

原因是 H5 初始化按 `paywallId` 查内部注册表，并不按 Admin 的 `paywallSource` 再选择来源。同名实例一旦部署，已有 `runtime=h5` 的 CMS 入口可能加载该实例；未修改 Admin 变体不能保证旧页面继续读取 CMS。Admin 发布冲突检查是额外防线，不能代替新增实例前的 ID 核对。

## 新实例配置

字段以当前 PaywallConfig schema 为准，不从旧 CMS DTO 全量复制。

本节定义结构约束。商品不存在、只有渠道 Offer、或不知道字段值从哪里取得时，按[商品建档与实例配置](product-onboarding.md)先完成参考商品、渠道身份、目录元数据和试用语义核对；不能只让配置通过 TypeScript/schema 就视为商业配置正确。

| 字段 | 职责 |
|---|---|
| paywallId、tenantId | 唯一身份与适用 App；公共层仍核对宿主/认证租户 |
| description | 内部说明，不是页面文案，不翻译 |
| spmb（必填） | PV 与 `page_scroll`/`page_exit` 的页面身份；当前 schema 仅允许 vip_purchase_product_page、free_trial_guide_page、pw_launch_never、pw_launch_lapsed，不得省略或自造值 |
| payType（可选） | 省略沿宿主默认；0 IAP、1 Airwallex、2 Stripe；显式 0 有效 |
| offers | 逻辑销售项到 Product/Offer 身份、计费类型、预期周期 |
| groups | 主推荐/挽留/All Plans/参考/档位用途，引用 offer key 与默认项 |
| content（可选） | 静态业务展示数据引用；页面在私有 UI/语言文件中维护文案和素材时可以不填 |

选哪组由 Policy 决定。配置没有网络、用户事实、实验分桶和金额；不维护平行 offer.config.ts，不新增实例/配置版本字段。

同 SKU 不同 Offer 保留独立销售项，默认项指向唯一 offer key。公共 schema/交易保证身份与引用，不新增固定某期 PRD 行数、分群枚举、SKU 或价格限制。

### offers 与 groups 的字段约束

`PaywallConfig` 是从共享 Zod schema 推导的 TypeScript 类型，不是需继承的业务类。配置使用 `as const satisfies PaywallConfig`，构建与运行时再按同一 schema 校验；不是只靠 TypeScript。

| 对象 | 允许的字段与约束 |
|---|---|
| 每个 offers 项 | 必填 productId（正整数形式字符串）、billingType（SUBSCRIPTION/ONE_TIME）、offerId（完整字符串/null）、offerType（NONE/INTRODUCTORY/PROMOTIONAL/WIN_BACK）；可选 expectedBillingPeriodMonths（正整数） |
| 每个 groups 项 | 必填 offerKeys（非空、不重复、均指向已声明 offer）、purpose（非空字符串）；可选 defaultOfferKey（必须属于本组） |

`offers` 与 `groups` 的字典 key 可按需求命名，但单个对象字段是严格 schema，不能随意新增。`purpose` 不是锁定 NEVER/LAPSED 的枚举；`main`、`primary`、`retention`、`all_plans` 是现有场景约定，具体 Controller/Policy 会检查自己所需的组。某 Policy 需要默认项时，即使公共 schema 允许省略，也必须填并通过场景校验。

`NONE` 要求 `offerId=null`；`PROMOTIONAL/WIN_BACK` 要求完整非空 ID；`INTRODUCTORY` 可按真实渠道契约使用 null 或完整 ID。同 Product/Offer 身份不能配置成 Native 无法区分的不同计费/优惠意图；不得仅按名称拼接 Offer ID。

`productId`、`offerId`、`offerType`、`billingType` 和 `expectedBillingPeriodMonths` 的权威来源与四类销售方案示例见[字段来源](product-onboarding.md#paywallconfigts-字段来源)。目录中的 `freeTrialDays`、用户资格与 Native 报价相位是三件不同的事实；不能仅因目录存在天数就把用户当作具备试用资格，也不能把一个应请求试用的销售项配置成 `NONE` 后再由页面文案补救。

一项 Offer 可同时被主页面和挽留组引用，组数不等于 SKU 数。商品矩阵先列“用途 × 设备档位 × 历史代际/周期”等实际业务维度，再为各行确定 offerKeys/defaultOfferKey；没有的维度不强加。例如启动 LAPSED 为 Gen2/Gen3 × 单/双/多设备六组，启动 NEVER 为三个设备组，首绑为 primary/retention 两组，见 [真实示例](instance-recipes.md)。

需要额外静态展示资料可用 `content`，但不能借它绕过商品 schema 存另一套 Offer、动态金额、资格或 IO。变更顶层/Offer/group 契约属于公共能力扩展；普通新 Paywall 只消费现有字段。

## 动态数据与交易

paywall-runtime 查询只需要去重 Product IDs，不带 Offer ID。公共入口请求含 paywallId、productIds、来源/语言和 app.tenantId 等上下文；服务端核对认证租户，再查对应 App 目录。客户端 tenant 是适用性声明，不是授权凭据。

设备数量、历史代际/周期、试用事实来自 runtime；需要活跃覆盖事实时使用 application/public 能力。缺失事实保留 unknown/null，不当作零或默认档位。Page 不发这些请求。

后续报价/购买使用完整 Product/Offer 身份与渠道。金额、币种、优惠阶段及试用来自实际报价，目录试用天数不证明资格。报价/购买身份一致，指定渠道不可用不静默切换。

必需商品/报价缺失阻止购买并重试；可选参考报价降级由 Policy 明确处理，不能污染主商品身份/曝光。

动态实现前的数据交接、旧元数据结构及选品规则见 [运行时输入](runtime-data-input.md)。新实例准备本地候选配置不等于本地伪造目录；仍须读取目标环境真实 runtime 商品详情。

## 目录与投放

- 实例配置/definition 自动发现，构建核对唯一 ID、保留 ID、绑定、静态数据和组引用，生成同包 dist/instance-catalog.json。
- 目录是已发布索引，不保存完整商品/文案，不是新 CMS 编辑器。
- 核对 H5、Backend、Admin 目标环境能力；本地实例存在不等于可投放。Admin 读取目标环境目录做候选和发布来源校验，不把实例 tenantId/spmb 固化到规则包；目录存在不证明 JS、图片、支付或真机已验收。
- 两种实例入口均设置 locked=true、OPEN_PAYWALL、actionConfig.runtime=h5、actionConfig.paywallSource=h5_instance；目标实例已在目标环境部署。
- 直接 slotType=paywall：cmsSlug 与 actionConfig.paywallId 一致，它是兼容字段，不要求创建同名 CMS 文档。
- slotType=blocks：cmsSlug 是已发布的 CMS 横幅/弹窗素材，actionConfig.paywallId 是独立实例 ID，两者可以不同。CMS 素材与实例分别校验目标环境；不能因为实例已部署就跳过素材校验。
- 发布 H5 后按授权配置 Experience/实验。回滚先撤投放引用再回滚制品；实例不独立部署。

## 旧 CMS 模式

仅维护已有个性化/标准 Paywall 才读取 CMS 实际 collection/editor/DTO 和模板字段、使用 marketing-cms。两份个性化页面和标准模板保留；标准内容包括商品、组件、文案、素材、动作及多语言。所有新增业务 Paywall 使用实例，不以“沿用模板”新增 CMS Paywall；blocks 素材不在此禁用范围内。

标准 templateKey 按旧协议留空/解析，专用模板用已注册 key。新实例不转成 CMS 模板，不为新增需求改商品矩阵编辑器。

旧操作：确认环境/key → Draft → 回读商品/Offer、源文案、支付/模板 → 预览 → 按授权翻译/发布 → 验收。重复 key 先查现有记录，不重复创建。

Draft、Publish 和 Transfer 分开，Draft 不代表 App 可见。新实例文案随 H5 发布；旧 CMS 翻译/跨环境传输沿原流程，不混用两条发布链路。

旧 CMS 默认先 Staging Draft，回读源文案和商品，再预览与翻译/发布。只有用户明确要求且对应验收完成，才依次 Staging → Pre、验证、Pre → Prod；不能跳过 Pre。重复 key 核对现有记录后更新，Transfer 不假定有通用回滚 API。
