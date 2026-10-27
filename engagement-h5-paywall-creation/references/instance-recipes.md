# 新 Paywall 实例创建示例

本页按 Engagement `origin/staging` 的 `40a3358bafa37d75f3e4a45351193222e87c9e63`（2026-10-12，含 !718）说明四个已实现需求。示例是接入方式和当前商品身份的证据，不是未来需求的默认商品表；执行时重新读取目标 checkout、最新 PRD 与目标环境。正本为仓库的 [实例贡献指南](https://gitlab.addx.ai/services/value-added/engagement/-/blob/40a3358bafa37d75f3e4a45351193222e87c9e63/docs/design/paywall/h5-instance-contribution.md)。

## 所有新业务的共同步骤

1. 从 PRD/Figma 确定独立投放页面与交互。新业务使用实例，已有个性化/标准 CMS 页面保留；不增加需求模板或 CMS 商品矩阵编辑器。
2. 列实例 ID、目标用户、frame、场景 Controller、商品组、默认项、关闭/挽留/All Plans/提醒行为。上游决定最终 ID，H5 不按分群或实验再换 ID。
3. 核对新 ID 与 staging/prod CMS 业务 ID 和已有实例互斥，方法见 [cms-configuration.md](cms-configuration.md#paywall-id-互斥检查)。商品实验用新 ID，不覆盖对照组配置。
4. 在 `paywall-h5/src/instances/<id>/` 建立静态 `paywall.config.ts`、组合 `definition.ts`、私有 `Page.tsx` 和必要的样式/模型/语言/素材。不是每个实例都必须有名为 `model.ts` 或 `page.css` 的文件；以当前门禁与页面需要为准。
5. 先找可复用的 scene Controller；行为相同直接组合，行为有差异则由业务研发新增薄 Controller/Policy。选品、报价、购买、恢复、提醒、互斥和重试按 [公共能力](reusable-capabilities.md) 接入，不复制底层实现。
6. 按 Figma 实现私有 UI。商品容器的横排/竖排、卡片、颜色、选中图标属于 Page/CSS；商品身份、默认项、可购买状态与动作由 Controller 提供。页面消费语义组件和公共 overlay，不能自行发支付或 PV。
7. 按 [verification.md](verification.md) 验证配置/Policy、交互、身份与视觉；按 [SKILL.md 多语言要求](../SKILL.md#4-实现-ui-与业务适配)交付支持语言。构建自动发现实例并生成同包目录，无需手改注册表。
8. H5 在目标环境部署后，按会话授权接入 Admin/实验，发布规则并联调。具体触点接入交给 `engagement-touchpoint-integration`；页面开发不自动包含部署和投放。

## 启动触点：流失用户 LAPSED

四个不同阶段的页面独立投放，分别使用四个 ID。对应代码位于 [launch-lapsed scene](https://gitlab.addx.ai/services/value-added/engagement/-/tree/40a3358bafa37d75f3e4a45351193222e87c9e63/paywall-h5/src/scenes/launch-lapsed)。

| 阶段 | 实例目录/ID | definition 绑定的 Controller |
|---|---|---|
| D0–D7 | pw_launch_lapsed_d0_d7 | useD0D7Controller |
| D8–D30 | pw_launch_lapsed_d8_d30 | useD8D30Controller |
| D31–D90 | pw_launch_lapsed_d31_d90 | useD31D90Controller |
| D91+ | pw_launch_lapsed_d91_plus | useD91PlusController |

`tenantId=vicoo`、`spmb=pw_launch_lapsed`。各实例按 Gen2/Gen3 × SINGLE/DUAL/MULTI 配置六个 `purpose=main` 组，键为 `gen2_single`、`gen2_dual`、`gen2_multi`、`gen3_single`、`gen3_dual`、`gen3_multi`。Policy 根据 runtime 的设备数和历史代际选当前实例的一组；缺少所需事实报错，不猜档位。

D0–D7 主推荐是月付/年付，默认项再按历史订阅周期匹配。D8–D30 主推荐是优惠月付/终身；各阶段商品依自己的配置，不能将 D0–D7 的商品表复用到全部阶段。此需求没有 All Plans 或挽留组，X 关闭整个 Paywall，不为公共 Controller 添加一份 LAPSED 特判。

下面是 [D8–D30 当前配置](https://gitlab.addx.ai/services/value-added/engagement/-/blob/40a3358bafa37d75f3e4a45351193222e87c9e63/paywall-h5/src/instances/pw_launch_lapsed_d8_d30/paywall.config.ts) 的矩阵。月付均为 SUBSCRIPTION/PROMOTIONAL，周期 1；终身为 ONE_TIME/NONE、`offerId=null`。名称中有 winback 不意味着 offerType 必须填 WIN_BACK。

| 商品组 | 月付 productId / 完整 offerId | 终身 productId | 默认项 |
|---|---|---|---|
| gen2_single | 20401 / 20401winback1m1 | 30430 | 本组月付 |
| gen2_dual | 20603 / 20603winback1m1 | 30940 | 本组月付 |
| gen2_multi | 20901 / 20901winback1m1 | 30940 | 本组月付 |
| gen3_single | 30401 / 30401winback1m1 | 30430 | 本组月付 |
| gen3_dual | 30603 / 30603winback1m1 | 30940 | 本组月付 |
| gen3_multi | 30901 / 30901winback1m1 | 30940 | 本组月付 |

六组共享两项终身商品，总共八个 Offer 定义，并非十二份重复商品。下面只摘取单设备 Gen3 的两项和一组，解释 offers/groups；完整实例仍需满足 Policy 要求的六组，不能只拷贝这个片段发布。

```ts
offers: {
  gen3_single_monthly: {
    productId: '30401', billingType: 'SUBSCRIPTION',
    offerId: '30401winback1m1', offerType: 'PROMOTIONAL',
    expectedBillingPeriodMonths: 1
  },
  lifetime_single: {
    productId: '30430', billingType: 'ONE_TIME',
    offerId: null, offerType: 'NONE'
  }
},
groups: {
  gen3_single: {
    purpose: 'main',
    offerKeys: ['gen3_single_monthly', 'lifetime_single'],
    defaultOfferKey: 'gen3_single_monthly'
  }
}
```

真实 `definition.ts` 只把三个绑定接起来：

```ts
import config from './paywall.config'
import { Page } from './Page'
import { useD8D30Controller as useController } from '../../scenes/launch-lapsed/useController'
import { definePaywallInstance } from '../../application/public'

export default definePaywallInstance({ config, Page, useController })
```

D0–D7 的竖排商品卡和 D8–D30 的横排商品卡由各自 Page/CSS 实现；四个导出 Hook 内部组合同一个场景 Controller，并继续使用公共报价/选择/购买/互斥。它们没有继承交易基类，也没有各实现一份支付流程。

这两个实例的配置未设置 `content`，展示资料在私有 UI/语言文件维护。商品与 Offer 在配置，金额从真实报价来；UI 文案固定并不意味着把价格写死。

## 启动触点：从未购买用户 NEVER

阶段边界与 LAPSED 不同，不能共用阶段名。代码位于 [launch-never scene](https://gitlab.addx.ai/services/value-added/engagement/-/tree/40a3358bafa37d75f3e4a45351193222e87c9e63/paywall-h5/src/scenes/launch-never)。

| 阶段 | 实例目录/ID | Controller |
|---|---|---|
| D0–D3 | vh_launch_never_d0_d3_h5 | useAnnualController |
| D4–D29 | vh_launch_never_d4_d29_h5 | useTimelineController |
| D30–D90 | vh_launch_never_d30_d90_h5 | useChoiceController |
| D91+ | vh_launch_never_d91_plus_h5 | useDiscountController |

`tenantId=vicoo`、`spmb=pw_launch_never`。Page 私有，definition 固定场景行为；大阶段已经由上游选定，不从 `segmentKey` 重新选择实例。

以 [D4–D29 配置](https://gitlab.addx.ai/services/value-added/engagement/-/blob/40a3358bafa37d75f3e4a45351193222e87c9e63/paywall-h5/src/instances/vh_launch_never_d4_d29_h5/paywall.config.ts) 为例，三组均为 `purpose=main`、默认 trial：

| group | trial 项 | discount 项 | 含义 |
|---|---|---|---|
| single | single_trial：30401 / null | single_discount：30407 / 30407existingusers2m1 | 单设备两种候选 |
| dual | dual_trial：30603 / null | dual_discount：30607 / 30607existingusers2m1 | 双设备两种候选 |
| multi | multi_trial：30901 / null | multi_discount：30907 / 30907existingusers2m1 | 多设备两种候选 |

这些项均为 SUBSCRIPTION/INTRODUCTORY、周期 1。Policy 根据已知正设备数选组；只有权威用户试用事实允许时才给 null-Offer 的 trial 项提供试用提示，付费 discount 项不能继承试用资格。真实 Native 报价仍决定是否有 TRIAL 相位。

D0–D3 另有 `single_retention`、`dual_retention`、`multi_retention` 挽留组；其他阶段不因为共用 scene 就强制增加挽留。需要提醒时通过 `usePaywallController(..., {trialReminder: true})` 接入，Page 只呈现开关并调用动作；提醒权限/状态不由 UI 自建。

沿用此场景时注意现有购买互斥入口是 `actions.runPurchase`，购买 handle 沿原路径消费，不要再嵌套一次相同的操作锁，详见 [公共能力](reusable-capabilities.md#薄-controller-如何组合)。

## 绑定触点：首次绑定

代码位于 [first-bind scene](https://gitlab.addx.ai/services/value-added/engagement/-/tree/40a3358bafa37d75f3e4a45351193222e87c9e63/paywall-h5/src/scenes/first-bind)。三个可独立投放的设计分别为：

| ID | Controller | 组合方式 |
|---|---|---|
| pw_bind_first_v1_h5 | useFirstBindV1Controller | 私有 UI/config + 公共 scene 的 v1 行为 |
| pw_bind_first_v2_h5 | useFirstBindV2Controller | 私有 UI/config + 公共 scene 的 v2 行为 |
| pw_bind_first_v3_h5 | useFirstBindV3Controller | 私有 UI/config + 公共 scene 的 v3 行为 |

`tenantId=vicoo`、`spmb=vip_purchase_product_page`。以 v1 为例：`monthlyTrial=30401`、`annualTrial=30402`，均 SUBSCRIPTION/INTRODUCTORY、offerId=null，周期分别 1/12。`primary` 和 `retention` 都引用这两项，默认 annualTrial；content 引用本实例 messages，不是 CMS 文档。

Policy 校验单设备商品及 primary/retention 的默认项。主页面与挽留是同一实例内部状态，返回主页面恢复原选择；v1 挽留保留选中商品，v2/v3 挽留使用其配置默认项。试用资格仍来自用户事实，提醒、报价、购买、关闭和恢复使用公共能力。Page 不在 X 点击时重新 evaluate，也不另建“挽留 Paywall ID”。

新增同类实验时，先确认现有 v1/v2/v3 行为是否符合新 PRD。符合则新的 definition 组合对应 Hook；不符合则新增薄业务 Controller/Policy，而不是往公共 `usePaywallController` 增加 experiment/v4/SKU 分支。

## 绑定触点：绑定更多设备

代码位于 [bind-more scene](https://gitlab.addx.ai/services/value-added/engagement/-/tree/40a3358bafa37d75f3e4a45351193222e87c9e63/paywall-h5/src/scenes/bind-more)。

| 人群 | ID | Controller |
|---|---|---|
| NEVER | pw_bind_more_never_h5 | useBindMoreNeverController |
| LAPSED | pw_bind_more_lapsed_h5 | useBindMoreLapsedController |

两个独立人群分别投放，双/多设备不必另拆 ID。`tenantId=vicoo`、`spmb=vip_purchase_product_page`；Page 自行实现主页面、All Plans 与挽留，Policy 用 runtime 正设备数和历史代际/周期决定商品，不从 paywallId 推断 SKU。

现有组键按 `用途_g代际_设备档位` 组织：

| 用途 | 例子 | 场景行为 |
|---|---|---|
| 主推荐 | primary_g3_dual / primary_g3_multi | NEVER 使用 Gen3；LAPSED 按历史 Gen2/Gen3 和月/年周期匹配 |
| 挽留 | retention_g3_dual / retention_g3_multi | 使用对应档位月付项 |
| All Plans | all_plans_g3_single / all_plans_g3_dual / all_plans_g3_multi | 设备档位和月/年切换，在当前实例内部选品 |

例如 NEVER 的主推荐双设备组引用 p30603/p30607、多设备组引用 p30901/p30907；All Plans 双设备引用 p30603/p30604，分别月付/年付。单项可被多组共享，不需要为了 All Plans 再定义相同身份的 Offer。

Controller 管理页面 surface、档位/周期、返回主页面的选中项和差价展示。它明确启用 `quoteCompleteness='selected'`；选中的 Offer 报价失败仍禁止购买，不能自动切换到成功报价商品。参考价格使用 `useReferencePrices`，参考失败按业务规则降级，不把参考项加入购买候选或曝光。

## 只改变商品/价格的实验

若 D8–D30 对照组和实验组 UI/流程相同，实验组改用另一已确认 Product/Offer：

1. 保留原实例作为对照组，另选经过互斥核对的新 ID。
2. 可以复制原实例私有 UI/模型/语言/素材；调整 paywallId、说明与 Product/Offer 引用，保持 Controller 要求的组键。不能跨实例直接导入原 Page，不能复制公共交易代码。
3. 新 definition 继续组合 `useD8D30Controller`。金额从报价获取，不改 Page 中的金额、不在原 ID 内按 variation 换商品。
4. 部署后由实验/Experience 返回不同 ID；框架自动发现新目录，不改模板 key、注册表或 CMS。
5. 实验结束先撤回失败组投放并考虑已有缓存客户端的兼容窗口，再删除不再被引用的失败组私有代码；不能因“将来会删”而复制底层支付或弱化检查。

自然的币种/本地化价格变化不需要新 ID；要同时对照不同销售方案才创建实验 ID。商品价格实验先由产品/渠道确认 Product/Offer，不能在 H5 人工改支付金额。

## 直接 Paywall 与 blocks CTA 的接入

两种入口的目标 actionConfig 都使用以下字段（示例目标为已部署 LAPSED 实例）：

```json
{
  "paywallId": "pw_launch_lapsed_d8_d30",
  "runtime": "h5",
  "paywallSource": "h5_instance"
}
```

这是 actionConfig 片段，不是完整规则包；Experience 还需 `locked=true`、`actionType=OPEN_PAYWALL` 等原契约字段。直接 `slotType=paywall` 的 cmsSlug 等于目标 ID，只是兼容字段，不录入同名 CMS Paywall。`slotType=blocks` 的 cmsSlug 是已发布的 CMS 横幅/弹窗素材，目标 ID 独立配置。

Admin 读取目标环境 `instance-catalog.json` 校验实例与来源；发布规则只保留目标 ID/runtime/source 等投放字段，不复制实例 tenantId/spmb。evaluate 不请求 H5 静态服务；H5 当前实例配置提供页面 spmb。规则缺省 source 仍按 CMS 兼容解析，所以新业务应显式选择 h5_instance，而不是仅修改 runtime。

转交触点 Skill 时明确以上实例契约；其旧模板参考中的“新增模板/templateKey”不能套用到新实例。触点任务消费已部署目标，不创建 UI 或修改 scene/公共框架；创建页面和必要的薄业务适配仍由本 Skill 负责。
