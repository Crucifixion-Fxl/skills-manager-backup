---
name: credential-provider
description: A4x 内部 CLI `@a4x/credential-provider` 全生命周期助手——npm scope 路由配置、装机、飞书 SSO setup、日常 env/list/get 注入、profile --add 自动注入、refresh token 续登、排错、月度健康检查；v0.1.19+ cipher 写操作 set/edit、v0.1.20+ rm / list-collections / --json 结构化输出（AI agent 友好）/ cross-scope shadow 警告。当用户提到 credential-provider、Vaultwarden、bw CLI、凭证注入、`eval $(credential-provider env)`、setup、refresh token 过期、Master Password、SSO C3 token、`@a4x` npm scope、新增/修改/删除凭证、rotate token、离职 SOP、collection 列表、JSON 输出，或说"装一下凭证工具"、"凭证过期了"、"vault 加了字段看不到"、"shell 启动自动注入"、"credential-provider 多久没升级了"、"加一个凭证"、"改下 GITLAB_TOKEN"、"删了那个 KEY"、"我能写入哪些 org collection"、"AI 脚本拿 KEY 列表"时使用。
---

平台登录与认证 SSOT：[vaultwarden](../vaultwarden/SKILL.md)。本 Skill 保留业务流程与门禁，认证事实由平台 owner 维护，日常访问调用 `web-access`。


# credential-provider

