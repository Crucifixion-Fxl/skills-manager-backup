# 权益中心接入规格

> 平台归属：vip-service
> 协议：gRPC（查询） + Kafka（事件通知）

## 1 平台简介

权益中心管理「用户购买订阅后，哪些设备获得哪些能力」。履约方通过监听 Kafka 事件 + gRPC 回查来获取设备的最新权益状态。

核心流程：**Kafka 事件通知权益变更 → gRPC 回查最新权益 → 执行业务动作**。

## 2 Kafka 事件

### Topic

`vip.entitlement.changed`

### Consumer Group 命名

`entitlement-<your-service-name>`（如 `entitlement-cloud-storage`）

### 消息格式

```json
{
  "type": "entitlement.changed",
  "tenantId": "vicoo",
  "userId": 123456,
  "deviceIds": ["DEVICE_001", "DEVICE_002"],
  "timestamp": 1712534400000
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定值 `"entitlement.changed"` |
| `tenantId` | string | 租户标识 |
| `userId` | int64 | 用户 ID |
| `deviceIds` | string[] | 权益变更的设备列表 |
| `timestamp` | int64 | 事件时间（Unix 毫秒） |

### 消费约束

- **at-least-once 投递，必须幂等**
- 事件不带权益内容，只通知「哪些设备变了」
- 收到后必须通过 gRPC 回查最新状态
- 有约 5 秒延迟（Outbox 投递）
- 不保证顺序，以回查结果为准

## 3 gRPC 接口

### 服务地址

K8s 集群内：`vip-service:9090`

### Proto 依赖

Go module: `com/addx/vip-service`
Go import: `entitlementv1 "com/addx/vip-service/api/entitlement/v1"`

引入方式：在 `go.mod` 中添加 replace 指向内部 Git 仓库：

```
require com/addx/vip-service v0.0.0

replace com/addx/vip-service => gitlab.addx.ai/addx/vip-service <version/commit>
```

具体版本号以实际发布为准，也可用 `go mod edit -replace` 命令添加。

### 3.1 QueryByDevices — 按设备批量查询（主接口）

收到 Kafka 事件后用事件中的 deviceIds 调用此接口。

#### 请求：QueryByDevicesRequest

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tenant_id` | string | 是 | 租户标识 |
| `device_ids` | string[] | 是 | 设备 ID 列表，最多 100 个 |

#### 响应：QueryByDevicesReply

```
QueryByDevicesReply
  └─ devices: DeviceEntitlements[]
       ├─ device_id: string
       └─ entitlements: DeviceEntitlement[]
            ├─ product_id: int64        — 产品 ID
            ├─ scope: enum              — 作用范围（仅供参考）
            └─ config: Struct           — 权益配置（核心字段）
```

- `entitlements` 为空 = 该设备无生效权益
- `config` 类型为 `google.protobuf.Struct`，所有数字为 double（Go 中 `GetNumberValue()` 返回 float64）
- 同一设备可能有多条记录（多个产品），需自行合并

#### 响应示例

```json
{
  "devices": [
    {
      "deviceId": "DEVICE_001",
      "entitlements": [
        {
          "productId": 1001,
          "scope": "ENTITLEMENT_SCOPE_USER",
          "config": {
            "rollingDays": 30,
            "storage": 5368709120,
            "videoPlaybackEnabled": true,
            "personDetectionEnabled": true
          }
        }
      ]
    },
    {
      "deviceId": "DEVICE_002",
      "entitlements": []
    }
  ]
}
```

### 3.2 QueryByUser — 按用户查询（可选）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tenant_id` | string | 是 | 租户标识 |
| `user_id` | int64 | 是 | 用户 ID |

响应结构与 QueryByDevices 完全一致。主要用于管理后台和运维调试。

### 3.3 错误码

| gRPC Status | 场景 | 应对 |
|-------------|------|------|
| `INVALID_ARGUMENT` | 参数缺失、device_ids 超 100、tenant_id 为空 | 不重试，修复参数 |
| `NOT_FOUND` | 产品无权益映射 | 不重试，视为无权益 |
| `UNAVAILABLE` | 设备服务不可用 | 重试，指数退避（初始 1s，最大 30s） |
| `INTERNAL` | 服务内部错误 | 重试，同 UNAVAILABLE；持续失败告警 |

## 4 config 字段清单

你只需关注自己服务相关的字段。

