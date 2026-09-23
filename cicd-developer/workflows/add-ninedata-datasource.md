---
name: add-ninedata-datasource
description: 把现有 RDS / Aurora 注册到 ninedata.addx.live（自建 NineData WebUI），让开发者自助 SQL 查询 / schema 变更，不再开运维 ticket。仅海外集群可用（CN 暂无 license）。
---

# Workflow：add-ninedata-datasource

## 目的

通过声明一个 Crossplane DataSource CR，把 RDS / Aurora 注册到 NineData WebUI：

- ArgoCD sync → provider-ninedata 调 NineData OpenAPI 建 DataSource
- provider 自动 patch `cloudProfile` / `accessAlias` / `regionId` 等 meta（OpenAPI 不暴露的字段）
- 开发者在 ninedata.addx.live 看到数据源，按需申请查询 / DDL 权限

跟 add-rds / add-aurora 关系：数据库已经能起来后，本 workflow **可选**，但跑完后开发者不用开 ticket 找运维查 DB。非 staging / legacy app-owned 的新 RDS / Aurora 默认建议都跑（海外集群）；新部署 staging 应用消费 shared-middleware 时不自动注册 NineData。

## 首次部署调用约定

- 独立调用默认 `$delivery_phase=runtime`，下面“数据库已建好”、live Secret schema、
  配额以及 Step 5 验收全部保留。
- new-service Step 10 传入 `candidate` 时，只核对海外/app-owned 资格与配额前置，
  把准确 target 和本 workflow Step 1 加入父流程 `$pending_runtime_checks[]` 后返回。
  此时不进入下面要求 DB 已 Ready 的步骤、不生成 DataSource、不猜连接 Secret schema，
  也不声称 NineData 子流程完成；资格/配额的真实 STOP 不能伪装为延期。
- new-service Step 11.5 在 DB/Secret 已同步就绪后以 `runtime` 从 Step 1 恢复，
  重新核对配额，提交独立后续 app MR；只有已有准确授权与审批门禁满足时才合并/同步。
  新服务的首次 Application 注册不能反过来等待本流程 Step 5。

## 进入条件

1. **集群必须是海外**——cn-tech-service / cn-prod / cn-dev / cn-staging 四个 CN EKS 集群没装 provider-ninedata（NineData 无 CN license）。如果 target 是 CN → **STOP** 产 ops-todo "等运维补 CN NineData license + 部署 provider"
2. **新部署 staging / tech-service shared-middleware 禁止自动注册** —— 如果 `$target.cluster` 是
   shared-middleware 集群（6 个：`{us,eu,cn}-eks-staging` + `{us,eu,cn}-eks-tech-service`；按 cluster 名判非 env，tech-service env=prod）
   也不要为来自 `references/shared-middleware/README.md` 公共 contract 的 RDS/Aurora 注册 NineData；仅当检测到 cd-requirements.md 或已有 ExternalSecret 明确该 DB 是 app-owned（而非来自公共 contract）时，才继续；此时 Vault 路径应为新规范的 per-app region-less 格式 `secret/{env}/{platform}/application/{app}/{key}`（如 RDS 则为 `secret/{env}/rds/application/{app}/database`）。
   本 workflow **STOP**。不要把公共 RDS 注册成单 app NineData datasource，也不要去找
   `secret/staging/rds/application/<app>/database` 这类 app-owned 路径。只有 legacy
   app-owned DB、明确迁移例外，或平台定义了 shared-middleware datasource 管理策略后，
   才能继续，并必须在 cd-requirements.md 记录例外原因。
3. **数据库已建好** —— `add-rds.md` / `add-aurora.md` 跑完，连接信息已经推到平台规范 Vault 路径：
   - RDS：`<app>-rds-conn`
   - Aurora：`<app>-aurora-conn`
   **特别注意**：NineData 最终消费的 Secret 必须有 `host` / `port` / `username` / `password`；原始 Crossplane Secret 不一定天然满足，见下文 Step 1。
