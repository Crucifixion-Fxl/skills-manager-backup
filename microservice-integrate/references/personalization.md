# 个性化服务接入规格

> 平台归属：personalization-engine
> 协议：HTTP（跨集群） / gRPC（集群内） + GrowthBook Feature Evaluation

## 1 平台简介

个性化服务（Entity Profile）是统一的用户/设备属性加载 + AB 实验评估服务。调用方传入用户上下文和 GrowthBook feature keys，服务自动加载所需属性、执行实验分流，返回各 feature 的命中结果。

核心流程：**构造上下文 → 调用 EvalFeatures → 根据返回值执行分支逻辑 → 上报曝光事件**。

## 2 接口

### 2.1 连接方式

| 场景 | 协议 | 地址 | 认证 |
|------|------|------|------|
| K8s 集群内 | gRPC | Nacos 服务发现（service name: `personalization-engine`） | 无需认证 |
| 跨集群 / 客户端 | HTTP POST | 网关地址（见下表） | JWT Bearer Token |

#### 网关地址

| 环境 | URL |
|------|-----|
| Staging US | `https://api-us-stage-new.addx.live` |
| Prod US | `https://api-us.addx.live` |
| Prod EU | `https://api-eu.addx.live` |
| Prod CN | `https://api-cn.addx.live` |

HTTP 请求需额外设置 Header：`X-T: pn`

### 2.2 EvalFeatures — 实验评估（唯一接口）

- HTTP：`POST /features/eval`
- gRPC：`personalization.v1.PersonalizationEngine/EvalFeatures`

#### 请求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `context.user_id` | string | 是 | 用户 ID |
| `context.sn` | string | 否 | 设备序列号 |
| `context.app_type` | string | 否 | 应用类型，如 `"vicoo"` |
| `context.app_version` | string | 否 | 应用版本，如 `"3.2.0"` |
| `context.tenant_id` | string | 否 | 租户标识 |
| `context.bundle` | string | 否 | App bundle name |
| `context.app_name` | string | 否 | 应用名称 |
| `context.language` | string | 否 | BCP 47 语言标签，如 `"en-US"` |
| `context.source` | string | 否 | 请求来源 |
| `context.env` | string | 否 | `"staging"` 或 `"production"` |
| `context.pay_type` | int32 | 否 | 0=App Store, 1=Airwallex/Card, 2=Stripe |
| `feature_keys` | string[] | 是 | 要评估的 GrowthBook feature key 列表 |

#### 响应

HTTP 响应包裹在标准结构中：

```json
{
  "result": 0,
  "msg": "success",
  "data": {
    "results": {
      "<feature_key>": {
        "value": true,
        "on": true,
        "source": "experiment",
        "rule_id": "rule_abc123",
        "variation_id": 1,
        "in_experiment": true
      }
    }
  },
  "trace_id": "xxx"
}
```

gRPC 直接返回 `EvalFeaturesResponse`（`map<string, FeatureResult>`）。

#### 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `value` | bool / string / double | 实验变体值，类型取决于 feature 配置 |
| `on` | bool | feature 是否激活 |
| `source` | string | 值来源：`experiment` / `force` / `defaultValue` / `override` / `prerequisite` |
| `rule_id` | string | 命中的 GrowthBook 规则 ID |
| `variation_id` | int / null | 实验分支索引，仅 `source="experiment"` 时存在 |
| `in_experiment` | bool | **关键字段** — `true` 时必须上报曝光事件 |

### 2.5 GetProfile — 属性批量加载（不走实验）

- HTTP：`POST /profiles`
- gRPC：`personalization.v1.PersonalizationEngine/GetProfile`

与 EvalFeatures 的区别：GetProfile **只加载原始属性值**，不触发 GrowthBook 实验评估。适用于后端数据驱动的决策场景（SmartPopup trigger、推送决策等）。

#### 请求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `entity_type` | string | 是 | `"user"` / `"device"` |
| `entity_id` | string | 是 | 实体标识 |
| `groups` | string[] | 是 | schema.yaml 中的 group 名，最多 20 个 |
| `context` | map<string,string> | 否 | 透传上下文（tenant_id / bundle 等） |

#### 响应

HTTP 响应包裹在标准结构中：

