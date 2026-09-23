---
name: add-redis
description: 给现有服务加 AWS ElastiCache Redis（生产用 ReplicationGroup，多副本跨 AZ）。生成 Crossplane CR + IAM RolePolicy + ExternalSecret 让 app 读连接信息。
---

# Workflow：add-redis

## 目的

通过 Crossplane GitOps 给现有服务开 Redis（**默认引擎 Valkey 9.1**，非 Redis OSS），让 app 从 K8s Secret 读 `REDIS_HOST` / `REDIS_PORT` 等。末态：

- AWS ElastiCache Redis 实例存在（dev/staging/pre 默认 Cluster；prod 强制 ReplicationGroup + multiAZ + 副本数 ≥ 2）
- Crossplane PushSecret 把连接信息推到 `secret/{env}/redis/application/<app>/redis`
- app 通过 ExternalSecret 读到 K8s Secret

## 进入条件

- 应用已 `k8s/base/` + 一个 overlay 就绪
- cd-requirements.md 列了 Redis 需求
- `$delivery_phase` 默认为 `runtime`；只有 `new-service` 显式调用时使用 `candidate`。
  candidate 完成配置和离线校验，登记准确 target、workflow、待验收项到
  `pending_runtime_checks`，由父流程首次 Application 同步后验收；不能据此声称实例或 Secret 已就绪。
- 先按 Step 1 分流 target。只有 app-owned `$managed_targets[]` 要求可写对应
  `crossplane-infra/{cluster_dir}/`，Step 3 会创建或补全该 app 的 Crossplane Role、
  `ClusterProviderConfig` 与 Redis `RolePolicy`；这不是 Pod runtime IRSA，**不要**因为
  它缺失而先跑 `add-irsa-role.md`。共享 claim 不要求新增 app IAM。
- app-owned target 的集群装了 `crossplane-conn-patcher`（平台前置，把 Redis connection
  Secret 归一成 `address` + `port`；没装产 ops-todo）。共享 target 按现有 Composition 的
  producer/PushSecret readiness 验收，不要求为消费方另建 patcher/实例权限。
- 若包含 staging（或 tech-service）target：**不要走本 app-owned workflow，改走 `kind: Database`
  自助——已上线**（2026-06-03，6 集群 us/eu/cn-staging + tech-us/eu/cn）。开发者在自己 app ns 用
  `recipes/k8s/shared-database-claim.yaml.tmpl` 从 canonical kebab app slug 生成
  `platform.addx.io/v1alpha1 kind: Database`：annotation 保留 slug，`spec.app` 把 `-` 机械替换为 `_`
  （`$database_app = $app.replace('-', '_')`），
  禁止手填拼接式多词名；`spec.engine: redis`。Composition
  （function-go-templating + provider-kubernetes）把**共享 ElastiCache 的 endpoint 推到 vault**
  `secret/{env}/redis/application/{database_app}/connection`（key: `host` / `port`），app 自己写 ExternalSecret
  读 → 注入 `REDIS_HOST`/`REDIS_PORT`。**不再 Ops Todo、不再 configmap 硬编码 endpoint。**
  ⚠️ 共享 Redis 是**无 auth 单节点**（无 authToken/TLS/RBAC,纯 SG+VPC 隔离）→ Composition 只发
  endpoint、**无 per-app 凭据、非真隔离**（真隔离需把实例切 RBAC 模式 + per-app ACL user,是破坏性
  变更,另起 workflow）。app 用 key 前缀（如 `pe:`）做软隔离。
  非 shared-middleware 集群的**独立** app-owned Redis（prod 专用实例）仍可继续本 workflow。

## Step 1. 解析需求 + tier

[precondition]
  - cd-requirements.md 有 Redis 条目

