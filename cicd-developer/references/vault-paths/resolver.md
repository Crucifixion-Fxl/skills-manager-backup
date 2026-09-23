---
name: vault-path
description: 确定性 resolver—— 给 (env, kind, app, key)，返回唯一规范的 Vault 路径。规则表、platform 枚举、regex pattern 都在 `references/vault-paths/rules.yaml` + `vault-paths/platforms.yaml`（跟 validator 共享单一数据源）。本文件只讲怎么 **用** resolver。
---

# Recipe：vault-path resolver

## 单一数据源

Vault 路径规则、platform 枚举、regex pattern、forbidden pattern、示例都在：

```
references/vault-paths/rules.yaml         7 条 rule + forbidden_patterns + examples
references/vault-paths/platforms.yaml     16 platform 枚举 + typical_keys
references/vault-paths/cross-app-credential.yaml   跨 app 共享方案
references/vault-paths/instances.yaml              Vault 实例清单
references/data/clusters.yaml                      目标集群 vault_css
```

`validators/check_vault_paths.py` 只校验 Vault 路径，消费 `rules.yaml` / `platforms.yaml`。
workflow 渲染 ExternalSecret / PushSecret 的 `ClusterSecretStore` 时，消费
`references/data/clusters.yaml -> vault_css`。

resolver 返回的是以 `secret/` 开头的**完整规范 Vault 路径**。写 ESO manifest 时，
ExternalSecret `remoteRef.key` / `extract.key` 和 PushSecret / ClusterPushSecret
`remoteRef.remoteKey` 都要去掉这个前缀，使用相对 ClusterSecretStore `path=secret` 的 key。
否则会解析成 `secret/secret/...`，并被 admission 或 validator 拒绝。

改路径规则或加 platform 只改 `rules.yaml` / `platforms.yaml`——recipe 和 validator 自动跟上。

## 输入

| 输入 | 类型 | 示例 |
|---|---|---|
| `env` | enum: `dev` / `staging` / `pre` / `prod`（**裸 env，首段绝不带 region**）| `staging` |
| `kind` | `cicd-tooling` / vault-paths/platforms.yaml 里 platforms 的某个 key / `app` / `legacy` | `rds` |
| `app` | kebab-case，匹配 GitLab repo 名 | `support-api` |
| `key` | 密钥 key 后缀（小写、dash 分隔）| `database` |

> ⚠️ **首段硬规则**：Vault 路径首段只能是裸 env（`dev`/`staging`/`pre`/`prod`）、`cicd`、或 legacy 的
> `<app>` 名。**`<env>-<region>` 作首段（`secret/staging-us/...`、`secret/prod-eu/...`）一律禁止**——
> staging 三区各连各自的 builder Vault 实例（`vault-{us,eu,cn}.builder.addx.live`），region 由实例隐含，
> 写进路径既冗余又违规。**没有独立的 `shared-middleware` 命名空间**：共享中间件凭据折叠进
> `platform-resource-credential` 的 per-app 路径（`secret/{env}/{platform}/application/{app}/{key}`）。
> `check_vault_paths.py` 会拦下 env-region 首段。（legacy `secret/<app>/<env>-<region>/...`——app 名在
> 第一段、env-region 在第二段——仍被接受，与此禁令不冲突。）

## 怎么解析

1. 打开 `references/vault-paths/rules.yaml`
2. 按 `rules[]` 顺序遍历；对每条 rule 看 `when:` 字段是否命中本次请求
3. **第一条** 命中的 rule 即为答案。用它的 `output_template`，替换：
   - `{env}` → `$env`
   - `{platform}` → `$kind`（仅 `platform-resource-credential` rule；`$kind` 必须是 `vault-paths/platforms.yaml` 里 `platforms:` 的一个 key）
   - `{app}` → `$app`
   - `{key}` → `$key`
   - `{component}` → `cicd-tooling` rule 用，是平台组件名（argocd / casdoor / harbor / ...）
4. 返回生成的路径字符串

任何规则都没命中 → STOP，问用户。**不要** 凭印象拼路径。

## 快速对照（哪种意图走哪条 rule）

