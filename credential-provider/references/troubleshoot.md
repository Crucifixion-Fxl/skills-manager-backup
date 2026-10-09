# troubleshoot.md — credential-provider 排障

> **上游 SSOT**：[troubleshooting.md](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/user-guide/troubleshooting.md)（497 行）。本文按"AI 拿到症状能 5 秒分派出处理路径"的取舍精简，按用户撞到频率排序。

## 1. refresh token 过期续登（最常见）

### 1.1 症状

7 天 rolling refresh 真过期那天，开新 shell 看到红字：

```
Vaultwarden refresh token expired (X min ago). To re-authenticate via Feishu SSO, run:
  credential-provider setup
Then close this shell and open a new one (profile will auto-inject again).
```

### 1.2 处理（v0.1.14+）

```
credential-provider setup
# 浏览器自动弹 → 点一次飞书授权 → 完事
# 然后关闭当前 shell 打开新的
```

**就这一步**。无需：

| 旧办法 | 为什么不需要 | 实证 |
|---|---|---|
| 先输 Master Password | env 用 JWT exp 验真，过期直接红字短路 | [ADR-016](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/016-skip-mp-prompt-when-token-expired.md) |
| 加 `--force` | setup case 2 自动 logout-then-relogin | [ADR-018](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/018-setup-revalidate-expired-token.md) |
| 输 SSO 标识符 | 写死 `a4x` 跳 prompt（server 端不校验）| [ADR-019](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/019-hardcode-sso-identifier.md) |

### 1.3 浏览器 OAuth 失败

如果浏览器没自动打开，看 stderr 里的 callback URL 自己手动访问。还不行：

1. 浏览器是否能访问 `https://vaultwarden.builder.addx.live`
2. 飞书账号是否在 A4x Vaultwarden Organization
3. `~/.credential-provider/error.log` 取详细 log

## 2. 如何识别错误等级（ADR-020 / 022 / 027）

v0.1.18 起共 4 级 logger。颜色 = 处理优先级：

| 在哪看 | 颜色/等级 | 处理 |
|---|---|---|
| TTY stderr | **红字** = `error` | 必处理，多半阻断后续 |
| TTY stderr | **黄字** = `warn`（v0.1.17+ / [ADR-022](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/022-yellow-warn-logs.md)） | 可参考，不阻断；profile 静默路径会被 `--quiet` 滤掉 |
| TTY stderr | **青字** = `hint`（v0.1.18+ / [ADR-027](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/027-cyan-hint-level-and-error-budget.md) + v0.1.19 / [ADR-034](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/034-profile-default-sync-and-hint-summary.md)） | (1) bw timeout 恢复建议，(2) profile guard 注入摘要 `✓ Injected N vars from <host> (synced\|cached): KEY1, ...`，(3) 凭证被静默跳过告警；**穿透 `--quiet`** |
| TTY stderr | 不染色 = `info` | 状态/进度提示，不阻断 |
| `~/.credential-provider/error.log` | 不染色（保 grep 友好）| 按时间倒序看最新 error / hint |

`error` 和 `hint` **不受 `--quiet` 控**——双写 stderr + file。`info` / `warn` 受 quiet 控，profile pipeline 里只剩 error + hint 喷出来。

## 3. bw command timeout

### 3.1 症状（v0.1.18 新 5 行格式）

v0.1.18 起 `env` / `list` / `get` 路径撞 timeout 的 stderr 现场标准化为 5 行（[ADR-027](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/027-cyan-hint-level-and-error-budget.md)）：

```
<context>: bw ... timed out after 30000ms                          ← 红 error
  Diagnostic snapshot: ~/.credential-provider/timeout-diag-<ts>.log ← 红 error
Just re-run the command — first invocation often warms OS cache    ← 青 hint
+ AV scan; the second usually succeeds in 2-4s. If retry also     ← 青 hint
fails, see the diagnostic snapshot path printed above ...          ← 青 hint
```

完整 mitigation 步骤搬到 `timeout-diag-<ts>.log` header 的 `=== Mitigation steps ===` section。**先按青字 hint 让用户重跑一次** —— 一半以上场景就解决了。

> **nuance**：ADR-027 当前只迁移了 `handleBwError`（list / get / env.fetchItems / ensureBwReady 4 处）。`setup` / `logout` / `set-server` / `syncBwVault` / `unlockInteractive` / `bw-sync` 这 6 处仍走老 17 行文案。setup 路径用户暂时还看不到 5 行格式。

