---
name: add-sentry
description: 给现有服务接 Sentry（自助流程）。开发者声明 4 个 app-scoped manifest，ArgoCD sync 时 sentry-onboard Job 自动建 Sentry project + 写 DSN 到 Vault + ExternalSecret 注入 Pod。零运维介入。
---

# Workflow：add-sentry

## 目的

给现有服务自助接 Sentry 错误监控，**完全 GitOps 化**：
- 开发者只声明 4 个 K8s manifest（app-scoped ConfigMap + SA + Job + ExternalSecret）
- ArgoCD sync 时 sentry-onboard Job 用 Vault JWT 拿 sentry admin token，调 Sentry API 建 project，把 DSN 写到 Vault platform 路径
- ExternalSecret 把 DSN 渲染成 K8s Secret，Pod env 读 `SENTRY_DSN`
- Job 幂等，每次 sync 重跑 = 自动 recover

跟独立的 `sentry-onboarding` skill 区别：
- 那个 skill 是 **老流程**（运维手动调 REST API 建 project，已过时）
- 本 workflow 是 **新自助流程**（开发者 MR 自助）
- 新应用 **必须** 走本 workflow，**不要** 用老 skill

## 进入条件

- 应用已经有 `k8s/base/` + `k8s/overlays/{env_keyword}/`
- 应用 GitLab repo 已把 `Argocd-deploy` 加为 **Reporter**（hard rule #6）；没加 → ArgoCD 拉不到代码报 `repository not found`
- 目标集群的运维前置已完成（Vault JWT role 允许 `<app>-sentry-onboard` ServiceAccount + sentry admin token + CN AWS NAT egress NodePool）；详见 `references/sentry/README.md`

## Step 1. 解析需求

[precondition]
  - cd-requirements.md 列了 Sentry 需求

[action]
  - 提取：
    - `$app`         kebab-case（Vault 路径中的 app 段；默认也是 Sentry project slug）
    - `$target.project_slug` 按 Step 2 的数据源生成；staging 复用同应用的 production 项目，必须显式设置 SDK environment 并隔离告警环境
    - `$target.dsn_host` 按 Step 2 和 SDK 实际运行网络选择事件接收 hostname；不要把管理 API URL 当作 ingest 验证
    - `$targets[]`   含 Sentry 需求的 target
    - `$platform`    Sentry platform slug，按业务代码语言：
        - 后端：`node` / `java-spring-boot` / `python-django` / `python-fastapi` / `go` ...
        - 客户端：`apple-ios` / `android` / `javascript-react` / ...
    - `$team`        Sentry 内的 team slug；**不能填默认值或凭经验猜**，必须从目标 Sentry org 的 team 列表确认（例如 prod org 常见 `backend` / `frontend`，但以实际查询为准）
    - `$sentry_onboard_image_tag` 平台批准的 sentry-onboard 不可变 tag / commit SHA；未知则 STOP 找平台确认，禁止 `latest`
    - `$route_app_type` 从 `cd-requirements.md` 读取的部署路由 slug：`c-end` / `restricted-admin` / `builder` / `data` / `observability`。只用于辅助判断，**不要**把它写进 Sentry `APP_TYPE`
    - `$sentry_app_type` 决定 prod DSN 域名改写：
        - `backend`         后端服务 Pod 内 SDK，C 端不可见——按 DSN_HOST 使用区域 relay
        - `admin`           内部管理后台——按 DSN_HOST 使用区域 relay
        - `mobile-app`      DSN 随 App 二进制发布，**prod 必须改写** `glitch-{region}.{brand}.{tld}`
        - `web-frontend`    DSN 进前端 JS bundle，同上
      默认映射：`restricted-admin -> admin`；`builder/data/observability -> backend`；`c-end` 不能自动判断，必须确认是服务端 SDK (`backend`) 还是客户端可见 (`mobile-app` / `web-frontend`)
    - `$brand`       `$sentry_app_type ∈ {mobile-app, web-frontend}` 且 env=prod 时**必填**：
                     `kiwibit` / `vicoo` / `vicohome` / `safemo`

