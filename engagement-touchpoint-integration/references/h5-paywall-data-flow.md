# H5 Paywall：先数据链路，再页面实现

适用于 `slotType=paywall`、`OPEN_PAYWALL` 直接打开 H5 的接入。它不要求新增素材 block 或 SDK builder。区块触点仍使用 [原接入流程](cms-block-touchpoint-workflow.md)。

本流程负责需求核对与数据交接，不扩大任何代码、配置、生产发布或消息发送权限。模板 UI 按 `engagement-h5-paywall-creation` 执行；框架与 CMS schema 缺口仍按已有 Owner 评审边界处理。同一授权任务可组合这些流程，无需另建 Agent 窗口。

## 职责与数据关系

```text
宿主在业务机会完成后触发触点
  → PE 按实验依赖取得权威属性
  → GrowthBook 返回 Experience key
  → Engagement 已发布快照解析 Paywall / runtime / actionContext
  → 宿主透传 scene、segment 和归因参数
  → H5 框架请求 App cms-content
  → 模板消费框架 view model / controller
```

| 系统 | 负责 | 不代替 |
|---|---|---|
| 商品目录 | SKU、订阅周期、权益、设备覆盖等元数据 | 商店的实际优惠报价和购买资格 |
| PE / GrowthBook | 属性口径、真实受众、实验条件、稳定分桶 | 页面选品和关闭行为 |
| Engagement Admin | 触点、Experience、scene / segment / Paywall 映射、快照 | 商品列表和价格 |
| Marketing CMS | 候选商品、Offer 身份、默认商品和适用的 All Plans | 未要求动态配置的页面行为 |
| Engagement Service | 按已支持的数据结构取得内容并补齐商品元数据 | 根据 SKU 猜页面方案 |
| H5 场景 / 框架 | 按场景选品和导航、报价、购买、Bridge、归因 | 在模板组件中直接实现这些底层逻辑 |

`sceneKey` 表示页面方案，`segmentKey` 表示该 Experience 选择的内容分群。真实用户属性与所展示的内容分群可以不同：分阶段上线可能让后期人群先使用早期内容。保留真实实验归因，不重新给用户分类。

## A. 确认业务输入和 review 节点

读 PRD、用户最新决策和设计师目标 frame。业务规则遵循已确认 PRD，Figma 提供视觉；冲突应记录并核实，不能用硬编码价格掩盖。

输出需求表：用户条件、业务触发时机、主页面候选 / 默认项、All Plans、挽留规则、X / 返回行为、渠道和版本门槛。记录已有授权与用户要求的阶段 review；只在约定节点等待确认，不能将首绑项目的 review 偏好变成所有任务的固定审批。

候选列表可能包含主页面不展示、但挽留或比价需要的商品。明确每个商品的用途，不把整个候选列表直接渲染为主页面。

## B. 核对商品目录与 CMS 能力

1. 先查询目标环境的商品目录，核对 tenant、tier / 权益、周期、设备覆盖和订阅组。缺商品时按授权准备新增与回滚 SQL；不要为了新页面重复建已有商品。
2. 常规续费价格、免费试用和付费优惠阶段分别核对；优惠资格与本地价格最终来自真实渠道报价。
3. 查模板注册、CMS selector / validator / transformer 是否支持当前数据结构；缺口先列出并进入已授权的代码 / Owner 流程。
4. 使用既有商品选择器配置候选、Offer 和默认项。`offerId` 当前为空是这次配置，不应直接变成对未来运营配置的永久禁止。
5. 不因单一场景增加关闭行为、挽留 SKU、场景等 CMS 字段。只有产品明确需要动态运营这些行为时再设计扩展。
6. 保存与发布后回读实际数据，确认 Draft / Published 状态与目标环境；Save Draft 不等于 App 可读。

矩阵字段、默认项位置、元数据字段和周期编码均以当前服务契约为准，不靠模板名称或数组顺序猜测。商品自带的设备档与 Admin 选择的内容分群也不是新增 Paywall 顶层运营字段。

## C. 核对 PE 属性、触点和实验