[action]
  - 提取 `$app`、`$targets[]`、是否需要 `REDIS_AUTH_TOKEN`。
  - 先按 `clusters.yaml` 分流，不能先用 env=prod 或 app-owned SG/成本条件判定 shared target：
    - `$unsupported_targets[]`：`$target.cloud != aws`。不解析 AWS 成本、ARN 或
      ProviderConfig，只产 Ops Todo；不能把 AWS 契约套到 TKE 或 GCP。
    - `$shared_middleware_targets[]`：AWS 且 `$target.cluster` 属于
      `{us,eu,cn}-eks-staging` + `{us,eu,cn}-eks-tech-service` 这 6 个集群。
      **按 cluster 名判，不用 `$target.env`**；tech-service 的 env=prod 仍消费共享实例。
    - `$managed_targets[]`：其余 cloud=aws target，或已明确批准的 AWS legacy/迁移例外。
      后续 Step 2 起只处理这组。
  - 对 shared target，先检查请求是否需要 auth/独立 ACL/TLS；当前共享实例**无 auth、非真隔离**，
    不满足时在写入前 STOP + 平台能力 Ops Todo。通过后用 `shared-database-claim.yaml.tmpl` 从 canonical kebab `$app`
    派生 `$database_app = $app.replace('-', '_')`，生成 `engine: redis` claim 和应用自己的
    ExternalSecret 消费配置。Composition 把共享 endpoint 推到
    `secret/{env}/redis/application/{database_app}/connection`（host/port）。
    consumer 使用 `recipes/k8s/external-secret.yaml.tmpl`，`{{name}}=$app-redis-secret`，
    `{{namespace}}=$target.namespace`、`{{cluster_secret_store}}=$target.vault_css`、
    `{{refresh_interval}}=1h`；`{{key_mappings}}` 只把小写 property `host`/`port` 映射成
    `REDIS_HOST`/`REDIS_PORT`，两项 remoteRef.key 都是
    `{env}/redis/application/{database_app}/connection`（无 `secret/` 前缀）。不能使用要求
    username/password 的 SQL consumer 模板，也不能沿用 app-owned 分支的大写 Vault property。
    将 claim 与 consumer 写入准确 app overlay 的独立文件并加入其 Kustomization resources；
    已有同名 consumer 时先核对 source 和用途，不得为新增连接覆盖运行中的另一个 consumer。
    按下方「容器消费连接信息」将 consumer 接入这个 target 的应用容器；不能只创建
    ExternalSecret，也不能因另一个 target/sidecar 已有引用而跳过。
    不生成 app-owned Redis CR / PushSecret，不套用 app-owned `.../redis` Vault key，
    不要求实例版本、prod ReplicationGroup tier、新 app IAM 或 SG。
    按 `references/shared-middleware/README.md` 区分 Git 候选生成与 live 首次供给验收；
    已有消费者迁移必须先验证 producer，再切换到新路径。
  - 只对 `$managed_targets[]` 解析以下 app-owned 配置：
    - **默认引擎 = Valkey（非 Redis OSS），默认 engineVersion `9.1`**（app-owned CR 由 recipe 模板
      固定 engine:valkey / engineVersion:"9.1"；已验证 us/cn 可用、CRD engine 字段接受 redis|valkey）。
      用户明确要 Redis OSS 或其它版本时，在 cd-requirements.md 记录理由后再改模板值。
    - 查 `references/cost-tiering/redis.yaml -> $target.env` 取 kind / nodeType / numNodes /
      automaticFailoverEnabled / multiAZ / snapshotRetentionLimit_days / atRestEncryption /
      managementPolicies。nodeType 默认 t4g；高资源占用改 r8g/m8g 需需求明确，不能自动升档。
    - prod env → 同时读 `cost-tiering/_global.yaml -> prod_self_check`，每条用户确认；任何 "no" 则 STOP
    - 查 `clusters.yaml` 取 aws_region（记为 `$target.region`）、account_id、partition、
      namespace、vault_css、argocd_apps_dir（记为 `$target.cluster_dir`）。
    - 查同集群其它应用仓 overlay 的 Redis CR 的 securityGroupIds，确认适用于当前
      私网访问边界后复用为 `$target.redis_security_group`；无可复用 SG 则 STOP + Ops Todo
      “声明 Redis SG；共享/跨账号访问规则由 crossplane-infra 管理”。

