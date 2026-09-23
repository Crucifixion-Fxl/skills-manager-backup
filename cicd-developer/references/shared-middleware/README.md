# shared-middleware

shared-middleware 是平台共享中间件组合，不是普通单 app 工作负载。部署到新
staging 集群时，按现有 staging 集群事实复制对应 region 的资源形态，但仍要遵守下面这些
差异化规则。

## 新部署 staging / tech-service 应用的中间件凭据申请规则

shared-middleware 已上线 **6 个集群**：staging-us/eu/cn（us/eu/cn-eks-staging）+
**tech-us/eu/cn**（002/010/589 tech-service）。在**这 6 个集群**上，新部署应用的数据需求
**只能消费 shared-middleware**，**不允许为单 app 自建数据类中间件**
（RDS / Aurora / ElastiCache Redis / DocumentDB / MSK / Kafka / ClickHouse），
**也不允许用 StatefulSet+PVC 自托管数据库引擎**绕过（hard-rule #30，软约定需在
cd-requirements.md 写明理由）。dev/staging/tech-service 默认复用平台共享实例，由共享实例按资源类型为
每个 app 签发**独立的 per-app database/user 或连接凭据**。

> **prod（prod-us/eu/cn）没有 shared-middleware**：prod 数据库继续用 app-owned 托管
> RDS / Aurora / ElastiCache（cost-tiering 表 prod tier，multiAZ + 加密 + 删除保护），
> 走 `workflows/add-rds.md` / `add-aurora.md` / `add-redis.md`；prod **同样不应**用 StatefulSet
> 自托管数据库。本节的"只能用 shared-middleware"只约束 staging + tech-service 这 6 个集群。

> **凭据约定（2026-06-01 起，取代旧"共享 root 契约"模型）**：不再有单一共享 root 契约。
> 每个 app 拿自己的 per-app 凭据，落 `platform-resource-credential` 的 per-app 路径
> `secret/{env}/{platform}/application/{app}/{key}`（**region-less**；组件→platform 映射见
> `references/vault-paths/platforms.yaml`：RDS→`rds`、Redis→`redis`、DocumentDB→`mongodb`、
> MSK→`kafka`、ClickHouse→`clickhouse`）。MSK 当前固定使用 `key=sasl` 保存
> SCRAM username/password/bootstrap brokers；broker 字段名为
> `bootstrap_brokers_sasl_scram`，值是 AWS 原生 `bootstrapBrokersSaslScram` `:9096`
> endpoint。**禁止** `secret/staging-{region}/shared-middleware/...`
> 这类「env-region 作首段 + shared-middleware 命名空间」路径——`check_vault_paths.py` 会拦下。
> 已存在的 live `staging-{region}/shared-middleware/*`（us/eu/cn 三集群）是**待迁移存量
> （backlog）**，本目录不再新增此形态。

应用仓声明完整的业务消费契约：已上线的 MySQL / Redis / PostgreSQL 写 `kind: Database`，
MSK SCRAM 写 `kind: KafkaScramCredential`，并在同一应用 overlay 中声明 `ExternalSecret`、
env 注入和 Rollout/Service。`k8s` 平台仓只维护共享实例、XRD/Composition/controller 等可复用
能力；`crossplane-infra` 只维护 IAM/IRSA、ProviderConfig 等集中权限边界。即使 composed resource
或 PushSecret 运行在 `crossplane-system`，也不改变业务 claim 的源码归属。

禁止在 `k8s/shared-middleware`、`crossplane-infra` 或其它平台目录为单 app 手写底层
User/Grant/PushSecret、静态 Vault per-app 路径扇出或其它业务实例配置。如果目标引擎尚无可用
Composition，立即 STOP，并产 Ops Todo 让平台补**可复用的自助 Composition**；不要先为当前 app
手工供给，也不要临时创建 app-owned staging 中间件、回退共享 root 或
`staging-{region}/shared-middleware/...` 旧路径。S3 bucket 是否 app-owned 取决于业务对象存储
语义，不按本规则自动归入 shared-middleware。

## 资源组合

典型 shared-middleware（staging + tech-service 6 集群）包含：

- RDS MySQL
- RDS PostgreSQL
- ElastiCache Redis
- DocumentDB
- MSK
- 自管 ClickHouse StatefulSet
- ClickHouse 对应的 office / VPC SG ingress rule

> **S3 不属于 shared-middleware**（2026-06-05 移除）：共享 S3 桶 `{env}-shared-middleware-data`
> 从未被消费、已删除。对象存储一律按业务语义走 **app-owned S3**（`workflows/add-s3-bucket.md`
> + IRSA），不归入 shared-middleware。

## 同 AWS account 多 region 的 IAM 命名

