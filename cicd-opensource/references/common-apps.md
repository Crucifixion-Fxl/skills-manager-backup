# 常见开源项目部署模式

按部署复杂度分类的常见开源项目参考。每个条目列出关键配置点，具体 K8s 模板见 [k8s-templates.md](k8s-templates.md)。

## 无状态工具类（最简单）

这类应用本身无状态，数据存在外部数据库中。使用 Rollout 部署。

### Grafana

| 项目 | 值 |
|------|-----|
| 镜像 | `docker.io/grafana/grafana:<version>` |
| 端口 | 3000 |
| 健康检查 | `/api/health` |
| 数据持久化 | 外接 PostgreSQL（推荐 Crossplane RDS）或 SQLite + PVC |
| 配置方式 | 环境变量 + ConfigMap |
| 访问控制 | 办公室 Ingress（`ingress.addx.io/sg: office`） |

**关键环境变量：**
```yaml
GF_DATABASE_TYPE: "postgres"
GF_DATABASE_HOST: "<rds-endpoint>"
GF_DATABASE_NAME: "grafana"
GF_DATABASE_USER: "grafana"
GF_SERVER_ROOT_URL: "https://grafana.addx.live"
GF_SECURITY_ADMIN_PASSWORD:  # → Vault ExternalSecret
```

### Metabase

| 项目 | 值 |
|------|-----|
| 镜像 | `docker.io/metabase/metabase:<version>` |
| 端口 | 3000 |
| 健康检查 | `/api/health` |
| 数据持久化 | 外接 PostgreSQL |

**关键环境变量：**
```yaml
MB_DB_TYPE: "postgres"
MB_DB_HOST: "<rds-endpoint>"
MB_DB_PORT: "5432"
MB_DB_DBNAME: "metabase"
MB_DB_USER: "metabase"
MB_DB_PASS:  # → Vault
MB_SITE_URL: "https://metabase.addx.live"
```

### n8n

| 项目 | 值 |
|------|-----|
| 镜像 | `docker.io/n8nio/n8n:<version>` 或 `quay.io/n8n/n8n:<version>` |
| 端口 | 5678 |
| 健康检查 | `/healthz` |
| 数据持久化 | 外接 PostgreSQL + 可选 Redis |

**关键环境变量：**
```yaml
DB_TYPE: "postgresdb"
DB_POSTGRESDB_HOST: "<rds-endpoint>"
DB_POSTGRESDB_DATABASE: "n8n"
DB_POSTGRESDB_USER: "n8n"
DB_POSTGRESDB_PASSWORD:  # → Vault
N8N_HOST: "n8n.addx.live"
N8N_PROTOCOL: "https"
WEBHOOK_URL: "https://n8n.addx.live"
```

### Nocodb

| 项目 | 值 |
|------|-----|
| 镜像 | `docker.io/nocodb/nocodb:<version>` |
| 端口 | 8080 |
| 健康检查 | `/api/v1/health` |
| 数据持久化 | 外接 PostgreSQL |

**关键环境变量：**
```yaml
NC_DB: "pg://host:5432?u=nocodb&p=xxx&d=nocodb"  # 或拆分
NC_PUBLIC_URL: "https://nocodb.addx.live"
```

## Web UI 类

### Redmine / GitLab Runner / Jenkins Agent 等

这类应用通常：
- 有 Web UI + API
- 需要持久化配置/数据
- 使用 Rollout + PVC 或外接 DB

配置模式与上面类似，注意检查官方文档的环境变量列表。

## 有状态中间件

这类应用自身保存数据，需要 StatefulSet + PVC。**优先考虑使用 Crossplane 创建托管服务替代。**

### Redis（缓存场景，非持久化）

如果只用于缓存（数据丢失可接受），可用 Rollout：