[validate]
  - `$platform` 在 Sentry 支持列表（不确定 → 查 sentry-{region}.addx.live "New Project" 页面看完整 platform 列表）
  - `$team` 在目标 Sentry org 中真实存在；不确定就先查 Sentry API/UI team 列表，不能用 `sentry` 兜底
  - `$sentry_onboard_image_tag` 非空且不是 `latest`
  - 若设置 `PROJECT_SLUG` / `DSN_HOST`，先核验不可变镜像已支持这些变量并已分发到本集群；旧镜像会静默忽略。字段契约和跨实例迁移顺序见 `references/sentry/project-migration.md`
  - `$route_app_type ∈ {c-end, restricted-admin, builder, data, observability}`
  - `$sentry_app_type ∈ {backend, admin, mobile-app, web-frontend}`
  - 如果 `$route_app_type=c-end` 且 `$sentry_app_type=backend`，必须有明确证据说明 DSN 只在服务端 Pod 内使用；否则 STOP 问用户，不要默认
  - 如果 `$sentry_app_type` 是 `mobile-app` 或 `web-frontend` 且有 prod-cn target → **STOP**：cn 区 prod 当前无品牌 relay 域名，Job 会 fail
  - 如果 `$sentry_app_type` 是 `mobile-app` 或 `web-frontend` 且有 prod target：BRAND 必填

[output]
  - 内存变量

## Step 2. 解析每 target 的 Sentry 实例

[precondition]
  - Step 1 完成

[action]
  - 对每 `$target`，从 `references/data/env-keywords.yaml` 读取应用 env 和 cluster，再从 `references/data/clusters.yaml` 读取 cluster.region；以 region 查询 `references/data/sentry-instances.yaml` 的 `regions`，得到 INSTANCE、管理 API URL、server_dsn_host，再按 `ingest` 的 SDK 类型策略选择 DSN_HOST。该文件是 Sentry 路由的唯一数据源，不在 workflow 重复维护实例表。
  - 新接入 staging/prod 均使用本区域 prod Sentry。按同一数据源的 `environments` 生成 PROJECT_SLUG：staging/prod 均为 `<app>`，通过 SDK environment 区分环境；应用 ENV、Vault 域、schema 和路径不随 Sentry 实例改变。staging-us-data / prod-us-data、staging-eu-data / prod-eu-data 同样使用所在 cluster.region；staging-cn-tke 使用 cn-k8s 的 region=cn、ENV=staging。
  - 新 staging 配置显式启用 PROJECT_SLUG 与 DSN_HOST；不要为新应用选择旧 staging 实例，历史实例只用于明确的迁移调查。按 SDK 运行位置选择 ingest：
    - `backend/admin`：使用本区 `server_dsn_host`，从实际应用网络验证。三区 regional relay 为私网入口，不能直接套给移动端或浏览器。
    - staging mobile-app/web-frontend：使用 `<app>` 并保留 `ENV=staging`，但 DSN_HOST 必须显式填写经确认的品牌公网 ingest hostname；可从现有品牌映射取得候选，仍需实际设备/浏览器网络验证。尚未确认品牌、公网 ingest 或实际终端可达时 STOP，禁止回退到 server_dsn_host。staging 的 BRAND 必须省略（非空会被拒绝），品牌 host 通过 DSN_HOST 显式设置。
    - prod mobile-app/web-frontend：继续由 BRAND 决定品牌域名，不用通用 relay 覆盖；保留 CN 无品牌 relay 的 STOP 规则。
  - `known_teams` 只是已知候选（US/EU backend/frontend，CN backend/team-zlin/team-mwang2）；仍先查询目标 org 并核验项目归属，不能自动任选 team。

  - 对每 `$target` 还要解析：
    - `$target.harbor_url`            sentry-onboard 镜像所在 harbor（查 `references/data/clusters.yaml -> clusters[].harbor_url`）
    - `$target.sentry_onboard_image`  sentry-onboard 镜像**完整路径（不含 tag）= 本集群 Harbor host + `/base/sentry-onboard`**：取 `$target.harbor_url`（即 `references/data/clusters.yaml -> clusters[].harbor_url`，如 `harbor-00249-us-tech.addx.live`；TKE 是 `harbor-cn.addx.live`）拼上 `/base/sentry-onboard`。统一 `base/` 工具镜像，DEV/base-images 扇出到**所有**集群（含 3 个 staging EKS + eu-prod-data + TKE），EKS / TKE 同一条规则，不分集群。Kyverno `require-harbor-image-path` 全 fleet 放行 `base/`，无需豁免
    - `$target.vault_addr`            sentry-onboard Job 访问 Vault 的完整 URL（含 `https://`；查 `references/sentry/README.md` 配置表；ops US/EU EKS 用 internal URL，builder staging 用 `vault-<region>.builder.addx.live`，AWS CN 用公网域名走 NAT，TKE 用 `https://vault-cn-internal.addx.live`）
    - `$target.vault_k8s_mount`       Vault JWT auth mount（查 `references/sentry/README.md` 配置表；如 `jwt-eks-tech-service` / `jwt-eks-prod` 等；前置由运维配）
    - `$target.sentry_dsn_css`        ExternalSecret 读 Sentry DSN 的 ClusterSecretStore（查 `references/sentry/README.md` 配置表；必须和 `$target.vault_addr` 指向同一个 Vault 实例，不能按 `vault-backend` 名字猜 Vault 域）
    - `$target.is_aws_cn`             cn-prod / cn-tech-service / cn-dev / cn-staging 需要 NAT egress NodePool 调度