US/EU staging 当前同属一个 AWS account。CN staging 是 AWS China account，但 IAM Role
仍是 account-global 资源。Role 不能只按 app 名命名，否则同账号多 region 或后续
同账号扩展会争用同一个 Role。

跨 region 复用 account 时，Crossplane app role 必须带 region/env 后缀：

| 环境 | Role 名 |
|---|---|
| staging-us | `crossplane-app-shared-middleware-us-staging` |
| staging-eu | `crossplane-app-shared-middleware-eu-staging` |
| staging-cn | `crossplane-app-shared-middleware-cn-staging` |

对应 `ClusterProviderConfig` 仍可在各自集群内叫 `shared-middleware`，因为它是集群内
Kubernetes 资源，不是 AWS account-global 资源。CN staging 使用 `arn:aws-cn` partition，
不要复用普通 AWS partition 的 ARN 模板。

## 共享 SG rule 不重复声明

RDS / Redis / DocumentDB / MSK 使用集群已有共享 SG 和预置 ingress rule。不要在
shared-middleware overlay 里重复声明这些端口的 `SecurityGroupIngressRule`。

如果重复声明，AWS 会返回：

```text
InvalidPermission.Duplicate: the specified rule ... already exists
```

然后 Crossplane SG rule CR `Ready=False`，ArgoCD Application 被汇总成 `Degraded`。

shared-middleware overlay 里只保留 app-owned 暴露面，例如 ClickHouse 的 HTTP/native
office/VPC ingress rule。

如果已经创建了重复 CR 且 Application `automated.prune=false`，Git 删除 manifest 后
还要手动删除 live CR；详见
`troubleshooting/securitygroupingressrule-duplicate.md`。

## ClickHouse 镜像和调度

ClickHouse recipe 已强制 `nodeSelector: kubernetes.io/arch: amd64`。部署到有 arm64
节点池的 staging 集群时不要移除这条规则，否则可能出现：

```text
exec /entrypoint.sh: exec format error
```

## ServiceLinkedRole 前置

MSK / RDS 首次在账号内创建时可能依赖 AWS service-linked role：

- `AWSServiceRoleForKafka`
- `AWSServiceRoleForRDS`

如果 Crossplane create 报 service-linked role 缺失，先按最小权限创建对应 SLR，再
重新观察资源；不要把空 external-name 或错误 ARN 写入 Git 来绕过 provider 状态。

## RDS external-name 回填

RDS Instance 创建完成后，如果 provider 写回的 external-name 与 Git 不一致，GitOps
后续可能触发 replace/reconcile 风险。完成态应把真实 DB identifier 回填到 Git：

```yaml
metadata:
  annotations:
    crossplane.io/external-name: <db-identifier>
```

回填后 hard refresh Application，确认 RDS `SYNCED=True READY=True`。

## 凭据申请流程（kind: Database）

> **`kind:Database` 只在 6 个 shared-middleware 集群可消费**（`{us,eu,cn}-eks-staging` +
> `{us,eu,cn}-eks-tech-service`；判别按 **cluster 名**，不是 `$target.env`——tech-service 的
> env-keyword `env=prod`，与真 prod 集群同 env）。`spec.env` 只决定 **vault 路径段 + store**，不决定
> 集群是否有 shared-middleware：tech-service 应用写 `env: prod`（路由到 tech-service 的共享实例 +
> ops vault），staging 应用写 `env: staging`。**真 prod 集群（`*-eks-prod`/`*-prod-data`）没有 shared
> 实例、也没部署 Composition** → 那里的数据库走 app-owned 托管 RDS/Aurora，不要在真 prod 写 kind:Database。

### ✅ 共享 MySQL + Redis + PostgreSQL —— 已上线（全 6 集群）

`engine=mysql` / `engine=redis` / `engine=postgres` 的共享中间件自助**已 live + 验证**：us/eu/cn-staging + tech-us/eu/cn（mysql/redis 2026-06-03；**postgres 2026-06-04**，6 实例已建+激活+端到端验证）。
开发者在**自己 app 的 namespace** 写一个统一 `kind: Database`（engine 区分），Composition
（function-go-templating + provider-sql/provider-kubernetes）自助供给并把连接信息推到 vault per-app
路径，app 自己写 ExternalSecret 消费。`Database` claim 和 ExternalSecret 都必须提交到应用仓
overlay；平台仓只维护通用 Composition。**平台不再为单 app 手加 yaml、不再 STOP 产凭据
Ops Todo、不再 configmap 硬编码 endpoint。**

```yaml
apiVersion: platform.addx.io/v1alpha1
kind: Database
metadata:
  name: order-service-api-mysql
  namespace: staging-order-service-api
  annotations:
    platform.addx.io/app-slug: order-service-api
spec:
  app: order_service_api             # app-slug 中的 '-' 机械替换为 '_'
  env: staging                       # dev|staging|pre|prod;决定 vault 路径段 + store
  engine: mysql                      # mysql | redis | postgres
```

