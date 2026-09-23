# 通用事件 Schema — 自动字段与 Base Schema

## SDK 自动附带字段

以下字段由各端 SDK 在初始化时自动注入到**每个事件**，无需在 `tracking-design.md` 中定义。
设计埋点参数时，**不要重复定义**这些字段。

### App 端共有字段（Android / iOS / Flutter）

| 字段 | 说明 | 来源 |
|------|------|------|
| `user_id` | 用户 tracker token | SDK 配置（AccountManager / AppGroupIdManager） |
| `device_sn` | 设备唯一标识 | SDK 配置 |
| `language` | 当前 App 语言 | 系统设置 |
| `build_env` | 构建环境标识 | 构建配置（iOS 会拼接国家后缀如 `-cn`、`-US`） |
| `push_permissions` | 推送权限状态 | "Open" / "Close" |
| `countryNo` | 国家编码 | SDK 配置 |
| `tenantId` | 租户 ID | SDK 配置 |
| `is_open_vpn` | 是否使用 VPN | 网络检测 |
| `is_used_proxy` | 是否使用代理 | 网络检测 |
| `spm` | SPM 标识 | 自动从事件参数组装 |
| `pre_spm` | 上一页 SPM | 页面导航自动维护 |
| `type` | 事件类型 | pv / exp / clk / self_event |

> **Flutter 说明**：Flutter 通过 Platform Channel 委托给原生层，字段与 Android/iOS 一致。

### iOS 额外自动 Context（Snowplow SDK 内置）

| Context | 说明 | 状态 |
|---------|------|------|
| sessionContext | 会话信息 | 启用 |
| platformContext | 平台/OS 信息 | 启用 |
| screenContext | 屏幕/设备信息 | 启用 |
| applicationContext | 应用元信息 | 启用 |
| geoLocationContext | 地理位置 | 禁用 |

### 后端字段（iot-service-local）

| 字段 | 说明 | 来源 |
|------|------|------|
| `sn` | 基站序列号 | BxBindService |
| `hub_version` | 基站固件版本 | DeviceService |
| `spm` | 事件标识 | 格式 `iot_local.{event_name}` |
| `type` | 固定为 `self_event` | — |
| `version` | 服务版本号 | IotLocalConstant |
| `gitsha` | Git commit hash | IotLocalConstant |
| `log_trace_id` | 链路追踪 ID | TrackingThreadLocal |

## Base Schema

Base Schema 是由埋点平台统一管理的**可复用参数集合**，在事件上报时自动附加。

### 工作原理

1. 平台管理员在 `/schema/list/` 页面创建 Base Schema，定义参数名和类型
2. 在 `tracking-design.md` 的 `base_schemas` 字段中引用名称（如 `["user_info", "device_info"]`）
3. 创建事件时，平台将 Base Schema 的 required 参数合并到事件的校验规则中
4. SDK 上报时，Base Schema 以 `iglu` URI 格式附加为 Snowplow Context

### 各端 Base Schema URI

| 端 | Schema URI | 源文件 |
|---|-----------|--------|
| Android | `iglu:com.base/base-schema/jsonschema/1-0-1` | SnowplowEventProcessor.kt |
| iOS | `iglu:com.base/base-schema/jsonschema/1-0-1` | TrackerConstants.swift |
| 后端 (iot-local) | `iglu:com.iot_local/base-schema/jsonschema/1-0-0` | SnowPlowManager.java |

### 查询可用 Base Schema

```bash
curl -H "Authorization: Bearer $TMT_TOKEN" \
  "https://us-analytics-management.theunismart.com/api/baseSchema/list?applicationId={id}"
```

### 与自定义参数的区分

| | Base Schema | 自定义参数 (parameters) |
|--|------------|----------------------|
| 定义位置 | 平台统一管理 | tracking-design.md |
| 复用性 | 跨事件共享 | 单事件独有 |
| 校验 | 平台 + SDK 双重校验 | 平台校验 |
| 设计时 | 在 `base_schemas` 引用名称 | 在 `parameters` 逐个定义 |

**原则**：如果一个参数会被多个事件使用（如 `user_id`、`device_info`），应该由平台管理员创建为 Base Schema，而不是在每个事件中重复定义。
