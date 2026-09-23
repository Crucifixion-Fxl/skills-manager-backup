---
name: new-stateful-service
description: 从零部署一个有状态服务（PVC + StatefulSet 或单节点 self-managed DB/CH 类）。跟 new-service 区别：进程内持久化数据，重启**不**安全，必须妥善处理 PVC。
---

# Workflow：new-stateful-service

本文的主运行态 Application 不代表一仓只能有一个 Application。若权限职责或生命周期
需要分离，先提出独立 runtime/infra 渲染与唯一资源管理者方案，再为各 Application
登记已有且批准的 Project、source/path/destination 合同；不能隐式多生成一个 Application。
平台 claim 可按现有合同留 runtime，owner 专属 Project 未获批准前不可使用。
存量资源拆分需独立 ownership/prune/finalizer/回滚评审，不能套用只改 Project 的迁移。
详见 [权限与部署划分合同](../references/data/permission-boundaries.yaml)。

## 目的

部署一个 **进程内有状态** 的服务——典型例子 ClickHouse 单节点、Elasticsearch single-node、Nacos、Redis（自管而非 ElastiCache）、Vector StatefulSet 等。

跟 `new-service.md`（无状态）核心区别：
- 用 **StatefulSet**（不是 Rollout）—— 因 StatefulSet 有稳定 pod 名 + 顺序起停 + PVC 1:1 绑定
- 必有 PVC（持久化数据）
- replicas 一般 1（单节点 self-managed）；多副本需配协调组件（如 ClickHouse Keeper），不在自助模板
- **ClusterIP only**，**永不** 公网暴露 Ingress（很多 native 协议无 auth 层）

如果是需要"原生 K8s Deployment + emptyDir/tmpfs"那种"假有状态"（实际重启安全）→ 用 `new-service.md`。

## 进入条件

- `docs/deployment/cd-requirements.md` 存在并填完，列了 stateful 资源
- 工作目录是目标应用 git 仓库
- 当前自助范围只有 `kind=clickhouse`；其它 kind 走 ops-led 流程，不能因为存在成本档位就现编 manifest
- 新部署应用的 **staging / tech-service** target（6 集群 us/eu/cn-staging + tech-us/eu/cn）
  不应用 StatefulSet+PVC 自托管 ClickHouse / Redis / Nacos / Elasticsearch / mysql / mongo 等
  数据类中间件（hard-rule #29 + #30）；数据需求必须通过 `kind: Database` 在平台共享实例上申请
  **per-app** 凭证（凭证落 `secret/{env}/{platform}/application/{app}/{key}`，region-less，**无**
  shared-middleware 命名空间）。对应 Composition 未就绪前产 Ops Todo。
  **prod** target 没有 shared-middleware，数据库走 app-owned 托管 RDS/Aurora（cost-tiering prod
  tier），同样不应自托管 StatefulSet 数据库。
  例外（#30 软约定，需在 cd-requirements.md 写明**理由 + 备份/HA/升级方案**走 review）：
  平台 shared-middleware 自身、上游只发 StatefulSet 形态的第三方派生组件、特殊一致性/协议需求、
  明确标注的 legacy 迁移。本 workflow 仍用于这些**正当的**自管 stateful 服务。

## Step 1. 读 cd-requirements.md + 解析

[precondition]
  - cd-requirements.md 存在

[action]
  - 提取（同 `new-service.md` Step 1）：
    - `$app`、`$domain`、`$app_type`、`$targets[]`、`$language`
  - 额外：
    - `$kind`             stateful 类型（当前仅支持 clickhouse）
    - `$db_name`          ClickHouse 启动时创建的 database 名
    - `$clickhouse_image_tag` 不可变 ClickHouse 版本（如 `25.8.23.13`，禁止 `latest`）
    - `$pvc_size`         按 cost-tiering/<kind>.yaml 取或用户给
    - `$pvc_storage_class` 按 cost-tiering/<kind>.yaml 取；缺省时按目标集群 cloud 默认值回退

