# platform-quirks.md — 跨平台陷阱

> credential-provider 的命令本身跨平台等价（同一份 [`inject-hint.ts`](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/src/lib/inject-hint.ts) 输出注入语法）。差异都在**怎么把 stdout 注入当前 shell** 和**profile 文件路径**这两件事。

## 1. 注入语法（不是 `eval`）

| Shell | 注入命令 |
|---|---|
| bash / zsh | `eval $(credential-provider env)` |
| PowerShell（5.1 / 7） | `credential-provider env --shell powershell \| Out-String \| Invoke-Expression` |

PowerShell 没有 `eval` builtin，`Invoke-Expression` 是等价物；`Out-String` **必需**——避免被默认按行切对象数组。

> credential-provider 默认 detectShell 优先级：`--shell` flag → `$SHELL` env → 父进程名 → 平台默认（win32 → powershell，其他 → bash）。源码 [`shell-detector.ts:detectShell`](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/src/lib/shell-detector.ts)。

## 2. PowerShell 5.1 vs 7 — `$PROFILE` 路径动态探测

profile-manager 按 `pwsh.exe` 是否在 PATH 决定路径：

| 探测结果 | profile 路径 |
|---|---|
| `pwsh` 在 PATH | `~/Documents/PowerShell/Microsoft.PowerShell_profile.ps1` |
| `pwsh` 不在（仅 PS5.1） | `~/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1` |

v0.1.11 之前写死 PS7 路径 → 装了 PS5.1 的人 `profile --add` 写到错误位置静默失败。**让 `profile --add` 自己探测**，AI 不要手填路径。

## 3. npm scope URL 双引号必需

```bash
# ✅ 跨 shell 都能跑
npm config set "@a4x:registry" "https://gitlab.addx.ai/api/v4/groups/public-tools/-/packages/npm/"

# ❌ PowerShell 把 `:` 当 scope 分隔符报错
npm config set @a4x:registry https://gitlab.addx.ai/api/v4/groups/public-tools/-/packages/npm/
```

bash / zsh 加引号也无副作用——这是可以无脑套的"跨 shell 安全形态"。

## 4. Windows npm 装的 CLI 有 3 个文件

```
%APPDATA%\npm\credential-provider          # POSIX shim（bash 用）
%APPDATA%\npm\credential-provider.cmd      # cmd.exe 用
%APPDATA%\npm\credential-provider.ps1      # PowerShell 用
```

cross-spawn / spawn 通过 PATHEXT 解析挑哪个。`Get-Command credential-provider` 默认只显示一个（按 PATHEXT 顺序），但 3 个都在。

**实战影响**：测试"CLI 缺失"路径时 `Move-Item` 一个 `.exe` 文件不够——必须把 3 个文件全 mv 走才会真触发 ENOENT。

## 5. Git Bash for Windows 与 cmd / PowerShell PATH 差异

- Git Bash 内置 POSIX-y PATH，`/c/Users/...`；cmd / PS 用 `C:\Users\...`
- Git Bash 调用 npm 装的 CLI 走 `.cmd` 还是 POSIX shim 取决于 cross-spawn
- **`/tmp` 在 Git Bash 是每个 session 独立 tmpfs**——跨 session 持久化文件得放 `~/.<dir>/` 不能放 `/tmp/`
- `~` 在 Git Bash 是 `C:\Users\<name>` 但带 POSIX 分隔符；shell 脚本里别假设是 `/home/<name>`

## 6. macOS：zsh 默认，bash 仍可用

- macOS 10.15+ 默认 zsh，profile 是 `~/.zshrc`
- 若用户改用 bash（chsh），profile 是 `~/.bash_profile` 或 `~/.bashrc`（bash 启动顺序差异）
- detectShell 看 `$SHELL` env var 取最末段 + `.exe` 剥离

## 7. WSL 与 Windows 主机互不可见

- WSL 内的 `~/.bashrc` 在 Windows PowerShell 看不见
- Windows 的 `$PROFILE` 在 WSL bash 看不见
- 用户在 WSL + Windows 两边都用 credential-provider 必须**两边各跑一次** `setup` + `profile --add`
- 隔离 bw 数据目录也是各管各的（`~/.credential-provider/bw-data/` 在 WSL 是 `~/.credential-provider/`，在 Windows 是 `C:\Users\<name>\.credential-provider\`）

## 8. npm prefix 不在 PATH

```bash
# 装完报 `credential-provider: command not found`
npm config get prefix
# 输出比如 /Users/foo/.npm-global，但 PATH 里没有
```

让用户把 `<prefix>/bin`（macOS/Linux）或 `<prefix>`（Windows）加到 PATH。各 shell 加法：

```bash
# ~/.zshrc 或 ~/.bashrc
export PATH="$(npm config get prefix)/bin:$PATH"
```

```powershell
# 用户级 PATH 永久加（PowerShell 管理员）
[Environment]::SetEnvironmentVariable(
  "PATH",
  [Environment]::GetEnvironmentVariable("PATH", "User") + ";$(npm config get prefix)",
  "User"
)
```

## 9. 检查凭证变量是否存在

不要输出凭证值或前缀。bash 与 PowerShell 分别只打印存在状态：

```bash
# bash / zsh
if [[ -n "${GITLAB_TOKEN+x}" ]]; then echo "GITLAB_TOKEN is set"; else echo "GITLAB_TOKEN is not set"; fi
```

```powershell
# PowerShell
if ($null -ne $env:GITLAB_TOKEN) { 'GITLAB_TOKEN is set' } else { 'GITLAB_TOKEN is not set' }
```

## 10. PowerShell `# no-op` 早退（v0.1.14+）

env 命令没有任何凭证可输出（早退）时，PowerShell 形态会输出一行注释 `# no-op` 而非空 stdout。原因：`Invoke-Expression` 撞空字符串会喷 5 行红字告警；写一个 no-op 注释让 `Invoke-Expression` 解析成无副作用的 statement。决策见 [ADR-017](https://gitlab.addx.ai/public-tools/credential-provider/-/blob/main/docs/architecture/adr/017-silence-empty-pipeline-warning.md)。

bash / zsh 早退就空 stdout，`eval` 对空字符串无副作用，不需要这种 hack。
