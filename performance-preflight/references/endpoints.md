# Endpoints & Domains — All Regions
# 端点与域名 — 全区域

This is the single source of truth for **where to query**. Re-probe only when a domain in this file stops resolving; do **not** ad-hoc try `thanos-prod-{region}` permutations on every session.
本文是**查询地址**的事实来源。本文中域名解析失败时再重新探测；**不要**每次会话都现猜 `thanos-prod-{region}` 的排列组合。

## 1. Thanos (long-term, cross-service per region)
## 1. Thanos（每区域长期 + 跨服务）

**HTTP only (no HTTPS), no auth required.** Each Thanos endpoint scrapes only its own region — there is no global Thanos.
**仅 HTTP（不是 HTTPS），无需认证。** 每个 Thanos 端点只抓自己区域，没有全局 Thanos。

| Region | URL | Job count (2026-04-28) | Naming convention |
|---|---|---|---|
| US | `http://thanos-prod-us.addx.live` | 250 | `prod-us-*` (iot, statemachine, a4x) and `us-prod-*` (kiss, vernemq) |
| EU | `http://thanos-prod-eu.addx.live` | 91 | same |
| CN | `http://thanos-cn.addx.live` ⚠️ no `prod-` prefix | 143 | `prod-cn-*` (iot, statemachine) and `cn-prod-*` (kiss) |

**Verified empty / not deployed**:
- `thanos-prod-cn.addx.live` (use `thanos-cn.addx.live` instead)
- `thanos-prod-sg.addx.live` (no SG region as of 2026-04-28)
- `thanos-staging-us.addx.live` / `thanos-pre-us.addx.live` (staging/pre is reachable through region Thanos, not separate)

**API path**: `POST /api/v1/query` with form-encoded `query=<PromQL>`, or `POST /api/v1/query_range` for range queries.
See [query-helpers.py](query-helpers.py) for a 5-line client.

## 2. DATA-team Prometheus (separate from IoT)
## 2. DATA team 的 Prometheus（与 IoT 独立）

| URL | Coverage |
|---|---|
| `http://prometheus-us-data.addx.live` (HTTP only) | DATA team infra: Kafka brokers, EKS nodes, Karpenter, Cluster Autoscaler. **NOT iot-service-cloud, NOT kiss.** |

Use only when investigating data-pipeline (Kafka brokers, fluentbit, etc.) or DATA team's own EKS nodes.
仅在排查数据管道（Kafka broker、fluentbit 等）或 DATA 团队自有的 EKS 节点时用。

## 3. Grafana (per-region datasources behind one URL)
## 3. Grafana（一个 URL 下挂多区域 datasource）

| Setting | Value |
|---|---|
| URL | `https://grafana.addx.live` |
| Auth | `Authorization: Bearer $GRAFANA_TOKEN` (env var, set globally) |
| Version | 8.3.3 (Legacy Alerting) |

### 3.1 Datasource UIDs (NOT discoverable via `/api/datasources` — that endpoint is admin-only)
### 3.1 Datasource UID（`/api/datasources` 是 admin 端点，会拒绝；本文是事实来源）

| Region | Datasource name | UID | Region's job-label prefix |
|---|---|---|---|
| US | `Prometheus-us` | `000000016` | `prod-us-*` (iot family) / `us-prod-*` (kiss) |
| EU | `Prometheus-eu` | `000000036` | `prod-eu-*` / `eu-prod-*` |
| CN | `Prometheus-cn` | `000000003` | `prod-cn-*` / `cn-prod-*` |

**Query via `POST /api/ds/query`** (the modern unified endpoint — does NOT require admin):
通过 `POST /api/ds/query`（新版统一端点，**不**需要 admin）：

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" -H "Content-Type: application/json" \
  -X POST "https://grafana.addx.live/api/ds/query" \
  -d '{"queries":[{"refId":"A","datasource":{"type":"prometheus","uid":"000000016"},
       "expr":"<PROMQL>","instant":true}],
      "from":"now-10m","to":"now"}'