```yaml
# rollout.yaml
containers:
  - name: redis
    image: redis:placeholder
    ports:
      - containerPort: 6379
    command: ["redis-server"]
    args: ["--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru"]
    resources:
      requests:
        cpu: 100m
        memory: 256Mi
      limits:
        cpu: 500m
        memory: 512Mi
```

如果需要持久化，使用 StatefulSet + PVC 或 **推荐 Crossplane ElastiCache**。

### RabbitMQ

| 项目 | 值 |
|------|-----|
| 镜像 | `docker.io/library/rabbitmq:3.11-management-alpine`（已同步） |
| 端口 | 5672 (AMQP), 15672 (Management UI) |
| 健康检查 | 端口 15672 `/api/healthchecks/node` |
| 存储 | StatefulSet + PVC `/var/lib/rabbitmq` |

**关键配置：**
```yaml
# 两个 Service：AMQP + Management UI
# Service 1: AMQP (Headless for StatefulSet)
ports:
  - port: 5672
    name: amqp

# Service 2: Management UI (ClusterIP)
ports:
  - port: 15672
    name: management

# 环境变量
RABBITMQ_DEFAULT_USER: "admin"     # → Vault
RABBITMQ_DEFAULT_PASS: "xxx"       # → Vault
RABBITMQ_ERLANG_COOKIE: "xxx"      # → Vault（集群部署必须一致）
```

### Elasticsearch

| 项目 | 值 |
|------|-----|
| 镜像 | `docker.io/library/elasticsearch:<version>` |
| 端口 | 9200 (HTTP), 9300 (Transport) |
| 存储 | StatefulSet + PVC `/usr/share/elasticsearch/data` |
| 内存 | 至少 2Gi（JVM heap = 50% container memory） |

**关键配置：**
```yaml
env:
  - name: discovery.type
    value: "single-node"       # 单节点；集群部署不需要此项
  - name: ES_JAVA_OPTS
    value: "-Xms1g -Xmx1g"    # 根据 memory limit 调整
  - name: xpack.security.enabled
    value: "false"             # 内网可关闭
resources:
  requests:
    memory: 2Gi
  limits:
    memory: 4Gi
```

## 多组件项目

部分开源项目包含多个微服务（如 ReportPortal、Sentry、Temporal），需要：

1. **所有组件镜像** 都添加到 images.yaml
2. **每个组件** 一个 Rollout/StatefulSet + Service
3. **共享配置** 用同一个 ConfigMap/Secret
4. **可以放在同一个 ArgoCD Application** 或拆分

### 多组件目录结构示例

```
reportportal-deploy/
└── k8s/
    ├── base/
    │   ├── kustomization.yaml
    │   ├── api-rollout.yaml
    │   ├── api-service.yaml
    │   ├── auth-rollout.yaml
    │   ├── auth-service.yaml
    │   ├── ui-rollout.yaml
    │   ├── ui-service.yaml
    │   ├── ingress.yaml          # UI 入口
    │   └── configmap.yaml        # 共享配置
    └── overlays/
        └── staging-us/
            ├── kustomization.yaml
            ├── configmap.yaml
            └── external-secret.yaml
```

overlay kustomization 中需要为每个组件指定镜像：

```yaml
images:
  - name: service-api
    newName: harbor-xxx/base/reportportal/service-api
    newTag: "5.12.0"
  - name: service-authorization
    newName: harbor-xxx/base/reportportal/service-authorization
    newTag: "5.12.0"
  - name: service-ui
    newName: harbor-xxx/base/reportportal/service-ui
    newTag: "5.12.0"
```

## 配置来源决策树

```
开源项目需要配置？
├── 少量环境变量（<10 个）
│   → ConfigMap envFrom
├── 大量环境变量 + 敏感值
│   → ConfigMap（非敏感） + ExternalSecret（敏感）
├── 需要挂载配置文件
│   → ConfigMap data + volumeMount
└── 配置文件中包含密钥
    → 用 ExternalSecret 生成 Secret，volumeMount 挂载
```
