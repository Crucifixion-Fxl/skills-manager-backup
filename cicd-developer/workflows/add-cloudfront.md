---
name: add-cloudfront
description: 给 app-owned private S3 bucket 加 CloudFront CDN（OAC + bucket policy + optional custom domain）。两阶段完成：bootstrap 后必须回填 Distribution ID 并收紧 SourceArn。
---

# Workflow：add-cloudfront

## 目的

给已经存在或同轮创建的 app-owned private S3 bucket 增加 CloudFront CDN。当前支持 **S3 private origin + Origin Access Control (OAC)**；不支持 ALB/custom origin、多 origin routing、Lambda@Edge、CloudFront Functions、KeyGroup signed URL 自助生成。

末态：
- S3 bucket 仍启用 PublicAccessBlock，不直接 public
- CloudFront Distribution 通过 OAC 读取 S3
- BucketPolicy 最终用 `AWS:SourceArn` 收紧到具体 Distribution ARN
- app config 拿到 `CDN_BASE_URL`
- DNS / ACM / signed URL key material 如未就绪，产 Ops Todo，不现场猜

## 进入条件

- S3 bucket 已由 app overlay 管理，或同一轮先跑 `add-s3-bucket.md`
- bucket 与 CloudFront 同 AWS commercial partition；`aws-cn` / 非 AWS 目标 STOP，走独立 CDN 方案评估
- crossplane-infra 可同时提交 per-app ProviderConfig / CloudFront RolePolicy
- 用户已说明是否需要 custom domain；如果需要，必须提供 hostname 和 us-east-1 ACM cert ARN，DNS 仍是 Ops Todo

## Step 1. 解析需求

[precondition]
  - cd-requirements.md 有 CDN / public download / CloudFront 需求

[action]
  - 提取：
    - `$app`
    - `$target.env_keyword`
    - `$target.aws_region`
    - `$target.account_id`
    - `$target.partition`
    - `$bucket_name`
    - `$cdn_name`，默认 `{$app}-{$target.env_keyword}-cdn`
    - `$origin_domain_name`，格式 `{$bucket_name}.s3.{$target.aws_region}.amazonaws.com`
    - `$custom_domain` 可选
    - `$acm_certificate_arn` 可选；custom domain 必填，且证书 ARN region 必须是 `us-east-1`
    - `$cache_mode`，当前只支持 `public-read-cache`（GET/HEAD）
    - `$need_signed_url`，当前只记录需求，不生成 KeyGroup/PublicKey

[validate]
  - `$target.partition == aws`；否则 STOP
  - `$target.aws_region` 存在；S3 region 用它；CloudFront Distribution / OAC 是全局资源，`cloudfront.aws.m.upbound.io/v1beta1` CRD 不写 `forProvider.region`
  - bucket manifest 存在并启用 PublicAccessBlock 四项 true；没有就先跑 `add-s3-bucket.md`
  - custom domain 有 hostname 时，必须有 `acm_certificate_arn` 且 ARN region 是 `us-east-1`
  - signed URL / signed cookie 需求存在时，产 Ops Todo；本 workflow 只接普通 CDN read path

[output]
  - 内存变量

## Step 2. 确保 per-app ProviderConfig + CloudFront 管理权限

[precondition]
  - Step 1 完成

[action]
  - 在 `crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml` 检查：
    - `kind: Role` / `metadata.name: crossplane-app-{$app}`
    - `kind: ClusterProviderConfig` / `metadata.name: {$app}`
    - `RolePolicy crossplane-app-{$app}-cloudfront`
  - 缺 Role / ProviderConfig → 用 `recipes/crossplane/app-providerconfig.yaml.tmpl`
  - 缺 CloudFront RolePolicy → append `recipes/crossplane/cloudfront-rolepolicy.yaml.tmpl`

[validate]
  - CloudFront RolePolicy 只挂在 `crossplane-app-{$app}`，不要挂 default provider role
  - `providerConfigRef.name: default` 只出现在 IAM 资源；CloudFront / S3 数据资源使用 `providerConfigRef.name: {$app}`
  - YAML 可解析

[output]
  - `crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml`

## Step 3. 写 OAC 和 Distribution bootstrap

[precondition]
  - Step 2 完成

