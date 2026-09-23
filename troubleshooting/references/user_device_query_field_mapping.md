# UserDeviceBindingQuery 字段定义

以下按数据块列出字段的业务含义；**JSON 键名以 Swagger（OpenAPI）与实际 API 响应为准**。

## factoryInfoDOS

| #   | 字段（业务含义）        | JSON 键名          |
|-----|------------------------|--------------------|
| 1   | 当前设备是否绑定        | `isBind`           |
| 2   | 设备SN                  | `userSn`           |
| 3   | 无线MAC地址             | `macAddress`       |
| 4   | 源型号                  | `originalModelNo`  |
| 5   | 衍生型号                | `derivedModelNo`   |
| 6   | 客户型号                | `customerModelNo`  |
| 7   | 包装型号                | `packageModelNo`   |
| 8   | 出厂固件版本            | `firmwareId`       |
| 9   | 出厂MCU版本             | `mcuNumber`        |
| 10  | 客户ID（cuid）          | `customerId`       |
| 11  | 品牌                    | `brand`            |
| 12  | 工厂                    | `factory`          |
| 13  | 出厂时间                | `manufactureTime`  |
| 14  | 批次号                  | `batchNumber`      |
| 15  | 是否支持 Alexa          | `supportAlexa`     |

## bindInfoDOS

| #   | 字段（业务含义） | JSON 键名        |
| --- | -------- | ---------------- |
| 1   | 绑定时间     | `bindTime`       |
| 2   | 设备SN       | `userSn`         |
| 3   | 设备UID      | `serialNumber`   |
| 4   | 用户邮箱       | `email`          |
| 5   | 用户手机号      | `phone`          |
| 6   | user_id  | `userId`         |

## cameraInfoAndSettingsDOS

| #   | 字段（业务含义）     | JSON 键名                  |
| --- | ------------ | -------------------------- |
| 1   | 设备序列号（UID）          | `serialNumber`             |
| 2   | 当前固件         | `firmwareId`               |
| 3   | 设备名称         | `deviceName`               |
| 4   | 时区           | `timezone`                 |
| 5   | PIR 运动检测开关   | `pirEnable`                |
| 6   | PIR 运动检测灵敏度  | `motionSensitivity`        |
| 7   | 云录像时长        | `cloudRecordTime`          |
| 8   | 拍摄间隔开关       | `cloudCooldownEnable`      |
| 9   | 拍摄间隔时间       | `cloudCooldownTime`        |
| 10  | 云录像分辨率       | `cloudVideoResolution`     |
| 11  | SD 卡录像拍摄间隔开关 | `sdcardCooldownEnable`     |
| 12  | SD 卡录像模式     | `sdcardVideoMode`          |
| 13  | SD 卡录像拍摄间隔时间 | `sdcardCooldownTime`       |
| 14  | SD 卡录像时长     | `sdcardRecordTime`         |
| 15  | 白光灯报警开关      | `whitelightAlarmEnable`    |
| 16  | 声音报警开关       | `soundAlarmEnable`         |
| 17  | 夜视切换灵敏度      | `nightVisionSensitivity`   |
| 18  | 夜视模式（红外/全彩）  | `nightVisionMode`          |
| 19  | propertyJson | `propertyJson`             |

## vipStatusResults

| #   | 字段（业务含义）    | JSON 键名      |
| --- | ----------- | -------------- |
| 1   | 设备 VIP 激活状态 | `vipActivate`  |

## notificationInfoResults

| #   | 字段（业务含义） | JSON 键名              |
| --- | -------- | ---------------------- |
| 1   | 推送总开关    | `notificationSwitch`   |
| 2   | 推送检测对象   | `eventObjects`         |
| 3   | 推送检测事件类型 | `eventTypes`           |
| 4   | other 开关 | `enableOther`          |

## activityZoneInfos

| #   | 字段（业务含义）  | JSON 键名    |
| --- | --------- | ------------ |
| 1   | 提醒区域名称    | `zoneName`   |
| 2   | 提醒区域顶点坐标  | `vertices`   |
| 3   | 提醒区域是否被删除 | `deleted`    |

## otaInfo

| #   | 字段（业务含义）  | JSON 键名          |
| --- | --------- | ------------------ |
| 1   | 目标固件版本    | `targetFirmware`   |
| 2   | OTA 是否进行中 | `inProgress`       |
| 3   | 已传输固件包大小  | `transferredSize`  |
| 4   | OTA 状态    | `otaStatus`        |
| 5   | 是否静默升级    | `silentOta`        |
| 6   | OTA 开始时间  | `otaTime`          |

