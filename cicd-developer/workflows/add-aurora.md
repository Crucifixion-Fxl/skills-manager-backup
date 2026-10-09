---
name: add-aurora
description: 给现有服务加 AWS Aurora Cluster + ClusterInstance（含 writer/reader endpoint）。比 RDS Instance 更适合读写分离场景。
---

# Workflow：add-aurora

## CN 迁移能力门禁

当前 CN 路由见 `references/cn-tencent-migration.md`：prod 为 `cn-k8s`（100014919455），
staging/tech-service 为 `cn-tke-staging` / `cn-tke-tech-service`（100052802231）。
AWS CN 已退役。下文 AWS shared-middleware 的已上线证明不能覆盖新 TKE；生成 `Database` /
`KafkaScramCredential` 前必须核验精确目标的 served API、Composition/ProviderConfig、共享实例、
Vault writer/reader 和 per-app 凭据交付，缺任一证据则 STOP + Ops Todo。消费策略仍适用，
不得回退到旧 AWS broker/ARN、共享 root 或 app-owned/self-hosted staging 数据库。
AWS 托管资源 recipe 仅用于 `cloud=aws`；腾讯云请求转对应原生能力或 Ops Todo。

## Application 与渲染入口前置

raw 数据面分支先执行 [资源职责拆分合同](../references/application-resource-split.md)：
登记 `$target.infra_source_path` 和 `$target.runtime_source_path`，分别绑定共享
`app-data-plane` / `app-runtime`。infra 的 kustomization 不得被 runtime root 引用；
每次生成后独立构建两份 render，核对唯一资源管理者。缺少准确合同先交付 Ops Todo，
不能把混合 Application 整体迁入数据面或新建 owner Project。已批准的高层 claim 分支保持原合同。
以下原有 app overlay 路径仅用于 runtime 消费文件，raw 与生产链使用明确的 infra 路径；
跨 Application 的依赖以实际生产/消费 Ready 证据验收，不靠资源 sync-wave 推断。

## 目的

通过 Crossplane GitOps 给现有服务开 Aurora Cluster + ≥1 个 ClusterInstance。

**DB 默认走 Aurora**（无论 MySQL 还是 PostgreSQL 需求）：
- 默认引擎版本：aurora-mysql `8.0.mysql_aurora.3.10.3`、aurora-postgresql `16.14`
  （见 `cost-tiering/aurora.yaml -> engine_contracts`）。
- RDS 单实例 **MySQL Community**（`add-rds.md` 的 engine=mysql 分支）**默认不选**；确有理由用
  单实例 mysql 时才走 `add-rds.md`，并在 cd-requirements.md 记录理由 override。
- postgres 需求也默认走本 workflow（aurora-postgresql）。

跟 RDS Instance 的区别：
- 有独立 writer endpoint 和 reader endpoint（app 可分离读写）
- prod 强制 ≥ 2 ClusterInstance（跨 AZ HA + reader 分流）
- 数据存储跟实例分离（删 instance 不丢数据；prod 用 `managementPolicies` 去掉 `Delete` 进一步兜底）

注：单实例 RDS（`add-rds.md`）成本更低，但**已非默认**——仅在 cd-requirements.md 记录理由
override（如 legacy 迁移、明确的低成本单实例场景）时才用；MySQL Community 默认不选。

## 进入条件

- 应用已有核验的 runtime 入口；raw target 记录准确 `$target.runtime_source_path` 与
  `$target.infra_source_path`。标准或获批自定义 Kustomize 路径使用其现有配置，不要求
  固定 `k8s/base/`；其它渲染器需先明确该入口的生成/渲染适配方案，不把模板改称通用生成器。
- cd-requirements.md 列了 Aurora 需求，包括需要 reader endpoint
- 每个 target 的 `crossplane-infra/{cluster_dir}/` 路径可由当前变更写入。Step 4 会创建或补全
  该 app 的 Crossplane Role、`ClusterProviderConfig` 与 Aurora `RolePolicy`；**不要**要求它们
  事先存在，也不要把 Crossplane provider identity 误当成 workload IRSA。
- 若包含 **staging / tech-service** target（当前 us/eu/cn staging + tech-service；CN 腾讯云能力须独立验证）：**STOP**。
  这些集群只能消费 shared-middleware（#29），但 Aurora 的 kind:Database 消费 Composition 尚未部署
  （Aurora 连共享实例都还没有）。产 Ops Todo（列 target 列表 + Aurora 需求，含 reader endpoint），
  由平台评估共享实例可用性或路由到 infra-modernization backlog；**不要为这些 target 创建 app-owned
  Aurora，也不要用 StatefulSet 自托管（#30）**。**非 shared-middleware 集群 target（真 prod `*-eks-prod`，非 tech-service）**仍可继续本 workflow。

