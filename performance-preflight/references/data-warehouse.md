# Data Warehouse — Superset + DataHub
# 数仓 — Superset + DataHub

Use this when Prometheus only gives "QPS at the edge" and you need answers like:
当 Prometheus 只能给"边缘 QPS"，你需要的答案是下面这种：

- "How many active devices per region in the last 24h?" / "近 24h 各区活跃设备数"
- "P50/P99/P999 device-count per user (for fan-out)" / "单用户绑设备数分布（fan-out 估算）"
- "How many Snowplow events of type X per day?" / "X 类型 Snowplow 事件/天"
- "Cohort-level conversion of experiment X" / "实验 X 的队列级转化"

## 1. Endpoints / 端点

| Tool | URL | Auth |
|---|---|---|
| Superset | `https://superset.addx.live` | Use the `superset` skill (handles SSO browser flow). |
| DataHub | `https://datahub.addx.live` | Use the `datahub` skill. |

## 2. Workflow / 流程

### Step A. Discover the table you need (DataHub)
### A. 发现需要的表（DataHub）

DataHub catalogs every fact / dim table with column descriptions and lineage. **Always start here** — guessing column names against Superset is expensive (each `EXPLAIN` failure roundtrip is several seconds).
DataHub 给每个事实/维度表的列描述 + 血缘。**永远从这里开始** —— 在 Superset 上瞎猜列名很贵（每次 EXPLAIN 失败几秒）。

```bash
# Use the datahub skill, which wraps the GraphQL search:
# Look for tables with "device", "user", "binding"
```

### Step B. Run the query (Superset SQL Lab)
### B. 跑 query（Superset SQL Lab）

The `superset` skill talks to SQL Lab. Make sure to:
通过 `superset` skill 跑。注意：
- **Always include a `LIMIT`** — Superset enforces it.
- **Always filter on date partition** — fact tables are huge; without `WHERE event_date >= ...` you'll OOM the executor.
- **Use approx_percentile**, not `PERCENTILE_CONT` — way faster on Trino/Presto.

## 3. Common templates / 常用模板

```sql
-- A. Active devices per region in the last 24h
SELECT region, COUNT(DISTINCT serial_number) AS active_devices
FROM <fact_device_event_table>
WHERE event_date >= date_format(now() - INTERVAL '1' DAY, '%Y-%m-%d')
GROUP BY region
ORDER BY active_devices DESC
LIMIT 10;

-- B. Distribution of devices per user (fan-out P50/P99/P999)
SELECT
  approx_percentile(device_count, 0.50)  AS p50,
  approx_percentile(device_count, 0.95)  AS p95,
  approx_percentile(device_count, 0.99)  AS p99,
  approx_percentile(device_count, 0.999) AS p999,
  MAX(device_count)                      AS max_observed,
  COUNT(*)                               AS total_users
FROM (
  SELECT user_id, COUNT(DISTINCT serial_number) AS device_count
  FROM <fact_user_device_binding>
  WHERE bind_status = 'active'
    AND event_date >= date_format(now() - INTERVAL '1' DAY, '%Y-%m-%d')
  GROUP BY user_id
)
LIMIT 1;

-- C. Snowplow event volume per day, by event type, last 7 days
SELECT
  event_date,
  event_name,
  COUNT(*) AS n_events
FROM <atomic_events_table>
WHERE event_date >= date_format(now() - INTERVAL '7' DAY, '%Y-%m-%d')
GROUP BY 1, 2
ORDER BY 1 DESC, 3 DESC
LIMIT 200;

-- D. Per-experiment event volume (e.g. setting_override_applied)
SELECT
  experiment,
  modelNo,
  COUNT(*) AS n
FROM <atomic_events_table>
WHERE event_date >= date_format(now() - INTERVAL '1' DAY, '%Y-%m-%d')
  AND event_name = 'setting_override_applied'
GROUP BY 1, 2
ORDER BY 3 DESC
LIMIT 50;

-- E. Daily Active Users / Devices
SELECT
  event_date,
  COUNT(DISTINCT user_id) AS dau,
  COUNT(DISTINCT serial_number) AS dad  -- daily active devices
FROM <fact_event>
WHERE event_date BETWEEN
        date_format(now() - INTERVAL '14' DAY, '%Y-%m-%d')
    AND date_format(now() - INTERVAL '1' DAY,  '%Y-%m-%d')
GROUP BY 1
ORDER BY 1 DESC
LIMIT 100;
```

## 4. Snowplow events of interest (point-in-time list)
## 4. 关心的 Snowplow 事件（点位清单）

iot-service-cloud emits Snowplow events from these sites (grep `SelfDescribingJson|SelfDescribingEvent`):
- `snowplow/alexa/AlexaSafemoSnowPlowTracker.java` — Alexa Safemo events (rich)
- `service/BindService.java` — device-bind lifecycle
- `helper/MsgHelper.java` — push message events
- `service/video/BirdStoryEligibilityResolver.java` — bird-story AB eval
- `service/video/SafemoAiNotificationService.java` — Safemo AI notification
- `service/user/ZendeskService.java` — Zendesk ticket lifecycle
- `service/deviceplatform/alexa/safemo/...` — multiple Safemo flow events

**There is NO general HTTP-level Snowplow tracker.** Each business node is wired individually. So Snowplow event-volume in Superset reflects business-node activity, not raw HTTP.
**没有通用的 HTTP 级 Snowplow 埋点**。每个业务节点单独接入。Superset 里的 Snowplow 事件量是业务节点活动，不是裸 HTTP。

## 5. Cross-validation between Prometheus and warehouse
## 5. Prometheus 与数仓的交叉验证

Common sanity checks:
- Counter `setting_override_applied_total` rate (Prometheus) × seconds-in-window ≈ Snowplow event count for `setting_override_applied` (Superset). If off by >2x, instrumentation drift.
  Counter rate × 时间窗 ≈ Snowplow 事件总数。差 > 2× 就是埋点漂移。
- DAU from `<fact_event>` (Superset) ÷ kiss `node_netstat_Tcp_CurrEstab` average (Thanos) ≈ "what fraction of online devices are doing user-driven actions".
  DAU 与 在线设备数 之比 = 用户主动操作的占比。
- Top-URI QPS in iot-service-cloud (Prometheus) × 86400 ≈ daily call count for that URI (cross-check against any per-URI Snowplow event).

## 6. Common gotchas / 常见坑

1. **Date partition must be in the WHERE** — otherwise the table scans all of history.
2. **`event_date` vs `dt` vs `partition_date`** — confirm with DataHub.
3. **Some fact tables have a 1-day delay** — yesterday's data may not be ready until 04:00 UTC. Cross-check with Dagster / Airflow Pipeline for materialization time.
4. **PII** — every result you get may contain user_id / email / SN. Don't externalize.
