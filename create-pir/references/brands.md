# Brands 速查表

> 三品牌在本 skill `scripts/create_pir_event.py` 脚本里的完整 preset + 2026-04 实测记录。

## 品牌 × tenantId（非常关键）

| 品牌 | App Bundle | tenantId | 原因 |
|------|-----------|----------|------|
| VicoHome | `com.smartaddx.vicohome` | `vicoo` | 主品牌 |
| **VicoNature** | `com.smartaddx.vicohome.nature` | **`vicoo`** | OEM 壳：数据共享 vicoo，仅 bundle 区分 |
| KiwiBit | `com.kb.kiwibit` | `kiwibit` | 独立租户 |

VN 踩坑警告：新手容易以为 "Nature 的 tenantId 是 nature"，**错**。VN 实测 tenantId=vicoo。

## API 域名矩阵

**铁律**：OEM 品牌的 `device_api` = `business_api`（业务同域）。跨租户的 `api.addx.live` 对 OEM 设备 JWT 返回 `tenantId=None`，wakeup 会返回 `deviceStatus=-2112`。

| (brand, region, env) | business_api / device_api (同域) | status |
|---|---|---|
| vicohome, us, staging | `https://api-staging-us.vicohome.io` | ✅ E2E |
| vicohome, us, pre     | `https://api-pre-us.vicohome.io`     | — |
| vicohome, us, prod    | `https://api-us.vicohome.io`         | ✅ E2E |
| vicohome, eu, staging | `https://api-staging-eu.vicohome.io` | — |
| vicohome, eu, pre     | `https://api-pre-eu.vicohome.io`     | — |
| vicohome, eu, prod    | `https://api-eu.vicohome.io`         | — |
| kiwibit, us, staging  | `https://api-staging-us.kiwibit.com` | ✅ 域名+JWT 验证 |
| kiwibit, us, pre      | `https://api-pre-us.kiwibit.com`     | ✅ DNS+域名验证 |
| kiwibit, us, prod     | `https://api-us.kiwibit.com`         | ✅ E2E |
| viconature, us, staging | `https://api-staging-us.vicohome.io` | — |
| viconature, us, pre     | `https://api-pre-us.vicohome.io`     | — |
| viconature, us, prod    | `https://api-us.vicohome.io`         | ✅ E2E |

> 脚本 preset 里 business_api 和 device_api 是两个字段，但都指向同一域名。这是刻意的：未来如果某些环境它们真的分家，保留两个字段的灵活性。

## APP_META 模板要点

每个 (brand, env) 有独立 APP_META 模板，由脚本 `_APP_META_TEMPLATES` 维护：

- `appName`：VicoHome Stage / Kiwibit Stage / VicoNature 等
- `bundle`：上表
- `tenantId`：上表
- `env`：staging / pre / prod-k8s（注意 prod 是 **prod-k8s**，不是 prod）
- `version`：202502xxx 级别整数
- `versionName`：`2.25.0_test(xxx)` 或 `2.25.0` 正式版

## 已知可用账号和设备（2026-04 实测，非 PII 仅用于本内部测试）

> 以下是开发者内部测试账号池。实际使用前请与账号主人确认该账号当前仍可用。

### VicoHome

| 账号 | 环境 | 绑定设备 | 备注 |
|------|------|---------|------|
| `tltest1@yopmail.net` | us-staging | `ae73e6955e5f369700940c4a5a0ee269`（CG9 mock） | MeterSphere 场景标配 mock；永远能造 |
| `txie@a4x.io` | us-prod | `aed0e4935c45a98f2aa2744ffcc812bf`（CG625A1） | 个人 prod 账号 |

### KiwiBit

| 账号 | 环境 | 绑定设备 | 备注 |
|------|------|---------|------|
| `a2xtest@163.com` | us-prod | `4293ff854213c0a15c28ca3e4d6b7674` | KB prod 测试账号 |
| `tltest1@yopmail.net` | us-staging | `c2a5a4a96adcf280e90bd439198defb3` | MeterSphere KB 场景硬编码；但 2025-03 后 wakeup -2112（上游问题） |

### VicoNature

| 账号 | 环境 | 绑定设备 | 备注 |
|------|------|---------|------|
| `txie@a4x.io` | us-prod | `d44736e2e0c12def8efd4d382c80f4f7` | 同一 txie 账号的 VN 设备 |
| `a1xtest@163.com` | us-prod | —（eventCount=0 需确认） | VN 账号可登录，设备绑定待核实 |

## 脚本来源追溯

本 preset 来源：MeterSphere 场景 `8f39d7ea-dce0-44ee-a8f9-354c2968bdf8` (VicoHome 「[自动化] PIR 事件」) 和配套 environment 配置（45 条 env 中筛选了 VH / KB 相关的 12 条，核对 domain、APP_META、签名 secret 后固化）。如果未来 MeterSphere 改了环境配置，需人工同步这张表（更新 `scripts/create_pir_event.py` 的 `PRESETS` 和 `_APP_META_TEMPLATES`）。
