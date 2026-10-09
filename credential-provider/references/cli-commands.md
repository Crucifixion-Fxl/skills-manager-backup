# cli-commands.md — AI 调度 cheat sheet

> **上游 SSOT**：[gitlab.addx.ai/public-tools/credential-provider/docs/architecture/credential-provider/cli-commands.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/credential-provider/cli-commands.md)（v0.1.20，~550 行）。
>
> **本文件用途**：AI 调度时一眼看清"调哪条命令 / 会副作用什么 / 错误码怎么解"。完整 synopsis 和 option 描述去上游看。
>
> **本文件是 skill 内机读契约（Exit code / JSON schema）的单点真值**：注脚指向上游源码 `src/commands/*_EXIT` 常量。当 SKILL.md / references/troubleshoot.md / 上游 cli-commands.md 之间撕裂时**以本文件为准**（详见 [SKILL.md 契约 SSOT 段](../SKILL.md#契约单点真值ssot--当-skillmd-与-references-撕裂时)）。

## 契约一致性自检清单（每次改 skill 必跑）

跨文件描述同一契约时**强制**对照以下表格 grep 全 skill 目录确保单一口径。Code-review 反馈：本 MR 历史上撞过 4 次同类回归（edit notFound / cross-scope shadow 描述漂移），加自检清单防进一步累积。

| 高风险字段 | 单一口径来源 | 必 grep 关键词（应只在单一来源出现） |
|---|---|---|
| `edit` notFound exit code | 本文件 §`edit` 表 = **1** | `exit \d notFound\|notFound.*exit \d` 全 skill 应只 match 本文件 §edit + SKILL.md Step 5.5 + 故障速查表，**数字必须一致为 1** |
| `set` conflict exit code | 本文件 §`set` 表 = **3** | 同上，conflict 数字必须一致为 3 |
| `rm` refused exit code | 本文件 §`rm` 表 = **3** | 同上，refused 数字必须一致为 3 |
| `list --json` warnings 字段集 | 本文件 §命令一览 list --json 行 = `suspectedTextSecrets + duplicateFields` 二者；**cross-scope shadow 不进** | grep `warnings 含\|warnings\.cross\|crossScopeShadows` 全 skill 应零 match（注脚解释段除外） |
| `get --json` schema | 本文件 §命令一览 = `{key, value}` | 全 skill 出现"get --json"+ schema 应一致 |
| `status --json` schema | 本文件 §命令一览 = `{vaultwardenUrl, bw, session}` | 同上 |

## 命令一览

| 命令 | 用途 | 交互 | 网络 | 会触发 `bw sync` |
|---|---|---|---|---|
| `setup` | 一次性 onboarding（配 server + SSO 登录） | ✅ 浏览器 + MP | ✅ | — |
| `setup --force` | 强制 logout + 重登（切账户/server） | ✅ | ✅ | — |
| `setup --shell <type>` | 同上，指定完成消息形态（bash/zsh/powershell；默认自动检测） | ✅ | ✅ | — |
| `env` | 输出 export 语句到 stdout（日常注入） | 首次输 MP | ✅ | ✅ 默认 |
| `env --no-sync` | 同上，跳 sync（离线 / 高频脚本 / shell rc） | 首次输 MP | ✅ | ❌ |
| `env --shell powershell` | PowerShell 形态 export 语句 | 同上 | 同上 | 同上 |
| `env --quiet` | 抑制 stderr，错误写日志（profile 用） | 同上 | 同上 | 同上 |
| `env --export-file <path>` | **禁止用于真实 secret**；不得落盘或跨边界传输完整环境。Sandbox 仅允许在受控 broker 内按需进程内注入 | — | — | — |
| `env --no-sso-token` | 跳过 C3 类 SSO token 获取 | 同上 | 同上 | 同上 |
| `list` | 列 vault 里所有可用 KEY 名 | 首次输 MP | ✅ | ✅ 默认 |
| `list --json` | **v0.1.20+** 结构化 `{keys, count, warnings}`（warnings 仅含 `suspectedTextSecrets` + `duplicateFields`；cross-scope shadow 仅走 stderr warn 不进 JSON）| 同上 | 同上 | 同上 |
| `get <KEY>` | 单个凭证值（仅用户本人本地使用；AI 不读取或回显） | 首次输 MP | ✅ | ✅ 默认 |
| `get <KEY> --json` | **v0.1.20+** 结构化输出（仅用户本人本地使用） | 同上 | 同上 | 同上 |
| `lock` | 锁 vault：清理本地 session/refresh 文件 + `bw lock`，不撤销服务端 token | ❌ | ❌ | ❌ |
| `logout` | 完全登出：lock + `bw logout`（撤 token） | ❌ | ✅ 撤 token | ❌ |
| `profile` | 查询 shell rc 注入状态 | ❌ | ❌ | ❌ |
| `profile --add` | 写 guard 块到 shell rc。v0.1.19+ 默认 `--quiet`（sync 默认开）；用户在交互 prompt 选 opt-out 时追加 `--no-sync` | ✅ **两个 Y/n 确认**（sync + write） | ❌ | ❌ |
| `profile --remove` | 移除 guard 块 | ❌ | ❌ | ❌ |
| `status` | 显示 bw / session / Vaultwarden URL 状态报告 | ❌ | ✅ | ❌ |
| `status --json` | **v0.1.20+** 结构化 `{vaultwardenUrl, bw: {installed, version, compatible, compatibleRange}, session: {present, valid, createdAt}}` | ❌ | ✅ | ❌ |
| `set` | **v0.1.19+** 创建新 cipher；当前 CLI 将 value 放在 argv，AI 不执行 secret 写入；引导用户使用 Vaultwarden Web UI | 首次输 MP | ✅ 写 server | ✅ 默认 |
| `set --collection <id>` | 写到指定 org collection；AI 不执行 secret 写入，用户使用 Vaultwarden Web UI | 同上 | 同上 | 同上 |
| `set --force --yes` | 覆盖已有 KEY；AI 不执行 secret 写入，用户使用 Vaultwarden Web UI | 同上 | 同上 | 同上 |
| `edit` | **v0.1.19+** 改已有 KEY 的值；当前 CLI 将 value 放在 argv，AI 不执行 secret 写入；引导用户使用 Vaultwarden Web UI | 首次输 MP | ✅ 写 server | ✅ 默认 |
| `rm <KEY> --yes` | **v0.1.20+** 用户明确确认后由用户本人执行；soft-delete 进 bw trash（30 天 Web UI 可恢复；`--yes` 必填） | 首次输 MP | ✅ 写 server | ✅ 默认 |
| `list-collections` | **v0.1.20+** 列用户可见的 org collections（id/name/orgName/readOnly/hidePasswords）| 首次输 MP | ✅ | ✅ 默认 |
| `list-collections --json` | **v0.1.20+** 结构化数组 | 同上 | 同上 | 同上 |
| `--version` | 输出 `credential-provider 0.1.x`（升级路径用） | ❌ | ❌ | — |

> "首次输 MP"指 session 失效后第一次调用；session 还活则 🟢 直接出。

## Exit codes

按命令分。AI 拿到 exit code 后按下表分派下一步。

### `setup`

| Code | 含义 | AI 处理 |
|---|---|---|
| 0 | 成功（或已配置） | continue |
| 1 | bw CLI 未装 | 让用户跑 `npm install -g @bitwarden/cli@2026.3.0` |
| 2 | `bw config server` / `bw logout` / 读 server 失败 | 看 `error.log` 红字行；多半 bw 状态损坏 |
| 3 | `bw login --sso` 失败（用户取消 / 网络 / 飞书账号问题） | 让用户重试，或查浏览器 callback URL |
| 4 | **serverConflict**：登在其他 server，需先 logout（v0.1.8+） | 让用户加 `--force` |

### `env`

| Code | 含义 | AI 处理 |
|---|---|---|
| 0 | 成功输出 export 语句 | continue |
| 1 | bw CLI 缺失 / 不可用 | 同 setup 1 |
| 2 | 未通过 bw login | 让用户跑 `credential-provider setup`（**不需要** `--force`，[ADR-018](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/018-setup-revalidate-expired-token.md)）|
| 3 | Unlock 失败（MP 错） | 让用户重输 |
| 4 | Vaultwarden 不可达 | 检查网络 / vaultwarden.builder.addx.live 可达性 |
| 5 | 凭证读取失败 | 看 `error.log` 红字行 |

### `get`

| Code | 含义 | AI 处理 |
|---|---|---|
| 0 | 成功输出值 | continue |
| 1 | 凭证不存在 | 让用户跑 `credential-provider list` 看真实 KEY 名 |
| 2 | session 无效 | 让用户先 `eval $(credential-provider env)` 注入或重新 setup |

### `list`

| Code | 含义 |
|---|---|
| 0 | 成功（含 0 条） |
| 2 | session 无效（处理同 get 2） |

### `profile`

| Code | 含义 | AI 处理 |
|---|---|---|
| 0 | 成功 | continue |
| 1 | profile 文件不可写 | 检查权限 / 文件是否存在 |
| 2 | profile 中存在残缺 guard 标记 | 让用户手动清理 `# >>> credential-provider >>>` ~ `# <<<` 块 |
| 3 | 用户在 `--add` 交互中取消 | 不重试，尊重用户选择 |

### `status` / `lock` / `logout`

`status`：0 正常 / 1 有问题（看 stderr 详情）。`lock`、`logout`：固定 0（幂等）。

### `set`（v0.1.19+，ADR-028 Phase 1）

| Code | 含义 | AI 处理 |
|---|---|---|
| 0 | 成功创建（或 `--force --yes` 覆盖完成）| continue |
| 2 | session 无效 | 让用户先 `eval $(credential-provider env)` |
| 3 | **conflict**：KEY 已存在且无 `--force` | 让用户在 Vaultwarden Web UI 修改；AI 不执行覆盖写入|
| 4 | bwFailure：bw create / listCollections / list items 失败（timeout / network / collection 不存在）| 看 `error.log` 红字行；timeout 路径有 cyan retry hint 建议先再跑一次 |

### `edit`（v0.1.19+，ADR-028 Phase 1）

| Code | 含义 | AI 处理 |
|---|---|---|
| 0 | 成功更新 | continue |
| 1 | **notFound**：KEY 不在 vault（与 `get` exit 1 对齐）| 让用户先 `credential-provider list` 看真实 KEY 名；或者用 `set` 创建新的 |
| 2 | session 无效 | 同 get 2 |
| 4 | bwFailure | 同 set 4 |

> 实测来源：`src/commands/edit.ts` `EDIT_EXIT.notFound = 1`。上游 [cli-commands.md §2.12](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/credential-provider/cli-commands.md#212-credential-provider-edit) 一度写 exit 3 notFound 是文档错（与代码 drift）；以代码为准。

### `rm`（v0.1.20+，ADR-030 Phase 2）

| Code | 含义 | AI 处理 |
|---|---|---|
| 0 | 成功 soft-delete 进 bw trash（30 天 Web UI 可恢复） | continue |
| 1 | bw CLI 缺失 / KEY 不存在 | 同 setup 1 或先 `list` 看真实 KEY 名 |
| 2 | session 无效 | 同 get 2 |
| 3 | **refused**：缺 `--yes` | 让用户自己加 `--yes`，AI **不代回**（即使可恢复也是不可逆动作）|
| 4 | bwFailure：bw delete item 失败 | 看 `error.log` 红字行；timeout 路径有 cyan retry hint |
| 5 | Vaultwarden 不可达 | 检查网络 |

> CLI **永远不调 `--permanent`**——硬删除仅走 Web UI。混合 item（多 fields）会只 patch 删除目标 Hidden field，保留其它字段（不破坏用户在 Web UI 看到的熟悉条目结构）。

### `list-collections`（v0.1.20+，ADR-033）

| Code | 含义 | AI 处理 |
|---|---|---|
| 0 | 成功（含 0 条）| continue；用户不属于任何 org 时 stderr `No accessible collections.` |
| 2 | session 无效 | 同 get 2 |

> `readOnly: true` 的 collection 不能作为 `set --collection <id>` 目标——server 端会拒（set exit 4 bwFailure）。`hidePasswords: true` 用户能列 cipher 但 value 被打码（不影响 set/edit 权限本身）。

## AI 调度速断（13 条）

1. **看到红字 `Vaultwarden refresh token expired`**：直接让用户跑 `credential-provider setup`（一次浏览器点击搞定）。**不要**先弹 MP / 加 `--force` / `logout` 再 setup。
2. **`env` 返 exit 2**：等价"未登录"。让用户跑 `setup`，不是 `--force`。
3. **`env` 返 exit 4**：网络问题，不是状态问题。检查 vaultwarden.builder.addx.live。
4. **`setup` 返 exit 4**：登在其他 server。让用户加 `--force`（这是 setup 唯一推荐 `--force` 的场景）。
5. **`get <KEY>` 返 exit 1**：用户记错 KEY 名。让用户先 `list` 看真实 KEY 名（vault 里的字段名是大小写敏感的）。
6. **用户说"新加一个凭证 X 值 Y"**：AI 不代填或执行 `set X Y`，因为当前 CLI 会把 value 放进 argv；引导用户在 Vaultwarden Web UI 写入，或由用户本人在本地替换占位符后执行。需要共享到 org 时先用 `list-collections` 选 collection。
7. **`set` 返 exit 3 conflict**：KEY 已存在。AI 不代执行 `edit` 或 `--force --yes`；先说明 argv 暴露风险，引导用户在 Web UI 修改，或由用户本人在本地执行占位符命令并自行确认。
8. **用户说"rotate / 改 X 的值"**：引导用户在 Vaultwarden Web UI 修改；当前 CLI 的 `edit X <new>` 仅供用户本人本地替换占位符后执行，AI 不代填真实值。**不要**用 set + --force（语义错位）。
9. **用户说"删了 X" / "清掉那个 token"**：先展示目标并请求明确确认；确认后让用户本人执行 `rm X --yes`。AI **不代用户追加或执行** `--yes`。误删 30 天内去 [Web UI Trash](https://vaultwarden.builder.addx.live) 恢复。
10. **`set --collection <id>` 返 exit 4 bwFailure**：collection 不存在或只读权限。让用户先 `list-collections` 看 `readOnly` 列再选目标。
11. **AI agent / CI 脚本要稳定 parse**：用 `--json` flag（v0.1.20+ ADR-031，覆盖 status/get/list/list-collections）。失败时**不输出 JSON**，stderr 走 human-readable，避免假阳性 parse。**不要**对 stdout 文本做正则。
12. **黄字 `⚠ KEY ... shadowed: personal vault overrides org collection`**：v0.1.20+ ADR-032。设计行为，不是 bug；想用 org 值时，先确认删除 personal 值，再由用户本人执行 `rm <KEY> --yes`，或在 Web UI 改名。
13. **用户问"我能往哪个 collection 写"**：跑 `list-collections`（或 `--json` 给 AI agent）；看 `readOnly` 列筛可写的；`hidePasswords` 不影响写权限。

## 会话生命周期三态

```
跑 `credential-provider env`
  ├─ 🟢 session 还活 + bw refresh 还活 → 直接出凭证
  ├─ 🔵 session 失效但 bw refresh 还活 → 输 Master Password
  └─ 🟡 bw refresh 也过期（默认 7 天 rolling）→ 红字提示跑 `setup`
```

refresh token **rolling**：7 天内至少用一次就永不过期。

## 不写的内容（去上游看）

- 完整 option synopsis 和默认值 → 上游 §2.x
- setup 4 case 状态机详细分发 → 上游 [setup-flow.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/credential-provider/setup-flow.md) + [ADR-010](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/010-setup-state-machine.md)
- env / setup / list / get 内部 bw 子进程调用顺序 → 上游 §1.0.1 + bw-integration.md
- profile guard 块的具体跨 shell 路径 → 上游 [profile-command.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/credential-provider/profile-command.md)
- env 输出格式（`export KEY="value"` vs `$env:KEY = "..."`）→ 上游 [env-flow.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/credential-provider/env-flow.md)