## Step 1. 解析需求

[precondition]
  - cd-requirements.md 有 Aurora 条目

[action]
  - 提取：
    - `$app`             kebab-case
    - `$targets[]`       含 Aurora 的 target
    - `$engine`          aurora-postgresql | aurora-mysql
    - `$engine_version`  具体 minor。默认取 `cost-tiering/aurora.yaml -> engine_contracts`：
                         aurora-mysql → `8.0.mysql_aurora.3.10.3`（Aurora MySQL v3.10.3）、
                         aurora-postgresql → `16.14`。cd-requirements.md 未显式写版本时用默认值；
                         用其它 minor 必须在 cd-requirements.md 记录理由并逐 region 验证可用。
    - `$database_name`   Aurora 用 databaseName（不是 RDS Instance 的 dbName）
    - `$username`        DB 用户名（snake_case，无 hyphen）

[validate]
  - `$engine ∈ {aurora-postgresql, aurora-mysql}`
  - `$engine_version` 是具体版本
  - `$username ~= ^[a-z][a-z0-9_]*$`
  - 把 `$targets[]` 拆成：
    - `$staging_targets[]`：`$target.cluster` 是 shared-middleware 集群（6 个：`{us,eu}-eks-staging`
      + `{us,eu}-eks-tech-service + cn-tke-staging + cn-tke-tech-service`；**按 cluster 名判，不用 `$target.env`**——tech-service env=prod，见 #29）。
      **Readiness gate STOP**：Aurora 连共享实例都还没有、kind:Database 消费 Composition 未落地。产 Ops Todo，
      列出这些 targets、Aurora writer/reader 需求、env label（是否 isolation=required）。
      不要为这些 target 创建 app-owned Aurora，也不要 StatefulSet 自托管（#30）。
    - `$unsupported_targets[]`：`$target.cloud != aws`。本 workflow 只生成 AWS Aurora
      Crossplane 资源；对这些 target 产 Ops Todo，不能把 AWS ARN/ProviderConfig 套到 TKE 或 GCP。
    - `$managed_targets[]`：**cloud=aws** 且非 shared-middleware 集群 target（真 prod
      `*-eks-prod`/`*-prod-data`；tech-service 不在此列），继续 Step 2 之后的 app-owned Aurora 流程。
  - 如果 `$managed_targets[]`（prod/无 shared-middleware）为空：end workflow。

[output]
  - 内存变量；后续 Step 2 起只处理 `$managed_targets[]`

## Step 2. 解析 tier + 集群信息

[precondition]
  - Step 1 完成

[action]
  - 对每个 `$managed_targets[]` 中的 `$target`：
    - 查 `cost-tiering/aurora.yaml -> $target.env`，记录所有字段。
      `clusterInstanceClass` 默认 t4g 家族（prod 起步 `db.r8g.large`）；**高资源占用实例改用
      r8g/m8g 家族，由用户在 cd-requirements.md 显式指定**（workflow 不自动升档，只校验取值与本表
      一致或有 override 理由）
    - prod env → 跑 `_global.yaml -> prod_self_check`，任何 "no" STOP
    - 查 `clusters.yaml` 取 `aws_region`（记为 `$target.region`）、`account_id`、
      `partition`、`namespace`、`vault_css`、`cluster_dir`
    - 查同集群其它 app 应用仓 overlay 的 RDS Instance YAML（从管理 Application 的准确 infra source path 查找）或 `crossplane-infra/<cluster_dir>/` 存量 legacy RDS YAML 的 `vpcSecurityGroupIds`（Aurora 跟 RDS 复用 SG），复制到 `$target.rds_security_group`

[validate]
  - 每个 `$managed_targets[]` target 的所有字段就位
  - prod target 的 `clusterInstanceReplicas >= 2`

[output]
  - `$targets[]` 完整

## Step 3. 计算 Vault 路径

[precondition]
  - Step 2 完成

[action]
  - 调 vault-path resolver：
    - `(env, kind=aurora, app, key=master-password)` → `$target.vault_master_password_path`
    - `(env, kind=aurora, app, key=cluster)` → `$target.vault_connection_path`
  - 同时记录两个 ESO mount-relative key：
    - `$target.vault_master_password_remote_key`：去掉 `$target.vault_master_password_path` 开头的 `secret/`
    - `$target.vault_connection_remote_key`：去掉 `$target.vault_connection_path` 开头的 `secret/`
    PushSecret writer 的 `remoteRef.remoteKey` 与 ExternalSecret reader 的 `remoteRef.key` 都必须使用
    相对 key，因为 ClusterSecretStore 已挂 `path=secret`

