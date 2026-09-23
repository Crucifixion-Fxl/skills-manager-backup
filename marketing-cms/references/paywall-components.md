# Paywall 组件详解

## Paywall 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `key` | string | Yes | 唯一标识符，被 GrowthBook variation value 引用 |
| `tenantId` | string | Yes | OEM 品牌（见下方租户列表） |
| `spmb` | string | Yes | SPM 埋点标识，如 `vip_purchase_product_page` |
| `description` | string | No | 内部备注 |
| `payType` | string/null | No | 支付方式（"0"=IAP, "1"=Airwallex, "2"=Stripe, null=默认） |
| `components` | array | Yes | 有序组件数组 |
| `_status` | enum | Auto | `draft` / `published` |

OEM 租户（tenantId）可选值见 SKILL.md。

---

## 组件类型

Paywall 页面由 `components` 数组组成，每个组件共有字段：

| 公共字段 | 说明 |
|---------|------|
| `blockType` | 组件类型标识 |
| `componentType` | 同 `blockType` |
| `layoutConfig.bottomPadding` | 底部间距（px），默认 24，bottom-area 通常为 0 |

---

## 1. navi-bar — 导航栏 / Hero Banner

| 字段 | 类型 | 说明 |
|------|------|------|
| `style` | enum | `hero_banner`（大图背景）/ `top_image`（顶部图片）/ `default_app_bar`（标准导航栏） |
| `title` | string | 主标题，如 `"Awareness Service"` |
| `subTitle` | string | 副标题（注意驼峰：`subTitle` 不是 `subtitle`） |
| `backgroundImage` | media/null | 背景图，关联 media 集合 |

```json
{
  "blockType": "navi-bar",
  "componentType": "navi-bar",
  "style": "hero_banner",
  "title": "Awareness Service",
  "subTitle": "AI-powered security for your home",
  "backgroundImage": { "id": "media_id_here" },
  "layoutConfig": { "bottomPadding": 0 }
}
```

## 2. carousel — 轮播图

| 字段 | 类型 | 说明 |
|------|------|------|
| `style` | enum | `default`（标准全幅）/ `card`（卡片式，用于弹窗场景） |
| `images` | array | 轮播项数组（字段名是 `images` 不是 `slides`） |
| `images[].image` | media | 图片，关联 media 集合 |
| `images[].title` | string | 标题（可选） |
| `images[].subtitle` | string | 描述文字（可选） |

```json
{
  "blockType": "carousel",
  "componentType": "carousel",
  "style": "default",
  "images": [
    {
      "image": { "id": "media_id" },
      "title": "60-day Cloud Storage",
      "subtitle": "Access more past recordings from anywhere anytime."
    }
  ],
  "layoutConfig": { "bottomPadding": 24 }
}
```

## 3. product-container — 商品容器

| 字段 | 类型 | 说明 |
|------|------|------|
| `layout` | enum | `vertical`（竖向，对应 card-long-horizontal）/ `horizontal`（横向，对应 card-square-full） |
| `tierType` | string | 套餐等级，如 `"3"`（Gen3） |
| `products` | array | **全部**可选商品 `[{ productId: "30401" }, ...]` |
| `visibleProducts` | array | 截面直接展示的商品卡片（通常 2 个） |
| `defaultProductId` | string | 默认选中的商品 ID |

### visibleProducts 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `productId` | string | 商品 ID，需与 `products` 数组中一致 |
| `displayName` | string | 卡片展示名称，如 `"Annual Family Plan"` |
| `cardStyle` | enum | `card-long-horizontal` / `card-square-full` / `card-square-simple` |
| `badges` | array | 角标配置（见下方） |

### 商品前置条件

`products` 中的 `productId` 必须已在外部商品服务注册，否则 Admin UI 下拉框无法选择。

### cardStyle 对应规则

| layout | 对应 cardStyle |
|--------|---------------|
| `vertical` | `card-long-horizontal` |
| `horizontal` | `card-square-full` |

### Badge 类型

