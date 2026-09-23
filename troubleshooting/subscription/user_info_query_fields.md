# UserInfoQuery 字段与路径说明

本文档供解析 `/api/v1/log-search/query/db` 响应中 `result.data.results.UserInfoQuery` 时使用。**英文 JSON 键名以 Swagger（OpenAPI）与实际响应为准**；下表为各数据块的业务语义说明。

**样例响应**：见同目录 `request_and_response.json`，`UserInfoQuery` 节点位于 `result.data.results.UserInfoQuery`。

---

## JSON 路径锚点

| 数据块 | 典型路径（相对 `result.data.results`） |
| ------ | -------------------------------------- |
| 整条用户查询记录 | `UserInfoQuery[i]` |
| 用户基础信息 | `UserInfoQuery[i].user[j]` |
| 名下设备列表 | `UserInfoQuery[i].devices[j]` |
| 订阅（VIP）流水 | `UserInfoQuery[i].vip[j]` |
| 绑定操作记录 | `UserInfoQuery[i].binding[j]` |
| SN 与 UserSN 映射 | `UserInfoQuery[i].bindSnAndUserSnInfo[j]` |

### 本 Skill 步骤中已使用的路径

| 用途 | 路径 |
| ---- | ---- |
| 用户基础信息 | `UserInfoQuery[0].user[0]` |
| 订阅流水 | `UserInfoQuery[0].vip[]` |
| 名下设备列表 | `UserInfoQuery[0].devices[]` |

---

## 字段清单（按数据块）

### 顶层字段

| 字段 | 业务含义 |
| ---- | -------- |
| `roleId` | 用户角色 ID |
| `requestUserInfo` | 本次查询使用的原始用户标识值 |
| `environment` | 数据中心环境 |

---

### user

| 字段 | 业务含义 |
| ---- | -------- |
| `userId` | 平台用户 ID（脱敏） |
| `email` | 注册邮箱（脱敏） |
| `anonymousEmail` | 系统生成的匿名邮箱，用于隐私保护 |
| `emailDomain` | 邮箱域名 |
| `name` | 用户昵称 |
| `phone` | 手机号 |
| `language` | App 语言设置 |
| `tenantId` | 租户 ID，即 App 标识 |
| `countryNo` | 国家/地区代码 |
| `registerTime` | 账号注册时间 |
| `appType` | 注册时使用的平台 |
| `cancellation` | 是否已注销账号（`0`=正常，`1`=已注销） |

---

### devices

用户账号下绑定的设备列表（摘要信息，详细设备数据见 `UserDeviceBindingQuery`）。

| 字段 | 业务含义 |
| ---- | -------- |
| `adminId` | 设备所有者（管理员）用户 ID（脱敏） |
| `userSn` | 设备与用户绑定关系的 UserSN（脱敏） |
| `serialNumber` | 设备 SN（脱敏） |
| `bindTime` | 绑定完成时间 |
| `firmwareId` | 设备当前固件版本 |
| `derivedModelNo` | 衍生型号 |
| `customerModelNo` | 客户型号 |
| `deviceName` | 设备自定义名称 |
| `timezone` | 设备所在时区 |

---

### vip

订阅（VIP）流水，每条记录对应一笔订阅订单，按时间顺序排列。

| 字段 | 业务含义 |
| ---- | -------- |
| `userId` | 用户 ID（脱敏） |
| `subscriptionType` | 套餐类型名称 |
| `startTime` | 套餐生效开始时间 |
| `endTime` | 套餐到期时间 |
| `createTime` | 订单创建时间 |
| `effectiveDevice` | 生效设备列表（JSON 数组字符串，设备维度套餐才有值；账号维度套餐为 `null`） |
| `freeTrial` | 是否首月免费试用（`1`=是，`0`=否；`null` 表示该套餐类型不适用） |
| `tradeNo` | 第三方支付平台订单号（Google Play / App Store / Airwallex 等；系统发放套餐为 `null`） |
| `rollingDay` | 云存储滚动保留天数 |
| `tenantId` | 下单时的租户 ID |
| `appVersion` | 下单时的 App 版本 |
| `active` | 当前是否处于激活状态（`1`=激活，`0`=未激活/已过期） |
| `paymentType` | 支付渠道与类型 |
| `orderCancel` | 是否已取消续订（`1`=已取消，`0`=未取消） |
| `orderCancelTime` | 取消续订时间 |
| `orderRefund` | 是否已退款（`1`=是，`0`=否） |
| `orderRefundTime` | 退款时间 |
| `serialNumber` | 关联设备 SN（设备维度的旧版免费套餐有值；新版付费订阅通过 `effectiveDevice` 关联，此字段为 `null`） |
| `autoReem` | 是否自动续订（`0`=否；系统发放套餐为 `null`） |
| `extend` | 扩展信息（JSON 字符串，含 App 详情、购买渠道、`productId`、`purchaseToken`、`tierDeviceList` 等） |

#### extend 内常用字段

| 字段 | 业务含义 |
| ---- | -------- |
| `app.appName` | App 名称 |
| `app.appType` | 平台（Android / iOS） |
| `app.versionName` | 下单时的 App 版本名 |
| `app.env` | 环境标识 |
| `countryNo` | 下单时的国家代码 |
| `guidanceSource` | 购买引导来源（整型枚举） |
| `outTradeNo` | 与 `tradeNo` 一致的外部订单号 |
| `productId` | 套餐产品 ID |
| `purchaseToken` | Google Play 购买 Token（用于服务端校验） |
| `subscriptionGroupId` | 订阅组 ID（如 `cloud_premium_group_v2`） |
| `tierDeviceList` | 生效设备列表（与 `effectiveDevice` 一致） |

---

### binding

设备绑定操作流水，每条记录对应一次绑定动作。

| 字段 | 业务含义 |
| ---- | -------- |
| `operationId` | 绑定操作唯一 ID |
| `bindRequestTime` | 绑定请求发起时间 |
| `bindCompleteTime` | 绑定完成时间 |
| `userSn` | 用户 UserSN（脱敏） |
| `serialNumber` | 设备 SN（脱敏） |
| `deviceIp` | 绑定时设备 IP（中间段脱敏） |
| `bindType` | 绑定类型（如普通绑定/共享绑定） |
| `bindMethod` | 绑定方式（如扫码/AP 配网） |

---

### bindSnAndUserSnInfo

设备 SN 与 UserSN 的映射关系（每台绑定设备一条）。

| 字段 | 业务含义 |
| ---- | -------- |
| `userSn` | 用户 UserSN（脱敏） |
| `serialNumber` | 设备 SN（脱敏） |

---

## 紧凑对照

| 数据块 | 包含字段 |
| ------ | -------- |
| user | userId、email、anonymousEmail、emailDomain、name、phone、language、tenantId、countryNo、registerTime、appType、cancellation |
| devices | adminId、userSn、serialNumber、bindTime、firmwareId、derivedModelNo、customerModelNo、deviceName、timezone |
| vip | userId、subscriptionType、startTime、endTime、createTime、effectiveDevice、freeTrial、tradeNo、rollingDay、tenantId、appVersion、active、paymentType、orderCancel、orderCancelTime、orderRefund、orderRefundTime、serialNumber、autoReem、extend |
| binding | operationId、bindRequestTime、bindCompleteTime、userSn、serialNumber、deviceIp、bindType、bindMethod |
| bindSnAndUserSnInfo | userSn、serialNumber |
| 顶层 | roleId、requestUserInfo、environment |
