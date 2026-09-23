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

A4x 有 **6 个 Vault 实例**（每个区域 Ops + Builder 各一个），分两套体系：

### Ops 域 Vault（线上 prod 密钥）—— 运维专属

| 区域 | 地址 | 服务集群 | 用途 |
|------|------|---------|------|
| US | `vault-us-new.addx.live` | us-prod, us-prod-gke, us-data | C 端 prod 密钥 |
| EU | `vault-eu.addx.live` | eu-prod, eu-data | C 端 prod 密钥 |
| CN | `vault-cn.addx.live` | cn-prod, cn-k8s, sg-devops | C 端 prod 密钥 |

> **开发者无法登录 Ops Vault**——Ops Vault 只配置运维相关 policy，没有开发者角色。应用读取 prod 密钥通过 K8s ExternalSecret + ClusterSecretStore `vault-backend` 间接完成，Pod 不直连 Vault API。

### Builder 域 Vault（开发者自助）—— 每区域单实例，不分 prod/nonprod

| 区域 | 公网地址 | 集群内访问（同集群 Pod） | 服务集群 |
|------|---------|---------|---------|
| US | `vault-us.builder.addx.live` | 同集群优先 `http://vault-builder-active.vault-builder.svc.cluster.local:8200` | us-staging 等 Builder 域集群 |
| EU | `vault-eu.builder.addx.live` | 同集群优先 `http://vault-builder-active.vault-builder.svc.cluster.local:8200` | eu-staging 等 Builder 域集群 |
| CN | `vault-cn.builder.addx.live` | 同集群：`vault-builder-active.vault-builder.svc.cluster.local:8200`；跨集群（如 cn-dev → cn-tech）：`https://vault-cn-internal.builder.addx.live` | cn-tech-service, cn-dev, cn-staging |

> Builder 域已覆盖 CN/US/EU。`gitlab-vault-sync` 目前按 `k8s/{builder,ops}/{cn,us,eu}/apps-registry.yaml` 分域、分区域注册应用；不要再假设海外 Builder Vault 只是占位。

KV engine 统一：`secret/` (KV v2)。

> **默认 Vault = 先按目标环境/集群判定域**。prod/tech/data/sg-devops 等 Ops 域负载通常用 `vault-{us-new,eu,cn}.addx.live`；dev/staging/sandbox 等 Builder 域集群用 `vault-{us,eu,cn}.builder.addx.live`。历史应用可保留 Ops Vault 老路径；新应用和自助权限以 `gitlab-vault-sync` registry 为准。

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
| 运维操作 Ops Vault | userpass | 用运维个人账号登录，AI 操作时从浏览器复制 token（见下方说明） |
| 运维操作 Builder Vault | userpass 或 OIDC | 同上；Builder Vault 也可用 OIDC admin role |
| 开发者操作 Builder/Ops app-owned Vault | OIDC 飞书 SSO | 权限来自 gitlab-vault-sync 同步的 Identity Group；登录后以 token policies 为准 |
| CI/CD 同步 | AppRole | role_id/secret_id 在 GitLab CI Variables |
| K8s ESO 读取 | JWT | `external-secrets` ServiceAccount，ClusterSecretStore 自动处理 |

### AI 辅助操作 Vault 的标准方式：浏览器复制 token

AI 没有 Vault 账号，**不能直接登录**。运维/开发者需要先在浏览器登录 Vault，把 token 复制给 AI，AI 用这个 token 做 API 调用。

**复制 token 步骤**：

1. 浏览器打开目标 Vault UI：
   - Ops Vault 示例: `https://vault-us-new.addx.live/ui`、`https://vault-eu.addx.live/ui`、`https://vault-cn.addx.live/ui`
   - Builder Vault 示例: `https://vault-{us,eu,cn}.builder.addx.live/ui`
2. 选认证方式登录：
   - 运维用 userpass（Method → Username）
   - 开发者用 OIDC 飞书 SSO（Method → OIDC；如实例要求 Role，使用平台提供的通用 OIDC role，不再手建每 app role）
3. 登录成功后，右上角头像 → **Copy token**
4. 把 token 粘贴给 AI（形如 `hvs.XXXXXXXXXXXXX...`）

**安全规则**：

- Token 有 TTL（OIDC 默认 8h，userpass 默认 24h），过期需重新复制
- AI 收到 token 后**不得在任何输出中回显 token 明文**（日志、总结、确认消息均不可）
- 任务结束后 token 自然过期即可。**UI 的 "Log out" 只清浏览器 session，不会 revoke token**；如需提前作废，必须用 token 调 API：`curl -sH "X-Vault-Token: $TOKEN" -X POST "$VAULT/v1/auth/token/revoke-self"`
- **严禁让用户把账号密码直接发给 AI** —— AI 代登录会导致个人凭据泄漏

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

#### Step A1：向用户要 token（不要要密码）