[validate]
  - 两个路径都匹配 Rule 2 platform=aurora

[output]
  - 每 target 两个完整 Vault 路径及两个 ESO mount-relative key

## Step 4. 写 Aurora RolePolicy（每集群一次）

[precondition]
  - Step 3 完成

[action]
  - 先确保 `crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml` 里已有
    `crossplane-app-{$app}` Role + `ClusterProviderConfig {$app}`；没有就先用
    `recipes/crossplane/app-providerconfig.yaml.tmpl` 创建/append。
  - 如果该文件已有 `metadata.name: crossplane-app-{$app}-rds` 的 RolePolicy，先验证其
    `spec.forProvider.roleRef.name: crossplane-app-{$app}`，然后跳过；RDS Instance 和 Aurora Cluster
    复用同一 IAM RolePolicy。
  - 否则打开 `recipes/crossplane/rds-rolepolicy.yaml.tmpl`，填
    `{{app}}=$app`、`{{partition}}=$target.partition`、
    `{{region}}=$target.region`、`{{account_id}}=$target.account_id`，append 到同一
    `{$app}-iam.yaml`。该模板已经包含 Aurora 所需的 app-scoped `cluster:` 和 `db:`
    ARN，以及 Resource:`*` 的 Describe 权限；不要手写第二个简化 policy。
  - 每个 target 集群最多写一次 RolePolicy；若文件已含 Role 和 ClusterProviderConfig，只追加
    缺失的 RolePolicy 文档。

[validate]
  - 同文件或同目录已有 `ClusterProviderConfig {$app}`，否则 Aurora CR 的 `providerConfigRef.name: {$app}` 会失败
  - 必须恰好有一个 `crossplane-app-{$app}-rds` RolePolicy，且其
    `spec.forProvider.roleRef.name` 是 `crossplane-app-{$app}`
  - YAML 可解析

[output]
  - crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml（创建或 append）

## Step 5. 写 Password Generator 链（每 target 循环）

[precondition]
  - Step 4 完成

[action]
  - 打开 Aurora 专用的三个模板：
      `recipes/crossplane/aurora-password-generator.yaml.tmpl`、
      `recipes/crossplane/aurora-password-external-secret.yaml.tmpl`、
      `recipes/crossplane/aurora-password-push-secret.yaml.tmpl`。
  - 填槽：generator / external-secret 两段只有 `{{app}}=$app`、`{{namespace}}=$target.namespace`；
      第三段 push-secret 额外填 `{{env}}=$target.env`、`{{cluster_secret_store}}=$target.vault_css`。
      模板已固定 `-aurora-password` 名称、selector、generatorRef、target Secret 与 Vault 路径；
      **不要**从 RDS 模板手工改名。
  - 写到 `{$target.infra_source_path}/aurora-password-{generator,external-secret,push-secret}.yaml`，
    三个文件只加入 `{$target.infra_source_path}/kustomization.yaml` 的 resources
  - PushSecret 的 vault `remoteKey` 改用 aurora 路径：
      `{$target.env}/aurora/application/{$app}/master-password`
    （相对 ClusterSecretStore `path=secret`；完整 Vault 路径仍是
    `secret/{$target.env}/aurora/application/{$app}/master-password`）
  - 保留模板内 ArgoCD sync-wave：Password `-5` → generator ExternalSecret `-4` → password PushSecret `-3`

[validate]
  - 三个文件存在；`kustomize build "{$target.infra_source_path}"` 成功，render 含完整密码生产链
  - `python3 "$skill_root/validators/check_vault_paths.py" "{$target.infra_source_path}"` PASS
  - `python3 "$skill_root/validators/check_db_resource_contracts.py" "{$target.infra_source_path}"` PASS；
    生成密码的 ExternalSecret 必须是 `refreshPolicy: CreatedOnce`、`creationPolicy: Orphan`、
    `target.immutable: true`、`deletionPolicy: Retain`。既有 Secret 不得直接晋级，需按
    `add-rds` Step 10 的存量密码迁移门禁核验 ownerReferences、immutable、Vault 与数据库认证。

[output]
  - {$target.infra_source_path}/aurora-password-{generator,external-secret,push-secret}.yaml 与 infra kustomization

## Step 6. 写 Crossplane Cluster + ClusterInstance（每 target 循环）

