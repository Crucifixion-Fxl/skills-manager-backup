---
name: prometheus
description: 查询 Prometheus 监控指标和告警规则。当用户需要查 CPU/内存/磁盘使用率、服务存活状态、告警规则审计、容量趋势分析，或提及 Prometheus、PromQL、指标监控、targets 时使用本 Skill。
---

# prometheus

通过 Prometheus HTTP API 查询监控指标、检查告警和目标健康。API 和 PromQL 语法通过 Context7 MCP 查询，此处只记录公司特有的规则。

## Description

首次接入/变更扫描与日常认证入口见 [SaaS 接入](references/saas-access.md)；已有平台业务契约与授权门禁仍在本 Skill 维护。

适用场景：系统资源监控（CPU / 内存 / 磁盘）、服务可用性排查、告警规则审计、容量趋势分析。

> 下方 HTTP / 无认证说明仅适用于已记录的 **US DATA Prometheus**；其他实例的协议与认证按目标环境核验，不能套用到 CN VictoriaMetrics。

| 变量 | 说明 | 必需 |
|------|------|------|
| `PROMETHEUS_URL` | 已核验的完整查询 API base；US DATA 示例：`http://prometheus-us-data.addx.live` | 是 |

US DATA 入口无需认证；其他目标按实际访问配置使用本地已授权认证，不能根据域名推断无需认证。

> US DATA 实例版本记录为 Prometheus 2.45.0。API 端点和 PromQL 语法请通过 Context7 MCP 查阅官方文档。

## Rules

### 实例说明

`prometheus-us-data.addx.live` 是 **DATA 团队的 US 区域 Prometheus 实例**，主要监控数据基础设施。其他区域/团队按 [监控端点与目标选择](../../infrastructure/sre-agent/references/infra/prometheus.md) 选择已核验的直接查询入口或 Grafana 数据源；下方 Job、Target 和指标数量仅描述 US DATA 实例的记录，不代表 CN 集群。

### CN tech-service 目标（待切换）

