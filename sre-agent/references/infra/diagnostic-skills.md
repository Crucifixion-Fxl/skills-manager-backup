# 可用的诊断 Skills

以下是当前可用的数据源 Skills 及其能力：

| Skill | 能力 | 网络访问 | 典型用途 |
|-------|------|----------|----------|
| `/prometheus` | PromQL 即时/范围查询 | 可访问内网 Thanos 端点 | 指标趋势、告警规则查询 |
| `/pagerduty` | PagerDuty REST API | 公网 | 告警查询、关联分析 |
| `/k8s-ops` | kubectl 只读操作 | 集群内网 | Pod/Node 状态、日志 |
| `/grafana` | Dashboard 查询 | 内网 | 可视化指标 |
| `/argocd` | 部署历史查询 | 内网 | 最近部署、同步状态 |
| `/sentry` | 错误追踪查询 | 公网 | 应用错误、堆栈跟踪 |
| `/aws-cli` | AWS API 只读 | 公网 | EC2/RDS/MSK 等资源状态 |
| `/gcp-cli` | GCP gcloud API 只读 | 公网 | GCE/GKE/Cloud SQL 等资源状态 |
| `/tencent-cloud-cli` | 腾讯云 API | 公网 | CVM/TKE/DTS 等资源状态 |
| `/troubleshooting` | ES 日志搜索（32 个索引，含 statemachine/kiss/auth 等）、Cross-reference ID 关联、iot-service 日志加载与查询、设备事件分析、DB 诊断查询 | 公网（troubleshooting-us/eu.addx.live） | 应用日志查询、设备故障排查、用户/设备 ID 关联追溯 |

> 这是已知的 skill 清单，不是限制。诊断过程中如果发现新的可用工具或方法，应主动尝试使用。
