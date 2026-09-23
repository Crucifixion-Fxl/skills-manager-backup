# Prometheus Job Labels — Naming Conventions
# Prometheus Job 标签 — 命名约定

**Two prefix orderings co-exist in this org**, and the wrong one returns empty silently. This file is the canonical map.
**本组织内两种前缀顺序共存**，用错了会静默返回空。本文是权威映射。

## 1. Service-to-job-label table (prod, all regions)
## 1. 服务到 job 标签的映射（prod，全部区域）

| Service | US | EU | CN | Naming family |
|---|---|---|---|---|
| iot-service-cloud (HTTP) | `prod-us-iot-service` | `prod-eu-iot-service` | `prod-cn-iot-service` | A |
| iot-service-cloud (gray) | `prod-us-iot-service-gray` | `prod-eu-iot-service-gray` | (n/a) | A |
| iot-consumer (Kafka consumer) | `prod-us-iot-consumer` | `prod-eu-iot-consumer` | `prod-cn-iot-consumer` | A |
| state-machine | `prod-us-statemachine` | `prod-eu-statemachine` | `prod-cn-statemachine` | A |
| a4x-log-report | `prod-us-a4x-log-report` | `prod-eu-a4x-log-report` | `prod-cn-a4x-log-report` | A |
| kiss (signaling/keepalive) | `us-prod-kiss` | `eu-prod-kiss` | `cn-prod-kiss` | **B (inverted)** |
| kiss (JVM-only metrics) | `us-prod-kiss-jvm` | `eu-prod-kiss-jvm` | `cn-prod-kiss-jvm` | B |
| kiss (port scrape) | `us-prod-kiss-port` | `eu-prod-kiss-port` | `cn-prod-kiss-port` | B |
| kiss-safertc (live-stream signaling) | `us-prod-kiss-safertc` | `eu-prod-kiss-safertc` | (n/a) | B |
| vernemq (MQTT broker) | `us-prod-vernemq` | (TBD) | (TBD) | B |
| vernemq HAProxy | `us-prod-vernemq-haproxy` | (TBD) | (TBD) | B |
| zendesk-webhook (public-region) | `us-public-zendesk-webhook` | `eu-public-zendesk-webhook` | `cn-public-zendesk-webhook` | B |

**Family A (`prod-{region}-<service>`)** — Used by: iot-* / state-machine / a4x-log-report — **most application services**.
**Family B (`{region}-prod-<service>`)** — Used by: kiss-family / vernemq / public-* services — **infrastructure-leaning components**.

There is no rule that predicts which family a new service uses. Always probe first:
没有规则能预测新服务用哪个 family。**先探测**：

```promql
# Discover the actual job label for any service name
sum by (job)(up{job=~".*<servicename>.*"})
```

## 2. Non-prod environments
## 2. 非 prod 环境

The same family pattern, with `prod` replaced by the env name. Examples:
模式不变，把 `prod` 替换为环境名：

| Env | Family A example | Family B example |
|---|---|---|
| pre | `pre-us-iot-service` | `us-pre-kiss` |
| staging | `staging-us-iot-service` (US has no staging in Thanos US — staging EU/CN only) | `us-staging-kiss` |
| test | `test-cn-iot-service` (CN region has a `test` env) | `cn-test-kiss` |

### Region with multiple non-prod envs (CN)
CN has `prod / staging / test / pre` simultaneously — confirm which one the dev means before querying.
CN 同时有 `prod / staging / test / pre` 四套，查询前先确认开发者说的是哪一套。

## 3. Special label conventions inside a job
## 3. Job 内部的 label 约定

Different scrape jobs use different label names for "which pod":
不同 scrape job 用不同的 label 表示"哪个 pod"：

| Service family | Pod label name |
|---|---|
| iot-service-cloud (Prometheus client `simpleclient`) | **`instance`** (= pod hostname, e.g. `iot-service-54d6fb6c85-25bnn`) |
| Spring Boot Actuator default (Micrometer) | `pod` (cAdvisor / kube-state-metrics) — when joining via `kube_pod_info` |
| kube-state-metrics views | `namespace`, `pod`, `container` |
| node-exporter (kiss has it) | `instance` (= node IP:port), `nodename` |

> When joining (e.g. tomcat threads vs hikari connections), check first which pod-label form each side uses; mixing `instance` and `pod` in one query yields zero matches.
> 联合查询（比如 tomcat 线程 vs hikari 连接）时先确认两边用什么 pod label；同一查询里混用 `instance` 和 `pod` 会返回零行。

## 4. Project Label (deprecated; some old metrics still carry it)
## 4. project label（已弃用；老指标里还存在）

Older `simpleclient` metrics in iot-service-cloud were registered with `project="iot-service-cloud"`. New code does not — don't filter on it unless the metric is known to have it. Quick check:
iot-service-cloud 的老 simpleclient 指标曾以 `project="iot-service-cloud"` 注册；新代码不再使用。**不要**默认按它过滤；用前先确认指标真有这个 label：

```promql
count by (project)(<metric_name>)
```