[action]
  - 用 `recipes/crossplane/cloudfront-oac.yaml.tmpl` 写：
    - `{{cdn_name}}=$cdn_name`
    - `{{app}}=$app`
  - 用 `recipes/crossplane/cloudfront-distribution.yaml.tmpl` 写：
    - `{{cdn_name}}=$cdn_name`
    - `{{distribution_external_name_annotation_block}}=` 空字符串（首次创建时不写空 external-name）
    - `{{cdn_comment}}="$app $target.env_keyword CDN (S3 private bucket via OAC)"`
    - `{{origin_domain_name}}=$origin_domain_name`
    - `{{app}}=$app`
    - custom domain：
      - `{{viewer_certificate_block}}` 填 ACM cert block
      - `{{aliases_block}}` 填 hostname aliases block
    - 无 custom domain / E2E：
      - `{{viewer_certificate_block}}` 填 `cloudfrontDefaultCertificate: true`
      - `{{aliases_block}}=` 空字符串
  - 写到应用仓库：
    - `k8s/overlays/{$target.env_keyword}/cloudfront-oac.yaml`
    - `k8s/overlays/{$target.env_keyword}/cloudfront-distribution.yaml`
  - 加入 overlay `kustomization.yaml`

[validate]
  - Distribution **必须**用 `originAccessControlIdRef.name: {$cdn_name}-oac`
  - 禁止 `originAccessControlId: ""`
  - Distribution / OAC 禁止写 `spec.forProvider.region`；当前 CloudFront m.upbound.io CRD 会 strict reject unknown field
  - Distribution 要显式写 CloudFront provider defaults：`httpVersion: http2`、
    `priceClass: PriceClass_All`、`waitForDeployment: true`、provider default tags
    `crossplane-kind/crossplane-name/crossplane-providerconfig`，并让
    `s3OriginConfig: {}`。不要写 legacy `originAccessIdentity: ""`，否则 Crossplane
    会规范化 live spec，造成 ArgoCD 长期 OutOfSync。
  - `providerConfigRef.name: {$app}` / `kind: ClusterProviderConfig`
  - `bash "$skill_root/validators/validate.sh" k8s/overlays/{$target.env_keyword}` 通过

[output]
  - `k8s/overlays/{$target.env_keyword}/cloudfront-oac.yaml`
  - `k8s/overlays/{$target.env_keyword}/cloudfront-distribution.yaml`

## Step 4. 写 bootstrap BucketPolicy

[precondition]
  - Step 3 完成

[action]
  - 用 `recipes/crossplane/cloudfront-bucket-policy-bootstrap.yaml.tmpl` 写到 bucket owner overlay：
    - `{{app}}=$app`
    - `{{bucket_name}}=$bucket_name`
    - `{{region}}=$target.aws_region`
    - `{{partition}}=$target.partition`
    - `{{account_id}}=$target.account_id`
  - 如果已有 bucket policy，要合并成单个 BucketPolicy；S3 一个 bucket 只有一个 policy，不要生成两个互相覆盖的 BucketPolicy。

[validate]
  - Principal 是 `cloudfront.amazonaws.com`，不是 `*`
  - Action 只有 `s3:GetObject`
  - PublicAccessBlock 不改
  - policy 用 `AWS:SourceAccount` 仅作为 bootstrap；cd-requirements.md 必须记录 Phase 2 hardening
  - `bash "$skill_root/validators/validate.sh" k8s/overlays/{$target.env_keyword}` 通过

[output]
  - `k8s/overlays/{$target.env_keyword}/s3-bucket-policy.yaml` 或 `s3-{$purpose}-bucket-policy.yaml`

## Step 5. 注入 CDN_BASE_URL

[precondition]
  - Step 4 完成

[action]
  - 如果有 custom domain：`CDN_BASE_URL=https://{$custom_domain}`
  - 如果无 custom domain：先不写业务配置；等 Distribution Ready 后用 `status.atProvider.domainName` 或 `*.cloudfront.net` 作为 E2E URL
  - ConfigMap 路径复用 `recipes/k8s/configmap.yaml.tmpl` 或现有 overlay config 文件

[validate]
  - custom domain 场景：config 里有 `CDN_BASE_URL`
  - 无 custom domain 场景：cd-requirements.md 明确这是 E2E / technical smoke，不是产品 URL

[output]
  - `k8s/overlays/{$target.env_keyword}/configmap.yaml` 或现有 config 文件

## Step 6. ArgoCD ignoreDifferences 检查

[precondition]
  - Step 5 完成

[action]
  - 检查对应 ArgoCD Application 是否包含 CloudFront bootstrap 期 ignoreDifferences：
    - group `cloudfront.aws.m.upbound.io`, kind `Distribution`
    - Phase 1 可临时忽略：
      - `/metadata/annotations/crossplane.io~1external-name`
      - `/metadata/annotations/crossplane.io~1external-create-pending`
      - `/metadata/annotations/crossplane.io~1external-create-succeeded`
      - `/metadata/annotations/crossplane.io~1external-create-failed`
    - Phase 2 回填 `crossplane.io/external-name` 后，必须移除 `/metadata/annotations/crossplane.io~1external-name` ignore，只保留 `external-create-*` bookkeeping（如仍有 diff）
  - 不要忽略整个 `/metadata/annotations` 或整个 `/spec/forProvider`；否则会掩盖 Phase 2 external-name / OAC / cache behavior 的真实 GitOps drift。
  - 如果这是修改既有 Application，先读取 live Application
    `spec.source.kustomize.images` 与 live workload image。若 Image Updater 已写入真实 SHA，
    相关 MR 必须把该 SHA作为 recovery seed 保留；不要从 template 把 `:0000000` sentinel 带回 Git。
  - 如 Application 不在当前仓库，产 Ops Todo 给 `argocd-apps/<cluster_dir>/...`

