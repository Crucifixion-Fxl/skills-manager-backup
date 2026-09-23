---
name: add-rds
description: 给现有服务加一个 AWS RDS Instance。生成 Crossplane Instance + IAM RolePolicy + ESO Password Generator 链 + 连接信息 PushSecret + app ExternalSecret。替代"找参考文档、复制 YAML、求别出错"的老流程。
---

# Workflow：add-rds

## 目的

通过 Crossplane GitOps 给现有服务开 RDS，走标准 A1 password-generator 链路（人不写 master password）。末态：

- AWS RDS 实例存在（Crossplane 创建）
- app 从 K8s Secret 读 `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD`，背后由 Vault 推
- Master password 在 `secret/{env}/rds/application/<app>/master-password`（Vault platform 路径）
- 连接信息在 `secret/{env}/rds/application/<app>/database`

Aurora（writer + reader endpoint）走 `add-aurora.md`，不走这个。

> **默认策略**：DB **默认走 Aurora**（`add-aurora.md`）。RDS 单实例 **MySQL Community**
> （本 workflow 的 `engine=mysql` 分支）**默认不选**——仅当 cd-requirements.md 显式记录理由
> override（如 legacy 迁移、明确低成本单实例场景）时才走本流程的 mysql 分支。postgres / mariadb
> 单实例、以及 staging/tech-service 的 shared-middleware `kind:Database{engine:mysql}` 自助链路
> **不受此默认影响**，按各自条件正常进行。
> instanceClass 默认 t4g 家族；高资源占用改 r8g/m8g，由用户在 cd-requirements.md 指定。

## 进入条件

- `docs/deployment/cd-requirements.md` 存在，且 Resources 段列了这个 RDS
- 应用已经有 `k8s/base/` + `k8s/overlays/{env_keyword}/`（new-service.md 跑过了）
- 先按 Step 1 分出 shared / unsupported / managed targets。只有 app-owned
  `$managed_targets[]` 才要求读取 `references/cost-tiering/rds.yaml`、理解 `immutable`
  字段，并可写对应 `crossplane-infra/{cluster_dir}/`。Step 4 会创建或补全该 app 的
  Crossplane Role、`ClusterProviderConfig` 与 RDS `RolePolicy`；**不要**把尚未存在的对象
  当作进入条件，也不要误路由到 workload IRSA workflow。共享 claim 不要求新增 app IAM。
- 若包含 staging（或 tech-service）target 且 **engine=mysql**：**不要走本 app-owned workflow**，改走 `references/shared-middleware/README.md` 的 `kind: Database` 自助申请——**已上线**（2026-06-03，6 集群：us/eu/cn-staging + tech-us/eu/cn；provider-sql + Composition + provider-kubernetes vault 交付全部 live + 验证）。用 `recipes/k8s/shared-database-claim.yaml.tmpl` 生成 claim：canonical kebab `$app` 写入 `platform.addx.io/app-slug`，`$database_app = $app.replace('-', '_')` 写入 `spec.app`，禁止手填拼接式多词名。Composition 自动在该集群共享 MySQL 上建 per-app database+user+grant，凭据 PushSecret 到 vault `secret/{env}/rds/application/{database_app}/database`（host/port/username/password），app 用 `recipes/k8s/shared-db-external-secret.yaml.tmpl` 消费。需要同库附加最小权限账号时，先执行 shared-middleware README 的 live CRD gate 且输出必须为 `array`，再在业务 claim 声明；空输出或非 `array` 则 STOP。**不要用 app-owned `db-external-secret.yaml.tmpl`，不要写 raw provider-sql User/Grant，也不要把业务账号/表名放平台仓。**不再产 Ops Todo。
- 若包含 staging（或 tech-service）target 且 **engine=postgres**：同 mysql，**改走 `kind: Database` 自助**——**已上线**（2026-06-04，6 集群：us/eu/cn-staging + tech-us/eu/cn）。同 mysql 用 `recipes/k8s/shared-database-claim.yaml.tmpl` 生成 `kind: Database`（kebab owner `$app` → snake `$database_app`），Composition 在该集群共享 PostgreSQL（ProviderConfig `shared-postgres`）上建 per-app `Role`+`Database`(**归 master `shared_mw` 所有**,保证删 claim 时可干净 DROP)+`Grant`(库级)。主库不填 `spec.purpose`，凭据 PushSecret 到 vault `secret/{env}/rds/application/{database_app}/postgres`。同一 owner 需要独立 PostgreSQL role/database 时，可填 optional `$purpose`：它必须是 2–20 字符 lower_snake、只能用于 postgres，且 `$database_app + "_" + $purpose` 总长不超过 63；`spec.app`、`platform.addx.io/app-slug` 和 namespace owner **保持不变**。平台派生数据库/role `${database_app}_${purpose}`，凭据仍在 owner 路径，末段改为 canonical kebab key `postgres-{$purpose.replace('_', '-')}`。app 用 `recipes/k8s/shared-db-external-secret.yaml.tmpl` 映射相同的小写四字段。禁止传 host/admin/path/owner/privilege 等底层控制字段，也禁止把 purpose 拼入 `spec.app` 或 app-slug。primary claim 由 app 用库级 CREATE 权创建**自有 schema**（migration 工具指定），默认 `public` 在 PG15+ **不可写**。purpose claim 必须先通过 shared-middleware README 的 **PostgreSQL purpose 目标能力 gate**：目标有 served Schema API 和已同步的 purpose Composition 后，平台通过 app 自身凭据创建同名 schema；未通过则 STOP + 平台 rollout Ops Todo，不能按六集群 primary 能力推断 purpose 已全量可用。
- 若 staging / tech-service target 是 **mariadb**：仍 **STOP** —— 无对应 Composition。产 Ops Todo："为 mariadb 补 shared-db Composition/ProviderConfig"。
- 其它共享数据中间件：**Redis 已自助上线**（走 `add-redis.md` 的 `kind: Database {engine: redis}`，**不 STOP**）；**MSK(Kafka) SCRAM 凭据已自助上线**（走 `KafkaScramCredential`，凭据落 `secret/{env}/kafka/application/{app}/sasl`，bootstrap brokers 写 AWS 原生 `:9096` endpoint，topic/ACL 治理仍 planned）；**Aurora / ClickHouse / DocumentDB 仍 STOP + Ops Todo**——其中 ClickHouse/DocumentDB 共享实例已于 2026-06-04 部署 6 集群、仅 kind:Database 消费 Composition 未落地（Ops Todo=补消费层非再建实例），Aurora 连共享实例都还没有。
- **真 prod 集群（`*-eks-prod`/`*-prod-data`，非 tech-service）**上独立实例的 app-owned RDS 仍可继续本 workflow。

