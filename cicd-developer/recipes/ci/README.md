# recipes/ci/

GitLab CI 流水线与 Dockerfile 模板。具体可用模板以本目录和 `new-service.md` /
`add-ci-aws-irsa.md` 的引用为准，不维护会漂移的文件数量。

## 文件清单

| 文件 | 用途 |
|---|---|
| `gitlab-ci-shell.yml.tmpl` | CI 文件**框架**：stages + 全局 variables + Kaniko / crane hidden jobs（写一次）|
| `gitlab-ci-static-web-verify.yml.tmpl` | **per-target static-web** premerge gate：exact Node image + `npm ci` + frozen target build script；MR 与目标分支 push 都执行 |
| `gitlab-ci-legacy-target-lock.yml.tmpl` | **SRE-owned pipeline execution policy** job；中央注入到 legacy-live app pipeline，候选仓不可复制、修改、删除或绕过。若中央 policy 不可用，workflow STOP / Ops Todo。|
| `gitlab-ci-legacy-target-state-gate.yml.tmpl` | **CE-guarded 降级 gate** job：仅当实例为 GitLab CE（无法托管 pipeline execution policy）且迁移 owner 在证据工件中记录了 `enforcement_mode: ce-guarded` 授权行时，渲染进候选 `.gitlab-ci.yml`（`stage: .pre`，credential-free、digest-pinned）。是**有残余风险**的降级（候选可 shadow、非 merged-result、无 CODEOWNER 审批），实例具备 SRE policy 能力后必须替换；移除/弱化/绕过该 job 的 MR 是 STOP。|
| `gitlab-ci-target-dual-arch.yml.tmpl` | **per-target** dual-arch snippet（EKS 等）：build:<target>:amd64 + build:<target>:arm64 + manifest:<target>。每 target 一份，append 到 shell 后面 |
| `gitlab-ci-target-single-arch.yml.tmpl` | **per-target** single-arch snippet（TKE / GKE 等）：build:<target> 单 job，无 manifest fan-in |
| `gitlab-ci-aws-job.yml.tmpl` | CI job snippet：通过 Job Pod IRSA 调 AWS API |
| `protected-provider-release-infrastructure.yaml.tmpl` | 受保护 Crossplane Provider 内部发布基础设施配置：固定专用 runner 的双 GitOps authority（DEV/k8s overlay + DEV/argocd-apps Application）、仅计划且必须先确认不存在的 child filenames、无 delete/admin robot、Vault/ESO path-only credential mounts、checksum-pinned verifier、签名/来源/扫描 policy；只生成静态候选，绝不读取 token、注册 runner、发布、安装或 sync |
| `dockerfile-go.txt` | Go 应用多阶段 Dockerfile |
| `dockerfile-node.txt` | Node.js 应用 |
| `dockerfile-python.txt` | Python 应用 |
| `dockerfile-java.txt` | Java（Maven wrapper / OpenJDK）应用 |
| `dockerfile-static-web.txt` | Static-web：Node `npm ci` build + non-root Nginx runtime（container port >=1024）|
| `dockerfile-static-web-port80.txt` | Static-web source-compatibility variant：Node build + Nginx port 80 root master |
| `nginx-static.txt` | Static asset server config：exact health endpoint + explicit SPA `/index.html` or non-SPA `=404` fallback |

## 渲染模型：shell + per-target snippet

新应用 CI 是 **拼接渲染**：

1. 先写一份 shell（stages + APP_NAME + hidden job primitives，一次性）
2. static-web 每个 `$target` 先 append 一份 `gitlab-ci-static-web-verify.yml.tmpl`；
   service profile 不追加。verify job 在已审计、credential-free 的 amd64 runner 上使用
   `${HARBOR_REGISTRY}/base/node:<exact-tag>`，执行 `npm ci` + frozen target build
   script并断言 output directory 非空。job 对常见 registry/cloud/Kubernetes credential
   mount/env fail closed；它必须覆盖 MR 与目标分支 push，但不能替代仓库已有测试。
   Target image-build runner 挂载 Harbor push credential，禁止执行 MR-controlled npm
   scripts。没有 credential-free runner 或 exact Node base mirror → STOP，让 Platform
   先补 prerequisite。