| 意图 | rule 名 | 输出模式 |
|---|---|---|
| 平台工具链自身凭据 | `cicd-tooling` | `secret/cicd/<component>/<key>` |
| app 的 DB / 缓存 / 队列 / 第三方代签发凭据 | `platform-resource-credential` | `secret/{env}/<platform>/application/<app>/<key>` |
| app 自己的第三方 API key / webhook secret | `app-owned-business-credential` | `secret/{env}/app/<app>/<key>` |
| 老应用（未注册 gitlab-vault-sync）| `legacy-app-not-registered` 或 `legacy-app-env-region` | `secret/<app>/<env>/...` |
| dvc-remote GCP 存量 app/database 路径 | `legacy-dvc-gcp` | `secret/dvc-remote/staging-us-gcp/{app\|database}` |
| 2026-04-30 前的 RDS master password | `pre-2026-04-30-rds-infra` | `secret/{env}/app/<app>/infra` |

## 关键区分（容易搞错的地方）

- **`mysql` vs `rds`（`platform-resource-credential` 的 platform 选择）** —— `rds` = 云厂托管 + Crossplane 闭环（PushSecret 自动写凭据）；`mysql` = 外部公网 MySQL。workflow 生成 Crossplane RDS YAML 时用 `rds`；第三方 MySQL endpoint 用 `mysql`。`mysql` 只解决路径分类，**不代表允许人工写 Vault 或手工建账号**：必须已有 GitOps owner claim/controller 自动签发连接信息，或直接跨 app 引用同 Vault 域内已有的 GitOps owner 路径。能力不存在、owner 路径不在同一 Vault 域、或需要新建最小权限账号时 STOP + Ops Todo，让平台补可复用高层 claim/Composition。
- **DB / 中间件凭据永不走 `app-owned-business-credential`** —— 哪怕"是我们自己的"也走 `platform-resource-credential` 对应 platform。`app-owned-business-credential` 只给凭据"主人是 app 自己"的（Stripe webhook secret、第三方 API token）。
- **共享中间件凭据也是 per-app，不是共享 root 契约** —— dev/staging 多 app 共用一个实例时，仍在该实例上为每个 app 建独立 `database` + `user` 或连接凭据，凭据落 `secret/{env}/{platform}/application/{app}/{key}`，**没有** `shared-middleware/` 命名空间、**绝不带 region**。组件映射：共享 RDS→`rds`、Redis→`redis`、DocumentDB→`mongodb`、MSK→`kafka`、ScyllaDB→`scylla`、ClickHouse→`clickhouse`。申请流程：RDS/Redis/Postgres 走 `kind: Database`（见 workflows/add-rds.md / add-redis.md）。PostgreSQL primary key 固定为 `postgres`；optional lower_snake purpose 通过 `shared-middleware/README.md` 的目标能力门禁后，只派生 secondary role/database/schema 和 kebab key `postgres-<purpose.replace('_', '-')>`，所以 canonical path 是 `secret/{env}/rds/application/{owner-spec.app}/postgres-<purpose-as-kebab>`，owner app 路径段不变。MSK SCRAM 走 `KafkaScramCredential`（见 references/shared-middleware/README.md，key 固定为 `sasl`，同一路径包含 `username`/`password`/`bootstrap_brokers_sasl_scram`，app ExternalSecret 将 broker 字段映射为 `KAFKA_BROKERS`；`kafka-brokers.yaml` / live MSK status 仅供平台校验）。
- **CI-only vs runtime 不是判据** —— 判据是"凭据主人是谁"，不是"什么时候用"。Runner 拉 Harbor 镜像的凭据 = `cicd-tooling`（cicd/harbor）。CI job 里 app 用的 Sentry upload token = `app-owned-business-credential`（app 自己用，只是触发点在 CI）。

## 平台凭据人员权限

`secret/{env}/{platform}/application/{app}/{key}` 中的 application 表示消费方，
不代表应用人员拥有凭据。所有平台（含 OCI/RDS/Kafka/AWS）的人员读取和维护权限，
由 `DEV/vault-policies` 指定的平台管理员/超级管理员策略授权。
`gitlab-vault-sync` 只管理 `secret/{env}/app/{app}` 的项目角色授权，不生成平台读权限，
不通过 deny 覆盖独立管理员授权。ESO 使用独立机器策略，不能借用应用 owner/developer 策略。

species-correction 路径仍为 `secret/{env}/oci/application/naturehood/species-correction-video-copy`。
不新增 restricted-runtime 分类。US/EU 使用各自 Vault 实例；staging 使用 Builder Vault，
prod 使用 Ops Vault；ESO 引用去掉 `secret/` 前缀。此分类规范依赖同步器修复并实际 reconcile，
仅修改文档或路径校验器不会改变线上 ACL。验收应用身份读取平台值返回 403，管理员和 ESO 仍可读。

## 跨 app 共享凭据

