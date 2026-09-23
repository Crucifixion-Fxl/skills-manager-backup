# 开源项目部署完整流程

从零部署一个开源项目到 K8s 集群的 5 步详细流程。

## Step 1: 镜像同步

在 `DEV/base-images` 仓库的 `images.yaml` 中添加所需镜像，提交 MR 并合并。详见 [image-sync.md](image-sync.md)。

**部署前检查清单：**
- [ ] 确认镜像在 Docker Hub（或其他公共仓库）上存在
- [ ] 确认镜像有 amd64 架构（`docker manifest inspect <image>` 查看）
- [ ] 确认 images.yaml 中尚未有该镜像
- [ ] 使用具体版本 tag，非 `latest`

## Step 2: 前置配置

### 开发者自助完成

| 配置项 | 操作 |
|--------|------|
| **GitLab 仓库权限** | 配置仓库 Settings → Members → 添加 `Argocd-deploy` 为 **Reporter** |
| **ArgoCD 注册** | 在 `DEV/argocd-apps` 对应集群目录创建 Application YAML |

### 联系运维配置

| 配置项 | 运维操作 | 开发者需提供 |
|--------|----------|-------------|
| **Vault 密钥** | 创建路径并写入值 | 应用名、环境、密钥列表 |
| **DNS 记录** | 添加域名解析 | 域名、目标集群 |

> **示例联系运维消息：** "请帮忙为 grafana 配置：1) Builder Vault 路径 `secret/staging/app/grafana/config` 写入 `admin_password`；2) DNS `grafana-staging.addx.live` → us-staging 集群的 ALB。"

## Step 3: K8s 配置文件

### 配置仓库选择

开源项目的 K8s 配置通常不放在源代码仓库中（我们不 fork 开源项目），而是放在以下位置之一：

| 场景 | 仓库 | 说明 |
|------|------|------|
| **独立运维服务** | 新建 `DEV/<app-name>-deploy` 仓库 | 适合独立运维的工具类应用 |
| **平台基础设施** | `DEV/k8s` 或已有 ops 仓库 | 适合平台级公共组件 |
| **团队内部工具** | 团队已有的 ops/infra 仓库 | 避免仓库泛滥 |

### 目录结构

```
<app-name>-deploy/         # 或 ops 仓库中的子目录
└── k8s/
    ├── base/
    │   ├── kustomization.yaml
    │   ├── rollout.yaml        # 或 statefulset.yaml（有状态应用）
    │   ├── service.yaml
    │   ├── ingress.yaml        # 可选
    │   └── configmap.yaml      # 开源项目通常有大量配置
    └── overlays/
        ├── staging-us/
        │   ├── kustomization.yaml
        │   ├── configmap.yaml
        │   └── external-secret.yaml
        └── prod-us/
```

详细模板见 [k8s-templates.md](k8s-templates.md)。

## Step 4: ArgoCD Application 注册

在 `DEV/argocd-apps` 对应集群目录创建 Application YAML。

**关键区别：开源项目不配置 Image Updater 注解**，因为镜像版本是固定的，不通过 CI 自动更新。

详见 [k8s-templates.md](k8s-templates.md#argocd-application)。

## Step 5: 提交 MR → 合并 → 验证部署

### 提交顺序

1. **先合并 base-images MR** — 等镜像同步到目标集群（~15 分钟）
2. **再合并配置仓库 MR** — K8s 配置
3. **最后合并 argocd-apps MR** — ArgoCD Application 触发部署

> 如果 argocd-apps MR 先合并，ArgoCD 会尝试部署但镜像可能还未同步完成，Pod 会 ImagePullBackOff。等待镜像同步后会自动恢复。

### 验证部署成功

1. **ArgoCD UI**：确认应用 `Synced` + `Healthy`
2. **Pod 状态**：`kubectl get pods -n <namespace> -l app=<app-name>` → `Running`
3. **镜像版本**：`kubectl get rollout <app-name> -n <namespace> -o jsonpath='{.spec.template.spec.containers[0].image}'`
4. **Ingress 访问**：`curl -I https://<domain>/health` → 200（或应用特定的健康检查路径）

## 版本升级流程

```
1. base-images/images.yaml 添加新版本 → MR → 同步
2. overlay kustomization.yaml 更新 newTag → MR → ArgoCD 自动部署
```

升级是两步操作，不像 cicd-developer 那样代码推送自动完成。详见 [image-sync.md](image-sync.md#版本升级)。

## 多环境部署

同一个开源项目部署到多个环境/集群时，只需：
1. **镜像同步一次** — images.yaml 同步到所有集群，不需要按环境添加
2. **每个环境一套 overlay** — overlays/staging-us/、overlays/prod-us/ 等
3. **每个集群一个 ArgoCD Application** — 在 argocd-apps 对应目录下创建

## 有状态 vs 无状态决策

| 类型 | 部署方式 | 示例 |
|------|----------|------|
| **无状态工具** | Rollout + ConfigMap | Grafana（数据在外部 DB）、n8n（外接 Postgres）、Metabase |
| **有状态但可用托管服务** | Rollout + Crossplane RDS/Redis | 应用本身无状态，数据库用 Crossplane 创建 |
| **必须本地有状态** | StatefulSet + PVC | Redis（缓存场景）、RabbitMQ、Elasticsearch |

> **优先使用托管服务：** 如果开源项目需要数据库，优先通过 Crossplane 创建 RDS/ElastiCache，而不是在 K8s 中自己部署数据库。参考 cicd-developer skill 的 references/crossplane/README.md。