[validate]
  - `$kind == clickhouse` 且 `references/cost-tiering/clickhouse.yaml` 存在；否则 STOP，产
    Ops Todo："stateful kind '$kind' has no bundled recipe"。
  - `$pvc_size` 非空；prod 如果 cost-tiering 给的是 null，必须用容量公式估算后写明来源
  - `$pvc_storage_class` 非空；禁止留空让集群默认 StorageClass 隐式接管
  - `$clickhouse_image_tag` 非空且不是 `latest`
  - 把 `$targets[]` 拆成：
    - `$shared_middleware_targets[]`：`$target.cluster` 是 shared-middleware 集群（6 个：
      `{us,eu,cn}-eks-staging` + `{us,eu,cn}-eks-tech-service`；**按 cluster 名判，不用 `$target.env`**——
      tech-service env=prod，见 #29），且本应用不是平台 shared-middleware 自身，也不是 cd-requirements.md
      明确标注的 legacy/迁移/第三方派生例外。这些 target 不生成 StatefulSet / PVC / ArgoCD Application；
      改为按 `references/shared-middleware/README.md` 的 `kind: Database` 流程申请 per-app 凭证
      （per-app 路径 `secret/{env}/{platform}/application/{app}/{key}`），就绪门禁未过则产 Ops Todo。
    - `$managed_targets[]`：**非 shared-middleware 集群 target（真 prod `*-eks-prod`/`*-prod-data`/TKE/dev；
      tech-service 不在此列）**，或明确允许的 shared-middleware/legacy/第三方派生例外
      （#30 软约定，cd-requirements.md 已写明理由 + 备份/HA/升级方案），继续 Step 2 之后的 StatefulSet/PVC 流程。
  - 如果 `$managed_targets[]` 为空：STOP 在 shared-middleware 输出/Ops Todo 后结束；
    不要继续 Step 2。
  - 当前 ClickHouse base recipe 只有一组资源/PVC 槽位：`$managed_targets[]` 必须恰好一个。
    若多于一个，STOP 并先完成一个 target；其余 target 走 `add-target-cluster.md` 的受控扩展，
    不要把多 target tier 混进一个 base manifest。
  - `$managed_targets[]` 非空时继续 Step 2；不要再次 STOP。

[output]
  - 内存变量；后续 Step 2 起只处理 `$managed_targets[]`

## Step 2. 决定 tier

[precondition]
  - Step 1 完成

[action]
  - 对每 `$target`：
    - 查 `cost-tiering/<$kind>.yaml -> $target.env` 取 CPU / Memory / PVC / backup / service_type 等
    - 解析 `$target.pvc_storage_class`：
      - 优先用 cost-tiering 的 `pvc_storage_class`
      - 如表里缺失，按 cluster.cloud 默认：AWS=`gp3`，GCP=`standard-rwo`，Tencent=`cbs`
      - 仍无法决定 → STOP 问用户；不要省略 `storageClassName`
    - prod env → 跑 `_global.yaml -> prod_self_check`
    - 查 `clusters.yaml` 取标准字段：
      `account_id`、`region`、`harbor_url`、`runner_tags`、`argocd_apps_dir`、`build_mode`、`vault_css`
    - 计算部署侧字面路径 `$target.harbor_image_path = {$target.harbor_url}/cicd/{$target.env}-{$target.region}/{$app}`
      - 自助构建镜像必须走这条业务路径；**部署侧永远用字面 host**（overlay `newName` + Application `image-list` / `kustomize.images`）。
      - 计算 CI 侧 push 路径 `$target.ci_image_path`（见 hard-rules #25）：B 默认 `${IMAGE_BASE}/{$target.env}-{$target.region}/{$app}`（host 由 `$target.runner_tags` 对应 runner 注入）；A（字面 host，单 target / 老应用）`= $target.harbor_image_path`。有状态服务通常单 target，A/B 近等价。
      - 不变量：`$target.runner_tags` 按 clusters.yaml 解析出的 harbor_url == `$target.harbor_url`。
      - 第三方现成镜像（如 ClickHouse）走 Harbor `base/` 同步，不走本业务路径，也不生成业务 CI job。
      - 当前 ClickHouse 固定为第三方 base 镜像：
        `$target.harbor_base_clickhouse_image=$target.harbor_url/base/clickhouse/clickhouse-server`，
        `$target.clickhouse_image_tag=$clickhouse_image_tag`。