| 字段 | 业务类型 | 说明 | 相关服务 |
|------|---------|------|----------|
| `rollingDays` | int | 视频回看天数 | 云存储 |
| `storage` | long | 云存储容量（byte） | 云存储 |
| `videoPlaybackEnabled` | bool | 视频回放 | 云存储 |
| `videoLibraryAccess` | string | 视频库访问等级 | 云存储 |
| `personDetectionEnabled` | bool | 人员检测 | AI |
| `petDetectionEnabled` | bool | 宠物检测 | AI |
| `vehicleDetectionEnabled` | bool | 车辆检测 | AI |
| `packageDetectionEnabled` | bool | 包裹检测 | AI |
| `faceRecognitionEnabled` | bool | 人脸识别（Gen3） | AI |
| `birdDetectionEnabled` | bool | 鸟类检测 | AI |
| `gen3FeaturesEnabled` | bool | Gen3 专属功能 | AI |
| `maxDeviceNum` | int | 最大设备数（-1=无限） | 设备管理 |
| `guestLiveStreamEnabled` | bool | Guest 直播 | 设备共享 |
| `guestBirdTabEnabled` | bool | Guest 鸟类 Tab | 设备共享 |
| `guestVideoLibraryAccess` | string | Guest 视频库 | 设备共享 |
| `eventNotificationTypes` | string[] | 事件通知类型 | 推送 |
| `homeModeEnabled` | bool | Home 模式总开关 | Home 模式 |
| `availableModes` | string[] | 可用模式 | Home 模式 |
| `emergencyResponseEnabled` | bool | 紧急响应 | Home 模式 |
| `homeNum` | int | Home 数量限制 | Home 模式 |
| `pirTriggerEnabled` | bool | PIR 触发录像 | 运动检测 |
| `availableCooldownIntervals` | int[] | 可选冷却间隔 | 运动检测 |
| `fourGDataEnabled` | bool | 4G 数据服务 | 4G |

### 无订阅默认值

当设备无生效权益（`entitlements` 为空）时，权益中心使用以下默认值。接入方可参考此配置实现降级逻辑：

```json
{
  "rollingDays": 7,
  "storage": 1073741824,
  "videoPlaybackEnabled": false,
  "videoLibraryAccess": "limited",
  "personDetectionEnabled": true,
  "petDetectionEnabled": true,
  "vehicleDetectionEnabled": true,
  "packageDetectionEnabled": false,
  "faceRecognitionEnabled": false,
  "birdDetectionEnabled": false,
  "gen3FeaturesEnabled": false,
  "maxDeviceNum": 0,
  "guestLiveStreamEnabled": false,
  "guestBirdTabEnabled": false,
  "guestVideoLibraryAccess": "none",
  "eventNotificationTypes": ["person", "pet", "vehicle"],
  "homeModeEnabled": false,
  "availableModes": [],
  "emergencyResponseEnabled": false,
  "homeNum": 0,
  "pirTriggerEnabled": false,
  "availableCooldownIntervals": [60, 120, 180, 300],
  "fourGDataEnabled": false
}
```

## 5 接入 Checklist

每项包含验证方法（`verify`），Step 6 必须对每项执行对应的验证动作。

| # | 检查项 | verify（自动验证方法） | 类型 |
|---|--------|----------------------|------|
| 1 | 引入 proto 依赖 | grep `com/addx/vip-service` in go.mod | auto |
| 2 | gRPC 客户端初始化 `vip-service:9090` | grep `vip-service` + `9090` in code | auto |
| 3 | Kafka Consumer 订阅 `vip.entitlement.changed` | grep `vip.entitlement.changed` in code | auto |
| 4 | Consumer group 命名 `entitlement-<service>` | grep `entitlement-` in consumer config | auto |
| 5 | 收到事件后调用 QueryByDevices 回查 | grep `QueryByDevices` in consumer handler | auto |
| 6 | 错误处理区分重试/不重试 | grep `INVALID_ARGUMENT|NOT_FOUND|UNAVAILABLE` in error handling code | auto |
| 7 | 处理无权益场景（entitlements 为空） | grep `Entitlements|entitlements` + `== 0|== nil|empty|len` in handler | auto |
| 8 | 消费幂等 | 检查是否有幂等键/去重机制（如 DB upsert、Redis setnx） | manual |
| 9 | 多 product 合并 | 检查同一设备多条记录的合并逻辑 | manual |
| 10 | 可选：定期轮询兜底 | 检查是否有定时任务调用 QueryByDevices | optional |