`save` badge 让 App 根据实际价格动态计算节省金额，避免价格调整后 badge 文案与实际不符。`tip` badge 是硬编码文案，仅用于与价格无关的临时活动标签（如"限时特惠"）。

| type | 说明 | 适用场景 |
|------|------|---------|
| `save` | 动态计算节省金额，需配 `calculationRule` | 年付商品对比月付的节省 |
| `tip` | 手写文字标签，需配 `tipSetting.tipText` | 活动临时标签 |

`save` badge 要求 `visibleProducts` 至少有 2 个商品（需要月付和年付做对比），单商品 paywall 不能用 save badge。

```json
{
  "type": "save",
  "calculationRule": {
    "formulaType": "monthly_annual_diff",
    "compareWithProductId": "30401"
  },
  "tipSetting": { "tipIcon": null }
}
```

完整 product-container 示例：

```json
{
  "blockType": "product-container",
  "componentType": "product-container",
  "layout": "vertical",
  "tierType": "3",
  "products": [
    { "productId": "30401" }, { "productId": "30601" }, { "productId": "30901" },
    { "productId": "30402" }, { "productId": "30602" }, { "productId": "30902" }
  ],
  "visibleProducts": [
    {
      "productId": "30402",
      "displayName": "Annual",
      "cardStyle": "card-long-horizontal",
      "badges": [{ "type": "save", "calculationRule": { "formulaType": "monthly_annual_diff", "compareWithProductId": "30401" } }]
    },
    {
      "productId": "30401",
      "displayName": "Monthly",
      "cardStyle": "card-long-horizontal",
      "badges": []
    }
  ],
  "defaultProductId": "30402",
  "layoutConfig": { "bottomPadding": 24 }
}
```

## 4. comparison — 功能对比表

| 字段 | 类型 | 说明 |
|------|------|------|
| `template` | relationship/ID | 引用 `comparison-templates` 集合（depth≥1 时展开） |
| `tips` | string | 对比表底部提示文字（可选） |

`members` 和 `features` 字段是只读的，从 template 自动派生。配置时只需指定 `template` ID。

```json
{
  "blockType": "comparison",
  "componentType": "comparison",
  "template": "comparison_template_id_here",
  "tips": "* New Gen3 features",
  "layoutConfig": { "bottomPadding": 24 }
}
```

## 5. feature-list — 权益列表

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 区块标题，如 `"What you will get"` |
| `titleIcon` | media/null | 标题图标（可选） |
| `features` | array | 权益项数组 |
| `features[].icon` | media | 权益图标 |
| `features[].title` | string | 权益名称 |
| `features[].description` | string | 权益描述 |

```json
{
  "blockType": "feature-list",
  "componentType": "feature-list",
  "title": "What you will get",
  "features": [
    {
      "icon": { "id": "media_id" },
      "title": "Cloud Storage",
      "description": "Store up to 60 days of video history in the cloud"
    }
  ],
  "layoutConfig": { "bottomPadding": 24 }
}
```

## 6. testimonial — 用户评价

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 区块标题 |
| `titleIconLeft` / `titleIconRight` | media/null | 标题装饰图标 |
| `userCount` | string | 用户数，如 `"180K+"` |
| `testimonials[].rating` | number | 星级（1-5） |
| `testimonials[].userName` | string | 用户名（已脱敏） |
| `testimonials[].title` | string | 评价标题 |
| `testimonials[].content` | string | 评价正文（支持 `\n` 换行） |

## 7. subscription-terms — 订阅条款

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 区块标题 |
| `terms[].icon` | media | 条款图标 |
| `terms[].title` | string | 条款标题（如 `"Cancel Anytime"`） |
| `terms[].description` | string | 条款说明 |
| `showRestorePurchases` | boolean | 是否显示恢复购买链接 |
| `restorePurchasesText` | string | 恢复购买按钮文案 |

## 8. bottom-area — 底部 CTA 区域