```

### 3.2 Useful dashboard UIDs (lookup-only, do NOT modify)
### 3.2 常用 Dashboard UID（仅查阅，不要修改）

| Dashboard | UID | When to look |
|---|---|---|
| **业务监控-后端** ⭐ team's primary ops panel | `UWulzimMk` | Cross-region binding/wakeup/MQTT/log/OTA funnel — the team's first-stop dashboard |
| **服务监控-JVM** ⭐ generic JVM health (parametric `$job`/`$instance`) | `_ZirS94Mz` | Heap/GC/threads/CPU on any single Java service, drill-down into one pod |
| 服务监控-iot-service-httprequestmetrics | `6u77nqInk` | iot-service HTTP latencies by region |
| 服务监控-iot-service-mybatismetrics | `z-6ZaQInz` | iot-service SQL P99 + fail rate by method |
| 服务监控-iot-service-keyfunctionsmetrics | `hKA_jNH7k` | iot-service key business function latency |
| 服务监控-iot-service-apimetrics | `oWQMeuInk` | iot-service outbound API calls |
| 服务监控-statemachine-metrics | `p6CseQSnk` | state-machine service health |
| 服务监控-statemachine-状态统计 | `8YnCOSInz` | state-machine state transition stats |
| 服务监控-kiss-metrics | `OfPlfqS7z` | kiss service health |
| KissProcesses-US | `FI-jWMRMz` | kiss US process-level monitoring |
| KissProcesses-CN | `KyC8uZRMz` | kiss CN process-level monitoring |
| kiss消息 | `ptnc22AIk` | kiss message metrics |
| 业务监控-Saas-美国-prod | `aZ6VJv2nz` | US Saas business monitoring (ES-backed) |
| 业务监控-Saas-欧洲-prod | `oUz4PAtnz` | EU Saas business monitoring |
| 业务监控-Saas-中国-prod | `eS_ZJv27z` | CN Saas business monitoring |
| saas-iot灰度上线 | `Doa2ZOLnz` | Gray rollout dashboard |
| 上线必看 - 美国 | `T2-U1V3Vk` | Post-deploy must-see panel — US |
| 上线必看 - 欧洲 | `-eN6JV3Vk` | Post-deploy must-see panel — EU |
| 上线必看 - 中国 | `TtuJoV34z` | Post-deploy must-see panel — CN |

```bash
# Read full panel definitions
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "https://grafana.addx.live/api/dashboards/uid/<UID>" | jq '.dashboard.panels'
```

## 4. Troubleshooting platform (logs + ES + DB queries)
## 4. Troubleshooting 平台（日志 + ES + DB 查询）

Use the **`troubleshooting` skill** for these — it has full Swagger + auth + cross-reference logic. Endpoint table:
用 **`troubleshooting` skill**——它有完整的 Swagger + 认证 + cross-reference 逻辑。端点：

| Environment | API base URL |
|---|---|
| Prod-US | `https://troubleshooting-us.addx.live` |
| Prod-EU | `https://troubleshooting-eu.addx.live` |
| Staging-US | `https://troubleshooting-staging-us.addx.live` |
| Staging-EU | `https://troubleshooting-staging-eu.addx.live` |

All four envs share one token. CN environment has no troubleshooting platform; use direct ES / DB query via dedicated tooling.
四个环境共用同一 token。CN 环境无 troubleshooting 平台；直接用 ES / DB 查询工具。

For statistical queries (rate over time across log lines), see [troubleshooting-bridge.md](troubleshooting-bridge.md).
统计类查询（日志行的时间速率）见 [troubleshooting-bridge.md](troubleshooting-bridge.md)。

## 5. Superset (BI / data warehouse)
## 5. Superset（BI / 数仓）

| URL | Auth |
|---|---|
| `https://superset.addx.live` | SSO; use `superset` skill which handles browser-flow auth |

Used for: Snowplow event volumes, daily active devices/users by region, fan-out distribution (P50/P99/P999 device-count per user).
用于：Snowplow 事件量、按区 DAU/DAD、fan-out 分布（每用户设备数 P50/P99/P999）。

See [data-warehouse.md](data-warehouse.md) for query templates.

## 6. DataHub (data catalog / lineage)
## 6. DataHub（元数据目录 / 血缘）

| URL | Auth |
|---|---|
| `https://datahub.addx.live` | use `datahub` skill |

Used for: discovering which fact table holds what column before writing a Superset query. Cheap to query, expensive to guess.
用于：写 Superset query 前，发现"哪个事实表存哪个列"。查 DataHub 便宜，瞎猜代价高。

## 7. Other observability surfaces (non-Prometheus)
## 7. 其它可观测面（非 Prometheus）

| Surface | When to use | Skill |
|---|---|---|
| Sentry | Error rate / release health on a per-service basis | `sentry` |
| GrowthBook | A/B experiments + feature flags + experiment results | `growthbook` |
| ArgoCD | Deployment / sync state | `argocd` |
| Kubernetes | Pod logs, restart count, resource limits | `k8s-ops` |
| ES indexes (raw) | Direct ES query if troubleshooting platform doesn't fit | `troubleshooting` skill `/log-search/query-index` |

## 8. Refresh procedure / 刷新流程

When a domain in this file stops resolving:
本文中某域名失效时：

1. Probe candidate variants (eg if `thanos-prod-cn` fails, try `thanos-cn`, `thanos-prod-cn-internal`, `thanos.cn.addx.live`).
   探测候选变种。
2. Update this file with the working URL + the date of last verification.
   更新本文为可用 URL + 最近验证日期。
3. Note the **failed** patterns explicitly so the next person doesn't re-probe them.
   把**失败**的模式也记下来，避免下次重复探测。

Last full verification: 2026-04-28.
最近一次完整验证：2026-04-28。