```json
{
  "result": 0,
  "msg": "success",
  "data": {
    "entity_type": "user",
    "entity_id": "12345",
    "groups": {
      "device_health": {
        "hasAnyDeviceSolarFault": true,
        "solarFaultDeviceCount": 3
      },
      "user_vip": {
        "isUserVip": false
      }
    },
    "failed_groups": [
      {
        "group": "nonexistent_group",
        "code": 40004,
        "reason": "group not found in registry"
      }
    ]
  },
  "trace_id": "xxx"
}
```

gRPC 直接返回 `GetProfileResponse`（`map<string, google.protobuf.Struct> groups` + `repeated FailedGroup failed_groups`）。

#### 值类型限定

`groups` 中每个字段的值只允许 JSON primitive（bool / number / string / null）或 `list<primitive>`。嵌套 object 视为契约违规。

#### Partial-Load 降级语义

单个 group 加载失败**不会导致整个请求失败**：
- 成功的 group 正常返回在 `data.groups` 中
- 失败的 group 从 `data.groups` 省略，错误信息在 `failed_groups` 数组中
- `failed_groups` 为空时整个字段省略

降级错误码：
| code | 含义 |
|------|------|
| 40004 | group 不在注册表中（group_not_found） |
| 50002 | loader 执行失败或超时（group_load_failed） |

#### 认证

- HTTP：网关注入 `X-User-ID` header；`entity_type=user` 时 `entity_id` 必须与 `X-User-ID` 一致，否则 403
- gRPC（集群内）：无需认证

#### SLA

- 并发：每请求最多 20 个 group 并行加载（MaxConcurrentLoaders = 20）
- 超时：单请求默认 5s deadline
- 建议 p95 < 100ms（集群内 gRPC）

### 2.3 错误码

| 错误码 | HTTP Status | gRPC Status | 场景 | 应对 | 适用接口 |
|--------|-------------|-------------|------|------|----------|
| 0 | 200 | OK | 正常 | 处理结果 | 通用 |
| 40001 | 400 | INVALID_ARGUMENT | JSON 格式错误或字段类型错误 | 不重试，修复请求 | 通用 |
| 40002 | 400 | INVALID_ARGUMENT | 缺少必填字段 | 不重试，补全必填字段 | 通用 |
| 40003 | 400 | INVALID_ARGUMENT | groups 数组超过 20 个 | 不重试，减少请求量 | GetProfile |
| 40004 | — | — | group 不在注册表中（仅出现在 failed_groups） | 检查 group 名拼写 | GetProfile |
| 40100 | 401 | UNAUTHENTICATED | X-User-ID 缺失（仅 HTTP） | 确认网关配置 | 通用 |
| 40300 | 403 | PERMISSION_DENIED | X-User-ID 与请求 entity_id 不匹配 | 不重试，修复 entity_id | 通用 |
| 50001 | 500 | INTERNAL | 服务内部错误 | 降级到默认值 | 通用 |
| 50002 | 500 | INTERNAL | 属性组加载失败（GetProfile 时仅出现在 failed_groups） | 降级到默认值 | 通用 |
| 50003 | 400 | INVALID_ARGUMENT | required_context 缺失 | 补全 context 字段 | GetProfile |
| 50201 | 502 | UNAVAILABLE | GrowthBook API 不可达 | 降级到默认值 | EvalFeatures |
| 50301 | 503 | UNAVAILABLE | GrowthBook SDK 未初始化 | 降级到默认值 | EvalFeatures |

### 2.4 降级策略（必须实现）

个性化服务不可用时，调用方**不应阻塞主流程**：
1. 记录告警日志（包含 user_id、trace_id）
2. 跳过实验评估，使用应用默认值
3. 继续处理原始请求

## 3 SDK

| 语言 | 安装方式 | 说明 |
|------|---------|------|
| Go | `go get gitlab.addx.ai/services/personalization-engine/sdk/go@latest` | 支持 gRPC + HTTP 两种模式 |
| Flutter/Dart | Git 依赖 `personalization-engine` 仓库 `sdk/flutter` 目录 | HTTP 模式 |
| Java | 计划中，暂未发布 | — |

Go SDK 关键配置：

