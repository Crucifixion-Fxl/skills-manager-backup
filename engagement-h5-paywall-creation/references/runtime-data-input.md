# H5 动态页面的数据输入

适用于主推荐、All Plans、挽留和升级比价。Page 仍只消费公开 ViewModel / actions，不请求目录或解析原始服务 DTO。先按实际 checkout 确认独立实例或旧 CMS 模式，详见 [加载与配置](cms-configuration.md)。

触点按 `engagement-touchpoint-integration` 与实际接入契约执行；其[共享数据流程](https://gitlab.addx.ai/engineering/skills/-/blob/main/skills/experimentation/engagement-touchpoint-integration/references/h5-paywall-data-flow.md)和首绑案例中的 CMS 步骤只适用于旧模式。新实例按已发布目录引用最终 ID，不能因旧指南要求补 CMS 草稿。

## 开发前交接

| 输入 | 核对 |
|---|---|
| 已确认 PRD / 场景行为 | 默认项、切换、挽留、X、返回、All Plans、提醒及购买结果 |
| evaluate / 宿主上下文 | 最终 paywallId、App/认证 tenant、来源、语言和实验归因；不在页面按实验重选 ID |
| 新实例静态配置 | 完整 Product/Offer 候选、组用途、默认 offer key；与同包发布目录一致 |
| 新实例真实 paywall-runtime | 商品元数据、用户事实、缺失 Product IDs；环境/租户/商品集合匹配 |
| 旧页面真实 App cms-content | key / templateKey、完整候选、默认项、元数据与原场景参数；发布状态/环境正确 |
| 报价与购买身份 | productId + offerType + offerId，及现有框架需要的渠道参数 |

保存脱敏的完整服务响应作为可追溯输入；不保存 bearer、账号密码或个人信息。必要字段缺失时先定位数据路径，不把本地商品详情拼成真实服务 fixtures。新实例静态 Product/Offer 引用是正式配置，不能替代服务端商品详情、用户事实或渠道报价。

商品尚未存在、需要为同 SKU 新增 Offer，或候选配置来源不明确时，先执行[商品建档与实例配置](product-onboarding.md)的决策与证据流程，再进入本页的运行时核对。创建商品库记录、配置渠道 Offer、填写实例配置和验证用户资格是不同步骤，不能用其中任一步替代其他步骤。

## 新实例数据准备

- 公共入口收集配置中的去重 Product IDs，请求 `POST /engagement/v1/paywall-runtime`，包含 paywallId、来源/语言和 app.tenantId 等上下文，不带 Offer ID。服务端核对认证租户，客户端 tenant 不能代替授权。
- runtime 返回商品详情及设备数量、历史代际/周期、试用事实等上下文；完整 Offer 身份留在配置，用于后续精确报价/购买。覆盖升级事实用 `application/public` 已公开能力获取。
- 设备/历史事实未知保持 null/unknown，不按零、数组首项或默认设备档位兜底。必需商品缺失停止相应购买并重试；可选参考数据降级由业务 Policy 明确处理。
- 双/多设备页面相同时由 Policy 在当前配置内选组；大用户分群、流失阶段与实验投放已由上游决定最终实例。不要修改真实用户事实或实验归因。
- 主、挽留、All Plans 的候选与选择分别管理；同 SKU 的不同 Offer 不合并。默认项只作用于所属组，参考商品不变成购买项。
- Controller 从 `application/public` 获取模型和交易能力；Page 不导入配置或 Controller，不直接网络/Native。close/restore 拒绝由 Controller 维护动作错误与重试。

先确认依赖已在目标 checkout 具备；没有实际 runtime 或 public API 时不能用本地目录伪装验收通过。独立配置、设计分析和静态页面准备仍可推进。

## 旧 CMS 场景选品

- CMS 配置的是完整候选，不必全部显示在主页面；其中可包含挽留和比价商品。
- defaultProductId 只控制其所属选择组的初始默认，不自动决定其他页面的商品。
- 场景规则匹配周期、设备覆盖、权益代际和 Offer 等已确认属性；具体 ID 来自 CMS。需要唯一候选时检查唯一性，不能用数组第一项静默兜底。
- 关联 offerMatrix 与 allProducts 时按现有契约处理数值 / 字符串 ID，在内部适配层归一化；不修改 wire response 类型。
- 保留完整 Offer 身份关联报价和购买，不把一个商品的全部 Offer 合并成同一个销售选项。
- 可见默认、当前选择与挽留选择分别管理；从挽留返回主页面时是否保持选择按已确认场景规则执行。
- 多设备比价需要单设备参考商品及真实报价；引用已有候选 / All Plans 可用数据，不因为不显示在主页面就跳过报价加载。

旧场景按原公开 Controller 复用。新增业务选品/导航不要继续塞入 Template Kit；共享场景和公共能力按各自 Owner review。不能在模板组件中直接 fetch、访问全局 state、调用 Native 或自行实现支付逻辑。

## 旧元数据结构

当前矩阵页面可能是 `content.offerMatrix` 与 `content.allProducts`；标准 / 覆盖升级商品容器可能是 `content.mainArea[].products` 与该容器的 `allProducts`。实际以当前模板契约与服务返回为准，不为新页面顺带统一所有线上结构。

旧场景由 Admin / 宿主传入；H5 不从 experimentKey、variationId、Paywall 名字或 SKU 猜页面方案。分阶段投放的内容分群也不能反过来篡改真实用户属性或实验归因。

## 报价和验收边界

两种模式的价格、货币、免费或付费优惠阶段和资格均从既有权威事实/渠道报价取得。目录试用天数不能证明用户资格；只有已判定资格的 Policy 才可向公共 Controller 提供 qualifiedTrialDays。freeTrialDays=0 不代表没有付费推荐优惠；正常续费价格不能代替首期报价。

优先用目标环境真实 runtime（新实例）或 cms-content（旧页面）验证浏览器渲染和交互。Mock Bridge 验证 UI / 契约，但不证明真机支付、权益或实际报价。交付分别记录数据准备、浏览器 UI、宿主触发、真实报价 / 支付 / 权益和埋点状态。

[首绑案例](https://gitlab.addx.ai/engineering/skills/-/blob/main/skills/experimentation/engagement-touchpoint-integration/references/vicohome-first-bind-case.md)中的商品和实验条件仅用于该需求。比如首绑 v3 主页面默认年包，但挽留匹配候选中的月包；不得把具体商品或“所有挽留都继承当前选择”固化到通用模板。
