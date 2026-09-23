# Magnitude Anchors — Point-in-time + Ratios
# 数量级锚点 — 点位值 + 比例

**Why this file exists**: when a query returns 5,000 QPS, you need a fast sanity check — is that high or low? This file gives you the comparison baseline. **Track ratios across services more than absolute values** — absolutes drift as the platform grows; ratios are sticky.
**本文为什么存在**：query 返回 5,000 QPS 时，你需要一个快速 sanity check ——这是高还是低？本文提供对比基线。**跨服务比例比绝对值更可靠** ——绝对值会随平台增长漂移，比例较稳定。

Last verified: **2026-04-28** (during setting-override evaluation). Re-query before any go/no-go decision; freshness > 30 days = re-verify.
最近一次验证：**2026-04-28**。做 go/no-go 决策前必须重新查；超过 30 天未验证必须重新验证。

## 1. Cross-region pod counts (prod)
## 1. 三区 pod 数量（prod）

| Service | US | EU | CN | US:EU:CN |
|---|---|---|---|---|
| iot-service-cloud | **124** | 33 | 8 | 15.5 : 4.1 : 1 |
| iot-service-cloud-gray | 3 | 3 | — | — |
| iot-consumer | 128 | 7 | 8 | 16 : 0.9 : 1 (CN ≈ EU here!) |
| state-machine | 18 | 2 | 5 | 3.6 : 0.4 : 1 (CN > EU pods!) |
| a4x-log-report | 9 | 4 | 3 | 3 : 1.3 : 1 |
| kiss | 29 | 8 | 3 | 9.7 : 2.7 : 1 |
| kiss-safertc | 2 | 2 | — | — |

**Read this**: pod ratios are NOT linear with traffic ratios. CN has more state-machine pods than EU because CN has more device-state churn per pod, NOT because CN has more devices.
**读法**：pod 比例不是流量比例的线性映射。CN 比 EU 有更多 state-machine pod 是因为 CN 设备状态变更更密，而不是因为 CN 设备更多。

## 2. iot-service-cloud — load anchors (prod)
## 2. iot-service-cloud — 流量锚点（prod）

| Signal | US | EU | CN | US:CN ratio |
|---|---|---|---|---|
| Total HTTP request rate (5min) | **52,378 req/s** | 6,848 | 1,028 | 51× |
| `/deviceMsg/setting` QPS (5min) | **3,295** (7d peak 5,588) | 363 | 14 | 235× ⚠️ |
| `/deviceMsg/setting` P99 (5min) | 99 ms (7d peak **9,164 ms** ⚠️) | 99 (7d peak 16,471) | 924 | — |
| `/deviceMsg/status` QPS | 6,935 | — | — | — |
| `/video/sliceReport` QPS (largest) | 10,150 | — | — | — |
| Total SQL QPS (5min) | **123,578 ops/s** | 15,048 | 1,854 | 67× |
| Top SQL `DeviceWhiteListDAO.queryBySn` | 15,545 ops/s | — | — | (each SetParam call may hit it) |
| MQTT consume rate | 13,342 /s | 1,777 | 177 | 75× |
| `update_user_config_end_count` (App→device setting writes) | 0.6 /s | 0.1 | 0 | (very rare) |
| `send_mqtt_count` (outbound) | 4.3 /s | 0.5 | 0.9 | (low — mostly MQTT-retained ops) |
| `device_wakeup_count` | 10.2 /s | 0.95 | 1.56 | — |
| `bind_from_camera_count` | 0.05 /s | 0 | 0 | (rare) |
| `log_level_count{loglevel=ERROR}` | 0.14 /s | — | — | — |
| `log_level_count{loglevel=WARN}` | 2.63 /s | — | — | — |
| HikariCP active per pod (current) | 2-3 | 0.4 | 0.25 | — |
| HikariCP **7d peak active per pod** ⚠️ | **50 = pool_max!** (saturated 2x in 7d) | 65/100 | 34/100 | (US has zero headroom) |
| HikariCP `pool_max` per pod | 50 | 100 | 100 | (US smaller pool, US still saturates first) |
| HikariCP **7d max pending sum** ⚠️ | 534 | 210 | 0 | (US has had threads queued for connections) |
| Average heap used per pod | 1,419 MB | 923 | 654 | — |

