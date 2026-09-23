---
name: grafana
description: 查询和管理 Grafana 监控面板、告警规则、数据源。当用户需要查看 Dashboard、排查告警、检查服务监控指标、查找数据源，或提及 Grafana、监控、告警、Dashboard、可观测性时使用本 Skill。
---

# grafana

通过 Grafana HTTP API 查询监控面板、管理告警、探索数据源。API 用法通过 Context7 MCP 查询（`resolve-library-id` → `get-library-docs`），此处只记录公司特有的规则。

## Description

适用场景：查找 Dashboard、查看告警状态、管理数据源、排查服务监控问题。

### 专项资源路由

当用户问题明确聚焦于 iot-service/naturehood 监控盘点时，先读取 [references/iot-service-monitoring.md](references/iot-service-monitoring.md)。典型信号包括：

- 明确提及 `iot-service` / `naturehood-server`
- 关键词包含 `library` / `timeline` / `postcard`
- 关键词包含 `iot 接口报错` / `MQTT` / `绑定` / `OTA` / `灰度上线`
- 以 `prod-{cn/us/eu}-iot-service` 监控状态为上下文

非明确 iot-service 场景按通用流程执行；涉及日志、工单、设备事件时转 `troubleshooting`。

| 变量 | 说明 | 必需 |
|------|------|------|
| `GRAFANA_URL` | `https://grafana.addx.live` | 是 |
| `GRAFANA_TOKEN` | API Key（Settings → API Keys） | 是 |

认证：`curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" "$GRAFANA_URL/api/..."`

> Grafana 版本 8.3.3（使用 Legacy Alerting，非 Unified Alerting）。API 端点和参数请通过 Context7 MCP 查阅官方文档。

## Rules

### Dashboard 组织结构

33 个 Folder，172+ 个 Dashboard。Folder 按**团队/领域/区域**组织：

| 类型 | Folder 示例 | 说明 |
|------|------------|------|
| 业务域 | `a4x`, `AddX`, `AI` | 核心业务线 |
| 基础设施 | `kubernetes`, `ETCD`, `AWS`, `DATA` | 基础设施监控 |
| 区域 K8s | `us-ai-k8s-cluster`, `eu-ai-k8s-cluster`, `cn-ai-k8s-cluster` | 按区域的 K8s 集群 |
| 业务功能 | `Alert`, `Bind`, `OTA指标`, `埋点监控`, `增值服务` | 特定功能监控 |
| 客户端 | `Android 直播统计`, `IOS 直播统计`, `iOS 支付统计`, `iOS 绑定统计` | 移动端统计 |

### Dashboard 命名规范

| 模式 | 示例 | 说明 |
|------|------|------|
| `上线必看 - {区域}` | `上线必看 - 美国` | **部署后必看**，监控设备离线率、直播成功率、切片上传、Kafka 消费延迟 |
| `业务监控-{主题}-{区域}-{环境}` | `业务监控-Saas-美国-prod` | 后端业务指标，按区域+环境拆分 |
| `k8s-{区域}-{环境}-{服务}` | `k8s-us-prod-gpu-infer` | K8s 服务监控 |
| `Flywheel Alerts - {环境}-{区域}` | `Flywheel Alerts - prod-us` | AI 服务告警 |
| `{类型}监控-{区域}-{团队}` | `Kafka监控-美国-DATA` | 按组件和区域的监控 |
| `埋点看板-{分类}` | `埋点看板-事件-PIR` | 埋点数据看板 |

**三个区域**：CN（中国）、EU（欧洲）、US（美国），加 staging 变体。

### 标签约定

Dashboard 标签用于筛选，主要维度：

- **区域**：`中国` / `欧洲` / `美国`（最常用，各约 25 个 Dashboard）
- **类型**：`业务`（21）、`服务`（21）、`saas`（6）、`staging`（6）、`webhook`（6）
- **技术栈**：`Prometheus`（19）、`node_exporter`（17）、`Kafka`（5）、`kubernetes`（4）
- **业务功能**：`音视频`（6）、`绑定`（5）、`直播`（4）、`增值营销`（3）、`埋点`

### Prometheus Job 标签约定

Job label 是定位服务的关键，格式为 `{env}-{region}-{service}`：