| 字段 | 类型 | 说明 |
|------|------|------|
| `style` | enum | `default` / `no_thanks` |
| `freeTrialText.title` | string | 免费试用按钮主文案 |
| `freeTrialText.subtitle` | string | 免费试用按钮副文案（如 "7-day free trial"） |
| `normalText.title` | string | 普通订阅按钮主文案 |
| `normalText.subtitle` | string | 普通订阅按钮副文案（可选） |
| `offerText.title` | string | 兑换按钮文案 |
| `noThanksText` | string | 关闭按钮文案（style=no_thanks 时显示） |
| `noSubscription` | string | 不订阅继续使用文案（如 "Continue without Subscription"），可选 |
| `directPay` | boolean | `true`=直接支付，`false`=先试用 |

App 根据用户状态自动选择显示 freeTrialText / normalText / offerText，三组文案都需要配置。

```json
{
  "blockType": "bottom-area",
  "componentType": "bottom-area",
  "style": "default",
  "freeTrialText": { "title": "Start Free Trial", "subtitle": "7-day free trial" },
  "normalText": { "title": "Subscribe Now", "subtitle": "" },
  "offerText": { "title": "Redeem Offer" },
  "noThanksText": "No thanks",
  "noSubscription": "Continue without Subscription",
  "directPay": false,
  "layoutConfig": { "bottomPadding": 0 }
}
```

## 9. purchase-notice — 支付方式说明

按平台（iOS/Android）和支付方式（native IAP/Airwallex/Stripe）分别配置支付说明文案。

| 字段 | 类型 | 说明 |
|------|------|------|
| `ios.native` | string | iOS IAP 原生支付说明 |
| `ios.airwallex` | string | iOS Airwallex 支付说明 |
| `ios.stripe` | string | iOS Stripe 支付说明 |
| `android.native` | string | Android IAP 原生支付说明 |
| `android.airwallex` | string | Android Airwallex 支付说明 |
| `android.stripe` | string | Android Stripe 支付说明 |
| `showPurchaseRecord` | boolean | 是否显示购买记录入口 |
| `purchaseRecordLabel` | string | 购买记录按钮文案（如 `"Purchase Record"`） |

```json
{
  "blockType": "purchase-notice",
  "componentType": "purchase-notice",
  "ios": {
    "native": "Payment will be charged to your Apple ID account.",
    "airwallex": "Payment processed by Airwallex.",
    "stripe": "Payment processed by Stripe."
  },
  "android": {
    "native": "Payment will be charged to your Google Play account.",
    "airwallex": "Payment processed by Airwallex.",
    "stripe": "Payment processed by Stripe."
  },
  "showPurchaseRecord": true,
  "purchaseRecordLabel": "Purchase Record",
  "layoutConfig": { "bottomPadding": 24 }
}
```

## 10. text — 自定义文本块

可配置样式、颜色、对齐方式的独立文本组件。

| 字段 | 类型 | 说明 |
|------|------|------|
| `text` | string | 显示文本 |
| `textStyle` | enum | 文本样式，如 `title2_emphasize` / `title3_emphasize` |
| `color` | string | 文字颜色（hex，如 `"#000000"`） |
| `textAlign` | enum | 对齐方式：`center` / `left` / `right` |
| `leftPadding` | number | 左侧内边距（px） |
| `rightPadding` | number | 右侧内边距（px） |

```json
{
  "blockType": "text",
  "componentType": "text",
  "text": "Choose Your Plan",
  "textStyle": "title2_emphasize",
  "color": "#000000",
  "textAlign": "center",
  "leftPadding": 0,
  "rightPadding": 0,
  "layoutConfig": { "bottomPadding": 24 }
}
```

---

## 辅助 Collection

### member-features（会员权益项）

| 字段 | 类型 | 说明 |
|------|------|------|
| `key` | string | 唯一标识，如 `ai_event_descriptions` |
| `name` | string | 展示名称 |
| `subtitle` | string | 权益描述（可选） |

### comparison-templates（对比模版）

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 模版名称 |
| `selectedMembers` | array | 方案列（表头），每项含 `memberKey` + `displayName` |
| `selectedFeatures` | array | 权益行，每项含 `feature`（关联 member-features）+ `values[]` |

values 中的值：`"true"` / `"false"` 渲染为勾/叉，其他字符串渲染为文本。