`list` / `get` 撞 timeout v0.1.18 起也走标准 diag 落盘（[ADR-024](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/024-uniform-bw-error-handling.md)），不再只输出 `Unexpected error: ...` 兜底。

### 3.2 重跑仍失败时三个根因（按概率序）

```
1. 残留 credential-provider 自有 bw 进程占用 data 文件
   只检查由当前用户、credential-provider 会话启动的 PID；先退出相关 shell / 运行 `credential-provider lock`，不要对模糊 `pgrep` 结果执行强制终止。
   PowerShell: 仅在确认进程命令行属于当前 credential-provider 会话后，让用户正常停止该进程；不要批量 `Stop-Process -Force`

2. 杀软实时扫描阻塞 bw / node 子进程
   仅让用户在安全团队批准后，针对 `~/.credential-provider/bw-data/`（或 Windows 对应 credential-provider 数据目录）配置最小范围排除；不要排除 `bw.exe`、`node.exe` 或整个模块目录，且不要由 AI 代为修改系统安全设置。
   Windows: Settings → Virus & threat protection → Manage settings → Add or remove exclusions（需用户确认）

3. Vaultwarden 不可达 / 慢（仅网络命令；--version / config 不适用）
   bash:       curl -w '%{time_total}s\n' https://vaultwarden.builder.addx.live/alive
   PowerShell: Test-NetConnection vaultwarden.builder.addx.live -Port 443
```

源自 [`src/lib/bw-error-hints.ts:formatBwTimeoutHint`](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/src/lib/bw-error-hints.ts) + diag 文件 `=== Mitigation steps ===` section。

### 3.3 把超时门槛拉高（v0.1.18 / ADR-026）

慢盘 / AV 重的机器，hint 重跑仍卡 30s，临时拉高超时：

```bash
# bash / zsh
export CP_BW_TIMEOUT_MS=60000          # 默认 30000，所有 bw 调用
export CP_BW_LIST_TIMEOUT_MS=90000     # 默认 60000，仅 bw list

# PowerShell
$env:CP_BW_TIMEOUT_MS = "60000"
$env:CP_BW_LIST_TIMEOUT_MS = "90000"
```

非整数或 ≤0 会被忽略退回默认。决策见 [ADR-026](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/026-bw-timeout-env-override.md)。**注意**：拉高超时只是延迟撞墙，根因仍是 1/2/3 之一——长期解决方案是 AV 排除 + 清残留进程。

### 3.4 历史已被实证否定的猜测（不要再贴）

- "LevelDB recovery"——bw 2024+ 改用单 `data.json`，没 LevelDB
- "Cold-start I/O after reboot"——用户开新 PS 不重启也撞，限定证伪

每次 timeout 都会写一个 `~/.credential-provider/timeout-diag-<ts>.log`。v0.1.18 起按类别保留最新 20 个自动清理（[ADR-025](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/025-diag-auto-cleanup.md)），不再无限增长；让用户贴 log 找真因。

## 4. `bw version outside compatible range` 黄字警告

```bash
npm install -g @bitwarden/cli@2026.3.0
bw --version    # 应输出 2026.3.0
```

兼容范围 `>=2024.1.0 <2027.0.0`，v2025.12.0 API 变更会让 `bw list items` 报错。固定版本决策见 [ADR-013](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/013-pin-bw-cli-version.md)。

## 5. `Logout required before server config update` (v0.1.7 残留)

仅 v0.1.7。**让用户升级到最新**：

```bash
npm install -g @a4x/credential-provider@latest
credential-provider setup
```

如果坚持不升级，方法 2 / 3 见上游 troubleshooting §1.3.1。

## 6. `bw is currently logged in to <other-server>` / setup exit 4

bw 在隔离目录登录到了不同 server（默认 `bitwarden.com` / 私人自托管 / 其他 vault）。

```bash
credential-provider setup --force
```

`--force` 自动 `bw logout` + `bw config server` + `bw login --sso`，**仅作用于** credential-provider 隔离目录（`~/.credential-provider/bw-data/`），不影响全局 bw 登录态。

> 这是 setup **唯一推荐 `--force`** 的场景。refresh token 过期场景**不需要** `--force`（[ADR-018](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/018-setup-revalidate-expired-token.md)）。

## 6.5 cross-scope shadow 黄字警告（v0.1.20+ ADR-032）

### 6.5.1 症状

```
⚠ KEY `<NAME>` shadowed: personal vault overrides org collection `<col>` (value details are intentionally omitted)
```

同 KEY 在 personal vault 和 org collection 都存在时，**personal 静默覆盖 org**——但 v0.1.20 起会黄字 warn 让用户知情。