## 首次部署调用约定

独立调用默认 `$delivery_phase=runtime`；new-service Step 10 显式传入 `candidate`
时，生成/离线验证完成后记录待验收状态，再由父流程 Step 11.5 恢复运行态检查。
shared 分支同样在 `$pending_runtime_checks[]` 记录准确 target、claim、producer/
consumer Secret 和 Ready 条件；candidate 不要求本次 claim 已存在于集群。
平台能力、producer identity 库存和权限检查仍是部署前置，不能把它们当作待创建资源而跳过。

## Step 1. 读 cd-requirements.md 提取 DB 上下文

[precondition]
  - cd-requirements.md 存在且有 RDS 条目

[action]
  - 解析文档，提取：
    - `$app`              kebab-case
    - `$targets[]`        Step 1 of new-service.md 同款，但**只**包含含本 RDS 的 target
    - `$engine`           postgres | mysql | mariadb
    - `$engine_version`   仅 app-owned target：pin 具体 minor；mysql 默认从
      `references/cost-tiering/rds.yaml -> engine_contracts.mysql.default_engine_version`
      读取（当前为 `"8.4.10"`），postgres / mariadb 使用需求中明确的具体版本
    - `$db_name`          仅 app-owned target：初始 database 名（snake_case；不填则由 app 名转 snake_case）
    - `$username`         仅 app-owned target：DB 用户名（snake_case 可，**不能**有 hyphen）
    - `$purpose`          shared PostgreSQL 的 optional secondary role/database purpose；
                          primary 留空，不能用于 app-owned RDS

  - 把 `$targets[]` 拆成：
    - `$shared_middleware_targets[]`：`$target.cluster` 是 shared-middleware 集群（6 个：
      `{us,eu,cn}-eks-staging` + `{us,eu,cn}-eks-tech-service`；**按 cluster 名判，不用 `$target.env`**——
      tech-service env=prod，见 #29）。**禁止 app-owned RDS**（#29），按引擎分流：
      **engine=mysql / postgres → kind:Database 自助已上线**（用 `shared-database-claim.yaml.tmpl`
      从 `$app` 机械派生 `$database_app = $app.replace('-', '_')`，见上面入口条件）。
      以下模板规则仅在本步 [validate] 的 claim/能力 gate 通过后执行，未通过不写入：
      - primary：`$database_claim_name="$app-$engine"`、`$purpose_field=""`
      - secondary postgres：`$database_claim_name="$app-postgres-$purpose_dns"`、
        `$purpose_field="  purpose: $purpose"`；`spec.app` 仍填 `$database_app`
      - SQL consumer 用 `recipes/k8s/shared-db-external-secret.yaml.tmpl`，显式填
        `{{consumer_secret_name}}`：已有 primary 保留当前名字（通常 `$app-db-secret`）；
        新 primary 默认 `$app-db-secret`，若该名已属于另一个数据库/引擎则用
        `$app-$engine-db-secret`；secondary postgres 用
        `$app-postgres-$purpose_dns-db-secret`。同名不同 source 必须 STOP，不覆盖旧连接。
        primary 的消费引用保持不变；新增 consumer 只供对应 DB 的 workload/migration 显式引用。
      - `{{vault_remote_key}}` 是共享 owner 路径去掉 `secret/`：MySQL primary 用
        `{env}/rds/application/{database_app}/database`，PostgreSQL primary 用
        `{env}/rds/application/{database_app}/postgres`，secondary 只把末段改为
        `$postgres_vault_key`。consumer 名绝不改变 app-slug、`spec.app` 或 Vault owner。
      - 将 claim 与 consumer 写到准确 app overlay 中不冲突的文件（如
        `$database_claim_name.yaml`、`$consumer_secret_name.yaml`），加入该 overlay 的
        `kustomization.yaml` resources；其余填槽取已核验的 namespace 与 `$target.vault_css`。
      **engine=mariadb → STOP 产 Ops Todo**（无对应 Composition）。
      不要为这些 target 生成 app-owned RDS，也不要 StatefulSet+PVC 自托管（#30）。
    - `$unsupported_targets[]`：`$target.cloud != aws`。本 workflow 只生成 AWS RDS Crossplane
      资源；对这些 target 产 Ops Todo，不能把 AWS ARN/ProviderConfig 套到 TKE 或 GCP。
    - `$managed_targets[]`：**cloud=aws** 且非 shared-middleware 集群 target（真 prod
      `*-eks-prod`/`*-prod-data`；tech-service 不在此列）或明确 AWS legacy/迁移例外，
      继续 Step 2 之后的 app-owned RDS 流程。
  - shared target 的数据库版本由共享实例 owner 管理，claim 不接受 engineVersion/db_name/username。
    不用 app-owned MySQL 版本、成本规格或 IAM/SG 门槛拦截共享 claim。