[validate]
  - 每 target 都解析到 `$target.sentry_onboard_image`（统一 `<harbor>/base/sentry-onboard`）+ INSTANCE + Sentry URL + Vault 字段 + Sentry DSN ClusterSecretStore
  - region/env 未被 `sentry-instances.yaml` 覆盖则 STOP，不回退到旧 staging endpoint；PROJECT_SLUG 超过 64 字符或与现有项目归属冲突时 STOP，不能截断或认领其他应用；同应用的 production 项目核验归属后复用
  - 复用项目不改变其团队、权限或数据隔离设置；先核验团队归属、首个 client key 已启用和告警环境。未限定环境的生产 issue alerts 加 `environment does not contain staging`（仅在保持原 filterMatch 逻辑时）；已有明确环境的规则保持原值；metric alerts 也须排除 staging。SDK environment 与 Vault ENV 是两件事，实际事件回读必须证明生效，JVM 显式属性/浏览器构建常量可能覆盖环境变量。
  - 当前镜像仍拒绝“项目已存在、此环境 Vault 无记录”。首次复用须由已授权 operator 核验同应用/团队、实际 ingest 和 staging Vault 归属后，用 CAS 仅初始化对应 staging DSN 字段，再重跑 Job；不能关闭保护、删除已有记录或让新接入流程静默认领项目。此步骤未完成则明确报告待初始化，不声明自动接入成功。
  - 新 staging Job 的实际 Vault 必须含区域 prod endpoint/token；镜像至少含数据源记录的 capability commit，并已在本集群 Harbor 核验，不用旧版本忽略字段

[output]
  - `$targets[]` 完整

## Step 3. 计算 Vault 路径

[precondition]
  - Step 2 完成

[action]
  - 对每 `$target`，调 `references/vault-paths/resolver.md` resolver：
      `env=$target.env, kind=sentry, app=$app, key=project`
  - 期望路径 `secret/{env}/sentry/application/{app}/project`（platform schema，**强制**，新接入不要走 legacy）

[validate]
  - 路径匹配 vault-paths/rules.yaml Rule 2 platform=sentry

[output]
  - 每 target 一个 Vault 路径

## Step 4. 写 4 个 manifest（每 target 循环）

[precondition]
  - Step 3 完成

