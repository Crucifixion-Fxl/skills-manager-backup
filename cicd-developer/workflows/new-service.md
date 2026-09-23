---
name: new-service
description: 从零部署一个无状态服务。产出 k8s manifest、ArgoCD Application、GitLab CI、Dockerfile，把 workflow 做不了的事进 Ops Todo。
---

# Workflow：new-service

本文的主运行态 Application 不代表一仓只能有一个 Application。若权限职责或生命周期
需要分离，先提出独立 runtime/infra 渲染与唯一资源管理者方案，再为各 Application
登记已有且批准的 Project、source/path/destination 合同；不能隐式多生成一个 Application。
平台 claim 可按现有合同留 runtime，owner 专属 Project 未获批准前不可使用。
存量资源拆分需独立 ownership/prune/finalizer/回滚评审，不能套用只改 Project 的迁移。
详见 [权限与部署划分合同](../references/data/permission-boundaries.yaml)。

## 目的

端到端首次部署一个无状态服务到一个或多个集群。"无状态"= 无 PVC、无进程内持久化、重启安全。如果服务需要 PVC，用 `new-stateful-service.md` 不要走这个。

本 workflow 先产出服务级资源（Rollout / Service / ExternalSecret / Kustomize / ArgoCD Application / Dockerfile / CI）。
如果 `docs/deployment/cd-requirements.md` 已声明 RDS / Aurora / Redis / S3 / IRSA / DB migration 等
可模板化资源，本 workflow 在服务级资源通过 validator 后继续 chain 对应的 `add-*`
workflow；未知资源进入 Ops Todo，不让用户自己记下一步该跑什么。

## 进入条件

- `docs/deployment/cd-requirements.md` 存在并填完。如未填，先跑 `interview-cd-requirements.md`
  - If present and complete, do not rerun `interview-cd-requirements.md`; treat it as the source of truth.
- 工作目录是目标应用的 git 仓库
- `k8s/` 目录为空，**或** 用户显式说要加新变体

任何前置不满足 → STOP 并说明缺口。

## Step 1. 读 cd-requirements.md 提取上下文

[precondition]
  - `docs/deployment/cd-requirements.md` 存在

[action]
  - 解析文档，提取：
    - `$app`              应用名（kebab-case，匹配 GitLab repo）
    - `$domain`           ops | builder
    - `$app_type`         c-end | restricted-admin | builder | data | observability（部署路由 slug，跟 `interview-cd-requirements.md` 一致；不是 Sentry `APP_TYPE`）
    - `$targets[]`        list of { env_keyword, cluster, env, namespace, branch }
    - `$exposure`         none | public-c-end | public-internal-with-guard
    - `$container_port`   integer
    - `$health_path`      string（如 /health）
    - `$runtime_profile`  service | static-web（运行形态；static-web 不是 programming language）
    - service profile：
      - `$language`       仓库实际运行语言；go | node | python | java 有内置 Dockerfile recipe
      - `$start_cmd`      production start command / entrypoint 证据
    - static-web profile：
      - `$build_toolchain=node`、`$node_version`、`$nginx_version`、`$spa_routing`、
        `$static_verify_runner_tag`
      - 每个 target 的
        `{ static_build_script, static_output_dir, bundle_config_source }`
  - backward compatibility：旧版 cd-requirements 没有 `$runtime_profile` 时，只要有
    明确的长驻 runtime 证据且不命中下面的 static-web candidate 歧义，就按原有
    `service` 路径继续。Go/Node/Python/Java 继续用内置 recipe；其它语言继续生成与语言
    无关的资源并产 Dockerfile recipe Ops Todo。不能把缺 profile 的 Node build 自动升级
    为 static-web，也不能让 static-web 分支改变既有 service 默认。
  - Node 仓库若同时满足“有 static build 产出证据 + 没有 production runtime start
    command（`dev`/`preview` 不算）”，只能标记为 static-web candidate。若
    cd-requirements 未显式确认 `$runtime_profile=static-web`，STOP 问用户；**不要**默认
    补 `npm start`。
  - 所选 profile 的任何必填字段是字面文本 "(question)" 或为空 → STOP 问用户

[validate]
  - `$targets` 至少 1 项
  - `$runtime_profile ∈ {service, static-web}`
  - `$runtime_profile=service` 时 `$language` 非空且匹配
    `^[a-z][a-z0-9+._-]*$`。Go/Node/Python/Java 保持原有 recipe 行为，且有对应
    production start/entrypoint 或既有 recipe default；其它语言保留 service profile、
    长驻 runtime 证据和 Dockerfile recipe Ops Todo
  - `$runtime_profile=static-web` 时：
    - `package.json` / `package-lock.json` 都被 Git 跟踪，且
      `npm ci --ignore-scripts --dry-run` 退出 0；安装 contract 是精确 `npm ci`
    - `$node_version` / `$nginx_version` 都匹配
      `^[1-9][0-9]*\.[0-9]+\.[0-9]+$`；都不是 `latest` / range
    - 每个 `$static_build_script` 是 `package.json.scripts` 的准确 key，且匹配
      `^[A-Za-z0-9:_-]+$`
    - 每个 `$static_output_dir` 匹配 `^[A-Za-z0-9][A-Za-z0-9._/-]*$`，且是显式、
      非绝对、无 `..` 的相对目录；配置声明与实际 build 产出不一致、多个目录都合理或
      目录未知 → STOP
    - `$spa_routing ∈ {yes,no}`；未确认 SPA fallback → STOP
    - 每个 target 的 `$bundle_config_source` 都是 Git 跟踪、可 review、无秘密的 bundle-time
      输入；要求 token/password/credential/private key/secret、Vault、ExternalSecret 或
      secret build arg → STOP，要求改后端/BFF contract
    - 展开每个准确 build script/mode 会自动读取的全部 `.env*` / config，并 review
      effective Docker context。任何可被读取的 untracked、ignored、generated config 或
      credential-bearing 文件 → STOP；只能用 reviewed `.dockerignore` 排除，且不能误排
      已确认的 Git-tracked public target config
    - 每个 `$bundle_config_source` 是无 CR/LF、`|`、`{{`、`}}` 的单行 evidence；
      Dockerfile 不插入这段自由文本
    - `$health_path` 匹配 `^/[A-Za-z0-9][A-Za-z0-9._~/-]*$`，不是 `/`，且不含 `..`
      或 `//`
    - `$container_port` 是 `1024..65535`；唯一特权例外是已冻结 source/legacy port 80，
      且 cd-requirements 记录 migration owner 对 root-master 风险的显式批准
    - static-web 不声明或消费 runtime secret；无 Vault / ExternalSecret / Secret
      `env`/`envFrom` / secret volume、bundle-consumed CI variable、Docker ARG/ENV 或
      CI/Vault/Secret generated config。需要凭据的依赖必须拆到 backend/BFF
    - 每个 `$target.env_keyword`、`$target.dockerfile_path` 都唯一
    - 每个 target 将构建独立 image artifact，不跨环境复用 bundle
  - 每个 `$target.cluster` 在 `references/data/clusters.yaml -> clusters[]` 找得到
  - 每个 `$target.env_keyword` 的 `app_types` 列表（在 `references/data/env-keywords.yaml -> env_keywords[<keyword>].app_types`）包含 `$app_type`
    - 不包含 → STOP，问用户：是 cd-requirements.md app_type 写错了，还是 env_keyword 选错了
    - 常见踩坑：data 团队的 internal HTTP API 想上 `prod-us`（仅 c-end + builder），实际应该归 c-end（团队归属≠app_type）；
      真正的 data pipeline 该走 `prod-us-data` 集群（cluster us-prod-data）
  - 新部署应用的 staging target 只能使用独立 staging 集群：
    - `staging-us` 必须解析到 `us-eks-staging`
    - `staging-eu` 必须解析到 `eu-eks-staging`
    - `staging-cn` 必须解析到 `cn-eks-staging`
    - `staging-cn-tke` / tech-service staging 只允许 legacy/迁移场景；新应用命中则 STOP，要求改到 `staging-cn`

