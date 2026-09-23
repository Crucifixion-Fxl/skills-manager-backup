# Managed cloud middleware metrics (MetricsCollector)

`manage-victoriametrics-scrape.md` covers scraping an **application's own**
`/metrics` endpoint. This reference covers a different capability: collecting
metrics from **cloud-managed middleware** (RDS / Redis / Kafka and their
per-cloud equivalents) that has no in-cluster pod to scrape. The metrics live in
the cloud provider's monitoring API (AWS CloudWatch, GCP Cloud Monitoring,
Tencent Cloud Monitor); a per-cloud exporter Deployment reads that API and
re-exposes the series as a normal in-cluster `/metrics` endpoint, which a
`VMServiceScrape` then scrapes like any other target.

The platform normalises every provider's raw series into one cross-cloud
`managed_*` contract, so a developer queries `managed_sql_cpu_utilization_ratio`
regardless of whether the instance is AWS RDS, GCP Cloud SQL or Tencent MySQL.

## What is a read-only bypass, and why it never breaks business health

Every adapter is a **read-only sidecar collector**: it only reads the cloud
monitoring API and re-exposes series. It has no webhook, no finalizer, holds no
lock on business resources, and lives in its own `metrics-collector` namespace
behind a NetworkPolicy. If an adapter crashes, business Crossplane Claim/XR
Ready, Argo health, and the managed instances themselves are unaffected — only
the metrics stop flowing until it recovers. This invariant is non-negotiable:
never give an adapter write scope, admission webhooks, or cross-namespace
coupling.

## Provider / engine support matrix

Verified in production. "auto-discovery" means new instances of that type are
collected automatically; "whitelist" means the instance id must be added to the
adapter config.

| Provider | SQL (RDS-class) | Cache (Redis-class) | Broker (Kafka-class) | Exporter | Discovery |
|---|---|---|---|---|---|
| AWS | RDS | ElastiCache | MSK | YACE (`yet-another-cloudwatch-exporter`) | auto-discovery per region |
| GCP | Cloud SQL (MySQL+PG) | Memorystore | (no managed Kafka in use) | `stackdriver-exporter` | auto by metric prefix |
| Tencent | TencentDB MySQL (CDB) + PostgreSQL | TencentDB Redis | CKafka | `tencentcloud-exporter` | whitelist (`only_include_instances`) |

All three exporters are open-source (no license fee). The only recurring cost is
the cloud monitoring API call charge (AWS CloudWatch GetMetricData ~US$100/month
across the AWS clusters; GCP/Tencent within free tier). Series are stored in each
cluster's existing VictoriaMetrics — no separate store, negligible storage cost.

## Near-source deployment rule

Each adapter runs **in its own cloud's prod cluster** and its metrics land in
that cluster's own VictoriaMetrics. Do not collect one cloud from another
cluster: cross-cloud scraping adds egress cost, crosses credential/data-residency
boundaries (CN data must stay in CN), and widens the blast radius. AWS adapters
run in the AWS cluster, GCP in the GKE cluster, Tencent in the Tencent cluster.

## Credential model

Credentials are always resolved through Vault + ExternalSecret, never a bare
Secret, never long-lived keys in Git. Use the resolver path
`secret/{env}/app/metrics-collector/<key>` (see `references/vault-paths/`), and
write to the Vault instance the target cluster's `vault-backend`
ClusterSecretStore points at (`references/vault-paths/instances.yaml`):

| Cluster | Vault instance | Key |
|---|---|---|
| AWS us-prod / eu-prod | vault-us-prod / vault-eu-prod | `aws-credentials` (readonly AK/SK) |
| GCP prod | vault-us-prod | `gcp-sa-key` (SA with `roles/monitoring.viewer`) |
| Tencent prod | vault-cn-prod | `tencent-credentials` (CAM sub-user) |

Each credential is minimum-privilege read-only, no write, no admin, no
cross-account, no cross-environment. Tencent needs BOTH the Monitor readonly
policy AND each product's readonly policy (CDB/PostgreSQL/CKafka/Redis) — the
Monitor policy reads metrics, the product policy lets the exporter discover
instances (without it the exporter reports "Instance not found count=0").

## The managed_* canonical contract

Recording rules (`VMRule`) convert each provider's raw series into these
cross-cloud names. Query these, not the raw `aws_*` / `stackdriver_*` / `qce_*`
series. Every managed series carries `provider` (`aws`/`gcp`/`tencent`),
`__managed_source__`, `__managed_contract__=v1`, and an `engine` label where
applicable (`mysql`/`postgresql`/`redis`/`kafka`).

