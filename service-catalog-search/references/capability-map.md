# 能力映射 —— 自然语言 / 关键词 → `API` 名 / `metadata.tags`

> 这是 `find-capability` 把「我要做 X」映射到目录里 `API` 实体（=「能力」）的本地知识。
> **SSOT 是全局 `engineering/skills` 的 `docs/architecture/domain-model.md`（能力清单）和 `docs/architecture/backend-service-architecture.md`（5 层 / 调用规则）** —— 这里是它的「自然语言 → 规范名」索引，二者要对齐。

查不到时：① 看这表确认关键词 ② `catalog-query.sh list-capabilities` 浏览目录里实际有哪些 `API` ③ 翻全局 domain-model。

## 后端平台能力（`Component spec.type: service`）

| 你想说的（自然语言/关键词）| `API` 名（目录里的 `kind: API` 实体名）| `metadata.tags` 关键词 | 提供它的 Component | `Domain` | 约束（Component 的 `no-direct-*` tag）|
|---|---|---|---|---|---|
| 发推送 / push / 设备推送 / App 推送 | `push-notification` | `push`, `notification` | `novu`（立项中→上线后）/ 现有 `push` 服务 | `notifications` | `no-direct-fcm`, `no-direct-apns`, `no-direct-sendgrid` |
| 站内消息 / in-app message | `in-app-message` | `in-app-message`, `notification` | `novu` / `notification` 服务 | `notifications` | 同上 |
| 邮件通知 / email | `email-notification` | `email`, `notification` | `novu` / `notification` 服务 | `notifications` | `no-direct-sendgrid` |
| 短信 / SMS | `sms-notification` | `sms`, `notification` | `novu` | `notifications` | — |
| 灰度 / feature flag / 功能开关 / 实验分流 | `feature-flag-eval` | `feature-flag`, `experiment` | `personalization-engine` / `entity-profile` | `personalization` | `no-direct-growthbook`（业务方不直连 GrowthBook，走 PE.evalFeature()）|
| A/B 实验分组 / ab assignment | `ab-assignment` | `ab`, `experiment` | `personalization-engine` | `personalization` | `no-direct-growthbook` |
| 用户画像 / user profile / 实体 profile | `user-profile` | `profile`, `personalization` | `personalization-engine` / `entity-profile` | `personalization` | — |
| 受众 / 人群圈选 / 定向 / audience targeting / segment | `audience-segment` | `audience`, `targeting`, `segment` | `audiences` | `personalization` | — |
| 判断用户是否在订阅期 / 权益校验 / entitlement | `entitlement-check` | `entitlement`, `billing` | `subscription`（SUB）| `monetization` | `no-direct-stripe`, `no-direct-apple-pay`, `no-direct-airwallex` |
| 退款 / refund | `refund` | `billing`, `refund` | `subscription` | `monetization` | 同上 |
| 查订阅状态 / 订阅信息 | `subscription-query` | `subscription`, `billing` | `subscription` | `monetization` | 同上 |
| 用户身份 / 鉴权 / 用户基础属性 | `user-identity` / `user-auth` / `user-attributes` | `identity`, `user` | `user-center`（UC）| `identity` | — |
| 设备绑定 / 解绑 / 共享 / 设备列表 | `device-bind` / `device-unbind` / `device-share` / `device-list` | `device`, `device-management` | `device-management`（DM）| `device-management` | — |
| 固件升级 / 灰度 / 回滚 / OTA | `ota-rollout` / `ota-rollback` | `ota`, `firmware` | `ota`（剥离中，当前在 `iot-service-unified`；Component tag `status-extracting`）| `device-management` | — |
| 录像 / 回看 / AI 事件片段 | `recording-playback` / `ai-event-clip` | `recording`, `playback` | `recording` / `video-records`（REC）| `media` | — |
| 直播 / 对讲 / PTZ | `live-stream` / `intercom` / `ptz` | `live`, `media` | `live`(业务) + `kiss`(信令) | `media` | — |
| 增值导购 / Paywall / 转化漏斗 | `paywall-funnel` / `monetization-guide` | `paywall`, `engagement` | `engagement` | `engagement` | — |
| 弹窗 / Rating / 保修 / 差评拦截 | `smart-popup` / `rating` / `warranty` / `bad-review-intercept` | `smart-popup`, `customer-care` | `customer-care` | `customer-care` | — |
| 设备属性/事件/命令上下行 / 设备消息 | `device-uplink-downlink` | `iot`, `device`, `uplink-downlink` | `iot-backend` / `iot-platform` | `iot` | `no-direct-mqtt`（业务方不直接处理 MQTT）|
| 设备事件流 | `device-events` | `iot`, `device-events` | `iot-event-service` | `iot` | — |
| 设备能力 schema 注册 / 校验 / 物模型 | `thing-model-register` / `thing-model-validate` | `thing-model`, `iot` | `thing-model-platform` | `iot` | — |
| 视觉识别 / 人形检测 / LLM 对话 | `vision-inference` / `human-detection` / `llm-chat` | `ai`, `inference` | `ai-inference`（规划中）| `ai` | — |
| 接入网关 / 鉴权 / 限流 | `api-gateway` / `gateway-auth` / `rate-limit` | `gateway` | `apisix` | `gateway`(shared) | — |
| 内容管理 / 多语言素材 / Paywall 文案 / CMS | `content-management` / `i18n-asset` / `paywall-copy` | `content`, `cms` | `payload-cms` | `content`(shared) | 全公司通用，非营销专属 |
| 埋点上报 / event tracking | `event-tracking` | `analytics`, `tracking` | `tracker-sdk` → Snowplow Collector | `analytics`(shared) | — |
| 错误监控 / crash 监控（云、App）| `error-monitoring` / `crash-monitoring`（tag `scope-cloud`/`scope-app`）| `observability`, `crash` | `sentry` | `observability`(shared) | — |
| crash 监控（设备/嵌入式）/ fleet 健康 | `crash-monitoring`（tag `scope-device`）/ `device-fleet-health` | `observability`, `crash`, `device` | `memfault`（选型中）| `observability`(shared) | — |

## 包级特性（`Component spec.type: library` —— Flutter package / 嵌入式 package；也是 `API` 实体，靠 `metadata.tags` 区分）

| 关键词 | `API` 名 | tags | 提供它的 library Component |
|---|---|---|---|
| OTA 客户端（嵌入式）| `ota-client-api` | `embedded`, `ota` | `embedded-pkg-ota-client` |
| RTSP 栈（嵌入式）| `rtsp-stack-api` | `embedded`, `rtsp` | `embedded-pkg-rtsp-stack` |
| AI 检测（嵌入式）| `ai-detection-api` | `embedded`, `ai-detection` | `embedded-pkg-ai-detect` |
| 设备配对 UI（Flutter）| `device-pairing-ui-api` | `flutter-ui`, `device-pairing` | `flutter-pkg-device-control` |
| 登录/会话（Flutter 共享包）| `a4x-auth-api` | `flutter`, `auth` | `flutter-pkg-a4x-auth` |
| 网络层（Flutter 共享包）| `a4x-net-api` | `flutter`, `net` | `flutter-pkg-a4x-net` |

> 注意：「能力」名（`API` 名）必须在全局 domain-model 的清单里——CI 检查会校验各仓 `catalog-info.yaml` 的 `spec.providesApis` 里的名都在清单里，防止自造导致反查失效。这表是「自然语言 → 规范名」的便捷索引，规范名以全局 domain-model 为准；本表里标「现有 X 服务 / 规划中」的，等服务实际上架/迁移后以目录里的实际实体为准。