明确告诉用户复制 token 的步骤（见上方"AI 辅助操作 Vault 的标准方式"）。收到后验证：

```bash
# 验证 token 有效（替换 VAULT 和 TOKEN 为实际值，TOKEN 不回显）
curl -sH "X-Vault-Token: $TOKEN" "$VAULT/v1/auth/token/lookup-self" | jq '.data.policies'
```

如返回的 policies 不含对目标路径的写权限，先判断目标 app 是否已在对应域/区域 registry 注册，再判断用户在 GitLab repo 中是否是 Maintainer+（写）或 Developer+（读）。

#### Step A2：CRUD（KV v2 API）

```bash
# 前置（每次脚本前 export）：
# export TOKEN=hvs.xxxxxxxxxxxxxxxx   # 用户从浏览器复制的
# export VAULT=https://vault-us-new.addx.live   # 目标 Vault 地址

# 读
curl -sH "X-Vault-Token: $TOKEN" "$VAULT/v1/secret/data/<path>"

# 写（KV v2：先读后合并再写，否则丢失已有 key）
curl -sH "X-Vault-Token: $TOKEN" -X POST \
  -d '{"data":{"k1":"v1","k2":"v2"}}' \
  "$VAULT/v1/secret/data/<path>"

# 写（大 payload 必走此模式：从临时文件 binary 读取，避免 argv 长度限制和空白归一化）
# 适用：path 已有几百个 key、单个 value 是几百 KB 的 base64/cert，或目标是 CN Vault
TMPFILE=$(mktemp)
curl -sH "X-Vault-Token: $TOKEN" "$VAULT/v1/secret/data/<path>" \
  | jq --arg s "$NEW_VALUE" '{data: (.data.data | ."target.key" = $s)}' > "$TMPFILE"
curl -sH "X-Vault-Token: $TOKEN" -H "Content-Type: application/json" -X POST \
  --data-binary @"$TMPFILE" "$VAULT/v1/secret/data/<path>"
rm -f "$TMPFILE"

# 列 key
curl -sH "X-Vault-Token: $TOKEN" -X LIST "$VAULT/v1/secret/metadata/<path>"

# 软删（可恢复）
curl -sH "X-Vault-Token: $TOKEN" -X DELETE "$VAULT/v1/secret/data/<path>"

# 永久删（不可恢复，谨慎）
curl -sH "X-Vault-Token: $TOKEN" -X DELETE "$VAULT/v1/secret/metadata/<path>"
```

### 场景 B：开发者自助通过 UI 操作 Builder Vault

**默认让开发者自己通过 UI 操作**——AI 不代持开发者 OIDC token。

**指引开发者**：

1. 打开 `https://vault-{region}.builder.addx.live/ui`
2. Method: **OIDC**
3. 登录后用 token lookup 确认是否包含 `app-{app-name}-owner` 或 `app-{app-name}-developer`
4. 点 "Sign in with OIDC Provider" → 飞书 SSO → 回跳完成登录
5. 进入 Secret Engine `secret/` → 按规范路径 `dev/app/{app}/{key}` 创建

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

- **AI 不代登录**——让用户在浏览器登录后复制 token 给 AI；严禁要求用户发账号密码
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
**问题**：个人账号密码不应离开用户的本地环境。正确做法是让用户在浏览器登录 Vault UI，Copy token 发给 AI。

**2. 代替开发者登录 Builder Vault OIDC**
```
开发者：我 Vault 写不进去
AI：把你的飞书账号密码给我，我帮你登录...
```
**问题**：OIDC token 是个人身份，AI 不能代持。正确做法是指导开发者自己登录 UI，并排查 registry / GitLab 权限 / token policies。

**3. 回显 token/密码明文**
```
AI：已收到 token hvs.ABCDEF12345...，开始执行
```
**问题**：token 出现在日志/总结中。应只确认"token 已收到，权限校验通过"，不回显内容。

**4. KV v2 POST 覆盖已有 key**
```bash
# 路径已有 api_key=xxx, api_secret=yyy
curl -sH "X-Vault-Token: $T" -X POST \
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

**1. 让用户在浏览器复制 token**
```
用户：帮我在 staging Builder Vault 上加个 KV 到 dev/app/my-app/config
AI：需要你先在浏览器登录 Vault 把 token 复制给我：
    1. 打开 https://vault-cn.builder.addx.live/ui
    2. Method: OIDC 登录
    3. 登录成功后，右上角头像 → Copy token
    4. 把 token 粘贴给我（形如 hvs.XXXXX）
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
EXISTING=$(curl -sH "X-Vault-Token: $TOKEN" "$VAULT/v1/secret/data/dapp/prod" | jq .data.data)
# Step 2: 合并新 key
MERGED=$(echo "$EXISTING" | jq '. + {db_host:"10.0.1.100",db_port:"5432"}')
# Step 3: 写
curl -sH "X-Vault-Token: $TOKEN" -X POST \
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
