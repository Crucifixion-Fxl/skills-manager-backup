# H5 动态页面的数据输入

适用于商品矩阵、用户分群、All Plans、挽留和升级比价页面。它不扩大模板 UI 的写入范围；模板仍只消费公开 view model / controller。触点数据链路按 `engagement-touchpoint-integration` 执行，Skill 不可用时可读[共享流程](https://gitlab.addx.ai/engineering/skills/-/blob/main/skills/experimentation/engagement-touchpoint-integration/references/h5-paywall-data-flow.md)，并先核对当前任务的环境和授权。

## 开发前交接

| 输入 | 核对 |
|---|---|
| 已确认 PRD / 场景行为 | 默认项、切换、挽留、X、返回、All Plans、提醒及购买结果 |
| evaluate / 宿主上下文 | Paywall、runtime、sceneKey、segmentKey、实验归因；CMS 不代替场景来源 |
| 真实 App cms-content | key / templateKey、完整候选、默认项、必要商品元数据；发布状态和环境正确 |
| 报价与购买身份 | productId + offerType + offerId，及现有框架需要的渠道参数 |

保存脱敏的完整服务响应作为可追溯输入；不保存 bearer、账号密码或个人信息。必要字段缺失时先修数据路径，不在模板中临时补 SKU 常量或把本地目录拼进服务 fixtures。

## 按场景选品，组件只展示

- CMS 配置的是完整候选，不必全部显示在主页面；其中可包含挽留和比价商品。
- defaultProductId 只控制其所属选择组的初始默认，不自动决定其他页面的商品。
- 场景规则匹配周期、设备覆盖、权益代际和 Offer 等已确认属性；具体 ID 来自 CMS。需要唯一候选时检查唯一性，不能用数组第一项静默兜底。
- 关联 offerMatrix 与 allProducts 时按现有契约处理数值 / 字符串 ID，在内部适配层归一化；不修改 wire response 类型。
- 保留完整 Offer 身份关联报价和购买，不把一个商品的全部 Offer 合并成同一个销售选项。
- 可见默认、当前选择与挽留选择分别管理；从挽留返回主页面时是否保持选择按已确认场景规则执行。
- 多设备比价需要单设备参考商品及真实报价；引用已有候选 / All Plans 可用数据，不因为不显示在主页面就跳过报价加载。

框架中已存在的场景规则直接复用。新增选品或导航能力涉及受保护路径时仍走 Owner 评审，不能在模板组件中直接 fetch、访问全局 state、调用 Native 或自行实现支付逻辑。

## 两种元数据结构都可能存在

当前矩阵页面可能是 `content.offerMatrix` 与 `content.allProducts`；标准 / 覆盖升级商品容器可能是 `content.mainArea[].products` 与该容器的 `allProducts`。实际以当前模板契约与服务返回为准，不为新页面顺带统一所有线上结构。

场景由 Admin / 宿主传入；H5 不从 experimentKey、variationId、Paywall 名字或 SKU 猜页面方案。分阶段投放的内容分群也不能反过来篡改真实用户属性或实验归因。

## 报价和验收边界

价格、货币、免费或付费优惠阶段和资格从现有渠道报价框架取得。freeTrialDays=0 不代表没有付费推荐优惠；正常续费价格不能代替首期报价。

优先用真实 CMS 内容验证浏览器渲染和交互；Mock Bridge 验证 UI / 契约，但不证明真机支付、权益或实际报价。交付分别记录数据准备、浏览器 UI、宿主触发、真实报价 / 支付 / 权益和埋点状态。

[首绑案例](https://gitlab.addx.ai/engineering/skills/-/blob/main/skills/experimentation/engagement-touchpoint-integration/references/vicohome-first-bind-case.md)中的商品和实验条件仅用于该需求。比如首绑 v3 主页面默认年包，但挽留匹配候选中的月包；不得把具体商品或“所有挽留都继承当前选择”固化到通用模板。