[action]
  - **按 `$target.cluster.cloud` 选 recipe 变体**（拆 recipe 避免 hand-edit；详 `references/sentry/README.md` TKE 差异表）：
    - `cloud == aws`（所有 EKS 集群，含 3 个 staging EKS + eu-prod-data）→ ConfigMap 用 `recipes/sentry/onboard-config-eks.yaml.tmpl`，Job 用 `onboard-job-eks.yaml.tmpl`，`{{image}}=$target.sentry_onboard_image`（统一 `<harbor>/base/sentry-onboard`）
    - `cloud == tencent`（TKE cn-main）→ ConfigMap 用 `recipes/sentry/onboard-config-tke.yaml.tmpl`，Job 用 `onboard-job-tke.yaml.tmpl`（TKE 变体已**预设**了固定 VAULT_ADDR / `jwt-tke-cn-main` mount / image base/ 前缀 / projected token / nodeSelector / imagePullSecrets）
    - `cloud == gcp` → 暂未支持 sentry 自助；STOP 产 Ops Todo

  - 用对应 ConfigMap recipe，填槽（EKS）：
      `{{app}}=$app`、`{{platform}}=$platform`、`{{instance}}=$target.instance_key`、
      `{{team}}=$team`、`{{sentry_app_type}}=$sentry_app_type`、`{{env}}=$target.env`、
      `{{brand}}=$brand`（仅 mobile-app/web-frontend + prod）、
      `{{vault_addr}}=$target.vault_addr`、`{{vault_k8s_mount}}=$target.vault_k8s_mount`
      - 不要再给 `{{vault_addr}}` 手动补 `https://`；配置表的值已经是完整 URL
    TKE 变体填：`{{app}}`、`{{platform}}`、`{{team}}`、`{{sentry_app_type}}`、`{{env}}=$target.env`、`{{brand}}`（Vault/mount/INSTANCE 固定于 cn-k8s，ENV 不能固定为 prod）
    两种变体都按 Step 2 启用相应注释行并填 `{{project_slug}}=$target.project_slug`、`{{dsn_host}}=$target.dsn_host`：新 staging 必须启用两行；prod backend/admin 启用 DSN_HOST，PROJECT_SLUG 可省略而使用 APP；C 端 prod 的 DSN 继续由 BRAND 规则决定。资源名和 ExternalSecret 路径继续使用 `$app`，不能替换成 project slug。
    写到 `k8s/overlays/{$target.env_keyword}/sentry/sentry-onboard-config.yaml`

  - 用 `recipes/sentry/onboard-sa.yaml.tmpl`（ServiceAccount，EKS/TKE 通用），填 `{{app}}=$app`，写到 `.../sentry/sentry-onboard-sa.yaml`

  - 用对应 Job recipe，填槽：
    - EKS：`{{app}}=$app`、`{{image}}=$target.sentry_onboard_image`（统一 `<harbor>/base/sentry-onboard`，取本集群 `harbor_url` 拼 `/base/sentry-onboard`）、`{{sentry_onboard_image_tag}}=$sentry_onboard_image_tag`
      - 若 `$target.is_aws_cn == true`，同时填可选槽：
        `{{aws_cn_node_group}}=$target.aws_cn_node_group`、
        `{{aws_cn_toleration_key}}=$target.aws_cn_toleration_key`、
        `{{aws_cn_toleration_value}}=$target.aws_cn_toleration_value`
      - 非 AWS CN 集群删除 / 保持注释中的 nodeSelector/tolerations 占位，不渲染成生效字段
    - TKE：`{{app}}=$app`、`{{sentry_onboard_image_tag}}=$sentry_onboard_image_tag`（其他字段固定）
    写到 `.../sentry/sentry-onboard-job.yaml`

  - 用 `recipes/sentry/externalsecret.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{env}}=$target.env`、`{{namespace}}=$target.namespace`、
      `{{cluster_secret_store}}=$target.sentry_dsn_css`
    写到 `.../sentry/sentry-externalsecret.yaml`

  - 加 `sentry/` 子目录到 `k8s/overlays/{$target.env_keyword}/kustomization.yaml`：
      ```yaml
      resources:
        ...
        - sentry/sentry-onboard-config.yaml
        - sentry/sentry-onboard-sa.yaml
        - sentry/sentry-onboard-job.yaml
        - sentry/sentry-externalsecret.yaml
      ```

