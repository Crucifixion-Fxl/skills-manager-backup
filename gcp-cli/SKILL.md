---
name: gcp-cli
description: 通过 gcloud CLI 管理多项目 GCP 资源。当用户提到 GCP、Google Cloud、GCE、GKE、Cloud Storage、Cloud SQL、Cloud Run、Cloud Functions、BigQuery、Pub/Sub、Cloud CDN、Cloud DNS、Cloud Load Balancing、Firewall、VPC、IAM、Gemini、Vertex AI、AI Platform、Artifact Registry、Secret Manager，或需要查询/操作 GCP 云资源、查看 GCP 费用时使用。即使用户只说"查一下 GCP 上的实例"、"看看 GKE 集群状态"、"Gemini 项目的配额"也应触发。
---

# gcp-cli

## Description

通过 Google Cloud CLI（gcloud）协助运维和开发人员管理公司多项目的 GCP 资源。

## 项目体系

公司采用多项目、多区域、职责分离架构：

### 项目映射表

| Project ID | Project Name | Project Number | 用途 |
|-----------|-------------|----------------|------|
| `a4xcloud-p` | a4xCloud-p | 128565888518 | 全球生产 |
| `a4xcloud-p-us` | a4xcloud-p-us | 316805749903 | 美区生产（含 GKE） |
| `a4xcloud-p-eu` | a4xcloud-p-eu | 930188386332 | 欧区生产 |
| `a4xcloud-p-gemini` | a4xcloud-p-gemini | 142954504100 | 生产 Gemini AI |
| `a4xcloud-p-eu-gemini` | a4xcloud-p-eu-gemini | 737607047816 | 欧区生产 Gemini AI |
| `a4xcloud-tech-service` | a4xCloud-tech-service | 900295803500 | 技术服务 |
| `a4xcloud-tech-service-eu` | a4xcloud-tech-service-eu | 1007060311362 | 欧区技术服务 |
| `a4xcloud-tech-service-us` | a4xcloud-tech-service-us | 492991028653 | 美区技术服务 |
| `a4xcloud-diversion` | a4xcloud-diversion | 628061703556 | 流量分发 |
| `a4xcloud-diversion-eu` | a4xcloud-diversion-eu | 102653985444 | 欧区流量分发 |
| `a4xcloud-t` | a4xCloud-t | 187170137062 | 测试环境 |
| `a4xpaas-000001` | A4xPaaS | 597807550844 | PaaS 平台 |
| `gen-lang-client-0660217726` | Default Gemini Project | 806183732506 | Gemini 默认项目 |

### 项目速查

用户提到的关键词与项目的映射：

| 关键词 | 优先匹配 Project |
|--------|-----------------|
| 生产/prod + 全球/默认 | `a4xcloud-p` |
| 生产/prod + 美区/US | `a4xcloud-p-us` |
| 生产/prod + 欧区/EU | `a4xcloud-p-eu` |
| Gemini/AI + 生产 | `a4xcloud-p-gemini` |
| Gemini/AI + 欧区 | `a4xcloud-p-eu-gemini` |
| 技术服务/tech + 默认 | `a4xcloud-tech-service` |
| 技术服务/tech + 美区 | `a4xcloud-tech-service-us` |
| 技术服务/tech + 欧区 | `a4xcloud-tech-service-eu` |
| 流量分发/diversion | `a4xcloud-diversion` |
| 流量分发 + 欧区 | `a4xcloud-diversion-eu` |
| 测试/test | `a4xcloud-t` |
| PaaS | `a4xpaas-000001` |

## gcloud 基础用法

```bash
gcloud <service> <resource> <action> --project=<project-id> [--region=<region>] [--zone=<zone>] [--format=<format>]
```

常用参数：
- `--project`：指定项目（必须显式指定，不要依赖默认项目）
- `--region` / `--zone`：指定区域
- `--format`：输出格式（`table`、`json`、`yaml`、`csv`、`value`）
- `--filter`：服务端过滤
- `--limit`：限制返回数量

