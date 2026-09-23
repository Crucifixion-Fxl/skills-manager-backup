---
name: stripe-product
description: 通过 Stripe REST API 管理商品（Product）和价格（Price）：创建、查询、更新商品和价格（禁止删除操作，修改需用户确认）。当用户提到 Stripe 商品、Product、Price、创建套餐、订阅计划、定价管理，或任何与 Stripe 商品目录相关的任务时使用此 Skill。
---

# stripe-product

通过 Stripe API 管理商品（Product）和价格（Price），支持一次性商品和订阅型商品的完整生命周期管理。

## Description

Stripe 商品管理涉及两个核心资源：

| 资源 | API 前缀 | 核心功能 |
|------|----------|---------|
| Product | `/v1/products` | 商品创建、查询、更新、删除、列表 |
| Price | `/v1/prices` | 价格创建、查询、更新、列表（支持一次性和订阅） |

**商品与价格的关系**：一个 Product 可以有多个 Price（不同货币、不同周期、不同金额）。创建商品后通常需要为其创建至少一个 Price 才能用于结算。

## Rules

### 安全权限分级

所有 Stripe API 操作按风险等级分为三级：

| 等级 | 操作类型 | 执行条件 | 适用场景 |
|------|----------|----------|----------|
| **L1 - 自由执行** | 只读查询（GET） | 无需确认，直接执行 | 查询商品、价格列表、搜索 |
| **L2 - 需用户确认** | 创建（POST）、修改（POST update） | **必须**展示操作摘要并获得用户明确确认后才执行 | 创建商品/价格、更新商品/价格属性、停用商品/价格 |
| **L3 - 禁止执行** | 删除（DELETE） | **绝对禁止**，即使用户要求也不执行 | 删除商品、删除任何 Stripe 资源 |

#### L2 确认流程

执行任何 L2 操作前，**必须**向用户展示以下信息并等待确认：

1. **操作类型**：创建 / 修改 / 停用
2. **目标环境**：Test（sandbox）还是 Live（生产）
3. **操作明细**：商品名称、价格金额、变更字段的旧值→新值
4. **影响范围**：涉及多少个资源、是否影响现有订阅

**批量创建例外**：用户在同一轮对话中明确提出批量创建需求（如"创建 5 个商品"），且已提供完整参数，视为一次性确认，无需逐个确认。

#### L3 禁止操作

以下操作**绝对禁止执行**，即使用户明确要求：

- `DELETE /v1/products/{id}` — 删除商品
- `DELETE /v1/prices/{id}` — 价格本身不支持删除，但也禁止尝试
- 任何使用 `-X DELETE` 方法的 Stripe API 调用

如果用户需要"删除"商品或价格，应引导为**停用**操作（`active=false`），并说明原因：
> "Stripe 建议通过停用（active=false）替代删除，以保留历史记录和审计追踪。已停用的商品/价格不会出现在客户可见的列表中。"

### 密钥安全

| 规则 | 说明 |
|------|------|
| **禁止存储** | 禁止将 Secret Key 写入任何文件（代码、配置、日志、笔记） |
| **禁止回显** | 禁止在输出中完整展示 Secret Key，最多显示前 12 位（如 `sk_test_51T6...`） |
| **环境变量传递** | 收到 Key 后立即存入环境变量 `STRIPE_SECRET_KEY`，后续仅通过变量引用 |
| **会话隔离** | Key 仅在当前会话有效，不跨会话保留 |

### 操作前置检查

每次执行写操作前，**必须**按顺序完成以下检查：

1. **Key 环境确认**：确认 Key 前缀是 `sk_test_`（测试）还是 `sk_live_`（生产）
2. **用户意图核实**：Key 环境与用户声明的目标环境一致（如用户说"sandbox"但 Key 是 `sk_live_` 则必须警告并停止）
3. **参数完整性**：必需参数齐全，金额单位正确（分而非元）
4. **lookup_key 检查（创建 Price 时）**：必须包含 `lookup_key`，格式为 `{ProductId}_{CountryNo}`（如 `20520_US`）。若用户未提供，**必须主动询问** ProductId 和目标国家后再拼接
5. **幂等性检查**：创建前查询是否已存在同名/同描述的商品，避免重复创建

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `STRIPE_SECRET_KEY` | Stripe Secret Key（以 `sk_live_` 或 `sk_test_` 开头） | 是 |