[validate]
  - target 三组明确；shared claim 的 namespace/owner/Vault 路径及能力符合共享契约。
  - 仅 managed target 要求上述实例字段完整，`$target.kind ∈ {Cluster, ReplicationGroup}`，
    其中 prod 的 kind 必须为 ReplicationGroup。
  - 对每个改动的 shared app overlay 创建独立临时目录：
    `shared_render_dir="$(mktemp -d)"`，再执行
    `kustomize build k8s/overlays/{$target.env_keyword}/ > "$shared_render_dir/manifest.yaml"`。
    render 成功后执行
    `bash "$skill_root/validators/validate.sh" --repo-context app "$shared_render_dir"`，验证继承 base
    和应用 patch 后的最终资源，不能只验证 raw overlay。两者必须退出 0；缺依赖、渲染失败或
    validator 非零均 STOP，不允许因 shared-only 提前返回而跳过。
  - 每个 shared target 还必须执行下方「容器消费连接信息」的精确容器检查，退出 0。
    claim 的发布还须通过 `references/shared-middleware/README.md#producer-identity-gate`
    的目标库存检查；普通 validator PASS 不证明跨仓身份无冲突。
  - 如果 `$managed_targets[]` 为空，在共享 Git 候选/校验或具体能力缺口 Ops Todo 后结束，
    不要继续 Step 2。candidate 将 producer/ExternalSecret readiness 和应用读取验证登记到
    `pending_runtime_checks`（本 workflow 的「出口」）；runtime 保留这些验收，缺 live producer
    证据时不得声称发布已完成。

[output]
  - `$managed_targets[]` 完整解析；后续 Step 2 起只处理 `$managed_targets[]`
  - shared target 的 claim/consumer/Kustomization 文件列表、逐 overlay 渲染与验证结果；
    具体能力缺口或 pending live producer/consumer 验收单独列入 Ops Todo。

### 容器消费连接信息

此段由 shared Step 1 和 managed Step 7 共用。先渲染准确 overlay，确定 `$workload_kind`、
`$workload_name`、`$container_name`（应用主容器，不是 sidecar），再按名字找到其源配置。
只修改该 target overlay 的精确 workload/container，保留已有 env、envFrom 和其它容器。
Rollout 的列表不依赖默认 strategic merge：JSON patch 使用已核对的容器索引；数组不存在时先创建，
存在时追加，不整体替换 containers。若 base 已有合法引用，只验证，不重复添加。

- 可追加无 prefix、非 optional 的 `envFrom.secretRef.name: <app>-redis-secret`；必须在原有
  envFrom 后面，且不能被同名显式 env 覆盖。现有显式值来源不明或冲突时先确认用途，不静默覆盖。
- 或为 `REDIS_HOST`、`REDIS_PORT` 分别使用同名 key 的非 optional `valueFrom.secretKeyRef`，
  name 均为 `<app>-redis-secret`；这种方式无需改变其它 envFrom 的优先级。
- 再次 render 后运行（使用实际名字，不从文件名推断 workload/container）：

  ```bash
  python3 "$skill_root/validators/check_workload_secret.py" "$shared_render_dir/manifest.yaml" \
    --namespace "$target_namespace" --kind "$workload_kind" --workload "$workload_name" \
    --container "$container_name" --secret "$app-redis-secret" \
    --key REDIS_HOST --key REDIS_PORT
  ```

`$target_namespace` 来自当前 `$target.namespace`；managed 分支也为自己的 render 创建
`$shared_render_dir`。该检查只证明渲染的应用容器引用正确，Secret readiness 和连接仍在出口验收。
若同一 render 的 consumer ExternalSecret 有更后的 sync-wave，先把准确 workload 安排在
consumer 之后（保留已有更高 wave），避免 workload 等不到下一 wave 才创建的 Secret；
不能改低 producer/consumer 的已声明顺序。首次 SQL migration 的 bootstrap 顺序由 new-service 管理。

## Step 2. 计算 Vault 路径

[precondition]
  - Step 1 完成

[action]
  - 对每个 `$managed_targets[]` 中的 `$target`，调
    `references/vault-paths/resolver.md` resolver：
    `env=$target.env, kind=redis, app=$app, key=redis`。
  - 记录 `$target.vault_path`（应为 `secret/{env}/redis/application/<app>/redis`）和
    `$target.vault_remote_key`（去掉前导 `secret/`），供 PushSecret writer 的
    `remoteRef.remoteKey` 与 app ExternalSecret reader 的 `remoteRef.key` 共用；
    两者都相对 ClusterSecretStore 的 `path=secret`。

[validate]
  - 路径匹配 vault-paths/rules.yaml Rule 2

