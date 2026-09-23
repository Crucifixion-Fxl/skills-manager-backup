---
name: add-s3-bucket
description: 给现有服务加 AWS S3 Bucket（含 PublicAccessBlock + SSE + Versioning + Lifecycle）+ IRSA 权限。一个 bucket 用途单一（data / assets / logs 拆开建）。
---

# Workflow：add-s3-bucket

## 目的

通过 Crossplane GitOps 给现有服务建 S3 Bucket。前置：app 已经有 IRSA Role（没有先跑 `add-irsa-role.md`），本 workflow 给 IRSA Role 挂 S3 权限。

末态：
- AWS S3 Bucket 存在（含 PublicAccessBlock + 默认加密 + versioning + lifecycle）
- Crossplane IRSA RolePolicy 给 Pod 加 S3 操作权限（按实际需要：仅 read / read+write / read+write+delete）
- bucket 信息（name + region）通过 ConfigMap 或环境变量交给 app

## 进入条件

- 应用已有 `k8s/base/` + overlay
- 应用已有 IRSA Role（`crossplane-infra/<cluster_dir>/{$app}-irsa.yaml` 已存在）
- cd-requirements.md 列了 S3 需求
- crossplane-infra MR 可同时声明 per-app ProviderConfig / IAM RolePolicy；这些 IAM / ProviderConfig YAML 不放进应用仓库
- app-owned S3 Bucket / BucketPolicy / PublicAccessBlock / SSE / Versioning / Lifecycle 放应用仓库 overlay，不放 crossplane-infra；crossplane-infra 只放管理这些 bucket 所需的权限
- 如果需求是"本 app 访问另一个 app 已拥有的 bucket"，**不要** 为 consumer 另建
  bucket：转为 existing-bucket access 模式，consumer 侧走 `add-irsa-role.md`，
  owner 侧只在 bucket owner 应用仓库 overlay 补 BucketPolicy。

## Step 1. 解析需求

[precondition]
  - cd-requirements.md 有 S3 条目

[action]
  - 提取：
    - `$app`            kebab-case
    - `$targets[]`      含 S3 的 target
    - `$purpose`        bucket 用途：data / assets / logs / backups（影响 bucket 名 + lifecycle 规则）
    - `$ops_needed`     权限列表：read / write / delete / list / multipart-upload
    - `$lifecycle`      lifecycle 规则（staging 必填 30d 过期；prod 业务决策）
    - `$bucket_owner`   self / another-app / platform-existing

[validate]
  - `$bucket_owner == self` 时，`$purpose` 是已知用途之一
  - `$bucket_owner == self` 且 target 是 staging / pre：必须有 lifecycle 规则（cost-tiering/s3.yaml 强制）
  - `$ops_needed` 没要求 "public" 之类（违反硬红线）
  - `$bucket_owner != self` 时，不进入 Step 2.5 / Step 3 创建 bucket；记录 owner app、
    owner account、bucket、prefix 后跳到 existing-bucket access 分支

[output]
  - 内存变量

## Step 1.5. existing-bucket access 分支（不创建 bucket）

[precondition]
  - Step 1 判定 `$bucket_owner != self`

[action]
  - 选择一个 bucket owner，bucket CR / BucketPolicy / PublicAccessBlock / SSE /
    Versioning / Lifecycle 都留在 owner 应用仓库 overlay。
  - consumer 所在账号写自己的 IRSA Role + RolePolicy（`add-irsa-role.md`）。
  - owner 侧用 `recipes/crossplane/s3-bucket-policy-read.yaml.tmpl` 追加
    BucketPolicy，Principal 是 consumer IRSA role ARN，不是 account root / `*`。
  - owner 与 consumer 若同 partition 跨账号：走 consumer IRSA + owner BucketPolicy。
  - owner 与 consumer 若不同 partition（`aws` ↔ `aws-cn`）：STOP，IRSA / OIDC /
    STS trust 不互通；产 Ops Todo 走 Vault AK/SK 或云厂商原生凭据方案。

[validate]
  - 没有在 consumer 应用仓库或 crossplane-infra 新建 bucket CR
  - BucketPolicy 与 bucket owner overlay 同目录，`providerConfigRef.name` 是 owner app
  - BucketPolicy Principal 是具名 role ARN；PublicAccessBlock 不为此关闭

[output]
  - consumer IRSA MR + owner BucketPolicy MR 的合并顺序写进 cd-requirements.md

## Step 2. 决定 bucket 名 + tier

[precondition]
  - Step 1 完成