### 6.5.2 处理

**不是 bug**——是设计行为（个人定制优先于团队共享，避免用户跑团队脚本时拿到自己的测试值）。

| 用户意图 | 处理 |
|---|---|
| 想用 personal 值（默认行为）| 忽略 warn，继续 |
| 想用 org collection 值 | 在 personal 里 `credential-provider rm <KEY> --yes` 或在 Web UI 改名 |
| 程序化处理（AI agent / CI）| 捕获 stderr 看是否含 `KEY \`<NAME>\` shadowed: personal vault overrides org collection`（cross-scope shadow **不进** `list --json` 的 `warnings` 对象——后者仅含 `suspectedTextSecrets` / `duplicateFields`）|

不能用 `--quiet` 抑制——warn 升档为永远双写（[ADR-029](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/029-warn-always-stderr.md)），profile pipeline 也会显示。

## 7. Hidden vs Text custom field 切换（Web UI）

vault 里加了字段但 `credential-provider list` 看不到 / stderr 警告 `1 field looks like a secret but is type=Text`。

1. 打开 Vaultwarden Web UI（`https://vaultwarden.builder.addx.live`）
2. 编辑那个 item → Custom Fields 区
3. 字段名旁边的 type **图标**（默认是 `T` 文本图标）
4. 点击图标循环切到 **👁 (eye) Hidden**
5. 保存
6. 用户重新跑 `credential-provider list` 应该看到该 KEY

四种 type 的处理见 [核心规则 #2](../SKILL.md#2-vault-加凭证必须用-hidden-custom-field眼睛图标-)。

## 8. BITWARDENCLI_APPDATA_DIR 与隔离目录

credential-provider 在 `~/.credential-provider/bw-data/` 跑 bw，与全局 bw（`~/.config/Bitwarden CLI/`）**互不干扰**。

要直接用 bw 操作 credential-provider 的隔离目录：

```bash
# bash / zsh
export BITWARDENCLI_APPDATA_DIR="$HOME/.credential-provider/bw-data"
bw <任意命令>
unset BITWARDENCLI_APPDATA_DIR
```

```powershell
# PowerShell
$env:BITWARDENCLI_APPDATA_DIR = "$HOME\.credential-provider\bw-data"
bw <任意命令>
Remove-Item Env:BITWARDENCLI_APPDATA_DIR
```

bw CLI **没有** `--bw-data-dir` 命令行参数，必须走 env 变量。

## 9. profile 写入相关

### 9.1 `~/.zshrc 启动时 event not found`

凭证值含 `!`，interactive shell 误当 history expansion。在 guard 块**之前**加：

```bash
# zsh
setopt NO_BANG_HIST

# bash
set +H
```

credential-provider 有意不转义 `!`（转义改变凭证值本身）。

### 9.2 PowerShell `running scripts is disabled`

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 9.3 企业代理

```bash
# bash / zsh
export HTTPS_PROXY=http://proxy.internal:8080
export NO_PROXY=127.0.0.1,localhost          # OAuth loopback 必须直连
```

```powershell
# PowerShell
$env:HTTPS_PROXY = "http://proxy.internal:8080"
$env:NO_PROXY = "127.0.0.1,localhost"
```

## 10. 次级：为什么 `setup` 不能写进 profile

v0.1.14+ 续登链路简化后，"profile 自动跑 setup" 这种 hack 几乎用不上了——续登日就那一天，跟着红字提示跑一次 setup 就行。但偶尔会有人想"把 setup 也塞进 ~/.zshrc 让它自动续"。**论据如下**：

| 论据 | 数据 |
|---|---|
| 启动延迟 | 实测 13~25s（bw login --sso + 浏览器 OAuth + bw config server）|
| 失败模式数 | 6 个（浏览器没装 / 网络抖动 / 用户取消 OAuth / bw 进程残留 / 杀软阻塞 / vault server 慢）|
| 副作用 | 弹浏览器（用户每次开 shell 必须手动点）|
| 频率不匹配 | 每天开 N 次 shell vs 每 7 天才需要续一次 token |
| 安全审计 | profile 自动 SSO 难以追溯哪次"是用户主观行为" |
| 替代路径 | `profile --add` 只塞 env（[`inject-hint.ts:profileGuardCommand`](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/src/lib/inject-hint.ts#L27)）；setup 留作显式动作 |

**结论**：不写。AI 看到这种请求时引导用户走"profile 只放 env + 续登日跑一次 setup"的标准路径。
