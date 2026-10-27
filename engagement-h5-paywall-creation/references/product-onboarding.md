# Paywall 商品建档与实例配置

当 Product ID/Offer 尚未确认、需要新建商品，或不知道 `paywall.config.ts` 字段如何填写时读取本页。目标是复用已经在线上证明可售的商品结构，只修改经过确认的差异，并让商品库、渠道 Offer、H5 配置和真实报价保持同一销售身份。

商品库写入、渠道配置、H5 开发和投放是不同授权面。普通 Paywall 开发任务可以查询、生成 SQL 和 review 差异；没有明确写入授权时不得执行数据库变更、创建渠道商品或发布投放。不得把凭证、用户数据或生产连接信息写进 MR、Skill 或验证记录。

## 先判断是否需要新商品

| 情况 | 处理 |
|---|---|
| 现有 SKU 和 Offer 已满足需求，只是 H5 选品、默认项或试用配置错误 | 不建商品；修正实例 config/Policy，并用真实 runtime 与 Native quote 回归 |
| 同一 SKU 需要渠道支持的另一个 Offer，且渠道/Native 能以完整 `offerId` 区分 | 不复制 SKU；新增/确认渠道 Offer，在 `offers` 中保留同 `productId` 的独立销售项 |
| 需要独立渠道 SKU、不同基础套餐/周期/权益，或商品实验明确要求新的 Product ID | 新建商品；按下面的线上参考商品流程建档 |
| 只是币种、本地化价格或用户资格不同 | 先核对渠道与用户事实；不要仅为展示差异复制商品 |

`freeTrialDays` 表示商品目录声明的试用上限/候选天数，不代表当前用户具备资格。资格来自已认证用户事实，实际免费或付费首期相位来自 Native/渠道报价。因此“改变用户资格”通常不是修改 `camera.product` 一行可以完成的事情。

## 选择线上参考商品

不要从空白 `INSERT` 猜二十个字段。先在目标环境选择一个正在正常报价/购买的同类商品，优先级依次为：

1. 相同 `tenant_id` 和 App/支付渠道；
2. 相同 `tier_id`、设备/家庭权益和 `subscription_group_id`；
3. 相同订阅周期（`month`）与商品类型（订阅/一次性）；
4. 相同基础续费价格/币种；
5. 相同优惠形态：普通、免费试用、付费 introductory、promotional 或 win-back。

参考商品必须有目标环境真实 `paywall-runtime`、Native 报价或线上购买证据；名称相似、ID 相邻或 Figma 金额相同都不足以证明可复用。找不到完全相同的商品时列出每个候选的差异，由商品/支付 Owner 确认基准，不混合多行字段拼出新商品。

可先查询候选，不执行写入：

```sql
SELECT
  id, type, key_id, price, currency, subject, body, status,
  tier_id, month, tenant_id, additional_tier_uid,
  subscription_period, show_in_tier, subscription_group_id,
  free_trial, free_trial_duration, show_in_product_list
FROM camera.product
WHERE tenant_id = :tenant_id
  AND status = 0
  AND tier_id = :tier_id
  AND month = :billing_months
ORDER BY id;
```

`:tenant_id`、`:tier_id`、`:billing_months` 表示待绑定参数，不是可直接粘贴执行的 MySQL 字面量。执行查询前按目标环境的只读访问流程取得授权。

## `camera.product` 字段复制规则

以目标数据库当前 DDL、商品管理服务和 Owner 确认为准；下表用于 review 差异，不替代 schema。当前 Paywall 目录由 PE 从 `camera.product` 联表 `camera.tier` 读取 `id`、`tier_id`、`subscription_group_id`、`free_trial_duration` 及 tier 权益字段；H5 展示价格仍来自 Native，不读取数据库静态 `price`。其他支付/订单服务可能继续消费表中其余字段，不能因为 H5 未读取就随意置空。