[output]
  - 内存变量

## Step 2. 决定每个 target 的 tier（只读）

[precondition]
  - Step 1 完成

[action]
  - 对每个 `$target`：
    - 打开 `references/data/clusters.yaml` 查这一行
    - 记录：`$target.account_id`、`$target.region`、`$target.harbor_url`、`$target.runner_tags`、`$target.argocd_apps_dir`、`$target.build_mode`、`$target.vault_css`
    - 计算部署侧字面路径 `$target.harbor_image_path = {$target.harbor_url}/cicd/{$target.env}-{$target.region}/{$app}`
      - 例：`harbor-39070-us-staging.addx.live/cicd/staging-us/my-app`
      - 这是业务镜像唯一标准路径；禁止 `library/{$app}` 或扁平 `cicd/{$app}`。
      - **部署侧永远用字面 host**：overlay `images[].newName`、Application `image-list`、
        `spec.source.kustomize.images` 都填这个字面值（kustomize / Image Updater 不展开变量）。
    - 计算 CI 侧 push 路径 `$target.ci_image_path`（见 hard-rules #25）：
      - **B 默认（runner 决定 host，fleet 主流）**：`${IMAGE_BASE}/{$target.env}-{$target.region}/{$app}`
        （`IMAGE_BASE=${HARBOR_REGISTRY}/cicd` 由 shell 定义，host 由 `$target.runner_tags` 对应 runner 注入）。
      - **A（字面 host，单 target / 老应用 grandfather）**：`= $target.harbor_image_path`。
      - 不变量：`$target.runner_tags` 按 clusters.yaml 解析出的 harbor_url 必须 == `$target.harbor_url`
        （即 CI push 落位的 Harbor == 部署侧字面 host）。
  - 如果任何 target 是 prod：
    - 打开 `references/cost-tiering/_global.yaml -> prod_self_check`
    - 对每条项目，打给用户，要求显式确认（"yes"/"no"）
    - 任何一条 "no" → STOP（用户必须先处理才能继续）

[validate]
  - 每个 `$target` 上述字段都已填
  - 每个 `$target.harbor_image_path`（部署侧字面）匹配 `/cicd/(dev|staging|pre|prod)-[a-z]+/{$app}$`
  - 每个 `$target.runner_tags` 按 clusters.yaml 解析出的 harbor_url == `$target.harbor_url`（CI push 与部署侧同 Harbor）
  - `$runtime_profile=static-web` 时，每个 `$target.harbor_image_path` 唯一；两个 target
    解析到相同 image identity → STOP，不能共享或 retag 已打包 bundle

[output]
  - `$targets[]` 完全解析

## Step 3. 写 base k8s manifest

[precondition]
  - Step 2 完成

[action]
  - 创建目录 `k8s/base/`（如不存在）
  - 用 `recipes/k8s/rollout.yaml.tmpl` 写 `k8s/base/rollout.yaml`，填槽：
      `{{app}}=$app`、`{{container_port}}=$container_port`、`{{health_path}}=$health_path`、
      `{{env}}=__overlay__`（实际 env 由每个 overlay patch 替换）、
      `{{replicas}}=1`、`{{cpu_requests}}=100m`、`{{cpu_limits}}=500m`、
      `{{memory_requests}}=256Mi`、`{{memory_limits}}=512Mi`、
      `{{liveness_initial_delay}}=15`、`{{readiness_initial_delay}}=5`
  - 用 `recipes/k8s/service.yaml.tmpl` 写 `k8s/base/service.yaml`，填槽：
      `{{app}}=$app`、`{{port}}=$container_port`、`{{target_port}}=$container_port`
  - 用 `recipes/k8s/kustomization-base.yaml.tmpl` 写 `k8s/base/kustomization.yaml`（无槽位）
  - `$runtime_profile=static-web` 时保持 Rollout container 无 `env`、`envFrom`、
    secret-backed volume/volumeMount；Nginx 不做 runtime config substitution。

[validate]
  - 三个文件都存在
  - `kustomize build k8s/base/` 退出 0
  - `bash "$skill_root/validators/validate.sh" k8s/base/` PASS
  - `$runtime_profile=static-web` 时，渲染后的 container 不含 `env` / `envFrom` /
    secret-backed volumeMount

[output]
  - k8s/base/rollout.yaml
  - k8s/base/service.yaml
  - k8s/base/kustomization.yaml

## Step 4. 写每个 target 的 overlay

按 `$targets` 循环跑这一步。

[precondition]
  - Step 3 完成

[action]
  - 创建目录 `k8s/overlays/{$target.env_keyword}/`
  - 用 `recipes/k8s/kustomization-overlay.yaml.tmpl` 写
    `k8s/overlays/{$target.env_keyword}/kustomization.yaml`，填槽：
      `{{namespace}}=$target.namespace`、`{{app}}=$app`、
      `{{env}}=$target.env_keyword`、
      `{{harbor_image_path}}=$target.harbor_image_path`、
      `{{image_tag}}=0000000`（overlay/draft placeholder，**不带冒号前缀**——kustomize 自动用 `:` 拼）
  - 不生成 `.argocd-source-*`、ImageUpdater CR、ApplicationSet 或 Git 凭据；应用仓只保留
    workload overlay，镜像自动化由 argocd-apps 中 Application annotations 表达。
  - 不要用 `staging-latest` / `prod-latest` 这类 env tag，因为 Application `allow-tags` 只接受纯 Git SHA。
  - 跑 `kustomize build k8s/overlays/{$target.env_keyword}/` 检查渲染出的 Rollout
    的 `spec.template.metadata.labels.env` 字段是不是 `$target.env_keyword`。
    没有 → overlay 的 env-label patch 有问题，STOP 修
    （`app` + `env` 两个 label 是 fluent-bit-base 日志接入的硬约束——详见 `references/logging/README.md`）