新 claim 必须从 `recipes/k8s/shared-database-claim.yaml.tmpl` 生成，不手填 `spec.app`：

- `platform.addx.io/app-slug` 是 canonical kebab-case 应用名，长度 2–31；
- `spec.app = app-slug.replace('-', '_')`，也就是 lower_snake_case 的物理库名/库用户/Vault 路径段；
- 标准 `{phase}-{app}` namespace 中，`app-slug` 必须等于 namespace 去掉 phase 前缀后的部分；
- Kyverno 只在 `CREATE` 时 Enforce。没有 `app-slug` annotation 的存量 claim 继续 grandfather，
  不批量改名，也不因普通 UPDATE 被拦截。
- primary claim 的 `purpose_field` 留空，`database_claim_name` 保持 `<app>-<engine>`。
- 同一个 owner 需要独立 PostgreSQL role/database 时，才把 `purpose_field` 渲染为
  `  purpose: <purpose>`，并把 `database_claim_name` 渲染为
  `<app>-postgres-<purpose-as-kebab>`。`purpose` 必须匹配 `^[a-z][a-z0-9_]*$`、长度 2–20，
  且派生的 `<spec.app>_<purpose>` 不超过 63 字符；mysql/redis 不接受 purpose。
- secondary claim 仍保留 owner app-slug、owner `spec.app` 和 owner namespace。禁止用
  `<app>-<purpose>` / `<app>_<purpose>` 冒充 owner，也禁止提交 host、admin/administrator、
  path/vaultPath、owner、privilege/privileges 等底层字段。

例如 `staging-order-service-api` 只能创建 `app-slug: order-service-api`、
`spec.app: order_service_api`；禁止新建 `orderserviceapi`。单词应用（如 `coord`）仍写 `coord`。

<a id="producer-identity-gate"></a>
### Producer 身份冲突门禁

新建 claim、增加 purpose/additional user 或修改 claim 身份时，必须在准确目标集群检查
完整 claim 库存。不同 consumer Secret 名称不代表 producer 隔离：当前 Composition 的
`<app>_<purpose>` 拼接存在歧义，例如 `billing_api` 的 `audit` purpose 与
`billing_api_audit` 的 primary 会管理相同 PostgreSQL role/database、source Secret 和 PushSecret。
保留现有外部名称；禁止直接改名或删除既有 claim 来“修复”冲突。

1. 核对该 target 的 live Composition 和 claim 实际选用的 CompositionRevision 的身份
   派生逻辑，必须与本节描述的当前 `database.platform.addx.io` 合同一致；不能只看最新
   Composition 对象或 provider 版本。未知/未同步版本、Manual 锁定或 revision selector
   不得按当前规则推断身份；由平台先核实并完成受审升级/迁移。普通 Automatic claim 的
   controller-generated revisionRef 不作为能力已就绪的证明。
   渲染一个准确 app target 到独立、扁平目录，只放这个 target 的最终 YAML。
2. 由拥有该目标只读权限的操作者从 collection API 导出所有 namespace 的 Database claims：

   ```bash
   kubectl --context "$target_context" --request-timeout=30s get \
     --raw /apis/platform.addx.io/v1alpha1/databases \
     > "$target_database_inventory"
   bash "$skill_root/validators/validate.sh" --repo-context app \
     --database-inventory "$target_database_inventory" "$target_render_dir"
   ```

   普通 `kubectl get ... --all-namespaces -o json` 可能包装成丢失 resourceVersion 的 `v1/List`；
   使用上述无 namespace/filter/limit 的 API 路径保留原始 DatabaseList metadata。
   变量必须绑定当前 catalog target/context；不得用默认 context、单 namespace、label selector、
   手写空清单或旧缓存代替。保留 API 返回的 List metadata 和所有 claim metadata/spec；
   不读取 Secret。清单只作受限的临时证据，不提交 Git/MR。缺权限或无法取得完整清单时，
   候选可保留但标 pending；由平台取得准确库存并完成检查后才能宣布可交付部署。
3. 必须拒绝候选内部和候选对库存的外部 database/user/role、source Secret、PushSecret、
   Vault 路径冲突。同一个 namespace/name 的既有 claim 只允许保留全部既有 producer 身份的
   非身份修改或追加账号；engine、app、env、purpose 变更及删除附加账号转受审迁移，不原地替换。
   `additionalUsers[].name` 保留 `database`、`postgres` 和 `postgres_*`，避免当前/后续
   PostgreSQL purpose 与 SQL 主账号路径碰撞，不能改 consumer 路径绕过。
