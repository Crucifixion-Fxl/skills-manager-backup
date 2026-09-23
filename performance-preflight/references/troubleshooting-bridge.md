# Troubleshooting Skill Bridge — When It Helps for Stats
# Troubleshooting Skill 桥 — 何时用它做统计

The `troubleshooting` skill targets **single-user / single-SN drilldown**, not statistical aggregation. But there are a few cases where it answers questions Prometheus can't.
`troubleshooting` skill 主要是 **单用户 / 单 SN 钻取**，不是统计聚合。但少数情况下它能回答 Prometheus 答不了的问题。

## 1. When troubleshooting **does** help for stats / 何时它**能**给统计

### A. ES log line frequency in services with non-split logs (kiss, statemachine, etc.)
### A. 不拆分日志的服务的 ES 行频率（kiss / statemachine 等）

Use `POST /api/v1/log-search/query-index` with a kibana-like filter, count results over a time window. Useful when:
- The metric you'd want doesn't exist as a counter.
- The service's logs aren't split by user/SN, so total count IS a global frequency.

```bash
# Pseudo-flow via troubleshooting skill:
# 1. List available indices: GET /api/v1/log-search/index-patterns
# 2. Pick the kiss-* or statemachine-* index for your env
# 3. POST /api/v1/log-search/query-index with filter `message:"specific-line-pattern"`
#    + time range
# 4. Look at total hits / time window = rate
```

### B. Cross-reference (which user owns which SN) for capacity-percentile estimation
### B. Cross-reference 关联（哪个用户拥有哪个 SN）用于容量百分位估算

If you need to know "the 99th percentile user has N devices" but Superset's user-device-binding fact table is too coarse:

`POST /api/v1/cross-reference/query` with a known SN list returns the owning user_ids. Sample 1000 SNs, compute device-counts per user from the result. Useful for ad-hoc P99 estimation without a Superset query.
`POST /api/v1/cross-reference/query` 给定 SN 列表，返回所有者的 user_id。采样 1000 个 SN 算每用户设备数，可以临时算 P99。

### C. Confirming a single-user / single-SN report from a customer
### C. 确认客户报来的单用户/单 SN 现象

When ops says "tenant X says feature is broken", the troubleshooting platform's iot-service log-load-then-query flow (Prod env) finds the specific request log lines. Useful for:
- Verifying that the **slow path** described in your perf assessment really shows up in production logs.
- Cross-validating that a contract violation has occurred (e.g. one user with 100+ devices triggering a fan-out you didn't anticipate).

## 2. When troubleshooting **does NOT** help for stats / 何时它**不**适合做统计

### A. iot-service-cloud INFO/DEBUG line frequency
### A. iot-service-cloud 的 INFO/DEBUG 行频率

iot-service-cloud splits logs by `userId` + `serialNumber`. Asking troubleshooting for INFO line frequency means you'll either:
- See zero (because the line ends up in a per-user index you didn't load).
- See per-user volume only (without aggregating you can't get global rate).

Use `log_level_count_total` PromQL counter for WARN/ERROR rate; for INFO, **add a counter at the line site instead of querying logs.**
取 WARN/ERROR 速率用 `log_level_count_total` PromQL；INFO 频率应**在打点处加 counter**而不是查日志。

### B. Endpoints / paths visited by all users
### B. 全用户访问的 endpoint / 路径

That's what `http_request_cost_time_histogram_count{uri=...}` is for. Don't use ES for it — Prometheus is faster, more accurate, and not partitioned by user.

### C. Cross-region / multi-tenant aggregations
### C. 跨区 / 多租户聚合

Use Superset / data warehouse — see [data-warehouse.md](data-warehouse.md). Troubleshooting platform queries are scoped to a single environment and can't aggregate across regions.

## 3. The two flows you'll actually run / 你实际会用到的两个流

### Flow X — "Verify a slow request happened in prod" (prod-only)
### Flow X — "确认一个慢请求确实在 prod 发生过"（仅 prod）

```text
1. troubleshooting skill: POST /api/v1/es-data/status — see if data already loaded
2. If not: POST /api/v1/es-data/load-data with sn or user_id (5-10 min for backend logs)
3. POST /api/v1/log-search/query/es with filter on uri + cost_ms > 5000
4. Read the slow request's log lines + traceId
```

### Flow Y — "Get business state for a specific user/SN" (any env)
### Flow Y — "拿某个用户/SN 的业务态"（任意环境）

```text
1. troubleshooting skill: POST /api/v1/log-search/query/db with db_queries={"user_device_binding":["all"],"user_info":["all"]}
2. Filter result blocks (factoryInfoDOS, bindInfoDOS, vipStatusResults, etc.) for what you need
```

See the troubleshooting skill itself for Swagger + auth details. Don't try to call its endpoints without the skill — auth + de-tokenization rules are non-trivial.
完整 Swagger + 认证细节见 troubleshooting skill 本身。**不要**绕过 skill 直接调它的端点 —— 认证 + 脱敏 ID 规则不平凡。
