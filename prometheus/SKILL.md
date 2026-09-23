---
name: prometheus
description: 查询 Prometheus 监控指标和告警规则。当用户需要查 CPU/内存/磁盘使用率、服务存活状态、告警规则审计、容量趋势分析，或提及 Prometheus、PromQL、指标监控、targets 时使用本 Skill。
---

# prometheus

通过 Prometheus HTTP API 查询监控指标、检查告警和目标健康。API 和 PromQL 语法通过 Context7 MCP 查询，此处只记录公司特有的规则。

## Description

适用场景：系统资源监控（CPU / 内存 / 磁盘）、服务可用性排查、告警规则审计、容量趋势分析。

> **重要**：Prometheus 使用 HTTP（非 HTTPS），且无需认证。

| 变量 | 说明 | 必需 |
|------|------|------|
| `PROMETHEUS_URL` | `http://prometheus-us-data.addx.live`（注意是 HTTP） | 是 |

认证：无需认证，直接访问。

> Prometheus 版本 2.45.0。API 端点和 PromQL 语法请通过 Context7 MCP 查阅官方文档。

## Rules

### 实例说明

当前 `prometheus-us-data.addx.live` 是 **DATA 团队的 US 区域 Prometheus 实例**，主要监控数据基础设施。其他区域/团队的 Prometheus 实例通过 Grafana 间接访问（Grafana 数据源配置了内部 Prometheus 地址）。

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

- **必须使用 HTTP**：`http://prometheus-us-data.addx.live`（HTTPS 端口不可达）
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
