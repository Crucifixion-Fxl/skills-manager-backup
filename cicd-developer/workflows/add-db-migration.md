---
name: add-db-migration
description: 给现有服务加 DB schema migration（建表 / 加列 / 改索引等）。走 ArgoCD PreSync hook Job 模式：失败必须阻断整个部署，新版本 Pod 不会启动。
---

# Workflow：add-db-migration

## CN 迁移能力门禁

当前 CN 路由见 `references/cn-tencent-migration.md`：prod 为 `cn-k8s`（100014919455），
staging/tech-service 为 `cn-tke-staging` / `cn-tke-tech-service`（100052802231）。
AWS CN 已退役。下文 AWS shared-middleware 的已上线证明不能覆盖新 TKE；生成 `Database` /
`KafkaScramCredential` 前必须核验精确目标的 served API、Composition/ProviderConfig、共享实例、
Vault writer/reader 和 per-app 凭据交付，缺任一证据则 STOP + Ops Todo。消费策略仍适用，
不得回退到旧 AWS broker/ARN、共享 root 或 app-owned/self-hosted staging 数据库。
AWS 托管资源 recipe 仅用于 `cloud=aws`；腾讯云请求转对应原生能力或 Ops Todo。

## 目的

给微服务加数据库 schema 升级流程：

- **PreSync hook Job** 在主资源 sync **之前** 跑 migration
- migration 二进制必须**幂等**（Job 可能重试）
- 任何 migration 失败 → Job 退出码非 0 → ArgoCD sync 失败 → 新 Pod 不启动
- 成功后 Job 保留 24h 便于排查日志

跟应用启动时跑 migration 的方式区别：
- 应用启动跑：多副本竞争 + 失败回滚难 + log 混在应用 log 里 + 新 Pod 启动失败不会阻断旧版本
- PreSync hook：单实例顺序跑 + 失败明确（Job 状态）+ log 隔离 + ArgoCD sync 卡住 → 自动告警 → 部署链整体停（hard rule #16 强制）

## 进入条件

- 应用已有数据库连接来源：
  - 非 shared-middleware 集群（真 prod `*-eks-prod` / dev）/ legacy：已有 app-owned RDS / Aurora
    （跑过 `workflows/add-rds.md` 或 `workflows/add-aurora.md`）
  - 新部署 **shared-middleware 集群**（当前 US/EU EKS staging/tech-service + CN TKE staging/tech-service；
    **按 cluster 名判别非 env**——tech-service env=prod，见 hard-rule #29）：仅对 AWS US/EU 已支持且满足对应前置条件的目标，
    或已完整通过上述 CN TKE 能力门禁的目标，才可通过 `kind:Database` 申请 per-app database/user；
    CN TKE 缺任一能力证据仍 STOP + Ops Todo，不生成该目标的 claim/凭据流程。开发者在自己 app ns 用 `recipes/k8s/shared-database-claim.yaml.tmpl`
    生成 `platform.addx.io/v1alpha1 kind: Database`：canonical kebab `app-slug` 写 annotation，
    `spec.app = app-slug.replace('-', '_')`（即 `$database_app = $app.replace('-', '_')`），
    禁止手填拼接式多词名；再填 `spec.env/spec.engine`
    （已上线的 mysql/redis/postgres 自助：**无 size 档位、无
    isolation flag**——size/isolation 是 README 标注「终态未落地」的设计，别写）。per-app 凭据落 region-less
    路径（`secret/{env}/{platform}/application/{database_app}/{key}`，platform 映射：RDS MySQL/PostgreSQL→rds、
    ElastiCache/Redis→redis、DocumentDB→mongodb、ScyllaDB→scylla、ClickHouse→clickhouse；MSK
    SCRAM 凭据走 KafkaScramCredential，不属于 DB migration），app 通过
    ExternalSecret 读取（`secretStoreRef = cluster.vault_css`，须与该目标 producer 的实例/路径合同一致；
    AWS staging 为 `vault-builder-backend`，新 CN staging 为 `vault-backend`，不能仅按 env 推断），须提供 `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME`。
- 应用代码已经有 migration 工具（Go / Java / Python / Rust binary），且主应用 image 内包含该 binary；如使用独立 migrate image，必须先补 CI build target 和 ArgoCD Image Updater alias
- 该 binary 符合 5 条契约（见 Step 1 验证）

