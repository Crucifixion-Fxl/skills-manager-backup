---
name: add-cloudsql
description: 在 GCP 集群上通过 Crossplane 声明式创建 Cloud SQL（物理实例 DatabaseInstance + 逻辑库 Database），API 与 AWS shared-db 的 kind:Database 对齐。物理实例产出连接 secret 全自动（provider 直接写 endpoint/port/username/password），逻辑库自动建 database+user+grant+Vault 密码。仅 GCP，AWS 走 add-rds.md 的 shared-db。
---

# Workflow：add-cloudsql（GCP Cloud SQL 声明式创建）

## 目的

在 **GCP** 集群上用统一入口 `platform.addx.io/v1alpha1` 声明式创建 Cloud SQL，业务方写 Claim，
Crossplane 按 GCP Cloud SQL 底座渲染。末态：

- **物理实例**（`kind: DatabaseInstance`）：GCP Cloud SQL 实例存在（Crossplane 创建），
  **provider 自动产出连接 secret**（`<physName>-pc`，4 key：`endpoint`/`port`/`username`/`password`，
  AWS RDS 同款模型，零手动、零 ESO 组装）。engine 自动判：MySQL→3306/root，PostgreSQL→5432/postgres。
- **逻辑库**（`kind: Database`）：引用物理实例，自动建 per-app `database` + `user` + `grant`，
  app 密码进 Vault `secret/gcp/cloudsql/application/{app}/{engine}`，app 用 ExternalSecret 消费。

> **与 AWS 的关系**：API group 同为 `platform.addx.io`（刻意对齐）。AWS 侧走 `add-rds.md` 里的
> shared-db `kind: Database`（RDS/共享实例底座，已上线更早）。**本 workflow 仅用于 GCP 集群**——
> 集群名形如 `gcp-a4xcloud-tech-service-us-us-tech-service`。AWS target 出现在本 workflow 一律 STOP，
> 路由回 `add-rds.md`。

## 进入条件

- target 是 **GCP 集群**（`gcp-*`）。非 GCP target STOP，路由回 `add-rds.md`（AWS）或产 Ops Todo（腾讯云无 Composition）。
- `docs/deployment/cd-requirements.md` 存在且列了 Cloud SQL 需求。
- 目标 GCP 集群已装 Cloud SQL 平台底座（下列**平台前置**，缺失产 Ops Todo，不在本 workflow 内建）：
  - provider `provider-gcp-cloudsql-safe`（物理，Healthy）+ `provider-sql-safe`（逻辑，Healthy）
  - XRD `databaseinstances.platform.addx.io` + `databases.platform.addx.io`（均 Established=True）
  - Composition `databaseinstance-gcp.platform.addx.io` + `database-gcp.platform.addx.io`
  - Kyverno policy `restrict-raw-gcp-cloudsql-provenance`（provenance 校验 + create/update/delete 保留给 crossplane core + owner GC 放行）
  - ESO compose RBAC `crossplane:cloudsql:compose-eso`（aggregate 到 crossplane core SA）
- **红线（不可协商）**：Provider Delete 恒为 no-op（`deletionPolicy: None` + managementPolicies 无 `Delete`），
  provider GSA **零 delete IAM**（只 `cloudsql.instances.create`）。**永不 import / adopt / migrate / delete 任何既有实例**。
- 逻辑 `kind: Database` claim 必须满足 fleet policy `require-database-snake-case-name`：
  claim 需 `metadata.annotations["platform.addx.io/app-slug"]`（kebab-case，≥2 字符）+
  `spec.app`（同名下划线版，`$app.replace('-', '_')`）。

## Step 1. 读 cd-requirements.md 提取上下文

[precondition]
  - cd-requirements.md 存在且有 Cloud SQL 条目；target 全是 GCP 集群

[action]
  - 解析文档，提取：
    - `$app`            kebab-case
    - `$targets[]`      只含本 Cloud SQL 的 GCP target
    - `$engine`         mysql | postgres
    - `$version`        mysql → `MYSQL_8_4`；postgres → `POSTGRES_16`（pin 具体大版本）
    - `$tier`           仅允许 `db-custom-1-3840`（其它 tier 需 cd-requirements 记录理由）
    - `$db_name`        逻辑库名（snake_case；不填默认 = `spec.app`）
    - `$database_app`   = `$app.replace('-', '_')`（逻辑 claim 的 `spec.app`）

[validate]
  - `$engine ∈ {mysql, postgres}`；其它（mariadb 等）STOP + Ops Todo（无 Composition）
  - `$version` 匹配 engine（mysql→`MYSQL_8_4`，postgres→`POSTGRES_16`）
  - `$db_name` / `$database_app` 匹配 `^[a-z][a-z0-9_]*$`（无 hyphen）
  - 每个 `$target.cluster` 是 `gcp-*`；否则 STOP

[output]
  - 提取出的上下文变量（`$app` / `$engine` / `$version` / `$db_name` / `$database_app` / `$targets[]`），供后续 step 使用

## Step 2. 物理实例 DatabaseInstance（平台/SRE 层）

> 物理实例通常由平台/SRE 预置（一个实例多 app 共享逻辑库），业务方一般只写 Step 3 的逻辑 Database。
> 仅当需要**专属新实例**时走本步。

[precondition]
  - 需要专属新物理实例（否则跳到 Step 3 引用平台预置实例）；Step 1 上下文已就绪