| 模式 | 示例 |
|------|------|
| `prod-us-{service}` | `prod-us-statemachine`, `prod-us-a4x-log-report` |
| `prod-us-iot.*` | IoT 服务（通配） |
| `us-prod-{service}` | `us-prod-kiss`, `us-prod-kiss-jvm`, `us-prod-kafka-public` |

> 注意：存在 `prod-us-*` 和 `us-prod-*` 两种前缀格式，查询时建议用正则匹配。

### 自定义业务指标

| 指标 | 含义 |
|------|------|
| `device_state_change_counter` | 设备状态变化（offline/online），按 `type`, `modelNo`, `job` 分组 |
| `video_upload_complete_count_total_high_level` | 视频切片上传完成数，按 `serviceName`, `sliceType` 分组 |
| `kiss_receive_tcp_udp_count` | Kiss 服务 TCP/UDP 接收计数 |
| `log_level_count_total` | 日志级别统计，按 `loglevel` 分组 |
| `kafka_consumer_group_ConsumerLagMetrics_Value` | Kafka 消费延迟，`name="OffsetLag"` |

### 告警规则（Legacy Alerting）

共 120 条告警，集中在以下 Dashboard：

| Dashboard | 告警数 | 用途 |
|-----------|--------|------|
| `业务监控-后端` | 23 | 后端核心服务健康 |
| `临时告警` | 11 | 临时排查用 |
| `Flywheel Alerts - prod-{cn/eu/us}` | 各 10 | AI 服务延迟和错误率 |
| `上线必看 - {区域}` | 各 2-4 | 部署关键指标 |

**Flywheel Alert 命名格式**：`{Feature} {Metric} {Condition} [{env}-{region}]`
- 示例：`LLM Error Rate High [prod-us]`、`Bird Summary P95 Latency High [prod-cn]`
- 评估频率：60s，持续 5min 触发

**业务告警常见命名**：
- `{description} alert` — 如 `4G设备离线数净增量alert`、`直播成功率 alert`
- `{region} - {metric} alert` — 如 `中国 - state_connection_lost alert`

### 关键部署看板

**`上线必看`** 系列是部署后必看 Dashboard，监控以下核心指标：
- 设备离线率（实时 vs 上线前对比）
- 各型号固件离线率
- 直播成功率
- Kafka topic 消费延迟（P0 级）
- 切片上传成功率
- 设备状态变化统计
- 错误日志趋势
- Kiss 服务 TCP 连接数

### 操作红线

- 生产 Dashboard **禁止删除**，只能归档（移到 Archive Folder）
- 写操作（创建/修改 Dashboard、告警规则）**必须用户确认后才执行**
- 查询优先用 search API + tags 筛选缩小范围，再用 uid 获取详情

### 常见工作流

- **查找 Dashboard**：`/api/search?query=...&tag=美国` → 取 uid → `/api/dashboards/uid/{uid}` 获取详情
- **部署后检查**：直接查 `上线必看 - {区域}` Dashboard（uid: US=`T2-U1V3Vk`, EU=`-eN6JV3Vk`, CN=`TtuJoV34z`）
- **排查告警**：`/api/alerts?state=alerting` → 查触发规则 → 定位 Dashboard 面板 → 查看 PromQL 表达式
- **按区域筛选**：`/api/search?tag=美国` 或 `tag=欧洲` 或 `tag=中国`

## Examples

### Bad

```bash
# 直接 DELETE 生产 Dashboard（禁止删除，只能归档）
curl -X DELETE "$GRAFANA_URL/api/dashboards/uid/abc123"

# 不加筛选条件拉取所有 Dashboard 详情（应先 search 缩小范围）
for uid in $(curl ... /api/search | jq -r '.[].uid'); do curl ... /api/dashboards/uid/$uid; done
```

### Good

```bash
# 按区域标签搜索 Dashboard
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" "$GRAFANA_URL/api/search?tag=美国&type=dash-db" | jq '.[] | {uid, title, folderTitle}'

# 部署后直接查关键 Dashboard
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" "$GRAFANA_URL/api/dashboards/uid/T2-U1V3Vk" | jq '.dashboard.panels[] | {title, type}'

# 查看当前触发的告警
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" "$GRAFANA_URL/api/alerts?state=alerting"
```