⚠️ **READINESS GATE（按引擎与目标分流）**：engine=mysql / postgres / redis 的 `kind:Database` 自助已在 AWS US/EU staging + tech-service 上线。
**只有 AWS US/EU 已支持且满足对应前置条件的目标，或已完整通过上述 CN TKE 能力门禁的目标，才能继续消费 per-app 凭据；CN TKE 缺任一能力证据仍 STOP + Ops Todo。**
这里的“继续”不豁免下文的 Secret 字段、migration binary 与同步时序检查。
**engine=aurora / mariadb / documentdb / scylla / clickhouse**：kind:Database 消费 Composition 未落地 → STOP 产 Ops Todo（其中 documentdb/
clickhouse 共享实例已部署、仅消费层未落地），**不要**手搓 shared-root 或 `staging-{region}/shared-middleware/` 路径。

## Step 1. 验证 migration binary 契约

[precondition]
  - 应用已经写了 migration binary

[action]
  - 跟开发者确认 binary 满足 5 条契约：
    1. **退出码语义**：成功 0，失败非 0（K8s Job 用退出码判成败）
    2. **幂等**：重复跑同一个 migration 必须安全（Job 可能 backoffLimit retry，也可能人工 retrigger sync）
    3. **DB 连接信息读取顺序**：先 `DATABASE_URL`，否则从 `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` 拼接
    4. **URL escape 密码**：Vault 自动生成的密码可能含 `:` `@` `/` `?` `#` 等 URL 保留字符，**裸 `fmt.Sprintf` 会被 URL parser 误解析**
       - Go：必须用 `url.UserPassword(user, pass).String()`
       - Python：必须 `urllib.parse.quote(password, safe='')`
       - 其他语言同理
    5. **日志**：每条 migration 名 + 开始/完成时间到 stdout（ArgoCD UI → Job → Logs 是唯一排查信息源）
  - 不满足 → STOP，让开发者修 binary 再回来

[validate]
  - 5 条契约都满足（开发者口头确认 + 代码 review）

[output]
  - 内存变量

## Step 2. 解析 DB 连接信息来源

[precondition]
  - Step 1 完成

[action]
  - 看应用现有的 DB Secret：
    - **模式 A**：单 Secret 含所有字段（业务 secret + DB 凭据都在 `<app>-secret`）—— PreSync Job envFrom 引用同一个 Secret 即可
    - **模式 B**：拆两个 Secret（`<app>-app-secret` 业务 + `<app>-db-secret` Crossplane push）—— PreSync Job envFrom 两个都加
    - **模式 C**：用合并的 ExternalSecret template 拼 `DATABASE_URL` —— PreSync Job 也读这个合并 Secret
    - **模式 D**：新 shared-middleware 集群（staging / tech-service）app 通过 `kind:Database`
      provisioning 拿到 per-app database credential，由 ExternalSecret 从 region-less per-app 路径
      （`secret/{env}/{platform}/application/{database_app}/{key}`）生成 `<app>-db-secret`；
      必须包含 `DB_NAME`，且 cd-requirements.md 记录 per-app database/user 授权来源。
      engine=mysql/postgres/redis 仅对 AWS US/EU 已支持且满足前置条件的目标，或已完整通过 CN TKE 能力门禁的目标继续；
      CN TKE 缺证据、或其它引擎 `kind:Database` 消费尚不可用（见进入条件 READINESS GATE）时，仍 STOP + Ops Todo
  - 用 `recipes/k8s/external-secret.yaml.tmpl` 写出来的就是模式 B（推荐——单一职责）
  - cd-requirements.md 记录选用了哪个模式

[validate]
  - 选定一个模式
  - 新 staging app 使用模式 D 时，`$envfrom_refs[]` 引用的 Secret 必须含
    `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME`；缺任一字段 STOP
  - **不要** 两个 ExternalSecret 写同一个 target Secret（**禁止** —— 跟 hard rule #15 + redlines #1 一致，触发 reconcile race 字段被反复擦写）

[output]
  - `$secret_pattern`、`$envfrom_refs[]`（PreSync Job envFrom 引用列表）

## Step 3. 写 PreSync hook Job

[precondition]
  - Step 2 完成