[validate]
  - 每 target 字段就位
  - `$target.pvc_storage_class` 已解析，且写入 StatefulSet `volumeClaimTemplates[].spec.storageClassName`
  - 自助构建场景下，每个 `$target.harbor_image_path` 匹配 `/cicd/(dev|staging|pre|prod)-[a-z]+/{$app}$`
  - `service_type == ClusterIP`（**不允许** LoadBalancer / NodePort）

[output]
  - `$targets[]` 完整

## Step 3. 写 base K8s manifest

[precondition]
  - Step 2 完成

[action]
  - 创建 `k8s/base/`。
  - 用 `recipes/clickhouse/statefulset.yaml.tmpl` 写 `k8s/base/statefulset.yaml`，填：
    `{{app}}=$app`、`{{namespace}}=$target.namespace`、`{{db_name}}=$db_name`、
    `{{cpu_requests}}=$target.cpu_requests`、`{{memory_requests}}=$target.memory_requests`、
    `{{cpu_limits}}=$target.cpu_limits`、`{{memory_limits}}=$target.memory_limits`、
    `{{ephemeral_storage_requests}}=$target.ephemeral_storage_requests`、
    `{{ephemeral_storage_limits}}=$target.ephemeral_storage_limits`、
    `{{pvc_size}}=$target.pvc_size`、`{{storage_class}}=$target.pvc_storage_class`。
    base StatefulSet 不承载镜像 slot；镜像只能由 Step 4 的 ClickHouse 专用 overlay 覆盖。
  - 用 `recipes/clickhouse/service.yaml.tmpl` 写 `k8s/base/service.yaml`，填
    `{{app}}=$app`、`{{namespace}}=$target.namespace`。
  - 用 `recipes/clickhouse/kustomization.yaml.tmpl` 写 `k8s/base/kustomization.yaml`。
  - 不要 inline YAML，也不要把 ClickHouse 模板改造成 Nacos、Vector 或其它 kind。

[validate]
  - 三个文件都存在
  - `kustomize build k8s/base/` 成功

[output]
  - k8s/base/statefulset.yaml + service.yaml + kustomization.yaml

## Step 4. 写 per-target overlay

按 `$managed_targets[]` 循环。

[precondition]
  - Step 3 完成

[action]
  - 用 `recipes/clickhouse/kustomization-overlay.yaml.tmpl` 写
    `k8s/overlays/{$target.env_keyword}/kustomization.yaml`，填：
    `{{namespace}}=$target.namespace`、`{{app}}=$app`、`{{env}}=$target.env_keyword`、
    `{{harbor_image_path}}=$target.harbor_base_clickhouse_image`、
    `{{image_tag}}=$target.clickhouse_image_tag`。
    这个专用 overlay 的 `images[].name: clickhouse-server` 必须和 base StatefulSet 的
    placeholder 名完全一致；不要改用通用 app-name overlay。
  - 用 `recipes/clickhouse/password-generator.yaml.tmpl` 写
    `k8s/overlays/{$target.env_keyword}/clickhouse-password-generator.yaml`，填
    `{{app}}=$app`、`{{namespace}}=$target.namespace`。
  - 用 `recipes/clickhouse/password-external-secret.yaml.tmpl` 写
    `k8s/overlays/{$target.env_keyword}/clickhouse-password-external-secret.yaml`，填
    `{{app}}=$app`、`{{namespace}}=$target.namespace`。
    Password Generator 产生 `password` key，ExternalSecret 写入
    `{$app}-clickhouse-secret`；StatefulSet 只能消费这同一个 secret/key 对。
  - ClickHouse 是固定第三方 base 镜像：不写业务 `cicd/` 路径、不写 CI build、也不使用
    `staging-latest` / `prod-latest`。目标 tag 必须是 `DEV/base-images` 已同步到 Harbor
    `base/` 的不可变 ClickHouse tag。
  - overlay 已内置 StatefulSet pod template 的 env label patch；不要添加 Rollout patch 或
    另一个通用 overlay。