4. 在提交最终部署候选与实际同步前，对最终 render 和新取库存重跑。多个仓库同时申请时，
   平台按准确目标协调顺序并重新检查；有尚未入库存的并行候选，必须纳入同一检查集合，
   无法确认时保持 pending，不并发放行冲突身份。

不传 `--database-inventory` 的普通扫描仅检查单份候选与同目录资源，不证明跨仓/跨 namespace
唯一性。库存模式验证的是传入快照，不能证明 context、时效或并发原子性；当前平台没有由本
skill 新增的全局原子身份分配器。这是部署前保护，不能宣称所有独立操作者已受 admission 强制。
若已存在冲突，停止此次写入，由平台评估连接、数据和回收归属后制定迁移。

发生什么：
- **engine=mysql**：Composition 在集群共享 MySQL（ProviderConfig `shared-mysql`）上 `CREATE DATABASE
  <spec.app>` + `CREATE USER <spec.app>` + `GRANT`（仅本库,受控权限集,无 `*.*`）；凭据 PushSecret 到 vault
  **`secret/{env}/rds/application/{spec.app}/database`**,4 key `host`/`port`/`username`/`password`。
  无 size 档位、无独立 `kind:Schema/DatabaseUser`、不依赖 provider-ninedata。需要区别于 owner 的
  同库最小权限账号时，在同一个 claim 使用 `spec.additionalUsers`；具体账号、表名和权限属于业务
  desired state，必须留在应用仓，平台仓只维护通用 Composition。
- **engine=redis**：共享 ElastiCache 是**无 auth 单节点**（无 authToken/TLS/RBAC,纯 SG+VPC 隔离）→
  **无 per-app 凭据**;Composition 只把共享 endpoint PushSecret 到 vault
  **`secret/{env}/redis/application/{spec.app}/connection`**,2 key `host`/`port`。**非真隔离**(真隔离需把实例
  切 RBAC 模式 + per-app ACL user,是破坏性变更,另议);app 用 key 前缀（如 `pe:`）软隔离。
- **engine=postgres**：Composition 在集群共享 PostgreSQL（ProviderConfig `shared-postgres`）上建 per-app
  `Role`(login,自动生成密码) + `Database`(**归 master `shared_mw` 所有,不是 app role**——若 role 拥有库,
  删 claim 时 `DROP DATABASE`(master 非 owner、RDS 无 superuser)与 `DROP ROLE`(role 拥有库)互相死锁) +
  `Grant`(库级 CONNECT/CREATE/TEMPORARY);凭据 PushSecret 到 vault
  **`secret/{env}/rds/application/{spec.app}/postgres`**（末段 key 是 `postgres`,不是 mysql 的 `database`;连接
  secret 也用 `{spec.app}-postgres-db-cred` 区分,使同一 app 可同时持 mysql+postgres claim）,4 key
  `host`/`port`/`username`/`password`。primary claim 仍由 app 靠库级 CREATE 权建并使用
  **自己的 schema**（migration 工具指定）；默认 `public` schema 在 PG15+ **不可写**。
  Optional `spec.purpose` 在下述目标能力 gate 通过后，为同一 owner 派生独立
  `<spec.app>_<purpose>` role/database，并由 Composition 通过该 app 自身凭据创建同名
  app-owned `Schema`，默认 `"$user", public` search path 因此可用于未指定 schema 的 migration。
  数据库仍归 shared master，不能给 master 增加 tenant role membership，也不开放 public schema。
  credentials 不另造 owner path，而是写
  **`secret/{env}/rds/application/{spec.app}/postgres-<purpose-as-kebab>`**。不填 purpose 的
  primary identity 和 `.../postgres` 路径完全不变。删除 purpose claim 时 Schema 外部对象
  随 master-owned Database 的删除级联回收；不要因 Schema CR 使用 Orphan 而保留整个数据库。
- store 由 env 决定:dev/staging→`vault-builder-backend`,pre/prod→`vault-backend`。
- **app 侧自己写 `ExternalSecret`**（`secretStoreRef.name` = 集群 `vault_css`,`remoteRef.key` = 上面的
  per-app 路径,相对路径不带 `secret/` 前缀）注入工作负载。**app namespace 不会有静态 K8s secret,只由
  ExternalSecret 创建。** db/user/路径段 = `spec.app`。回收:删 `kind:Database` → 组合资源(provider-sql
  DROP / Object)+ PushSecret 删;**vault 路径不自动清**（ESO jwt 无 vault delete 权限,deletionPolicy=None
  避免删 claim 时 403 卡死）——残留由平台/同名 claim 覆盖。

#### PostgreSQL purpose 的目标能力 gate

