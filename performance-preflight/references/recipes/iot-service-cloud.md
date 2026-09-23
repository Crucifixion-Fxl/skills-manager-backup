# Recipe: iot-service-cloud
# 配方：iot-service-cloud

The biggest, most-frequently-changed Java service. Spring Boot 2.5.5 on **Undertow** (NOT Tomcat). Hosts most device-facing HTTP, MQTT consumer code, and a large business surface (binding, video, payment, AB, push, etc).
最大、改动最频繁的 Java 服务。Spring Boot 2.5.5，**Undertow** servlet 容器（不是 Tomcat）。承载大部分设备面 HTTP、MQTT 消费者代码、绝大部分业务面（绑定、视频、付费、AB、推送等）。

## 1. Code locations / 代码位置

| Concern | Path |
|---|---|
| Repo (local) | `/Users/zouyijiang/workspace/iot-release` (subtree contains both cloud and local; cloud is what we mean here) |
| Repo (GitLab) | `gitlab.addx.ai/CLOUD/iot-service-unified` |
| Custom Prometheus metrics — master registry (95+ metrics) | `iot-service-cloud/src/main/java/com/addx/iotcamera/util/PrometheusMetricsUtil.java` |
| Micrometer-style metrics (newer code) | grep `Counter.builder(` / `MeterRegistry` (e.g. `service/setting/SettingOverrideObservability.java`) |
| Logback (proves what's in MDC / ES) | `iot-service-cloud/src/main/resources/logback-spring.xml` |
| Log split / no-split helper | `iot-service-cloud/src/main/java/com/addx/iotcamera/util/LogUtil.java` (see `EXCLUDE_MDC_FIELDS = ["serialNumber","userId"]`, `doOnPrintLogNotSplit`) |
| Snowplow tracking | `iot-service-cloud/src/main/java/com/addx/iotcamera/snowplow/...` (mostly Alexa-Safemo; bind / video AI also emit events) |

## 2. Prometheus job labels / Prometheus job 标签

| Variant | Job label | Notes |
|---|---|---|
| Main HTTP service | `prod-{us,eu,cn}-iot-service` | 124/33/8 pods |
| Gray rollout | `prod-{us,eu}-iot-service-gray` | 3/3 pods (CN has no gray) |
| Kafka consumer | `prod-{us,eu,cn}-iot-consumer` | 128/7/8 pods (separate scrape) |

Use `job=~"prod-us-iot.*"` to combine main + gray. Use exact `job="prod-us-iot-service"` for main-only.
用 `job=~"prod-us-iot.*"` 同时覆盖主 + 灰度；用精确 `job="prod-us-iot-service"` 只看主。

## 3. Top recipes (US shown; swap `us`→`eu`/`cn` for other regions) / 标准查询

```promql
# Entry QPS for a specific URI (most common)
sum(rate(http_request_cost_time_histogram_count{job="prod-us-iot-service",uri="<URI>"}[5m]))

# QPS per pod — sanity-check load balancing
sum by (instance) (rate(http_request_cost_time_histogram_count{job="prod-us-iot-service",uri="<URI>"}[5m]))

# P99 latency at the entry
histogram_quantile(0.99, sum by (le)(rate(http_request_cost_time_histogram_bucket{job="prod-us-iot-service",uri="<URI>"}[5m])))

# 7-day P99 peak (use this to capture spikes, not the current value)
max_over_time(histogram_quantile(0.99, sum by (le)(rate(http_request_cost_time_histogram_bucket{job="prod-us-iot-service",uri="<URI>"}[5m])))[7d:5m])

# 7-day QPS peak
max_over_time(sum(rate(http_request_cost_time_histogram_count{job="prod-us-iot-service",uri="<URI>"}[5m]))[7d:5m])

# Body size by type — type=reqJsonSize|respJsonSize|reqProtoSize|respProtoSize
histogram_quantile(0.99, sum by (le)(rate(http_body_size_histogram_bucket{job="prod-us-iot-service",uri="<URI>",type="respJsonSize"}[5m])))

# WARN/ERROR rate, project-wide (NOT split by user)
sum by (loglevel)(rate(log_level_count_total{job="prod-us-iot-service",loglevel=~"WARN|ERROR"}[5m]))

# WARN/ERROR by class+method — usually narrow enough to find the source
sum by (className,methodName,loglevel)(rate(log_level_count_total{job="prod-us-iot-service",loglevel=~"WARN|ERROR"}[5m]))

# Top SQL methods — read-heavy fingerprint
topk(20, sum by (method)(rate(server_sql_duration_milliseconds_count{job="prod-us-iot-service"}[5m])))

# Total SQL QPS
sum(rate(server_sql_duration_milliseconds_count{job="prod-us-iot-service"}[5m]))

# SQL P99 by method
topk(10, histogram_quantile(0.99, sum by (le,method)(rate(server_sql_duration_milliseconds_bucket{job="prod-us-iot-service"}[5m]))))

# SQL fail rate
sum by (exception)(rate(server_sql_fail_count_total{job="prod-us-iot-service"}[5m]))

# HikariCP — CRITICAL for capacity (see L7 in historical-lessons.md)
sum(hikaricp_connections_active{job="prod-us-iot-service"})
max(hikaricp_connections_active{job="prod-us-iot-service"})  # peak per pod
avg(hikaricp_connections_max{job="prod-us-iot-service"})     # configured pool max per pod
sum(hikaricp_connections_pending{job="prod-us-iot-service"}) # threads queued for connections — non-zero = saturating
max_over_time(sum(hikaricp_connections_pending{job="prod-us-iot-service"})[7d:1m])  # 7d peak pending

# JVM threads by state (proxy for worker saturation since no Tomcat/Undertow metric)
sum by (state)(jvm_threads_states_threads{job="prod-us-iot-service"})

# JVM heap used per pod
avg(jvm_memory_used_bytes{job="prod-us-iot-service",area="heap"}) / 1024 / 1024  # MB
sum(jvm_memory_used_bytes{job="prod-us-iot-service",area="heap"}) * 100 / sum(jvm_memory_max_bytes{job="prod-us-iot-service",area="heap"})  # %

# GC pause rate + duration
sum by (action)(rate(jvm_gc_pause_seconds_count{job="prod-us-iot-service"}[5m]))
max by (action,cause)(jvm_gc_pause_seconds_max{job="prod-us-iot-service"})

# CPU
avg(process_cpu_usage{job="prod-us-iot-service"})    # process-level (0..1)
avg(system_cpu_usage{job="prod-us-iot-service"})     # node-level (0..1)
max(system_load_average_1m{job="prod-us-iot-service"})

# Pod count
count(up{job="prod-us-iot-service"} == 1)

# Outbound RPC traffic (top external hosts called)
topk(10, sum by (host)(rate(server_api_call_cost_milliseconds_count{job="prod-us-iot-service"}[5m])))

# Outbound RPC P99 by host
topk(10, histogram_quantile(0.99, sum by (le,host)(rate(server_api_call_cost_milliseconds_bucket{job="prod-us-iot-service"}[5m]))))
```

## 4. Highest-signal custom counters from `PrometheusMetricsUtil` / 最高信噪比的自定义 counter

Only the ones most useful for capacity / hot-path estimation:
仅列对容量评估 / 热路径估算最有用的：

| Counter | Labels | Use for |
|---|---|---|
| `server_sql_duration_milliseconds` (Histogram) | `instance,method` | DB QPS by method, slow-query detection |
| `server_sql_fail_count_total` | `instance,method,exception,msg` | DB error budget |
| `server_api_call_cost_milliseconds` (Histogram) | `instance,host,path` | Outbound RPC fan-out cost |
| `mqtt_consume_count_total` | `instance,msgSrc` | Inbound MQTT — device event entry |
| `mqtt_handle_count_total` | `instance,msgSrc,type` | MQTT handler invocations |
| `mqtt_handle_error_count_total` | `instance,msgSrc,type` | MQTT handler errors |
| `mqtt_handle_cost_milliseconds` (Histogram) | `instance,msgSrc,type,event` | MQTT handle latency |
| `send_mqtt_count_total` | `instance,type,retained,qos` | Outbound MQTT (retain ops, repush) |
| `update_user_config_end_count_total` | `instance,modelNo,updateStatus,reason` | App→device setting updates |
| `device_wakeup_count_total` | `instance,type` | Device wakeup events (`COUNT` vs `SYNC`) |
| `bind_from_camera_count_total` / `bind_mqtt_reponse_count_total` | `instance` | Bind funnel (use as ratio) |
| `repeat_bind_count_total` | `instance,type` | Bind retry / amplification |
| `device_pir_result_count_total` | `instance,modelNo,firmwareType,firmwareId,isValidTrigger` | PIR detection events |
| `video_msg_begin_count_total` / `video_msg_exe_count_total` | `instance,type[,result]` | Video pipeline funnel |
| `video_upload_complete_count_total` | `instance,serviceName,bucket,region,sliceUploadSuccessIsEmpty` | Video upload completion |
| `report_log_count_total` | `instance,reportType,reportGroup,reporter` | Cross-service event-tracking emission |
| `report_log_statistic_counter` (a4x-log-report only) | `instance,key` | wow_timeout, etc. |
| `push_msg_count_total` | `instance,tenantId,event,pushResult` | Push fan-out + result |
| `ios_push_count_total` / `ios_push_fail_count_total` / `ios_push_cost_milliseconds` | `instance,type[,exception]` | iOS APNS |
| `jwt_token_verify_total` | `instance,verify_result,token_source,key_hash` | JWT auth path |
| `async_task_queued_cost_time_histogram` / `async_task_exe_cost_time_histogram` | `instance,poolName` | Per-pool async queue + execution time (Little's Law inputs) |
| `device_task_queued_cost_time_histogram` / `device_task_exe_cost_time_histogram` | `instance,type` | Device-task pipeline |
| `setting_override_applied_total` (Micrometer) | `modelNo,experiment` | Setting-override hit rate |
| `setting_override_failed_total{stage}` (Micrometer) | `stage` ∈ {fetch,parse,merge,emit} | Setting-override fail by stage |
| `log_level_count_total` | `instance,loglevel,className,methodName` | Project-wide WARN/ERROR rate |
| `linode_bucket_size_gauge` / `linode_bucket_qps_remaining_gauge` | `instance,bucket` | Linode storage health |
| `xxl_job_check_count_total` / `xxl_job_check_fail_count_total` | `instance,type[,errorMsg]` | xxl-job triggers |

For the full list run:
完整列表跑：
```bash
python3 references/query-helpers.py metrics prod-us-iot-service
```

## 5. Logs in iot-service-cloud — total counts vs frequency proxies / 日志总量 vs 频率代理

`logback-spring.xml` JSON pattern emits MDC fields: `requestId, trace_id, span_id, userId, accountId, thirdUserId, serialNumber, serverId, traceId, modelNo, firmwareId, deviceMsgSrc`.

Downstream log shipping splits by `userId` and `serialNumber`. Consequence:
下游收日志按 `userId` / `serialNumber` 拆分，结果：

- **INFO/DEBUG total count is NOT a global frequency** — must scope to a user/SN. Use a Prometheus counter instead, or add one.
  **INFO/DEBUG 总条数不是全局频率** — 必须 scope 到 user/SN。用 Prometheus counter，或新加。
- **WARN/ERROR ARE project-wide queryable.** `LogUtil.warn/error(...)` calls `doOnPrintLogNotSplit` to remove `userId` + `serialNumber` from MDC during the log line build. Both also auto-bump `log_level_count_total{loglevel}` — **prefer the counter over an ES query for rate**.
  **WARN/ERROR 是全服务可查的。** `LogUtil.warn/error(...)` 调 `doOnPrintLogNotSplit` 在日志生成时摘掉 user/SN，同时自动给 `log_level_count_total{loglevel}` 加 1 —— **取速率优先用 counter，不要查 ES**。
- **Drilldown via troubleshooting platform** is for single-user/SN traces — see [../troubleshooting-bridge.md](../troubleshooting-bridge.md).

## 6. Servlet container / worker thread — IMPORTANT GAP / 重要差距

iot-service-cloud uses **Undertow**, not Tomcat. As of 2026-04-28 there is **no Undertow / worker-thread metric exposed**:
- ❌ `tomcat_threads_busy_threads` — empty
- ❌ `tomcat_threads_config_max_threads` — empty
- ❌ `undertow_*`, `xnio_*`, `executor_*`, `process_threads` — all empty

**Workarounds for worker-thread saturation detection** (see L8 in historical-lessons.md):
1. `jvm_threads_states_threads{state="runnable"}` — noisy proxy.
2. `hikaricp_connections_pending` — leading indicator (DB stall blocks worker first).
3. `histogram_quantile(0.99, http_request_cost_time_histogram_bucket{...})` jump — downstream observable.
4. `UT005023` log-line count via troubleshooting — direct evidence of upstream timeout.

**Long-term**: file an issue to expose Undertow XNIO worker-pool stats via Micrometer. This is the bird-story canary's underlying instrumentation gap.

## 7. Anchor magnitudes (snapshot 2026-04-28) / 锚点量级（2026-04-28 快照）

See [../magnitudes.md](../magnitudes.md) for the full table. Key US numbers:
完整表见 [../magnitudes.md](../magnitudes.md)。US 关键数：

- 124 pods · 52,378 total HTTP req/s · 123,578 total SQL ops/s · 13,342 MQTT consume/s
- `/deviceMsg/setting` 3,295 req/s (7d peak 5,588) · P99 99 ms (7d peak 9,164 ms!)
- HikariCP per-pod active: current 2-3 · 7d peak **50 = pool_max!** · 7d max pending **534**
- Avg heap per pod: 1,419 MB
