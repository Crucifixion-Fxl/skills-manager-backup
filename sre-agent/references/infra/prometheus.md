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
| tencent-100014919455 | cn-prod（cn-main） | `http://thanos-cn.addx.live` | 存量 Thanos；与 VictoriaMetrics 迁移共存，先验证 job/时间范围 |
| tencent-100014919455 | cn-prod（cn-main） | `http://victoria-metrics-cn.addx.live/select/0/prometheus` | VictoriaMetrics 集群；API base 含租户路径 |
| tencent-100052802231 | cn-staging | 先查集群 `victoria-metrics` namespace 的 vmsingle Service，再 port-forward；API base 为本地转发地址 | VictoriaMetrics 单机；不假设 prod Thanos 已聚合 |
| tencent-100052802231 | cn-tech-service | **目标，待切换**：`https://victoria-metrics-cn-tech-service-tke.addx.live/select/0/prometheus` | VictoriaMetrics 集群；API base 含租户路径，尚非可用性证明 |

CN 账户/集群来源见 [CN 清单](../../../k8s-ops/references/cn-tencent-inventory.md)。监控声明来源：`argocd-apps/tencent-100052802231-cn-staging/victoria-metrics-k8s-stack-application.yaml` 与 `k8s/clusters/tencent-100014919455-cn-main/victoria-metrics-config/`；tech-service 目标及固定版本旧声明见 [域名切换清单](../../../k8s-ops/references/cn-tencent-inventory.md#cn-tech-service-目标域名待切换)。实际查询前核对 Ingress/Service、租户、认证与可用性；目标清单不证明已经部署。

tech-service 使用 [query helper](../../../../quality/performance-preflight/references/query-helpers.py) 时，必须显式设置 `PROMETHEUS_CN_TECH_SERVICE_URL` 为现场核验的 API base（可以是经核验的现有入口或 port-forward）。缺失时 helper 不发请求，也不回退旧域名、新目标或 prod。

### CN tech-service VMAlert

**目标，待切换**：`https://vm-alert-cn-tech-service-tke.addx.live`。VMAlert 负责评估规则并向 Alertmanager 发送告警，不能替代上表 vmselect 的 PromQL 查询 base。排查规则加载/评估和通知时，先核对实际 VMAlert 的 namespace/rule selector、数据源和 Alertmanager 路由，再访问经核验的入口；目标域名的认证和可用性尚待验证。规则归属和通知链路见 [VM 告警说明](../../../../delivery/cicd-developer/references/victoriametrics/alerting.md)。域名命名调整不修改现有 cluster/job 标签。

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

告警 labels 中的 `prometheus_group` 或 `job` 前缀只提供环境线索，查询前还要核验采集实例和资源归属：
- `us-prod-*` → thanos-prod-us
- `us-staging-*` / `staging-us-*` → thanos-us
- `eu-prod-*` → thanos-prod-eu
- `eu-data-*` → prometheus-eu-data
- `us-data-*` → prometheus-us-data
- `cn-prod-*` / `prod-cn-*` → CN prod：按 job 实际采集位置选择同账户 Thanos 或 VictoriaMetrics
- `cn-staging-*` / `staging-cn-*` → 必须显式核验采集目标；cn-main 配置仍有 `cn-staging-kiss` 和 `cn-staging-kafka-metrics`，不能仅凭前缀改查新 staging vmsingle，也不能默认查 prod
- `cn-tech-service*` → CN tech-service 逻辑环境；核验资源与采集实例后，显式选择实际 VictoriaMetrics API base，不能自动连接待切换域名

`cluster`、scrape 配置与 Application destination 优先于模糊 job 前缀。Dispatcher 提取的 `cn-staging` 只是逻辑环境，不能代替现场目标核验。使用 query helper 时，这类 job 必须传明确 target：`cn` 表示已核验的 cn-main 存量指标，`cn-staging` 表示新集群，并配置目标实际 API base。缺少目标证据时先解析资源归属。VictoriaMetrics 集群在 `/select/0/prometheus` 后追加 `/api/v1/query`；单机模式不追加租户路径。

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
- 仅查询与目标资源相关且已核验的 Prometheus/Thanos/VM 端点；待切换域名不作为探测列表
- 一个 IP 可能有多个端口（如 `:9100` Node Exporter、`:18002` JVM），需分别查询
- Thanos 的 `targets` API 响应可能非常大（数百 MB），避免全量下载解析