[validate]
  - `kustomize build k8s/overlays/{$target.env_keyword}/` 退出 0
  - 渲染后 Rollout 的 pod template 含 `env:` label
  - `bash "$skill_root/validators/validate.sh" k8s/overlays/{$target.env_keyword}/` PASS

[output]
  - k8s/overlays/{$target.env_keyword}/kustomization.yaml

## Step 5. 准备 ArgoCD Application contract（**不写文件**）

按 `$targets` 循环跑。本步骤只冻结参数；真实 Application 必须等 Step 11 的目标镜像
证据完成后再写入独立 argocd-apps MR。

[precondition]
  - Step 4 完成

[action]
  - 为每个 target 计算并记录：
    - `$target.application_path={$target.argocd_apps_dir}/{$app}-{$target.env_keyword}.yaml`
    - `$target.source_repo_url=<工作仓库 origin remote 归一化后的 HTTPS URL>`
    - `$target.source_path=k8s/overlays/{$target.env_keyword}`
    - `$target.project`：按 `permission-boundaries.yaml -> argocd_app_projects.application_registration_contract`
      读取目标集群已有且获批准的 AppProject 与 argocd-apps boundary lint 合同，核对
      source repo/path/revision、destination、最终 render 资源（含 hooks）及 namespace
      创建权限。不得默认填 `default`，不得自行新建 AppProject 或引入新名称。
    - `$target.app_platforms`（dual-arch → `linux/amd64,linux/arm64`；
      single-arch-amd64 → `linux/amd64`）
    - `$target.first_real_git_sha=PENDING`
  - `source_repo_url` 归一化规则：
    - `https://gitlab.addx.ai/<group>/<repo>.git` 原样使用。
    - `git@gitlab.addx.ai:<group>/<repo>.git` 转为
      `https://gitlab.addx.ai/<group>/<repo>.git`。
  - 不渲染 `recipes/argocd/application.yaml.tmpl`，不创建或修改 argocd-apps 文件，
    不使用 `0000000`、`PENDING` 或其它虚构值写 `{{image_seed}}`。把 Application
    注册记为等待 Step 11 的 pending handoff。
  - 项目合同缺失或权限不足时记录准确 cluster/project/repo/path/namespace/GVK 的
    Ops Todo；由平台独立处理权限 MR，合并同步后才可注册 Application。缺少 checkout
    或只读证据时可继续 app-repo 构建准备，但不能猜 project 或宣称注册 ready。

[validate]
  - 每个 target 的 Application 参数都已解析，且 repo URL 默认是 HTTPS。
  - 当前步骤没有创建或修改任何 argocd-apps 文件。
  - `$target.first_real_git_sha` 只存在于内存 pending contract，不在 Git 文件中。
  - 每 target 的 project 合同已有证据，或明确列为阻塞 Application 注册的 Ops Todo。

[output]
  - 每 target 的 pending Application contract；本步骤无 Git 变更。

## Step 6. 写 GitLab CI（per-target build + push 到各自 harbor）

[precondition]
  - Step 3 完成

[action]
  - 如果 `.gitlab-ci.yml` 已存在：STOP 问用户是否合并新 job（不要覆盖）；否则：
  - 先写**框架**：用 `recipes/ci/gitlab-ci-shell.yml.tmpl` 写到 `.gitlab-ci.yml`，填槽：
      `{{app}}=$app`
  - **对每个 `$target` append per-target snippet**（在选定 runner build + push 到该 target 的 Harbor；不能假定跨账号/跨云 pull 可用，也不能按“主要 target”替其它目标选择 Harbor）：
    - 先定 `$target.dockerfile_path`：
      - `$runtime_profile=service` → `Dockerfile`（保持现有 go/node/python/java 行为）
      - `$runtime_profile=static-web` → `Dockerfile.{$target.env_keyword}`；每个 target
        的文件固化其准确 build script/output/config contract，禁止用 build arg 动态切环境
    - `$runtime_profile=static-web` 时，先 append
      `recipes/ci/gitlab-ci-static-web-verify.yml.tmpl`，填槽：
        `{{target_key}}=$target.env_keyword`
        `{{static_verify_runner_tag}}=$static_verify_runner_tag`
        `{{branch}}=$target.branch`
        `{{build_script}}=$target.static_build_script`
        `{{static_output_dir}}=$target.static_output_dir`
        `{{node_verify_image}}=${HARBOR_REGISTRY}/base/node:$node_version-alpine`。
      先确认这个 exact Node tag 已在 `DEV/base-images/images.yaml` 登记并同步到
      verification runner 所在 Harbor；所有地区都禁止直接使用 Docker Hub/public
      `node:*` CI image。
      该 job 在 MR 和目标分支 push 上执行准确 `npm ci` +
      `npm run $target.static_build_script`，并断言 `$target.static_output_dir` 存在且非空。
      job 开始时还必须 fail closed 检查常见 registry/cloud/Kubernetes credential
      mount/env 不存在。它是 target bundle 的 premerge gate；
      仓库已有测试仍须保留或合并到 CI，不能拿这个通用 build gate 代替既有测试。
    - 若 `$target.build_mode == "dual-arch"`：append `recipes/ci/gitlab-ci-target-dual-arch.yml.tmpl`，填槽：
        `{{target_key}}=$target.env_keyword`
        `{{harbor_image_path}}=$target.ci_image_path`（B 默认 `${IMAGE_BASE}/<env>-<region>/<app>`；单 target / 老应用可用字面 host）
        `{{runner_tag_amd64}}=$target.runner_tags[0]`
        `{{runner_tag_arm64}}=$target.runner_tags[1]`
        `{{branch}}=$target.branch`
        `{{dockerfile_path}}=$target.dockerfile_path`
    - 若 `$target.build_mode == "single-arch-amd64"`：append `recipes/ci/gitlab-ci-target-single-arch.yml.tmpl`，填槽：
        `{{target_key}}=$target.env_keyword`
        `{{harbor_image_path}}=$target.ci_image_path`（B 默认 `${IMAGE_BASE}/<env>-<region>/<app>`；单 target / 老应用可用字面 host）
        `{{runner_tag_amd64}}=$target.runner_tags[0]`
        `{{branch}}=$target.branch`
        `{{dockerfile_path}}=$target.dockerfile_path`
  - CI 终态 image tag 必须是纯 `${CI_COMMIT_SHORT_SHA}`，匹配 Application
    `allow-tags: regexp:^[a-f0-9]{7,40}$`；环境隔离只靠
    `cicd/<env>-<region>/<app>` image path，不靠 tag 前缀。
  - `$runtime_profile=static-web` 时，检查 effective build job（含全局/default/inherited
    `variables`、`before_script` 和 `script`）：禁止把 `VITE_*`、`NEXT_PUBLIC_*` 或任何
    token/password/credential/private-key/secret CI variable 作为 Docker build arg/env，
    也禁止在 Kaniko context 里生成 `.env`、JavaScript、JSON 等前端配置。registry auth
    只保留 Kaniko 自身的 `/kaniko/.docker/config.json`，不进入 build context 或 image。

