# H5 Paywall 依赖与权限边界

## 适用范围

`engagement-touchpoint-integration` 只负责触点接入。对于 `OPEN_PAYWALL`，它只选择和校验已有 `paywallId`，并验证触点归因能够传到 Paywall 打开、支付成功和支付失败等结果事件；它不负责创建或修改 H5 Paywall 模板。

## 处理规则

| 情况 | 处理方式 |
|---|---|
| 已有 Paywall 满足需求 | 在 engagement-admin 的 action 配置中引用该 `paywallId`，确认目标环境已发布，再用通用 P0 看板按触点和 Paywall 筛选验收 |
| 只有小幅 UI 差异 | 转交 H5 Paywall 流程评估复用现有模板，只修改允许开放的业务 UI 层 |
| 与现有模板结构差异较大 | 转交 H5 Paywall 流程申请新增模板和 `templateKey`；`templateKey` 不是自动生成的 |
| 需要修改底层逻辑 | 立即停止修改，先提交 issue 给 Engagement owner 评审 |

## 受保护范围

触点 Skill 不得直接修改 `paywall-h5` 的 Template Kit、Bridge、支付、埋点、运行时框架或其他架构 / 逻辑层代码。产品运营只允许修改模板的业务 UI 层；底层变更是否合理、如何实现及由谁实施，以 Engagement owner 的 issue 评审结论为准。

## 验收边界

- admin 中的 `actionType=OPEN_PAYWALL` 与 `paywallId` 正确。
- 目标 Paywall 在对应环境已发布且可打开。
- SDK 向 Paywall 透传 `slotName`、`solutionId`、实验信息和设备上下文。
- 支付成功 / 失败等结果沿既有框架上报，不为单个页面重复实现埋点。
- 通用 P0 看板可按 `slot_name + paywall_id` 查看完整漏斗。

## 看板选择

| action | 默认入口 | 最小筛选与用途 |
|---|---|---|
| `OPEN_PAYWALL` | `paywall-p0-touchpoint-to-paywall-health` | `slot_name + paywall_id`，验触点到支付结果的完整漏斗 |
| `DEEP_LINK` | 已有领域看板 | `slot_name + source/领域结果`，验领取、激活等非 Paywall 结果；缺指标时先由 owner 确认 |
| 任意 action 的 SDK 断链 | Dashboard 527 | `slot_name + solution_id/experience_key/eval_result`，只做 evaluate、曝光、点击和归因排障 |

通用 P0 看板最少固定下面三个筛选条件，分享结论时一并记录，避免不同接入方使用不同口径：

```yaml
time_range: <触点发布或测试开始时间 ~ 验收结束时间>
slot_name: <engagement-admin slug>
paywall_id: <actionConfig 中实际发布的 paywallId>
```

实验对比还要在同一时间窗内固定 App、版本、环境和实验分组；不要用全量默认时间范围直接判断接入成功或业务效果。