> **重要**：`sk_test_` 前缀的 Key 用于测试环境，`sk_live_` 前缀用于生产环境。操作前确认当前使用的 Key 类型。

### 认证方式

Stripe 使用 HTTP Basic Auth，Secret Key 作为用户名，密码留空：

```bash
curl https://api.stripe.com/v1/products \
  -u "$STRIPE_SECRET_KEY:"
```

### API Reference

完整 API 文档：`https://docs.stripe.com/api`

### 操作流程

#### 创建商品（Product）

```bash
curl https://api.stripe.com/v1/products \
  -u "$STRIPE_SECRET_KEY:" \
  -d name="Gold Plan" \
  -d "description=Premium subscription plan" \
  -d "active=true"
```

**必需参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | string | 商品名称，面向客户展示 |

**常用可选参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `id` | string | 自定义 ID（不设则自动生成） |
| `description` | string | 商品描述 |
| `active` | boolean | 是否可购买，默认 `true` |
| `images` | array of strings | 商品图片 URL（最多 8 个） |
| `metadata` | object | 自定义键值对 |
| `default_price_data` | object | 同时创建默认价格（见下方） |
| `marketing_features` | array | 营销特性列表（最多 15 个，用于 Pricing Table） |
| `shippable` | boolean | 是否为实物商品 |
| `unit_label` | string | 计量单位标签 |
| `tax_code` | string | 税码 ID |
| `url` | string | 商品页面 URL |

#### 创建商品并同时设置默认价格

```bash
curl https://api.stripe.com/v1/products \
  -u "$STRIPE_SECRET_KEY:" \
  -d name="Pro Plan" \
  -d "default_price_data[currency]=usd" \
  -d "default_price_data[unit_amount]=1999" \
  -d "default_price_data[recurring][interval]=month"
```

`default_price_data` 子参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| `currency` | string | **必需**，三位 ISO 货币代码（如 `usd`） |
| `unit_amount` | integer | 金额，单位为"分"（如 1999 = $19.99） |
| `unit_amount_decimal` | string | 精确金额（与 `unit_amount` 二选一） |
| `recurring.interval` | enum | 订阅周期：`day` / `week` / `month` / `year` |
| `recurring.interval_count` | integer | 周期倍数（如 interval=month, count=3 → 每 3 个月） |
| `tax_behavior` | enum | 税务行为：`inclusive` / `exclusive` / `unspecified` |

#### 为已有商品创建价格（Price）

> **⚠️ 强制规则：每个 Price 必须设置 `lookup_key`，格式为 `{ProductId}_{CountryNo}`（如 `20520_US`）。缺少 lookup_key 或格式不符的 Price 创建请求禁止执行。**

```bash
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d product="prod_xxx" \
  -d currency=usd \
  -d unit_amount=999 \
  -d "recurring[interval]=month" \
  -d "lookup_key=20520_US"
```

**Price 必需参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `currency` | string | 三位 ISO 货币代码 |
| `product` | string | 商品 ID（与 `product_data` 二选一） |
| `lookup_key` | string | **必需（公司规范）**，格式 `{ProductId}_{CountryNo}`，如 `20520_US`。ProductId 为内部商品编号，CountryNo 为 ISO 3166-1 alpha-2 国家代码（大写）。如用户未提供，必须主动询问 |

**Price 常用可选参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `unit_amount` | integer | 金额（分）|
| `recurring.interval` | enum | 订阅周期（不设则为一次性价格） |
| `recurring.interval_count` | integer | 周期倍数 |
| `recurring.usage_type` | enum | `licensed`（默认）或 `metered` |
| `nickname` | string | 价格备注（内部使用，客户不可见） |
| `billing_scheme` | enum | `per_unit`（默认）或 `tiered` |
| `tiers_mode` | enum | 阶梯模式：`graduated` 或 `volume` |
| `tiers` | array | 阶梯定价配置 |
| `transfer_lookup_key` | boolean | 调价时设为 `true`，将 lookup_key 从旧 Price 转移到新 Price |
| `metadata` | object | 自定义键值对 |
| `active` | boolean | 是否启用 |
| `tax_behavior` | enum | 税务行为 |

#### 查询商品