4. **50 slot 配额** —— NineData 自建是 ENTERPRISE license，整 org 共享 50 slots。**提交前找运维（qlv）确认剩余**。≥48 时容易踩 `COMMERCIAL_INSUFFICIENT_QUOTA`，**失败 create 还会留 orphan record**（要运维 DB DELETE 清）

## Step 1. 决定 DataSource 使用哪个连接 Secret

[precondition]
  - `workflows/add-rds.md` 或 `add-aurora.md` 已跑完
  - 用户已告知 `$target_env_keyword`
  - 先确定 `$db_kind` 和 `$raw_conn_secret`：
      - 跑的是 `add-rds.md` / 应用仓 `k8s/overlays/<env>/rds-instance.yaml`（或 legacy `crossplane-infra/<cluster_dir>/{$app}-rds.yaml`）存在 → `$db_kind=rds`
      - 跑的是 `add-aurora.md` / 应用仓 `k8s/overlays/<env>/aurora.yaml`（或 legacy `crossplane-infra/<cluster_dir>/{$app}-aurora.yaml`）存在 → `$db_kind=aurora`
      - `$db_kind=rds` → `$raw_conn_secret={$app}-rds-conn`
      - `$db_kind=aurora` → `$raw_conn_secret={$app}-aurora-conn`
  - `$target.vault_connection_path` 可由数据库类型解析：
      - RDS：`secret/{$env}/rds/application/{$app}/database`
      - Aurora：`secret/{$env}/aurora/application/{$app}/cluster`
  - `$target.vault_connection_remote_key` = 去掉 `secret/` 前缀后的 ExternalSecret `remoteRef.key`：
      - RDS：`{$env}/rds/application/{$app}/database`
      - Aurora：`{$env}/aurora/application/{$app}/cluster`
  - K8s Secret `$raw_conn_secret` 在目标 namespace 存在

[action]
  - 按下文 Step 2 的同一套 `env-keywords.yaml` / `clusters.yaml` 解析，先拿到 `$namespace`、`$env`、`$target.vault_css`
  - 如果 `$target.cluster` 是 shared-middleware 集群（6 个：`{us,eu,cn}-eks-staging` +
    `{us,eu,cn}-eks-tech-service`；**按 cluster 名判，不用 `$env`**——tech-service env=prod）
    且 cd-requirements.md / 现有 ExternalSecret 显示 DB 来自共享中间件（新 per-app 路径
    `secret/{env}/{platform}/application/{app}/...`，或 legacy `staging-<region>/shared-middleware/...`）：
      - **STOP**，输出 "new shared-middleware DB does not auto-register
        NineData datasource; define platform shared datasource policy first"
      - 例外只允许 legacy app-owned DB / 迁移场景，并必须能在应用仓
        `k8s/overlays/<env>/rds-instance.yaml` / `aurora.yaml`（或 legacy
        `crossplane-infra/<cluster_dir>/{$app}-rds.yaml` / `{$app}-aurora.yaml`）找到
        app-owned DB 证据
  - 运维执行（开发者无 kubectl）：
      ```
      if command -v jq >/dev/null 2>&1; then
        kubectl -n <namespace> get secret <raw_conn_secret> -o jsonpath='{.data}' | jq 'keys'
      else
        raw_data_json="$(kubectl -n <namespace> get secret <raw_conn_secret> -o jsonpath='{.data}')"
        python3 - "$raw_data_json" <<'PY'
      import json
      import sys

      data = json.loads(sys.argv[1] or "{}")
      print(sorted(data.keys()))
      PY
      fi
      ```
  - 如果 RDS 原始 Secret 已含 `host` / `port` / `username` / `password`：
      - 直接设 `$db_conn_secret={$app}-rds-conn`
  - 如果是 Aurora，或 RDS 原始 Secret 只有 `endpoint` / `port` / `username` / `password`：
      - 设 `$db_conn_secret={$app}-ninedata-conn`
      - 用 `recipes/crossplane/ninedata-conn-external-secret.yaml.tmpl` 写：
        `k8s/overlays/{$target_env_keyword}/infra/ninedata-conn-external-secret.yaml`
      - 填槽：
        `{{db_conn_secret}}=$db_conn_secret`、`{{namespace}}=$namespace`、
        `{{cluster_secret_store}}=$target.vault_css`、`{{refresh_interval}}=1h`、
        `{{vault_remote_key}}=$target.vault_connection_remote_key`
      - 这个模板从 Vault `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` 派生 NineData 需要的
        `host` / `port` / `username` / `password`，并对 `DB_HOST` 做 `regexReplaceAll ":[0-9]+$"`，避免把 `host:port` 复合值喂给 NineData
  - 如果原始 Secret 既没有 `host` 也没有 `endpoint`，或 `$target.vault_connection_path` / `$target.vault_connection_remote_key` 不明确：
      - **STOP**，输出实际 keys + 目标 env，让运维先确认 provider 输出 schema

