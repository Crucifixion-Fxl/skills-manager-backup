# Recipe: Fallback (services not in this directory)
# 配方：兜底（本目录未列出的服务）

This file kicks in when:
1. The service you're touching has no dedicated `recipes/<service>.md` here.
2. The service is a typical Java microservice (Spring Boot + Actuator + micrometer-registry-prometheus).
3. You need a fast path to: pod count, entry QPS, error rate, latency, basic JVM stats.

If true / 满足以上时:

## Step 1 — Discover the job label
## Step 1 — 发现 job 标签

```bash
# Replace <name> with the service name as you'd guess
python3 references/query-helpers.py thanos us "sum by (job)(up{job=~\".*<name>.*\"})"
```

Try also EU/CN if it's a regional service. **Do NOT assume `prod-{region}-X` form** — kiss-family + vernemq-family + public-* services use `{region}-prod-X` (Family B). See [../job-labels.md](../job-labels.md).
也试试 EU/CN。**不要默认 `prod-{region}-X` 形式** —— kiss / vernemq / public-* 走相反的 Family B。

## Step 2 — Confirm the service is alive and pod count
## Step 2 — 确认服务存活 + pod 数

```promql
count(up{job="<job>"} == 1)
```

If 0, service is down or doesn't exist. Re-check the job name.

## Step 3 — Default Spring Boot Actuator metrics (most likely available)
## Step 3 — Spring Boot Actuator 默认指标（多半有）

```promql
# Total HTTP entry QPS — Spring Boot Actuator default
sum(rate(http_server_requests_seconds_count{job="<job>"}[5m]))

# By URI (top 10)
topk(10, sum by (uri)(rate(http_server_requests_seconds_count{job="<job>"}[5m])))

# Error rate (5xx)
sum(rate(http_server_requests_seconds_count{job="<job>",status=~"5.."}[5m]))
  / sum(rate(http_server_requests_seconds_count{job="<job>"}[5m]))

# P99 by URI
topk(10, histogram_quantile(0.99, sum by (le, uri)(rate(http_server_requests_seconds_bucket{job="<job>"}[5m]))))

# JVM heap %
sum(jvm_memory_used_bytes{job="<job>",area="heap"}) * 100
  / sum(jvm_memory_max_bytes{job="<job>",area="heap"})

# JVM threads by state
sum by (state)(jvm_threads_states_threads{job="<job>"})

# GC pause rate
sum(rate(jvm_gc_pause_seconds_count{job="<job>"}[5m]))

# Process CPU
avg(process_cpu_usage{job="<job>"})
```

## Step 4 — Find custom metrics by name probe
## Step 4 — 通过名字探测自定义指标

```bash
python3 references/query-helpers.py metrics <job>
```

This dumps all `__name__` series for the job. Scan for business counters (look for `_count`, `_total`, `_count_total`, `_seconds_count`).
列出该 job 的所有 metric 名字。扫一眼有没有业务 counter。

## Step 5 — Find custom metrics by source code grep
## Step 5 — 通过源码 grep 找自定义指标

If the service repo isn't local, GitLab API:
仓不在本地用 GitLab API：

```bash
# Find Counter.build / meterRegistry.counter / Tracker.track sites
curl -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/search?scope=blobs&search=Counter.build"

# Or via the web search:
# https://gitlab.addx.ai/CLOUD/<service>/-/search?search=Counter.build&scope=blobs
```

Read the registration site (file + line) — that gives metric name + label set.
读注册点（文件+行号）—— 得到 counter 名 + label 集合。

## Step 6 — Logs, if no counter exists
## Step 6 — 没有 counter 时用日志

If the service's logs are non-split (most non-iot-service-cloud Java services), troubleshooting platform's `/log-search/query-index` with the service's index pattern works.
如果服务日志不拆分（大多数非 iot-service-cloud 的 Java 服务），troubleshooting 平台的 `/log-search/query-index` + 该服务索引模式可用。

`GET /api/v1/log-search/index-patterns` lists all indices. See the `troubleshooting` skill for full flow.
`GET /api/v1/log-search/index-patterns` 列出所有索引。完整流程见 `troubleshooting` skill。

## Step 7 — Promote the service to a dedicated recipe file
## Step 7 — 把该服务升级为独立 recipe 文件

If you find yourself running this fallback flow more than once for the same service, **write a `recipes/<service>.md` file** in the same shape as kiss.md / state-machine.md and add an entry to [../magnitudes.md](../magnitudes.md). That converts a 5-step discovery into a 1-step lookup.
对同一服务跑过 2 次本流程，**把它升级为独立 `recipes/<service>.md`**，按 kiss.md / state-machine.md 同样骨架写，并在 [../magnitudes.md](../magnitudes.md) 加一行。下次就是一步查到。

## Common gotchas / 常见坑

1. **Service exposes metrics at non-standard port** — check `up{job="<job>"} == 0` to verify scraping; if scraping is off the metrics simply don't exist.
2. **Service uses Micrometer with non-default registry** — metrics may be under different names (`http_server_requests_seconds_count` vs `http.server.requests.seconds.count` vs custom). Use `count by (__name__)({job="<job>"})` to confirm.
3. **Service is Go / Node / Python, not Java** — no JVM metrics; check Go runtime metrics (`go_*`), Node Prometheus client (`process_*`), or whatever the language's library exposes. Use the metric-name probe.
4. **Metric exists but empty** — possibly low traffic; try `count_over_time(<metric>[7d:1m])` to confirm any data point exists in the last week.
