---
name: interview-cd-requirements
description: 通过访谈把用户意图填进 `docs/deployment/cd-requirements.md`。每个"新建"workflow（new-service / new-stateful-service / new-cronjob）会先 chain 本 workflow；用户也可单独触发只写需求文档。
---

# Workflow：interview-cd-requirements

## 目的

把用户的部署意图变成 `docs/deployment/cd-requirements.md`，作为讨论记录。这是所有下游 manifest 生成的输入；如果下游 workflow 发现文档里有未回答的问题，**必须** 停下来。

## 进入条件

- 用户表达了部署意图（新建服务、加资源等）
- 工作目录在目标应用的 git 仓库
- `docs/deployment/cd-requirements.md` **不存在**，或用户显式要求刷新

如果文档已存在且用户未要求刷新，**跳过** 本 workflow，把现有文档作为下游 workflow 的输入。

## Step 1. 摸现有事实

[precondition]
  - 工作目录是一个 git 仓库

[action]
  - 读 `README.md`（如存在）
  - 读 `docs/architecture.md` / `docs/design.md`（如存在）
  - 列 `k8s/` 目录（看是首次部署还是扩展）
  - 跑 `git log -20 --oneline` 看最近活动

[validate]
  - 无（只读步骤）

[output]
  - 内存变量；本步不写文件

## Step 2. 识别 domain 和 app 类型

[precondition]
  - Step 1 完成

[action]
  - 打开 `references/data/domain-classification.yaml`，按 `decision_order` 从上到下判：
    1. 先判定 domain：`ops` 还是 `builder`
    2. 如果是 `ops`，继续判定二级分类：`c-end` / `restricted-admin`
    3. 再记录面向人的 app type（如 管理后台 / 数据分析/离线任务 / 生产环节应用）
  - `$app_type` 是部署路由用的规范 slug（用于 `env-keywords.yaml -> app_types` 校验），集合是 `c-end | restricted-admin | builder | data | observability`，只能取：
    - `c-end`
    - `restricted-admin`
    - `builder`
    - `data`
    - `observability`
  - `$workload_profile` 是补充说明，不参与环境路由：
    - 服务明确要上 `prod-*-data` 这类 data 集群 → `$app_type=data`
    - 服务只是内部管理后台里的数据/报表能力 → `$app_type=restricted-admin`，`$workload_profile=data`
    - 观测平台目前只有 staging route；prod 观测类如果没有明确 env keyword → STOP 问用户，不能把它塞进 c-end prod
  - 如果不能精确选一个，STOP 问用户；**不要** 挑"最接近的"

[validate]
  - 选出的 (domain, app_type) tuple 是以下之一：
    - ops / c-end
    - ops / restricted-admin
    - ops / data
    - ops / observability
    - builder / builder

[output]
  - 变量 `$domain`、`$app_type`、可选 `$workload_profile`；不写盘

## Step 3. 解析环境关键词到集群

[precondition]
  - Step 2 完成
  - 用户已说明要部署到哪些环境（staging-us / prod-us 等）
  - 如未说明，STOP 问用户

[action]
  - 对每个用户给的环境关键词：
    - 打开 `references/data/env-keywords.yaml`
    - 找 `env_keywords[<keyword>]` 行
    - 验证 `$app_type` 在该行 `app_types` 列表里
        - 高频踩坑：admin 类应用用户说"prod-us"，应改为 `prod-us-restricted-admin`
    - 记录：env_keyword、cluster、env、namespace_pattern
  - 如果关键词不在 `env_keywords`，STOP 问用户

[validate]
  - 每个用户给的环境关键词都解析到一个集群 + namespace

[output]
  - `$targets[]`，每项：{ keyword, cluster, env, namespace, branch }
  - branch 默认：staging keyword → "main" 或 "staging"（按 app 惯例）；
    prod → "prod"；dev → "dev"。app 没惯例时问用户。（pre 已废弃，不再路由）