[action]
  - 对每 `$target`：
    - bucket 名：`{$app}-{$target.env_keyword}-{$purpose}`（全局唯一，**immutable**）
    - 查 `cost-tiering/s3.yaml -> $target.env` 取 `versioning` / `encryption` / `lifecycle_required` / `access_logging`
    - prod env → 跑 `_global.yaml -> prod_self_check`，任何 "no" STOP
    - 查 `clusters.yaml -> clusters[<target.cluster>]` 取 `cloud`、`region`、`aws_region`、`account_id`、`partition`
      - `region` 是业务短码（us / eu / cn / sg），只用于 env keyword / 文档
      - `aws_region` 是 AWS API region（us-east-1 / eu-central-1 / cn-northwest-1），必须用于 S3 `forProvider.region`
      - **`cloud != aws`** → STOP 产 Ops Todo "GCP / TKE 集群没有 AWS S3 + Crossplane 通道；用户场景需要对象存储应转换成 GCS / 腾讯云 COS，由运维评估或本 workflow 跳过该 target"。**继续往下处理其他 aws target，不要给非 aws target 强行写 AWS S3 manifest**
      - `cloud == aws` 但 `partition` 字段不存在 → 数据错乱，STOP 让用户先修 clusters.yaml

[validate]
  - 每 bucket 名全球唯一（搜 git 看是否冲突；S3 API 也能查但慢，git 反查够用）

[output]
  - `$targets[]` 完整解析

## Step 2.5. 确保 per-app ProviderConfig + S3 管理权限（每集群一次）

[precondition]
  - Step 2 完成

[action]
  - 检查 `crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml` 是否已有：
    - `kind: Role` / `metadata.name: crossplane-app-{$app}`
    - `kind: ClusterProviderConfig` / `metadata.name: {$app}`
    - S3 管理 RolePolicy `crossplane-app-{$app}-s3-{$purpose}`
  - 缺 Role / ProviderConfig → 用 `recipes/crossplane/app-providerconfig.yaml.tmpl` 写入或 append
  - 缺 S3 管理 RolePolicy → 用 `recipes/crossplane/s3-rolepolicy.yaml.tmpl` 写入或 append
  - 这一步写的是 **Crossplane provider 代建 bucket 的权限**，不是 Pod runtime IRSA 权限；Pod 权限仍在 Step 4 写到 `<app>-irsa.yaml`

[validate]
  - `providerConfigRef.name: {$app}` 的 S3 CR 对应的 `ClusterProviderConfig {$app}` 已存在，或在同一轮 crossplane-infra MR 中声明
  - S3 管理 RolePolicy 的 Allow 资源只 scoped 到 `$bucket_name` 的 bucket ARN，
    不包含 `$bucket_name/*` 对象 ARN
  - S3 管理 RolePolicy 使用 recipe 的显式桶级 action 清单，不得出现 `s3:*`、
    `s3:DeleteBucket` Allow 或对象数据面 action；`s3:DeleteBucket` 必须保持显式 Deny
  - YAML 可解析；`providerConfigRef.kind` 是 `ClusterProviderConfig`

[output]
  - crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml 更新

## Step 3. 写 S3 Bucket（每 target 循环）

[precondition]
  - Step 2.5 完成

[action]
  - 用 `recipes/crossplane/s3-bucket.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{env_keyword}}=$target.env_keyword`、
      `{{bucket_name}}=$target.bucket_name`、`{{region}}=$target.aws_region`、
      `{{account_id}}=$target.account_id`、`{{partition}}=$target.partition`、`{{encryption}}=$target.encryption`、
      `{{versioning_status}}=$target.versioning_status`、`{{lifecycle_rules}}=$lifecycle`
  - `{{versioning_status}}` 由 Step 2 取到的 cost-tiering `versioning` 映射：`false → Suspended`、
    `true/Enabled → Enabled`、prod `business-decision` 走 `_global.yaml -> prod_self_check` 后决定（默认 `Suspended`）
  - Bucket `managementPolicies` 保持模板默认的 `["Observe", "Create", "Update", "LateInitialize"]`，所有环境都不允许 `Delete` / `*`
  - 写到应用仓库 `k8s/overlays/{$target.env_keyword}/s3-bucket.yaml`；如同一 overlay 有多个 bucket，用 `s3-{$purpose}-bucket.yaml`
  - 加入应用仓库 overlay `kustomization.yaml` 的 `resources`
  - 一个 yaml 含 5 doc（Bucket + PublicAccessBlock + SSE + Versioning + Lifecycle）

[validate]
  - YAML 多 doc 可解析
  - Bucket `spec.managementPolicies` 非空，且不包含 `Delete` / `*`
  - PublicAccessBlock 4 字段都 true（**任何** 一个 false 就违反硬红线）
  - BucketLifecycleConfiguration 写 `expectedBucketOwner: "$target.account_id"`，账号 ID 必须是字符串，避免前导 0 丢失和 provider adopt diff

[output]
  - k8s/overlays/{$target.env_keyword}/s3-bucket.yaml（或 `s3-{$purpose}-bucket.yaml`）

## Step 4. 给 IRSA Role 挂 S3 权限（每集群一次）

[precondition]
  - Step 3 完成；app 的 IRSA Role 存在

