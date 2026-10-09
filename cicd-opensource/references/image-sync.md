# 镜像同步（base-images）

开源项目镜像通过 `DEV/base-images` 仓库统一同步到全集群 Harbor。

## 同步架构

```
开发者编辑 images.yaml → MR 合并到 main
  ↓
海外/SG/GKE/staging jobs 并行
  SG / US×3 / EU×3 / us-staging / eu-staging / GKE×2 — 直接从源仓库拉取
  ↓
CN Tencent jobs / replication（按当前 DEV/base-images pipeline 与 Harbor 复制规则核验）
  prod: 100014919455 → harbor-cn.addx.live
  staging: 100052802231 → harbor-02231-cn-staging-pub.addx.live（SG/外部同步入口）
  tech-service 目标（待切换）: 100052802231 → harbor-02231-cn-tech-service-pub.addx.live（SG/外部入口）
  旧 AWS CN / 旧腾讯账号 job 不是当前目标证明
```

所有镜像统一推到各集群 Harbor 的 `base/` 项目下，路径格式：`<harbor>/base/<name>:<tag>`。

CN staging 的内网域名 `harbor-02231-cn-staging.addx.live` 和外部域名 `harbor-02231-cn-staging-pub.addx.live` 指向同一 registry，不是两个同步目标。SG 扇出使用 `-pub` 入口；目标集群部署镜像默认使用内网入口，例如 `harbor-02231-cn-staging.addx.live/base/nginx:1.25-alpine`。DNS 与 token realm 规则见 [Harbor 双入口合同](../../harbor/SKILL.md)，按本次客户端所在网络验证 registry 和 token 服务均可达。

旧 `harbor-cn-staging.addx.live` 是同一实例的兼容入口，保留兼容（当前 runner `HARBOR_REGISTRY` 与 Image Updater 仍使用它）。AWS `harbor-80144-cn-staging` / `harbor-80144-cn-dev` 已退役，不再作为扇出目标。切换前核验实际 runner `HARBOR_REGISTRY`、同步目的端及精确 hostname 凭据；内网 pull secret 必须有内网域名的 auth key，由平台同步机制补齐并验证后再采用。技能默认值不自动迁移现存镜像或 Secret，也不证明当前 pipeline 已更新；历史 runner 快照和新部署回执见 [CN 迁移证据](../../cicd-developer/references/cn-tencent-migration.md)。

CN tech-service 的外部同步目标为 `harbor-02231-cn-tech-service-pub.addx.live`，集群镜像目标为 `harbor-02231-cn-tech-service.addx.live`。这是同一实例双入口的目标命名，尚未切换；不得声称当前 base-images job、Harbor replication、token realm、PrivateDNS 或 pull secret 已使用这些入口。先按 [Harbor tech-service 目标说明](../../harbor/SKILL.md) 核验并完成迁移，不能把私网域名直接填到 SG 公网同步端。

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
2. **CN Tencent 同步/复制链路**：按精确账号、registry 核验 pipeline target 与实际复制执行；不能因旧 sync-cn / sync-tke job 成功而认定新账号 staging/tech-service 已收到镜像
3. 在本次目标 Harbor 读取 `base/<name>:<tag>` manifest/digest，确认架构；未分发到目标时 STOP 并补对应同步链路

同步耗时以本次执行为准。具体 target/job 名称以 `DEV/base-images/.gitlab-ci.yml` 为准；不要维护手写集群数量。

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