[action]
  - 用 `recipes/crossplane/cloudsql-databaseinstance.yaml.tmpl` 生成 `kind: DatabaseInstance`：
    - `metadata.name` = 实例逻辑名（如 `<app>-<engine>`），`namespace` = 目标 ns
    - `spec.engine` / `spec.version` / `spec.tier=db-custom-1-3840` /
      `spec.diskSizeGb` / `spec.availabilityType=ZONAL` / `spec.defaultShared`
  - **不要**手写 `writeConnectionSecretToRef`——由 Composition 自动注入；provider 自动产出连接 secret。

[validate]
  - 不含任何 delete/adopt/import 语义；不引用既有实例名
  - 连接 secret 由 provider 产出（`<physName>-pc`，4 key），**不手建连接 secret、不手工组装 ESO**

[output]
  - 实例 Ready 后 `<physName>-pc` secret 自动出现（endpoint/port/username/password）

## Step 3. 逻辑库 Database（业务层，主路径）

[precondition]
  - 目标物理实例已 Ready（Step 2 或平台预置），其连接 secret `<physName>-pc` 存在

[action]
  - 用 `recipes/crossplane/cloudsql-database.yaml.tmpl` 生成 `kind: Database`：
    - `metadata.annotations["platform.addx.io/app-slug"]` = `$app`（kebab）
    - `spec.app` = `$database_app`（snake）
    - `spec.engine` = `$engine`
    - `spec.instanceRef.name` = 物理 DatabaseInstance 的 claim 名
    - `spec.databaseName` = `$db_name`（可选，默认 = `spec.app`）
  - Composition 自动：per-instance provider-sql ProviderConfig（sslMode=require）→
    provider-sql `Database` + `Access`（profile application-v1）→ 建 database+user+grant →
    ESO Password + ExternalSecret（app 密码）+ PushSecret（推 Vault
    `secret/gcp/cloudsql/application/{$database_app}/{$engine}`）
  - app 侧用 `recipes/k8s/shared-db-external-secret.yaml.tmpl` 消费同名 Vault 路径（host/port/username/password）

[validate]
  - claim 满足 `require-database-snake-case-name`（app-slug + spec.app 成对）
  - 不写 raw provider-sql `User`/`Grant`，不把业务账号/表名放平台仓
  - **PG 限制**：app 用自有 schema（库级 CREATE 权自建），`public` 在 PG15+ 默认不可写

[output]
  - 逻辑 Database XR 顶层 `Ready=True/Synced=True`（含 database/user/grant/secret 全就绪）
  - GCP 实例里出现 `$db_name` 库 + `$database_app` 用户

## 关键约束与已知坑（实测钉死）

- **连接 secret 全自动只在 Create 时产出**：provider 的 `AdditionalConnectionDetailsFn` 在实例
  **Create** 时写连接 secret（含 root_password）。对**已存在**（Observe 态）的实例切 revision **不会补产**
  连接 secret——要连接 secret 全自动，实例必须是本 provider 新建的（v3.0.21+）。
- **provider-sql ProviderConfig credentials.source**：仅 `PostgreSQLConnectionSecret` / `MySQLConnectionSecret`；
  `connectionSecretRef` 只有 `name`（same-namespace）。
- **sslMode=require**：加密但不验证证书（DSN 无 sslrootcert）→ 可用私网 IP endpoint（GCP PSA 私有 DNS 在 GKE 不可解析，故不能用 verify-full）。
- **PG Access grant-inspect ACL**：查询用 `LEFT JOIN LATERAL aclexplode(d.datacl) acl ON true`——
  **不要用 `COALESCE(datacl, ARRAY[]::aclitem[])` 或 `'{}'::aclitem[]`**：GCP Cloud SQL PG 的 `aclexplode()`
  拒绝任何空 aclitem[] 字面量（报 `ACL arrays must be one-dimensional`），且无 grant 时 datacl 为 NULL，
  COALESCE 空数组 fallback 必炸。（provider-sql-safe v0.15.0-a4x.4 已修）
- **XR 顶层 readiness**：ESO Password generator 和 provider-sql ProviderConfig **不产生 Ready condition**，
  Composition 里必须给这两个 composed 资源加 `gotemplating.fn.crossplane.io/ready: "True"`，否则逻辑 Database
  XR 顶层永远 `Ready=False Creating`（尽管 database/user/grant 都建成）。核对就绪必看 **XR 顶层 Ready**，不能只看 composed。
- **退役 / 删除**：删逻辑 Database claim → 删物理 DatabaseInstance claim（Provider Delete no-op，**不删** GCP 实例）→
  带外 gcloud `--no-deletion-protection` 再 `delete`。owner GC（`kube-system:generic-garbage-collector`）
  必须被 provenance policy 放行，否则删 claim 后底层 managed 对象 orphan、provider 不断重建实例（policy 已修）。
  **GCP 实例删除一律带外手动 gcloud，AI 不代执行**（破坏性、红线）。

## 平台组件版本（GCP US Tech 集群，2026-09 实测）

- `provider-gcp-cloudsql-safe` v3.0.21（digest `sha256:d38d08ee…`）：物理 + 连接 secret 全自动
- `provider-sql-safe` v0.15.0-a4x.4（digest `sha256:53434bc1…`）：逻辑 + Access grant-inspect ACL 修复
- ArgoCD App：platform-api / policy / identity / provider-gcp-cloudsql-safe / provider-sql-safe（全 Synced/Healthy）

## 相关

- AWS 关系型数据库：`add-rds.md`（shared-db `kind: Database`）
- 对象存储（GCS）：`add-object-bucket.md`
- 逻辑库消费 secret：`recipes/k8s/shared-db-external-secret.yaml.tmpl`
