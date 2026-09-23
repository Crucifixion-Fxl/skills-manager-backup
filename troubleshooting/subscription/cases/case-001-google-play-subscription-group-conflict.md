# Case 001：Google Play 订阅组冲突导致包年被取消

**结论类型**：用户操作问题（Google Play 平台机制）
**关键词**：Google Play、v2-单设备-12个月、v2-无限设备-12个月、订阅组冲突、包年被取消
**平台**：Google Play（Android 内购）

## 用户描述

用户为第一台设备购买了 vico home 包年套餐，随后为第二台设备购买包月套餐，但第一台设备的包年被取消，现在只剩包月，用户已付了一年费用。问题：另一台设备是否不支持 vico 套餐？

## 关键数据要点

- 设备1：Smart Bird Feeder，bindTime 2025-05-26，customerId CU0441
- 设备2：Hummingbird Smart Camera，bindTime 2025-07-14，customerId CU0292
- VIP 链路关键节点：
  - `v2-单设备-12个月`（包年，for 设备1）2025-07-11 付费，2025-07-17 被取消（仅 6 天！）
  - `v2-单设备-1个月`（包月，for 设备2）createTime=2025-07-17，**startTime=2026-07-23，endTime=2026-07-23**（零时长，排期未启动）
  - `v2-无限设备-12个月`（无限包年，for 两台）2025-07-22 生效，endTime=2026-07-23，**当前仍有效**
- 两类套餐的 `subscriptionGroupId` 均为 `cloud_premium_group_v2`

## 核心原因

Google Play 将同一 `subscriptionGroupId` 内的所有产品视为同一订阅。当用户在 `cloud_premium_group_v2` 内购买新产品（包月）时，Google Play 自动终止旧产品（包年），包月被排期至原包年周期结束后才生效。用户随即升级购买无限包年，将排期包月取消，无限包年立即生效覆盖两台设备。

## 排查注意事项

1. **当前状态与用户感知不符**：用户说"只剩包月"，实际数据显示当前为 `v2-无限设备-12个月` 有效至 2026-07-23，需先核实用户的实际感知
2. **零时长排期记录**：`startTime == endTime` 的 VIP 记录表示 Google Play 降级排期被取消前，从未实际生效，不代表异常
3. **两台设备均支持 vico 套餐**：两台设备 tenantId 均为 `vicoo`，VIP #6 的 `effectiveDevice` 同时包含两台，无兼容性问题
4. **Google Play 订阅组识别**：查看 VIP 记录的 `extend.subscriptionGroupId` 字段，同组内切换会互相取消
5. **包年被取消的财务问题**：包年仅付费 6 天后被取消，Google Play 通常按比例折算剩余价值抵扣后续订阅；如用户认为损失，需通过 Google Play 客服核实

## 结论

当前账号实际持有 `v2-无限设备-12个月` 包年，覆盖两台设备，有效至 2026-07-23，非包月。两台设备均支持 VicoHome 套餐。包年被取消是 Google Play 订阅组机制的正常行为（非后端 Bug），财务损失问题需通过 Google Play 核实折算抵扣情况。