primary PostgreSQL 已有六集群契约，不代表 purpose Schema 能力在所有目标均已部署。
平台源 `DEV/k8s` 的 `cicd/base/default/shared-db-compositions/composition-database.yaml`
在 `a7835aee3d58ca108961bbf9220ce92e79ff3bda` 增加了 purpose Schema；
当前源快照 `1882119f9bd620b70342ac82b3e7126276e402b7` 中，US/EU staging 使用
provider-sql v0.11.0，CN staging 与三套 tech-service 仍声明 v0.9.0。
这是 Git desired state 对照，不能代替目标 live 验证。

生成新的 purpose claim 前，逐 target 只读确认：

1. live `databases.platform.addx.io` 的 served `v1alpha1` schema 包含字符串 `spec.purpose`；
2. `schemas.postgresql.sql.crossplane.io` 的 `v1alpha1` 已 served，provider-sql 已 Healthy；
3. 该 claim 将选用的 live Composition/CompositionRevision 包含 purpose 专用
   `schema-provider-config` 和 `schema` 两个 composed resources，并与已审查的平台版本一致。

任一项缺失、版本未同步或证据不可读，STOP 该 purpose target 并产 Ops Todo，让平台先完成
升级/同步。禁止用 app-owned raw Schema/ProviderConfig、master SQL Job、public-schema grant，
或取消 purpose 来绕过。primary claim 不因这个 purpose 专属 gate 被拦截。

#### MySQL 附加最小权限账号

写入前先由运维执行以下 gate，输出必须严格为 `array`：

```bash
kubectl --context <target-context> get crd databases.platform.addx.io \
  -o jsonpath='{.spec.versions[?(@.name=="v1alpha1")].schema.openAPIV3Schema.properties.spec.properties.additionalUsers.type}{"\n"}'
```

空输出或非 `array` 时 **STOP**，等待 shared Database 平台能力 rollout；不能改写成应用仓 raw
provider-sql `User/Grant`，也不能把业务表名放进 `DEV/k8s` shared-middleware overlay。

```yaml
spec:
  app: vip
  env: staging
  engine: mysql
  additionalUsers:
    - name: flink_cdc              # 实际用户名固定派生为 vip_flink_cdc
      maxUserConnections: 50       # 1..200，默认 50
      grants:
        - table: payment
          privileges: [INSERT, UPDATE, DELETE]
        - table: product
          privileges: [SELECT]
```

硬边界：数据库固定为 `spec.app`；用户名固定为 `{spec.app}_{name}` 且最长 32；表必须是小写字面 MySQL
identifier；权限只允许 `SELECT/INSERT/UPDATE/DELETE`，禁止 wildcard、DDL、routine、global grant；
同一账号不能重复表。凭据固定推到
`secret/{env}/rds/application/{spec.app}/{name-dns}`，4 key 仍是
`host/port/username/password`。首个账号按下方首次供给门槛验证 User、每个 Grant、PushSecret、Vault
字段相等和事务回滚式权限预检。

迁移既有附加 MySQL 账号时，平台 XRD/Composition 已支持 optional
`additionalUsers[].passwordSecretRef: {name, key}`：只引用 **Database claim 同 namespace**
中已有的凭据 Secret，禁止指定 namespace 或把密码内联到 claim。使用前还需确认 live CRD
包含该字段、当前 Composition 将引用绑定到 claim namespace，且引用 Secret 的 GitOps owner
已就绪；旧目标若不支持则 STOP 等平台 rollout，不能依赖 API 静默丢弃字段后生成新密码。
该选项仅保留迁移账号密码，不放宽 username/database/table/privilege/Vault 路径边界。

SQL 消费端必须用 `recipes/k8s/shared-db-external-secret.yaml.tmpl`：shared Composition 的 Vault
property 固定是小写 `host`/`port`/`username`/`password`，模板把它们映射成应用常用的
`DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`。`db-external-secret.yaml.tmpl` 只适用于
app-owned RDS 的大写 Vault property，不能混用。应用若需要 JDBC/DSN，可在 shared 模板的
`target.template.data` 中基于这四个 `DB_*` 输入组装；若还要保留 raw key，同时设置
`mergePolicy: Merge`。不要把 endpoint 或凭据写进 ConfigMap。

`{{consumer_secret_name}}` 显式决定 ExternalSecret 的 `metadata.name` 与 `spec.target.name`，
它与 claim owner 分开，按每个目标 namespace 的现有资源核对：

- 已有 primary：保留其当前 ExternalSecret/target Secret 身份和消费引用，通常为
  `<app>-db-secret`；不得为套新命名批量改名。若现有 ExternalSecret 与 target Secret
  名不同，保留已审查的原 manifest，不用这个同名模板覆盖。
- 新 primary：默认 `<app>-db-secret`；若该名称已属于另一个数据库或引擎，新增 consumer
  使用 `<app>-<engine>-db-secret`，不修改既有 consumer。
