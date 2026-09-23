---
name: add-irsa-role
description: 给现有服务加 IRSA Role 让 Pod 直接调 AWS API（S3 / SQS / DynamoDB / KMS 等）。比 AK/SK 安全且 GitOps 化。
---

# Workflow：add-irsa-role

## 目的

让 Pod 通过 ServiceAccount + EKS OIDC 直接拿 AWS 凭证，无需在 Vault 写 AK/SK。产出：

- Crossplane IRSA Role（信任 EKS OIDC Provider，挂 CrossplaneAppBoundary）
- Crossplane RolePolicy（按 AWS 服务拆分的权限策略）
- K8s ServiceAccount（带 `eks.amazonaws.com/role-arn` annotation）
- 更新 Rollout 的 `serviceAccountName`

S3 特例：如果 Pod 访问的是**已有 bucket**，尤其是另一个 app / 另一个 AWS
账号拥有的 bucket，本 workflow 只负责 consumer 侧 IRSA；bucket 本体和
BucketPolicy 仍归 bucket owner 管理。

## 进入条件

- 应用已经有 `k8s/base/` + `k8s/overlays/{env_keyword}/`
- 用户已说明：需要哪些 AWS 服务 + 操作 + 资源 ARN 范围
- 每个 target 集群 EKS OIDC Provider 已配（GCP / TKE 集群没有 IRSA，**STOP** 产 ops-todo 改用 AK/SK 走 vault）

## Step 1. 解析需求

[precondition]
  - cd-requirements.md 列了 IRSA 需求 + AWS 服务列表

[action]
  - 提取：
    - `$app`              kebab-case
    - `$targets[]`        含需要 IRSA 的 target
    - `$aws_services[]`   每项含 {service, actions, resources}，例如：
        - {service: s3, actions: [GetObject, PutObject, ListBucket], resources: ["arn:aws:s3:::myapp-*", "arn:aws:s3:::myapp-*/*"]}
        - {service: sqs, actions: [SendMessage, ReceiveMessage], resources: ["arn:aws:sqs:us-east-1:123:myapp-*"]}
    - S3 若是已有 bucket，再提取 `$bucket_owner_app`、`$bucket_owner_account_id`、
      `$bucket_owner_partition`、`$read_prefix`、访问模式 read / write / delete

[validate]
  - 每个 service 都有 actions + resources（不能用 `Action: "*"` 或 `Resource: "*"`，除非 explicit list/describe API）
  - 所有 actions 不在 CrossplaneAppBoundary 拦截列表（kms:CreateGrant、secretsmanager:CreateSecret 等会被拦）
  - S3 cross-account：consumer role 必须在 consumer 所在账号；**不要** 让 Pod
    去 assume bucket owner 账号的 runtime role

[output]
  - `$targets[]`、`$aws_services[]`

## Step 2. 查 OIDC Provider 信息

[precondition]
  - Step 1 完成

[action]
  - 对每个 `$target`：
    - 查 `references/data/clusters.yaml -> clusters[<target.cluster>]`，记录 `account_id`、`region`、`cloud`、`partition`
    - **`cloud != aws`** → STOP 产 ops-todo "GCP/TKE 集群无 IRSA，改 AK/SK 走 Vault"（**不要** 给非 aws target 写 IRSA manifest；workflow 跳过该 target，对其他 aws target 继续）
    - `cloud == aws` 但 `partition` 字段不存在 → 数据错乱，STOP 让用户先修 clusters.yaml
    - 若目标 S3 bucket 在不同 partition（如 `aws` ↔ `aws-cn`）→ STOP：
      IRSA / OIDC / STS trust 不能跨 partition；产 Ops Todo 走 Vault AK/SK 或云厂商
      原生凭据方案。**不要** 生成跨 partition IRSA / BucketPolicy 假象。
    - 查 OIDC Provider ARN：
        `aws eks describe-cluster --name <cluster> --query 'cluster.identity.oidc.issuer'`
        ARN 格式 `arn:{$target.partition}:iam::{$target.account_id}:oidc-provider/<issuer>`
        URL = issuer 去掉 `https://` 前缀
    - 记录 `$target.oidc_provider_arn`、`$target.oidc_provider_url`

[validate]
  - 每个 target 三个字段都填了

[output]
  - `$targets[]` 完整

## Step 3. 写 IRSA Role + Policy（每 target 集群一次）

[precondition]
  - Step 2 完成

[action]
  - 用 `recipes/crossplane/irsa-role.yaml.tmpl` 写 Role，填槽：
      `{{app}}=$app`、`{{namespace}}=$target.namespace`、`{{sa_name}}=$app`、
      `{{oidc_provider_arn}}=$target.oidc_provider_arn`、`{{oidc_provider_url}}=$target.oidc_provider_url`、
      `{{partition}}=$target.partition`、`{{account_id}}=$target.account_id`
  - 对每个 `$aws_service`，用 `recipes/crossplane/irsa-rolepolicy.yaml.tmpl` 写一个 RolePolicy：
      `{{app}}=$app`、`{{service}}=<service-name>`、`{{policy_json_indented_6}}=<完整 JSON 块，每行加 6 空格缩进——详 recipe 头部"缩进规则"段>`
  - S3 cross-account read：
    - consumer 侧 RolePolicy 只写 `s3:GetObject` + `s3:ListBucket`，Resource 只含
      目标 bucket ARN + 允许 prefix 的 object ARN
    - owner 侧还必须在 bucket owner 应用仓库 overlay 写 BucketPolicy，允许这个
      consumer IRSA role ARN；用 `recipes/crossplane/s3-bucket-policy-read.yaml.tmpl`
    - owner 侧 BucketPolicy 跟 Bucket / PublicAccessBlock / SSE / Versioning / Lifecycle 同目录；
      IAM Role / RolePolicy 仍在各自账号的 crossplane-infra
  - 写到 `crossplane-infra/{$target.cluster_dir}/{$app}-irsa.yaml`（多个 doc 用 `---` 分隔）