```bash
# 获取单个商品
curl https://api.stripe.com/v1/products/prod_xxx \
  -u "$STRIPE_SECRET_KEY:"

# 列出商品（默认 limit=10，最大 100）
curl -G https://api.stripe.com/v1/products \
  -u "$STRIPE_SECRET_KEY:" \
  -d limit=100

# 按状态筛选
curl -G https://api.stripe.com/v1/products \
  -u "$STRIPE_SECRET_KEY:" \
  -d active=true \
  -d limit=100

# 搜索商品（Search API）
curl -G https://api.stripe.com/v1/products/search \
  -u "$STRIPE_SECRET_KEY:" \
  --data-urlencode "query=name~'Gold'"
```

#### 更新商品（L2 - 需确认）

更新操作**必须**先查询当前值，向用户展示变更对比（旧值→新值），获得确认后才执行。

```bash
# 第 1 步：查询当前商品信息
curl https://api.stripe.com/v1/products/prod_xxx \
  -u "$STRIPE_SECRET_KEY:"

# 第 2 步：向用户展示变更摘要（示例）
# 商品: prod_xxx (当前名称: "Gold Plan")
# 变更项:
#   name: "Gold Plan" → "Updated Plan Name"
#   description: (空) → "New description"
#   metadata.tier: (空) → "premium"
# 请确认是否执行以上变更？

# 第 3 步：用户确认后执行
curl https://api.stripe.com/v1/products/prod_xxx \
  -u "$STRIPE_SECRET_KEY:" \
  -d "name=Updated Plan Name" \
  -d "description=New description" \
  -d "metadata[tier]=premium"
```

#### 停用商品（替代删除）

**禁止使用 DELETE 方法删除商品**。需要下架商品时，使用 `active=false` 停用：

```bash
curl https://api.stripe.com/v1/products/prod_xxx \
  -u "$STRIPE_SECRET_KEY:" \
  -d active=false
```

> 停用后商品不会出现在客户可见的列表中，但保留历史记录和审计追踪。

#### 查询价格

```bash
# 查询商品的所有价格
curl -G https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d product="prod_xxx" \
  -d limit=100

# 按类型筛选
curl -G https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d type=recurring \
  -d limit=100
```

### 金额单位

Stripe 所有金额单位为**最小货币单位**（通常是"分"）：

| 表示金额 | `unit_amount` 值 |
|----------|-----------------|
| $9.99 | 999 |
| $19.99 | 1999 |
| $0.50 | 50 |
| ¥100 (JPY) | 100（日元无小数位） |

### 操作红线

#### 绝对禁止（违反即终止）

- **禁止执行任何 DELETE 请求**——包括 Product、Price 及所有 Stripe 资源
- **禁止**在未确认 Key 环境的情况下执行写操作
- **禁止**将 Secret Key 写入文件或在输出中完整展示
- **禁止**在 Key 环境与用户声明的目标环境不一致时继续执行

#### 强制确认（跳过即违规）

- **生产环境（`sk_live_`）** 所有写操作必须用户逐一确认后才执行
- **测试环境（`sk_test_`）** 修改和停用操作必须展示变更摘要并获得用户确认
- **测试环境（`sk_test_`）** 批量创建操作在用户提供完整参数后视为一次性确认
- **创建 Price 时必须包含 `lookup_key`**，格式为 `{ProductId}_{CountryNo}`（如 `20520_US`）。如用户未提供 ProductId 或 CountryNo，必须主动询问后再执行，禁止跳过或留空

#### 安全兜底

- Price 一旦创建**不可删除**，只能通过 `active=false` 停用
- 创建前应检查是否已存在同名商品，避免重复
- 查询操作（GET）是只读的，可直接执行无需确认
- 操作完成后应展示结果摘要，供用户核对

### 常见工作流

1. **创建订阅商品**：创建 Product → 创建 recurring Price（月/年）→ 确认 `default_price` 设置
2. **创建一次性商品**：创建 Product → 创建 one-time Price（不设 `recurring`）
3. **多币种定价**：创建 Product → 为每种货币创建独立 Price
4. **商品下架**：更新 Product `active=false` → 更新关联 Price `active=false`
5. **调价**：创建新 Price → 更新 Product 的 `default_price` 指向新 Price → 停用旧 Price

## Examples

### Bad