[validate]
  - `kustomize build k8s/overlays/{$target.env_keyword}/` 成功；渲染出的 StatefulSet pod
    template 含 env label，镜像恰好是
    `$target.harbor_base_clickhouse_image:$target.clickhouse_image_tag`
  - 渲染出的 StatefulSet 的 `CLICKHOUSE_PASSWORD` 必须引用
    `{$app}-clickhouse-secret` 的 `password` key；同一 overlay 内必须有对应 Password
    Generator 和 ExternalSecret，且 generatorRef 指向 `{$app}-clickhouse-password-gen`
  - `bash "$skill_root/validators/validate.sh" k8s/overlays/{$target.env_keyword}/` PASS

[output]
  - k8s/overlays/{$target.env_keyword}/kustomization.yaml + clickhouse password chain

## Step 5. 准备 Application contract；真实镜像就绪后再写独立 MR

[precondition]
  - Step 4 完成
  - 用户有 argocd-apps 仓库 checkout；没有则本步骤只产 Ops Todo

[action]
  - 先按 `new-service.md` Step 5 解析 pending Application contract，核对目标集群中
    已有且获批准的 project、repo/path/revision/destination/resource（含 hooks）与
    namespace 前置；不得默认使用 default 或自行新建 AppProject。缺权限先交付精确
    Ops Todo，平台独立权限 MR 合并同步后再注册 consumer。
  - 写文件前必须已验证每个目标 Harbor 的真实镜像 tag、immutable digest 和所需架构。
    任一未就绪时只保留 pending contract / Ops Todo，不渲染/提交 Application 文件或 MR；
    继续 Steps 6-9 准备 app-repo 产物，完成获准的构建/镜像同步后回到本步骤。
  - 仅在镜像证据和 project/namespace 前置全部就绪后，用 `recipes/argocd/application.yaml.tmpl`
    的下述对应镜像分支，通用槽映射见 `new-service.md` Step 11，
    `{{project}}` 使用上述已核验的 `$target.project`；不要把 Step 5 的 pending
    `first_real_git_sha` 当成可写入 Application 的真实版本。
  - 自助构建镜像场景必须填：
    - `{{harbor_image_path}}=$target.harbor_image_path`
    - `{{image_seed}}=<first_real_git_sha>`（生成文件前已证明 CI 推送到目标 Harbor 的真实 SHA）
    - `{{app_platforms}}=` 按 `$target.build_mode`：dual-arch →
      `linux/amd64,linux/arm64`；single-arch-amd64 → `linux/amd64`
    - Application 使用模板生成的 Image Updater annotations 与 `write-back-method=argocd`；
      不创建 app-owned/中心 CR、ApplicationSet、merger、Git credential 或 `.argocd-source-*`
    - **飞书通知注解（核心契约，所有 app 默认必带，非 opt-in）**：Application 必须带
      `notifications.argoproj.io/subscribe.on-deployed.feishu-ops` / `.on-health-degraded.feishu-ops` /
      `.on-sync-failed.feishu-ops`（值 `""`）；`check_argocd_application.py` 也强制这 3 条。
    - 首次接入顺序 gate：先交付可审查的 app repo k8s + CI + Dockerfile MR；
      在已有准确授权和仓库门禁满足后合并，跑目标分支 pipeline，验证每个
      `$target.harbor_image_path` 的真实 Git SHA tag、digest 与 `$target.app_platforms`。
      然后才生成/提交独立 Application MR，`{{image_seed}}` 直接填已验证 SHA；
      `0000000`、`PENDING`、`latest` 或虚构值不得作为 Application seed。
      合并和同步都必须在当前用户授权范围内并满足对应审批门禁；已有准确授权继续有效，
      工作流步骤本身不授予权限。未获授权时交付可审查的 MR 与 pending handoff。
    - `force-update` 默认不生成；只有证明 `status.summary.images` 不可靠时才允许为准确 alias 添加。
  - 第三方现成镜像场景：
    - 如果镜像由 Image Updater 管业务版本，仍按上面 Application 契约填。
    - 如果镜像版本固定在 base/ 且不由 Image Updater 管，本步骤必须先取得用户确认：
      "这是 pinned third-party image，不使用 Image Updater contract"，并在 `cicd.md`
      记录原因；已有本会话准确确认继续有效，没有确认才 STOP，不要悄悄产出缺
      Image Updater 的 Application。当前 ClickHouse recipe 走此分支。
    - **pinned Application 模板变体**：保留模板的 metadata labels、finalizer、全部
      Feishu notifications、project、source repo/revision/path、destination 和 syncPolicy；
      在写盘前省略全部 `argocd-image-updater.argoproj.io/*` annotations 与
      `spec.source.kustomize.images`（空 kustomize 也省略）。此变体不使用
      `harbor_image_path` / `image_seed` / `app_platforms` 这些 Image Updater 专属槽，
      不生成伪造 Git SHA 或 recovery override。镜像版本只由已审查的 ClickHouse
      overlay `images` 声明，image name 为 `clickhouse-server`，不能误用业务 app alias。
    - 在目标 `$target.harbor_base_clickhouse_image` 验证准确不可变
      `$target.clickhouse_image_tag`（如 `25.8.23.13`）、manifest digest 和该 target
      `build_mode` 要求的全部平台；同时核对 DEV/base-images 已登记并同步该 exact
      image/tag。`latest`、浮动 minor tag、错误 Harbor 或缺任一平台均不允许注册。
      把 image/tag/digest/platforms、base-images 来源与用户确认记录写入 `cicd.md`。
    - 先交付/按授权合并包含 pinned overlay 的 app MR，再提交独立 Application MR；
      不要求业务 CI build、Git SHA tag 或 Image Updater recovery seed。合并/同步仍
      受相同授权、project/namespace 与仓库审批门禁约束。

