# VicoHome 首绑案例与后续需求差异

记录截至 2026-09-30 的首绑配置和验证结论，供实施方法参考。它是历史案例，不是当前部署或配置的实时保证；复用前应回读目标环境。以下商品、版本门槛、试用、实验数量与分配比例仅属于已确认的 VicoHome 需求。

## 业务确认与商品准备

PRD：[新用户订阅页转化策略优化](https://a4x-paas.feishu.cn/wiki/QQZWwzmpqiW5ynk0uLjcnjbGnrg)。UI 参考指定 DW frame；业务冲突沿用 PRD 和用户最新确认。

| Paywall / sceneKey | CMS 完整候选 | 主页面与初始默认 | 挽留 |
|---|---|---|---|
| pw_bind_first_v1 / bind_first_v1 | 30401 月包、30402 年包 | 两项可切换，默认 30402 | 继承当前完整 Offer |
| pw_bind_first_v2 / bind_first_v2 | 30402 年包、30401 月包 | 仅年包，默认 30402 | 从候选匹配月包，当前 30401 |
| pw_bind_first_v3 / bind_first_v3 | 30502 年包、30504 年包、30501 月包 | 两个年包，默认 30502 | 从候选匹配月包，当前 30501 |

共同模板 `awareness_bind_offer_v1`，SpmB 为 `vip_purchase_product_page`，All Plans 为空。当前全部为 `INTRODUCTORY`、Offer ID 空；后台保留修改 Offer 类型 / ID 的能力。

- v1 / v2 商品为 30 天免费试用。
- v3 的 30502 为 14 天免费年包，30504 为首月 $1.99 付费优惠年包，30501 为 14 天免费月包。
- v2 / v3 挽留按 `billingType=SUBSCRIPTION`、`subscriptionPeriodMonths=1`、`offerType=INTRODUCTORY` 匹配唯一候选。当前 ID 是匹配结果，不能写死，也不能套用主页面年包 defaultProductId。
- 返回主页面保持原选择。三个方案的挽留 X 都关闭整个 WebView；FreeLicense 是否展示由首页接口和宿主负责。

新增商品时，30501 参考 30401；30502 / 30504 参考 30402，同为 Gen3 单设备权益 `tierId=313`。按 review 后的 SQL 在 staging 录入，新三项订阅组 `growth_group`。续费价为月 $6.99 / 年 $69.99；优惠阶段与本地价格来自渠道，不能从数据库常规价格推导。

## 用户条件与实验映射

触点 `vh_device_bind_success`，在整批绑定完成后产生机会；当前设备数条件不能证明历史首次绑定。

```json
{
  "tenantId": "vicoo",
  "bundle": {"$in": ["addx.ai.vicoo", "com.smartaddx.vicohome"]},
  "version": {"$gte": 40700},
  "deviceNo": 1
}
```

这些 PE 属性已存在。deviceNo 计算当前 owner 非 4G 设备，SN 去重，不计共享设备；本次不额外加 NEVER、注册新用户或首次绑定日期条件。宿主时机和机会去重仍需独立验收。

| 变体索引 | Experience / variantKey | sceneKey | paywallId |
|---|---|---|---|
| 0 对照 | exp_bind_first_v1 | bind_first_v1 | pw_bind_first_v1 |
| 1 实验 A | exp_bind_first_v2 | bind_first_v2 | pw_bind_first_v2 |
| 2 实验 B | exp_bind_first_v3 | bind_first_v3 | pw_bind_first_v3 |

共同配置为 `slotType=paywall`、`OPEN_PAYWALL`、`runtime=h5`、`actionContext.segmentKey=BIND_FIRST_DEVICE`。GB 返回 Experience key；sceneKey 来自 evaluate / 宿主，不能从 CMS 商品猜方案。

一个业务实验 `device_bind_first`，按 userId / hashVersion 2 分三组，各 1/3；这是并行对照，不是分阶段上线。无业务疲劳限制，框架兼容配置 `fat_bind_none` 使用 `strategy=none`、`maxImpressions=null`。不修改外层 reminder_migration。

正式 Admin 发布了 App staging 快照 [v77](https://engagement-admin.addx.live/releases/77)；[实验](https://us-ab-management.addx.live/experiment/exp_2r679k2bmunubf0l) 初始 Draft，随后经用户授权启动 staging。其他 Agent 复用前必须回读，不假定这些状态永远不变。

## 实现与接口证据

- [Engagement !675](https://gitlab.addx.ai/services/value-added/engagement/-/merge_requests/675)：补齐 Admin 的 H5 runtime / 场景保存、校验与发布能力。
- [Engagement !677](https://gitlab.addx.ai/services/value-added/engagement/-/merge_requests/677)：在已有通用商品补全路径上补齐配置覆盖，保持旧矩阵与商品容器路径。不是以后每个需求都加模板枚举。
- [GitOps !2336](https://gitlab.addx.ai/DEV/argocd-apps/-/merge_requests/2336)：处理 staging 固定旧镜像的问题。这是环境修复案例，不是每个 PRD 都需要修改 GitOps。

使用六个真实测试账号与 Mock 设备自然覆盖 iOS / Android 的三变体，补充版本与设备数边界。接口与内容验证不替代真实宿主触发。

最新仅首绑 CMS 检查 run ID 为 `20260930T153328Z-5d529089`，三份真实响应均 HTTP 200、result=0：

| Paywall | offerMatrix 候选 | defaultProductId | allProducts |
|---|---|---|---|
| pw_bind_first_v1 | 30401、30402 | 30402 | 2 项 |
| pw_bind_first_v2 | 30402、30401 | 30402 | 2 项 |
| pw_bind_first_v3 | 30502、30504、30501 | 30502 | 3 项 |

响应中的矩阵商品 ID 是数字，allProducts.productId 是字符串；内部关联归一化，原始 JSON 不改。30504 的 freeTrialDays=0 不代表没有付费推荐优惠。没有把本地目录拼进响应，也没有在这轮执行旧个性化 Paywall API 回归。

此时完成了商品、CMS、Admin、GB 和真实 API 链路；H5 UI、真实宿主绑定机会、渠道报价、支付和权益验收尚未完成。不要将以上数据通过结论扩大为四个 PRD 或全链路完成。

## 后续三个需求：复用流程，单独确认差异

| 需求 | 已确认方向 | 复用前核对 |
|---|---|---|
| 绑定更多设备 | NEVER / LAPSED 各一个 Paywall，All Plans 在同一份配置内；提供单设备候选用于多设备比价；ACTIVE 复用 vip_coverage_upgrade_v1 | 实际用户状态、设备档和代际；月包挽留资格；单设备报价是否在主页面加载 |
| 从未订阅用户启动 | 一个 NEVER Paywall，四个时间人群对应四个独立实验 / Experience；新启动模板 awareness_launch_offer_v1 | 时间阈值、触发与频控、每种内容的候选和 X 行为 |
| 曾经 VIP 用户启动 | 一个 LAPSED Paywall，四个独立实验；按流失时间、代际和设备档选内容，优惠使用 PROMOTIONAL | 真实流失属性及优惠资格、准确 Offer ID、默认选项和 Lifetime 商品 |

启动分阶段上线：如 D91+ 实验条件仍限定 D91+，初期只把返回值设为 D4–D29 内容的 Experience，后续再改返回 Experience。保持实验 key 和准入条件，保留实际用户和实验归因，不新增 strategyKey 或额外 Paywall ID。

更多设备的 ACTIVE 使用 `coverageUpgradeProducts → mainArea[].products → mainArea[].allProducts` 商品容器，`selectionMode=coverage_upgrade`，与 offerMatrix 路径并存。此次不顺带统一线上结构。

首绑没有 All Plans、没有业务疲劳限制；不能将这两点照搬到启动场景。召回 Offer ID 含 winback 字样也不意味着 WIN_BACK offerType。本次确认 Lifetime 为 30430 / 30940，新用户当前 Offer ID 空只是配置约定，均需在实施时回读并核验。