- secondary PostgreSQL：`<app>-postgres-<purpose-dns>-db-secret`，Vault 末段仍为
  `postgres-<purpose-dns>`。MySQL additional user 使用 `<app>-mysql-<user-dns>-db-secret`。
- 写入前检查 ExternalSecret 名和 target Secret 名都不与其它连接重复；同名但不同 Vault
  source 的对象必须 STOP，不能覆盖。新增 consumer 只供对应数据库的 workload/migration
  显式引用，不替换 primary 的 envFrom/secretKeyRef，也不把多份同名 `DB_*` envFrom 混入同一容器。

以上命名不改变 canonical app-slug、claim `spec.app`、namespace owner 或 Vault owner 路径。
Redis 用通用 `recipes/k8s/external-secret.yaml.tmpl`，consumer 为 `<app>-redis-secret`，
只把小写 Vault `host`/`port` 映射为 `REDIS_HOST`/`REDIS_PORT`；不读取 SQL 的 username/password。

共享分支即使没有任何 app-owned target，也必须把新 claim/ExternalSecret 加入准确 overlay 的
Kustomization。每 target 用 `shared_render_dir="$(mktemp -d)"` 创建独立临时目录，执行
`kustomize build k8s/overlays/<env-keyword>/ > "$shared_render_dir/manifest.yaml"`；成功后运行
`bash "$skill_root/validators/validate.sh" --repo-context app "$shared_render_dir"`，验证继承 base
和应用 patch 后的最终资源，不能只扫描 raw overlay。
任一非零结果即 STOP，不能跳到 app-owned 后续步骤或以“无需 IAM 变更”为由省略验证。
通过后交付文件与验证结果，并区分 Git 候选完成和仍待 live 供给/消费验收的状态。

#### 首次供给验收硬门槛

`Database Ready=True`、`XDatabase Ready=True`、provider-kubernetes `Object Ready=True` 和 ArgoCD
`Synced/Healthy` **都不能单独证明连接凭据已经写入 Vault**。Object 的 Ready 只证明 observed manifest
可管理；其 `.status.atProvider.manifest` 内的 PushSecret 仍可能 `Ready=False`。

Git 候选可以在同一应用 overlay 中生成 claim 和对应 ExternalSecret；生成 manifest 不等于
连接已可用。首次供给只有同时取得以下证据才可验收完成，缺一项则 STOP 后续发布/验收，
保留实际 pending/failure 状态，禁止人工 seed 或硬编码 endpoint。迁移已有消费者时，必须先
取得这些 producer 证据，再让消费者的 ExternalSecret 切换到新路径；不能用同一次自动同步
把尚未就绪的新凭据替换到运行中的消费者。

1. claim 和 XR 均 `Ready=True`、`Synced=True`；
2. MySQL/Postgres 的 database/user（或 role）/grant composed resources 均 Ready；
   purpose PostgreSQL 的 Schema 也必须 Ready（schema ProviderConfig 本身没有 Ready condition）；
3. XDatabase 的 Object observed manifest PushSecret `Ready=True`；
4. 直接查询对应 `crossplane-system` PushSecret 也 `Ready=True`，名称按下表，不能统一查询
   `<app>-db-push`；
5. Vault 目标路径有 `custom_metadata.managed-by=external-secrets`，字段名与契约一致，并与 generated
   connection Secret 做不回显值的逐字段相等比较。

下表的 `app-dns = spec.app.replace('_', '-')`，`purpose-dns` 和 `user-dns` 同样由下划线
转连字符；Vault owner 路径仍使用原始 `spec.app`。

| 分支 | PushSecret 名 | source Secret 名 |
|---|---|---|
| MySQL primary | `<app-dns>-db-push` | `<app-dns>-db-cred` |
| MySQL additional user | `<app-dns>-<user-dns>-db-push` | `<app-dns>-<user-dns>-db-cred` |
| Redis | `<app-dns>-redis-push` | `shared-redis-conn` |
| PostgreSQL primary | `<app-dns>-postgres-push` | `<app-dns>-postgres-db-cred` |
| PostgreSQL purpose | `<app-dns>-<purpose-dns>-postgres-push` | `<app-dns>-<purpose-dns>-postgres-db-cred` |

相等比较按 producer 映射进行：SQL source 的 `endpoint` 对 Vault `host`，Redis source 的
`address` 对 Vault `host`；`port` 及 SQL 的 `username/password` 保持同名。验收记录只保存
字段存在/相等的布尔结果，不回显 Secret 或 Vault 值。

迁移场景若标准 platform 路径已被人工 seed，PushSecret 会报
`secret not managed by external-secrets`。不要手工伪造 ownership metadata，也不要直接覆盖：先把完整源
连接用 KV v2 `cas=0` 备份到 app-owned 路径，GitOps repoint 全部旧消费者并验证；再 soft-delete 标准路径的
current version，让 PushSecret 首次创建新 current version 并取得 ownership。完整步骤见
`troubleshooting/shared-database-pushsecret-unmanaged-vault-path.md`。

