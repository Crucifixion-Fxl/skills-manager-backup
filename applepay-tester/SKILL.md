---
name: applepay-tester
description: Apple Pay订阅续费测试策略。聚焦Server Notification空值安全、沙盒环境降级、多租户配置路由三大风险点。适用于任何实现Apple Pay订阅的系统。
---

# applepay-tester

## Description

Apple Pay 订阅续费的测试策略和关键风险点。本 skill 不依赖具体实现，聚焦在通用的测试思路上。

**⚠️ 架构迁移警告**：当前系统正在重构中（迁移到 engagement 项目的 Entitlement BC），本 skill 描述的是通用测试策略，不绑定具体代码路径。

## Rules

### Rule 1 — 测试覆盖范围

Apple Pay 订阅续费测试必须覆盖以下场景：

| 场景类型 | 关键验证点 |
|---------|-----------|
| **正常续费** | DID_RENEW 通知 → 订单匹配 → VIP 延期 → 支付记录创建 |
| **取消订阅** | auto_renew_status=0 → 订单标记取消 → VIP 到期处理 |
| **退款处理** | cancellation_date_ms 存在 → VIP 降级/退款 → 清理待支付任务 |
| **升级处理** | is_upgraded=true → 按退款路径处理 → 新订单生效 |
| **沙盒降级** | Production 验证失败（status=21007）→ 自动重试 Sandbox URL |
| **多租户路由** | bundleId → tenantId 映射 → 正确的支付配置 |

### Rule 2 — Server Notification 空值安全（Critical）

Apple 的 Server Notification V2 中，以下字段可能为 null 或缺失，必须有防御性检查：

| 字段路径 | 风险场景 | 防御措施 |
|---------|---------|---------|
| `latest_receipt_info` | billing retry 通知可能不包含此字段 | 调用 `.size()` 前检查 null |
| JWT `data` claim | TEST 通知类型可能缺失 data | `.asMap()` 前检查 null |
| `purchase_date` | 某些通知类型可能缺失 | `.substring()` 前检查 null |
| `pending_renewal_info` | 非续订场景可能缺失 | 访问前检查 null |
| `paymentConfig` | tenantId 不在配置中 | 从 map 取值后检查 null |

**测试策略**：构造最小化的 JSON payload（只包含必需字段），验证系统不会因缺失可选字段而崩溃。

### Rule 3 — 沙盒环境降级逻辑

Apple 的 receipt 验证有两个环境：

```
Production URL 验证
  ↓ status=21007（沙盒 receipt 发到生产环境）
自动降级到 Sandbox URL 重试
  ↓ 成功
处理订单
```

**测试要点**：
- 模拟 status=21007 响应，验证自动重试逻辑
- 验证不会无限重试（需要 retry 计数器）
- 验证 Sandbox 重试成功后正常处理订单

### Rule 4 — 多租户配置路由

Apple Pay 按 `tenantId` + `bundleId` 匹配支付配置。测试时必须验证：

| 测试点 | 验证内容 |
|--------|---------|
| **bundleId 提取** | 支持两种来源：顶层 `bundle_id`（Apple receipt 格式）和嵌套 `app.bundle`（自定义格式），优先级正确 |
| **租户路由** | 特殊 bundleId（如 Nature App）能正确路由到对应租户配置 |
| **配置缺失** | tenantId 不在配置 map 中时有降级处理，不直接崩溃 |
| **独立配置** | 多租户各自的 `applePassword` 和 `appleKeyId` 不会混用 |

**测试策略**：构造不同 bundleId 的通知，验证路由到正确的租户配置。

### Rule 5 — 测试数据构造原则

构造 Apple Pay 测试数据时，遵循"最小化 + 必需字段"原则：

**必需字段**（缺失会导致 NPE）：
- `purchase_date`（用于时间解析）
- `purchase_date_pst`（用于时区转换）
- `product_id`（用于订单匹配）
- `transaction_id`（用于去重）
- `original_transaction_id`（用于订单查询）