腾讯云 `100052802231` / `cn-tech-service` 的统一命名已确认，部署切换尚待核验，完整状态见 [CN 域名切换清单](../../infrastructure/k8s-ops/references/cn-tencent-inventory.md#cn-tech-service-目标域名待切换)。

| 用途 | 目标入口（待切换） | 使用边界 |
|------|-------------------|----------|
| VictoriaMetrics 指标查询 | `https://victoria-metrics-cn-tech-service-tke.addx.live/select/0/prometheus` | VMCluster API base 保留租户路径，再追加 `/api/v1/query` 或 `/api/v1/query_range`；先核验实际租户、Ingress/Service、认证与采集范围 |
| VMAlert 告警评估 | `https://vm-alert-cn-tech-service-tke.addx.live` | 评估规则并向 Alertmanager 发送告警；不是 PromQL 查询 base，不能设置为 `PROMETHEUS_URL` |

不能因目标命名已确认就开始查询。先定位并核验实际入口（或 port-forward），再设置 `PROMETHEUS_URL`。使用 [performance query helper](../../quality/performance-preflight/references/query-helpers.py) 时，则必须显式设置 `PROMETHEUS_CN_TECH_SERVICE_URL`；未配置时不发请求，不回退旧域名、新目标或 prod。规则与通知排查按 [VM 告警链路](../../delivery/cicd-developer/references/victoriametrics/alerting.md) 核对实际 evaluator、规则选择器与通知路由。

### Scrape 目标（11 个 Job，271 个 Target）

| Job | Target 数 | 用途 |
|-----|----------|------|
| `kubernetes-nodes` | 53 | K8s 节点指标 |
| `kubernetes-nodes-cadvisor` | 53 | 容器资源指标 |
| `kubernetes-service-endpoints` | 56 | 服务端点指标 |
| `kubernetes-pods` | 47 | Pod 指标 |
| `us-data-eks-nodes` | 53 | DATA EKS 节点监控 |
| `us-prod-data-kafka` | 2 | Kafka Broker |
| `us-prod-data-kafka-metrics` | 2 | Kafka Exporter 指标 |
| `kubernetes-apiservers` | 2 | K8s API Server |
| `karpenter` | 1 | AWS Karpenter 自动扩缩 |
| `prometheus` | 1 | Prometheus 自身 |
| `prometheus-pushgateway` | 1 | Pushgateway |

### 指标体系（1562 个指标）

| 前缀 | 数量 | 来源 |
|------|------|------|
| `node_*` | 299 | Node Exporter（主机指标） |
| `kube_*` | 236 | kube-state-metrics（K8s 对象状态） |
| `prometheus_*` | 199 | Prometheus 自身 |
| `apiserver_*` | 173 | K8s API Server |
| `kubelet_*` | 112 | Kubelet |
| `karpenter_*` | 68 | AWS Karpenter 自动扩缩 |
| `container_*` | 60 | cAdvisor（容器资源） |
| `kafka_*` | 16 | Kafka Exporter |
| `fluentbit_*` | 14 | Fluent Bit 日志采集 |
| `cluster_autoscaler_*` | 28 | Cluster Autoscaler |

### Kafka 监控

DATA 团队 Kafka 集群有 5 个 Topic：

| Topic | 说明 |
|-------|------|
| `good` | 数据质量合格的事件 |
| `bad` | 数据质量不合格的事件 |
| `enriched` | 已富化的事件 |
| `test` | 测试 |

Consumer Group 与数据处理流水线对应：`good` → `enrich`（富化） → `enriched`（已富化）。`bad` 是数据质量不合格的旁路。

### Kafka 消费延迟监控

```promql
# 按 consumergroup 和 topic 聚合消费延迟
sum by (consumergroup, topic) (kafka_consumergroup_lag)
```

正常 lag 范围：< 500。如果 `good` topic 的 lag 持续增长，说明 `enrich` 消费者处理能力不足。

### Job 标签约定

| 格式 | 示例 | 说明 |
|------|------|------|
| `us-prod-data-{component}` | `us-prod-data-kafka` | DATA 团队组件 |
| `us-data-eks-nodes` | — | DATA EKS 节点 |
| `kubernetes-{resource}` | `kubernetes-pods` | 标准 K8s 指标 |

> 注意：这个 Prometheus 实例的 job 命名格式与 Grafana 中 IoT/SaaS 服务的 Prometheus（`prod-us-{service}`）不同。

### 查询注意事项

- **US DATA 入口使用 HTTP**：`http://prometheus-us-data.addx.live`；不要据此改写其他目标的 HTTPS 配置
- `step` 不要小于抓取间隔（通常 15s-60s），避免无效插值
- 高基数标签（user_id、request_id）**禁止**用于 `rate()` / `sum by()` 聚合
- macOS 下用 `date -v-1H +%s` 替代 Linux 的 `date -d '1 hour ago' +%s`

### 常见工作流

- **节点资源排查**：`node_cpu_seconds_total` → `node_memory_MemAvailable_bytes` → `node_filesystem_avail_bytes` → 定位高负载节点
- **Kafka 健康检查**：`kafka_brokers`（broker 数）→ `kafka_consumergroup_lag`（消费延迟）→ `kafka_topic_partition_under_replicated_partition`（副本不足）
- **Karpenter 扩缩监控**：`karpenter_nodes_created` → `karpenter_cluster_state_node_count` → `karpenter_disruption_actions_performed_total`
- **容器排查**：`container_cpu_usage_seconds_total` → `container_memory_working_set_bytes` → 按 pod/namespace 聚合

## Examples

### Bad

```bash
# 用 HTTPS 访问（连接会被拒绝）
curl "https://prometheus-us-data.addx.live/api/v1/query?query=up"

# 高基数标签聚合 — 会导致 Prometheus OOM
curl "http://prometheus-us-data.addx.live/api/v1/query?query=sum by(pod)(rate(container_cpu_usage_seconds_total[5m]))"
# pod 标签基数过高（数百个 pod），应按 namespace 或 deployment 聚合
```

### Good

```bash
# 检查 Kafka 消费延迟
curl -s "http://prometheus-us-data.addx.live/api/v1/query?query=sum%20by%20(consumergroup,topic)(kafka_consumergroup_lag)" | jq '.data.result[] | {group: .metric.consumergroup, topic: .metric.topic, lag: .value[1]}'

# 检查节点 CPU 使用率 top 10
curl -s "http://prometheus-us-data.addx.live/api/v1/query?query=topk(10,100*(1-rate(node_cpu_seconds_total{mode=\"idle\"}[5m])))" | jq '.data.result[] | {node: .metric.instance, cpu_pct: .value[1]}'
```