### ✅ 共享 MSK(Kafka) SCRAM 凭据 —— 已上线（全 6 集群）

共享 MSK 本身已部署到 6 个 shared-middleware 集群，并已切到
`SASL_SSL + SCRAM-SHA-512`。应用在自己的 namespace 写
`platform.addx.io/v1alpha1 kind: KafkaScramCredential`，Composition 会在该集群的共享
MSK + shared SCRAM CMK 上自动生成 per-app SCRAM credential：

```yaml
apiVersion: platform.addx.io/v1alpha1
kind: KafkaScramCredential
metadata:
  name: <app>-kafka-scram
  namespace: <app-ns>
spec:
  app: <app>                       # ^[a-z][a-z0-9-]{1,40}$, 可带连字符
  env: staging                     # dev|staging|pre|prod；决定 vault store/path 段
  region: us-east-1                # 当前集群共享 MSK 所在 AWS region
```

发生什么：
- ESO 生成 40 位 SCRAM password。
- AWS Secrets Manager 创建 `AmazonMSK_<env>-<app>` secret（使用 shared MSK SCRAM CMK）。
- `ScramSecretAssociation` 绑定到带 `addx.io/shared-msk=true` 的共享 MSK。
- PushSecret 写 Vault **`secret/{env}/kafka/application/{app}/sasl`**，3 key：
  `username`/`password`/`bootstrap_brokers_sasl_scram`。Composition 通过
  function-go-templating `ExtraResources` 读取共享 MSK
  `.status.atProvider.bootstrapBrokersSaslScram`，再由 PushSecret template 合并进
  Vault，不改动 CreatedOnce 的 SCRAM password Secret。

app 侧自己写 `ExternalSecret` 读取 `.../sasl`，把 `KAFKA_USERNAME` /
`KAFKA_PASSWORD` / `KAFKA_BROKERS` 注入工作负载。`KAFKA_BROKERS` 的
`remoteRef.property` 写 `bootstrap_brokers_sasl_scram`。不要使用 `addx.live`
CNAME，避免 Kafka client TLS hostname 校验失败。

开发者发现路径：开发者只需要看应用仓 ExternalSecret / env 映射，或 Vault
`secret/{env}/kafka/application/{app}/sasl` 的三个字段；不要求、也通常没有
tech-service / Crossplane kubectl 权限。`kafka-brokers.yaml` 和 live Crossplane status 是
cicd-developer / 平台维护者校验、排查、刷新 Composition 输入事实的路径，不是开发者自助步骤。
平台侧只读查询示例：

6 个 shared-middleware 集群的当前 broker YAML 清单在
[`kafka-brokers.yaml`](kafka-brokers.yaml)。该文件是给 cicd-developer / 平台维护者
对照 Composition 推入 Vault 的 broker 值用的缓存；live Crossplane status 仍是最终来源。
如果 live status 与 YAML 不一致，先用 live status 生成 skill 更新 MR，再排查
`KafkaScramCredential` reconcile / PushSecret 是否把新值写入 Vault。

```bash
kubectl --context <ctx> -n crossplane-system \
  get clusters.kafka.aws.m.upbound.io <shared-msk-name> \
  -o jsonpath='{.status.atProvider.bootstrapBrokersSaslScram}'
```

6 个 shared-middleware 集群的 `<shared-msk-name>` 约定为
`staging-us-shared-msk` / `staging-eu-shared-msk` / `staging-cn-shared-msk` /
`tech-us-shared-msk` / `tech-eu-shared-msk` / `tech-cn-shared-msk`。如果 Vault 路径、
PushSecret status 或应用 ExternalSecret 映射里缺 `bootstrap_brokers_sasl_scram`，STOP 并让平台
排查 `KafkaScramCredential` Composition、Crossplane ExtraResources RBAC、共享 MSK status 和 PushSecret；不要让开发者凭旧
`addx.live` 域名或历史应用配置猜地址，也不要把下面的 kubectl 命令当作开发者自助步骤。

> ⚠️ Kafka **topic / ACL / consumer group 治理 workflow 仍未实现**：
> `routes-build.yaml` 的 `add-kafka-topic` 仍是 planned。当前共享 MSK 开启
> `auto.create.topics.enable=true`，普通 smoke / 低治理接入可由首次 produce/consume 自动建 topic；
> 需要精确分区、retention、ACL 或显式 topic 声明时仍产 Ops Todo 给平台补治理。