[validate]
  - `.gitlab-ci.yml` 存在
  - YAML 可解析（`python3 -c "import yaml; yaml.safe_load(open('.gitlab-ci.yml'))"`）
  - 渲染后含 N 组（N = `len($targets)`）目标构建任务，按准确 `$target.build_mode` 校验：
    - `dual-arch`：`build:<target_key>:amd64` + `build:<target_key>:arm64` +
      `manifest:<target_key>`；manifest 的 needs 精确依赖本 target 的两个 build。
    - `single-arch-amd64`：仅 `build:<target_key>`；本 target 没有 arm64 build 或 manifest job。
  - 每组 build job 的 `DOCKERFILE` 精确等于 `$target.dockerfile_path`；service 仍为
    `Dockerfile`，static-web 不得让两个 target 指向同一个环境专属 Dockerfile
  - 每组 build job 的 runner tag 按 clusters.yaml 解析出的 Harbor == 该 target 部署侧字面 host
    （**人工核对**，静态 tag→Harbor validator 待补）；B 写法下各 target 的 `env-region` 段 + runner tag
    不同（不能共用），A 写法下 IMAGE_PATH 是各自字面 host
  - `$runtime_profile=static-web` 时 effective build job 没有 bundle-consumed secret CI
    variables / secret build args，也不生成前端配置文件
  - `$runtime_profile=static-web` 时每个 target 有且仅有一个
    `verify:<target_key>:static-web` job；它使用 Harbor base 中的 exact Node image、
    已审计的 credential-free `$static_verify_runner_tag`、`npm ci` 和准确 target build
    script，并在 MR 及目标分支 push 上运行；job 对常见 credential mount/env fail
    closed，且验证 `$target.static_output_dir` 存在并非空
  - static-web exact build script 与 tracked config 已证明 production semantics；
    Vite effective `NODE_ENV=production`，不存在 `--mode=development`、
    `NODE_ENV=development` 或等价开发语义

[output]
  - .gitlab-ci.yml（shell + static-web verify（适用时）+ per-target image snippet 拼接）

## Step 7. 写 Dockerfile 与 static-web Nginx 配置

[precondition]
  - Step 1 已经定 `$runtime_profile`
  - `$runtime_profile=service` 时已定 `$language`
  - `$runtime_profile=static-web` 时 static-web contract 已完整且无歧义
  - Step 2 已经解析 `$targets[]`

[action]
  - 先计算 `$has_cn_target`：任一 target cluster 的 `region == cn`、`cloud == tencent`
    或 `partition == aws-cn` 即为 true。
  - 若 `$runtime_profile=service`，保持现有 recipe 选择和根 `Dockerfile` 输出：
    - `$language == "go"`     → `recipes/ci/dockerfile-go.txt`，填槽：
      `{{go_version}}=<repo go.mod 的 go 版本或 1.22>`、`{{binary_name}}=$app`、
      `{{main_path}}=<main package path, default ./cmd/{$app}>`、`{{port}}=$container_port`
    - `$language == "node"`   → `recipes/ci/dockerfile-node.txt`，填槽：
      `{{node_version}}=<package.json engines.node 或 20>`、`{{port}}=$container_port`、
      `{{start_cmd}}=<package.json scripts.start、已确认 production start command，或既有
      service recipe fallback npm start>`。`npm start` fallback 只在 profile 已明确为
      service 后保留；不能拿它绕过 Step 1 的 static-web candidate 歧义。
    - `$language == "python"` → `recipes/ci/dockerfile-python.txt`，填槽：
      `{{python_version}}=<runtime.txt / pyproject requires-python 或 3.11>`、
      `{{port}}=$container_port`、`{{start_cmd}}=<app start command>`
    - `$language == "java"`   → `recipes/ci/dockerfile-java.txt`，填槽：
      `{{java_version}}=<maven/gradle toolchain 或 17>`、`{{jar_path}}=<built jar path>`、
      `{{port}}=$container_port`
    - 都不命中（如 rust / ruby / php）→ recipe 未覆盖；本步骤产 Ops Todo
      "Dockerfile recipe for $language pending；workflow 跳过 Dockerfile 写盘"，
      **继续往下走**（manifest set 不依赖 Dockerfile）
  - 若 `$runtime_profile=static-web`：
    - 再次确认 `package.json` / `package-lock.json` 被 Git 跟踪，并运行
      `npm ci --ignore-scripts --dry-run` PASS；recipe 固定
      `COPY package.json package-lock.json ./` + `RUN npm ci`，禁止 `npm install`。
    - 因 recipe 使用 `COPY . .`，再次 review effective Docker context、准确 build
      script/mode 的 framework auto-loaded `.env*` / config 和 `.dockerignore`。任何可被
      build 读取的 untracked/ignored/generated 或 credential-bearing config → STOP。
    - 用 `recipes/ci/nginx-static.txt` 写工作仓库根 `nginx.conf`：
      - `{{port}}=$container_port`
      - `{{health_path}}=$health_path`
      - `{{spa_fallback}}=/index.html`（`$spa_routing=yes`）或 `=404`
        （`$spa_routing=no`）
    - 对每个 `$target` 分别写 `Dockerfile.{$target.env_keyword}`：
      - `$container_port >= 1024` → `recipes/ci/dockerfile-static-web.txt`
        （`nginxinc/nginx-unprivileged` + `USER 101`，默认/首选）
      - `$container_port == 80` 且 cd-requirements 明确记录“保留 source container port 80”
        和 migration owner 对 root-master 例外的批准
        → `recipes/ci/dockerfile-static-web-port80.txt`。该兼容 variant 使用标准 Nginx
        root master；不能静默当作 non-root。
      - 其它 privileged port `<1024` → STOP，让用户明确改为非特权端口或补一个经 review
        的兼容 recipe；不要现场加 Linux capability。
      - 填槽：
        `{{node_version}}=$node_version`、`{{nginx_version}}=$nginx_version`、
        `{{build_script}}=$target.static_build_script`、
        `{{static_output_dir}}=$target.static_output_dir`、
        `{{nginx_conf_path}}=nginx.conf`、
        `{{port}}=$container_port`
      - `$target.bundle_config_source` 只留在 reviewed `cd-requirements.md`；不要把这段
        自由文本插进 Dockerfile 注释。准确 build script 必须自己选择该 target 的
        Git-tracked public config/mode。
    - 每个 Dockerfile 把准确 script/config 固化在文件里，CI Step 6 指向该 target 的
      `$target.dockerfile_path`。不要新增 `ARG VITE_*` / `ARG NEXT_PUBLIC_*` / 通用
      `ARG BUILD_ENV`，不要从 Vault/ExternalSecret/Kubernetes Secret/CI variable 生成
      `.env`、JavaScript、JSON 或其它前端配置，也不要把一个 target 构建出的目录复制或
      retag 给另一个 target。
  - 如果 `$has_cn_target == true`：
    - 生成前必须把 Dockerfile 里所有外部 `FROM`（如 `golang:*`、`node:*`、
      `python:*`、`eclipse-temurin:*`、`nginx:*`、
      `nginxinc/nginx-unprivileged:*`、`gcr.io/...`、`docker.io/...`）替换为
      `${HARBOR_REGISTRY}/base/<reviewed-mirror-name>:<tag>` 形态。
    - 在第一个 `FROM` 前加 `ARG HARBOR_REGISTRY`（如已有则复用）。CI shell 模板已经把
      runner 注入的 `HARBOR_REGISTRY` 作为 Kaniko build arg 传入。
    - 查 `DEV/base-images/images.yaml` 是否已有这些 base image 的同步条目；没有就代写
      base-images MR（或在无仓库权限时产 Ops Todo），并把"等待 base-images MR 合并"列为阻塞项。
    - 在 app MR 合并前用 `crane manifest` 或 `skopeo inspect --raw` 验证目标 Harbor
      mirror 的 manifest digest 和该 target 所需全部平台；把 exact tag、digest 和
      `linux/amd64` / `linux/arm64` 证据记录到 `docs/deployment/cicd.md`。缺镜像、
      digest 不明或缺任一架构都 STOP。
    - 合并前不要让用户在 CN target 干等；提示可先用本地 / US / EU target 验证业务镜像构建。
  - service profile 把槽位写到工作仓库根 `Dockerfile`；static-web profile 写
    `nginx.conf` + 每 target 的 `Dockerfile.<env-keyword>`。