[output]
  - 每 target 一个完整 Vault 路径，另有 ESO writer/reader 共用的 `$target.vault_remote_key`

## Step 3. 写 Crossplane RolePolicy（每集群一次）

[precondition]
  - Step 2 完成

[action]
  - 先确保 `crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml` 里已有
    `crossplane-app-{$app}` Role + `ClusterProviderConfig {$app}`；没有就先用
    `recipes/crossplane/app-providerconfig.yaml.tmpl` 创建/append。
  - 如 `crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml` 已含 `crossplane-app-{$app}-redis` RolePolicy → 跳过。
    否则用 `recipes/crossplane/redis-rolepolicy.yaml.tmpl` 写入该文件，填
    `{{app}}=$app`、`{{partition}}=$target.partition`、`{{region}}=$target.region`、
    `{{account_id}}=$target.account_id`。不要 inline RolePolicy，也不要把 AWS partition 写死为 `aws`。

[validate]
  - 同文件或同目录已有 `ClusterProviderConfig {$app}`，否则 Redis CR 的 `providerConfigRef.name: {$app}` 会失败
  - YAML 可解析

[output]
  - crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml 更新

## Step 4. 写 Crossplane Redis CR（每 target 循环）

[precondition]
  - Step 3 完成

[action]
  - 如果 `$target.kind == Cluster`：用 `recipes/crossplane/elasticache-cluster.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{env_keyword}}=$target.env_keyword`、
      `{{namespace}}=$target.namespace`、`{{node_type}}=$target.nodeType`、
      `{{num_nodes}}=$target.numNodes`、`{{management_policies}}=$target.managementPolicies`、
      `{{region}}=$target.region`、`{{redis_security_group}}=$target.redis_security_group`
  - 如果 `$target.kind == ReplicationGroup`：用 `recipes/crossplane/elasticache-replicationgroup.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{env_keyword}}=$target.env_keyword`、
      `{{namespace}}=$target.namespace`、`{{node_type}}=$target.nodeType`、
      `{{num_nodes}}=$target.numNodes`、
      `{{automatic_failover}}=$target.automaticFailoverEnabled`、
      `{{multi_az}}=$target.multiAZ`、
      `{{snapshot_retention}}=$target.snapshotRetentionLimit_days`、
      `{{at_rest_encryption}}=$target.atRestEncryption`、
      `{{management_policies}}=$target.managementPolicies`、
      `{{region}}=$target.region`、`{{redis_security_group}}=$target.redis_security_group`
  - 写到应用仓库 `k8s/overlays/{$target.env_keyword}/redis.yaml`，并加入 overlay
    `kustomization.yaml` 的 `resources`。**app-owned Redis CR 放应用仓 overlay，不放
    crossplane-infra**（同 add-rds.md Step 5：crossplane-infra 只收 IAM / IRSA /
    ProviderConfig / WAF/IPSet / 共享 SG 入站规则等权限边界资源；namespaced CR 与 conn Secret 同仓同 namespace）

[validate]
  - YAML 可解析；`kustomize build k8s/overlays/{$target.env_keyword}/` 成功
  - Redis CR `apiVersion` 使用集群已安装的 `elasticache.aws.m.upbound.io/v1beta1`
  - 对目标集群运行 server-side dry-run；v2 `ReplicationGroup` 以
    `metadata.annotations["crossplane.io/external-name"]` 作为 AWS replication group ID，
    **不得**写 live CRD 不存在的 `spec.forProvider.replicationGroupId`
  - Redis CR `metadata.namespace` 等于 `$target.namespace`
  - `writeConnectionSecretToRef` 不带 `namespace` 子字段；connection Secret 与 Redis CR 同 namespace

[output]
  - k8s/overlays/{$target.env_keyword}/redis.yaml

## Step 5. 写连接信息 PushSecret（每 target 循环）

[precondition]
  - Step 4 完成

[action]
  - 用 `recipes/crossplane/redis-conn-push-secret.yaml.tmpl` 写
    `k8s/overlays/{$target.env_keyword}/redis-conn-push.yaml`，填
    `{{app}}=$app`、`{{env}}=$target.env`、`{{namespace}}=$target.namespace`、
    `{{cluster_secret_store}}=$target.vault_css`。模板固定 normalized `address` + `port`
    输入、`IfNotExists` 和相对 Vault `remoteKey`；不要改成 `endpoint` 或 inline PushSecret。
  - 加到 kustomization resources