3. 对每个 `$target`，按 `$target.build_mode` 选 dual-arch 还是 single-arch snippet，**append** 到同一 `.gitlab-ci.yml`，填槽：
   - `{{target_key}}` = `$target.env_keyword`（多 target 区分 job 名，如 `build:staging-us:amd64`）
   - `{{dockerfile_path}}` = service profile 的 `Dockerfile`；static-web profile 的
     target 专属 `Dockerfile.<env-keyword>`
   - `{{harbor_image_path}}` = 业务镜像 push 路径，两种写法（见 hard-rules #25）：
     - **B 默认（runner 决定 host，fleet 主流）**：`${IMAGE_BASE}/{$target.env}-{$target.region}/{$app}`，
       `IMAGE_BASE=${HARBOR_REGISTRY}/cicd`（shell 定义），host 由 runner 注入。按 clusters.yaml 的目标集群 runner tag 选择 Harbor；历史 tag/DNS alias 必须核验实际映射（hard rule #25），不能假设所有 tag 严格 1:1。
     - **A（字面 host，单 target / 老应用 grandfather）**：`{$target.harbor_url}/cicd/{$target.env}-{$target.region}/{$app}`。仍须验证 runner 与目标 artifact；部分平台凭据覆盖多个 Harbor，选错 runner 不保证认证失败。
     - 两种都 **不能用 library/**，也不能两 target 共用同一 `env-region` 段。

**为什么仍是 per-target**：新接入以目标集群自己的 Harbor 为准，不能假定跨账号/跨云镜像可拉取；存量跨集群 pull 或 DNS alias 以准确网络、凭据和镜像证据为准，不强制迁移。每个 target 在选定 runner build + push 到该目标 Harbor（B 由 runner 注入 host，A 字面指定）。**部署侧（overlay newName + Application `image-list` + `spec.source.kustomize.images`）无论 A/B 都必须是同一个字面 host**——kustomize / Image Updater 不展开 `${IMAGE_BASE}`，写变量进去会被当字面量拉取 404。详见 workflow `new-service.md` Step 6 与 hard-rules #25。

## 首次接入顺序：CI before first Application sync

新应用首次接入时，先合入 app repo 的 k8s + CI + Dockerfile 变更，跑目标分支 pipeline，
确认每个 target 的 Harbor path 都有符合 Application `allow-tags` 的真实 Git SHA tag、
immutable digest 和目标所需架构，再生成/提交独立 Application MR，将
`spec.source.kustomize.images` recovery seed 填成已验证的真实 SHA。
合并 / 同步仍须满足当前用户授权和仓库门禁。

`0000000` 仅可用于 app-repo overlay 的初始待填参数，不能写入 Application 文件或 MR。
镜像证据未就绪时只保留 pending Application contract / Ops Todo，先完成 CI/Dockerfile
准备；不能创建 draft Application 后等待 Image Updater 补齐。
这样做可以避免首次 sync 时 Rollout 或 StatefulSet 先用不存在的 seed 创建工作负载，
后续再靠 Image Updater 追状态。

## 跨模板设计原则：multi-arch 构建

新应用 CI **默认按 target 类型** 渲染（hard-rules.yaml #26/#27/#28）：

- **`build_mode: dual-arch`**：per-target amd64 + arm64 + manifest index。
- **`build_mode: single-arch-amd64`**：per-target amd64，无 manifest fan-in。

准确模式读取 `clusters.yaml`；GKE、TKE、eu-prod-data 等单架构目标不能从“非 TKE”推导成 dual-arch。

### 为什么 dual-arch target 构建两种架构

ARM64 Graviton 实例比 amd64 同档 **~25% 便宜**，spot 中标率也更高。Karpenter NodePool 可同时申请 c5/c6i（amd64）+ c7g/m7g（arm64）spot 实例，扩缩时自动挑可用 + 便宜。

如果镜像只有 amd64，Karpenter 锁死 amd64 NodePool，arm64 spot 池白白浪费。

### CI 三件套（dual-arch target）

snippet `gitlab-ci-target-dual-arch.yml.tmpl` 产 3 个 job：

```yaml
build:<target>:amd64:    # 跑在 *-amd64 runner，产 :<sha>-amd64 image
  tags: [<region>-tech-amd64]

build:<target>:arm64:    # 跑在 *-arm64 runner，产 :<sha>-arm64 image
  tags: [<region>-tech-arm64]

manifest:<target>:        # crane 合并两 arch 成 multi-arch index :<sha>
  needs: [build:<target>:amd64, build:<target>:arm64]
  tags: [<region>-tech-amd64]
```

Application 的 Image Updater annotations 选择 `manifest:<target>` 产的 tag（不带 arch 后缀）。K8s 节点拉 image 时按节点 arch 自动挑对应 manifest 层。

终态 tag 必须是纯 Git SHA（`$CI_COMMIT_SHORT_SHA` 或 `$CI_COMMIT_SHA`），以匹配
Application 的 `allow-tags: regexp:^[a-f0-9]{7,40}$`。环境隔离放在 Harbor path
`cicd/<env>-<region>/<app>`，不要把 `staging-` / `prod-` 写进 tag。

### TKE / GKE single-arch fallback

snippet `gitlab-ci-target-single-arch.yml.tmpl` 产 1 个 job：

```yaml
build:<target>:           # 只一个 job，产 amd64-only image，直接终态 tag :<sha>
  tags: [tke-amd64]       # 或 gcp-tech-amd64
```

无 manifest job。Rollout 也不写 `nodeSelector.kubernetes.io/arch`（集群本就单 arch）。

### Application Image Updater `<alias>.platforms`

平台模板在 Application annotations 中配置：

```yaml
argocd-image-updater.argoproj.io/app.platforms: linux/amd64,linux/arm64  # build_mode: dual-arch
# 或 linux/amd64（build_mode: single-arch-amd64）
```

这个字段限制 updater 检查的镜像平台；它不是 Pod 调度约束。

## 边界情况

### 反例：Rollout 加 `nodeSelector arch=amd64` + image 是 multi-arch

浪费——arm64 manifest 永远没节点拉，NodePool 锁死 amd64 单 arch。新应用 **禁止** 这个组合（hard rule #27）。`recipes/k8s/rollout.yaml.tmpl` 默认不写 nodeSelector arch。

### 部分 target 是 TKE，部分不是（mixed-arch / mixed-cloud）

snippet 是 **per-target** 设计，mixed 场景不会"统一渲染"——每个 target 按自己的 build_mode 各自渲染：

- staging-us（dual-arch） → append `gitlab-ci-target-dual-arch.yml.tmpl`，产 `build:staging-us:amd64` + `build:staging-us:arm64` + `manifest:staging-us`
- prod-cn-tke（single-arch） → append `gitlab-ci-target-single-arch.yml.tmpl`，产 `build:prod-cn-tke`（amd64 only，无 manifest）

两 target 各自 push 到 **自己的 harbor**：B 写法下两 job 的 `${IMAGE_BASE}` 文本相同但 runner tag 不同 → 经各自 runner 注入的 `HARBOR_REGISTRY` 落到不同 Harbor；A 写法下 IMAGE_PATH 是各自字面 host。TKE 集群从 `harbor-cn.addx.live` 拉，staging-us 集群从 `harbor-39070-us-staging.addx.live` 拉。准确 host 取 `clusters.yaml`，不能照搬旧 tech-service staging 例子。

### 现有 single-arch 应用

v1 时代有不少应用钉 `nodeSelector arch=amd64`，CI 单 build。**不强迫迁** ——hard rule #26 只说新应用，老 fleet 维持现状。新增目标按 `add-target-cluster.md` 的现有工作流执行；现有 target 切多 arch 需要独立核验构建产物和调度条件，不能仅删除 nodeSelector。

### crane 哪里来

CI 模板 base image：`${HARBOR_REGISTRY}/base/crane:<tag>`。如果集群 Harbor base/ 没装，跟 `DEV/base-images images.yaml` 提 MR 加一行——见 hard rule #28。

## Dockerfile 跨语言通用约定

| 约定 | 适用 |
|---|---|
| 多阶段构建（builder + runtime 分开） | 全部 |
| 用 LTS / 主版本 pin（不要 latest）| 全部 |
| Non-root user 跑 | 全部默认；static-web 已确认 container port 80 的兼容 variant 例外 |
| 静态二进制（CGO_ENABLED=0 / static binary） | Go |
| Distroless 或 alpine runtime image | Go / Java |
| Slim Debian + 多阶段（避免 alpine manylinux 兼容性）| Python |
| eclipse-temurin JRE（不用 OracleJDK） | Java |

## Static-web profile：Node 只 build，Nginx 才是 runtime

Static-web 是 `new-service.md` 的 runtime profile，不是第五种 programming language。
有 `package.json` 或 Vite 依赖不代表它一定是 static-web；只有明确 build artifact、没有
production runtime start command 的证据才能提出候选，最终仍要在
`cd-requirements.md` 确认 profile、每 target build script/output/config 和 SPA routing。

该 profile 的固定 contract：

- `package.json` / `package-lock.json` 必须被 Git 跟踪，且
  `npm ci --ignore-scripts --dry-run` 必须 PASS，证明 lockfile 与 manifest 同步并适用于
  recipe 的精确 `npm ci`；不使用 `npm install` 静默修锁。
- 每 target 渲染 `Dockerfile.<env-keyword>`，把准确 package script 和静态 output
  directory 固化。已 review 的 bundle config source 记录在 `cd-requirements.md`，由
  target build script 明确选择；不把这段自由文本插进 Dockerfile 注释。CI 的
  `DOCKERFILE` 指向该文件，image path 本身也按 target 隔离。
- Exact target build 必须生成 production-semantics bundle。Vite 可以用
  `vite build --mode=<target>`，但 effective `NODE_ENV=production` 必须能由 tracked
  script/config（含 Vite 默认 production 行为且无 override）证明；禁止
  `--mode=development`、`NODE_ENV=development` 或等价开发语义。每 target 的
  `verify:<target>:static-web` job 在 MR 和目标分支 push 上用 credential-free runner
  重跑 exact build，并证明目标 output directory 非空。
- 新服务可共用经过验证的 `nginx.conf`；parallel migration 必须生成
  `nginx.<target-overlay>.conf` 并让目标 Dockerfile 通过 `{{nginx_conf_path}}`
  精确复制，不能覆盖或复用来源环境的 Nginx 配置。
- 前端环境值是 bundle-time 公共输入。只能来自 Git 跟踪、非秘密配置或明确 build mode；
  不把 Vault、ExternalSecret、Kubernetes Secret、CI secret variable、token/password/
  private key 变成 Docker build arg/ENV 或 generated `.env`/JavaScript/JSON，也不 retag
  一个环境的 bundle 给另一个环境。static-web Nginx container 不挂 Secret
  `env`/`envFrom`/volume；需要凭据的依赖拆到 backend/BFF。
- Static recipe 使用 `COPY . .` 前必须 review effective Docker context 和框架会自动读取的
  `.env*`/config 文件。任何可能被 build script 读取的 untracked/ignored/generated
  config 或 credential-bearing 文件都必须 STOP；只能用 reviewed `.dockerignore` 排除，
  同时保留已确认的 Git-tracked public target config。
- Node 与 Nginx 使用 exact semver image tag（不是 `latest`、range、major-only 或
  major/minor-only）。CN target 仍须把同一精确 tag 映射到 reviewed Harbor base mirror。
- 最终 stage 只能是 pinned Nginx runtime；不含 Node、`node_modules` 或 production
  `npm ci`。静态文件必须从显式 `/app/<output-dir>/` copy。
- 新 contract 选 `dockerfile-static-web.txt` 的 unprivileged port 1024..65535 + `USER 101`。
  只有已确认 source container port 80 必须保持时才用 `dockerfile-static-web-port80.txt`，
  并在需求文档记录 migration owner 对标准 Nginx root-master 兼容例外的明确批准。
- `nginx-static.txt` 的 container `listen`、Kubernetes `containerPort` / Service
  `targetPort` 和 probe port 必须一致。health path 是 exact 200；SPA 显式使用
  `try_files $uri $uri/ /index.html`，非 SPA 显式 `=404`，不靠框架名猜。health path
  必须先通过 workflow 的单路径正则，不能把自由文本插入 Nginx 配置。

## CN target Dockerfile base image rule

The language Dockerfile templates show the upstream image names for readability.
When any deployment target is CN / TKE / `aws-cn`, `workflows/new-service.md`
Step 7 must rewrite every external `FROM` to `${HARBOR_REGISTRY}/base/...`,
ensure `ARG HARBOR_REGISTRY` is present, and register missing images through
`DEV/base-images` before the CN build is considered unblocked.

Do not leave `FROM gcr.io/...`, `FROM docker.io/...`, or bare upstream images
(`node:...`, `python:...`, `golang:...`, `eclipse-temurin:...`, `nginx:...`,
`nginxinc/nginx-unprivileged:...`) in a Dockerfile that will be built by a CN
target runner.

## 关联

- 失败模式诊断：`troubleshooting/image-pull-failure.md`
- hard-rules.yaml #25 / #26 / #27 / #28
- `references/data/clusters.yaml` 的 `build_mode` 字段
- `recipes/k8s/rollout.yaml.tmpl` 头注释（不写 nodeSelector arch）
