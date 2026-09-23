# 镜像同步（base-images）

开源项目镜像通过 `DEV/base-images` 仓库统一同步到全集群 Harbor。

## 同步架构

```
开发者编辑 images.yaml → MR 合并到 main
  ↓
海外/SG/GKE/staging jobs 并行
  SG / US×3 / EU×3 / us-staging / eu-staging / GKE×2 — 直接从源仓库拉取
  ↓
CN relay + TKE jobs
  SG → 旧 CN Harbor → CN EKS×4（cn-tech/cn-prod/cn-dev/cn-staging）
  SG → TKE Harbor
```

所有镜像统一推到各集群 Harbor 的 `base/` 项目下，路径格式：`<harbor>/base/<name>:<tag>`。

## images.yaml 格式

```yaml
images:
  # ===== 分类注释 =====
  - source: docker.io/library/nginx:1.25-alpine    # 完整 registry/repo:tag
    name: nginx                                      # Harbor base/ 下的名称
    tag: 1.25-alpine                                 # tag（字符串）
```

### 字段说明

| 字段 | 格式 | 说明 |
|------|------|------|
| `source` | `registry/repo:tag` | 源镜像完整路径。Docker Hub 官方镜像用 `docker.io/library/<name>:<tag>`，第三方用 `docker.io/<org>/<name>:<tag>` |
| `name` | `<name>` 或 `<org>/<name>` | 推到 Harbor `base/` 下的镜像名。官方镜像直接用名称，第三方镜像保留组织前缀 |
| `tag` | 字符串 | 镜像 tag，数字型 tag 必须加引号（如 `"3.18"`、`"5.12.0"`） |

### 命名规范

```yaml
# 官方镜像（docker.io/library/）— name 直接用镜像名
- source: docker.io/library/redis:7.2-alpine
  name: redis
  tag: 7.2-alpine
# → Harbor: base/redis:7.2-alpine

# 第三方镜像（docker.io/<org>/）— name 保留 org 前缀
- source: docker.io/grafana/grafana:10.4.0
  name: grafana/grafana
  tag: "10.4.0"
# → Harbor: base/grafana/grafana:10.4.0

# 非 Docker Hub 镜像 — 同样支持
- source: quay.io/n8n/n8n:1.94.1
  name: n8n/n8n
  tag: "1.94.1"
# → Harbor: base/n8n/n8n:1.94.1
```

## 添加镜像步骤

### Step 1: 编辑 images.yaml

在 `DEV/base-images` 仓库中，找到合适的分类位置，添加镜像条目：

```yaml
  # ===== 新分类 =====
  - source: docker.io/grafana/grafana:10.4.0
    name: grafana/grafana
    tag: "10.4.0"
```

**注意事项：**
- 确认镜像在 Docker Hub 上存在且有 amd64 架构
- tag 必须是具体版本号，禁止 `latest`
- 数字型 tag 用引号包裹
- 一个开源项目可能需要多个镜像（如 ReportPortal 需要 6 个）

### Step 2: 提交 MR

```bash
cd ~/Project/A4x/base-images
git checkout -b feat/add-<app-name>
# 编辑 images.yaml
git add images.yaml
git commit -m "feat: add <app-name> images for deployment"
# 推送并创建 MR
```

### Step 3: MR 合并后自动同步

MR 合并到 `main` 后，Pipeline 自动触发：
1. **海外/SG/GKE/staging sync jobs**（~2-5 分钟）：SG、US/EU prod/tech/data、US/EU staging、GKE 并行从源拉取
2. **sync-cn-relay + sync-tke**（~5-10 分钟）：SG 中转到 CN legacy Harbor 和 TKE Harbor
3. **sync-cn**（~3-5 分钟）：CN EKS 内部同步到 cn-tech、cn-prod、cn-dev、cn-staging

总耗时约 10-15 分钟。具体 target/job 名称以 `DEV/base-images/.gitlab-ci.yml` 为准；不要维护手写集群数量。

### Step 4: 验证同步

在目标集群的 Harbor UI 搜索镜像，确认 `base/<name>:<tag>` 存在。

## 版本升级

升级开源项目版本需要两步：

### Step 1: 更新 images.yaml

在 `DEV/base-images` 仓库中添加新版本（保留旧版本，避免影响其他使用者）：

```yaml
  # 保留旧版本
  - source: docker.io/grafana/grafana:10.4.0
    name: grafana/grafana
    tag: "10.4.0"

  # 添加新版本
  - source: docker.io/grafana/grafana:11.0.0
    name: grafana/grafana
    tag: "11.0.0"
```

### Step 2: 更新 K8s overlay

等镜像同步完成后，修改对应 overlay 的 `kustomization.yaml`：

```yaml
images:
  - name: grafana
    newName: <HARBOR>/base/grafana/grafana
    newTag: "11.0.0"   # 更新版本
```

提交到应用配置仓库，ArgoCD 自动同步部署新版本。

## 清理旧版本

确认无集群使用后，可从 images.yaml 中删除旧版本条目。但 **不会** 自动清理 Harbor 中已有的镜像，需在 Harbor UI 手动删除（一般不需要）。

## 已同步镜像清单

当前 images.yaml 中已有的镜像（无需重复添加）：

- **Java**: maven (8/17), gradle (11), eclipse-temurin (8/21)
- **Node.js**: node (18/20 alpine, 20-slim)
- **Python**: python (3.11/3.12 slim)
- **Go**: golang (1.22/1.25/1.26)
- **基础**: alpine (3.18/3.19/3.20)
- **ReportPortal**: service-api, service-authorization, service-jobs, migrations, service-ui, service-index (5.12.0)
- **Nginx**: nginx (1.25-alpine)
- **中间件**: rabbitmq (3.11-management-alpine)
- **CI**: kaniko-executor (v1.23.2-debug)

部署前先检查 images.yaml，避免重复添加。