[validate]
  - `$runtime_profile=service`：
    - 如果 `$language` 命中四种之一：`Dockerfile` 存在，且至少包含一个 `FROM `
    - 否则：Ops Todo 已包含 missing-recipe 行
  - `$runtime_profile=static-web`：
    - `nginx.conf` 与 N 个 `Dockerfile.<env-keyword>` 都存在，CI 中每个
      `DOCKERFILE` 精确指向对应文件
    - 每个 Dockerfile 都含 `COPY package.json package-lock.json ./`、`RUN npm ci`、
      该 target 的准确 `RUN npm run <script>`，并从明确
      `/app/<static_output_dir>/` copy 静态资产
    - 最终 `FROM` 是 pinned Nginx image，最终 stage 不含 Node runtime、`node_modules`
      或 production `npm` install
    - `nginx.conf` 的 `listen` 等于 `$container_port`、exact `$health_path` 返回 200；
      SPA 必须精确含 `try_files $uri $uri/ /index.html;`，非 SPA 必须精确含
      `try_files $uri $uri/ =404;`
    - Dockerfile / CI 不含 frontend config/credential build args；每 target image path
      与 bundle config contract 一一对应
    - Dockerfile 不含 `$bundle_config_source` 自由文本；render 后不剩 `{{...}}`
    - 渲染后的 Rollout/Nginx container 不含 runtime Secret `env`/`envFrom`/volumeMount，
      overlay 不含给前端消费的 ExternalSecret
  - 如果 `$has_cn_target == true`：所有生成的 Dockerfile 不得残留外部 `FROM`
    （`gcr.io/`、`docker.io/`、裸 `golang:` / `node:` / `python:` /
    `eclipse-temurin:` / `nginx:` / `nginxinc/nginx-unprivileged:`），且已有
    `ARG HARBOR_REGISTRY`；目标 Harbor mirror 的 exact digest 和所需平台已验证并记录

[output]
  - service：Dockerfile（如适用）或 Ops Todo 行
  - static-web：nginx.conf + Dockerfile.<env-keyword>（每 target 一份）

## Step 8. 全量 validator

[precondition]
  - Step 3-7 完成

[action]
  - 跑 `bash "$skill_root/validators/validate.sh" k8s/`

[validate]
  - `bash "$skill_root/validators/validate.sh" k8s/` 退出 0
  - Step 5 仍只有 pending Application contract；尚未写 argocd-apps，也不假装跑过
    Application validator

[output]
  - validator 日志打给用户

## Step 9. 更新 cicd.md

[precondition]
  - Step 8 通过

[action]
  - 写或更新 `docs/deployment/cicd.md`，包含技术决策：
    - 涉及的集群、分支、namespace
    - CI 策略（per-target dual-arch / single-arch-amd64）
    - Image Updater 写回方式
    - app 消费的 Vault 路径列表
    - 指回 `docs/deployment/cd-requirements.md`

[validate]
  - `docs/deployment/cicd.md` 存在
  - 至少包含一个 `$targets` 中的集群名和分支名

[output]
  - docs/deployment/cicd.md

## Step 10. Chain 声明的资源 workflow

[precondition]
  - Step 9 完成

