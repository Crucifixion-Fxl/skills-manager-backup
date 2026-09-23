# Recipe: kiss
# 配方：kiss

Standalone Java service. Owns **device keepalive (TCP + WebSocket)** and **WebRTC live-stream signaling**. Same magnitude as iot-service-cloud in terms of importance, **vastly larger** in terms of long-lived connections (US 2.4M vs ~250k for any other service).
独立 Java 服务，承载**设备保活 (TCP + WebSocket)** 和 **WebRTC 直播信令**。重要性与 iot-service-cloud 同等，**长连数远超**任何其它服务（US 240 万 vs 其它 ~25 万）。

## 1. Code locations / 代码位置

| Concern | Path |
|---|---|
| Repo (local) | `/Users/zouyijiang/workspace/kiss` |
| Repo (GitLab) | `gitlab.addx.ai/CLOUD/kiss` |
| Custom metrics — master | `src/main/java/com/addx/kiss/utils/PrometheusMetricsUtil.java` (13 metrics) |
| Logback config | `src/main/resources/logback-spring.xml` |

## 2. Prometheus job labels (NOTE: prefix INVERTED vs iot-service-cloud) / job 标签（前缀与 iot-service-cloud 相反）

| Variant | Job label | US pods | EU | CN |
|---|---|---|---|---|
| Main signaling/keepalive | `{us,eu,cn}-prod-kiss` | 29 | 8 | 3 |
| JVM-only scrape | `{us,eu,cn}-prod-kiss-jvm` | 29 | 8 | 3 |
| Port scrape (node_exporter) | `{us,eu,cn}-prod-kiss-port` | 58 | 16 | 6 |
| Live-stream signaling (WebRTC SafeRTC) | `{us,eu}-prod-kiss-safertc` | 2 | 2 | n/a |

## 3. Custom counters/gauges (label `instance` = pod hostname) / 自定义 counter / gauge

13 metrics:

| Name | Type | Labels | What it answers |
|---|---|---|---|
| `kiss_receive_tcp_udp_count` | Counter | `instance,type,packet_type` | TCP/UDP packet ingress (signaling traffic) |
| `kiss_heartbeat_device_count` | Gauge / Counter? | `instance` | Heartbeat-driven activity rate (US ~21,576/s) |
| `p2p_user_device_num` | Gauge | `instance,type` | Currently connected user/device via WS |
| `p2p_websocket_connect_disconnect_count` | Counter | `instance,type` | WS connect/disconnect events |
| `p2p_websocket_live_time` | Histogram | `instance,type,role` | WS lifetime ms |
| `p2p_websocket_readwrite_time` | Histogram | `instance,type` | WS read/write ms |
| `p2p_websocket_dead_count` | Counter | `instance` | Dead WS count |
| `p2p_websocket_message_count` | Counter | `instance,type` | WS message count (live-stream traffic) |
| `device_wakeup_count` | Counter | `instance,type` | (kiss has its own) |
| `websocket_cost_time` | Histogram | `instance,type,method` | WS request latency |
| `kiss_ws_cost_time` | Histogram | `instance,type,method` | Kiss WS request latency by method |
| `send_message_begin` | Counter | `instance,object,method,isSuccess` | Send-message issuance |
| `send_message_begin_cost_time` | Histogram | same | Send-message duration |

## 4. Top recipes / 标准查询

```promql
# === Online device count (cluster-wide via TCP heartbeat — the gold-standard fleet size proxy) ===
sum(node_netstat_Tcp_CurrEstab{job="us-prod-kiss"})

# Per-pod connection density
avg(node_netstat_Tcp_CurrEstab{job="us-prod-kiss"})

# === Heartbeat & ingress rate ===
sum(rate(kiss_heartbeat_device_count{job="us-prod-kiss"}[5m]))   # ~21,576/s in US
sum(rate(kiss_receive_tcp_udp_count{job="us-prod-kiss"}[5m]))    # ~40,393/s in US

# === WebSocket events ===
sum(rate(p2p_websocket_connect_disconnect_count{job="us-prod-kiss",type="connect"}[5m]))   # connect rate
sum(rate(p2p_websocket_connect_disconnect_count{job="us-prod-kiss",type="disconnect"}[5m]))
sum by (type)(rate(p2p_websocket_message_count{job="us-prod-kiss"}[5m]))                   # by message type

# === WS request latency by method ===
histogram_quantile(0.99, sum by (le,method)(rate(kiss_ws_cost_time_bucket{job="us-prod-kiss"}[5m])))

# === Dead connection rate (red flag if rising) ===
sum(rate(p2p_websocket_dead_count{job="us-prod-kiss"}[5m]))

# === HTTP-level (admin endpoints, etc) ===
sum(rate(http_server_requests_seconds_count{job="us-prod-kiss-jvm"}[5m]))
histogram_quantile(0.99, sum by (le)(rate(http_server_requests_seconds_bucket{job="us-prod-kiss-jvm"}[5m])))

# === JVM thread states (kiss has thread-state) ===
sum by (state)(jvm_threads_states_threads{job="us-prod-kiss-jvm"})
```

## 5. Logs in kiss — total counts ARE valid frequency / 日志总条数**可以**作为频率

`src/main/resources/logback-spring.xml` JSON encoder emits ONLY `requestId` + `clientId` (no `userId` / `serialNumber`). Therefore log shipping does NOT split, and **total log-line count IS a meaningful frequency** (modulo sampling).
`logback-spring.xml` JSON encoder 仅含 `requestId` + `clientId`，无 user/SN。因此日志收集**不**拆分，**某行日志的总条数是有意义的频率**（受采样影响除外）。

Use the `troubleshooting` skill `/log-search/query-index` with index pattern `kiss-*`. Get live index list via `/api/v1/log-search/index-patterns`.
通过 `troubleshooting` skill 的 `/log-search/query-index`，索引模式 `kiss-*`。索引列表见 `/api/v1/log-search/index-patterns`。

## 6. Anchor magnitudes (US prod 2026-04-28) / 锚点量级

| Signal | Value |
|---|---|
| Pods | 29 |
| Connected devices (TCP CurrEstab sum) | 2,384,131 |
| Avg per-pod connection density | 82,200 |
| `kiss_heartbeat_device_count` rate | 21,576 /s |
| `kiss_receive_tcp_udp_count` rate | 40,393 /s |
| `p2p_websocket_message_count` rate | 8.4 /s |

EU: 228k connected devices, 8 pods · CN: 53.8k devices, 3 pods.

## 7. Why kiss is foundational for iot-service-cloud capacity calc / 为什么 kiss 是 iot-service-cloud 容量计算的基石

You can derive **per-device call frequency** from any iot-service-cloud endpoint by `endpoint QPS ÷ kiss TCP CurrEstab`. This works because:
通过 `endpoint QPS ÷ kiss TCP CurrEstab` 可以反推**单设备调用频率**，因为：

1. Every device that's online holds a kiss TCP connection (kiss is the keepalive layer).
2. Endpoints on iot-service-cloud are device-driven (devices poll).
3. The ratio answers "how often does each device hit this endpoint?"

Example for `/deviceMsg/setting`:
```
US: 3,295 req/s ÷ 2,384,131 devices = 1 call per device per ~12.4 min
```

This is your single-device frequency contract for any code change touching that endpoint.
这就是任何改动该 endpoint 时的单设备频率契约。