**可选字段**（测试空值安全）：
- `latest_receipt_info`（billing retry 可能缺失）
- `pending_renewal_info`（非续订场景可能缺失）
- `cancellation_date_ms`（只有退款时存在）
- `is_upgraded`（只有升级时存在）

**测试策略**：
1. 先构造包含所有字段的"完整通知"，验证正常流程
2. 再逐个移除可选字段，验证空值安全
3. 构造异常值（如 status=21007），验证错误处理

## Examples

### ❌ Bad

#### 1. 只测空数组，不测字段缺失

```java
// 只测了空数组，没测 key 完全不存在的情况
JSONObject orderInfo = new JSONObject();
orderInfo.put("status", 0);
orderInfo.put("latest_receipt_info", new JSONArray()); // 空数组 ≠ null
```

**问题**：Apple 的 billing retry 通知可能完全不包含 `latest_receipt_info` 字段，`getJSONArray()` 返回 null，直接调 `.size()` 会 NPE。

#### 2. 测试时忽略多租户路由

```java
// 硬编码 tenantId，忽略 bundleId 路由逻辑
orderDO.setTenantId("default_tenant");
// 但实际 bundleId 可能需要路由到特殊租户配置
```

**问题**：某些 App（如 Nature）的 bundleId 需要特殊路由，用错配置会导致 receipt 验证失败。

#### 3. 只测正常流程，不测边界情况

```java
// 只测了 DID_RENEW，没测 DID_FAIL_TO_RENEW / TEST 等类型
when(appleService.verify(any())).thenReturn(successResponse);
```

**问题**：不同 notificationType 的处理逻辑不同，只测正常续费无法覆盖取消、退款、升级等场景。

### ✅ Good

#### 1. 完整的续费测试数据（包含所有必需字段）

```java
// 模拟真实的 DID_RENEW 通知，包含所有必需字段
JSONObject orderInfo = new JSONObject();
orderInfo.put("status", 0);

JSONArray receiptInfo = new JSONArray();
JSONObject order = new JSONObject();
order.put("product_id", "subscription_monthly");
order.put("transaction_id", "test_txn_001");
order.put("original_transaction_id", "orig_txn_001");
order.put("expires_date_ms", System.currentTimeMillis() + 30*24*3600*1000L);
order.put("purchase_date_ms", System.currentTimeMillis());
order.put("purchase_date", "2026-03-27 10:00:00");        // 必需，用于解析
order.put("purchase_date_pst", "2026-03-27 02:00:00");    // 必需，用于时区
receiptInfo.add(order);
orderInfo.put("latest_receipt_info", receiptInfo);

JSONArray renewalInfo = new JSONArray();
JSONObject renewal = new JSONObject();
renewal.put("original_transaction_id", "orig_txn_001");
renewal.put("auto_renew_status", 1);   // 1=续订中, 0=已取消
renewal.put("product_id", "subscription_monthly");
renewalInfo.add(renewal);
orderInfo.put("pending_renewal_info", renewalInfo);
```

#### 2. 空值安全测试（移除可选字段）

```java
// 测试 latest_receipt_info 完全缺失的情况
JSONObject minimalNotification = new JSONObject();
minimalNotification.put("status", 0);
// 不设置 latest_receipt_info，模拟 billing retry 通知

// 验证不会 NPE，而是有降级处理
assertDoesNotThrow(() -> applePayService.process(minimalNotification));
```

#### 3. 多场景覆盖（续费/取消/退款/升级）

```java
// 测试取消订阅
renewalInfo.getJSONObject(0).put("auto_renew_status", 0);

// 测试退款
order.put("cancellation_date_ms", System.currentTimeMillis());

// 测试升级
order.put("is_upgraded", true);

// 测试沙盒降级
orderInfo.put("status", 21007);  // 触发 Sandbox 重试
```

## References

- [Apple App Store Server Notifications V2](https://developer.apple.com/documentation/appstoreservernotifications)
- [Apple Receipt Validation](https://developer.apple.com/documentation/appstorereceipts/verifyreceipt)
- [StoreKit 2 API](https://developer.apple.com/documentation/appstoreserverapi)