| 字段 | 默认处理 | 只有什么变化时才修改 |
|---|---|---|
| `id` | 使用商品/支付 Owner 分配并确认未占用的新 ID | 新建 SKU 必改；不能根据相邻编号猜测 |
| `type`、`key_id` | 从同类型参考商品复制 | 商品类型或渠道契约明确变化 |
| `price`、`currency` | 从相同基础续费方案复制 | 基础价格或币种变化；introductory 首期金额以渠道 Offer 为准，不能擅自覆盖基础价 |
| `subject`、`body` | 按新商品真实周期、权益和优惠更新 | 新建 SKU 必须与商业含义一致，不能沿用错误试用天数 |
| `status` | 新建可售商品通常沿已确认参考值；上线前核对为目录可读取状态 | 上下架流程另有明确阶段 |
| `cdate`、`mdate` | 使用当前 UTC 时间 | 不复制参考商品历史时间 |
| `tier_id` | 相同权益时复制 | 设备/家庭上限或 tier 类型改变 |
| `month` | 相同订阅周期时复制 | 月/年/其他真实周期改变 |
| `tenant_id` | 必须与目标 App/认证 tenant 一致 | 不允许从其他 tenant 借商品 |
| `additional_tier_uid` | 相同叠加包关系时复制 | 权益组合明确改变 |
| `subscription_period` | 从同周期线上参考商品复制并记录来源 | 历史值并不总能可靠推导月/年，不能只根据枚举名猜 |
| `show_in_tier`、`show_in_product_list` | 从相同投放方式的参考商品复制 | 产品明确改变可见范围 |
| `subscription_group_id` | 同一互斥订阅组时复制 | 渠道订阅组真实改变；必须同时核对渠道配置 |
| `free_trial` | 先查看目标环境 DDL，再复制同类优惠的值 | 当前 camera schema 的历史语义是 `0=支持免费试用、1=不支持`，与常见布尔直觉相反，不能凭字段名填写 |
| `free_trial_duration` | 从同类商品复制 | 免费试用天数改变时填目标正整数；无免费试用填目标 schema 认可的无试用值 |

付费 introductory（例如首期低价）不是免费试用：不要仅因 `offerType=INTRODUCTORY` 就填写 `free_trial_duration`。首期金额、周期和资格必须在渠道 Offer 与 Native quote 中核对。

## 基于参考商品生成 SQL

获准准备建档 SQL 时优先使用显式列的 `INSERT ... SELECT`，让未变化字段来自一行已验证参考商品；商品/支付 Owner review 后，必须通过受管的 [NineData SQL Task](../../../data/ninedata/SKILL.md) 流程提交审批与执行，禁止使用任意直连方式绕过规则检查和审计：

```sql
INSERT INTO camera.product (
  id, type, key_id, price, currency, subject, body, status,
  cdate, mdate, tier_id, month, tenant_id, additional_tier_uid,
  subscription_period, show_in_tier, subscription_group_id,
  free_trial, free_trial_duration, show_in_product_list
)
SELECT
  :new_product_id,
  type,
  key_id,
  price,
  currency,
  :new_subject,
  :new_body,
  status,
  UTC_TIMESTAMP(),
  UTC_TIMESTAMP(),
  tier_id,
  month,
  tenant_id,
  additional_tier_uid,
  subscription_period,
  show_in_tier,
  subscription_group_id,
  :new_free_trial,
  :new_free_trial_duration,
  show_in_product_list
FROM camera.product
WHERE id = :reference_product_id
  AND tenant_id = :tenant_id
  AND status = 0;
```

如果价格、周期、tier、订阅组或展示范围也变化，在 SQL 中显式替换对应表达式，并在 review 表中逐项说明 Why。执行前确认参考查询恰好一行、新 ID 不存在；执行后确认影响行数恰好一行并回读新旧两行生成字段 diff。任何条件不满足都停止，不通过去掉 `WHERE`、放宽 tenant 或重复执行绕过。

## `paywall.config.ts` 字段来源