## Step 4. 识别 runtime profile 并探测声明的资源

[precondition]
  - Step 3 完成；`$targets` 非空

[action]
  - 先确定 `$shape = stateless | stateful | cronjob`。若是 `cronjob`，先记录下列
    scheduled-job contract，再继续探测资源和密钥；不要把它伪装成长驻 service：
    - 标准 five-field schedule，以及 target API/repository 是否支持 `spec.timeZone` 的证据；
      支持时记录 IANA timezone，不支持时记录 controller timezone 和省略理由
    - `concurrencyPolicy`、`startingDeadlineSeconds`、`activeDeadlineSeconds`、
      `backoffLimit`、`successfulJobsHistoryLimit`、`failedJobsHistoryLimit`
    - immutable tag plus digest image、非空 command/args、env/envFrom（无凭据时明确 `[]`）
    - resource/security/placement/ServiceAccount 决策、owner、观察/告警和 rollback
    - registration 默认 `spec.suspend: true`；activation 必须是只改 approval URL 和
      suspend 的 separate MR
  - `$shape=cronjob` 时 `$runtime_profile=(n/a - cronjob)`，跳过下面仅适用于常驻 HTTP
    workload 的 service/static-web profile 分支；任何 scheduled-job 字段未知时 STOP，
    不写需求文档。
  - `$shape != cronjob` 时，再确定 `$runtime_profile = service | static-web`，它是部署/运行形态，不是编程语言：
    - `service`：镜像内运行 Go / Node / Python / Java 等长驻应用进程。显式选择该 profile
      后保持既有 recipe 默认（包括 Node recipe 的 `npm start` fallback）；但不能把这个
      fallback 当作仓库存在 production runtime 的探测证据。
    - `static-web`：Node 只用于构建，最终镜像由 Nginx 提供静态文件，不包含 Node runtime。
  - 对已有 Node 前端仓库做 evidence-driven 探测：
    - `package.json` 有可执行的 build script、`package-lock.json` 存在、构建产出静态目录，
      且没有 production runtime start command（`dev` / `preview` 不算 production start）
      时，只能**建议** `$runtime_profile=static-web`，不能仅因“语言是 Node”自动决定。
    - “没有 start script”本身不是充分证据；可能是仓库不完整、library 或自定义启动方式。
    - 如果 service/static-web 仍有两种合理解释，STOP，让用户显式确认 profile；不要补
      `npm start` 绕过。
  - 若确认 `$runtime_profile=service`：
    - 记录仓库实际 `$language` 和 production `$start_cmd`（Go/Java 可由 recipe 固定
      入口表达；Node 可显式记录既有 recipe 的 `npm start` fallback）。
    - Go/Node/Python/Java 继续使用现有 recipe。其它已有长驻服务语言仍保留
      `service` profile，由 `new-service.md` 产 Dockerfile recipe Ops Todo 后继续生成
      与语言无关的资源；不要因为本次增加 static-web profile 提前 STOP。
  - 若确认 `$runtime_profile=static-web`，记录以下显式 contract：
    - `$build_toolchain=node`；`$node_version` 取 `engines.node` / 现有 CI 的已验证精确
      semver，且匹配 `^[1-9][0-9]*\.[0-9]+\.[0-9]+$`；没有证据时用维护默认前必须让
      用户确认，禁止 `latest`、major-only 或 range。
    - `$nginx_version` 是已维护镜像的精确 semver，匹配
      `^[1-9][0-9]*\.[0-9]+\.[0-9]+$`；禁止 `latest`、major/minor-only 或 range。
    - 记录 `$static_verify_runner_tag`。该 amd64 runner 必须有可审计配置证明：
      不挂载 Harbor push credential，不运行复制 registry credential 的
      `pre_build_script`，不向 job 暴露云写凭据、Kubernetes service-account token 或
      protected secret variables。不能证明 credential-free → STOP，先让 Platform 提供
      专用 verification runner；不得改用 target image-build runner。
    - `package.json` 与 `package-lock.json` 都必须被 Git 跟踪。运行
      `npm ci --ignore-scripts --dry-run` 必须退出 0，以证明 lockfile 与 package manifest
      同步且可供 recipe 的精确 `npm ci` contract 使用；不得靠 `npm install` 修锁后继续。
    - 对每个 target 分别记录
      `{ static_build_script, static_output_dir, bundle_config_source }`：
      - `static_build_script` 必须是 `package.json.scripts` 中的准确 key，并匹配
        `^[A-Za-z0-9:_-]+$`；多个候选或 script 是否负责目标环境不明确 → STOP 问用户。
      - `static_output_dir` 必须匹配 `^[A-Za-z0-9][A-Za-z0-9._/-]*$`，且是明确、非绝对、
        无 `..` 的仓库相对目录；Vite 默认 `dist` 只有在 script/config 证据唯一时才能采用。
        配置与实际 build 产出冲突或目录不明确 → STOP 问用户。
      - `bundle_config_source` 必须是该 target 对应的、Git 跟踪且可 review 的非秘密配置
        或 build mode。前端环境值会写进浏览器 bundle，任何 token/password/credential/
        private key/secret 需求 → STOP，要求移到后端/BFF 或运行时安全边界。
      - 准确 build script 必须生成 production-semantics bundle。Vite target 必须证明
        effective `NODE_ENV=production`（可由 `vite build` 默认值 + 无 tracked override
        共同证明，或由 tracked config 显式设置），且不得使用 `--mode=development`、
        `NODE_ENV=development` 或等价开发语义。无法从 script 与 tracked config 唯一证明
        production semantics → STOP 问用户。
      - 展开准确 build script/mode 会自动读取的全部 `.env*` / config，并 review effective
        Docker context。任何可能被 build script 读取的 untracked、ignored、generated
        config 或 credential-bearing 文件 → STOP；只能用 reviewed `.dockerignore` 排除，
        且不能误排已确认的 Git-tracked public target config。
      - `bundle_config_source` 是写入 Markdown 需求表的单行 evidence，禁止 CR/LF、`|`、
        `{{` 或 `}}`；Dockerfile recipe 不插入该自由文本，只固化经过正则约束的 build
        script 与 output dir。
    - 显式记录 `$spa_routing = yes | no`：
      - `yes` → Nginx `try_files $uri $uri/ /index.html`
      - `no` → Nginx `try_files $uri $uri/ =404`
      - 不能从框架名、现有路由文件或“看起来像 SPA”猜；不明确 → STOP 问用户。
    - `$container_port` 与 `$health_path` 必须显式。`$health_path` 必须匹配
      `^/[A-Za-z0-9][A-Za-z0-9._~/-]*$` 且不能是 `/`、不能含 `..` 或 `//`；Nginx
      为该 exact path 返回 200，保证 Kubernetes probe 不依赖某个 bundle asset，也不让
      未约束文本注入 Nginx 配置。
    - `$container_port` 只能是 `1024..65535`，或已冻结 legacy/source port 80 contract。
      端口 80 还必须在需求文档记录 migration owner 对 root-master 兼容例外的显式批准；
      新 contract 默认改为 8080 等非特权端口，Service 对外端口仍可保持 80。
    - 每个 target 产独立 Dockerfile + 独立 image path。即使 build script 恰好相同，
      也不能让一个已为某环境打包的 artifact 静默复用到另一个环境；不得给 Docker build
      增加 secret build args，也不得把 Vault/ExternalSecret 值注入静态 bundle。
    - static-web 容器不消费 runtime Secret：不得为它生成或挂载 ExternalSecret、Secret
      `env`/`envFrom`、secret volume，或由 CI/Vault/Secret 生成的 `.env`、JavaScript、
      JSON 等配置文件。任何需要凭据的 datastore/API/resource 属于独立 backend/BFF
      service，STOP 并拆分 contract。只有已明确分类为 public、可提交 Git 的 bundle
      配置可以进入前端构建。
  - 列出服务需要的每个外部资源：
    rds / aurora / redis / mongodb / kafka / s3 / msk / clickhouse / external-mysql / ...
  - 对每个资源：
    - 若任一 target 的 `$target.cluster` 是 shared-middleware 集群（6 个：`{us,eu,cn}-eks-staging` +
      `{us,eu,cn}-eks-tech-service`；**按 cluster 名判，不用 `$target.env`**——tech-service env=prod，见 #29）
      且资源属于 `rds` / `aurora` / `redis` / `elasticache` / `documentdb` / `msk` / `kafka` /
      `clickhouse` / `mongodb`（hard-rule #29：这些集群只能消费 shared-middleware，**禁止 app-owned，
      禁止 StatefulSet+PVC 自托管 #30**）：
      - **已上线自助 mysql / redis / postgres**：不 STOP。指引开发者用
        `recipes/k8s/shared-database-claim.yaml.tmpl` 从 canonical kebab app slug 生成
        `kind: Database`（slug annotation → lower_snake_case `spec.app`，即
        `$database_app = $app.replace('-', '_')`），凭据落
        `secret/{env}/{platform}/application/{database_app}/{key}`，app 写 ExternalSecret 消费；记入需求文档。
      - **已上线自助 MSK(Kafka) SCRAM 凭据**：不 STOP。指引开发者写
        `kind: KafkaScramCredential {app, env, region}`，凭据落
        `secret/{env}/kafka/application/{app}/sasl`（username/password/bootstrap_brokers_sasl_scram），
        app 写 ExternalSecret 消费并把 broker 字段映射为 `KAFKA_BROKERS`；记入需求文档。
        Kafka topic/ACL/consumer group 治理仍 planned，需要显式治理时路由 Ops Todo，不现编
        `add-kafka-topic`。
      - **尚无消费 Composition：aurora / mariadb-RDS / documentdb / clickhouse**：STOP 并路由 Ops Todo
        "Platform: 补 <kind> 的可复用 kind:Database 消费 Composition + 自动 per-app 凭据交付"（注：documentdb /
        clickhouse 共享实例已部署 6 集群，仅消费层未落地）。记为 **Future State**。不要手搓 shared-middleware
        路径或 legacy `staging-{region}/` 资源，也不要让单 app 自建 / StatefulSet 自托管。
      - **非 shared-middleware 集群 target（真 prod `*-eks-prod`/`*-prod-data`/TKE/dev；tech-service 不在此列）**
        continue with the rules below（app-owned 托管实例）。
    - 在 `references/cost-tiering/<kind>.yaml` 找到 → **模板化** 资源；记录各 env 的规格 + immutable 字段
    - 在 `cost-tiering/_global.yaml -> unknown_resources.exceptions` 里（例如 clickhouse）→ 也是模板化
    - 都不在 → **未知资源**；workflow **不** 自动生成，加进 Ops Todo