SQL (`managed_sql_*`):
- `managed_sql_cpu_utilization_ratio`
- `managed_sql_connections_current`
- `managed_sql_connection_utilization_ratio`
- `managed_sql_connections_max`
- `managed_sql_storage_free_bytes` / `managed_sql_storage_utilization_ratio`

Cache (`managed_cache_*`):
- `managed_cache_memory_used_bytes`
- `managed_cache_connected_clients`
- `managed_cache_hit_ratio`
- `managed_cache_cpu_utilization_ratio`
- `managed_cache_replication_lag_seconds`

Broker (`managed_broker_*`):
- `managed_broker_disk_used_bytes` / `managed_broker_disk_used_ratio`
- `managed_broker_messages_in_total`
- `managed_broker_bytes_in_total`
- `managed_broker_partition_count`

## Identifying a specific instance

The metric name is unified but the **instance-identity label differs per cloud**
(a known non-uniform edge; filter by the right label per provider):

| Provider | Instance label | Example |
|---|---|---|
| AWS RDS | `dimension_DBInstanceIdentifier` / `dimension_DBClusterIdentifier` | `us-prod-piivault` |
| AWS MSK | `name` (ARN) / `dimension_Cluster Name` | `...:cluster/us-prod-msk/...` |
| Tencent | `instanceid` | `crs-ga2i8t63` |
| GCP | `database_id` / resource label | project-scoped id |

## PromQL examples (verified against production)

```promql
# AWS RDS instance CPU
managed_sql_cpu_utilization_ratio{provider="aws", dimension_DBInstanceIdentifier="us-prod-piivault"}

# AWS MSK cluster disk
managed_broker_disk_used_bytes{provider="aws", name=~".*us-prod-msk.*"}

# Tencent Redis instance memory
managed_cache_memory_used_bytes{provider="tencent", instanceid="crs-ga2i8t63"}

# Cross-cloud: all SQL CPU above 80% on any provider
managed_sql_cpu_utilization_ratio > 0.8

# Filter by provider
managed_cache_hit_ratio{provider="tencent"}
```

## Sparse-data caveat (GCP and multi-product Tencent)

Cloud monitoring APIs return sparse points (one per ~5m). When the adapter
scrape interval is 5m (needed when one adapter collects many products/instances,
e.g. Tencent with Redis+MySQL+PG+CKafka), the recording rule's instant
evaluation can fall outside VictoriaMetrics' default 5m staleness window and
produce 0 samples even though the raw series exist. Wrap such recording-rule
exprs in `last_over_time(raw[10m])` and set the rule group `interval: 5m`. Dense
scrapes (AWS CloudWatch at 5m with fewer series per target) do not need this.

## VMRule namespace depends on the cluster's vmalert

- If the cluster's `vmalert` runs `selectAllByDefault=true` (GCP prod, Tencent
  prod, eu-prod), the `VMRule` may live in the `metrics-collector` namespace
  next to the adapter.
- If `selectAllByDefault=false` (aws us-prod), `vmalert` only discovers VMRules
  in its own `victoria-metrics` namespace, so the recording-rule `VMRule` MUST be
  placed there or it is silently ignored and no `managed_*` is produced.

Check with `kubectl -n victoria-metrics get vmalert -o jsonpath=...spec.selectAllByDefault`
before deciding the VMRule namespace.

## Verifying series after deploy

Query the vmalert datasource `vmselect-victoria-metrics` (the headless service),
NOT `vmselect-victoria-metrics-public` — the public service's query result can be
unreliable and misleads verification. Give the recording rule one group interval
(up to 5m) plus one scrape interval after the adapter starts before expecting
`managed_*` to appear.

## Platform-owned vs developer-owned

Developer-owned (this workflow): the adapter Deployment/Service/ExternalSecret/
NetworkPolicy/VMServiceScrape and the recording-rule VMRule, all in the
application's overlay under `controllers/metrics-collector/config/...` plus the
Argo Application/AppProject and the per-cluster Kyverno rollout PolicyException.

Platform-owned (escalate to observability/SRE, do not create in the app repo):
`VMAgent`, `VMCluster`, `VMAlert`, operator/CRD installation, remote write,
retention, and the ClusterSecretStore itself.