[validate]
  - 不允许用 ignoreDifferences 掩盖 `originAccessControlId: ""`；Step 3 validator 已阻断
  - 不允许忽略 `/metadata/annotations` 整个 map；Phase 2 必须能让 ArgoCD 看到并应用真实 `crossplane.io/external-name`
  - 不允许忽略 `/spec/forProvider` 整个 spec；CloudFront 行为变更必须能出现在 diff 里
  - 修改既有 Application 时，`spec.source.kustomize.images` 不能把 live SHA 降回 `0000000`
  - 如果需要 custom domain，Ops Todo 包含 DNS CNAME：hostname → CloudFront domain

[output]
  - ArgoCD Application MR 或 Ops Todo

## Step 7. Phase 1 在线验收

[precondition]
  - app MR + crossplane-infra MR + ArgoCD Application MR 已合并并同步

[action]
  - 运维只读检查：
    ```bash
    kubectl -n <ns> get originaccesscontrol,distribution | grep <cdn_name>
    kubectl -n <ns> get distribution <cdn_name> -o jsonpath='{.metadata.annotations.crossplane\.io/external-name}{" "}{.status.atProvider.domainName}{"\n"}'
    ```
  - 上传一个最小测试对象到 bucket（可用 app 自己已有上传链路；不要临时打开 bucket public）
  - curl CloudFront domain：
    ```bash
    curl -I https://<cloudfront-domain>/<test-key>
    ```

[validate]
  - OAC / Distribution `SYNCED=True READY=True`
  - CloudFront 访问测试对象返回 200 / 304
  - 直接 S3 public URL 不应该匿名可读

[output]
  - Distribution ID
  - CloudFront domain

## Step 8. Phase 2 hardening（必须做完才算完成）

[precondition]
  - Step 7 拿到 `$distribution_id`

[action]
  - 回填 `cloudfront-distribution.yaml`：
    - `metadata.annotations.crossplane.io/external-name: $distribution_id`
  - 用 `recipes/crossplane/cloudfront-bucket-policy-final.yaml.tmpl` 替换 bootstrap policy：
    - `{{distribution_id}}=$distribution_id`
  - 再次 push / sync

[validate]
  - `bash "$skill_root/validators/validate.sh" k8s/overlays/{$target.env_keyword}` 通过
  - bucket policy 中有 `AWS:SourceArn: arn:aws:cloudfront::{$account_id}:distribution/{$distribution_id}`
  - ArgoCD diff 不再因为 external-name 空值反复创建 / adopt
  - curl CloudFront domain 仍返回 200 / 304

[output]
  - hardening commit / MR
  - 最终 E2E 证据

## Step 9. summary + Ops Todo

[precondition]
  - Step 8 完成；或 custom domain / signed URL 等外部事项仍待 Ops

[action]
  - 更新 cd-requirements.md：
    - bucket
    - distribution id
    - CloudFront domain
    - custom domain / DNS 状态
    - policy 从 SourceAccount 收紧到 SourceArn 的证据
  - Ops Todo：
    - DNS CNAME（如果 custom domain）
    - ACM cert（如果未提供）
    - signed URL / KeyGroup / PublicKey（如果用户需要）
  - 输出 summary

[validate]
  - 不再有 bootstrap SourceAccount 作为最终状态，除非本次明确只是 Phase 1 且状态标记 BLOCKED
  - 所有 `{{...}}` 已填

[output]
  - summary card
  - Ops Todo 表

## 出口

完成后：
- S3 bucket private
- CloudFront OAC 已绑定
- BucketPolicy 最终 SourceArn 收紧
- 可用 CloudFront domain 或 custom domain 读取对象
- DNS / signed URL 这类外部动作有明确 Ops Todo

**常见踩坑**：
- `originAccessControlId: ""` → Crossplane 不会自动补，CloudFront 请求未签名，S3 403
- `crossplane.io/external-name: ""` 长期留在 Git → forced sync 可能让 Crossplane 忘记已建 distribution，触发 CNAMEAlreadyExists
- 只用 SourceAccount 结束 → 能跑但不是最终最小权限；必须 Phase 2 收紧 SourceArn
- 为 public download 关闭 PublicAccessBlock → 禁止；CloudFront OAC 是唯一支持路径
- custom domain 没有 us-east-1 ACM cert → CloudFront 不接受；先产 Ops Todo