[validate]
  - `$shape=cronjob` 时 schedule 恰好 five-field；timezone 决策有证据；并且 concurrency、
    missed-run deadline、job deadline、backoff、history、digest image、command/args、
    env、resources、security、placement、ServiceAccount、suspended registration 和
    separate activation MR 全部显式。任一缺失即 STOP。
  - `$shape != cronjob` 时 `$runtime_profile ∈ {service, static-web}`
  - `$runtime_profile=service` 时 `$language` 非空且匹配
    `^[a-z][a-z0-9+._-]*$`。Go/Node/Python/Java 必须有 recipe 所需的 production
    start/entrypoint 或既有 recipe default；其它语言必须有长驻 runtime 证据并记录
    downstream Dockerfile recipe Ops Todo。现有 service contract 不被 static-web 分支改写。
  - `$runtime_profile=static-web` 时：
    - `package.json` / `package-lock.json` 均被 Git 跟踪，且
      `npm ci --ignore-scripts --dry-run` PASS
    - `$node_version` / `$nginx_version` 均为 exact semver 且非 `latest` / range
    - `$static_verify_runner_tag` 已从可审计 runner 配置证明 credential-free，且不是
      挂载 Harbor push credential 的 target image-build runner
    - 每个 target 的 build script、output dir、bundle config source 都唯一且显式
    - 每个 target 的 exact build script 与 tracked config 已证明 production semantics；
      Vite effective `NODE_ENV=production`，且不存在 `--mode=development`、
      `NODE_ENV=development` 或等价开发语义
    - exact build script/mode 的 framework auto-loaded config 与 effective Docker
      context 已 review；不存在可被读取的 untracked/ignored/generated 或 credential-bearing
      config
    - `$spa_routing` 已明确为 `yes` 或 `no`
    - `$container_port` 是 `1024..65535`，或有显式 owner 批准的 source port 80 兼容例外
    - bundle config evidence 可安全写入 Markdown 单行，且不含秘密
    - 没有 Vault、ExternalSecret、Secret、secret/CI build variable、Docker ARG/ENV 或
      generated config 注入 bundle/runtime 的计划
    - 任一项未知、歧义或字面文本 `(question)` → STOP，不写需求文档
  - 新部署应用的 **staging / tech-service**（6 集群 us/eu/cn-staging + tech-us/eu/cn）数据类中间件
    没有出现在 `$templated_resources[]` 的 app-owned per-env config 里，也没有以 StatefulSet+PVC
    自托管形态出现（#30）；只允许出现在 `$shared_middleware_resources[]` 或 missing-contract Ops Todo。
  - 每个未模板化资源的 Ops Todo 必须包含 `_global.yaml -> unknown_resources.required_inputs` 三项：
    `expected_data_volume`、`read_write_qps`、`retention_duration`