[precondition]
  - Step 5 完成

[action]
  - 用 `recipes/crossplane/aurora-cluster.yaml.tmpl` 写 Cluster，填槽：
      `{{app}}=$app`、`{{env_keyword}}=$target.env_keyword`、
      `{{namespace}}=$target.namespace`、`{{engine}}=$engine`、
      `{{engine_version}}=$engine_version`、`{{database_name}}=$database_name`、
      `{{username}}=$username`、`{{backup_retention}}=$target.backupRetentionPeriod_days`、
      `{{storage_encrypted}}=$target.storageEncrypted`、
      `{{deletion_protection}}=$target.deletionProtection`、
      `{{skip_final_snapshot}}=$target.skipFinalSnapshot`、
      `{{region}}=$target.region`、`{{rds_security_group}}=$target.rds_security_group`、
      `{{management_policies}}=$target.managementPolicies`、
      `{{final_snapshot_identifier_line}}=<prod 时为 4 空格 + finalSnapshotIdentifier: {$app}-{$target.env_keyword}-final-<YYYY-MM-DD>；非 prod 留空>`
  - 用 `recipes/crossplane/aurora-clusterinstance.yaml.tmpl` 写 `$target.clusterInstanceReplicas` 个 ClusterInstance（按 index 1, 2, 3 ...），填槽：
      `{{app}}=$app`、`{{env_keyword}}=$target.env_keyword`、
      `{{namespace}}=$target.namespace`、`{{engine}}=$engine`、`{{index}}=<1-based index>`、
      `{{instance_class}}=$target.clusterInstanceClass`、`{{region}}=$target.region`、
      `{{management_policies}}=$target.managementPolicies`
  - 全部写到应用仓库 `{$target.infra_source_path}/aurora.yaml`（多 doc 用 `---` 分），
    并加入独立 infra `kustomization.yaml` 的 `resources`。**app-owned Aurora CR 放应用仓 overlay，
    不放 crossplane-infra**（同 add-rds.md Step 5：crossplane-infra 只收 IAM / IRSA /
    ProviderConfig / WAF/IPSet / 共享 SG 入站规则等权限边界资源；namespaced CR 与密码链同仓同 namespace）
  - prod target 的 `$target.managementPolicies` 必须是 `["Observe","Create","Update","LateInitialize"]`，
    并通过上述 slots 写进 Cluster + 所有 ClusterInstance；prod 的 `finalSnapshotIdentifier` 也必须通过
    `{{final_snapshot_identifier_line}}` 写入。**不要**在渲染后的模板上手工改字段。
  - 顺序由 sync-wave 保证（密码链 `-5/-4/-3` → Aurora wave 0）；首次 sync 仍须确认
    `<app>-aurora-password` Secret 已在目标 namespace 生成且包含 `data.password`。
    不要绕过 wave 顺序单点同步 Aurora Cluster。

[validate]
  - YAML 多 doc 都可解析；`kustomize build "{$target.infra_source_path}"` 成功

[output]
  - {$target.infra_source_path}/aurora.yaml

## Step 7. 写连接 PushSecret + app ExternalSecret

[precondition]
  - Step 6 完成

[action]
  - PushSecret 推 Aurora 连接信息（含 reader endpoint）到 Vault：
    - endpoint (writer endpoint) → DB_HOST
    - reader_endpoint (reader endpoint) → DB_READER_HOST
    - port → DB_PORT
    - master_username → DB_USER
    - attribute.master_password → DB_PASSWORD
  - 用 `recipes/crossplane/aurora-conn-push-secret.yaml.tmpl` 写 PushSecret，填槽：
      `{{app}}=$app`、`{{env}}=$target.env`、
      `{{namespace}}=$target.namespace`、`{{cluster_secret_store}}=$target.vault_css`
    写到 `{$target.infra_source_path}/aurora-conn-push.yaml`。
    渲染后的所有 `remoteKey` 必须等于 `$target.vault_connection_remote_key`，不得带字面 `secret/`。
  - 用 `recipes/k8s/aurora-db-external-secret.yaml.tmpl` 写 app ExternalSecret，填槽：
      `{{app}}=$app`、`{{namespace}}=$target.namespace`、
      `{{cluster_secret_store}}=$target.vault_css`、
      `{{vault_remote_key}}=$target.vault_connection_remote_key`。
    保留模板内 `argocd.argoproj.io/sync-wave: "1"`。
    写到 `{$target.runtime_source_path}/db-external-secret.yaml`，只登记到
    `{$target.runtime_source_path}/kustomization.yaml` 的 resources。
    前述连接 PushSecret 只登记到 `{$target.infra_source_path}/kustomization.yaml` 的 resources；
    两个入口分别构建，并按跨 App Ready 合同验收。