[validate]
  - 4 文件存在
  - `kustomize build k8s/overlays/{$target.env_keyword}/` 成功
  - 资源名必须 app-scoped，避免共享 namespace 下 ArgoCD ownership 冲突：
    - ServiceAccount / Job：`$app-sentry-onboard`
    - ConfigMap：`$app-sentry-onboard-config`
    - ExternalSecret / Secret target：`$app-sentry-dsn`
  - Job container 必须有 `resources.requests/limits.{cpu,memory}`，否则 Kyverno `require-resources` 会拒绝 hook Job
  - Job 必须显式 `imagePullSecrets: harbor-registry-secret`，并使用 `hook-delete-policy: HookSucceeded,HookFailed`（**不是** BeforeHookCreation——避免撞 peer-wave 失败产生僵尸 op；两个都开让成功+失败 Job 都即时清，失败 Job 不残留毒化后续 resync，见 onboard-job 模板注释）
    - 边角：残留的终态 Job 升 image tag 时会因 `spec.template` immutable 卡 sync；两个 policy 都开后终态 Job 即清，基本不出现，万一遇到 `kubectl delete job` 删旧 Job 再 sync（见 troubleshooting/sentry-onboard-issues.md）
  - Job image tag 必须是 `$sentry_onboard_image_tag`，不能是 `:latest`
  - 可选 `PROJECT_SLUG` / `DSN_HOST` 通过 `check_sentry_project_config.py`；该静态检查不能证明镜像支持、Vault endpoint/token 或真实网络已就绪
  - staging 配置必须是区域 prod INSTANCE、同应用 PROJECT_SLUG、ENV=staging；SDK 必须实际报告 staging（可含区域后缀），不能缺省或标为 production；DSN_HOST 按 `ingest` 策略检查，backend/admin 用已验证 server_dsn_host，移动端/浏览器用已验证品牌公网 host，不能把私网 relay 作为客户端默认值。TKE staging 也写 staging Vault path，不能从 prod 模板继承 ENV=prod
  - ExternalSecret `refreshInterval` 必须是 `1m`，避免 Job 写入 Vault 后 Pod 长时间卡在缺 Secret
  - ConfigMap.VAULT_PATH_SCHEMA 是 `platform`（**必须** 跟 ExternalSecret.remoteRef.key 的 `sentry/application/<app>/project` 路径一致——E2E 实测两端不一致 Job 写新路径 ES 拉旧路径 = `$app-sentry-dsn` Secret 永远空）
  - ExternalSecret.secretStoreRef.name == `$target.sentry_dsn_css`（不能用通用 `clusters.yaml -> vault_css` 替代；尤其 cn-dev builder cutover 后，普通 app CSS `vault-builder-backend` 指向 builder Vault，必须先确认和 sentry-onboard Job 写入的是同一个 Vault 实例）

[output]
  - 4 个文件 per target

## Step 5. 把 SENTRY_DSN 注入 workload

[precondition]
  - Step 4 完成

[action]
  - ⚠️ **不变量：`SENTRY_DSN` env 必须和它的 `<app>-sentry-dsn` ExternalSecret 同一个 overlay**。ExternalSecret 是 per-target overlay 渲的（Step 4），所以引用它的 `SENTRY_DSN` secretKeyRef **只能写进该 target 的 overlay patch，绝不写进共享 `k8s/base/`**——否则同 repo 里任何**没渲 ExternalSecret 的 overlay**（即没接 Sentry 的 target）会从 base 继承到 `SENTRY_DSN` 却无对应 Secret → 主 workload 永卡 `CreateContainerConfigError`（混合 target 实测 bug；`secretKeyRef` 默认 `optional:false`）。
  - 找应用主 workload manifest 定位要 patch 的 container：优先 `k8s/base/rollout.yaml`；有状态服务 `k8s/base/statefulset.yaml`；老应用原生 Deployment 用 `k8s/base/deployment.yaml`——**仅用于确定 container 名，env 不写这里**。
  - 在**每个渲染了 Sentry 的 target 的 overlay patch**（`k8s/overlays/{$target.env_keyword}/`，跟 `SENTRY_ENVIRONMENT` 同一个 patch）给该 container 加（该 target 已有 `SENTRY_DSN` 则仅跳过重复注入；仍核验项目、SDK environment 和告警环境）：
        ```yaml
        env:
          - name: SENTRY_DSN
            valueFrom:
              secretKeyRef:
                name: {{app}}-sentry-dsn
                key: dsn
          - name: SENTRY_ENVIRONMENT
            value: {{env}}        # 跟 ConfigMap.ENV 一致；写进 overlay patch（非 base）
        ```
  - `SENTRY_DSN` 与 `SENTRY_ENVIRONMENT` 都是 per-target overlay patch，**都不写死在 base**（base 被所有 overlay 继承，含任何没接 Sentry 的 overlay）
  - 如果同一 repo 同时有 Rollout / StatefulSet / Deployment，必须只改实际承载该 app 的 workload；不确定就 STOP 问用户，不要把 DSN 注入到 sidecar / hook Job

[validate]
  - 对**每个** target overlay 跑 `kustomize build k8s/overlays/<target>/`：
    - 接 Sentry 的 target：渲染出的主 workload 含 `SENTRY_DSN` secretKeyRef **且**同 overlay 有 `<app>-sentry-dsn` ExternalSecret
    - 没接 Sentry 的 target：**既无 `SENTRY_DSN` 也无 ExternalSecret**（确认没从 base 漏继承 `SENTRY_DSN`——否则该 overlay 的 Pod 会卡 `CreateContainerConfigError`）
  - 不要只检查文件名