[output]
  - `$shape`；cronjob 时额外输出 `$scheduled_job_contract`
  - `$runtime_profile`
  - service profile：`$language`、`$start_cmd`
  - static-web profile：`$build_toolchain`、`$node_version`、`$nginx_version`、
    `$spa_routing`、`$static_verify_runner_tag`、每 target 的
    `{ static_build_script, static_output_dir, bundle_config_source }`
  - `$templated_resources[]`（kind + per-env config）
  - `$shared_middleware_resources[]`（staging / tech-service target + shared contract / missing-contract Ops Todo）
  - `$unknown_resources[]`（kind + 需要的输入）

## Step 5. 探测声明的密钥

[precondition]
  - Step 4 完成

[action]
  - 列出服务需要的每个密钥：
    - DB 连接（如 `$templated_resources` 含 rds / aurora / redis / mongodb 等，自动生成）
    - staging middleware 连接（注：per kind:Database / KafkaScramCredential readiness gate，仅记录预期路径模式，不从 contract 表查）：使用新的 per-app region-less 格式 `secret/staging/{platform}/application/{app}/{key}`，其中 `kind:Database` 的 `{app}` 必须是从 slug 派生的 `database_app/spec.app`；KafkaScramCredential 继续使用自己的 app 字段。platform 对应 RDS→rds、ElastiCache/Redis→redis、DocumentDB→mongodb、MSK→kafka（key=sasl，含 username/password/bootstrap_brokers_sasl_scram）、ScyllaDB→scylla、ClickHouse→clickhouse
    - 第三方 API key（Stripe / Sentry DSN / Casdoor OIDC / Feishu / ...）
    - 内部 webhook / 共享服务 token
  - 对每个密钥，跑 `references/vault-paths/resolver.md` resolver（输入 env / kind / app / key）记录 Vault 路径
  - 对每个密钥，识别 populated-by（Crossplane / Generator / Maintainer 手写 / 运维写入）
  - 若 `$runtime_profile=static-web` 且列出了任何由该前端消费的 secret，STOP：
    static-web 的浏览器 bundle 与 Nginx runtime 都不能消费 Vault / ExternalSecret /
    Kubernetes Secret。把凭据和依赖移到独立 backend/BFF；公开配置留在每 target 的
    `bundle_config_source`，不要把 public value 误记为 secret。

