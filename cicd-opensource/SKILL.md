---
name: cicd-opensource
description: 协助开发者将开源项目（DockerHub 上已有镜像）部署到公司 K8s 集群。通过 base-images 仓库同步镜像到内部 Harbor，编写 K8s 配置和 ArgoCD Application，管理密钥和对应云资源。当开发者需要部署开源中间件、工具或服务（如 Redis、RabbitMQ、Grafana、n8n 等）时使用。
---

# cicd-opensource

协助开发者将开源项目部署到公司 K8s 集群。与 cicd-developer 的区别：**不需要写 Dockerfile 和 CI 构建**，镜像来自 DockerHub 等公共仓库，通过 base-images 同步到内部 Harbor。

## Description

```
DockerHub 镜像 → base-images/images.yaml MR → 全集群 Harbor base/ 同步 → ArgoCD → K8s Pod
```

**核心流程：** 在 `DEV/base-images` 仓库的 `images.yaml` 中添加镜像条目，合并 MR 后自动同步到 fleet 的 Harbor `base/` 项目（实际目标以当前 pipeline 和目标 Harbor 回读为准，当前 CN 走腾讯云链路）。然后编写 K8s 配置和 ArgoCD Application 完成部署。

**与 cicd-developer 的关键区别：**

| 维度 | cicd-developer | cicd-opensource |
|------|---------------|-----------------|
| 镜像来源 | 代码构建（Kaniko） | DockerHub / 公共仓库 |
| Harbor 项目 | `cicd/<env>/<app>` | `base/<image>:<tag>` |
| 需要 Dockerfile | 是 | 否 |
| 需要 .gitlab-ci.yml | 是 | 否（仅 base-images 仓库有 CI） |
| Image Updater | 自动更新（commit SHA tag） | 不使用（固定版本 tag） |
| 版本升级 | 代码推送自动构建 | 手动更新 images.yaml + overlay |
| K8s 配置仓库 | 应用代码仓库内 `k8s/` | 独立仓库或 ops 仓库 |

## Rules

1. **禁止在 CI 中直接 `kubectl apply` / `helm install`** — 部署由 ArgoCD 完成
2. **禁止在代码中硬编码密钥** — 必须通过 Vault + ExternalSecret
3. **Crossplane providerConfigRef 必须指定 `kind: ClusterProviderConfig`** — 不写 kind 会导致资源创建失败且无报错
4. **ArgoCD Application 不要放在应用仓库中** — 统一存放在 `DEV/argocd-apps` 仓库
5. **应用 Git 仓库必须授权 `Argocd-deploy` 用户为 Reporter** — 否则 ArgoCD 无法拉取代码
6. **禁止自建 Harbor imagePullSecret** — 使用平台 Kyverno 同步的 `harbor-registry-secret`；采用新 registry 域名前，核验其 Docker `auths` 包含精确 hostname，缺失则先由平台补齐并验证，不能假定旧域名凭据覆盖新域名
7. **开源镜像统一通过 base-images 仓库同步** — 不要手动 docker push 到 Harbor，不要在 CI 中拉取外部镜像
8. **镜像版本必须使用具体 tag** — 禁止用 `latest`，使用明确版本号（如 `3.11-management-alpine`、`5.12.0`）
9. **CN 集群 Pod 镜像必须使用 Harbor base 路径** — `${HARBOR_REGISTRY}/base/<image>:<tag>`
10. **ArgoCD Application 不配置 Image Updater 注解** — 开源镜像使用固定版本，升级通过更新 images.yaml + overlay kustomization
11. **新应用使用 Rollout 替代 Deployment** — 除非开源项目有特殊要求（如 StatefulSet）
12. **PVC 数据持久化必须评估** — 有状态应用（数据库、消息队列）必须配置 PVC 或使用 Crossplane 托管服务

## 集群环境

不按仓库目录数推导活跃 fleet。AWS CN 已弃用；当前 CN prod 账号为 `100014919455`，staging/tech-service 账号为 `100052802231`。权威信息以 `k8s/clusters/`、`argocd-apps/`、`base-images/.gitlab-ci.yml` 为准；能读取 `cicd-developer` 时，必须先用它的 `references/data/{clusters,env-keywords}.yaml` 解析集群、Harbor、runner tag、namespace，并运行部署准入检查。仅 allowed 可新建/扩展；blocked、retired 或缺失状态均 STOP。旧 cn-main staging 应用须按实际 Application 排查，不能因为 keyword 更新就假定迁移完成。

