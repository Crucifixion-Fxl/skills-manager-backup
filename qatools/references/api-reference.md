# qa-tools API 参考（staging）

> 本文档覆盖 `qatools` skill 会用到的 qa-tools 全部接口。SKILL.md 定义可直接执行的操作流程；本文档补充各辅助接口的请求字段和错误语义。

## 目录

- [1. 测试用户](#1-测试用户apitest-user)
- [2. 用户查询](#2-用户查询apiuser-query)
- [3. 一键环境与套餐](#3-一键环境apiquick-setup)
- [4. Stripe 订阅管理](#4-stripe-订阅管理apistripe)
- [5. VIP 查询与到期时间](#5-vip-查询与到期时间apivip)
- [6. 错误码速查](#6-错误码速查)
- [7. 本地 iot-service 常见问题](#7-存量-iot-service-本地-dev-常见问题对-staging-不适用供-debug-参考)

**Base URL**：`https://qa-tools-staging.addx.live`

**Auth**：所有 `/api/*` 接口要求 `Authorization: Bearer $QA_TOOLS_TOKEN`（健康检查 `/api/health` 除外）。

**⚠️ 通用行为：邮箱大小写**

iot-service 在存库前会把 email 统一转小写（`/api/test-user/register`、`/api/quick-setup` 的 register 步骤、`/api/test-user/mark` 的 login 校验等均如此）。调用方：

- 上游产生的 email 必须是**全小写**（skill 的生成命令 `tr -dc 'a-z0-9'` 已满足）
- 若用户传大写，响应里的 email 字段（`generatedEmail` / `completedData.email`）是**小写版本**，后续查询 / 登录 / 打标必须用小写

---

## 1. 测试用户（`/api/test-user/*`）

### 1.1 单个注册 `POST /api/test-user/register`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `tenantId` | string | 否 | `vicoo` | 支持 `vicoo` / `guard` / `safemo` / `dzees` / `viconature` 等 |
| `countryNo` | string | 否 | `US` | |
| `password` | string | 否 | `Aa123456` | 明文传，服务端 SHA256 后存库 |
| `phone` | string | 否 | - | |
| `supportFreeLicense` | boolean | 否 | 不传 | 仅 Free License 新账号 fixture 传 `true`；不传保持旧注册行为 |

响应：

```json
{
  "result": 0,
  "data": {
    "generatedEmail": "ab3cd@qa.test",
    "userId": 26301,
    "marked": true
  }
}
```

### 1.2 批量注册 `POST /api/test-user/register/batch`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `count` | number | 是 | 1~5（超过 5 会被截断） |
| `tenantId` / `countryNo` / `password` / `phone` | - | 否 | 同单个注册 |

响应：`{ results: [ { status: 'success', email, userId, marked } \| { status: 'fail', error } ] }`

### 1.3 内部账号列表 `GET /api/test-user/list`

Query：`limit` ∈ [1, 500]（默认 100），`offset` ≥ 0（默认 0）

响应：

```json
{
  "result": 0,
  "data": {
    "list": [
      {"id": 26301, "email": "ab3cd@qa.test", "name": null, "phone": null,
       "tenantId": "vicoo", "countryNo": "US", "internal": 1}
    ],
    "totalCount": 123,
    "limit": 100,
    "offset": 0
  }
}
```

### 1.4 获取登录 OTP `GET /api/test-user/login-code`

Query：`email`（必填，仅 `@qa.test` 账号）

响应：`{ result: 0, data: { code: "123456" } }`。验证码 5 分钟有效。

### 1.5 手动打标 `POST /api/test-user/mark`

用于 `register` 后 `marked=false` 的补救。

| 字段 | 类型 | 必填 |
|------|------|------|
| `email` | string | 是 |
| `password` | string | 是（与注册时一致） |
| `tenantId` | string | 否（默认 `vicoo`） |

响应：`{ result: 0, data: { marked: true } }`

---

## 2. 用户查询（`/api/user-query/*`）

### 2.1 账号简要信息 `GET /api/user-query/account-brief-info`

Query：
- `email` 或 `phone` 至少一项必填
- `tenantId` 必填

响应：转发 iot-service `/ops/accountBriefInfo`，含 `userId`、`name`、邮箱、手机号等。

---

## 3. 一键环境（`/api/quick-setup`）

### 3.1 `POST /api/quick-setup`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `email` | string | 是 | - | 建议 `<prefix>@qa.test` 风格 |
| `password` | string | 是 | - | 建议 `Aa123456` |
| `deviceType` | number | 是 | - | 未知时填 `0` |
| `modelNo` | string | 否 | - | 指定 Mock 设备型号；省略时使用 deviceType 默认型号 |
| `productId` | number | 是 | - | **无默认值**；调用方先现查 `GET /api/product/list` 取当前 staging 实时套餐清单，按用户描述选定后再传。详见 [`account-and-vip.md` 的 Live product selection](account-and-vip.md#live-product-selection) |
| `tenantId` | string | 否 | `vicoo` | 必须与 productId 所属 tenant 一致；也决定自动支付路由 |
| `mockPay` | boolean | 否 | 按 tenant 自动路由 | 显式 true 走 mock-pay，false 走 Stripe，显式值优先 |
| `mockChannel` | string | 否 | 按 tenant 推荐 | 仅 mock-pay 分支使用；stripe/apple/google/airwallex |
| `phone` | string | 否 | - | |
| `countryNo` | string | 否 | `US` | |
| `trialPeriodDays` | number | 否 | - | 留空无试用，立即扣款 |

Pipeline 步骤（失败后续 skipped，无回滚）：

1. `register` (`/private/account/register`)
2. `login` (`/account/login`，拿 JWT 用于打标)
3. `bind-device` (`/private/device/bind-mock`)；mock-pay 分支固定
   `grantMockAiTier=false`，避免 CDC/订阅回调测试账号额外获得 QA AI Tier 90001；
   Stripe 分支保持既有绑定行为
4. Stripe：`create-stripe-customer`（**Stripe API 直接建 Customer，带 `source=tok_visa` 默认卡 + `metadata[userId]`**）+ `/private/stripe/customer` 绑定
5. Stripe：`create-subscription` (`/private/stripe/subscription`)

mock-pay 分支把第 4 步替换为 `/private/mock-pay/notify`，不执行 Stripe 两步；
响应以 `mockTransactionId` / `mockChannel` 为清理和审计标识。

响应：

```json
{
  "steps": [
    {"name": "register", "status": "success", "data": {...}},
    {"name": "login", "status": "success"},
    {"name": "bind-device", "status": "success", "data": {"serialNumber": "MOCKxxxx"}},
    {"name": "create-stripe-customer", "status": "success", "data": {"customerId": "cus_xxx"}},
    {"name": "create-subscription", "status": "success", "data": {"subscriptionId": "sub_xxx", "status": "active"}}
  ],
  "completedData": {
    "userId": 26302,
    "authToken": "...",
    "serialNumber": "MOCKxxxx",
    "customerId": "cus_xxx",
    "subscriptionId": "sub_xxx"
  }
}
```

---

### 3.2 套餐清单 `GET /api/product/list`

透传 iot-service `POST /inner-api/revenue/product/queryAllProduct`（OpenAPI 签名由 qa-tools server 注入）。每次需要 productId 时实时调，**不要缓存**，避免与 iot-service 数据库 (`product` JOIN `tier`) 漂移。

无请求参数（仅需 `Authorization` 头）。

响应：

```json
{
  "result": 0,
  "msg": null,
  "data": [
    { "id": 30401, "subject": "Gen3 单设备月订阅", "month": 1, "tierType": 3, "tierServiceType": 0, ... },
    { "id": 31002, "subject": "Bird 喂鸟器月订阅", "month": 1, "tierType": 0, "tierServiceType": 2, ... }
  ]
}
```

本 skill 用于选择套餐的关键字段如下；完整 row 以实时响应为准，筛选规则见
[`account-and-vip.md` 的 Live product selection](account-and-vip.md#live-product-selection)：

| 字段 | 用途 |
|------|------|
| `id` | productId（quick-setup 入参） |
| `subject` | 套餐中文名（已含产品线 / 设备数 / 周期语义，可直接展示） |
| `month` | `1` = 月订阅，`12` = 年订阅 |
| `tierType` | `3` = Gen3；其它值（`0/1/2/4` 等）为 V2 / 早期产品线 |
| `tierServiceType` | `0` = 普通云服务，`1` = 4G 流量，`2` = Nature(喂鸟器) |

字段语义来源（SQL JOIN tier 表 + Java enum）：iot-service `RevenueProductDAO.queryAllProduct` + `enums/pay/TierTypeEnums` + `enums/pay/TierServiceTypeEnums`。

---

## 4. Stripe 订阅管理（`/api/stripe/*`）

### 4.1 绑定 Stripe Customer `POST /api/stripe/customer`

**仅 BIND**，需要 `customerId` 已在 Stripe 侧建好。qa-tools QuickSetup 内部才用；外部很少直接调。

| 字段 | 必填 | 说明 |
|------|------|------|
| `userId` | 是 | iot-service userId |
| `customerId` | 是 | Stripe `cus_xxx` |

### 4.2 创建订阅 `POST /api/stripe/subscription`

iot-service 内部查 userId→customerId 映射后扣款建 Stripe Subscription。**前端会自动调 attach-test-card 做 pre-flight**，但如果是直接调接口，需要自己确保 Customer 有默认支付方式。

| 字段 | 必填 | 说明 |
|------|------|------|
| `userId` | 是 | |
| `productId` | 是 | number，见 [`account-and-vip.md` 的 Live product selection](account-and-vip.md#live-product-selection) |
| `trialPeriodDays` | 否 | |

响应：`{ result: 0, data: { subscriptionId, status, customerId, priceId, productId, trialStart, trialEnd, userId } }`

常见错误：
- `-1002 Stripe customerId not found for this user`：userId 在 `external_customer` 表无映射
- `-2002 Stripe API error: This customer has no attached payment source or default payment method`：Customer 缺默认 PM（先调 4.4 attach-test-card）

### 4.3 取消订阅 `POST /api/stripe/subscription/cancel`

| 字段 | 必填 | 说明 |
|------|------|------|
| `userId` | 是 | internal 测试账号；用于 webhook/order 尚未落地时的 owner 校验 |
| `subscriptionId` | 是 | `sub_xxx` |

iot-service 会用 `userId` 找到该账号的 Stripe Customer，并从 Stripe 实时回读
Subscription 的 Customer 归属；不匹配时 fail-closed。这样 quick-setup 刚创建完成、
本地 order 尚未被 webhook 建立时也能立即精确取消。

### 4.4 切换订阅 `POST /api/stripe/subscription/switch`

| 字段 | 必填 | 说明 |
|------|------|------|
| `userId` | 是 | |
| `productId` | 是 | 新套餐 |

### 4.5 绑定测试卡（幂等，pre-flight 用） `POST /api/stripe/customer/attach-test-card`

给已有 Stripe Customer 补一张 4242 测试卡并设为默认支付方式。仅在 `STRIPE_API_KEY` 为 `sk_test_*` 时可用（非 test key 返回 403）。

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `userId` | 是 | 正整数 | Stripe Search 按 `metadata[userId]` 反查 Customer |

行为：
- Customer 已有 `invoice_settings.default_payment_method` 或 `default_source` → `{ result: 0, data: { skipped: true, paymentMethodId } }`
- 否则创建 PaymentMethod → attach → 设默认 → `{ result: 0, data: { customerId, paymentMethodId } }`

错误码：
- 400 `userId 必须为正整数`
- 403 `attach-test-card 仅在 Stripe test mode 可用`
- 404 `未找到 userId=xxx 对应的 Stripe Customer`（Stripe Search 延迟最多 ~1 分钟）

### 4.6 清空 Customer 余额 `POST /api/stripe/customer/balance/clear`

测试 refund / credit 场景用。

| 字段 | 必填 |
|------|------|
| `customerId` | 是 |

---

## 5. VIP 查询与到期时间（`/api/vip/*`）

所有接口要求目标账号为 `internal=1`。写 `/api/vip/endtime` 时应额外携带 `userId`：qa-tools middleware 只在 body 有 `userId` 时执行第一层 `internalCheck`；iot-service 随后按 `userVipId` 反查真实 owner 并再次执行 internal guard。

### 5.1 查询 VIP `POST /api/vip/list`

请求：

```json
{
  "userId": 1161554,
  "tierServiceType": 0
}
```

`tierServiceType`：`0` 云存储，`1` 4G 流量。响应 `data[]` 常用字段包括 `userVipId`、`productName`、`effectiveTime`、`endTime`、`tradeNo`、`orderSn`、`freeTrial`。

### 5.2 立即过期 `POST /api/vip/expire`

请求：`{"userId":1161554,"userVipId":2066360,"tierServiceType":0}`。

服务端实际把目标记录设置为 8 天前到期。该接口用于快速越过 7 天宽限期，不适合精确模拟 D0/D7/D30 等边界；精确分群使用 `/api/vip/endtime`。

### 5.3 精确修改到期时间 `POST /api/vip/endtime`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `userId` | number | 调用方应传 | 触发 qa-tools `internalCheck`；路由不会把它转发给 iot-service |
| `userVipId` | number \| string | 是 | 必须先由 `/api/vip/list` 唯一确认 |
| `newEndTime` | number | 是 | Unix epoch 秒；禁止传毫秒 |
| `newEffectiveTime` | number | 否 | 默认省略；仅在明确需要同步调整生效时间时传 |

响应 `result: 0` 表示 iot-service 已更新指定 `user_vip` 记录，并调用设备套餐缓存刷新。调用方仍需回读 `/api/vip/list`，并在目标业务系统验证属性或分群结果。

## 6. 错误码速查

| Code | 含义 | 出处 |
|------|------|------|
| 0 | 成功 | - |
| -1001 | 参数非法 / User not found | iot-service |
| -1002 | Stripe customerId not found for user | iot-service |
| -1003 | Stripe configuration not found | iot-service（payment config） |
| -2001 | Stripe 创建 Subscription 返回空 | iot-service |
| -2002 | Stripe API error（信息透传） | iot-service |
| -2003 | 其他失败（内含 root cause） | iot-service |
| -9999 | 未处理异常（Unknown Error） | iot-service GlobalExceptionHandler |

---

## 7. 存量 iot-service 本地 dev 常见问题（对 staging 不适用，供 debug 参考）

本地调 qa-tools 时若 iot-service pipeline 挂，参照：

| 症状 | 根因 | 修复 |
|------|------|------|
| `device_manual.device_category` 列不存在 | 本地 DB schema 滞后 | `ALTER TABLE device_manual ADD COLUMN device_category tinyint DEFAULT NULL COMMENT '设备大类' AFTER thread_config;` |
| `camera.external_customer` 表不存在 | 本地 DB schema 滞后 | 跑 `iot-service-cloud/sql/2025/stripe_integration.sql` |
| `Stripe configuration not found` | 本地无 `payment_dev-local.yml` 或 vicoo block 缺 `stripeApiKey` | 从 `payment_dev.yml` 复制并补 `stripeApiKey: sk_test_xxx` |
| `apple_private_key.p8 cannot be opened` + 进程退出 | vicoo 配了 `applePrivateKeyPath` 但文件不存在，触发 `System.exit(400)` | 删掉 vicoo block 的 `applePrivateKeyPath` 或放任意 dummy 文件到 `keys/apple_private_key.p8` |
| `Unable to make protected final java.lang.Class java.lang.ClassLoader.defineClass accessible` | Java 版本错（Java 17+ 报这个） | `export JAVA_HOME=$(/usr/libexec/java_home -v 11)` |
| `required a bean of type 'IUserIdAndCheckAspect'` | 缺 `appName` 环境变量 | `export appName=iot-service-cloud` |

上述问题均为**本地环境问题**，staging 部署不会出现。