- 先读当前分支实现、GrowthBook attribute 定义及实际属性来源，再判断是否需要新增。确认值类型、缺失值、设备范围和历史语义；不能从“新用户”标题自行推导注册天数或购买状态。
- 宿主决定业务时机与同一机会去重，GB 决定准入。设备数量条件本身不能证明刚完成绑定。
- 记录 `slug → Feature → Experience key → sceneKey / segmentKey → paywallId`。GB 的 value 必须在实际快照中存在；CMS 的 templateKey 从内容取得。
- 确认 Admin 编辑、校验、持久化、快照组装和 evaluate 均保留 `runtime=h5` 及 actionContext；UI 能选择不代表运行时能收到。
- 日常配置走正式 Admin，发布目标选择 App staging。Admin staging UI 研发沙盒与 App staging 规则源不同；鉴权与写入保留 [Admin API 规则](admin-api-auth.md)，不能失败后自行回退 UI。
- 使用 experiment-ref 时核对实验 phase 的真实 condition，不能只把条件写在说明或引用层。实验索引、返回值和权重明确记录。
- 无业务疲劳限制时，按框架要求保留 `strategy=none`、`maxImpressions=null`；一次机会去重不等于账号永久一次。
- 先按授权发布 staging 快照并回读，实验是否启动遵循当前授权。不能因为配置完成就宣称 UI 已可展示，也不自动扩大迁移开关或生产流量。

同一触点可扩展已有配置：先查重，再增量添加当前需求的规则和 Experience，保留已生效分支。不要每个 PRD 都重建一份相同触点。

## D. 用真实 App 接口完成数据交接

参考 `personalized-paywall-uat` / QATools 已有流程：注册账号、登录、绑定 Mock 设备，确认权威属性，再调用：

- `POST /en/engagement/v1/touchpoints/evaluate`
- `POST /en/engagement/v1/cms-content`

真实实验使用足够账号自然覆盖目标变体，并确认重复评估稳定。若使用强制规则仅检查字段，标明它不是实际分桶验收，不能伪造实验归因。边界围绕当前需求的版本、人群、设备数、时间阈值和缺失属性设计，不复制首绑用例数量作为统一要求。

逐层核对：业务 result、触点、Experiment / Experience、scene / segment、Paywall key、templateKey、候选与默认项、商品元数据。仅 HTTP 200 不代表内容完整。

保留未改写的完整响应和脱敏请求上下文；认证信息、密码、真实用户个人数据不进入 MR 或共享 fixtures。ID 类型跨字段不同时，仅在验证器 / H5 适配层归一化关联，不修改服务原始响应。

`evaluate` 的现有预取契约继续保留；当前需求不消费完整渲染 context 时，不额外增加数据库查询。H5 所需完整数据从 `cms-content` 取得。

## E. 缺字段或版本不符时的排查

1. 回读 CMS 发布内容、快照及 evaluate 映射，确认请求的是目标 Paywall。
2. 对照当前 DTO / transformer 和实际运行镜像 SHA / digest；代码已合入、CI 成功或 Argo Healthy 不证明服务运行了该代码。
3. 对 `offerMatrix` 收集当前契约下 offers 与 All Plans 引用的商品 ID，按既有顺序去重，从 PE 目录补到 `content.allProducts`。Offer / 默认项仍保留在矩阵里，不能把矩阵业务字段混入元数据。
4. 标准商品容器可能使用 `mainArea[].allProducts`。保留既有补全路径；若本次只需接入新需求，不顺带统一线上结构。
5. 只有数据结构不受支持或实现确有缺口时才改服务，不为每个新模板增加业务白名单。

修改共享处理逻辑时验证已有结构和字段兼容。复测范围与实际改动匹配；只检查新场景内容时，不默认扩大为完整旧 Paywall 回归，更不能据此声称旧场景已验收。

## F. 进入 H5 与分层验收

开发动态页面前，交接真实 CMS JSON、宿主 scene / segment 参数、候选用途和报价 / 购买身份。必要字段缺失时先修数据路径，不能自行拼接目录数据伪装成完整服务返回。视觉分析和不依赖数据的准备可以并行进行。

模板只消费公开 view model / controller。候选过滤、挽留匹配、默认选择和关闭行为落在已批准的场景 / 框架扩展点，不能在组件中直接 fetch、调用 Bridge 或复制支付状态机。

交付分别声明：配置回读、API 路由 / 内容、浏览器 UI / 契约、宿主触发与 WebView、真实渠道报价 / 支付 / 权益、埋点。前一层通过不替代后一层。MR 中附证据、未完成项和当前需求的下一 review 节点。

首绑的具体参数和后续三个需求差异见 [VicoHome 案例](vicohome-first-bind-case.md)。