[validate]
  - 每个密钥有且仅有一个 Vault 路径
  - staging database credentials must follow the per-app region-less format:
    `secret/staging/{platform}/application/{app}/{key}` (for `kind:Database`, `{app}` is the
    slug-derived `database_app/spec.app`; Kafka keeps its own app field. Platform ∈ {rds, redis,
    mongodb, kafka, scylla, clickhouse}; MSK uses `key=sasl` for
    username/password/bootstrap_brokers_sasl_scram, and app ExternalSecret maps
    bootstrap_brokers_sasl_scram to KAFKA_BROKERS)
  - staging resources NOT yet deployed via kind:Database must be noted in the Ops Todo section
  - 业务凭据 **不** 走 `secret/cicd/*`（红线 7a）
  - `$runtime_profile=static-web` 时 `$secrets[]` 不含任何 `consumed_by` 指向前端
    bundle、Nginx container 或 generated frontend config 的项

[output]
  - `$secrets[]`，每项：{ name, vault_path, populated_by, consumed_by }

## Step 6. 探测网络暴露

[precondition]
  - Step 3 完成

[action]
  - 问用户（或读已声明的设计）：服务对外公开吗？
  - `$shape=cronjob` 时固定 `$exposure=none`；本 workflow 不为定时任务生成 Service、
    Ingress 或公网入口。若用户另有暴露需求，必须独立路由和 review。
  - 如果是：
    - hostname？
    - 面向 C 端 OR 内部工具？（内部工具 **必须** 有 SG / WAF / SSO 三选一，否则按项目网络安全规则 #3 STOP）
    - 需要 WAF？
    - 需要 from-office SG？
    - 需要 SSO 鉴权挂前面？