集群内（Nacos 服务发现，推荐）：
```go
// nacosClient 通常在服务启动时已创建用于自注册，直接复用
client, err := profile.New(
    profile.WithNacosDiscovery(profile.NacosDiscoveryConfig{
        NamingClient: nacosClient,            // naming_client.INamingClient
        ServiceName:  "personalization-engine",
        Group:        "DEFAULT_GROUP",
        ClusterName:  os.Getenv("NACOS_CLUSTER_NAME"), // 按集群区分，如 "us"/"eu"
    }),
    profile.WithTimeout(10*time.Second),
)
```

跨集群：`profile.WithBaseURL("https://api-us.addx.live")` + 每次请求传 JWT

> **注意**：`WithNacosDiscovery` 内部使用 `grpc.WithResolvers` 注入，不污染全局 resolver 注册表。
> 初始 Nacos 不可用时不会阻断 dial，gRPC 会通过 backoff + `ResolveNow` 自动重试直到 Nacos 恢复。

## 4 Proto 定义

```protobuf
service PersonalizationEngine {
  rpc EvalFeatures(EvalFeaturesRequest) returns (EvalFeaturesResponse);
  rpc GetProfile(GetProfileRequest) returns (GetProfileResponse);
}

message EvalContext {
  string user_id = 1;
  string sn = 2;
  string app_type = 3;
  string app_version = 4;
  string tenant_id = 5;
  string bundle = 6;
  string app_name = 7;
  string language = 8;
  string source = 9;
  string env = 10;
  optional int32 pay_type = 11;
}

message EvalFeaturesRequest {
  EvalContext context = 1;
  repeated string feature_keys = 2;
}

message EvalFeaturesResponse {
  map<string, FeatureResult> results = 1;
}

message FeatureResult {
  oneof value {
    bool bool_value = 1;
    string string_value = 2;
    double number_value = 3;
  }
  bool on = 4;
  string source = 5;
  string rule_id = 6;
  optional int32 variation_id = 7;
  bool in_experiment = 8;
}

message GetProfileRequest {
  string entity_type = 1;
  string entity_id = 2;
  repeated string groups = 3;
  map<string, string> context = 4;
}

message GetProfileResponse {
  string entity_type = 1;
  string entity_id = 2;
  map<string, google.protobuf.Struct> groups = 3;
  repeated FailedGroup failed_groups = 4;
}

message FailedGroup {
  string group = 1;
  int32 code = 2;
  string reason = 3;
}
```

Go import: `personalizationv1 "gitlab.addx.ai/services/personalization-engine/api/v1"`

## 5 接入 Checklist

每项包含验证方法（`verify`），Step 6 必须对每项执行对应的验证动作。

| # | 检查项 | verify（自动验证方法） | 类型 |
|---|--------|----------------------|------|
| 1 | SDK 或 proto 依赖已引入 | grep `personalization-engine` in go.mod / pubspec.yaml | auto |
| 2 | 客户端初始化（全局复用） | grep `personalization-engine` + `WithNacosDiscovery|WithGRPCTarget|WithBaseURL` in init code | auto |
| 3 | 调用 EvalFeatures | grep `EvalFeatures` in code | auto |
| 4 | feature_keys 已指定 | grep `FeatureKeys|feature_keys` in EvalFeatures 调用处 | auto |
| 5 | 检查 `in_experiment` 并上报曝光 | grep `InExperiment|in_experiment` in result handling | auto |
| 6 | 降级逻辑（错误时跳过实验） | grep `EvalFeatures` + `err|error|fallback` in same handler | auto |
| 7 | HTTP 模式：JWT + `X-T: pn` Header | grep `Authorization|Bearer` + `X-T` in HTTP client config（仅 HTTP 模式） | optional |
| 8 | 超时设置 | grep `Timeout|timeout|DialTimeout` in client init | auto |
| 9 | EvalContext 填充完整性 | 检查 context 构造是否包含 user_id + 至少 app_type/app_version | manual |
| 10 | 曝光上报实现正确性 | 检查上报的 feature_key/variation_id 是否从 result 中正确取值 | manual |
| 11 | GetProfile：调用正确 | grep `GetProfile` in code（仅使用 GetProfile 的项目） | optional |
| 12 | GetProfile：降级处理 | 检查 `failed_groups` 非空时的处理逻辑，缺失字段按 undefined 处理 | optional |
| 13 | GetProfile：groups 数量 ≤ 20 | 静态检查请求中 groups 列表长度 | optional |