[validate]
  - 文件存在
  - YAML 可解析（每个 doc 都能解析）

[output]
  - crossplane-infra/{$target.cluster_dir}/{$app}-irsa.yaml

## Step 4. 写 K8s ServiceAccount（每 target overlay 一次，**包括非 IRSA target**）

[precondition]
  - Step 3 完成

[action]
  - **每个 `$target` overlay 都创同名 SA**（即使非 aws target / 没在 Step 2-3 写 IRSA Role）。
    这是为了让 Step 5 改的 base.serviceAccountName 在所有 target 都能落到一个真实存在的 SA。
  - 对 aws target（partition 存在）：用 `recipes/k8s/serviceaccount-irsa.yaml.tmpl` 完整模板，填槽：
      `{{app}}=$app`、`{{namespace}}=$target.namespace`、
      `{{partition}}=$target.partition`、`{{account_id}}=$target.account_id`
  - 对非 aws target（cloud=gcp/tencent）：用 `recipes/k8s/serviceaccount.yaml.tmpl` 写
    **plain ServiceAccount**，填 `{{app}}=$app`、`{{namespace}}=$target.namespace`；不要带
    `eks.amazonaws.com/role-arn` annotation。
  - 写到 `k8s/overlays/{$target.env_keyword}/serviceaccount.yaml`
  - 加到 `kustomization.yaml` resources 列表

[validate]
  - 每 target overlay 的 serviceaccount.yaml 都存在
  - `kustomize build k8s/overlays/{$target.env_keyword}/` 成功
  - aws target 渲染出的 SA 有 `eks.amazonaws.com/role-arn`；非 aws target SA 不带 annotation

[output]
  - k8s/overlays/{$target.env_keyword}/serviceaccount.yaml（per target）

## Step 5. 修 Rollout 用新 SA

[precondition]
  - Step 4 完成

[action]
  - 看 `k8s/base/rollout.yaml` 的 `spec.template.spec.serviceAccountName`：
    - 已是 `$app` → 跳过
    - 不存在或是 `default` → 加：
        ```yaml
        spec:
          template:
            spec:
              serviceAccountName: {{app}}
        ```
  - 验证 `kustomize build` 渲染出的 Rollout pod spec 含 `serviceAccountName`

[validate]
  - `kustomize build k8s/overlays/<任一 target>/ | grep serviceAccountName` 显示 `$app`

[output]
  - k8s/base/rollout.yaml 更新（一次编辑，不按 target）

## Step 6. 全量 validator

[precondition]
  - Step 5 完成

[action]
  - `bash "$skill_root/validators/validate.sh" k8s/`
  - 每 target 集群跑 `bash "$skill_root/validators/validate.sh" crossplane-infra/{$target.cluster_dir}/`

[validate]
  - 全部退出 0

[output]
  - validator 日志

## Step 7. 更新文档 + summary

[precondition]
  - Step 6 通过

[action]
  - `docs/deployment/cd-requirements.md` 加 "IRSA" 段，列每个 service 的 actions + resources
  - `docs/deployment/cicd.md` append IRSA 接入方式
  - 用 `recipes/docs/ops-todo-table.md` 出 Ops Todo（如果 CrossplaneAppBoundary 拦了某个 action，需要运维评估扩 boundary）
  - S3 cross-account 时，在 cd-requirements.md 明确记录：
    - bucket owner app / owner account / bucket / prefix
    - consumer app / consumer account / IRSA role ARN
    - owner-side BucketPolicy MR 与 consumer-side IRSA MR 的合并顺序
  - 用 `recipes/docs/summary-card.md`

[validate]
  - summary 已打印

[output]
  - 更新后的文档 + 最终用户消息

## 出口

ArgoCD sync 后：
- Crossplane 在 AWS 建 IRSA Role + 挂 policy
- K8s ServiceAccount 创建（带 role-arn annotation）
- Rollout 用新 SA → Pod 调 AWS API 时自动用 OIDC token 换临时凭证

**常见踩坑**：
- Pod 报 `AccessDenied` → 看 RolePolicy 的 Action / Resource 是否覆盖实际调用；CloudTrail 反查具体 deny 的 action
- Pod 启动报 `unable to assume role` → SA 的 role-arn annotation 不对，或 OIDC condition 里 `system:serviceaccount:<ns>:<sa>` 跟实际 ns/sa 不一致
- S3 cross-account 读写不要让 consumer Pod assume owner 账号 runtime role；正确模式是
  consumer 自己账号 IRSA RolePolicy + owner bucket 侧 BucketPolicy，两边都收口到
  具体 role ARN、bucket、prefix。
- 跨 partition（`aws` ↔ `aws-cn`）不是普通跨账号；IRSA / STS web identity 不互通，
  STOP 后走 Vault AK/SK 或云厂商原生凭据方案，不要编 cross-partition trust。
