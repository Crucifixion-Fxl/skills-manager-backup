---
name: vault-kv-manager
description: Manage HashiCorp Vault KV secrets and app-owned Vault access across Ops and Builder Vault instances. Use when user needs to add, update, read, delete, or list KV secrets, troubleshoot permission denied, or register an app in gitlab-vault-sync for owner/developer self-service.
---

# vault-kv-manager

通过 Vault HTTP API 管理公司 Vault 实例上的 KV 密钥。覆盖 **Ops 域** 和 **Builder 域** 两套 Vault 体系。

**适用场景：**

- 在 Ops 或 Builder Vault 上增删改查 KV 密钥
- 为新应用开通 Vault 自助权限（通过 gitlab-vault-sync apps-registry 注册，自动生成 owner/developer policy + Identity Group）
- 排查开发者 `permission denied` 问题
- 列出路径下的 key / 查看历史版本

## Vault 实例全景

A4x 按 **Ops / Builder** 分域管理 Vault。实例和服务集群会随迁移变化，不以固定实例数量或地区名推断密钥去向；先查目标集群的 ClusterSecretStore 与应用 registry。

### Ops 域 Vault（线上 prod 密钥）—— 运维专属

| 区域 | 地址 | 服务集群 | 用途 |
|------|------|---------|------|
| US | `vault-us-new.addx.live` | us-prod, us-prod-gke, us-data | C 端 prod 密钥 |
| EU | `vault-eu.addx.live` | eu-prod, eu-data | C 端 prod 密钥 |
| CN | `vault-cn.addx.live` | 腾讯云 prod `100014919455` / cn-main；新 tech-service `100052802231` 的 `vault-backend` 也指向此地址 | 按实际路径和 registry 判定密钥域 |

> **开发者无法登录 Ops Vault**——Ops Vault 只配置运维相关 policy，没有开发者角色。应用读取 prod 密钥通过 K8s ExternalSecret + ClusterSecretStore `vault-backend` 间接完成，Pod 不直连 Vault API。

### Builder 域 Vault（开发者自助）—— 以 registry 和实际实例为准

| 区域 | 公网地址 | 集群内访问（同集群 Pod） | 服务集群 |
|------|---------|---------|---------|
| US | `vault-us.builder.addx.live` | 同集群优先 `http://vault-builder-active.vault-builder.svc.cluster.local:8200` | us-staging 等 Builder 域集群 |
| EU | `vault-eu.builder.addx.live` | 同集群优先 `http://vault-builder-active.vault-builder.svc.cluster.local:8200` | eu-staging 等 Builder 域集群 |
| CN staging | 不从 Builder tech-service 域名推断 staging 入口 | 新 staging 的 `vault-backend` 指向 `http://vault-active.vault.svc:8200`；这是 Pod 内地址，本机须先发现服务再 port-forward 或使用已核验的登录入口 | 腾讯云 `100052802231` / cn-staging；应用 registry 见 `k8s/builder/cn-staging` |
| CN tech-service（目标） | `vault-cn.builder.addx.live`（`pending-cutover`） | 新 TKE 的 Builder 部署、Store 和集群内地址均待核验 | 腾讯云 `100052802231` / cn-tech-service 的目标归属；不能据旧 URL/DNS 宣称已迁移 |

> Builder 域已覆盖 CN/US/EU。`gitlab-vault-sync` 按域与部署目录管理应用 registry，不能再假设所有 CN 应用共用 `k8s/builder/cn`。新 staging 的 ArgoCD Application 已指向 `k8s/builder/cn-staging`，需继续读取其 Kustomize 引用以定位实际 registry。不要自行复制旧 registry 或密钥。

**CN 迁移核对（2026-09-28）**：AWS CN 已弃用；prod = `tencent-100014919455-cn-main`，staging = `tencent-100052802231-cn-staging`，tech-service = `tencent-100052802231-cn-tech-service`。详见 [CN 集群清单](../../infrastructure/k8s-ops/references/cn-tencent-inventory.md)。