## deviceStatuses

| #   | 字段（业务含义）      | JSON 键名        |
| --- | ------------- | ---------------- |
| 1   | UID           | `serialNumber`   |
| 2   | 设备最近一次上报状态    | `status`         |
| 3   | 设备最近一次状态流转的原因 | `reason`         |
| 4   | 设备最近一次状态更新时间  | `updateTime`     |

常见 JSON 键名（以响应为准）：`status`、`reason`、`updateTime`。

---

# UserInfoQuery 字段定义

## user（用户账号信息）

| #   | 字段（业务含义）              | JSON 键名          |
| --- | ----------------------- | ------------------ |
| 1   | 用户ID                   | `userId`           |
| 2   | 邮箱                     | `email`            |
| 3   | 匿名邮箱                   | `anonymousEmail`   |
| 4   | 邮箱后缀                   | `emailDomain`      |
| 5   | 账号用户名                  | `name`             |
| 6   | 手机号                    | `phone`            |
| 7   | App语言                  | `language`         |
| 8   | 租户ID                   | `tenantId`         |
| 9   | App注册国家                | `countryNo`        |
| 10  | App注册时间                | `registerTime`     |
| 11  | 手机系统（iOS/Android）      | `appType`          |
| 12  | 注销状态（0-未注销、1-已注销）      | `cancellation`     |

## devices（当前绑定设备信息）

| #   | 字段（业务含义） | JSON 键名          |
| --- | -------- | ------------------ |
| 1   | 管理员账号ID  | `adminId`          |
| 2   | 设备SN     | `userSn`           |
| 3   | 设备UID    | `serialNumber`     |
| 4   | 绑定时间     | `bindTime`         |
| 5   | 固件版本     | `firmwareId`       |
| 6   | 衍生型号     | `derivedModelNo`   |
| 7   | 客户型号     | `customerModelNo`  |
| 8   | 设备名字     | `deviceName`       |
| 9   | 时区       | `timezone`         |

## vip（账号订阅信息）

| #   | 字段（业务含义）              | JSON 键名            |
| --- | ----------------------- | -------------------- |
| 1   | 用户ID                   | `userId`             |
| 2   | 订阅类型                   | `subscriptionType`   |
| 3   | 套餐生效时间                 | `startTime`          |
| 4   | 套餐结束时间                 | `endTime`            |
| 5   | 套餐创建时间 / 扣款时间          | `createTime`         |
| 6   | 账号下生效 vip 设备列表         | `effectiveDevice`    |
| 7   | 是否免费试用                 | `freeTrial`          |
| 8   | 订单号                    | `tradeNo`            |
| 9   | 滚动云存大小                 | `rollingDay`         |
| 10  | 租户ID                   | `tenantId`           |
| 11  | App版本                  | `appVersion`         |
| 12  | 套餐是否生效中                | `active`             |
| 13  | 套餐支付类型                 | `paymentType`        |
| 14  | 订阅是否取消（0-未取消 1-取消）     | `orderCancel`        |
| 15  | 订阅取消时间                 | `orderCancelTime`    |
| 16  | 订阅是否退款（0-未退款 1-退款）     | `orderRefund`        |
| 17  | 订阅退款时间                 | `orderRefundTime`    |
| 18  | 设备UID                  | `serialNumber`       |
| 19  | 是否自动领取free license      | `autoReem`           |

## binding（账号维度绑定历史记录）

| #   | 字段（业务含义） | JSON 键名            |
| --- | -------- | -------------------- |
| 1   | 绑定操作ID   | `operationId`        |
| 2   | 绑定请求时间   | `bindRequestTime`    |
| 3   | 设备SN     | `userSn`             |
| 4   | 设备UID    | `serialNumber`       |
| 5   | 设备IP     | `deviceIp`           |
| 6   | 绑定类型     | `bindType`           |
| 7   | 绑定方式     | `bindMethod`         |
| 8   | 绑定完成时间   | `bindCompleteTime`   |

## bindSnAndUserSnInfo（账号历史绑定设备去重列表）

| #   | 字段（业务含义） | JSON 键名        |
| --- | -------- | ---------------- |
| 1   | 设备SN     | `userSn`         |
| 2   | 设备UID    | `serialNumber`   |
