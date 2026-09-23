# 项目与实例迁移

用于明确授权的 staging → prod 实例合并或已有项目迁移。本文不声明任何旧实例已删除。新接入默认路由单独由 `references/data/sentry-instances.yaml` 管理：staging/prod 均使用区域 prod 实例，staging 复用同应用项目并保留 staging Vault 身份，SDK environment 区分环境；已有应用仍须按下述顺序迁移。

## 三种身份分别保留

| 字段 | 作用 | 迁移示例 |
|---|---|---|
| `APP` + `ENV` + `VAULT_PATH_SCHEMA` | 应用的 Vault 身份，ExternalSecret 和 K8s 资源名仍据此生成 | `orders` + `staging` + 现有 `legacy` 或 `platform`，保持不变 |
| `INSTANCE` + `PROJECT_SLUG` + `TEAM` | 管理 API 目标实例与共享应用项目 | `us-prod` + `orders` + 实际存在且拥有该项目的 team |
| `DSN_HOST` | 事件接收入口，仅替换 DSN host | 后端 Pod 经验证的 `sentry-relay-us.addx.live`；staging 移动端/浏览器须验证品牌公网 host；无 `https://`、端口或 path |

`PROJECT_SLUG` 省略/空白时默认 `APP`，显式值须满足 `^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$`。`DSN_HOST` 省略/空白时保持原规则；显式值为最多 253 字符、每 label 最多 63 字符的 DNS hostname，禁止 scheme/userinfo/port/path/query/fragment。C 端 `ENV=prod` 始终使用品牌 relay，仍校验 BRAND 和 CN relay 限制；不能用 DSN_HOST 绕过。

三区 `sentry-relay-*` 是私网入口，只作为 backend/admin 的默认值。staging 移动端/浏览器复用同应用项目，但必须先确认品牌公网 ingest、目标项目转发和真实终端可达性，再显式设置 DSN_HOST；未确认时停止生成，不能回退到私网 relay。工具仅在 prod 接受 BRAND；staging BRAND 必须省略，非空会被拒绝，品牌 host 通过 DSN_HOST 设置。

这些字段依赖支持它们的 `sentry-onboard` 镜像，源码参考 [独立项目与 ingest host 支持](https://gitlab.addx.ai/DEV/sentry-onboard/-/merge_requests/9)。先核验已合并版本、CI 构建及本集群 Harbor 的不可变 tag/digest，再启用；旧版本会静默忽略字段。feature branch 的 SHA 不等于已构建镜像，当前构建只在 main 触发。

## 迁移顺序

1. 建立源 project → 目标共享 project → 消费者 → Vault/Secret 的清单。staging 迁入 prod 时核验并复用同应用的原 project（如 `<app>`），不再自动创建 `<app>-staging`。缺少同名项目时核验归属后创建原名；仅有旧后缀项目且无冲突时可改名保留历史。超长或归属冲突时停止，不悄悄截断或认领。目标 project/team 需核验归属，历史事件和告警配置不会因新建项目自动复制。
2. 核验实际 Job 的 `VAULT_ADDR`。其中 `secret/cicd/sentry/endpoints` 和 `secret/cicd/sentry/tokens` 两个 KV map 都须含目标 `INSTANCE` 字段，并由 Job 的 JWT role 可读。builder 与 ops 是不同 Vault，不能因 ops 中已有 prod 字段而假定 builder 存在；只输出字段名和状态。新增字段时保留其他区域/实例项。
3. 分别验证目标管理 API 的认证/权限，以及 ingest 候选入口的 DNS/TLS/网络可达性。此时目标项目可能尚未创建，真实事件验收在下一步完成。公网跨云流量遵循已有 NAT 出口及允许列表规则，不添加动态节点 IP。
4. 切换 DSN 前先核验 SDK 实际 environment 含 `staging`，修复缺省/误标 production 的消费者（包括 JVM 参数和浏览器构建配置）。已有生产告警的明确环境保持不变；无环境限制的 issue alerts 排除 environment 含 staging，保持其他条件/动作和 AND/OR 语义；metric alerts 同样排除 staging。不能以设置 Vault ENV 代替事件环境核验。
5. 复用或创建并核验目标 project，保留原 Vault DSN 记录和回滚版本，此步不改写 Vault。现有 Job 会拒绝“Vault 有记录但目标 project 缺失”；不要删 Vault 绕过。目标项目存在、Vault 无记录时同样拒绝自动认领，先核验归属，人工写入需等下一步停止旧 Job 后再做；只初始化已核验的同应用 staging 路径，保留防自动认领保护。取得目标 DSN 后，从实际 staging Pod/应用终端网络发送带唯一标识的事件，并在目标项目回读；网页 HTTP 200、envelope HTTP 200 不能单独证明事件已入库。
6. 暂停旧接入 Job 的同步/重试，等待已启动的旧 Job 完成；协调应用 GitOps 源的 `INSTANCE`、`PROJECT_SLUG`、`TEAM`、必要 `DSN_HOST` 和支持字段的不可变镜像。若需由迁移步骤人工写入 DSN（包括目标项目已有而 Vault 无记录的分支），只能在旧 Job 已停止后、恢复同步前进行，并保留其他 Vault 字段。`APP`、`ENV`、`VAULT_PATH_SCHEMA`、ExternalSecret/CSS 和 Secret 名保持原身份。onboard 的 WriteKV 只写 `{dsn}`：恢复 Job 前必须确认该路径是 DSN 专用且无其他需保留字段；否则停止预检，不能假定 Job 会保留其他字段。不要仅手改 Vault 后保留旧 Job 自动回写，也不要把 staging ENV 改为 prod。
7. 同步新版 Job，核验写入的是原 Vault path/property `dsn`，再次运行不重复写；等待 ExternalSecret Ready，并重启需重新读取环境变量的应用。检查实际运行的应用 DSN 目标及测试事件的 staging environment，报告只写目标host/project/状态，不写完整 DSN/token。
8. 所有消费者和自动接入配置确认已切换、旧写入源已失效后，再停用旧 `*-staging` 项目的 client keys，保留项目和历史事件，不删除历史数据。旧实例下线/删除只在另有明确授权且依赖清理完成时执行。仅项目创建、代码 MR 或部分事件成功，不等于完成下线；失败时停止删除，保留旧实例和 Vault 版本供恢复。

新应用仍走 `workflows/add-sentry.md`；本迁移不顺带转换 legacy/platform schema。静态字段验证由 `validators/check_sentry_project_config.py` 完成，不能代替镜像、Vault、网络和事件入库验收。

## 共享项目的边界

Environment 用于查询/告警筛选，不隔离 issue 聚合、解决/回归状态、成员权限、
数据保留或项目配置；相同错误仍可能跨环境聚为同一 issue。不要承诺 issue 生命周期
隔离，也不要为此次迁移擅自修改生产 fingerprint。首次复用需核验同应用和实际团队。
当前 onboard 从 TEAM projects 第一页查项目、从未过滤的 keys 响应取第一项，
因此预检须使用相同请求确认目标可见且首个 key active；不能先过滤后取首项，
也不能假定禁用 key 会让工具自动选择另一个。未满足时停在预检，不猜测新 DSN。