[validate]
  - YAML 可解析
  - `kustomize build` 成功
  - `python3 "$skill_root/validators/check_eso_pushsecret_bug.py" <dir>` PASS
  - `python3 "$skill_root/validators/check_vault_paths.py" <dir>` PASS；PushSecret `remoteKey`
    不带字面 `secret/` 前缀

[output]
  - k8s/overlays/{$target.env_keyword}/redis-conn-push.yaml

## Step 6. 写 app ExternalSecret（每 target 循环）

[precondition]
  - Step 5 完成

[action]
  - 用 `recipes/k8s/external-secret.yaml.tmpl`，填槽：
      `{{name}}={$app}-redis-secret`、`{{namespace}}=$target.namespace`、
      `{{cluster_secret_store}}=$target.vault_css`、`{{refresh_interval}}=1h`、
      `{{vault_remote_key}}=$target.vault_remote_key`、
      `{{key_mappings}}=`：
        ```yaml
        - secretKey: REDIS_HOST
          remoteRef:
            key: {{vault_remote_key}}
            property: REDIS_HOST
        - secretKey: REDIS_PORT
          remoteRef:
            key: {{vault_remote_key}}
            property: REDIS_PORT
        ```
  - 写到 `k8s/overlays/{$target.env_keyword}/redis-external-secret.yaml`
  - 加到 kustomization resources

[validate]
  - YAML 可解析；`kustomize build` 成功；`python3 "$skill_root/validators/check_vault_paths.py" <dir>` PASS

[output]
  - k8s/overlays/{$target.env_keyword}/redis-external-secret.yaml

## Step 7. 把连接 Secret 接到应用容器

[precondition]
  - Step 6 完成

[action]
  - 每个 managed target 按 Step 1「容器消费连接信息」修改准确 overlay，并渲染到独立临时目录。
    不在共享 base 中给未请求 Redis 的 target 注入引用；已有正确 base 引用无需重复添加。

[validate]
  - 精确容器 `check_workload_secret.py` 检查退出 0；保留其它 target 和 sidecar 的配置。

[output]
  - 准确 overlay 的应用容器引用与渲染检查结果

## Step 8. 全量 validator + 文档 + summary

[precondition]
  - Step 7 完成

[action]
  - `bash "$skill_root/validators/validate.sh" k8s/` 和每个 crossplane-infra target 目录
  - 更新 `cd-requirements.md` Secrets 段
  - `cicd.md` append Redis provisioning 说明
  - Ops Todo + summary

[validate]
  - validator 全 0；文档无 `{{...}}` 残留

[output]
  - 最终用户消息

## 出口

candidate 在离线门禁通过后登记下列验收并返回；runtime（或父流程恢复验收）在 ArgoCD sync 后：
- shared target：按 shared-middleware README 验证 Database/producer、PushSecret、ExternalSecret Ready，
  应用 Secret 包含 `REDIS_HOST`/`REDIS_PORT`，准确应用容器确实消费它；不打印值。
- managed target：继续以下实例链验收：
- Crossplane 创建 ElastiCache Cluster 或 ReplicationGroup（prod RG 需 ~10 min 起完整）
- `<app>-redis-conn` 最终含 `address` + `port`：
  - Cluster：provider 写基础 Secret，conn-patcher 补 `address`
  - ReplicationGroup：conn-patcher 创建 / 填充 Secret
- PushSecret 推 address+port 到 Vault redis 路径
- app ExternalSecret 渲染 `<app>-redis-secret`，Rollout envFrom 拉到 `REDIS_HOST`/`REDIS_PORT`

**常见踩坑**：
- ElastiCache 起后 PushSecret 报 `secret key address does not exist` → conn-patcher 未补 `address` 字段，跳 `troubleshooting/redis-pushsecret-address-missing.md`
- prod 用 Cluster kind 副本数错位 → cost-tiering/redis.yaml prod 强制 ReplicationGroup
- 跨 region 访问 Redis → 不要做，应用在哪个 region 就用哪个 region 的 Redis；跨 region 走 service-to-service 不要直连 Redis