### Key implications / 关键含义
- **CN has anomalously low setting QPS** (235:1 ratio vs 51:1 for total HTTP). Either CN devices use a model with longer poll interval, or CN endpoint differs. Worth grepping CN-specific config when making changes near setting flow.
- **iot-service-cloud is read-heavy**: top 20 SQL methods are all SELECTs at 1,000-15,000 ops/s; top write methods are 1-95 ops/s. Adding 1 SELECT to a hot path is consistent with existing pattern; adding writes punches above weight.
- **US Hikari pool has zero headroom** (saturated twice in 7 days). Any new SELECT on a setting-build path must add a Caffeine-class cache to avoid raising pool pressure.

## 3. iot-consumer (Kafka consumer) — anchors (prod)
## 3. iot-consumer（Kafka 消费者）— 锚点（prod）

| Signal | US | EU | CN |
|---|---|---|---|
| `mqtt_consume_count` (per-pod across 128 pods) | **2,047 /s** | 188 | 545 ⚠️ (CN > EU) |

> CN's iot-consumer rate exceeding EU's is anomalous; possibly a different MQTT topology or a different message-emission rate per device. Don't assume CN < EU < US blindly.

## 4. kiss — anchors (prod, US-only Thanos data for now)
## 4. kiss — 锚点（prod，目前仅 US Thanos 数据）

| Signal | US | EU | CN |
|---|---|---|---|
| **Concurrent connected devices (TCP)** | **2,384,131** | 228,389 | 53,799 |
| Connection density per pod (avg) | 82,200 | 28,549 | 17,933 |
| `kiss_heartbeat_device_count` (5min rate, US) | 21,576 /s | — | — |
| `kiss_receive_tcp_udp_count` (5min rate, US) | 40,393 /s | — | — |
| `p2p_websocket_message_count` (5min rate, US) | 8.4 /s | — | — |

**Note**: Use `node_netstat_Tcp_CurrEstab{job="us-prod-kiss"}` as the "online device count" proxy. This is the cleanest way to get fleet size for capacity calc.
**用法**：用 `node_netstat_Tcp_CurrEstab{job="us-prod-kiss"}` 作为"在线设备数"代理。这是拿 fleet size 做容量计算最干净的方法。

## 5. state-machine — anchors (prod)
## 5. state-machine — 锚点（prod）

| Signal | US | EU | CN |
|---|---|---|---|
| `device_state_change_counter` total | 125 /s | 24.5 | 5.7 |
| `…{type=online}` | 61.8 /s | 12.0 | 2.8 |
| `…{type=offline}` | 63.7 /s | 12.6 | 2.9 |

**Read**: ~125 transitions/s in US ÷ 2.38M online devices = once per device per ~5h. That's a much slower frequency than `/deviceMsg/status` polling, suggesting state-machine only fires on real online/offline events, not on every poll.
**读法**：US 125 次/秒 ÷ 238 万在线设备 = 每设备每 5h 才一次。比 `/deviceMsg/status` 拉取频率低得多，说明 state-machine 只在真实在线/离线变更时触发，不是每次 poll 都触发。

## 6. a4x-log-report — anchors (prod)
## 6. a4x-log-report — 锚点（prod）

| Signal | US | EU | CN |
|---|---|---|---|
| HTTP rate | 2,130 req/s | 353 | 641 |

> CN > EU here — same pattern as iot-consumer. Possibly because EU has fewer log shippers per device (different deployment density).

## 7. Per-device call frequency (US prod, derived)
## 7. 设备级调用频率（US prod，推导）

Computed as `endpoint QPS ÷ kiss TCP CurrEstab`:
公式 `endpoint QPS ÷ kiss TCP CurrEstab`：

| Endpoint | Per-device interval |
|---|---|
| `/deviceMsg/setting` | **~12.4 min** (this is your C2 contract value for setting-related changes) |
| `/deviceMsg/config` | ~12.0 min |
| `/deviceMsg/connection` | ~7.7 min |
| `/deviceMsg/status` | ~5.7 min |
| `/deviceMsg/wakeup` | ~11.3 min |
| `/deviceMsg/dormancyStatus` | ~11.3 min |
| `/deviceMsg/detectPirResult` | ~16.3 min |
| `/deviceMsg/pir` | ~21.3 min |
| `/video/sliceReport` | ~3.9 min (most frequent device call) |

**Use these as developer-estimate cross-checks**: if a dev says "device polls every minute", these say no — the actual is 5-20 minutes per endpoint.
**用作开发者估计的交叉验证**：开发者说"设备每分钟轮询"，这些数据说不对——实际是每 5-20 分钟。

## 8. How to refresh / 如何刷新

```bash
# One-shot refresh of the most important anchors via the helper
python3 references/query-helpers.py anchors
```

Or run individual recipes from [recipes/](recipes/) and update this file in place.
或从 [recipes/](recipes/) 跑单条配方，原地更新本文。