[output]
  - 每个正常 target 的 overlay patch 更新（含 `SENTRY_DSN` + `SENTRY_ENVIRONMENT`）；base workload **不加** Sentry env

## Step 6. 全量 validator

[precondition]
  - Step 5 完成

[action]
  - `bash "$skill_root/validators/validate.sh" k8s/`

[validate]
  - 退出 0
  - `python3 "$skill_root/validators/check_vault_paths.py" k8s/` 看到的 vault 路径都匹配 Rule 2 platform=sentry

[output]
  - validator 日志

## Step 7. 更新文档 + summary

[precondition]
  - Step 6 通过

[action]
  - cd-requirements.md 加 Sentry 段：
      - platform / team / sentry_app_type / brand（如适用）
      - 每 target 的 Sentry 实例 + 有效 project slug + 必要 ingest host + Vault 路径（不记录完整 DSN/token）
  - cicd.md append "Sentry 自助接入" 段
  - Ops Todo：如果目标集群运维前置（Vault JWT/K8s role 允许 `<app>-sentry-onboard` / sentry admin token / CN NAT NodePool / CoreDNS rewrite）**没** 就绪 → 加 todo
  - summary

[validate]
  - summary 已打印

[output]
  - 最终用户消息

## 出口

ArgoCD sync 后：
- sentry-onboard Job 跑（每次 sync 重跑，幂等）
- Job 用 K8s ServiceAccount JWT 换 Vault token（mount 路径预设）
- Job 拿 sentry admin token 从 `secret/cicd/sentry/tokens/<instance>`
- Job 调 Sentry API `POST /api/0/teams/{org}/{team}/projects/` 建 project（已存在则 idempotent）
- 如果 Job 日志里 `GET /api/0/teams/{org}/{team}/projects/` 返回 404，优先怀疑 `$team` 写错或该 org 没这个 team；先查 team 列表，不要改 project slug 硬绕
- Job 把 DSN 写 Vault platform 路径
- ExternalSecret 1min 内读到 → 渲染 K8s Secret `<app>-sentry-dsn`
- Pod env `SENTRY_DSN` 拿到（已存在 pod 需要 rollout 一次 / 改 cd-requirements.md 加 refreshInterval=1m 后自动 fresh）
- 如果同一次 sync 里 Rollout 先启动，Pod 可能短暂 `CreateContainerConfigError: secret "<app>-sentry-dsn" not found`；Job 成功 + ExternalSecret Ready 后应自动恢复。超过 2 分钟仍未恢复，跳 `troubleshooting/sentry-onboard-issues.md`

**常见踩坑** —— 跳 `troubleshooting/sentry-onboard-issues.md`：
- Job ImagePullBackOff（镜像路径错——统一是 `<harbor>/base/sentry-onboard`，host 写成别的集群或漏 `base/` 段）
- ArgoCD sync 卡 `Job ... is invalid: spec.template: Invalid value ... immutable`（残留 Job 升 image tag 时挡住更新；`HookSucceeded,HookFailed` 下终态 Job 即清基本不出现，万一遇到 `kubectl delete job` 删旧 Job 再 sync）
- Job vault login 400/403（JWT role 没配 / bound_audiences 错）
- Job slug collision（Sentry 有同 slug project 但 Vault 无记录：可能是首次共享接入或归属冲突，须核验后由 operator 初始化）
- Pod SENTRY_DSN 为空（VAULT_PATH_SCHEMA 跟 ES key 不一致）
- AWS CN Pod 504 timeout（没加 NAT egress NodePool 调度）

## 老应用迁移说明

staging → prod 实例合并是平台迁移，先读 [项目与实例迁移](../references/sentry/project-migration.md)，由 `sentry-onboarding` 协调目标项目预建、Vault 和旧 Job 防回写；不要直接重跑本新应用流程改变已有 app/schema。

2026-04-30 之前接入的应用走 legacy 路径 `secret/{env}/app/<app>/sentry-dsn`。**不要主动迁** ——老应用 ConfigMap **不写** `VAULT_PATH_SCHEMA` 字段（默认 legacy），ExternalSecret 的 `remoteRef.key` 用 legacy 路径。新接入应用 100% 走 platform 路径（本 workflow 写出的就是 platform 模式）。