顶层字段继续遵守[实例配置契约](cms-configuration.md#新实例配置)。填写真实值时使用下表，不从参考页面盲拷：

| 字段 | 权威来源与填写规则 |
|---|---|
| `paywallId` | 当前独立投放实例 ID；完成 staging/prod CMS 与 H5 目录互斥检查。商品实验使用新 ID |
| `tenantId` | 目标 App 与认证 tenant；必须与商品目录的 `tenant_id` 一致 |
| `description` | 内部用途/场景说明，不放页面文案或敏感信息 |
| `spmb` | 实际页面 PV 身份；只能使用当前 schema 允许值，不根据商品类型猜 |
| `payType` | 仅在需求指定渠道时填写；省略沿宿主默认，不能为让报价成功静默切换渠道 |
| `productId` | 已确认渠道 SKU 与 `camera.product.id` 对应值，按 schema 写成正整数字符串 |
| `billingType` | 渠道商品性质：自动续订填 `SUBSCRIPTION`，一次性商品填 `ONE_TIME` |
| `offerType` | 基础正常销售填 `NONE`；免费试用或付费首期优惠填 `INTRODUCTORY`；促销填 `PROMOTIONAL`；召回优惠填 `WIN_BACK` |
| `offerId` | 渠道/支付 Owner 提供的完整 Offer ID。`NONE` 必须为 `null`；`PROMOTIONAL/WIN_BACK` 必须非空；`INTRODUCTORY` 按真实渠道契约可为 `null` 或完整 ID，不能拼接 |
| `expectedBillingPeriodMonths` | 目标 Native recurring quote 的预期周期，用商品 `month` 交叉检查；不能只根据历史 `subscription_period` 推断。一次性商品通常省略 |
| `groups.*.offerKeys` | PRD 中该页面状态/档位允许选择的销售项，均引用当前 `offers` key |
| `groups.*.defaultOfferKey` | 当前组产品确认的默认项，必须属于本组；其他组拥有各自默认项 |
| `content` | 可选静态展示数据；不能再存一套商品、价格、资格或 IO |

常见销售项：

```ts
offers: {
  monthlyBase: {
    productId: 'PRODUCT_ID', billingType: 'SUBSCRIPTION',
    offerId: null, offerType: 'NONE', expectedBillingPeriodMonths: 1
  },
  monthlyTrial: {
    productId: 'PRODUCT_ID', billingType: 'SUBSCRIPTION',
    offerId: null, offerType: 'INTRODUCTORY', expectedBillingPeriodMonths: 1
  },
  annualPaidIntro: {
    productId: 'PRODUCT_ID', billingType: 'SUBSCRIPTION',
    offerId: 'FULL_CHANNEL_OFFER_ID', offerType: 'INTRODUCTORY',
    expectedBillingPeriodMonths: 12
  },
  monthlyPromotion: {
    productId: 'PRODUCT_ID', billingType: 'SUBSCRIPTION',
    offerId: 'FULL_CHANNEL_OFFER_ID', offerType: 'PROMOTIONAL',
    expectedBillingPeriodMonths: 1
  },
  lifetime: {
    productId: 'PRODUCT_ID', billingType: 'ONE_TIME',
    offerId: null, offerType: 'NONE'
  }
},
groups: {
  primary: {
    purpose: 'primary',
    offerKeys: ['monthlyTrial', 'annualPaidIntro'],
    defaultOfferKey: 'annualPaidIntro'
  }
}
```

示例中的 ID 是占位符，不能发布。若真实 introductory 不使用独立 Offer ID，应按渠道证据填 `null`，不能为了匹配示例创建或伪造 ID。

### 免费试用的三层事实

1. 商品目录 `freeTrialDays`：SKU 最多/候选可提供多少天，来自 `free_trial_duration`。
2. Policy `qualifiedTrialDays`：只有已认证用户事实明确为合格时才为对应 offer key 提供；明确不合格给 `0`，未知资格不从目录自行授予。
3. Native quote `TRIAL`：决定本次页面是否能展示免费、试用周期以及购买请求的实际相位；不匹配时禁止购买或按已定义降级处理。

免费试用销售项必须使用与渠道一致的 `INTRODUCTORY` 意图。不能把它配置成 `NONE`、在 Policy 中强制 `0`，再依靠商品目录天数或页面文案声称免费。付费 introductory 的报价相位是 `INTRODUCTORY`，不是 `TRIAL`，也不启用免费试用提醒。

## 验证与交付证据

商品和 H5 合入前至少记录：

1. 参考商品 ID、目标环境、选择理由，以及新旧字段 diff；
2. 新 Product ID 的分配/唯一性与商品库回读（若新建）；
3. 渠道 SKU、完整 Offer ID/类型、基础周期和优惠阶段；
4. PE `tenant.product_catalog` 或 `paywall-runtime` 返回目标商品，`missingProductIds` 不包含它；
5. Native `getProductPrices` 请求/响应保持精确 `productId + offerId + offerType + billingType`，周期和相位符合预期；
6. config/Policy 单测覆盖每个销售项、默认项、合格/不合格/未知试用资格；
7. 浏览器 E2E 覆盖选择、展示、购买 payload 和错误报价 fail closed；
8. staging 真机验证真实报价、购买、权益、恢复购买，以及试用/付费优惠和提醒行为。

CI 可以验证 schema、引用、Policy 和模拟报价一致性，但不能证明渠道商品存在、用户真实资格或支付成功。交付中分别标记商品建档、渠道配置、runtime、浏览器、真机支付和权益状态；缺项写 `NOT_RUN`，不能用 Mock 替代。