| 当前集群 | `vault-backend` Git 声明 | 认证挂载 |
|----------|--------------------------|----------|
| cn-main | `https://vault-cn-internal.addx.live` | 读取本集群 ClusterSecretStore 验证 |
| 新 cn-staging | `http://vault-active.vault.svc:8200` | Kubernetes `kubernetes`，role `external-secrets` |
| 新 cn-tech-service | `https://vault-cn.addx.live` | JWT `jwt-tke-cn-tech-service-2231`，role `external-secrets` |

来源为 `k8s/clusters/<完整集群名>/cicd/external-secrets-config/cluster-secret-store.yaml` 和 `argocd-apps/tencent-100052802231-cn-staging/gitlab-vault-sync-builder-cn-staging.yaml`。Git 声明不等于认证已可用，写入前核实实际 SecretStore、registry、挂载和路径。

用户指定 `vault-cn.builder.addx.live` 为 cn-tech-service Builder **目标入口，待切换**；
它与上述 Ops `vault-backend` 是两个身份/权限域，不能把 Ops server 改为 Builder。
旧 Builder URL 的存在不证明实例已迁入 TKE，旧 `vault-cn-internal.builder.addx.live` 也不是新集群默认入口。
[固定声明与迁移边界](../../infrastructure/k8s-ops/references/cn-tencent-inventory.md#cn-tech-service-目标域名待切换)
保留 AWS Builder 历史回执及新 tech-service Ops 的真实声明；执行前须核验 Builder 部署归属、
registry、登录方式和 Store 的完整 auth 合同，未核实就暂停，不自动连接旧 AWS 或切到 Ops。
域名更新不改 Ops 的 `jwt-tke-cn-tech-service-2231`、KV 前缀、role/SA/audience；
Builder mount/issuer/role/audience/SA 尚无新 TKE 证据。SG 同步器的 `jwt-eks-sg-devops`、
人员 CLI 的 `jwt-casdoor` 分属其他调用身份，也不能据域名改名统一替换。

KV engine 统一：`secret/` (KV v2)。

> **默认 Vault = 目标应用 registry + 目标集群 SecretStore + 密钥实际权限**。不要仅按 prod/staging/tech 环境名拼接域名；新 CN staging 和 tech-service 的路由见上表。历史应用可保留 Ops Vault 老路径；新应用和自助权限以 `gitlab-vault-sync` registry 为准。

## 密钥分级与操作主体

| 级别 | 定义 | 路径范例 | 所在 Vault | 可操作主体 | 审批 |
|------|------|---------|-----------|-----------|------|
| **L1 生产密钥** | 直接关联 prod 环境的凭证 | `secret/<app>/prod/*`（历史）、业务 prod 路径 | Ops Vault | **仅运维**；开发者无读写 | [密钥使用审批流](https://applink.feishu.cn/T95dQpjJsTtK) |
| **L2a app-owned** | 应用自管凭证 | `secret/{dev,staging,pre,prod}/app/<app>/*` | Ops 或 Builder Vault（看 registry 域） | **应用 owner 自助**（GitLab Maintainer+ → Vault owner group） | 首次接入走 `gitlab-vault-sync` registry MR |
| **L2b platform-provisioned** | 平台批量 provision 的凭证（如 Crossplane RDS、Stripe test key、Sentry DSN） | `secret/{dev,staging,pre,prod}/<platform>/application/<app>/*` | Ops 或 Builder Vault（看 registry 域） | 平台管理员/控制器写入，应用 owner 只读 | 平台管理员（aws-admin、stripe-admin、sentry-admin 等） |
| **L2c 历史应用 staging/dev** | 历史应用的 dev/staging 凭证（仍在 Ops Vault 的老路径） | `secret/<app>/staging-<region>/*`、`secret/<app>/dev/*` | Ops Vault（老路径） | 仅运维直接操作；应用通过 ExternalSecret 间接读 | 无需审批（非 prod） |
| **L3 个人凭证** | 个人账号凭证 | — | — | 禁止入任何 Vault（Git Token、SSH Key、个人 API Token） | — |

**密钥级别判定原则：以密钥实际能访问的最高环境为准。** 不确定时按 L1 处理。

**硬规则：**

- L1 密钥创建/修改须走审批流，由运维操作
- L1 密钥至少每 90 天轮换
- 禁止通过飞书/邮件/口头传输密钥明文
- 开发者不得以"调试"/"临时使用"为由要求 L1 明文

## 认证方式（按使用场景选）

| 场景 | 认证方式 | 备注 |
|------|---------|------|
| 运维操作 Ops Vault | userpass | 用运维个人账号在本机安全页面登录，已授权任务经私密通道注入 Token（见下方说明） |
| 运维操作 Builder Vault | userpass 或 OIDC | 同上；Builder Vault 也可用 OIDC admin role |
| 开发者操作 Builder/Ops app-owned Vault | OIDC 飞书 SSO | 权限来自 gitlab-vault-sync 同步的 Identity Group；登录后以 token policies 为准 |
| CI/CD 同步 | AppRole | role_id/secret_id 在 GitLab CI Variables |
| K8s ESO 读取 | JWT | `external-secrets` ServiceAccount，ClusterSecretStore 自动处理 |

### AI 辅助操作 Vault 的标准方式：本机登录与私密注入

用户在已核验目标实例的本机安全页面完成登录。已明确授权的任务允许 AI 辅助正常 OIDC/OAuth 导航和登录检测，并将浏览器私有会话凭据或 credential provider 的 Token 经私密 stdin／进程内存、加密 SSH stdin 注入 native 消费者；不得要求用户把 Token 发到聊天，不把凭据放入 argv、文件、日志或输出。已有授权涵盖的正常登录步骤不重复申请批准；密码、MFA、验证码仍由用户直接在本机页面输入。未获授权不主动代持个人凭据，不改变用户选择的身份或认证方式。保留官方 Vault CLI/API 与 `X-Vault-Token`，网页辅助与原生访问并存。

**本机登录步骤**：

1. 浏览器打开目标 Vault UI：
   - Ops Vault 示例: `https://vault-us-new.addx.live/ui`、`https://vault-eu.addx.live/ui`、`https://vault-cn.addx.live/ui`
   - US/EU Builder Vault 示例: `https://vault-{us,eu}.builder.addx.live/ui`
   - CN staging：先核验 `100052802231` 目标集群的 `vault-backend`、实际 Service 和认证配置，再使用该实例已核验的登录入口；若只有集群内地址，按下方「CN staging 登录入口核验」建立本机转发。不能回退到旧 CN Builder 域名。
   - CN tech-service Builder：目标 UI 为 `https://vault-cn.builder.addx.live/ui`，当前 `pending-cutover`。先核实新 TKE 实例、登录方式/回调和权限域；未核实前不发起登录，不将它替换为 Ops `vault-cn.addx.live`。
2. 选认证方式登录：
   - 运维用 userpass（Method → Username）
   - 开发者用 OIDC 飞书 SSO（Method → OIDC；如实例要求 Role，使用平台提供的通用 OIDC role，不再手建每 app role）
3. 登录成功后核验目标实例及预期身份；已授权任务由安全浏览器会话或 credential provider 私密注入 native Token。
4. 按下方最小身份与权限检查继续；用户无需在聊天复制任何 Token。缺安全注入能力时保留网页登录结果并说明 native 未覆盖，不要求发送凭据。

**安全规则**：

- Token TTL 取实际认证配置与 lookup 响应，不按认证方式假定固定期限；过期时走正常登录或已有安全凭据刷新流程。
- Token 只经已授权私密注入，**不得在任何输出中回显 Token 明文**；stdin 不回显，消费者只输出允许的字段和脱敏错误类别。
- **UI 的 "Log out" 只退出浏览器 session，不自动 revoke native 认证 Token**。准确记录实际退出响应、临时进程／SSH资源和任务独立 profile 的清理；任务结束清理自己的临时资源，不影响既有会话。Token 撤销仅在对应用户授权或明确生命周期约定下使用官方 `POST /v1/auth/token/revoke-self`，不追加“撤销后请求必须拒绝”的验证。
- **严禁让用户在聊天发送账号密码、MFA、验证码或 Token**；需要用户操作时将安全登录页置前提醒，完成后检测登录继续已授权任务。

### CN staging 登录入口核验

`http://vault-active.vault.svc:8200` 是 ESO 的集群内入口，不是开发者公网登录地址。
先用已核验属于 `100052802231/cn-staging` 的 context 读取 `vault-backend` 和对应 Service，
确认 server、namespace、端口及目标实例。需要本机访问时，将已核验的 Service 端口
转发到仅监听 `127.0.0.1` 的空闲本机端口，再使用该转发的 `/ui`。
转发只解决连通性：须另行核验此实例的登录方式；OIDC 还需允许该实际回调地址。
登录方式、回调或实例身份未核实就暂停认证，补齐目标证据，不能尝试旧 Builder/Ops 地址作为 fallback。

## Builder Vault 路径规范（开发者必读）

**环境在前，应用在后**——写反最常见的错误。

```
secret/
├── dev/
├── staging/
├── pre/
└── prod/
    ├── app/<app-name>/<key>                     ← app-owner 读写（自助管理）
    └── <platform>/application/<app-name>/<key>  ← 平台管理员/控制器写，app-owner 只读
        例：rds/application/my-app/database、sentry/application/my-app/project
```

**常见错误**：

| ❌ 错误路径 | ✅ 正确路径 |
|-----------|-----------|
| `scm/dev` | `dev/app/scm/<key>` |
| `staging/app/my-app`（没有 key 末段） | `staging/app/my-app/<key>` |
| `secret/my-app-staging-cn` | `staging/app/my-app/<key>` |

**UI 注意**：Vault UI 的 "Path for this secret" 字段**不要带 `secret/data/` 前缀**，UI 内部会自动加。直接从 env 开始写，如 `dev/app/scm/config`。

**新应用 vs 历史应用路径规范不一样**：

| 类型 | 路径规范 | 示例 |
|------|---------|------|
| **新应用**（2026-04+ 规范） | `secret/{env}/app/<app>/<key>` | `secret/dev/app/scm/config` |
| **历史应用**（老路径） | `secret/<app>/<env>/<key>` | `secret/nacos/discovery/staging-us` |

历史应用**不强制迁移**——保持现有路径即可。新建应用必须用新规范，否则 policy 匹配不上。

## 权限模型（开发者问"我为什么 permission denied"时看这里）

gitlab-vault-sync 自动把 GitLab repo 权限同步成 Vault Identity Group 和 policy：Maintainer+ 是 owner，Developer+ 是 developer。普通 developer 只读 dev/staging；owner 可写 app-owned 的 dev/staging/pre/prod。

| Policy | 能读 | 能写 |
|--------|------|------|
| `app-{app}-developer` | `secret/data/{dev,staging}/app/{app}/*` + `secret/data/{dev,staging}/+/application/{app}/*` | — |
| `app-{app}-owner` | `secret/data/{dev,staging,pre,prod}/app/{app}/*` + `secret/data/{dev,staging,pre,prod}/+/application/{app}/*`（平台 provision 的凭据，如 Crossplane/Sentry 写入的连接信息） | `secret/data/{dev,staging,pre,prod}/app/{app}/*` |
| `{platform}-admin` | 该平台所有路径 | 该平台 provision 用 |
| `admin` | 所有 | 所有（含 auth/sys） |

**关键机制**：OIDC 登录时 Vault 依据 Identity Group 绑定的 policy 给权限；group membership 由 gitlab-vault-sync 从 GitLab 权限同步。确认权限时先查 app 是否已注册到正确的 registry，再查用户是否在 repo 里是 Maintainer+/Developer+。

## 常见踩坑（排障 checklist）

| 症状 | 根因 | 解决 |
|------|------|------|
| UI 写 KV 报 permission denied，路径看起来对 | app 未注册、用户不是 repo Maintainer+、或 token 缓存的是旧 policy | 查 registry + GitLab 权限；必要时登出后重新 OIDC 登录 |
| registry 和 GitLab 权限都对但仍 denied | gitlab-vault-sync 尚未 reconcile 或上次失败 | 等待 15 分钟，查对应区域 Job/日志 |
| 路径写成 `{app}/dev/*` 或 `{app}-staging/*` | 层级反了 | 改成 `dev/app/{app}/*` / `staging/app/{app}/*` |
| 路径层级对，但还是 denied | app 未注册到对应域/区域 registry，或用户不是 repo Maintainer+ | 按「场景 C：新应用 owner 接入」走 gitlab-vault-sync MR；或先调整 GitLab 权限 |
| registry MR 合并后 policy/group 没生效 | gitlab-vault-sync 尚未 reconcile 或目标区域 Job 异常 | 等待 15 分钟后查 Job/日志；必要时 dry-run/重跑对应区域同步 |
| 整条 POST 覆盖了已有 key（KV v2 陷阱） | KV v2 每次 POST 创建新版本，整体替换 | 必须先 GET → merge → POST |
| `vault-us-nonprod.addx.live` 连不上 | 2026-04-11 重命名为 `vault-us.builder.addx.live` | 用新域名 |
| 大 payload（KV path 已有几百个 key，>100KB）写入报 `Argument list too long` 或 `error parsing JSON`（尤其 CN Vault） | `curl --data 'big-string'` 走 argv 有长度上限；`-d`/`--data` 也会对空白做归一化，CN Vault 校验更严 | 用 `curl --data-binary @<tmp.json>` + 显式 `--header "Content-Type: application/json"`，从临时文件读取 payload（见下方 KV v2 写入示例） |

## 执行流程

### 场景 A：AI 代运维做 Vault CRUD

#### Step A1：安全登录、私密注入与最小核验

按上方本机登录与私密注入方法获得已授权会话。先用官方 `GET /v1/auth/token/lookup-self` 私有处理响应，最小投影仅保留 `display_name`、`policies`、`ttl`、`renewable` 等本任务确需字段，省略 `id`、`accessor`、`entity_id` 及原始 metadata。`display_name` 匹配不等于已证明 OIDC claim 绑定或业务资源权限；结合目标实例的服务端身份契约和确切路径 capabilities/policy 核验，未覆盖处明确标注。身份／权限验收不读取 KV value，也不通过写入测试权限。

下列 API 示例由安全注入器将 Token 置于当前进程内存，`printf` 必须是 shell builtin、禁止 xtrace／管道日志，安全注入器须拒绝凭据 CR/LF 并正确转义 curl 配置中的引号与反斜杠；header 通过 curl 私密配置 stdin 而非 `-H` argv，禁用会打印请求的 debug/verbose 模式；真实消费者优先直接在进程内构造 `X-Vault-Token`。

```bash
# VAULT 是已核验目标；TOKEN 由安全注入器提供，不从聊天或凭据文件读取
printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - "$VAULT/v1/auth/token/lookup-self" | jq '.data.policies'
```

如返回的 policies 不含对目标路径的写权限，先判断目标 app 是否已在对应域/区域 registry 注册，再判断用户在 GitLab repo 中是否是 Maintainer+（写）或 Developer+（读）。

#### Step A2：CRUD（KV v2 API）

```bash
# 前置：目标 VAULT 已核验；TOKEN 经授权私密注入，不硬编码或写凭据文件

# 读
printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - "$VAULT/v1/secret/data/<path>"

# 写（KV v2：先读后合并再写，否则丢失已有 key）
printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - -X POST \
  -d '{"data":{"k1":"v1","k2":"v2"}}' \
  "$VAULT/v1/secret/data/<path>"

# 写（大 payload 必走此模式：从临时文件 binary 读取，避免 argv 长度限制和空白归一化）
# 适用：path 已有几百个 key、单个 value 是几百 KB 的 base64/cert，或目标是 CN Vault
TMPFILE=$(mktemp)
printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - "$VAULT/v1/secret/data/<path>" \
  | jq --arg s "$NEW_VALUE" '{data: (.data.data | ."target.key" = $s)}' > "$TMPFILE"
printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - -H "Content-Type: application/json" -X POST \
  --data-binary @"$TMPFILE" "$VAULT/v1/secret/data/<path>"
rm -f "$TMPFILE"

# 列 key
printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - -X LIST "$VAULT/v1/secret/metadata/<path>"

# 软删（可恢复）
printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - -X DELETE "$VAULT/v1/secret/data/<path>"

# 永久删（不可恢复，谨慎）
printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - -X DELETE "$VAULT/v1/secret/metadata/<path>"
```

### 场景 B：开发者自助通过 UI 操作 Builder Vault

默认由开发者在本机 UI 完成密码／MFA 和自助操作；已授权 AI 辅助任务可按上方私密注入方法使用该身份，未授权不主动代持 OIDC Token。

**指引开发者**：

1. 先核验目标应用 registry 和集群 SecretStore。US/EU Builder 可用上表对应 UI；CN staging 按「CN staging 登录入口核验」进入实际实例，不能按 `region` 拼域名。
2. 核验该实例已配置开发者 **OIDC** 登录及回调地址；尚未配置则先补齐目标认证条件，不能改登其他 Vault。
3. 点 "Sign in with OIDC Provider" → 飞书 SSO → 回跳完成登录。
4. 登录后用 token lookup 确认是否包含 `app-{app-name}-owner` 或 `app-{app-name}-developer`。
5. 进入 Secret Engine `secret/` → 按实际环境路径（如 `staging/app/{app}/{key}`）操作。

如果开发者**没有对应的 app-owner policy**（登录后仍 denied），走「场景 C」。

### 场景 C：为新应用开通 Vault 自助权限

适用：开发者想自助管理 `{app}` 的 app-owned 密钥，但登录后没有 `app-{app}-owner` / `app-{app}-developer` policy。

**当前正确入口：`DEV/gitlab-vault-sync` registry，不是 `vault-policies`。** gitlab-vault-sync 每 15 分钟读取 registry，自动：

- 从 GitLab repo 拉 Maintainer+ / Developer+ 用户
- 生成/更新 `app-<name>-owner` 和 `app-<name>-developer` policy
- 生成/更新 Vault Identity Group，并把成员绑定到 policy

**所需输入**：
- 应用名（DNS-safe：小写字母/数字/连字符）
- GitLab repo path（如 `services/user-center`）
- 目标域：`builder` 或 `ops`
- 目标区域：`us` / `eu` / `cn`，多区域就改多个 registry

**registry 路径**：

| 域 | 区域 | 文件 |
|----|------|------|
| Builder | US/EU/CN | `k8s/builder/{us,eu,cn}/apps-registry.yaml` |
| Ops | US/EU/CN | `k8s/ops/{us,eu,cn}/apps-registry.yaml` |

以上为 registry 文件，不等同于 ArgoCD 部署目录。新 CN staging 的实际入口是
`gitlab-vault-sync/k8s/builder/cn-staging/kustomization.yaml`；本次核验其 `resources: [../cn]`
仍共享 `k8s/builder/cn/apps-registry.yaml`。修改前沿目标 Application 的 Kustomize 引用确认
当前 registry 和消费实例，不要新建假定的 `cn-staging/apps-registry.yaml`，也不要凭共享 registry 推断登录地址。

**流程**：

```bash
cd ~/Project/A4x/gitlab-vault-sync
git fetch origin main
git checkout -b "feat/register-${APP}-vault"

# 编辑对应 registry，例如 k8s/builder/us/apps-registry.yaml：
# apps:
#   - name: my-new-app
#     repo: services/my-new-app
#     regions: [us]

git add k8s/<domain>/<region>/apps-registry.yaml
git commit -m "feat: register ${APP} vault access"
git push origin "feat/register-${APP}-vault"
glab mr create --title "feat: register ${APP} vault access" --target-branch main
```

MR 合并后等待下一轮 gitlab-vault-sync reconcile（约 15 分钟），再让用户重新登录 Vault UI 或重新获取 token，并用 `lookup-self` 确认 policies。

### 场景 C-extra：添加/移除 owner 或 developer

不要改 Vault OIDC role。owner/developer 来自 GitLab repo 权限：

- 写权限 owner：把用户加为 repo Maintainer 或更高
- 读权限 developer：把用户加为 repo Developer 或更高
- 移除权限：从 repo 权限移除或降级，等待下一轮 sync

如果一个应用有多个 repo 共同维护，先确认 `apps-registry.yaml` 是否支持该 app 的期望 repo 模型；不支持时优先扩展 gitlab-vault-sync，而不是手工改 Vault。

### 场景 C-cleanup：废弃应用清理

1. 从对应 `apps-registry.yaml` 删除 app 条目，提 MR。
2. MR 合并后等待 gitlab-vault-sync 清理 orphan group/policy。
3. 密钥删除是独立高风险动作：仅在应用下线确认后，由运维用场景 A 的 token 流程删除 `secret/metadata/{env}/app/{app}`。不要把 registry 清理和密钥永久删除混在一个无确认操作里。

### 场景 D：批量操作

用户可能一次提出多区域、多路径操作。Agent 应：
1. 先列出所有待执行操作，请用户确认
2. 逐一执行，每个操作报告结果
3. 最后给汇总

## 安全规则

- **本机安全登录与授权私密注入**——可辅助正常 OAuth/OIDC；密码／MFA由用户页面输入，未授权不主动代持个人凭据，禁止聊天发送 Token。
- **禁止回显 token/密码明文**——日志、总结、确认消息都不行
- **禁止打印 KV value**——向用户展示结果只说"写入了哪些 key"
- **禁止把任何凭据写入 skill 文档 / Git 仓库**——包括测试用的密码、token、AppRole id
- **KV v2 写前必读**——直接 POST 会覆盖同路径所有 key，必须先 GET 合并再写
- **路径错误导致 404 ≠ permission denied**——KV v2 必须带 `/data/` 段
- **L1 密钥必须走审批流**——无审批单据拒绝操作

## Examples

### ❌ Bad

**1. 要求用户发送账号密码明文**
```
用户：我想在 Vault 上加个 KV
AI：请提供你的 Vault 用户名和密码。
```
**问题**：个人账号密码不应离开用户的本地环境。正确做法是用户在本机安全页面登录，已授权任务使用私密会话／credential provider 注入，禁止把 Token 发到聊天。

**2. 代替开发者登录 Builder Vault OIDC**
```
开发者：我 Vault 写不进去
AI：把你的飞书账号密码给我，我帮你登录...
```
**问题**：密码应由用户在本机页面输入；个人 Token 仅能在明确授权的任务中经私密通道使用。指导正常 OIDC 登录并检查 registry、GitLab 权限和 Token policies，不通过聊天收集凭据。

**3. 回显 token/密码明文**
```
AI：已收到 token hvs.ABCDEF12345...，开始执行
```
**问题**：token 出现在日志/总结中。应只确认"token 已收到，权限校验通过"，不回显内容。

**4. KV v2 POST 覆盖已有 key**
```bash
# 路径已有 api_key=xxx, api_secret=yyy
printf 'header = "X-Vault-Token: %s"\n' "$T" | curl -s --config - -X POST \
  -d '{"data":{"db_host":"10.0.1.100"}}' \
  "$VAULT/v1/secret/data/dapp/prod"
# 结果：新版本只剩 db_host，api_key/api_secret 丢失
```

**5. 新应用用老路径格式**
```bash
# ❌ 新应用（2026-04 之后建的）写成 <app>/<env> 层级，不匹配 Builder Vault 的 app-owner policy
-d '{"data":{...}}' "$VAULT/v1/secret/data/my-new-app/dev"
# ✅ 应该用新规范 {env}/app/<app>/<key>
-d '{"data":{...}}' "$VAULT/v1/secret/data/dev/app/my-new-app/config"
```
（⚠️ 老应用沿用 `secret/<app>/<env>/*` 老路径是允许的，不要强行迁移）

### ✅ Good

**1. 本机安全登录后私密注入 Token**
```
用户：帮我在 CN staging Builder Vault 上加个 KV 到 staging/app/my-app/config
AI：先核验应用 registry、100052802231/cn-staging 的 vault-backend 和目标实例权限。
    使用该实例已核验的 UI；只有集群内地址时，按「CN staging 登录入口核验」
    建立本机转发并核验实际登录方式及 OIDC 回调。
    目标登录入口或认证未确认前暂停，不能改用旧 CN Builder 域名。
    登录并确认目标路径权限后，按场景 A 的私密注入流程操作，不发送或回显 Token。
```

**2. 引导开发者自助登录排障（场景 B）**
```
开发者：我在 Vault 上创建 staging/app/scm/config 报 permission denied
AI：先排除路径问题——你的路径 staging/app/scm/config 格式正确（env 在前、app 在后）。

    接下来查两件事：
    1. scm 是否在对应 k8s/<domain>/<region>/apps-registry.yaml 注册
    2. 你在对应 GitLab repo 是否是 Maintainer+（写）或 Developer+（读）

    如果 registry 或 GitLab 权限缺失，走场景 C；如果都对，等下一轮 sync 后重新登录。
```

**3. KV v2 安全写入（先读后合并）**
```bash
# Step 1: 读
EXISTING=$(printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - "$VAULT/v1/secret/data/dapp/prod" | jq .data.data)
# Step 2: 合并新 key
MERGED=$(echo "$EXISTING" | jq '. + {db_host:"10.0.1.100",db_port:"5432"}')
# Step 3: 写
printf 'header = "X-Vault-Token: %s"\n' "$TOKEN" | curl -s --config - -X POST \
  -d "{\"data\":$MERGED}" "$VAULT/v1/secret/data/dapp/prod"
```

**4. 为新应用开通 owner（场景 C）**
```
用户：给 alice@a4x.io 开 my-app 应用的 Builder Vault 写权限
AI：按场景 C 流程走：
    1. 确认 my-app 的 GitLab repo 和目标域/区域
    2. 在 gitlab-vault-sync 的 k8s/<domain>/<region>/apps-registry.yaml 注册 app
    3. 提 MR，合并后等待自动 reconcile
    4. alice 需要是该 repo Maintainer+；登录后会拿到 app-my-app-owner policy
```

## 豁免

| 场景 | 条件 |
|------|------|
| 非标准 Vault 实例 | 用户提供了不在上述列表中的 Vault 地址 |
| 非 userpass/OIDC 认证 | 用户要求使用 AppRole 或其他方式直接认证 |

豁免方式：`/override skill=vault-kv-manager reason="..."`

## 相关 Skill / 资源

- `cicd-developer`：应用部署侧的 Vault 路径规范、ExternalSecret 配置
- `gitlab-vault-sync` 仓库：app owner/developer 自助权限注册与自动同步（`gitlab.addx.ai/DEV/gitlab-vault-sync`）
- `vault-policies` 仓库：平台/系统级 Policy SSOT + CI sync 配置（`gitlab.addx.ai/DEV/vault-policies`）