### 常用区域

| 区域 | 代码 |
|------|------|
| 美东 | us-east1, us-east4 |
| 美西 | us-west1, us-central1 |
| 欧洲 | europe-west1, europe-west3 |
| 亚太 | asia-east1, asia-southeast1 |

## 常用服务速查

| 服务 | gcloud 命令 | 说明 |
|------|------------|------|
| Compute Engine | `gcloud compute` | GCE 虚拟机 |
| GKE | `gcloud container` | Kubernetes 集群 |
| Cloud Storage | `gcloud storage` / `gsutil` | 对象存储 |
| Cloud SQL | `gcloud sql` | 托管数据库 |
| Cloud Run | `gcloud run` | 无服务器容器 |
| Cloud Functions | `gcloud functions` | 无服务器函数 |
| BigQuery | `bq` | 数据仓库 |
| Pub/Sub | `gcloud pubsub` | 消息队列 |
| Cloud DNS | `gcloud dns` | DNS 管理 |
| Cloud Load Balancing | `gcloud compute forwarding-rules` / `backend-services` | 负载均衡 |
| Firewall | `gcloud compute firewall-rules` | 防火墙规则 |
| VPC | `gcloud compute networks` | 网络管理 |
| IAM | `gcloud iam` / `gcloud projects` | 权限管理 |
| Logging | `gcloud logging` | 日志服务 |
| Monitoring | `gcloud monitoring` | 监控告警 |
| Artifact Registry | `gcloud artifacts` | 镜像/包仓库 |
| Secret Manager | `gcloud secrets` | 密钥管理 |

## 执行流程

### Step 1: 执行命令

直接执行目标命令。如果报错（如 command not found、认证失败、配置缺失），读取 `references/setup.md` 并按引导帮助用户完成安装和配置。

### Step 2: 确认操作目标

根据用户描述确定：

1. **目标项目**：匹配上方项目映射表，确定 `--project`
   - 如果有多个项目，且根据上下文和已有知识无法判断使用哪个项目，主动询问用户使用哪个项目
   - 如果上下文可以明确推断（如"查看生产环境的 GCE"默认匹配 `a4xcloud-p`），告知用户使用的项目并直接执行
2. **目标区域**：根据项目和资源类型推断，或由用户指定
3. **操作内容**：判断读/写操作

### Step 3: 执行命令

所有命令必须显式指定 `--project`，**不要依赖默认项目**：

```bash
gcloud <command> --project=<project-id> [--region=<region>]
```

## Rules

### 查询范围约束

**默认单项目查询**：除非用户明确要求（如"查看所有项目的资源"、"对比各个项目"），否则只在单个项目中查询资源，不要自动遍历所有项目进行全局查找。

### 操作分级

#### 只读操作（直接执行）

所有 `list`、`describe`、`get-iam-policy` 类命令均为只读：

- **Compute**: `instances list`, `instances describe`, `disks list`, `images list`, `machine-types list`
- **GKE**: `clusters list`, `clusters describe`, `node-pools list`, `operations list`
- **Cloud SQL**: `instances list`, `instances describe`, `databases list`, `backups list`
- **Cloud Run**: `services list`, `services describe`, `revisions list`
- **Cloud Functions**: `list`, `describe`, `logs read`
- **Cloud Storage**: `ls`, `stat`, `du`（`gsutil` / `gcloud storage`）
- **Pub/Sub**: `topics list`, `subscriptions list`, `snapshots list`
- **DNS**: `managed-zones list`, `record-sets list`
- **Firewall**: `firewall-rules list`, `firewall-rules describe`
- **VPC**: `networks list`, `networks subnets list`
- **IAM**: `service-accounts list`, `roles list`, `get-iam-policy`
- **Logging**: `logs list`, `read`
- **Monitoring**: `dashboards list`, `alerting policies list`
- **Secret Manager**: `secrets list`, `secrets versions list`（注意：`secrets versions access` 会暴露密钥值，需提醒用户）
- **Billing**: `budgets list`, `accounts list`

