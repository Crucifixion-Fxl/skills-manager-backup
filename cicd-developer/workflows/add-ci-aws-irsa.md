---
name: add-ci-aws-irsa
description: 让 GitLab CI job 通过 Job Pod IRSA 临时拿 AWS 凭证调 AWS API。用于 CI 上传 S3 artifact、触发 Lambda 部署、同步 DynamoDB/SQS/SNS 配置；禁止写 AK/SK。
---

# Workflow：add-ci-aws-irsa

## 目的

给 **CI job** 接 AWS API 权限，不给应用 runtime Pod 扩权。末态：

- crossplane-infra 里有 CI 专用 IAM Role + RolePolicy
- 应用 overlay 里有 CI 专用 ServiceAccount + RoleBinding
- `.gitlab-ci.yml` 的目标 job 用 `KUBERNETES_NAMESPACE_OVERWRITE` +
  `KUBERNETES_SERVICE_ACCOUNT_OVERWRITE` 切到 runner 允许的 CI job namespace + CI SA
- CI job 通过 STS `AssumeRoleWithWebIdentity` 拿 1h 临时凭证

如果是应用运行时访问 AWS，走 `add-irsa-role.md`；如果只是构建 Harbor 镜像，不需要本 workflow。

## 进入条件

- 用户说明 CI job 要做的 AWS 动作、资源 ARN、目标环境、job 名和 script
- 目标是 AWS EKS 集群；GCP / TKE 没有 AWS IRSA，不能套这个 workflow
- 目标集群的 GitLab runner v4 pod label 与 `gitlab-runner-job-executor` RBAC 已就绪；
  以目标集群当前 runner 配置为准，不能从 fleet 数量推断

## Step 1. 解析 CI AWS 需求

[precondition]
  - cd-requirements.md 或用户消息列了 CI -> AWS 需求

[action]
  - 提取：
    - `$app`                 kebab-case，匹配 repo 名
    - `$purpose`             kebab-case，如 `engine-publisher` / `lambda-deployer`
    - `$ci_project_name`     GitLab `CI_PROJECT_NAME`（仓库末段，不含 group）
    - `$targets[]`           需要这个 CI AWS job 的 target
    - `$job_name`            `.gitlab-ci.yml` job 名
    - `$stage`               job stage
    - `$script_lines[]`      CI script；只能是 AWS CLI / shell，不写 AK/SK
    - `$aws_services[]`      每项 {service, actions, resources}
    - `$protected_only`      prod-only SA 是否要求 protected branch（prod 默认 true）

[validate]
  - `$purpose` 匹配 `^[a-z][a-z0-9-]*$`
  - 每个 service 都有 actions + resources；禁止 `Action: "*"` 或 `Resource: "*"`，除非是 explicit List/Describe 类 API
  - `$script_lines` / `.gitlab-ci.yml` 不得含 `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`aws configure set aws_access_key_id`
  - 不允许复用 runtime SA；SA 名必须是 `$app-ci-$purpose`

[output]
  - `$app`、`$purpose`、`$sa_name`、`$targets[]`、`$aws_services[]`、job 输入

## Step 2. 解析集群 + OIDC + runner

[precondition]
  - Step 1 完成

[action]
  - 对每个 `$target`：
    - 查 `references/data/clusters.yaml -> clusters[<target.cluster>]`
    - 记录 `account_id`、`partition`、`aws_region`、`cloud`、`runner_tags[0]`
    - 记录 `$target.cluster_dir = clusters[].argocd_apps_dir`（crossplane-infra 使用同名集群目录）
    - `cloud != aws` → STOP 该 target，产 Ops Todo："GCP/TKE target cannot use AWS IRSA; use cloud-native equivalent or ops-reviewed Vault AK/SK fallback"
    - 查 EKS OIDC issuer：
      `aws eks describe-cluster --name <cluster> --query 'cluster.identity.oidc.issuer'`
    - 计算：
      - `$target.oidc_provider_url` = issuer 去掉 `https://`
      - `$target.oidc_provider_arn` = `arn:{$target.partition}:iam::{$target.account_id}:oidc-provider/{$target.oidc_provider_url}`
      - `$target.runner_service_account` = `gitlab-runner-amd64`，除非集群事实明确给出其他 SA
      - `$target.runtime_namespace` = 运行时 app namespace（来自 env facts / cd-requirements）
      - `$target.ci_namespace` = CI job pod namespace，必须使用环境前缀并与 runtime namespace 分离：`{$target.env}-{$app}-ci`（如 `staging-cicd-v2-e2e-ci`），同时必须被目标 GitLab runner 的 `KUBERNETES_NAMESPACE_OVERWRITE` allow-list 接受
      - `$target.protected_branch_annotation`：
        `$protected_only == true` 或 `$target.env == prod` → `    ci.gitlab.com/require-protected-branch: "true"`；否则空