两个 app 共享一个第三方凭据（Stripe webhook secret、公网腾讯 CDB 两个 app 都连等）。先按凭据类型选 owner 路径：业务凭据用 `secret/{env}/app/<owner>/<key>`；DB 等平台资源凭据用 `secret/{env}/<platform>/application/<owner>/<key>`。DB 凭据不能为了复用改放 app 路径。

| 方案 | 描述 | 何时用 |
|---|---|---|
| A（推荐）| 一个 app 作 owner，凭据由 GitOps provisioner 写入该类型的 canonical owner 路径；另一 app 的 ExternalSecret 直接引用 | 同 Vault 域、相同权限契约；单一数据源，轮换一次两端同步 |
| B（legacy only）| 两 app 各有既存路径（同值），由同一 GitOps controller/reconciler 扇出 | 仅存量兼容；不得为新接入人工双写 |

**禁止方案**（workflow 必须拒绝）：见 `vault-paths/rules.yaml -> forbidden_patterns` + `vault-cross-app-credential.yaml -> forbidden`。要点：
- `secret/{env}/app/shared/*` —— 违反"一条路径一个 app 所有权"
- `secret/cicd/<service>/<business-credential>` —— 红线 7a
- 改 `app-*-owner` group policy —— 下次 gitlab-vault-sync reconcile 会覆盖

## 集群 -> ClusterSecretStore

workflow 写普通 ExternalSecret / PushSecret 时，直接查
`references/data/clusters.yaml -> clusters[].vault_css`，不要按 domain 手推。

`vault-backend` 只作为 ops/tech-service 域的默认 Vault store alias。
builder/dev/staging 集群统一使用 `vault-builder-backend`；dev/staging
manifest 不得访问 ops Vault。例外：明确部署到 `cn-k8s` / TKE 的
staging workload 跟随 cn-k8s ops domain，使用 `vault-backend`。
判断 Vault 域只看 `clusters[].vault` / live `ClusterSecretStore.spec.provider.vault.server`，
不要从 `secretStoreRef.name` 反推。

当前普通 app SecretStore 约定：
- ops / tech-service 集群：`vault_css: vault-backend`，实际 server 指向 ops Vault
- cn-k8s / TKE（包括 staging workload）：`vault_css: vault-backend`，实际 server 指向 ops Vault
- cn-dev / cn-staging / us-staging / eu-staging builder 集群：manifest 使用 `vault_css: vault-builder-backend`，实际 server 指向 builder Vault

Sentry DSN 是例外：读 `references/sentry/README.md -> SENTRY_DSN_CSS`，因为它必须和 sentry-onboard Job 写入 DSN 的 Vault 实例一致。

## 校对样本

`references/vault-paths/rules.yaml -> examples:` 列了 (input → output) 对。resolver 跑这张表必须能复现 output 字段，workflow 可以用来自检。

## 强制 validator hook

写完任何 ExternalSecret / PushSecret / Crossplane manifest 引用 Vault 路径之后，workflow **必须** 跑：

```
python3 "$skill_root/validators/check_vault_paths.py" <manifest-dir>
```

如果 `<manifest-dir>` 是 `helm template` / `kustomize build` 写到临时目录的渲染结果，
文件路径已经丢失仓库归属；平台组件引用 `secret/cicd/*` 时必须由调用方显式传入原始
`DEV/k8s` 组件路径：

```bash
python3 "$skill_root/validators/check_vault_paths.py" <rendered-dir> \
  --platform-source <k8s-checkout>/clusters/<cluster>/<platform-component>
```

validator 会同时核对 Git origin 必须是 `gitlab.addx.ai/DEV/k8s`，且 source 必须位于
`clusters/<cluster>/<allowlisted-component>` 或 `cicd/apps/<allowlisted-component>`，checkout
必须有 HEAD，且该 component 至少有一个 tracked file。`--platform-source` 是可信
workflow/CI 注入的 provenance，不能从渲染 YAML 的 namespace、name 或 labels 推导；
它会授权 `<rendered-dir>` 下的全部文件，所以该目录必须是**单个 platform component 的隔离输出**，
不能混入业务 manifest，也不能让被扫描仓库控制这个 CLI 参数。Git origin/path 检查是防误配的
fail-closed sanity check，不是对本机调用者的身份认证；能控制进程和 checkout 的调用者也能改 Git
配置。没有可信调用方和隔离 provenance 时，`secret/cicd/*` 默认拒绝。

非 0 退出 = STOP，把失败路径原文给用户，**不要** 自己修。