A4x 内部 [`@a4x/credential-provider`](https://gitlab.addx.ai/public-tools/credential-provider) CLI 的 AI 调度助手——把 Vaultwarden 凭证注入当前 shell 当环境变量。本 skill 覆盖跨平台（Windows PowerShell / macOS zsh / Linux bash）从装机到日常使用到排障的全流程。

> **上游 SSOT**：命令设计见 [cli-commands.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/credential-provider/cli-commands.md)；用户文档优先见 [Vaultwarden 使用指南](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/98598e323075e3f2d293ebccc84de93e4b7df6fd/docs/user-guide/vaultwarden-guide.md)；CLI 详解见 [quickstart.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/user-guide/quickstart.md) / [troubleshooting.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/user-guide/troubleshooting.md) / [vaultwarden-web-ui.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/user-guide/vaultwarden-web-ui.md)（用户自助开 Web UI 加字段）。本 skill 是 AI 调度 cheat sheet，不镜像上游文档。

## 能力与交互边界

- 本 skill 支持 CLI 安装、SSO 登录调度、同步、凭证键名/集合查询、环境变量注入、续登和排障。
- 首次账户激活、主密码设置/输入、真实账号密码与 Token 写入由用户在网页或本地交互中完成；不得要求用户把秘密值发给 Agent。
- `profile --add` 支持配置终端启动自动注入；同步选择和写入确认由用户本人完成。新终端加载对应 shell 配置后，从该终端启动的 Codex、Claude Code 或脚本继承环境变量；已运行的进程不会自动更新。
- 浏览器账号填充由 Bitwarden 插件完成。集合创建/重命名、成员邀请、权限分配/回收由管理员在 Web UI 操作；`list-collections` 只查询，不代表具备集合管理命令。
- Vaultwarden 服务端升级不自动改变 bw CLI 推荐版本；仍使用 `2026.3.0`，新版本需独立完成兼容验证。

## 核心规则

按重要度排序。每条都来自实战教训或 ADR 决策——违反会让用户撞坑。

### 1. profile 只放 `env`，绝不放 `setup`

`profile --add` 自动写 guard 块 [`src/lib/inject-hint.ts:27-32`](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/src/lib/inject-hint.ts#L27-32)，AI **不手改** `~/.zshrc` / `~/.bashrc` / `$PROFILE`。setup 会弹浏览器 + 慢 13~25s + 失败模式多，塞进 profile 每次开 shell 都会卡。

### 2. vault 加凭证必须用 Hidden custom field（眼睛图标 👁）

Web UI 加 custom field **默认 Text 类型**（T 图标），credential-provider **不读 Text**。v0.1.10+ 会 stderr 警告"看起来像 secret 但 type=Text"——让用户改成 Hidden。规则见 [ADR-011](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/011-custom-field-type-handling.md)。

| UI 图标 | Type | credential-provider 行为 |
|---|---|---|
| T | Text | **跳过**；像 secret 名时 stderr 警告 |
| 👁 | **Hidden** | ✅ 注入为环境变量 |
| ☑ | Boolean | 跳过（元数据 flag） |
| 🔗 | Linked | 跳过（字段别名） |

### 3. bw CLI 用固定推荐版本 `@bitwarden/cli@2026.3.0`

不是 `@latest`。凭证类工具供应链安全敏感，团队统一实测过的版本。兼容范围 `>=2024.1.0 <2027.0.0`，v2025.12.0 API 变更会触发版本警告。决策见 [ADR-013](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/013-pin-bw-cli-version.md)。

### 4. PowerShell 注入语法 ≠ bash

```bash
# bash / zsh
eval $(credential-provider env)
```

```powershell
# PowerShell（Out-String 必需，避免按行切对象数组）
credential-provider env --shell powershell | Out-String | Invoke-Expression
```

PowerShell 没有 `eval`；`Invoke-Expression` 才是等价物。命令是同一份 [`inject-hint.ts:14-19`](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/src/lib/inject-hint.ts#L14-L19) 输出的 SSOT。

### 5. npm scope 路由 URL 必须 group 级 + 双引号

```bash
npm config set "@a4x:registry" "https://gitlab.addx.ai/api/v4/groups/public-tools/-/packages/npm/"
```

- **group 级**：实例级 `/api/v4/packages/npm/` 对匿名请求会 fallback 到 npmjs.org（[ADR-007](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/007-npm-endpoint-group-level.md)）
- **双引号**：PowerShell 把无引号 URL 里的 `:` 解析为 scope 分隔符报错；bash/zsh 加引号也无副作用
- 项目在 Public group `public-tools/`，**不需要** `_authToken` / PAT

### 6. refresh token 过期走 v0.1.14+ 自动续登链路

7 天 refresh token 过期那天用户开新 shell 看到一行**红字**：

```
Vaultwarden refresh token expired (X min ago). To re-authenticate via Feishu SSO, run:
  credential-provider setup
Then close this shell and open a new one (profile will auto-inject again).
```

**这是预期行为，不是 bug**。让用户跑 `credential-provider setup`（**不需要** `--force`，**不会问** SSO 标识符）→ 一次浏览器点击 → 关 shell 开新的 → profile 自动注入回来。

| 链路 | 实证 |
|---|---|
| env 解 JWT exp 验真，过期红字短路（不弹 MP）| [ADR-016](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/016-skip-mp-prompt-when-token-expired.md) + [`bw-token-status.ts`](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/src/lib/bw-token-status.ts) |
| setup case 2 自动 logout-then-relogin（不需 `--force`）| [ADR-018](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/018-setup-revalidate-expired-token.md) |
| 写死 SSO identifier `a4x` 跳 cosmetic prompt | [ADR-019](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/019-hardcode-sso-identifier.md) |

**绝不**先盲弹 MP；**绝不**让用户加 `--force`（除非真的切账户/切 server）。

### 7. `--no-sync` 何时用

v0.1.11+ 默认每次 `env` / `list` / `get` 都先 `bw sync` 拉 server 端最新 vault（SSOT 优先）。代价是 ~0.5-2s 网络往返。三种场景显式跳：

| 场景 | 加 `--no-sync` 理由 |
|---|---|
| 离线 / 弱网 | 网络拿不到也不该阻断 |
| 高频脚本（`KEY=$(... get FOO --no-sync)`） | 别每次脚本调用都 sync |
| shell rc guard 块（**用户在 `profile --add` 时主动选 opt-out**） | 多终端用户接受"开终端可能不是最新"换启动速度 |

> **v0.1.19 ADR-034 起翻转默认**：`profile --add` 默认写**不带** `--no-sync` 的 guard 块（开终端 = 最新 vault）；交互 prompt 让用户显式选 sync vs no-sync。v0.1.18 之前默认追加 `--no-sync` 的逻辑被废弃。
> （ADR-034 原编号 028，与 cipher 写命令 ADR-028 撞号 → 重编号；引用同步切到 034。）

sync 失败不阻断，会 stderr warn 后用本地缓存继续。

### 8. stderr 染色规则 = 必须看懂（ADR-020 / 022 / 027）

v0.1.18 起共 4 级。配色让 AI 和用户都能 5 秒判断"要不要处理":

| 等级 | TTY stderr | 文件落盘 | 受 `--quiet` 控 | 引入 |
|---|---|---|---|---|
| `info` | 不染色 | 不染色 | ✅ | 一直 |
| `warn` | **ANSI 黄色** | 不染色 | ✅ | v0.1.17 / [ADR-022](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/022-yellow-warn-logs.md) |
| `error` | **ANSI 红色** | 不染色（保 grep 友好）| ❌（双写）| v0.1.16 / [ADR-020](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/020-red-error-logs.md) |
| `hint` | **ANSI 青色**（bw timeout 恢复建议 / profile guard 注入摘要 / 凭证被跳过告警）| 不染色 | ❌（双写）| v0.1.18 / [ADR-027](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/027-cyan-hint-level-and-error-budget.md) + v0.1.19 / [ADR-034](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/034-profile-default-sync-and-hint-summary.md) |

排障时让用户先看 `~/.credential-provider/error.log` 红字行——profile pipeline 静默挂掉的代价 >> stderr 多一行。`hint` 三个用途（ADR-027 + ADR-034）：(a) bw timeout 的"建议先重跑一次"恢复建议（OS cache + AV 首次冷扫，第二次往往 2-4s 通过）；(b) profile guard 注入摘要 `✓ Injected N vars from <host> (synced|cached): KEY1, ...`；(c) 凭证被静默跳过的告警（Text 错填 / duplicate Hidden）。**穿透 `--quiet`**，profile pipeline 下也可见。

### 10. v0.1.20+ AI agent 拿数据用 `--json`（ADR-031）

`status` / `get` / `list` / `list-collections` 四个读命令支持 `--json` flag——失败时不输出 JSON（continue stderr human-readable，避免假阳性 parse）。AI agent / CI 脚本消费时**优先 `--json`**，但 AI 不读取、不回显、不转发 `get` 返回的真实凭证值；需要给目标进程用值时，优先使用 `env` 的进程内注入路径。`env` 不支持 `--json`，因为它输出 shell pipe `export KEY=VALUE` 是 CLI 与 shell 的契约本身。

```bash
credential-provider list --json              # {"keys":[...], "count":N, "warnings":{...}}
credential-provider get <KEY> --json         # 仅用户本人本地使用；AI 不输出返回值
credential-provider status --json            # {vaultwardenUrl, bw, session} 状态对象
credential-provider list-collections --json  # [{id, name, organizationId, organizationName, readOnly, hidePasswords}, ...]
```

### 11. v0.1.20+ cross-scope shadow 黄字警告（ADR-032）

同 KEY 同时存在 personal vault 和 org collection 时，**personal 静默覆盖 org**；v0.1.20 起加 stderr 黄字 warn 让用户知情：

```
⚠ KEY `<NAME>` shadowed: personal vault overrides org collection `<col>` (value details are intentionally omitted)
```

**不是 bug**——是设计行为（个人定制优先）。若用户想用 org 值，先确认删除 personal 值，再让用户本人执行 `rm <KEY> --yes`，或在 Web UI 改名。

> cross-scope shadow 仅通过 stderr 黄字 warn 暴露，**不在** `list --json` 的 `warnings` 对象里（只有 `suspectedTextSecrets` / `duplicateFields` 两类进了 JSON）。AI agent 需要程序化判断时捕获 stderr 看是否含 `KEY shadowed`。

### 9. CLI 升级走 `npm install -g @a4x/credential-provider@latest`

不引入 self-update-notifier（凭证类工具供应链表面 = 0）。本 skill 是唯一升级路径。月度健康检查触发词："credential-provider 多久没升级了" / "check credential-provider version"。

> **完整命令清单 + 退出码表**：见 [`references/cli-commands.md`](references/cli-commands.md)。本 SKILL.md 只覆盖高频流程；用户问"有什么命令"/"列一下命令"等枚举类问题时**先读 references**，别凭主文档印象答。

### 契约单点真值（SSOT — 当 SKILL.md 与 references 撕裂时）

skill 内部跨文件描述同一契约时，**实际命令行为 / Exit code / JSON schema 的单点真值是 [`references/cli-commands.md`](references/cli-commands.md)**（与上游源码 `src/commands/*_EXIT` 常量逐行核对过）。SKILL.md 的命令描述是 references 的精简引用，遇歧义以 references 为准；遇 references 与上游 `gitlab.addx.ai/public-tools/credential-provider/docs/architecture/credential-provider/cli-commands.md` 撕裂时以 references 的"实测来源"注脚为准（注脚指向源码常量）。

两条易撕裂边界（已校准，预防回归）：

1. **`edit` notFound = exit 1**（与 `get` exit 1 对齐）—— 不是 3。上游 cli-commands.md 一度写 3 是 doc drift，已通过 [credential-provider!70](https://gitlab.addx.ai/public-tools/credential-provider/-/merge_requests/70) 校准
2. **`list --json` 的 `warnings` 对象仅含 `suspectedTextSecrets` + `duplicateFields`** —— cross-scope shadow 只走 stderr 黄字 warn，不进 JSON；AI agent 程序化判断 shadow 需捕获 stderr 看是否含 `KEY shadowed`

## 执行流程

按用户场景分发到对应 Step。

### Step 0：探测平台

```bash
# Unix-like
echo "$SHELL" && uname -s

# Windows
$PSVersionTable.PSVersion        # PS5.1 vs PS7 影响 $PROFILE 路径
```

跨平台差异参见 [`references/platform-quirks.md`](references/platform-quirks.md)。

### Step 1：前置检查

```bash
node --version    # 需 >=18
npm --version     # 需 >=9
```

不满足让用户装 Node.js（官方 / nvm / Volta），本 skill 不代办。

### Step 2：配 npm scope 路由（一次性）

```bash
npm config set "@a4x:registry" "https://gitlab.addx.ai/api/v4/groups/public-tools/-/packages/npm/"
```

详见 [核心规则 #5](#5-npm-scope-路由-url-必须-group-级--双引号)。

### Step 3：全局安装 credential-provider + bw CLI

```bash
npm install -g @a4x/credential-provider@latest @bitwarden/cli@2026.3.0
credential-provider --version    # 应输出 0.1.x
bw --version                     # 应输出 2026.3.0
```

- `credential-provider@latest`（[核心规则 #9](#9-cli-升级走-npm-install--g-a4xcredential-providerlatest)）
- `@bitwarden/cli@2026.3.0` **固定版本**（[核心规则 #3](#3-bw-cli-用固定推荐版本-bitwardencli20263)）
- 两个一起装；bw 是 setup 运行时依赖，不是 npm install 前置

### Step 3.5：Vaultwarden 账户激活（仅首次用户）

如果你**从未访问过** https://vaultwarden.builder.addx.live ——必须先在 Web UI 走一遍激活，才能用 CLI。Step 4 的 `bw login --sso` 假设 server 端已有你的账户 + Master Password；没做这步会撞 `account not found` / `user not registered`。

1. 浏览器打开 https://vaultwarden.builder.addx.live/#/login
2. "电子邮箱地址"栏填写**飞书绑定的公司邮箱**，勾选"记住电子邮箱"
3. 点击 **"使用单点登录"** 按钮（**不是** "Log in"）→ 跳飞书 SSO
4. 首次回调时 Web UI 引导你**设置 Master Password**——记牢，后续 CLI unlock vault 用它（不是飞书密码）
5. Web UI 里能看到自己的 vault 后，回 CLI 跑 Step 4

> **org collection 共享是可选的**：只用 personal vault 跳过即可。想读 / 写**团队共享凭证**（`set --collection <id>` / `list-collections` 等场景）才需要找 A4x Vaultwarden org admin 把你加入 org（admin 在 Web UI Organizations → Manage → Members 操作）。

**为啥 CLI 不接管首次设 MP 这步**：MP 是 vault 加密的种子密钥（PBKDF2 派生主密钥用于加密 cipher），必须在客户端本地派生才能保证 server 拿不到明文。bw CLI **没有** `set-master-password` 命令，Bitwarden / Vaultwarden 把首次设 MP 绑死在 Web UI / Desktop App。

已经激活过的老用户跳过这步直接进 Step 4。

### Step 4：一次性 onboarding

```bash
credential-provider setup
```

**会做三件事**：

1. 校验 bw CLI 已装且版本兼容
2. `bw config server https://vaultwarden.builder.addx.live`（A4x Vaultwarden）
3. `bw login --sso`（**浏览器自动打开**走飞书 SSO）

setup 是状态机，4 个 case 自动分发（详见 [`references/cli-commands.md`](references/cli-commands.md) §setup-cases）。**不需要让用户输 SSO 标识符**——v0.1.15+ 写死 `a4x` 跳 cosmetic prompt。

### Step 5：日常注入

按 shell 选一种：

```bash
# bash / zsh
eval $(credential-provider env)
```

```powershell
# PowerShell
credential-provider env --shell powershell | Out-String | Invoke-Expression
```

首次会问 Master Password。验证：

```bash
credential-provider list                  # 看 vault 里有哪些 KEY
# 只检查变量是否存在，不要输出凭证值或前缀
if [[ -n "${GITLAB_TOKEN+x}" ]]; then echo "GITLAB_TOKEN is set"; else echo "GITLAB_TOKEN is not set"; fi
```

PowerShell 只输出存在性状态：

```powershell
if ($null -ne $env:GITLAB_TOKEN) { 'GITLAB_TOKEN is set' } else { 'GITLAB_TOKEN is not set' }
```

详见 [核心规则 #4](#4-powershell-注入语法--bash)。

### Step 5.5：写凭证 / 列 collection（v0.1.19+ set/edit，v0.1.20+ rm/list-collections）

vault 凭证的增 / 改 / 删有 CLI 契约；涉及真实 secret 的写入优先走 Vaultwarden Web UI，避免把值放进命令行参数。

> **secret 输入安全边界**：v0.1.20 的 `set` / `edit` 只接受 positional value，没有 stdin 或隐藏输入接口。AI **不得**把真实凭证值放进命令、聊天、日志或进程参数，也不得代用户执行写命令。需要写入、修改或轮换真实 secret 时，引导用户在 Vaultwarden Web UI 完成；本 skill 不提供可复制的 secret 写入命令。

```bash
# 增（v0.1.19+ ADR-028）

# 改（v0.1.19+ ADR-028）

# 删（v0.1.20+ ADR-030）
# 先确认目标和删除意图，再由用户本人显式执行
credential-provider rm <KEY> --yes                         # soft-delete 进 bw trash, 30 天 Web UI 可恢复

# 列 collection（v0.1.20+ ADR-033）
credential-provider list-collections                       # 给 `set --collection <id>` 选目标用
credential-provider list-collections --json                # AI agent 程序化消费
```

- **`set` 返 exit 3 conflict**（KEY 已存在）→ 默认建议改用 `edit`，**不要主动加** `--force --yes`（破坏性覆盖路径，需用户显式确认意图）
- **`edit` 返 exit 1 notFound**（KEY 不存在，与 `get` exit 1 对齐）→ 让用户先 `list` 看真实 KEY 名（大小写敏感）；或改用 `set` 创建
- **`rm` 返 exit 3 refused**（用户没传 `--yes`）→ 先展示目标并获得明确确认，再让用户本人加 `--yes` 执行；AI **不代用户追加或执行** `--yes`
- **`rm <KEY>` 误删 30 天内可恢复**：让用户去 [Vaultwarden Web UI Trash](https://vaultwarden.builder.addx.live)，CLI 不暴露 `--permanent` flag 防脚本误触发硬删除
- `set --collection <id>` 写入只读 collection → exit 4 bwFailure，server 端拒绝。让用户先跑 `list-collections` 看 `readOnly` 列再选目标
- 完整退出码表 + 调度速断见 [`references/cli-commands.md`](references/cli-commands.md)

### Step 6：自动注入到 shell rc（可选）

```bash
credential-provider profile --add
```

自动检测当前 shell 写入正确路径 + guard 块（永远带 `--quiet`；`--no-sync` 由用户在交互 prompt 中显式选才追加）。**`--add` v0.1.19 起会有两个交互 prompt：sync 取舍 + 写入确认**——AI **不代用户回 Y**，告知用户自己跑（避免安全审计追溯困难）。

撤销：

```bash
credential-provider profile --remove
```

### Step 7：refresh token 过期续登

用户看到红字提示时跑：

```bash
credential-provider setup
# 一次浏览器点击 → 关 shell 开新的
```

**不需要** `--force`、不需要先 `logout`。详见 [核心规则 #6](#6-refresh-token-过期走-v0114-自动续登链路)。

如果**真的要切账户/切 server**才用 `setup --force`。

### Step 7.5：月度健康检查

触发词："credential-provider 多久没升级了" / "check credential-provider version"。

```bash
credential-provider --version
npm view @a4x/credential-provider version
# 不一致就跑：npm install -g @a4x/credential-provider@latest
```

让一次主动调用做两件事——同时检查升级 + 处理用户当下问题。

### Step 8：SSO C3 平台扩展（可选）

需要 Troubleshooting / CS Workspace / Dagster / DAPP 等平台 token 时，配 `CP_FEISHU_*` + `CP_C3_PLATFORMS` 环境变量。详见 [`references/sso-c3.md`](references/sso-c3.md)。

### Step 9：临时锁定 / 完全登出

| 用户场景 | 命令 | 下次 env |
|---|---|---|
| 同事路过看屏幕，临时锁 | `credential-provider lock` | 输 Master Password |
| 设备给别人用，彻底清 | `credential-provider logout` | 跑 `setup`（飞书 SSO） |
| 想强制刷新 SSO refresh token | `setup --force`（or `logout` + `setup`）| 完整 SSO |

`lock` 清理本地 session 和 refresh 文件，但不撤销服务端 access/refresh token；`logout` 才执行服务端撤销。决策见 [ADR-012](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/012-lock-vs-logout-semantics.md)。

### Step 10：卸载

```bash
credential-provider profile --remove           # 先撤 shell rc 注入
npm uninstall -g @a4x/credential-provider     # 卸 CLI
```

> 如需清理 `~/.credential-provider` 下的本地数据，必须由用户明确确认后自行逐项删除；AI 不执行递归删除。

bw CLI 不强制卸——其他工具可能用到。

## 故障速查表

| 症状 | 章节 |
|---|---|
| 官方插件 SSO 登录循环 / 密码库为空 | 先核对自托管 URL、SSO 解锁和同步；共享条目检查集合授权。升级后仍循环可由用户尝试重装插件，先确认无未同步内容且能够重登。见上游 Web UI 文档；不自动 logout CLI，也不把重装当作根因证明 |
| 红字 `Vaultwarden refresh token expired` | [核心规则 #6](#6-refresh-token-过期走-v0114-自动续登链路) — 让用户跑 `setup` |
| `bw CLI is not installed or not in PATH` | [Step 3](#step-3全局安装-credential-provider--bw-cli) — 装 `@bitwarden/cli@2026.3.0` |
| `bw version outside compatible range` 黄字警告 | [核心规则 #3](#3-bw-cli-用固定推荐版本-bitwardencli20263) — 降级到 `@2026.3.0` |
| `Not logged in to Vaultwarden` exit 2 | [Step 4](#step-4一次性-onboarding) — 跑 `credential-provider setup` |
| 新人首跑 setup 报 `account not found` / `user not registered` | [Step 3.5](#step-35vaultwarden-账户激活仅首次用户) — 先在 Web UI 走飞书 SSO 设 MP，CLI 不接管这步 |
| vault 加了字段看不到 / `1 field looks like a secret but is type=Text` | [核心规则 #2](#2-vault-加凭证必须用-hidden-custom-field眼睛图标-) — 改成 Hidden |
| 黄字 `⚠ N Hidden field defined in multiple items ...` | 跨 item 重名 Hidden 字段，先到先得（[ADR-021](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/021-duplicate-hidden-field-warning.md)）。让用户改名或删一个 |
| `bw command timed out after 30000ms` + 青字 hint | v0.1.18 起统一 5 行新格式，先按 hint 重跑一次；详见 [`references/troubleshoot.md`](references/troubleshoot.md) §3 |
| PowerShell 输出 5 行红字 `Invoke-Expression : ... 空 pipe` | v0.1.14+ 已修，让用户升级到 v0.1.16+ |
| npm install 报 `404 Not Found @a4x/credential-provider` | [核心规则 #5](#5-npm-scope-路由-url-必须-group-级--双引号) — group 级 URL + 双引号 |
| bw command timeout / LevelDB 锁竞争 / "Logout required" | [`references/troubleshoot.md`](references/troubleshoot.md) |
| Windows PS5.1 vs PS7 / Git Bash / WSL profile 路径 | [`references/platform-quirks.md`](references/platform-quirks.md) |
| `set` 返 exit 3 `conflict`（KEY 已存在） | [Step 5.5](#step-55写凭证--列-collectionv0119-setedit-v0120-rmlist-collections) — 让用户在 Vaultwarden Web UI 修改；AI 不执行覆盖写入 |
| `edit` 返 exit 1 `notFound`（KEY 不在 vault；与 `get` exit 1 对齐） | [Step 5.5](#step-55写凭证--列-collectionv0119-setedit-v0120-rmlist-collections) — 先 `list` 看真实 KEY 名（大小写敏感）|
| `rm` 返 exit 3 `refused`（缺 `--yes`） | [Step 5.5](#step-55写凭证--列-collectionv0119-setedit-v0120-rmlist-collections) — 让用户自己加 `--yes`，AI 不代回 |
| `set --collection <id>` 返 exit 4 bwFailure（只读 / 无权限） | [Step 5.5](#step-55写凭证--列-collectionv0119-setedit-v0120-rmlist-collections) — 让用户先 `list-collections` 看 readOnly 列 |
| 黄字 `⚠ KEY ... shadowed: personal vault overrides org collection` | [核心规则 #11](#11-v0120-cross-scope-shadow-黄字警告adr-032) — 设计行为，不是 bug；想用 org 值就在 personal 里 rm 同名 KEY |
| AI agent / CI 脚本拿 KEY 列表 / 凭证值要稳定 parse | [核心规则 #10](#10-v0120-ai-agent-拿数据用---jsonadr-031) — 用 `--json` flag，别正则 stdout |

## 示例

### ❌ Bad：把 setup 写进 shell profile

```bash
# 用户问"怎么开终端自动登录"，AI 不要这样回
echo 'credential-provider setup' >> ~/.zshrc       # ⛔ 每次开 shell 弹浏览器，慢 13~25s
echo 'eval $(credential-provider env)' >> ~/.zshrc
```

### ✅ Good：让 CLI 自己写 guard 块

```bash
credential-provider profile --add
# v0.1.19+: 默认写 eval $(credential-provider env --quiet)（不带 --no-sync）
# 选 opt-out 时写 eval $(credential-provider env --quiet --no-sync)
# 装机一次跑 setup；profile 只管注入
```

### ❌ Bad：refresh token 过期，加 `--force` 或先 `logout`

```bash
# v0.1.13 之前的老办法，v0.1.14+ 不再需要
credential-provider logout
credential-provider setup --force                  # ⛔ 多余步骤
```

### ✅ Good：直接跟红字提示跑

```bash
credential-provider setup       # case 2 自动 logout-then-relogin（ADR-018）
# 一次浏览器点击 → 关 shell 开新的
```

### ❌ Bad：vault 加 secret 用默认 Text 类型

```
Web UI → Custom Fields → Add → Name: GITLAB_TOKEN, Value: 由用户在密码框中输入
       Type: T (Text)  ⛔ credential-provider 不读
```

### ✅ Good：手动切到 Hidden 类型

```
Web UI → Custom Fields → Name: GITLAB_TOKEN, Value: 由用户在密码框中输入
       点击 type 图标 → 选 👁 (Hidden) → 保存
       → credential-provider list 出现，env 可注入
```

## 验证

本 skill 改动后跑：

```bash
cd D:/Projects/engineering/skills
uv run python scripts/validate.py --skill skills/security/credential-provider
uv run python scripts/validate.py --skill skills/security/credential-provider --security
```

通过才提 MR。
