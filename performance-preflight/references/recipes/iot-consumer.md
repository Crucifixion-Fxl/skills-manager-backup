# Recipe: iot-consumer
# 配方：iot-consumer

Kafka consumer side of iot-service-cloud — consumes MQTT-bridged messages from devices. Same codebase as iot-service-cloud (subtree), deployed as a separate K8s deployment with the consumer profile.
iot-service-cloud 的 Kafka 消费者侧 —— 消费来自设备的 MQTT 桥接消息。与 iot-service-cloud 同一代码库（subtree），以独立 K8s 部署 + consumer profile 上线。

## 1. Code locations / 代码位置

Same repo as iot-service-cloud (`/Users/zouyijiang/workspace/iot-release`). Consumer-specific code:
- `iot-service-cloud/src/main/java/com/addx/iotcamera/kafka/...` — `@KafkaListener` annotations
- `iot-service-cloud/src/main/java/com/addx/iotcamera/mqtt/...` — MQTT consumer adapter

## 2. Prometheus job labels / job 标签

| Region | Job | Pods (2026-04-28) |
|---|---|---|
| US | `prod-us-iot-consumer` | **128** ⚠️ (more than main service's 124!) |
| EU | `prod-eu-iot-consumer` | 7 |
| CN | `prod-cn-iot-consumer` | 8 |

**Why does CN have more consumer pods than EU?** The MQTT topic distribution differs by region. Don't assume linear ratio.
**为什么 CN consumer pod 比 EU 多？** 各区 MQTT topic 分布不一样，**不要**默认线性比例。

## 3. Top recipes / 标准查询

```promql
# MQTT consume rate (the entry point for this service)
sum(rate(mqtt_consume_count_total{job="prod-us-iot-consumer"}[5m]))

# By message source — many consumer code paths branch on msgSrc
sum by (msgSrc)(rate(mqtt_consume_count_total{job="prod-us-iot-consumer",msgSrc!=""}[5m]))

# Handle rate vs error rate
sum(rate(mqtt_handle_count_total{job="prod-us-iot-consumer"}[5m]))
sum(rate(mqtt_handle_error_count_total{job="prod-us-iot-consumer"}[5m]))

# Handle latency by event type
histogram_quantile(0.99, sum by (le,event)(rate(mqtt_handle_cost_milliseconds_bucket{job="prod-us-iot-consumer"}[5m])))

# Consumer-side SQL (most consumer methods do DB writes)
topk(20, sum by (method)(rate(server_sql_duration_milliseconds_count{job="prod-us-iot-consumer"}[5m])))

# Lag (Kafka consumer group lag — actual offset lag, NOT a metric exposed by the app)
# Use this from Kafka exporter: kafka_consumergroup_lag (DATA team Prometheus)
```

## 4. Anchor magnitudes (US prod 2026-04-28) / 锚点量级

| Signal | US | EU | CN |
|---|---|---|---|
| Pods | 128 | 7 | 8 |
| `mqtt_consume_count` rate | 2,047 /s | 188 | 545 |

**Anomaly**: CN > EU here. Likely a consumer-group rebalancing or topic-partition allocation difference. Worth investigating but not blocking.
**异常**：CN > EU。可能是 consumer-group rebalance 或 topic partition 分配差异。值得查但不阻塞。

## 5. When iot-consumer matters in a perf-preflight assessment / 何时关心 iot-consumer

- The change touches `@KafkaListener` / `@RabbitListener` / MQTT consumer handler classes.
- The change adds DB writes that fire on incoming events (vs on user request).
- The change might cause Kafka consumer lag — visible at `kafka_consumergroup_lag{consumergroup=~"public-gray|public|...iot..."}`.

The 2026-04-23 bird-story incident manifested partially as consumer lag spike on `public-gray` consumer group — pod restarts → rebalance → backlog accumulation.
2026-04-23 bird-story 事故的一个表现就是 `public-gray` consumer group lag 飙升 —— pod 重启 → rebalance → 积压。