#### 高危操作（必须用户确认）

以下操作必须展示完整命令并获得确认：

- **创建资源**: `create`
- **修改资源**: `update`, `set-iam-policy`, `add-iam-policy-binding`
- **删除资源**: `delete`
- **启停操作**: `instances start`, `instances stop`, `instances reset`
- **GKE 变更**: `clusters resize`, `clusters upgrade`, `node-pools update`
- **部署操作**: `run deploy`, `functions deploy`
- **DNS 变更**: `record-sets transaction execute`
- **防火墙变更**: `firewall-rules create/update/delete`
- **Secret 写入**: `secrets create`, `secrets versions add`
- **Storage 写操作**: `cp`（上传方向）, `rm`, `mv`

确认格式：

```
⚠️ 即将执行写操作：

  项目: a4xcloud-p-us (316805749903)
  区域: us-east4
  命令: gcloud compute instances stop my-instance --project=a4xcloud-p-us --zone=us-east4-b

确认执行？(y/n)
```

### 操作红线

1. **禁止未确认的写操作** — 任何写操作都必须二次确认
2. **禁止暴露密钥** — Secret Manager 值、Service Account Key 等只展示前 4 位 + `***`
3. **禁止大范围删除** — 批量删除必须先列出受影响资源并确认
4. **IAM 变更需谨慎** — 添加/移除 IAM 绑定前必须展示当前策略和变更内容
5. **必须显式指定 --project** — 避免误操作到默认项目

### 分页查询

gcloud 默认会自动分页返回全部结果。如需限制：
- `--limit=N`：限制返回条数
- `--page-size=N`：每页条数
- `--sort-by=FIELD`：排序

### 输出优化

- 资源列表默认使用 `--format=table` 提升可读性
- 需要程序化处理时使用 `--format=json`
- 使用 `--filter` 服务端过滤减少返回量，如 `--filter="status=RUNNING"`
- 使用 `--format='table(name,status,zone)'` 自定义列

## Examples

### Good

```
# 跨项目查询
用户：看看美区生产和欧区生产的 GCE 实例

AI：查询两个项目的 GCE 实例：

  美区 a4xcloud-p-us:
  [执行 gcloud compute instances list --project=a4xcloud-p-us --format='table(name,zone,machineType,status,networkInterfaces[0].accessConfigs[0].natIP,networkInterfaces[0].networkIP)']

  欧区 a4xcloud-p-eu:
  [执行 gcloud compute instances list --project=a4xcloud-p-eu --format='table(...)']
```

```
# 写操作确认
用户：停掉测试环境的某台 GCE

AI：找到实例：
  test-server (us-east1-b) - RUNNING

  ⚠️ 即将执行写操作：

    项目: a4xcloud-t (187170137062)
    区域: us-east1-b
    命令: gcloud compute instances stop test-server --project=a4xcloud-t --zone=us-east1-b

  确认执行？(y/n)
```

```
# 密钥保护
用户：查一下 tech-service 的某个 secret

AI：查到 Secret: api-key, 版本: 3
  值: AIza****（已脱敏，如需完整值请确认）
```

### Bad

```
# 未指定 project
gcloud compute instances list  ← 缺少 --project，会使用默认项目

# 未确认就执行写操作
用户：删掉这个 GCS bucket
AI：已删除。 ← 未展示影响范围和确认

# 暴露密钥
AI：Service Account Key 为 {"type":"service_account","private_key":"-----BEGIN..."}  ← 完整暴露
```

## 豁免

| 场景 | 条件 |
|------|------|
| 紧急故障恢复 | 用户明确声明紧急情况，可简化确认流程（仍需展示命令） |
| 测试环境 (a4xcloud-t) | 非删除类写操作可简化确认 |