[validate]
  - 已生成 Application 时，`python3 "$skill_root/validators/check_argocd_application.py" argocd-apps/{$target.argocd_apps_dir}/` 退出 0
  - 已生成 Application 时，对新增文件运行 `check_argocd_namespace_creation.py <cluster-dir> --application <changed-app.yaml>`，
    并通过 argocd-apps 当前 CI 的 boundary/application contract 检查。
  - 自助构建镜像场景下，Application `image-list` / `spec.source.kustomize.images` / overlay `newName` 三者是同一字面 host，且 CI runner 解析出的 Harbor == 该 host（见 hard-rules #25）
  - 自助构建 / Image Updater 分支：生成/提交 Application 前，目标 Harbor 已有
    真实 Git SHA tag、digest 与所需架构，且 recovery seed 精确匹配。
  - 已确认 pinned third-party 分支：目标 Harbor 的准确不可变版本、digest、所需
    架构和 base-images 来源均有证据；rendered StatefulSet 镜像等于已验证 image:tag，
    Application 无 Image Updater annotations 或 kustomize image override，且保留
    notifications/finalizer/syncPolicy。此分支不检查 Git SHA/recovery seed。
  - 任一分支的必需证据缺失时没有 Application 文件/MR，只有 pending handoff。
  - 本步骤 deferred 时不运行不存在文件上的 ArgoCD validator，也不宣称已验证；
    待镜像就绪返回本步骤后必须完成以上 Application 和 namespace 门禁。

[output]
  - argocd-apps/{$target.argocd_apps_dir}/{$app}-{$target.env_keyword}.yaml 或 Ops Todo 行

## Step 6. 写 GitLab CI

[precondition]
  - Step 3 完成

[action]
  - 自助构建场景（自己写 Dockerfile）：同 new-service Step 6
    - 每个 target 用 `$target.ci_image_path` 填 `{{harbor_image_path}}`（B 默认 `${IMAGE_BASE}/<env>-<region>/<app>`；单 target / 老应用可用字面 host）
    - 禁止 `library/{$app}` 或扁平 `cicd/{$app}`
  - **第三方现成镜像**（如 ClickHouse `clickhouse/clickhouse-server`）：
    - 不需要 GitLab CI build，但需要把镜像同步到 Harbor base/（避 GFW）
    - 先核对 `DEV/base-images/images.yaml` 和目标 Harbor 已同步的 exact tag/digest/platforms；
      已满足则记录证据，不重复建 MR。缺少才代写该 exact 版本的 base-images MR，
      在授权/权限不足时产精确 Ops Todo；不在本仓添加业务 CI。
    - 不使用浮动 `25.8`，镜像同步项必须是已选择的完整 `$target.clickhouse_image_tag`。

[validate]
  - 视情况；如果不写 CI，跳到 Step 7