```bash
# ❌ 执行 DELETE 操作（L3 禁止操作，绝对不允许）
curl -X DELETE https://api.stripe.com/v1/products/prod_xxx \
  -u "$STRIPE_SECRET_KEY:"
# 错误：禁止删除任何 Stripe 资源，应使用 active=false 停用

# ❌ 修改商品时未先查询当前值，未展示变更对比
curl https://api.stripe.com/v1/products/prod_xxx \
  -u "$STRIPE_SECRET_KEY:" \
  -d "name=New Name"
# 错误：更新前必须先查询当前值，展示旧值→新值对比，获得用户确认后才执行

# ❌ 将 Secret Key 硬编码写入文件
echo "sk_test_51T6Ryd..." > stripe_key.txt
# 错误：禁止将 Key 写入任何文件

# ❌ 在输出中完整展示 Secret Key
echo "你的 Key 是: sk_test_51T6RydQcAn7nNsBzcACh0gWz..."
# 错误：最多显示前 12 位，如 sk_test_51T6...

# ❌ Key 环境与用户意图不一致时继续执行
# 用户说"sandbox 环境"，但提供了 sk_live_ 的 Key
curl https://api.stripe.com/v1/products \
  -u "sk_live_xxxxx:" \
  -d name="Test Product"
# 错误：Key 环境 (live) 与用户声明 (sandbox) 不一致，必须警告并停止

# ❌ 创建价格时缺少 lookup_key
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d product="prod_xxx" \
  -d currency=usd \
  -d unit_amount=999
# 错误：必须设置 lookup_key，格式为 {ProductId}_{CountryNo}

# ❌ lookup_key 格式错误
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d product="prod_xxx" \
  -d currency=usd \
  -d unit_amount=999 \
  -d "lookup_key=prod_xxx_usd"
# 错误：lookup_key 格式应为 {ProductId}_{CountryNo}，如 20520_US

# ❌ 金额单位搞错
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d product="prod_xxx" \
  -d currency=usd \
  -d unit_amount=9.99
# 错误：unit_amount 是整数（分），$9.99 应传 999
```

### Good

```bash
# ✅ 操作前确认 Key 环境
echo $STRIPE_SECRET_KEY | cut -c1-8
# 输出 sk_test_ 则为测试环境

# ✅ 收到 Key 后存入环境变量，后续仅通过变量引用
export STRIPE_SECRET_KEY="sk_test_..."

# ✅ 创建前先检查是否已存在同名商品（幂等性检查）
curl -G https://api.stripe.com/v1/products/search \
  -u "$STRIPE_SECRET_KEY:" \
  --data-urlencode "query=name~'Pro Plan'"

# ✅ 创建订阅商品并同时设置默认月度价格
curl https://api.stripe.com/v1/products \
  -u "$STRIPE_SECRET_KEY:" \
  -d name="Pro Plan" \
  -d "description=Professional subscription with all features" \
  -d "default_price_data[currency]=usd" \
  -d "default_price_data[unit_amount]=1999" \
  -d "default_price_data[recurring][interval]=month"

# ✅ 更新前先查询当前值
curl https://api.stripe.com/v1/products/prod_xxx \
  -u "$STRIPE_SECRET_KEY:"
# 然后向用户展示: name: "Old Name" → "New Name"，获得确认后再执行更新

# ✅ 为已有商品添加年度价格（lookup_key = 商品编号_国家代码）
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d product="prod_NWjs8kKbJWmuuc" \
  -d currency=usd \
  -d unit_amount=19900 \
  -d "recurring[interval]=year" \
  -d "nickname=Pro Plan Yearly" \
  -d "lookup_key=20520_US"

# ✅ 调价：创建新价格并转移 lookup_key
curl https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d product="prod_NWjs8kKbJWmuuc" \
  -d currency=usd \
  -d unit_amount=24900 \
  -d "recurring[interval]=year" \
  -d "lookup_key=20520_US" \
  -d "transfer_lookup_key=true"

# ✅ 安全下架商品（停用替代删除，先停价格再停商品）
curl https://api.stripe.com/v1/prices/price_xxx \
  -u "$STRIPE_SECRET_KEY:" \
  -d active=false
curl https://api.stripe.com/v1/products/prod_xxx \
  -u "$STRIPE_SECRET_KEY:" \
  -d active=false

# ✅ 用户要求删除时，引导为停用操作
# 用户: "帮我删除 prod_xxx"
# 回复: "Stripe 建议停用替代删除，我将执行 active=false，请确认。"
```

## References

- [Stripe Products API](https://docs.stripe.com/api/products)
- [Stripe Prices API](https://docs.stripe.com/api/prices)
- [Stripe 货币与金额](https://docs.stripe.com/currencies)
- [Stripe 定价模型](https://docs.stripe.com/products-prices/pricing-models)