> ⚠️ **仍 STOP + Ops Todo（共享实例已部署、消费 Composition 尚未落地）**：
> **ClickHouse / DocumentDB(MongoDB)** —— 这 2 类的**共享实例本身已于 2026-06-04
> 部署到全 6 集群**（staging + tech-service 各有 shared ClickHouse StatefulSet /
> shared DocumentDB，全 Synced/Healthy/私有），但 `kind:Database` **自助消费 Composition 还没写**，
> 所以仍 STOP，Ops Todo 内容是"补可复用 kind:Database 消费层 + 自动 per-app 凭据交付"，
> **不是**"再建共享实例"，也不是在平台仓为当前 app 手写底层资源。
> **Aurora** 与 **mariadb 引擎的 RDS** 连共享实例都还没有，同样 STOP 产 Ops Todo。
> 遇到这些仍按下方"统一 kind:Database 分流"目标 STOP；**不要**回退到共享 root 或
> `staging-{region}/shared-middleware/...` 旧路径，**不要**为单 app 自建实例，**也不要**用 StatefulSet 自托管。

### 终态设计（其它引擎/特性的目标形态,尚未落地）

统一声明、Composition 按 `env`/`engine` 分流（设计依据
`k8s/docs/plans/2026-04-17-infra-modernization-meeting.md` 维度 1/2）。后续给 Redis/Aurora/
ClickHouse 等补 Composition 时，沿用同一 `kind: Database` 入口 + per-app vault 路径
`secret/{env}/{platform}/application/{app}/{key}`（component 映射：RDS→rds、Redis→redis、
DocumentDB→mongodb、ClickHouse→clickhouse）；MSK 凭据已走独立 `KafkaScramCredential`
入口，topic / ACL 治理仍待 workflow；`env=prod`/`isolation=required` 走独立实例
（现有 `workflows/add-rds.md` / `add-aurora.md` 路径）。`size` 档位/`kind:Schema`/ninedata 多租户
授权属更后续，未实现。

> ⚠️ **连接信息默认走 vault，Kafka bootstrap broker 也走同一路径**：RDS / Redis / PostgreSQL 等共享
> 中间件的 host/port/credential 走 vault per-app 路径、消费者 ExternalSecret 读。MSK 当前
> `KafkaScramCredential` 写 `username`/`password`/`bootstrap_brokers_sasl_scram` 到
> `secret/{env}/kafka/application/{app}/sasl`；app ExternalSecret 将 broker 字段映射成
> `KAFKA_BROKERS`。**不要**把 Kafka endpoint 写成 `addx.live` CNAME。地址事实仍可用
> [`kafka-brokers.yaml`](kafka-brokers.yaml) 或 live Crossplane status 校验，但不再要求
> 业务配置携带 Kafka endpoint。
> **mysql/redis/postgres 已有 Composition → 写 `kind:Database`(engine:mysql|redis|postgres)自动把 endpoint 推 vault,
> claim 提交后无需平台 per-app 手工动作**（不要再手 PushSecret、更不要 configmap）。尚无 Composition 的中间件(Aurora/
> ClickHouse/DocumentDB)必须 STOP，等待平台补可复用的 Composition 和自动凭据交付；**禁止**由平台
> 用单个 PushSecret 静态枚举 app 并扇出 per-app 路径，也禁止为当前 app 手写 User/Grant/PushSecret。
> 允许放业务 ConfigMap / 应用配置的例外只有：纯应用配置（如飞书 APP_ID 这类
> 非连接信息的非敏感字面量，见 `references/vault-paths/platforms.yaml`）。

### per-app database/schema 边界

新 staging 应用需要写库或跑 schema migration 时，应用仓必须声明 `kind: Database`；Composition
负责在共享实例上创建 database/schema + user/grant，并把 per-app 凭据（含明确的 DB 名/授权边界）
push 到 `secret/{env}/{platform}/application/{app}/{key}`。如果目标引擎尚无 Composition，或输出只含
host/user/password、没有明确 DB 名或授权边界，应用 workflow 必须 STOP，并产 Ops Todo 修复通用
Composition；不能在平台仓手写当前 app 的资源，也不能临时创建 app-owned staging RDS 绕过。

> MSK 当前是 SCRAM-only listener（`SASL_SSL` / `SCRAM-SHA-512`，`bootstrapBrokersSaslScram`
> `:9096`）；per-app SCRAM 凭据通过 `KafkaScramCredential` 写入
> `secret/{env}/kafka/application/{app}/sasl`，同一路径包含 `bootstrap_brokers_sasl_scram`。
>
> 历史参考：CN staging 旧的 `shared-middleware-e2e-staging-cn` 烟测 Application 已于 2026-06-03
> **整体退役删除**（`DEV/k8s!813`），不再有 e2e 租户消费 `staging-cn/shared-middleware/*` 旧 root
> 契约。新接入一律按上文 per-app vault 路径，**不要照抄已删 e2e 的旧 root 形态**。