[validate]
  - YAML 可解析；分别 `kustomize build "{$target.infra_source_path}"` 和
    `kustomize build "{$target.runtime_source_path}"` 成功；连接 PushSecret 只在 infra，
    app ExternalSecret 只在 runtime，后者映射 DB_HOST/DB_READER_HOST/DB_PORT/DB_USER/DB_PASSWORD。
  - 对两份最终 render 分别执行 `check_eso_pushsecret_bug.py` 和 `check_vault_paths.py`，均 PASS。

[output]
  - infra 的 aurora-conn-push.yaml、runtime 的 db-external-secret.yaml 与各自 kustomization

## Step 8. 接 Rollout envFrom

[precondition]
  - Step 7 完成

[action]
  - 每个 target 新建独立临时 `$shared_render_dir`，将
    `kustomize build "{$target.runtime_source_path}"` 输出写入其中的 `manifest.yaml`；
    patch 后重新构建同一入口，不复用前一 target 的 render。
  - 从 `{$target.runtime_source_path}` 的最终 render 定位准确 workload/main container，
    复用 add-rds Step 1「shared SQL 容器消费连接信息」的按 target 安全追加方式，消费
    `{$app}-db-secret`；必需 keys 除 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD 外还包含 DB_READER_HOST。
    patch 只登记到该 runtime kustomization，已有合法 base 引用仅验证；不向所有 target
    的共享 base 注入新引用，不覆盖已有连接或其它容器/env；同 App wave 顺序规则保持。

[validate]
  - 对该 runtime 最终 render 运行 `check_workload_secret.py`，传入实际 namespace、
    workload kind/name、主容器及 `{$app}-db-secret`，用五个 `--key` 检查上述必需 keys，退出 0。
    只证明消费引用；跨 App producer/consumer Ready 仍按独立合同验收。

[output]
  - {$target.runtime_source_path} 中准确 workload 消费 patch/已有引用与 render 检查结果

## Step 9. 全量 validator + 文档 + summary

[precondition]
  - Step 8 完成

[action]
  - 执行 [两份 render 的执行与验收](../references/application-resource-split.md#两份-render-的执行与验收)，
    对最终 runtime/infra 分别运行 validator，核对密码链、Cluster/ClusterInstance、连接 PushSecret
    只在 infra，业务 consumer/消费引用只在 runtime，且预期资源不遗漏。
    每个改动的 crossplane-infra 集群目录也跑原 validator；仓库级扫描仅补充。
  - cd-requirements.md 加 5 个 Vault key 行（writer + reader endpoint）
  - cicd.md append "Aurora 读写分离接入说明"
  - Ops Todo + summary

[validate]
  - 0 错；文档无 `{{...}}` 残留

[output]
  - 最终用户消息

## 出口

ArgoCD sync 后：
- 先回读 `<app>-aurora-password` Secret：存在密码 key，`ownerReferences` 为空，
  `immutable: true`；再核对 Vault 交付和数据库认证，不打印密码。
  存量链路不满足时 STOP，按 `add-rds` Step 10 的受控迁移处理，不依赖模板变更自动清除旧状态。
- Crossplane 建 Aurora Cluster（~15 min）
- 按 `clusterInstanceReplicas` 起 ClusterInstance（一般 prod 2 个）
- Generator 链路写 master-password 到 Vault aurora 路径
- Crossplane Cluster 写 conn Secret，含 endpoint + reader_endpoint + port + master_username + attribute.master_password
- PushSecret 推到 Vault aurora 路径
- app ExternalSecret 渲染 5 key 的 K8s Secret
- Rollout envFrom 拿 DB_HOST + DB_READER_HOST + ...

**应用层做读写分离**：
- 写操作（INSERT / UPDATE / DELETE / DDL）用 `DB_HOST`
- 只读 query 用 `DB_READER_HOST`
- ORM 通常支持 read replica（Spring Data + Hibernate / SQLAlchemy / Sequelize 等）

**常见踩坑**：
- prod 只起 1 个 ClusterInstance → 没 reader endpoint（writer 也是 reader）；按 cost-tiering 强制 2 个
- v2.0.0 observe 误匹配（同 RDS）→ Cluster + 每个 ClusterInstance 都要显式 `identifier` + `external-name` annotation
- master-password 链路坏 → 跟 RDS 同款修法，跳 `troubleshooting/pushsecret-stuck-endpoint-does-not-exist.md`