| 集群 | 区域 | ArgoCD | Harbor | Runner Tags |
|------|------|--------|--------|-------------|
| sg-devops | SG | argocd-sg-devops.addx.live | harbor-12571-sg-devops.addx.live | `sg-amd64`, `sg-arm64` |
| us-eks-prod | US | argocd-us.addx.live | harbor-30257-us-prod.addx.live | `us-prod-amd64`, `us-prod-arm64` |
| eu-eks-prod | EU | argocd-eu.addx.live | harbor-74031-eu-prod.addx.live | `eu-prod-amd64`, `eu-prod-arm64` |
| us-eks-tech-service | US | argocd-us-tech-service.addx.live | harbor-00249-us-tech.addx.live | `us-tech-amd64`, `us-tech-arm64` |
| eu-eks-tech-service | EU | argocd-eu-tech-service.addx.live | harbor-01084-eu-tech.addx.live | `eu-tech-amd64`, `eu-tech-arm64` |
| us-prod-data | US | argocd-us-data.addx.live | harbor-76949-us-data.addx.live | `us-data-amd64`, `us-data-arm64` |
| eu-prod-data | EU | argocd-eu-data.addx.live | harbor-76949-eu-data.addx.live | `eu-data-amd64` |
| us-staging | US | argocd-us-staging.addx.live | harbor-39070-us-staging.addx.live | `us-staging-amd64`, `us-staging-arm64` |
| eu-staging | EU | argocd-eu-staging.addx.live | harbor-39070-eu-staging.addx.live | `eu-staging-amd64`, `eu-staging-arm64` |
| cn-tke-staging (100052802231 / cls-riukakjb；CN 唯一 dev/staging) | CN | argocd-cn-staging.addx.live | harbor-02231-cn-staging.addx.live（内网） | `tke-cn-staging-amd64`（兼容 `cn-staging-amd64`，无 arm64） |
| cn-tke-tech-service (100052802231，目标域名待切换，业务准入 blocked) | CN | argocd-cn-tech-service-tke.addx.live | harbor-02231-cn-tech-service.addx.live | 待核验，禁止复用旧 cn-tech tag |
| cn-k8s (TKE prod，100014919455) | CN | argocd-cn-k8s.addx.live | harbor-cn.addx.live | `tke-amd64` |
| us-tech-service-gke | US | argocd-us-tech-service-gke.addx.live | harbor-a4xt-us-tech.addx.live | `gcp-tech-amd64` |
| us-prod-gke | US | argocd-us-prod-gke.addx.live | harbor-a4xp-us-prod.addx.live | `gcp-prod-amd64` |

CN staging 部署镜像默认用表中的内网域名；SG 同步和获准的外部访问用 `harbor-02231-cn-staging-pub.addx.live`，两者是同一 registry。采用内网域名前核验精确 auth key，现存镜像和 Secret 不自动迁移；DNS、token realm 与兼容规则见 [Harbor 双入口合同](../harbor/SKILL.md)。

CN tech-service 表中为目标命名，尚未切换；外部同步目标为 `harbor-02231-cn-tech-service-pub.addx.live`。使用前核对实际 GitOps、DNS/TLS、认证 realm、精确 auth key 和 runner。Builder Vault 服务入口目标为 `vault-cn.builder.addx.live`，不改变该集群现有 Ops `vault-backend`；完整七入口与迁移状态见 [CN 清单](../../infrastructure/k8s-ops/references/cn-tencent-inventory.md)。

## Vault 实例

| 区域 | 地址 | 密钥路径格式 |
|------|------|-------------|
| Ops US | vault-us-new.addx.live | 历史应用可保留 `secret/<app>/<env>`；新规范优先看 cicd-developer/vault-kv-manager |
| Ops EU | vault-eu.addx.live | 同上 |
| Ops CN | vault-cn.addx.live | 同上 |
| Builder US/EU | vault-{us,eu}.builder.addx.live | 新 app-owned 路径 `secret/{dev,staging,pre,prod}/app/<app>/<key>` |
| 当前 TKE CN staging | 集群内 `http://vault-active.vault.svc:8200`，CSS `vault-backend` | 同一 app 路径规则；不因 CSS 名称推断 ops Vault |

## 话题索引

| 需求 | 文档 | 关键点 |
|------|------|--------|
| **部署完整流程** | [deploy-workflow.md](references/deploy-workflow.md) | 5 步流程：镜像同步 → 前置配置 → K8s 配置 → ArgoCD 注册 → 验证 |
| **镜像同步（base-images）** | [image-sync.md](references/image-sync.md) | images.yaml 格式、MR 流程、同步架构、版本升级 |
| **K8s 配置模板** | [k8s-templates.md](references/k8s-templates.md) | Kustomize base/overlay、Rollout/StatefulSet、Ingress、ConfigMap |
| **常见开源项目配置** | [common-apps.md](references/common-apps.md) | 各类开源项目的部署模式和注意事项 |
| **排查部署问题** | [troubleshooting.md](references/troubleshooting.md) | 镜像拉取失败、配置问题、PVC 问题 |

## Examples

### Bad

```yaml
# ❌ 直接用 DockerHub 镜像（CN 集群不可达，且绕过了镜像管控）
image: docker.io/grafana/grafana:10.4.0

# ❌ 手动 docker push 到 Harbor（绕过 base-images 同步流程）
docker push harbor-xxx.addx.live/base/grafana:10.4.0

# ❌ 使用 latest tag（不可追溯、不可复现）
image: grafana/grafana:latest

# ❌ 配置 Image Updater 注解（开源镜像不走 Image Updater）
argocd-image-updater.argoproj.io/image-list: app=harbor-xxx/base/grafana
```

### Good

```
用户：帮我部署 n8n 到 staging-us

AI：
1. 在 DEV/base-images images.yaml 添加 n8n 镜像（查阅 image-sync.md）
2. 创建 K8s 配置 base/ + overlays/staging-us/（查阅 k8s-templates.md）
3. 生成 ArgoCD Application YAML，不含 Image Updater 注解（查阅 k8s-templates.md）
4. 提示用户添加 Argocd-deploy 为 Reporter，联系运维配置 Vault 和 DNS
```

```yaml
# ✅ 正确引用 Harbor base 镜像（overlay kustomization 中）
images:
  - name: n8n
    newName: harbor-39070-us-staging.addx.live/base/n8n
    newTag: "1.94.1"
```