[action]
  - **决策 `{{service_account_name}}`**：读取精确 target 渲染后主应用 Pod template 的 `spec.serviceAccountName`，Job 复用同一个有效 ServiceAccount：
      - 主应用显式指定名称 → 填该名称；AWS IRSA 与 TKE 自定义身份都不能靠文件名推断。
      - 主应用未指定或显式为 `default` → 才填 `default`。缺少 AWS IRSA 文件不代表主应用使用 `default`；无法确定主应用身份时 STOP，不猜测。
  - 用 `recipes/db-migration/presync-hook.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{namespace}}=$target.namespace`、
      `{{service_account_name}}=$sa_name`（上面决策结果）、
      `{{migration_command}}=`（如 `/usr/local/bin/{{app}}-migrate`）、
      `{{envfrom_block}}=`（按 `$envfrom_refs` 渲染）
  - 默认复用主应用 image，让 Application Image Updater 的主 image override 同时更新 Rollout 和 Job；只有在 CI 已经构建并推送独立 migrate image、Application annotations 与 recovery seed 也配置了第二个 alias 时，才把模板里的 `image` 改为独立 migrate image
  - 写到 `k8s/overlays/{$target.env_keyword}/migration-job.yaml`
  - 加到 `kustomization.yaml` resources

[validate]
  - 文件存在
  - YAML 可解析
  - `kustomize build k8s/overlays/{$target.env_keyword}/` 成功
  - Job 有 `argocd.argoproj.io/hook: PreSync`
  - Job 有 `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation`
      （`BeforeHookCreation` 让下次 sync 自动删上次的 Job；
       不加 `HookSucceeded`，否则 ArgoCD 会在成功后立刻删 Job，`ttlSecondsAfterFinished: 86400` 无法保留日志）
  - Job container 默认 `image: $app`，除非本 MR 同时证明独立 migrate image 的 CI 和 Image Updater alias 已配置
  - Job spec `serviceAccountName` 跟精确 target 渲染后主应用的有效 ServiceAccount 一致；未指定时按 Kubernetes 的 `default` 语义比较，不按云厂商或 IRSA 文件存在性推断

[output]
  - k8s/overlays/{$target.env_keyword}/migration-job.yaml

## Step 4. 验证时序约束

[precondition]
  - Step 3 完成

[action]
  - 检查这次 MR 是否同时改了：
    - ExternalSecret（包括 ConfigMap，影响 envFrom 的内容）
    - migration binary 的 image tag 或 Application Image Updater image override
  - 如果两者同 commit → **STOP**：PreSync hook 跑在 ExternalSecret 同步**之前**，会拿到旧 Secret
  - **正确做法（two commit rule）**：先 1 commit 改 ExternalSecret（让新值生效），再 1 commit 改 image 触发 migration

[validate]
  - 当前 commit 不同时改 Secret 来源和 migration 触发器

[output]
  - 时序顺序确认

## Step 5. 全量 validator

[precondition]
  - Step 4 通过

[action]
  - `bash "$skill_root/validators/validate.sh" k8s/`

[validate]
  - 退出 0

[output]
  - validator 日志

## Step 6. 更新文档 + summary

[precondition]
  - Step 5 通过

[action]
  - cd-requirements.md 加 "DB Migration" 段：
      - migration binary 路径（image / command）
      - 选用的 Secret 模式（A / B / C）
      - 5 条契约满足说明
  - cicd.md append "DB Migration 接入"
  - summary

[validate]
  - summary 已打印

[output]
  - 最终用户消息

## 出口

ArgoCD 下次 sync：
1. 看到 PreSync hook → 删上次 Job（如有）→ 建新 Job
2. Job 跑 migration binary，envFrom 注入 DB env vars
3. binary 退出 0 → PreSync 成功 → ArgoCD 继续 sync 主资源（Rollout 滚新版本）
4. binary 退出非 0 → Job 重试 backoffLimit 次（默认 1）→ 仍失败 → PreSync 失败 → ArgoCD sync 整体失败 → 通知到飞书 feishu-ops channel
5. 新版本 Pod **不启动**——旧版本继续承接流量

**常见踩坑** —— 跳 `troubleshooting/db-migration-failures.md`：
- 同 commit 改 Secret + 镜像 → 时序坑
- 密码含 `:` 但拼接没 URL escape
- migration 非幂等 → Job retry 写脏数据
- 两个 ExternalSecret 写同一个 target Secret
- Job 没显式 imagePullSecrets（hard rule #24，跟 hook Job pod 一样适用）
- SA 跟主应用不一致 → IRSA 失败 → DB 连不上

## 详细参考

- `references/db-migration/secret-patterns.md` —— 三种正确模式 + 两种禁忌的详细对比
- `references/db-migration/end-to-end-example.md` —— 完整端到端 MR 示例
- `references/db-migration/timing-pitfalls.md` —— 时序坑深入
- `recipes/db-migration/presync-hook.yaml.tmpl` —— Job 模板
- hard-rules.yaml #15 / #16（Migration 必须 PreSync + 不许同 Secret 两 ES）
