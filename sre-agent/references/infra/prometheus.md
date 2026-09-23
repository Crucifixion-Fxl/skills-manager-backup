# Prometheus / Thanos / VictoriaMetrics 端点

| 账户/项目 | 环境 | 端点 | 类型 |
|-----------|------|------|------|
| aws-302571458622 | us-prod | `http://thanos-prod-us.addx.live` | Thanos |
| aws-002497567426 | us-tech-service | `http://thanos-us.addx.live` | Thanos |
| aws-740315635167 | eu-prod | `http://thanos-prod-eu.addx.live` | Thanos |
| aws-769494896000 | eu-data | `http://prometheus-eu-data.addx.live` | Prometheus |
| aws-769494896000 | us-data | `http://prometheus-us-data.addx.live` | Prometheus |
| gcp-a4xcloud-p-us | us-prod | `http://34.11.81.69:8428/` | VictoriaMetrics |
| gcp-a4xcloud-tech-service-us | us-tech-service | `http://34.11.81.69:8428/` | VictoriaMetrics |
| tencent-100014919455 | cn-main | `http://thanos-cn.addx.live` | Thanos |

### VM 级监控组件

除 K8s 集群内的 Prometheus 外，还有独立 VM 运行的监控服务：

| VM 名称 | IP | 项目 | 服务 | 覆盖范围 |
|---------|-----|------|------|----------|
| `us-tech-service-prometheus` | `10.225.0.3` | `gcp-a4xcloud-tech-service` | Prometheus (9090) + Alertmanager (9093) | 通过 federation 聚合旧 GCP 集群指标，评估 `prod.*` namespace 告警规则 |

> SSH 访问：`gcloud compute ssh us-tech-service-prometheus --project=a4xcloud-tech-service --zone=us-east4-a --tunnel-through-iap`
> Prometheus 配置：`/data/apps/prometheus/prometheus.yml`

### 监控拓扑 / Federation 链路

同一条告警可能从不同路径到达 PagerDuty，排查时需根据 PagerDuty alert 的 `client_url` 或 `cluster` label 判断来源：

```
GCP 旧 prod 集群 (gcp-a4xcloud-p)      GCP 新 prod 集群 (gcp-a4xcloud-p-us)
  kube-state-metrics                     kube-state-metrics
        ↓                                      ↓
  Prometheus (10.224.0.114:9090)         Prometheus (集群内)
        ↓ federation                           ↓ 本地 Alertmanager
  VM us-tech-service-prometheus                ↓
  (10.225.0.3:9090)                      PagerDuty (pagerduty-devops)
        ↓ Alertmanager (10.225.0.3:9093)
        ↓
  PagerDuty (pagerduty-devops)
```

**来源识别规则**：
- `client_url` 含 `10.225.0.3:9093` 或 `cluster` label = `us-tech-service-prometheus` → VM 路径，需 SSH 到 VM 查看
- `client_url` 含集群内 pod IP → K8s 集群内 Alertmanager 路径，用 kubectl 查看

### 端点选择规则

从告警 labels 中的 `prometheus_group` 或 `job` 前缀判断环境：
- `us-prod-*` → thanos-prod-us
- `us-staging-*` / `staging-us-*` → thanos-us
- `eu-prod-*` → thanos-prod-eu
- `eu-data-*` → prometheus-eu-data
- `us-data-*` → prometheus-us-data
- `cn-*` / `staging-cn-*` → thanos-cn

### 通过 IP/标签值反查指标

当需要查找某个 IP 地址（或任意标签值）关联了哪些指标时，按以下步骤操作：

**第 1 步：定位 IP 所在的 Prometheus 实例和标签名**

`label/__name__/values?match[]=` 在 Thanos 上对 federated 数据**不可靠**（Store Gateway 限制）。正确做法是查 `up` 指标遍历所有标签值：

```bash
# 在 up 指标中搜索包含目标 IP 的标签（遍历所有端点）
curl -s "$PROMETHEUS_URL/api/v1/query?query=up" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data['data']['result']:
    for k, v in r['metric'].items():
        if 'TARGET_IP' in v:
            print(json.dumps(r['metric'], indent=2))
"
```

> IP 通常在 `instance` 标签中（格式 `IP:Port`），但也可能在 `node`、`host`、`__address__` 等标签中。

**第 2 步：获取该 IP 的所有指标名**

用 `series` API（**必须 POST**），`label/__name__/values` 在 Thanos 上不可靠：

```bash
START=$(date -v-1H +%s)  # macOS；Linux 用 date -d '1 hour ago' +%s
END=$(date +%s)
curl -s "$PROMETHEUS_URL/api/v1/series" \
  --data-urlencode 'match[]={instance="TARGET_IP:PORT"}' \
  --data-urlencode "start=$START" --data-urlencode "end=$END" \
  | jq '[.data[].__name__] | unique'
```

**要点**：
- 需遍历上方所有 Prometheus/Thanos 端点
- 一个 IP 可能有多个端口（如 `:9100` Node Exporter、`:18002` JVM），需分别查询
- Thanos 的 `targets` API 响应可能非常大（数百 MB），避免全量下载解析