[validate]
  - 如果是公网暴露 + 内部工具 + SG/WAF/SSO 三件套都没设：workflow STOP；用户必须先改 exposure 方案

[output]
  - `$exposure`：none / public-c-end / public-internal-with-guard

## Step 7. 写文档

[precondition]
  - Step 2-6 完成

[action]
  - 打开 `recipes/docs/cd-requirements.md` 模板
  - 用 Step 2-6 收集的值替换每个 `{{slot}}`
  - 写到工作仓库的 `docs/deployment/cd-requirements.md`

[validate]
  - 文件存在于 `docs/deployment/cd-requirements.md`
  - 文件内不剩任何 `{{...}}` 占位符

[output]
  - `docs/deployment/cd-requirements.md`

## Step 8. 给用户的 review 摘要

[precondition]
  - Step 7 完成

[action]
  - 打印简短摘要：
    - 解析到的 targets（环境关键词 → 集群 + namespace）
    - cronjob 时列出 schedule/timezone、concurrency/deadline/backoff/history、digest、
      command/args 和 suspended activation contract
    - runtime profile；static-web 时列每 target 的 build script / output dir / bundle config
      source 和 SPA routing 判定
    - 模板化资源（数量 + 种类）
    - 未知资源（会进 Ops Todo）
    - 密钥数量按 populated_by 分组
    - exposure 判定
  - 告诉用户："review `docs/deployment/cd-requirements.md`；准备好生成 manifest 时跑对应的 workflow"
  - 如果本 workflow 是被 `routes-build.yaml -> internal_calls` 链式调用，返回给调用方继续跑下游 workflow。
  - 如果用户显式触发的是"仅写 cd-requirements"，到这里结束，不自动续跑下游 workflow。

[validate]
  - 无

[output]
  - 用户可见的摘要；不写盘

## 出口

本 workflow 到此结束。若它是 build workflow 的 internal call，调用方继续把生成的
`cd-requirements.md` 作为输入执行；若用户只要求写需求文档，则停在这里等待 review。