[output]
  - .gitlab-ci.yml（如有）或 Ops Todo 行

## Step 7. 写 Dockerfile（如自助构建）

[precondition]
  - Step 6 完成
  - 已确认镜像来源是自助构建还是第三方 base/ 镜像

[action]
  - 自助构建场景：同 `new-service.md` Step 7，按语言选择 `recipes/ci/dockerfile-<lang>.txt`
  - 第三方现成镜像场景：不写 Dockerfile；确认已有目标 Harbor base/ 镜像证据，或
    有缺失 exact tag/digest/platforms 的镜像同步 Ops Todo

[validate]
  - 自助构建场景：Dockerfile 存在；如有 CN target，`FROM` 不直接引用 Docker Hub
  - 第三方现成镜像场景：镜像证据完整，或 Ops Todo 含准确 base-images 同步项

[output]
  - Dockerfile 或 Ops Todo 行

## Step 8. 全量 validator

[precondition]
  - Step 3-7 完成

[action]
  - `bash "$skill_root/validators/validate.sh" k8s/`
  - 如 Step 5 写过 argocd-apps，跑 `python3 "$skill_root/validators/check_argocd_application.py" <argocd-apps-subdir>`
  - 如 Step 5 只产 Ops Todo，确认 Ops Todo 明确包含 argocd-apps MR 路径、Application
    validator 交接要求和阻塞原因；不要在本地假装 ArgoCD validator 已经通过
  - 额外：手动核查 service.yaml 的 type 都是 ClusterIP（**绝不** LoadBalancer / NodePort）

[validate]
  - `bash "$skill_root/validators/validate.sh" k8s/` 退出 0
  - 如 Step 5 写过 argocd-apps，`python3 "$skill_root/validators/check_argocd_application.py" <argocd-apps-subdir>` 退出 0
  - 如 Step 5 只产 Ops Todo，Ops Todo 不为空且包含 validator handoff
  - 没 LoadBalancer / NodePort Service

[output]
  - validator 日志

## Step 9. 更新文档 + summary

[precondition]
  - Step 8 通过

[action]
  - cd-requirements.md 补 PVC 容量估算公式（如 ClickHouse `qps × bytes × retention × 86400 × 0.1 × 5`）
  - cicd.md 加"为什么 StatefulSet / 单节点 / 不公网暴露"说明
  - Ops Todo（按需）：
    - 第三方镜像同步到 Harbor base/
    - PVC backup 策略（业务自评是否需要 `BACKUP TO S3` 定时 Job）
  - 交付 app-repo MR；若 Step 5 仍 deferred，列明目标 Harbor、所缺 tag/digest/platform
    证据与 Application handoff。用户已授权继续部署且相应门禁满足时，完成构建/同步
    并返回 Step 5；仅请求配置准备时交付该具体 MR 和 pending contract。
  - summary

[validate]
  - summary 已打印；没有用 pending contract 冒充已创建 Application 或已部署

[output]
  - 最终用户消息

## 出口

ArgoCD sync 后：
- StatefulSet pod 创建（按 replicas 顺序起）
- PVC 自动 provision（按 Step 2 解析的 `$target.pvc_storage_class`：AWS=`gp3` / GCP=`standard-rwo` / Tencent=`cbs`，或 cost-tiering 指定值）
- Headless Service 给 pod 稳定 DNS `{{app}}-0.{{app}}-headless.{{ns}}.svc.cluster.local`
- ClusterIP Service 给负载均衡（如果有多副本）

**常见踩坑**：
- 单副本 + RWO PVC + `strategy=RollingUpdate` → 死锁（新 pod 抢不到 PVC，老 pod 不让位）。StatefulSet 默认 RollingUpdate 但 partition 控制；单副本一般 OK，但如果有自定义 strategy 改 `Recreate`
- 用户后续多副本扩展 → 直接改 `replicas` 不够（需要 Keeper / Sentinel / Raft 协调），跳到 ops-led 多节点流程
- PVC 永远 Pending → storageClass 不存在 / EBS quota 满 / 错 zone（PVC 跨 AZ 不能挂）
- pod 一直 ImagePullBackOff（第三方镜像）→ Harbor base/ 没同步该镜像，跳 `troubleshooting/image-pull-failure.md`