[validate]
  - 每个 AWS target 都有 OIDC ARN / URL / runner tag / runner SA / ci namespace
  - `$target.ci_namespace` 必须满足目标 runner 的 namespace overwrite 规则；若 runner 仍只接受历史 `.+-(staging|pre|prod|dev)$` 后缀式 allow-list，STOP 该 target 并产 Ops Todo，让平台增加前缀式规则后再继续；不得把新 CI namespace 倒回 `{$app}-{$target.env}`
  - 非 AWS target 没有写任何 IRSA manifest，只有 Ops Todo

[output]
  - `$targets[]` 完整

## Step 3. 写 crossplane-infra CI IRSA Role + RolePolicy

[precondition]
  - Step 2 完成

[action]
  - 对每个 AWS target：
    - 用 `recipes/crossplane/ci-irsa-role.yaml.tmpl` 写 Role，填槽：
      `{{app}}=$app`、`{{purpose}}=$purpose`、`{{namespace}}=$target.ci_namespace`、
      `{{sa_name}}=$sa_name`、`{{oidc_provider_arn}}=$target.oidc_provider_arn`、
      `{{oidc_provider_url}}=$target.oidc_provider_url`、`{{partition}}=$target.partition`、
      `{{account_id}}=$target.account_id`
    - 对每个 `$aws_service`，用 `recipes/crossplane/ci-irsa-rolepolicy.yaml.tmpl` 写 RolePolicy，填槽：
      `{{app}}=$app`、`{{purpose}}=$purpose`、`{{service}}=$service`、
      `{{policy_json_indented_6}}=<完整 IAM policy JSON，每行缩进 6 空格>`
    - 写到 `crossplane-infra/{$target.cluster_dir}/{$app}-ci-{$purpose}-iam.yaml`

[validate]
  - YAML 多 doc 可解析
  - Role 名 / RolePolicy roleRef 都是 `crossplane-app-{$app}-ci-{$purpose}-irsa`
  - providerConfigRef.kind = `ClusterProviderConfig`

[output]
  - crossplane-infra/{$target.cluster_dir}/{$app}-ci-{$purpose}-iam.yaml

## Step 4. 写应用 overlay CI ServiceAccount + RoleBinding

[precondition]
  - Step 3 完成

[action]
  - 对每个 AWS target，用 `recipes/k8s/serviceaccount-ci-irsa.yaml.tmpl` 写：
      `{{sa_name}}=$sa_name`、`{{namespace}}=$target.ci_namespace`、
      `{{partition}}=$target.partition`、`{{account_id}}=$target.account_id`、
      `{{app}}=$app`、`{{purpose}}=$purpose`、
      `{{ci_project_name}}=$ci_project_name`、
      `{{protected_branch_annotation}}=$target.protected_branch_annotation`、
      `{{runner_service_account}}=$target.runner_service_account`
  - 写到 `k8s/overlays/{$target.env_keyword}/ci-irsa-{$purpose}.yaml`
  - 加到该 overlay `kustomization.yaml` resources
  - 如果 overlay 使用顶层 `namespace:`，必须确认它不会把 CI SA / RoleBinding 覆盖回 runtime namespace；必要时改为显式 namespace 或把 CI 资源放到单独 overlay

[validate]
  - `kustomize build k8s/overlays/{$target.env_keyword}/` 成功
  - 渲染后的 CI ServiceAccount 和 RoleBinding namespace 都等于 `$target.ci_namespace`
  - SA 有 `eks.amazonaws.com/role-arn` 和 `ci.gitlab.com/allowed-project`
  - prod / protected-only target 有 `ci.gitlab.com/require-protected-branch: "true"`
  - RoleBinding subject 指向 runner SA，roleRef 是 `gitlab-runner-job-executor`

