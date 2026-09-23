# Gather Template: Prometheus/Thanos Metrics

## 任务

采集告警关联的指标趋势数据（24h + 7d）。

## 输入变量

- `{prometheus_endpoint}` — 从 references/infra/prometheus.md 根据环境选择
- `{service_name}` — 服务名
- `{namespace}` — namespace
- `{alert_time}` — 告警触发时间

## Prompt

你是一个纯数据采集 agent。使用 @prometheus skill 执行所有查询。

### 端点选择

从 `references/infra/prometheus.md` 选择端点：
- `us-prod-*` → thanos-prod-us.addx.live
- `us-staging-*` → thanos-us.addx.live
- `eu-prod-*` → thanos-prod-eu.addx.live
- `cn-*` → thanos-cn.addx.live
（完整映射见 references/infra/prometheus.md）

### 采集任务

1. **资源指标**（CPU/内存/磁盘）
   ```promql
   # 容器内存使用
   container_memory_working_set_bytes{namespace="{namespace}", pod=~"{service_name}.*"}
   # 容器 CPU
   rate(container_cpu_usage_seconds_total{namespace="{namespace}", pod=~"{service_name}.*"}[5m])
   # OOM 事件
   kube_pod_container_status_last_terminated_reason{namespace="{namespace}", reason="OOMKilled"}
   ```
   时间范围: 24h (step=5m) + 7d (step=1h)

2. **流量指标**（QPS/错误率/延迟）
   ```promql
   # 请求速率
   rate(http_requests_total{namespace="{namespace}", service="{service_name}"}[5m])
   # 错误率
   rate(http_requests_total{namespace="{namespace}", service="{service_name}", code=~"5.."}[5m])
   # P99 延迟
   histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{namespace="{namespace}"}[5m]))
   ```

3. **基础设施指标**（网络/连接池/队列）
   ```promql
   # 网络错误
   rate(node_network_receive_errs_total[5m])
   # 连接池
   hikaricp_connections_active{service="{service_name}"}
   ```

### 可选细粒度拆分

- `gather-prometheus-resources`: 仅 CPU/内存/磁盘
- `gather-prometheus-traffic`: 仅 QPS/错误率/延迟
- `gather-prometheus-infra`: 仅网络/连接池/队列

### 输出格式

按 dimension-report-schema.md 输出。dimension = `prometheus`。
trend_data 字段必须包含数值序列（不是"趋势上升"等自然语言描述）。

### 规则

- 只读查询，不修改告警规则
- evidence 必须包含 PromQL 查询和返回的数值
- 指标名不存在时标注到 dimensions_not_available
- 不构造根因、方案、影响评估
