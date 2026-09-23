---
title: iot-service-monitoring
---

# iot-service-monitoring 参考

适用于 `iot-service` 后端（含 `naturehood-server`）的线上监控盘点与问题排查：

- 线上/灰度健康度
- iot 接口报错/延迟/MQTT/绑定/OTA 异常
- 告警覆盖是否齐全
- 定位 Grafana 上 iot-service 相关 dashboard/告警
- 上线后验证

## 触发边界

当出现以下任一上下文时，优先套用该参考文档：

- 明确提及 `iot-service` / `naturehood-server`
- 提及 `library`、`timeline`、`postcard`、`saas-iot灰度`
- 提及 `prod-{cn,us,eu}-iot-service` 或相关 dashboard 名称
- 典型排查词中出现 `iot 接口报错排查`、`iot-service 线上告警`、`iot-service 监控`

## 监控拓扑（截至 2026-06）

### Dashboard 清单

| 类型 | Dashboard | uid | 用途 |
|------|-----------|-----|------|
| **iot 服务层** | 服务监控-iot-service-apimetrics | `oWQMeuInk` | API 调用量/耗时（`server_api_call_cost_milliseconds`） |
| | 服务监控-iot-service-httprequestmetrics | `6u77nqInk` | HTTP 请求（`http_server_requests_seconds_*`、`reject_http_count`） |
| | 服务监控-iot-service-keyfunctionsmetrics | `hKA_jNH7k` | 关键业务函数（绑定/唤醒/PIR/par/push） |
| | 服务监控-iot-service-mybatismetrics | `z-6ZaQInz` | MyBatis SQL 耗时 |
| **iot 业务层** | 业务监控-Library相关业务接口耗时 | `69AkfmL7k` | Library API（36 panel） |
| | 业务监控-跨镜聚合-iot-service-cloud | `iot-cross-camera-aggregation` | 跨镜聚合 |
| | saas-iot灰度上线 | `Doa2ZOLnz` | 灰度与上线必看 |
| **后端总览** | 业务监控-后端 | `UWulzimMk` | 后端主监控（23 条告警） |
| | 业务监控-后端业务耗时-{中国/美国/欧洲} | `OP2cEgHIk`/`k4F_eVI7z`/`3v7NygNSz` | 分区域耗时 |
| **上线必看** | 上线必看 - 美国/欧洲/中国 | `T2-U1V3Vk`/`-eN6JV3Vk`/`TtuJoV34z` | 部署后必看（离线率/直播/Kafka/切片） |
| **naturehood** | Timeline Service (Prod) | `JX8PgJTvz` | timeline 指标 |
| | PostCard Service (Prod) | `0k59WToDk` | postcard 指标 |
| | Naturehood Service Health (Prod) | `swUPRJovz` | health 指标 |
| | engagement-service · alerts/health | `engagement-service-alerts`/`engagement-service-health` | engagement 相关 |

### iot-service 关键指标

常见指标包括：

- `http_server_requests_seconds_count{job,uri,status,exception}`
- `server_api_call_cost_milliseconds`
- `mqtt_consume_count` / `mqtt_handle_count` / `mqtt_handle_error_count` / `mqtt_handle_cost_milliseconds`
- `send_saas_ai_task_count`
- `async_wakeup_device_count` / `device_wakeup_count`
- `bind_from_camera_count` / `bind_mqtt_reponse_count`
- `report_log_count`
- `log_level_count{loglevel}`
- `ios_push_count` / `ios_push_fail_count`
- `http_report_event_count` / `http_report_event_error_count`
- `reject_http_count`
- `*_linode_par_*`

`job` 常用 `prod-{cn,us,eu}-iot-service`，部分告警使用通配 `prod-{region}-iot.*`。

### 已知覆盖缺口（盘点结论）

1. 7 个 iot-service 专属 dashboard 当前无告警：`apimetrics`、`httprequestmetrics`、`keyfunctionsmetrics`、`mybatismetrics`、`Library接口耗时`、`跨镜聚合`、`saas-iot灰度上线`。
2. `unknown` 常见于失败率告警分子为空（`noDataState=keep_state`），高危时可误判。
3. iot/naturehood 告警默认未配置 default notification channel。
4. iot-service 无专属告警 channel，当前告警多共用 PagerDuty。

### 排查流程（建议）

1. 先看 `上线必看` 与 `saas-iot灰度上线`。
2. HTTP 层定位：`6u77nqInk`（`status!=200` / `exception!=None`）。
3. 业务层定位：`hKA_jNH7k`。
4. DB 层定位：`z-6ZaQInz`。
5. MQTT/绑定/OTA：在 `UWulzimMk` 观察吞吐与失败率。
6. 深入：结合 `troubleshooting` 查日志、数据库与设备事件。

### 配套边界（与 grafana/troubleshooting 的分工）

- 通用 Grafana 操作仍走 `grafana`。
- 日志、工单、设备事件排查走 `troubleshooting`。
- PromQL 直查走 `prometheus`。
- 本文不负责直接改 dashboard/alert：产出建议后由 `grafana` 或 `grafana-dashboard-alert-update` 执行变更。