[action]
  - 读取 `docs/deployment/cd-requirements.md` 的 Resources / Secrets / External dependencies 段。
  - 本步骤是首次部署的 **candidate 阶段**：给子 workflow 传
    `$delivery_phase=candidate`。生成候选文件并跑离线 validator；本应用尚无
    Application，不能要求本次新建的 DB、Secret、Pod 或 DataSource 已经 live。
    目标平台能力、身份冲突、权限/网络和配额等检查仍是部署前置，不能一并延期；
    可以准备候选但未完成这些检查时，必须明确阻塞 Step 11 Application 注册。
    在 `cicd.md` 维护 `$pending_runtime_checks[]`，每条记录准确
    `target / workflow / resume-step / resource-or-secret / acceptance / status=pending`；
    candidate 完成只表示可交付候选，不表示子流程运行态验收完成。
  - 若 `$runtime_profile=static-web`，先做 secret boundary gate：
    - 任何会给 workload 添加 Vault / ExternalSecret / Secret `env`/`envFrom` /
      secret volume、IRSA credential use、或 generated runtime/frontend config 的资源
      workflow → STOP，要求把依赖拆到独立 backend/BFF service；不要 chain。
    - public bundle config 继续由每 target 的 Git-tracked build script/config 提供，不走
      Vault、ExternalSecret 或 CI secret variable。
    - 纯 workload metadata 能力（例如 logging labels）可继续；不得因此改变既有
      `service` profile 的资源 chaining。
  - 先按 target 过滤 hard rule #29 + #30。**shared-middleware target** = `$target.cluster ∈
    {us,eu,cn}-eks-staging + {us,eu,cn}-eks-tech-service`（6 个；**按 cluster 名判，不用 `$target.env`**——
    tech-service env=prod 会与真 prod 混，见 #29）：
    - `rds` / `aurora` / `redis` / `elasticache` / `documentdb` / `msk` / `kafka` / `clickhouse`
      对 shared-middleware target **不 chain** app-owned `add-*` workflow，也不生成 crossplane-infra
      单 app 资源，**也不允许**用 StatefulSet+PVC 自托管（#30）。
    - **已上线自助（mysql / redis / postgres）**：用 `recipes/k8s/shared-database-claim.yaml.tmpl`
      从 canonical kebab app slug 生成 `kind: Database`（annotation 保留 slug，`spec.app` 把 `-`
      机械替换为 `_`，即 `$database_app = $app.replace('-', '_')`，禁止手填拼接式多词名），
      Composition 自动在该集群共享实例上置备 per-app database/user/endpoint，凭据写
      `secret/{env}/{platform}/application/{database_app}/{key}`，app 写 ExternalSecret 消费。**不再 Ops Todo**。
      SQL 必须使用 `shared-db-external-secret.yaml.tmpl` 把小写 producer 字段映射为
      `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD`；Redis 按 add-redis Step 1 的独立两字段
      contract 生成 `REDIS_HOST/REDIS_PORT`，不能套用 SQL 模板。
    - **shared consumer 也必须注入实际容器**：这些内联分支不会执行 add-rds/add-redis
      后面的注入步骤。Redis 执行 add-redis Step 1「容器消费连接信息」；SQL 执行
      add-rds Step 1「shared SQL 容器消费连接信息」。只修改准确 target overlay 的
      workload/main container，并保留其它 target/容器/env；渲染后执行对应
      `check_workload_secret.py` 精确检查。ExternalSecret 存在不等于已消费；多个 SQL
      连接不能同时覆盖 DB_*，必须先确认应用接受的独立消费映射，保留已有 primary。
    - **已上线自助（msk / kafka SCRAM credential）**：指引 user 写
      `kind: KafkaScramCredential {app, env, region}`，Composition 在该集群共享 MSK 上创建
      per-app SCRAM username/password，并把 AWS 原生 `bootstrapBrokersSaslScram` `:9096`
      endpoint 作为 `bootstrap_brokers_sasl_scram` 写入
      `secret/{env}/kafka/application/{app}/sasl`，app 写 ExternalSecret 消费并映射
      `KAFKA_BROKERS`。topic/ACL workflow 仍 planned，需要显式 topic/ACL/consumer group
      治理时写 Ops Todo。**凭据本身不再 Ops Todo**。
    - 任何 shared Database 候选都执行
      [producer identity gate](../references/shared-middleware/README.md#producer-identity-gate)，
      按准确 target 取得 fresh all-namespaces DatabaseList，并对该 target 完整渲染执行
      `bash "$skill_root/validators/validate.sh" --repo-context app --database-inventory "$target_database_inventory" "$target_render_dir"`。
      不能只扫描本仓 claim，也不能把“本次 claim 尚未 live”当作库存获取失败。
      无准确库存可以交付候选与 pending，但禁止宣布可部署或继续 Step 11 注册。
    - **尚无消费 Composition（aurora / mariadb-RDS / documentdb / clickhouse）**：STOP 并写 Ops Todo：
      "<resource> on <env>-<region>：补可复用 kind:Database 消费 Composition + 自动 per-app 凭据交付"（注：documentdb /
      clickhouse 共享实例已部署 6 集群，仅消费层未落地；不让单 app 自建、不用 StatefulSet 自托管）。
    - 同一个 resource 若还有 **非 shared-middleware 集群 target（真 prod `*-eks-prod`/`*-prod-data`/TKE/dev；
      tech-service 不在此列）**，只把这些 targets 交给对应 `add-*` workflow 继续生成 app-owned 资源；
      不要因为有 shared-middleware target 就阻断它们。
  - 对每个已声明且有 workflow 的资源，按依赖顺序继续执行（**IRSA 必须先于 S3**：`add-s3-bucket` 的进入条件与 Step 4 都要求 app 的 IRSA Role 已存在——它给现有 Role 挂 S3 权限，IRSA 没建好会 STOP）：
    1. `rds` → `workflows/add-rds.md`
    2. `aurora` → `workflows/add-aurora.md`
    3. `redis` / `elasticache` → `workflows/add-redis.md`
    4. `aws api access` / `irsa` → `workflows/add-irsa-role.md`
    5. `s3` → `workflows/add-s3-bucket.md`
    6. `ci aws access` / `ci irsa` → `workflows/add-ci-aws-irsa.md`
    7. `db schema migration` → `workflows/add-db-migration.md`
    8. `sentry` → `workflows/add-sentry.md`
    9. `logging` → `workflows/add-logging.md`
    10. `ninedata` → `workflows/add-ninedata-datasource.md`（仅海外集群）
  - 按下表执行子流程；只有表内明确的 runtime 依赖可排队，其它 STOP 仍使父流程
    STOP，并保留原始错误。独立调用子流程默认 `$delivery_phase=runtime`，不得静默
    切到 candidate 来跳过其验收。

    | 资源 | candidate 阶段执行 | Step 11.5 的 runtime 恢复点 |
    |---|---|---|
    | app-owned RDS | add-rds Steps 1–13；Step 10 只核对渲染的 sync-wave 并登记待验收 | add-rds Step 10 的 Secret/Instance live 验收 |
    | shared DB/Redis、Aurora、managed Redis | 生成 claim/密码链/consumer 引用，离线验证；登记各自出口的 live 条件 | 目标 claim/资源 Ready、凭据交付、准确容器引用 |
    | IRSA → S3、CI IRSA、Sentry、logging | 按依赖完成文件和离线验证；IRSA Role 可由同轮独立平台 MR 声明，平台 MR 必须先于 consumer 同步 | 核对各自出口的 Role/SA/凭据/日志等运行态条件 |
    | DB migration | 仅核对需求并排队，暂不生成或加入 PreSync Job；新 DB Secret 尚不存在 | DB/Secret Ready 后从 add-db-migration Step 1 开始，独立后续 MR |
    | NineData | 仅核对海外/app-owned 资格和配额前置并排队，暂不生成 DataSource | DB/Secret Ready 后从 add-ninedata-datasource Step 1 开始，独立后续 MR |

  - 首次 Secret 与 PreSync migration 不能放在同一轮同步：PreSync 先于普通 Sync
    资源，负 sync-wave 也不能改变 phase 顺序。保留 add-db-migration 的 two commit
    rule；不能让依赖尚未创建 schema 的 workload 卡住首次 sync operation，再指望
    后续 MR 的 PreSync 自动解除它。声明首次 migration 的全新服务必须使用下述
    “首次资源初始化”两次同步；其它服务保留正常候选，workload wave 必须晚于它消费的
    ExternalSecret（默认 consumer=1、workload=2；保留已有更高 wave）。
  - 对没有 workflow / recipe 的资源（DocumentDB、第三方控制台、Kafka topic/ACL 等）写 Ops Todo，不自动猜 manifest。

### 首次资源初始化（仅全新服务且声明首次 migration）

1. 只在准确 target 尚无本应用 Application、workload、Service/Ingress 的只读证据完整时
   启用。发现已运行资源或身份不明时 STOP，不能通过删除引用、scale-to-zero 或 prune
   现有服务套用此流程。记录原计划 replicas，不假定 replicas=0 会使 Rollout Healthy。
2. 先完成完整业务候选的离线 render/精确容器检查，记录准备在第二次 MR 启用的资源、
   patches、镜像和 migration command。然后将**同一个**
   `k8s/overlays/<target>/kustomization.yaml` 的首次 resources 改为初始化所需的
   DB/密码链/consumer ExternalSecret、SA/配置等已声明资源；不引用含新 workload/Service
   的 `../../base`，不引用 Ingress、PreSync migration 或其它依赖业务 schema 的 Job。
   同时暂不启用只针对这些未渲染资源的 patches/replacements。文件可以保留在 Git，
   但首次最终 render 不得出现它们；其它 target 的 Kustomization 不变。
3. 首次 consumer Secret 就须满足 migration binary 的完整连接合同：DATABASE_URL 或
   DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME。标准 DB consumer 只有前四项时，按
   `references/db-migration/secret-patterns.md` mode B 的 template.data 变体保留四项
   字段映射并加入准确 `$db_name` 字面量；保留当前 producer 的 remoteRef 大小写/路径，
   不复制示例路径。此 Secret 来源变更必须包含在首次 MR，不能留到 migration MR。
4. 对首次初始化 render 重新执行全量 validator 与 project/resource 边界检查。初始化
   render 中没有业务容器，因此不对它调用“必须找到 workload”的消费检查；该检查必须
   对已准备的完整业务候选及第二次 MR 的实际 render 通过。记录两个 render 的差异。
   Step 11 的 Application name/repo/path/revision/project、真实 SHA seed 均不变，
   不创建临时 Application，也不为尚无 summary image 添加 force-update。
5. 首次同步仅初始化资源；必须等操作完成 `Succeeded`，DB/consumer Secret Ready，且
   migration 所需 keys 均存在，再进入 Step 11.5 的第二次 MR。此时报告“资源初始化完成”，
   不报告应用部署完成。若 operation 仍 Running/Failed，先诊断，不能提交后续 MR 碰运气。

[validate]
  - 每个 candidate 已生成文件的子 workflow validator 都通过；合并前对资源加入后的
    全部 app overlay 重新 render 并运行全量 validator（Step 8 的较早结果不足以替代）。
  - 每个延后的 runtime 验收或整个子流程都在 `$pending_runtime_checks[]` 中有准确恢复点。
  - 每个不支持的声明资源都有 Ops Todo 行；等待首次同步不是“不支持”，不能漏掉恢复。

[output]
  - 子 workflow 写出的文件列表并入最终 summary
  - Ops Todo 累加
  - `$pending_runtime_checks[]` 写入 cicd.md，交给 Step 11.5

## Step 11. 合并 app MR、证明真实镜像并写独立 Application MR

[precondition]
  - Steps 3-10 的 app-repo manifest、CI、Dockerfile、文档和 resource claim 已提交到
    同一个 app MR。子 workflow 产生的其它仓库 MR 保持独立，并已合并/ready 或明确列为
    阻塞 Application 的 Ops Todo。
  - Step 10 完成后的全部 app repo validators 已通过；candidate 阶段已完成，
    runtime 验收仍 pending 不阻塞首次 Application 注册。
  - 写 Application 前，Step 5 的准确已有 project 合同和 namespace 前置已就绪；
    相关权限 MR 已独立合并同步，否则只交付 app MR 与阻塞注册的 Ops Todo。

[action]
  - 先完成可审查的 app MR 与验证，确认不含 argocd-apps 文件或流量变更；只有当前用户
    授权范围已包含该 MR 合并且仓库审批门禁满足时才合并。沿用本会话已有的准确授权，
    不重复索要；仅请求生成/修正配置不等于授权合并。未获授权时返回具体 MR 与 pending
    artifact/Application handoff，不把工作流步骤或 Deployment Task 当作授权。
  - 对每个 target 跑目标分支 pipeline，要求对应 build/manifest job 成功，并在
    `$target.harbor_image_path` 验证一个符合 `^[a-f0-9]{7,40}$` 的真实 Git SHA tag、
    immutable digest 和 `$target.app_platforms` 要求的全部架构。把该 SHA 写入
    `$target.first_real_git_sha`。
  - 任何 target 尚无真实 SHA、digest 不明或缺架构时 STOP；不要创建 Application，
    不要用 `0000000`、`PENDING`、`latest` 或不存在的 tag 占位。
  - 基于 argocd-apps 当前主分支创建独立 worktree/MR。若无 checkout 或访问权，
    每 target 写一条包含 `$target.application_path`、真实 SHA 和 validator 要求的
    Ops Todo；不要在 app repo 中放 Application。
  - 对每个 target 用 `recipes/argocd/application.yaml.tmpl` 写
    `$target.application_path`，填槽：
      `{{app_with_target_suffix}}={$app}-{$target.env_keyword}`、
      `{{app}}=$app`、
      `{{env_keyword}}=$target.env_keyword`、
      `{{project}}=$target.project`（Step 5 已核验的准确已有 project）、
      `{{source_repo_url}}=$target.source_repo_url`、
      `{{source_target_revision}}=$target.branch`、
      `{{source_path}}=$target.source_path`、
      `{{destination_namespace}}=$target.namespace`、
      `{{harbor_image_path}}=$target.harbor_image_path`、
      `{{app_platforms}}=$target.app_platforms`、
      `{{image_seed}}=$target.first_real_git_sha`。
  - 模板直接生成完整的 `argocd-image-updater.argoproj.io/*` annotations，且
    `write-back-method=argocd`；不创建 ImageUpdater CR、ApplicationSet、merger、Git
    写权限或 `.argocd-source-*`。
  - 如果工作仓库是 private/internal，确认目标 ArgoCD 有 repo 读权限；
    `Argocd-deploy` Reporter 足够。不要为镜像更新提升项目角色或修改 protected branch。
    未验证 repo credential 时不要改用 SSH URL；`ssh: no key found`、
    `repository not found` 或 `don't have permission to view it` 都必须先修权限再继续。
  - 修改既有 Application 时先读 live `spec.source.kustomize.images[]` 和 workload
    image，保留 live SHA recovery seed，禁止降回模板值。
  - `app.force-update=true` 不是默认值。只有只读证据证明目标 Application 的
    `status.summary.images` 无法呈现 workload image 时，才允许为准确 alias 添加并记录原因。
  - 提交独立 argocd-apps MR；它只能包含 Application 注册，不能包含 app repo、流量、
    source workload 或 source retirement 变更。
  - Step 5 的 project/namespace 权限前置仍未合并同步时，停在 Application handoff。
    创建 MR 不授权合并或同步；执行部署时继续遵循 deployment-tracking 和对应运维流程。

[validate]
  - app MR pipeline 对每个 target 都产出真实 SHA tag、digest 和所需架构。
  - `python3 "$skill_root/validators/check_argocd_application.py" <argocd-apps-subdir>`
    退出 0。
  - 对每个新增 Application 运行
    `python3 "$skill_root/validators/check_argocd_namespace_creation.py" <argocd-apps-subdir> --application <changed-app.yaml>`；
    同时通过 argocd-apps 当前 CI 的 boundary/application contract 检查。skill validator
    通过不替代目标仓库的 repo/path/destination/resource 权限合同。
  - Application repo URL 是 `https://gitlab.addx.ai/...`；private/internal repo 的
    `Argocd-deploy` 至少有 Reporter。
  - Application recovery seed 等于已验证的真实 target SHA，不是
    `0000000` / `PENDING` / `latest`。
  - Application 含标准 Image Updater annotations 和三条 Feishu notification
    annotations，不含 `image-writeback.addx.io/*`、`git-branch`、`.argocd-source-*`，
    默认不含 `app.force-update`。
  - app MR 与 argocd-apps MR 分离；Application MR 不含流量或 source 变更。

[output]
  - app MR、target pipeline、tag/digest/platform evidence。
  - 每 target 的独立 argocd-apps Application 文件和 MR，或精确 Ops Todo。

## Step 11.5. 首次同步后恢复资源验收

[precondition]
  - Step 11 已交付具体 Application MR。
  - 只有当前授权和仓库门禁允许时才合并/同步；未获授权或尚未同步时，保留
    `$pending_runtime_checks[]` 并转 Step 12 交付，不等待一个尚不存在的 live 资源。

[action]
  - 在已获准的首次同步后，按依赖恢复 `$delivery_phase=runtime`：先平台权限/IRSA，
    再 DB/Redis 等资源与 Secret，接着 DB migration，再 NineData 和其它 consumer 验收。
  - RDS 回到 add-rds Step 10；其它已生成资源执行原子流程出口的 live 检查。只读取
    Secret 的存在、key 名和 Ready 状态，不输出凭据值。失败保留准确 pending/failed
    记录并进入对应 Troubleshoot，不把离线通过替代实际验收。
  - 首次资源初始化模式必须先确认首次 sync operation `Succeeded` 且 DB/Secret Ready，
    再从 add-db-migration Step 1 开始准备第二次独立 app MR：恢复同一 overlay 原计划的
    workload/Service 资源引用和 patches，并加入 PreSync Job；流量资源仅在既有授权范围内
    恢复。保留原计划 replicas，不能通过 replicas=0 绕过 workload 验收。
    该 MR 不修改已生效的 Secret 来源/keys；确认已验证镜像包含 migration command，
    必要的新镜像也须先满足真实 tag/digest/platform 门禁。正常顺序为已有 Secret →
    PreSync migration 成功 → Sync workload（wave 高于 consumer）→ workload Ready。
    第二次实际 render 再执行精确容器检查与全部 validator，授权合并/同步后验收。
  - NineData 在 DB/Secret 就绪后从 Step 1 跑到出口，
    重核当时配额，独立提交/同步并完成 datasource Ready 验收。
  - 后续 MR 的创建、合并与同步分别沿用准确授权；未获授权时交付具体 MR 和恢复点，
    不把父流程授权扩大到额外部署动作。

[validate]
  - 只有全部声明资源的 runtime 检查通过、migration/可选 NineData 等声明子流程完成，
    且 workload Ready 后，才可报告“部署完成”。
  - 未完成项保留 target、恢复点、阻塞原因和验收条件；状态为“候选已交付/待部署验收”。

[output]
  - 更新后的 `$pending_runtime_checks[]`、运行态证据或明确 pending handoff。

## Step 12. 出 summary card + Ops Todo

[precondition]
  - Step 11.5 已完成验收，或已交付具体 MR 与等待授权/权限/镜像/首次同步的精确 handoff

[action]
  - 整理 Ops Todo 行（workflow 做不了的事）：
    - DNS 记录（如 `$exposure` 是公网）
    - 跨账号 IAM trust（如某个 target.account_id 跟参考方不同）
    - Harbor registry 初始化（如这是某 Harbor 下第一个项目）
    - GitLab 仓库需在正确的 group 下（核查；不在产 Ops Todo）
    - argocd-apps MR 创建 + 合并（如 Step 11 因无 checkout/权限只产 handoff）
  - 用 `recipes/docs/ops-todo-table.md`；如无项目，写"(none)"
  - 用 `recipes/docs/summary-card.md` 出最终摘要
  - 摘要分开写候选文件/各 MR 状态与 runtime 验收状态；打印未完成的
    `$pending_runtime_checks[]`，不能只给 Ops Todo 而漏掉已支持的延后子流程。

[validate]
  - summary card 已打印
  - Ops Todo 已打印（表或 "(none)"）

[output]
  - 最终给用户的消息；不再写盘

## 出口

用户拿到：
  - k8s/base/* + k8s/overlays/*（提交到工作仓库）
  - .gitlab-ci.yml + service profile 的 Dockerfile，或 static-web profile 的
    nginx.conf + 每 target Dockerfile.<env-keyword>（提交到工作仓库）
  - app MR 合并后每 target 的真实 SHA/digest/platform evidence
  - argocd-apps/{cluster_dir}/{app}-{env_keyword}.yaml（每 target，以真实 SHA 提交独立 MR）
  - docs/deployment/cd-requirements.md、docs/deployment/cicd.md（提交到工作仓库）
  - Ops Todo 列表（如有 `$gitlab-issue-sop`，已建对应 issue）

若 `cd-requirements.md` 已声明 DB / 缓存 / 队列 / S3 等资源，本 workflow 已自动 chain
对应 `add-*` workflow；未声明的新资源才需要用户后续单独触发。
