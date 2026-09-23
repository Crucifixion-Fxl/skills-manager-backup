# Recipe: state-machine
# 配方：state-machine

Standalone service. Owns device online/offline state aggregation. Receives MQTT messages and produces `device_state_change` events to Kafka.
独立服务，负责设备在线/离线状态聚合。消费 MQTT 消息，产出 `device_state_change` 事件到 Kafka。

## 1. Code locations / 代码位置

| Concern | Path |
|---|---|
| Repo | `gitlab.addx.ai/CLOUD/state-machine` (not always checked out locally; clone if needed) |
| Custom metrics | `src/main/java/com/addx/statemachine/config/PrometheusConfiguration.java`, `service/impl/StateMachineServiceNewImpl.java` |
| Device-state-change Kafka log helper | `src/main/java/com/addx/statemachine/kafka/DeviceStateChangeLogHelper.java` |

## 2. Prometheus job labels / job 标签

| Region | Job |
|---|---|
| US | `prod-us-statemachine` (18 pods) |
| EU | `prod-eu-statemachine` (2 pods) |
| CN | `prod-cn-statemachine` (5 pods) |

Family A naming (`prod-{region}-X`).

## 3. Top recipes / 标准查询

```promql
# Total device state transitions (online + offline)
sum(rate(device_state_change_counter{job="prod-us-statemachine"}[5m]))

# By type (online vs offline)
sum by (type)(rate(device_state_change_counter{job="prod-us-statemachine"}[5m]))

# By device model — find which models churn the most
topk(10, sum by (modelNo)(rate(device_state_change_counter{job="prod-us-statemachine"}[5m])))

# By type x modelNo
sum by (type, modelNo)(rate(device_state_change_counter{job="prod-us-statemachine"}[5m]))
```

## 4. Anchor magnitudes (snapshot 2026-04-28) / 锚点量级

| Signal | US | EU | CN |
|---|---|---|---|
| Pods | 18 | 2 | 5 |
| Total transitions/s | 125 | 24.5 | 5.7 |
| online type/s | 61.8 | 12.0 | 2.8 |
| offline type/s | 63.7 | 12.6 | 2.9 |

**Useful ratio**: US 125 transitions/s ÷ 2.38M online devices = once per device per **~5h** — much slower than `/deviceMsg/status` polling (5.7 min). So state-machine fires only on **real** online/offline edges, not on every keepalive.
**有用比例**：US 125 次/秒 ÷ 238万在线 ≈ 每设备每 5h 一次，比 `/deviceMsg/status` 拉取频率（5.7 min）慢得多。所以 state-machine 只在**真实**在线/离线变更触发，不是每个保活都触发。

## 5. When you need state-machine signals in a perf-preflight assessment / 何时需要 state-machine 信号

- Any code change that fires "on device online" event (push, fan-out, sync).
- Any feature whose load grows with **device churn**, not steady-state device count.
- Cross-validating "is this code path hit?" — if the change is driven by online events, the bound is `device_state_change_counter`, not `/deviceMsg/setting`.

## 6. Logs / 日志

state-machine logs are **not split** by user/SN (similar to kiss). Total log volume IS a frequency proxy. Index pattern in troubleshooting platform: `statemachine-*`.

## 7. Other state-machine related dashboards / 相关 dashboard

- `服务监控-statemachine-metrics` (uid `p6CseQSnk`) — service health
- `服务监控-statemachine-状态统计` (uid `8YnCOSInz`) — state transition stats