[action]
  - 用 `recipes/crossplane/irsa-rolepolicy.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{service}}=s3-{$purpose}`、
      `{{policy_json}}=<未缩进原始 JSON>`、
      `{{policy_json_indented_6}}=`（**整体 JSON 每行加 6 空格缩进**，详 recipe 头部"缩进规则"段）：
        ```json
        {
          "Version": "2012-10-17",
          "Statement": [{
            "Effect": "Allow",
            "Action": [<根据 $ops_needed 列>],
            "Resource": [
              "arn:{{partition}}:s3:::{{bucket_name}}",
              "arn:{{partition}}:s3:::{{bucket_name}}/*"
            ]
          }]
        }
        ```
      `$ops_needed` 到 Action 映射：
        - read    → s3:GetObject, s3:ListBucket
        - write   → s3:PutObject
        - delete  → s3:DeleteObject
        - multipart → s3:AbortMultipartUpload, s3:ListMultipartUploadParts
  - 写到 `crossplane-infra/{$target.cluster_dir}/{$app}-irsa.yaml`（append doc）

[validate]
  - YAML 可解析
  - Action 列表都在 CrossplaneAppBoundary 允许范围内（s3:* 类基本都允许）

[output]
  - crossplane-infra/{$target.cluster_dir}/{$app}-irsa.yaml 更新

## Step 5. 把 bucket name 注入 app

[precondition]
  - Step 4 完成

[action]
  - 选其一：
    - **ConfigMap**（推荐，bucket name 非敏感）：
      1. 写 `k8s/overlays/{$target.env_keyword}/configmap.yaml`（用 `recipes/k8s/configmap.yaml.tmpl`），data 段加 `BUCKET_NAME_{$PURPOSE_UPPER}: $target.bucket_name` + `AWS_REGION: $target.aws_region`
      2. 加到 overlay kustomization.yaml 的 resources 列
      3. **检查 `k8s/base/rollout.yaml` 是否已有 `envFrom: configMapRef: name: {$app}-config`**：
         - 没有 → 加上（一次性 base 编辑，所有 overlay 继承）
         - 已有 → 跳过
    - **环境变量 patches**：在 overlay kustomization.yaml 加 patch 直接 set Rollout container env

[validate-after-action]
  - `kustomize build k8s/overlays/{$target.env_keyword}/` 渲染出的 Rollout pod env 含 `BUCKET_NAME_*`
  - 如不含 → 90% 是 base 缺 envFrom 引用，去修

[validate]
  - `kustomize build` 渲染出的 Rollout pod env 含 `BUCKET_NAME_*`

[output]
  - k8s/overlays/{$target.env_keyword}/configmap.yaml 更新

## Step 6. validator + 文档 + summary

[precondition]
  - Step 5 完成

[action]
  - `bash "$skill_root/validators/validate.sh" k8s/` 全过；应用仓库跑 `kustomize build k8s/overlays/{$target.env_keyword}/`，crossplane-infra 跑对应校验
  - cd-requirements.md "Object Storage" 段填 bucket 名 + 用途 + 月度成本估
  - cicd.md append S3 接入说明
  - 如需要 CloudFront 暴露 bucket → 下一步 chain `workflows/add-cloudfront.md`；只有 DNS / ACM / signed URL key material 进入 Ops Todo
  - summary

[validate]
  - validator 全 0；文档无 `{{...}}`

[output]
  - 最终消息

## 出口

ArgoCD sync 后：
- Crossplane 建 S3 Bucket（秒级）
- PublicAccessBlock 自动生效
- 默认加密 SSE-AES256
- Versioning 配置生效（dev/staging/pre 默认 Suspended；prod 按需 Enabled）
- Lifecycle 规则应用（staging 临时产物 30d 过期）
- IRSA RolePolicy 给 Pod 加 S3 权限
- Pod 通过 SDK 调 S3（AssumeRoleWithWebIdentity 自动），ARN 范围限定 `{{bucket_name}}/*`

**常见踩坑**：
- 只在应用仓库写 S3 Bucket，没在 crossplane-infra 写 per-app ProviderConfig / S3 管理 RolePolicy → S3 CR 会卡在 `ClusterProviderConfig "<app>" not found` 或 AssumeRole AccessDenied。先补 `app-providerconfig.yaml.tmpl` + `s3-rolepolicy.yaml.tmpl`。
- 把 app-owned S3 Bucket 本体写进 crossplane-infra → 不符合现有 app 约定；应迁回应用仓库 overlay，crossplane-infra 只保留 IAM / ProviderConfig / IRSA。
- consumer 访问 owner bucket 时给 consumer 新建同名/相似 bucket → 错；bucket owner 是
  单一事实源，consumer 只拿最小 IRSA 权限，owner bucket policy 显式 allow consumer role。
- bucket 名冲突 → AWS 返 `BucketAlreadyExists`；改名（按 `{$app}-{$env_keyword}-{$purpose}-<random>` 之类）
- bucket name 改了之后想"回退" → 7d+1h 才能复用同名（AWS quirk）
- public download 需求 → 不开 PublicAccessBlock 一项一项关；普通 CDN 读走 `add-cloudfront.md`，signed URL key material / DNS / ACM 才产 Ops Todo
- 多 region 复制 → S3 跨 region replication 是另一 Crossplane CR；本 workflow 不覆盖，产 Ops Todo