[validate]
  - `$db_conn_secret` 决策已完成：要么直接引用 raw Secret，要么记录了 normalization ExternalSecret 的目标文件和全部 slot
  - 如果直接引用 raw Secret，它已确认含 `host` / `port` / `username` / `password`

[output]
  - 内存：`$db_kind`、`$raw_conn_secret`、`$db_conn_secret`（DataSource 实际引用哪个 Secret）

## Step 2. 解析 target 信息

[precondition]
  - Step 1 完成

[action]
  - 用户告知 `$target_env_keyword`（如 staging-us / prod-eu）
  - 查 `references/data/env-keywords.yaml`：
      - `$cluster` = cluster 名
      - `$namespace` = namespace_pattern 解出来
      - `$env` = staging | prod
  - 查 `references/data/clusters.yaml -> clusters[<cluster>]`：
      - 必须 `region ∈ {us, eu}`；**`region == cn` → STOP**
  - 解析 NineData WebUI 显示名前缀 `$region_zh`：
      - region=us → `美国`
      - region=eu → `欧洲`
  - 解析 `$env_id`：
      - env=staging or dev → `env-dev`
      - env=prod → `env-product`
      - **不许**写 env-staging / env-prod（NineData system env 只这两个）

[validate]
  - `$cluster.region ∈ {us, eu}`
  - `$env_id ∈ {env-dev, env-product}`

[output]
  - `$cluster` / `$namespace` / `$region_zh` / `$env_id` / `$datasource_type`（MySQL | PostgreSQL | Redis；通常 = RDS/Aurora engine）

## Step 3. 找运维确认 NineData slot 剩余

[precondition]
  - Step 2 完成

[action]
  - 找运维（qlv）问当前 NineData 剩余 slot 数
  - 剩余 ≥ 2 → 继续
  - 剩余 1-2 → 警告用户：可能踩 `COMMERCIAL_INSUFFICIENT_QUOTA`，建议先让运维清 orphan records 或扩配额
  - 剩余 0 → STOP，产 ops-todo "NineData quota 用完，找运维处理"

[validate]
  - 用户/运维确认剩余 slot 充足

[output]
  - 可以继续的信号

## Step 4. 写 DataSource CR

[precondition]
  - Step 3 通过

[action]
  - 用 `recipes/crossplane/ninedata-datasource.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{env_keyword}}=$target_env_keyword`、
      `{{env}}=$env`、`{{region_zh}}=$region_zh`、
      `{{datasource_type}}=$datasource_type`、`{{namespace}}=$namespace`、
      `{{db_conn_secret}}=$db_conn_secret`、`{{env_id}}=$env_id`、
      `{{management_policies_block}}=<prod 时完整、缩进两格的 managementPolicies 块；staging/dev 留空>`
  - prod target 的 slot 必须渲染成完整、缩进两格的
    `managementPolicies:` 块；该块里的列表必须是
    `[Observe, Create, Update, LateInitialize]`，排除 Delete 防误删 NineData 数据。
    staging/dev 的 slot 必须留空（默认全权 `*`）。**不要**在渲染后手工删除该段。
  - 写到 `k8s/overlays/{$target_env_keyword}/infra/ninedata-datasource.yaml`
  - 加到 `k8s/overlays/{$target_env_keyword}/kustomization.yaml` 的 resources 列表
  - 如果 Step 1 写了 `infra/ninedata-conn-external-secret.yaml`，也必须一起加到同一个 resources 列表