[output]
  - k8s/overlays/<env_keyword>/ci-irsa-<purpose>.yaml

## Step 5. 更新 `.gitlab-ci.yml` job

[precondition]
  - Step 4 完成

[action]
  - 如果 `$job_name` 已存在：
    - 给该 job 加/改：
      `tags: [$target.runner_tags[0]]`
      `KUBERNETES_NAMESPACE_OVERWRITE=$target.ci_namespace`
      `KUBERNETES_SERVICE_ACCOUNT_OVERWRITE=$sa_name`
      `AWS_DEFAULT_REGION=$target.aws_region`
    - image 必须是 `${HARBOR_REGISTRY}/base/aws-cli:<version>` 或团队已验证的 Harbor base image
  - 如果 `$job_name` 不存在：
    - 用 `recipes/ci/gitlab-ci-aws-job.yml.tmpl` append job，填槽：
      `{{job_name}}=$job_name`、`{{stage}}=$stage`、
      `{{runner_tag_amd64}}=$target.runner_tags[0]`、
      `{{aws_cli_version}}=2.27.50`、`{{namespace}}=$target.ci_namespace`、
      `{{sa_name}}=$sa_name`、`{{aws_region}}=$target.aws_region`、
      `{{script_lines_indented_4}}=$script_lines 每行缩进 4 空格`、
      `{{rules_lines_indented_4}}=<按 target.branch / protected_only 生成 rules>`
  - 如果 `${HARBOR_REGISTRY}/base/aws-cli:2.27.50` 未在 `DEV/base-images` 注册，代写 base-images MR；
    无权限则 Ops Todo 阻塞。

[validate]
  - `.gitlab-ci.yml` YAML 可解析
  - job 不含 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
  - job 含 `KUBERNETES_NAMESPACE_OVERWRITE` 和 `KUBERNETES_SERVICE_ACCOUNT_OVERWRITE`
  - job image 不得是 `public.ecr.aws/...`、`amazon/aws-cli`、`docker.io/...`

[output]
  - .gitlab-ci.yml 更新

## Step 6. 全量 validator + 文档

[precondition]
  - Step 5 完成

[action]
  - 跑：
    - `bash "$skill_root/validators/validate.sh" k8s/`
    - `bash "$skill_root/validators/validate.sh" crossplane-infra/<cluster_dir>/`
    - `.gitlab-ci.yml` YAML parse
  - 更新 `docs/deployment/cd-requirements.md`：
    - CI AWS purpose
    - actions/resources
    - protected branch requirement
  - 更新 `docs/deployment/cicd.md`：
    - CI job 如何通过 Job Pod IRSA 取临时凭证
    - 禁止 AK/SK fallback，除非 Ops Todo 明确审批

[validate]
  - validator 全 0
  - 文档无 `{{...}}` 残留

[output]
  - validator 日志 + 更新文档

## 出口

GitLab CI job 触发后：
- Runner 创建 job pod 到 `$target.ci_namespace`
- Kyverno 校验 job pod label 与 SA `ci.gitlab.com/allowed-project` 匹配
- prod-only SA 只允许 protected branch
- Pod 用 CI SA 的 projected token 走 STS
- AWS CLI 用临时凭证执行 `$script_lines`

**常见踩坑**：
- `admission webhook denied: allowed-project missing` → SA 少 annotation
- `requires CI from a protected branch` → feature 分支用了 prod-only SA
- `secrets is forbidden` → RoleBinding 缺，runner SA 不能在应用 ns 创建 job pod配套资源
- `AccessDenied AssumeRoleWithWebIdentity` → IAM trust 的 `sub` namespace/SA 不匹配
- `AccessDenied PutObject/UpdateFunctionCode` → RolePolicy action/resource 不足
- `provided value "<namespace>" does not match ".+-(staging|pre|prod|dev)$"` → runner 仍只接受历史后缀式 namespace；STOP 并让平台增加前缀式 allow-list，再同步 SA、RoleBinding、IAM trust、`.gitlab-ci.yml`，不要把新 namespace 改回后缀式