[validate]
  - `$engine ∈ {postgres, mysql, mariadb}`
  - 以下 engine version / db_name / username 检查仅用于非空 `$managed_targets[]`；共享 target
    执行 shared-middleware README 的 claim/能力/首次供给门槛，不输入 app-owned 实例参数。
  - 对 app-owned target，`$engine_version` 是具体版本字符串（不许 "latest" 或空）
  - 如果 `$managed_targets[]` 非空且 `$engine == mysql`：
    - **默认策略门禁**：RDS 单实例 MySQL Community 默认不选。若该 mysql 需求落在 `$managed_targets[]`
      （app-owned 单实例，非 shared-middleware `kind:Database` 自助），必须先确认 cd-requirements.md
      有显式 override 理由（如 legacy 迁移 / 明确低成本单实例场景 + 审批人 @）；没有理由 → STOP，
      建议改走 `add-aurora.md`（aurora-mysql，默认 `8.0.mysql_aurora.3.10.3`）。
      （shared-middleware 的 `kind:Database{engine:mysql}` 自助不受此门禁影响。）
    - `$engine_version` 必须匹配
      `rds.yaml -> engine_contracts.mysql.allowed_engine_version_regex`（当前只允许 `8.4.x` LTS）
    - `8.0.x` 一律 STOP；不得因为旧 manifest / 旧示例仍使用 8.0 就继续复制
    - 默认使用 `default_engine_version`；使用其它 8.4 minor 时，必须在
      `cd-requirements.md` 记录理由，并对每个 managed target region 运行
      `aws rds describe-db-engine-versions --engine mysql --engine-version <version>`
      验证可用，否则 STOP
  - 对 app-owned target，`$db_name` 匹配 `^[a-z][a-z0-9_]*$`（不能含 hyphen，RDS 拒收且创建后 immutable）
  - 对 app-owned target，`$username` 匹配 `^[a-z][a-z0-9_]*$`（不能含 hyphen，RDS 拒收）
  - `$purpose` 非空时：
    - `$engine` 必须等于 `postgres`，且 target 必须走 shared-middleware `kind: Database`；
      mysql、redis、mariadb 或 app-owned RDS 出现 purpose 都 STOP
    - 匹配 `^[a-z][a-z0-9_]*$` 且长度为 2–20
    - `$database_app = $app.replace('-', '_')` 保持 owner identity；
      `len($database_app + "_" + $purpose) <= 63`
    - `$purpose_dns = $purpose.replace('_', '-')`，
      `$postgres_vault_key = "postgres-" + $purpose_dns`
    - 输入只允许 declarative purpose；host、admin/administrator、path/vaultPath、owner、
      privilege/privileges 或其它底层连接/权限字段一律 STOP
    - 对每个 purpose target 执行 `references/shared-middleware/README.md` 的 PostgreSQL
      purpose 目标能力 gate，确认 served API 与实际 Composition；失败则 STOP 该 target
  - 对每个已写入的 shared target，确认 namespace 内 ExternalSecret 和 target Secret
    均不与其它连接重名；已有 primary 的 source 和消费引用不变。
  - 执行下方「shared SQL 容器消费连接信息」，不能在 shared-only 返回时跳过应用注入。
  - shared claim 的 producer 身份必须通过
    [producer identity gate](../references/shared-middleware/README.md#producer-identity-gate)：
    使用准确 target context 的 fresh all-namespaces DatabaseList，不能只检查本仓 claim。
    可先写候选，但没有准确库存时记录阻塞部署的 pending，不能宣称 ready 或提交首次
    Application 注册；该库存无需包含本次尚未同步的 claim。
  - 对每个改动的 shared app overlay 创建独立临时目录：
    `shared_render_dir="$(mktemp -d)"`，再执行
    `kustomize build k8s/overlays/{$target.env_keyword}/ > "$shared_render_dir/manifest.yaml"`。
    render 成功且已取得准确完整库存后，执行
    `bash "$skill_root/validators/validate.sh" --repo-context app --database-inventory "$target_database_inventory" "$shared_render_dir"`，验证继承 base
    和应用 patch 后的最终资源，不能只验证 raw overlay。库存暂不可得时，只运行
    `bash "$skill_root/validators/validate.sh" --repo-context app "$shared_render_dir"` 做候选本地校验，
    同时保留阻塞部署的 inventory pending；不要执行空路径库存命令，也不能把本地 PASS 当作
    跨仓门禁通过。此时只可交付待补证据的候选，不得注册 Application 或合并/同步部署；
    取得准确库存后必须补跑完整门禁。实际执行的 render/validator 均须退出 0；缺依赖、渲染失败
    或 validator 非零仍 STOP，不允许因 shared-only 提前返回而跳过。
  - 如果 `$managed_targets[]` 为空：完成共享 claim/消费配置的 Git 候选与校验，或输出具体
    能力缺口 Ops Todo 后结束；不要继续 Step 2。候选生成与 live 验收分开，不能把缺少 live
    producer 证据的发布标成完成；已有消费者迁移必须在 producer 就绪后再切换路径。

[output]
  - 内存变量；后续 Step 2 起只处理 `$managed_targets[]`
  - shared target 的 claim/consumer/Kustomization 文件列表、逐 overlay 渲染与验证结果；
    具体能力缺口或 pending live producer/consumer 验收单独列入 Ops Todo。

### shared SQL 容器消费连接信息

从准确 target 的最终 render 确定 `$workload_kind`、`$workload_name` 和应用主容器
`$container_name`，只在该 overlay 给这个容器接入 `$consumer_secret_name`。可复用
add-redis Step 1「容器消费连接信息」的按名字定位/安全追加方式，将所需 keys 改为
`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`：无 prefix、非 optional 的 envFrom
放在其它导入之后，或四个显式同名 secretKeyRef；不能覆盖其它容器/已有 env 数组。
已有合法引用不重复添加。不同连接已有 DB_* 来源时不静默覆盖；purpose/secondary
需要独立应用消费映射，缺少该 contract 时 STOP，不把新增 Secret 当作接入完成。
同一 render 内 consumer ExternalSecret wave=1 时，给 workload **metadata.annotations**
设置 `argocd.argoproj.io/sync-wave: "2"`；已有更高 wave 保留。若 consumer 使用其它
wave，则取 `max(原 workload wave, consumer wave + 1)`。不能只改 pod-template annotation，
也不能让依赖 Secret 的 wave 0 workload 阻止后面的 consumer 被同步。

再次 render 后执行，必须退出 0；只证明消费引用，不代替首次同步后的 Secret Ready：

```bash
python3 "$skill_root/validators/check_workload_secret.py" "$shared_render_dir/manifest.yaml" \
  --namespace "$target_namespace" --kind "$workload_kind" --workload "$workload_name" \
  --container "$container_name" --secret "$consumer_secret_name" \
  --key DB_HOST --key DB_PORT --key DB_USER --key DB_PASSWORD
```

`$target_namespace` 来自当前 `$target.namespace`；多连接使用独立映射时必须逐项核对
实际 env 名与 Secret key，用 `--key AUDIT_DB_HOST=DB_HOST` 等逐项检查显式映射，
不能假称上面的默认 DB_* 检查覆盖了自定义映射。

## Step 2. 从 cost-tiering 解析每个 target 规格

[precondition]
  - Step 1 完成

[action]
  - 打开 `references/cost-tiering/rds.yaml -> engine_contracts`
  - 如果 `$engine == mysql`：
    - 读取 `engine_contracts.mysql`，记录 `$mysql_engine_contract`
    - 设置 `$engine_family_fields` 为以下两行，值必须来自 contract，不得凭印象填写：
      ```yaml
          parameterGroupName: default.mysql8.4
          optionGroupName: default:mysql-8-4
      ```
  - 如果 `$engine != mysql`：设置 `$engine_family_fields=""`；对应引擎的非默认
    parameter / option group 不属于本 workflow，需求出现时 STOP 交平台评审
  - 对每个 `$managed_targets[]` 中的 `$target`：
    - 打开 `references/cost-tiering/rds.yaml`，按 `$target.env`（dev/staging/pre/prod）取这一行
    - 原样复制字段：
        `instanceClass`、`allocatedStorage_gb`、`multiAZ`、`backupRetentionPeriod_days`、
        `storageEncrypted`、`deletionProtection`、`skipFinalSnapshot`、`managementPolicies`
      （`instanceClass` 默认 t4g 家族；**高资源占用改 r8g/m8g，由用户在 cd-requirements.md 指定**，
      不自动升档）
    - 如果 env == prod，**额外**读 `references/cost-tiering/_global.yaml -> prod_self_check`，
      每条让用户显式确认，任何 "no" → STOP
    - 读 `rds.yaml -> immutable`。对每个 immutable 字段：如果这个实例以后要去 prod，
      创建时就按 prod 值设；否则按当前 env 值（事后改要走 delete-and-rebuild）
  - 对每个 `$managed_targets[]` 中的 `$target` 还要解析：
    - `$target.region`               从 `clusters.yaml cluster.aws_region`（完整 AWS region 字符串，如 us-east-1 / cn-northwest-1）；**禁止**从短形式凭印象推导
    - `$target.account_id`           从 `clusters.yaml cluster.account_id`
    - `$target.partition`            从 `clusters.yaml cluster.partition`（`aws` 或 `aws-cn`）
    - `$target.namespace`            从 `env-keywords.yaml env_keywords[<keyword>].namespace_pattern`
    - `$target.cluster_dir`          从 `clusters.yaml cluster.argocd_apps_dir`（同名 crossplane-infra 子目录）
    - `$target.vault_css`            从 `clusters.yaml cluster.vault_css`
    - `$target.rds_security_group`   **查找方式**：看同集群其它 app 应用仓 overlay 的 RDS Instance YAML（`k8s/overlays/<env>/rds-instance.yaml`）复制 `vpcSecurityGroupIds`。如该集群找不到可复用的 app RDS SG：STOP + 产 ops-todo "ops 在集群 {{cluster}} 通过平台 GitOps 声明 RDS security group，并在需要放行共享/跨账号访问时用 crossplane-infra 管理 SecurityGroupIngressRule"。这是平台级网络资源，开发者不要直接改控制台 / route / SG。

[validate]
  - 每个 `$managed_targets[]` target 的上述字段都填了
  - mysql 的 `$engine_version` 与 `$engine_family_fields` 必须同时满足
    `engine_contracts.mysql`；版本、parameter group、option group 任一不一致都 STOP
  - prod target 的 immutable 字段都按 prod-tier 设置
  - prod target 的 `managementPolicies` 必须恰好是
    `["Observe","Create","Update","LateInitialize"]`，不得包含 `Delete` 或 `*`
  - **cd-requirements.md 列的 instanceClass / allocatedStorage_gb / multiAZ / storageEncrypted 必须等于 cost-tiering/rds.yaml `$target.env` 同档值**
    - 不等于 → STOP，问用户：是 cd-requirements 写错了（应按 cost-tiering），还是真的要 override？
    - 真要 override → 用户在 cd-requirements.md 显式留 override 原因（"业务期望 db.r6g.large 因为 X Y Z"+ 月度成本 + 审批人 @）
    - 没原因 → 强制用 cost-tiering 值（成本治理硬约束）

[output]
  - `$targets[]` 完整解析

## Step 3. 计算 Vault 路径

[precondition]
  - Step 2 完成

[action]
  - 对每个 `$target`，调用 `references/vault-paths/resolver.md` resolver，输入：
      `env=$target.env, kind=rds, app=$app, key=master-password`
    记录 `$target.vault_master_password_path`
  - 同样输入 `key=database`，记录 `$target.vault_connection_path`
  - 同时记录两个 ESO mount-relative key：
    - `$target.vault_master_password_remote_key`：去掉 `$target.vault_master_password_path` 开头的 `secret/`
    - `$target.vault_connection_remote_key`：去掉 `$target.vault_connection_path` 开头的 `secret/`
    PushSecret writer 的 `remoteRef.remoteKey` 与 ExternalSecret reader 的 `remoteRef.key` 都必须使用
    相对 key，因为 ClusterSecretStore 已挂 `path=secret`

[validate]
  - 每个路径匹配 vault-paths/rules.yaml Rule 2（pattern `secret/{env}/rds/application/<app>/<key>`）

[output]
  - 每 target 两个完整 Vault 路径及两个 ESO mount-relative key

## Step 4. 写 Crossplane RolePolicy（**按集群一次**，不按 target）

[precondition]
  - Step 3 完成

[action]
  - 先确保 `crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml` 里已有
    `crossplane-app-{$app}` Role + `ClusterProviderConfig {$app}`；没有就先用
    `recipes/crossplane/app-providerconfig.yaml.tmpl` 创建/append。
  - 如果 `crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml` 已包含 `crossplane-app-{$app}-rds` RolePolicy，**跳过**。否则：
  - 打开 `recipes/crossplane/rds-rolepolicy.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{partition}}=$target.partition`、
      `{{region}}=$target.region`、`{{account_id}}=$target.account_id`
  - append 或创建 `crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml`
  - **每个 target 集群跑一次**（RolePolicy 是 cluster-scoped）

[validate]
  - 文件存在于目标集群的 crossplane-infra 子目录
  - 同文件或同目录已有 `ClusterProviderConfig {$app}`，否则 RDS Instance 的 `providerConfigRef.name: {$app}` 会失败
  - YAML 可解析

[output]
  - crossplane-infra/{$target.cluster_dir}/{$app}-iam.yaml 更新

## Step 5. 写 Crossplane RDS Instance（按 target 循环）

[precondition]
  - Step 4 完成

[action]
  - 打开 `recipes/crossplane/rds-instance.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{env_keyword}}=$target.env_keyword`、
      `{{engine}}=$engine`、`{{engine_version}}=$engine_version`、
      `{{engine_family_fields}}=$engine_family_fields`、
      `{{db_name}}=$db_name`、
      `{{instance_class}}=$target.instanceClass`、`{{allocated_storage}}=$target.allocatedStorage_gb`、
      `{{username}}=$username`、`{{multi_az}}=$target.multiAZ`、
      `{{storage_encrypted}}=$target.storageEncrypted`、
      `{{deletion_protection}}=$target.deletionProtection`、
      `{{skip_final_snapshot}}=$target.skipFinalSnapshot`、
      `{{backup_retention}}=$target.backupRetentionPeriod_days`、
      `{{region}}=$target.region`、`{{rds_security_group}}=$target.rds_security_group`、
      `{{namespace}}=$target.namespace`、`{{management_policies}}=$target.managementPolicies`、
      `{{final_snapshot_identifier_line}}=<prod 时为 4 空格 + finalSnapshotIdentifier: {$app}-{$target.env_keyword}-final-<YYYY-MM-DD>；非 prod 留空>`。
    模板已经承载 prod orphan-safe 差异；不要在渲染后手改 `managementPolicies` 或插入字段。
  - 写到应用仓库 `k8s/overlays/{$target.env_keyword}/rds-instance.yaml`，并加入 overlay
    `kustomization.yaml` 的 `resources`。**app-owned RDS Instance CR 放应用仓 overlay，不放
    crossplane-infra**（crossplane-infra README / CI 契约：业务应用自身的云资源由应用仓管理，该仓只收
    IAM / IRSA / ProviderConfig / WAF/IPSet / 共享 SG 入站规则等权限边界资源；且
    `.m.upbound.io` CR 是 namespaced 的，与密码链 Secret 同 namespace 同仓最自然）。
    crossplane-infra 当前数据面 grandfather 仅 Harbor S3，不作为 DB 模板。

[validate]
  - 文件存在
  - YAML 可解析；`kustomize build k8s/overlays/{$target.env_keyword}/` 成功
  - RDS Instance `metadata.namespace` 等于 `$target.namespace`（kustomization `namespace:` 注入或模板槽填写一致）
  - mysql Instance 的 `engineVersion`、`parameterGroupName`、`optionGroupName` 与
    `rds.yaml -> engine_contracts.mysql` 完全一致；8.0.x 直接失败
  - `passwordSecretRef` 和 `writeConnectionSecretToRef` 不带 `namespace` 子字段；它们跟 RDS CR 使用同一 namespace

[output]
  - k8s/overlays/{$target.env_keyword}/rds-instance.yaml

## Step 6. 写 Password Generator + ExternalSecret + PushSecret（按 target 循环）

[precondition]
  - Step 5 完成

[action]
  - 用 `recipes/crossplane/rds-password-generator.yaml.tmpl` 写第一段
  - 用 `recipes/crossplane/rds-password-external-secret.yaml.tmpl` 写第二段
  - 用 `recipes/crossplane/rds-password-push-secret.yaml.tmpl` 写第三段
  - generator / external-secret 两段只填 `{{app}}=$app`、`{{namespace}}=$target.namespace`
    （这两个模板正文**没有** `{{env}}` / `{{cluster_secret_store}}` 槽，别去找）
  - 第三段 push-secret 额外填 `{{env}}=$target.env`、`{{cluster_secret_store}}=$target.vault_css`；
    渲染后的 `remoteKey` 必须等于 `$target.vault_master_password_remote_key`，不得带字面 `secret/`
  - 全部写到 `k8s/overlays/{$target.env_keyword}/` 的应用仓库内（**不**进 crossplane-infra/，
    因为这些是 K8s namespaced 资源，跟 app 同生命周期），三个文件：
      `rds-password-generator.yaml`、`rds-password-external-secret.yaml`、`rds-password-push-secret.yaml`
  - 保留模板内 ArgoCD sync-wave：
      Password `-5` → generator ExternalSecret `-4` → password PushSecret `-3`
    这让 password Secret 先于后续 RDS consumers 准备好。
  - 加到 `k8s/overlays/{$target.env_keyword}/kustomization.yaml` 的 resources 列表（或建 infra/ 子目录）

[validate]
  - 三个文件存在
  - YAML 可解析
  - `kustomize build k8s/overlays/{$target.env_keyword}/` 成功
  - `python3 "$skill_root/validators/check_vault_paths.py" <dir>` PASS

[output]
  - k8s/overlays/{$target.env_keyword}/rds-password-{generator,external-secret,push-secret}.yaml

## Step 7. 写连接信息 PushSecret（按 target 循环）

[precondition]
  - Step 6 完成

[action]
  - 用 `recipes/crossplane/rds-conn-push-secret.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{env}}=$target.env`、
      `{{namespace}}=$target.namespace`、`{{cluster_secret_store}}=$target.vault_css`
  - 写到 `k8s/overlays/{$target.env_keyword}/rds-conn-push.yaml`
  - 保留模板内 `argocd.argoproj.io/sync-wave: "0"`，让连接信息 PushSecret 早于 app DB ExternalSecret。
  - 渲染后的所有 `remoteKey` 必须等于 `$target.vault_connection_remote_key`，不得带字面 `secret/`
  - 加到 kustomization resources 列表

[validate]
  - 文件存在
  - YAML 可解析
  - `kustomize build` 成功
  - `python3 "$skill_root/validators/check_vault_paths.py" <dir>` PASS

[output]
  - k8s/overlays/{$target.env_keyword}/rds-conn-push.yaml

## Step 8. 写 app 的 ExternalSecret（按 target 循环）

[precondition]
  - Step 7 完成

[action]
  - 用 `recipes/k8s/db-external-secret.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{namespace}}=$target.namespace`、
      `{{cluster_secret_store}}=$target.vault_css`、
      `{{vault_remote_key}}=$target.vault_connection_remote_key`
  - 保留模板内 `argocd.argoproj.io/sync-wave: "1"`，避免 app DB Secret 早于连接信息入 Vault。
  - 写到 `k8s/overlays/{$target.env_keyword}/db-external-secret.yaml`
  - 加到 kustomization resources 列表

[validate]
  - 文件存在
  - YAML 可解析
  - `kustomize build` 成功

[output]
  - k8s/overlays/{$target.env_keyword}/db-external-secret.yaml

## Step 9. 把 envFrom 接到 Rollout

按 target 循环；已有正确引用时保留，但仍检查同步顺序。

[precondition]
  - Step 8 完成

[action]
  - 对准确 target overlay 执行 Step 1「shared SQL 容器消费连接信息」的精确 workload /
    main container 注入与 wave 检查，`$consumer_secret_name={$app}-db-secret`；该段
    只处理消费方式，app-owned producer/ExternalSecret 仍使用本流程 Steps 5–8。
    已有引用不重复添加，不把 per-target DB 引用写入其它未申请 DB 的 target。
  - 将 workload metadata 的 sync-wave 设为 consumer 之后（标准 consumer=1，
    workload 至少 2），保留已有更高波次。既有服务保留 workload/Service/replicas，
    不删除资源或暂停服务；只有全新服务的父流程可使用首次 migration 初始化模式。

[validate]
  - 每个准确 target 的最终 render 通过 `check_workload_secret.py`，包括四个 DB_*
    keys 和 workload/ExternalSecret 波次关系；其它 target 不新增该连接依赖。

[output]
  - 准确 target 的容器引用/同步波次 patch；新服务 migration 初始化另由父流程分两次 MR

## Step 10. GitOps 同步顺序（同仓 sync-wave 门槛）

[precondition]
  - Step 5-9 的 app overlay 文件（RDS Instance + 密码链 + conn PushSecret + app ExternalSecret + consumer 引用）已生成，计划随同一个 app MR 提交。
  - `$delivery_phase` 默认 `runtime`；只有 new-service Step 10 显式传入时才为
    `candidate`。runtime 还要求该 app MR 和独立平台前置 MR 已按授权合并同步。

[action]
  - 任何合并/同步前先对完整 app overlay 和本轮独立平台候选运行 Step 11 的离线
    validator 命令；不必等待 Step 10 的 live 结果才能执行这些命令。失败仍 STOP。
    未获得合并/同步授权时交付具体 MR 和本步骤 pending，不把 runtime 默认值当作授权。
  - `candidate`：检查渲染后的密码链/Instance/consumer 的 namespace、Secret 引用和
    下述 sync-wave 顺序；在父流程 `$pending_runtime_checks[]` 登记本 Step 10 的
    target、Instance 和 Secret 名及 live 验收条件，继续 Steps 11–13 返回候选。
    不运行本次新资源的 live 检查，不要求首次 Application 已存在，也不把此分支标为
    RDS Ready。首次同步后由 new-service Step 11.5 回到本步骤执行 `runtime`。
  - RDS 资源链本身放在同一 overlay/MR。顺序由 ArgoCD sync-wave 保证：
    Password Generator `-5` → generator ExternalSecret `-4` → password PushSecret `-3` →
    RDS Instance / connection PushSecret（wave 0）→ app ExternalSecret（wave 1）→
    workload（至少 wave 2）。新服务还声明首次 migration 时，父 new-service 仍须先
    初始化 DB/Secret、再以第二次 MR 加 PreSync/workload，不能把整个应用压回同一次同步。
    ExternalSecret 在 Secret 同步成功前不 Healthy，ArgoCD 不会提前进入下一 wave。
  - 首次 sync 后仍要人工确认：目标 namespace 的 `<app>-rds-password` Secret 存在且包含
    `data.password`，RDS Instance 才会正常 create。如果 Instance 在 password Secret 就绪前
    被手工 sync / 单独 apply，RDS 首次异步 create 读到空 / 缺失 password，可能进入需要
    人工清理的 `AsyncCreateFailure` 状态——不要绕过 wave 顺序单点同步 Instance。

[validate]
  - candidate：渲染的顺序/引用通过，待验收记录完整；本次缺少 live Secret 不作为 candidate 失败。
  - runtime：`kubectl -n <namespace> get secret <app>-rds-password -o json | jq '.data.password != null'` 返回 `true`。
  - runtime：RDS Instance Ready/Synced，`<app>-rds-conn` 包含 host/port/username/password，
    连接 PushSecret 与 app DB ExternalSecret Ready、`<app>-db-secret` 的预期 keys 存在，
    再继续 migration/NineData；不打印 Secret 值。未满足仍 STOP，不能用 candidate 结果关闭验收。

[output]
  - 同步顺序确认记录到 MR 描述或部署记录

## Step 11. 全量 validator

[precondition]
  - Step 10 当前阶段完成（candidate 已登记 pending live 验收，或 runtime 已通过）

[action]
  - `bash "$skill_root/validators/validate.sh" k8s/`
  - 每个改动的集群跑 `bash "$skill_root/validators/validate.sh" crossplane-infra/{$target.cluster_dir}/`

[validate]
  - 全部退出 0
  - 特别：`python3 "$skill_root/validators/check_vault_paths.py" <dir>` PASS（业务凭据无 `secret/cicd/*`），`python3 "$skill_root/validators/check_eso_pushsecret_bug.py" <dir>` PASS（PushSecret 都显式设 updatePolicy）

[output]
  - validator 日志给用户

## Step 12. 更新 cd-requirements.md + cicd.md

[precondition]
  - Step 11 通过

[action]
  - 在 `docs/deployment/cd-requirements.md` 的 Secrets 表填两行刚建好的 Vault 路径：
      `secret/{env}/rds/application/{$app}/master-password`
        populated-by=Crossplane Generator+PushSecret, consumed-by=Crossplane Instance.passwordSecretRef
      `secret/{env}/rds/application/{$app}/database`
        populated-by=Crossplane PushSecret, consumed-by=app via ExternalSecret
  - 在 `docs/deployment/cicd.md` append 一段记 RDS provisioning：A1 链路、不人工写 password、immutable 字段锁定

[validate]
  - 两个文档都更新；无残留 `{{...}}`

[output]
  - 更新后的文档

## Step 13. 出 summary + Ops Todo

[precondition]
  - Step 12 完成

[action]
  - 整理 Ops Todo：
    - prod RDS：snapshot 策略核查（实例后续删除时 final snapshot 是否到位）
    - 多集群：跨 region Vault 读权限（ESO ClusterSecretStore 已覆盖；除非 CSS 没装）
    - Step 2 用 fallback Ops Todo 的 `$target.rds_security_group`：那条 todo 仍未关，且必须由运维在平台 GitOps 仓执行；不要把 SG / route 变更塞进应用仓 MR
  - 用 `recipes/docs/ops-todo-table.md`
  - 用 `recipes/docs/summary-card.md` 出最终摘要
  - candidate 摘要明确“RDS 候选已验证，运行态待 new-service Step 11.5 验收”；保留
    pending 记录。standalone/runtime 未通过 Step 10 时不得报告 RDS 就绪。

[validate]
  - summary 已打印

[output]
  - 最终用户消息

## 出口

用户拿到：
  - `crossplane-infra/{cluster_dir}/{$app}-iam.yaml`（每 target 集群；仅 IAM RolePolicy + ProviderConfig）
  - `k8s/overlays/{env_keyword}/rds-instance.yaml` + `rds-password-{generator,external-secret,push-secret}.yaml` + `rds-conn-push.yaml` + `db-external-secret.yaml`
  - 准确 target overlay 的 workload 消费引用与同步波次 patch
  - `cd-requirements.md` + `cicd.md` 更新

ArgoCD 同步后：
  - Crossplane 创建 RDS 实例
  - Password generator + PushSecret 把 master-password 写进 Vault
  - Crossplane 写 7-key `<app>-rds-conn` Secret
  - PushSecret 把 host/port/username/password 推到 database Vault 路径
  - app 的 ExternalSecret 渲染 `<app>-db-secret`
  - Rollout 拉到 DB_* env vars

**已知运维点**（workflow 不自动处理）：

如果 PushSecret 在首次 sync 后卡在 `secret key endpoint does not exist`：
跑 `kubectl delete pushsecret {$app}-rds-push`，让 ArgoCD selfHeal 重建。
这是 Crossplane <-> ESO 首次 provision 时已知的竞态，详细诊断走
`troubleshooting/pushsecret-stuck-endpoint-does-not-exist.md`。

## 后续动作（建议）

RDS 建好后让开发者自助 SQL 查询 / DDL 不再开运维 ticket，**强烈建议** 接着跑：

- `workflows/add-ninedata-datasource.md` —— 把 RDS 注册到 ninedata.addx.live
  （**仅海外集群**：CN 集群暂无 NineData license，跳过本步）