[validate]
  - 文件存在
  - 如有 normalization ExternalSecret，文件也存在
  - YAML 可解析
  - `kustomize build k8s/overlays/{$target_env_keyword}/` 成功
  - manifest 含正确字段：
      - `cloudProfile.env: AWS` / `instanceType: Aurora` / `cloudInstanceType: URL` / `accessAlias: aws-overseas`
      - `regionId: ninedata-cn-hangzhou`
      - **没有** `deletionPolicy` 字段（CRD 不接受）

[output]
  - k8s/overlays/{$target_env_keyword}/infra/ninedata-datasource.yaml

## Step 5. 验证部署

[precondition]
  - Step 4 完成，commit + push + MR 合并 + ArgoCD sync 完成
  - 合并和同步已经获得本会话准确授权并满足仓库门禁；若仅授权生成配置，交付具体 MR
    与 Step 5 pending 验收，不把本 precondition 当作合并授权，也不报告 datasource Ready。

[action]
  - 运维执行（开发者无 kubectl）：
      ```
      kubectl get datasource {$app}-{$target_env_keyword}-db
      # 期望 Ready=True + SYNCED=True
      ```
  - 看 CR annotation：
      ```
      kubectl get datasource {$app}-{$target_env_keyword}-db -o jsonpath='{.metadata.annotations.crossplane\.io/external-name}'
      # 期望返回 ds-xxxxxx
      ```
  - 登 `https://ninedata.addx.live` → 数据源管理 → 应看到名为 `{$region_zh}-{$env}-{$app}-实例` 的记录
  - 如果没有已登录且有权限的 NineData WebUI session，可用 OpenAPI `/openapi/v1/datasource/list` 只读确认 datasourceId / name / datasourceType / host / envId / regionId；不要把 SQL 窗口查询当作本 workflow 的必需验收

[validate]
  - Ready=True / SYNCED=True
  - WebUI 或 OpenAPI 能看到新数据源
  - 不要求 SQL smoke：查询 / DDL 权限、账号授权、审批流属于 NineData WebUI 人工流程；如果用户额外要求 SQL 查询或 SQL 任务验证，必须先拿到已授权 NineData 用户/session，并把它作为单独 E2E 记录

[output]
  - NineData 数据源就绪

## Step 6. 出 summary + Ops Todo

[precondition]
  - Step 5 通过

[action]
  - summary card：
    - 注册到 NineData 的数据源名 + ds-xxxxxx ID
    - 开发者下一步：登 ninedata.addx.live 申请查询 / DDL 权限
  - Ops Todo：
    - 如本次部署用了 `ninedata-conn-external-secret.yaml` normalization → 记录"NineData 使用派生连接 Secret"
    - 如配额接近上限 → 提示运维评估扩 quota 或清 orphan

[validate]
  - summary 已打印

[output]
  - 最终消息

## 出口

ArgoCD sync 后用户拿到：
- AWS RDS 已注册到 NineData
- WebUI 数据源名 `<region_zh>-<env>-<app>-实例`
- ds-xxxxxx ID 写在 CR annotation
- 任何应用 owner 可登 ninedata.addx.live 申请查询 / DDL 权限（这一步走 NineData WebUI 流程，不是本 workflow 范围）

**当前不在 Crossplane 自动化范围**（NineData WebUI 手工）：
- Account 创建（user 注册）
- 角色 / 权限 / DDL 审批策略
- saved query / change task 配置

这些走 NineData WebUI；CR 只搞 DataSource 这一层。

**任何失败模式** → `troubleshooting/ninedata-datasource-issues.md`：
- `Connect failed! username is required`（SQL 查询挂） → cloudProfile 三字段缺失
- `Init source connector failed` + `AccessKey must not be null`（变更 + 备份挂） → accessAlias 缺失
- 单 `AccessKey must not be null`（io.minio.MinioClient） → regionId 被改写
- `COMMERCIAL_INSUFFICIENT_QUOTA` → 配额满
- `cloudProfile.accessAlias "xxx" is not registered` → ProviderConfig Secret 未下发 alias
