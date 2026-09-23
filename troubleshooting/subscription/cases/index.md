# 订阅问题案例索引

**遇到订阅问题时，先检索此索引找相似案例，再读取对应文件分析。**

## 案例模板

新建案例文件名格式：`case-NNN-关键词.md`

```markdown
# Case NNN：[简短标题]

**结论类型**：用户操作问题 / 系统 Bug / 正常逻辑
**关键词**：[套餐类型、支付平台、问题现象]
**平台**：Google Play / App Store / Airwallex / Stripe / 其他

## 用户描述
[用户反馈摘要]

## 查询数据
[jq 提取后的关键字段输出]

## 分析
[结合套餐规则的推导过程]

## 结论
[给到用户的处理结果]
```

---

## 案例列表

| 编号 | 标题 | 结论类型 | 关键词 | 文件 |
|---|---|---|---|---|
| 001 | Google Play 订阅组冲突导致包年被取消 | 用户操作问题（Google Play 平台机制） | Google Play、订阅组冲突、包年被取消 | `case-001-google-play-subscription-group-conflict.md` |
| 002 | App Store 双重扣费投诉（实际数据不支持） | 无法定位（用户描述与支付数据不符） | App Store、双重扣费、支付流水核查 | `case-002-ios-double-charge-complaint.md` |
| 003 | 三方 Apple Pay 连续切换套餐后，包月有效期被折算拉长 | 正常逻辑（Airwallex 剩余价值折算） | Airwallex、Apple Pay、套餐切换、有效期折算 | `case-003-airwallex-applepay-switch-proration.md` |
