# Claude Code 内置沙箱：agent 的本地加固层

`env -i` 白名单和 0600 env 管不住同一 Unix UID 下的 agent 去读 owner 的文件、去用 owner 的个人变量。Claude Code 自带的 Bash 沙箱（bubblewrap）可以在**不换 Unix 用户**的前提下，把 agent 的 Bash 子进程关进受限的文件系统与环境，是一层实用的本地加固。官方文档：[Configure the sandboxed Bash tool](https://code.claude.com/docs/en/sandboxing)。

## 两层防护，先后关系

同一次隔离事故里验证过两层，各管一段：

| 层 | 管什么 | 文档 |
|---|---|---|
| 第一层：`~/.bashrc` 门禁 | 让 agent 的 shell 默认是干净的：不再把 owner 的个人密钥文件灌回来 | [runtime-setup.md](runtime-setup.md)「白名单只管启动那一刻」 |
| 第二层：内置沙箱（本文） | 对文件、环境变量、docker.sock 做强制约束：即使 agent 主动去读也读不到 | 本文 |

两层都**不是硬边界**：同一 Unix UID 下要硬边界只能是独立 Unix 用户（长期建议）。先做第一层，再按本文做第二层。

## 适用与边界

- **只管 Bash 子进程**（及其子进程）。agent 主进程和内置 Read／Write／Edit 工具不在沙箱里，靠 `permissions.deny` 的 `Read(...)` 规则拦。所以沙箱与 `permissions.deny` 两处都要配，缺一处就是漏的。
- 官方自己写明「不是完整隔离边界」。同 UID 下它仍**不是硬边界**：要硬边界只能是独立 Unix 用户／容器（见 [act-authorization.md](act-authorization.md)、runtime-setup.md「Executor 的强制隔离」）。不要因为开了沙箱就放宽 executor 的隔离要求或给 agent 更多凭据。
- 只对 harness 是官方 `@agentclientprotocol/claude-agent-acp` 的 agent 有效；harness 不是它的（如 Grok、Codex）Claude Code 的沙箱设置对其无效，要另想办法。

## 前提

要求 Claude Code ≥ 2.1.246（`sandbox.credentials` 的分层语义；实测本机 2.1.278）。

```bash
sudo apt install bubblewrap socat
```

**Ubuntu 24.04 及以上**：AppArmor 默认限制非特权用户命名空间（`sysctl kernel.apparmor_restrict_unprivileged_userns` 返回 `1`），bwrap 会报 `setting up uid map: Permission denied`。需要 root 给 bwrap 单独放行，创建 `/etc/apparmor.d/bwrap`：

```
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
```

```bash
sudo systemctl reload apparmor
bwrap --ro-bind / / --dev /dev --proc /proc --unshare-user true; echo $?   # 必须是 0
```

这个配置只针对 bwrap 本身，不作用于它里面跑的命令。代价：bwrap 因此可以创建用户命名空间，**agent 自己也能用**，这正是 Ubuntu 默认限制它的原因；接受这一点再开。

## 配置分层

沙箱的开关放在**每个 agent 自己的项目级／本地级配置**，安全策略放在**所有 agent 共用的用户级配置**。这样没有项目级配置的 agent（比如别的频道的、还没评估的）不会被一刀切打挂。

### 共用：`$CLAUDE_CONFIG_DIR/settings.json`（只放安全策略，不写 `sandbox.enabled`）

`CLAUDE_CONFIG_DIR` 是 agent 启动 env 里指向的配置目录（本机是 `~/.claude-buzz`）。安全策略放共用层有两个原因：所有 agent 一次生效；它不在任何 agent 的工作目录里。沙箱里的命令写不了工作目录中的 `.claude/settings.json`（官方受保护路径），但内置 Write／Edit 工具不受沙箱管，能否改动项目级文件本文没有验证，所以**不要把项目级配置当作 agent 改不了的策略**，不可篡改的部分放在这里。

```json
{
  "sandbox": {
    "allowUnsandboxedCommands": false,
    "credentials": {
      "envVars": [
        { "name": "<你的个人变量名 1>", "mode": "deny" },
        { "name": "<你的个人变量名 2>", "mode": "deny" }
      ]
    }
  }
}
```

- `allowUnsandboxedCommands=false`：沙箱里失败的命令不能自己重试成「不沙箱」（否则 agent 传 `dangerouslyDisableSandbox` 就能出去）。
- `credentials.envVars`：**逐个**列出 owner 个人的变量名（私钥、云服务 token、个人 SaaS 账号密码…），每次沙箱命令前都会被摘掉。没有内置黑名单，没列的不管。

### 每个 agent：项目级或本地级 settings

适配器 `claude-agent-acp`（实测 0.70.0，见其 `dist/acp-agent.js`）每个会话都用 `settingSources: ["user","project","local"]`，三级都会加载。放哪一级看 agent 的**实际 cwd**：

- cwd 不是代码仓（普通 agent 工作目录）：项目级 `<cwd>/.claude/settings.json`。
- cwd 本身是一个被 Git 跟踪的代码仓（如 cwd 是业务仓 checkout）：用**本地级** `<cwd>/.claude/settings.local.json`，并把它加进 `.git/info/exclude`，避免和仓内可能已有／已跟踪的 `.claude/settings.json` 冲突，也避免被提交。
- 先确认启动器真正的 cwd：启动脚本可能把 cwd 换到子目录（本机 `run-agent.sh`：`AGENT_WORKDIR` 未设且 `$WORKDIR/<repo>/.git` 存在时，cwd 是该子目录），配置要放在**那个子目录**里，放在外层不会被读到。

下面以项目级为例：

```json
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "allowUnsandboxedCommands": false,
    "filesystem": {
      "denyRead": ["~/"],
      "allowRead": [
        ".",
        "~/.local/opt/buzz-<版本>",
        "~/.local/share/buzz-agent-setup/releases/<固定 release sha>",
        "~/.config/buzz/agents/<channel>/responsible/<name>.json",
        "~/.config/buzz/agents/people.json",
        "~/.claude-buzz/plugins",
        "~/.local/state/buzz/<channel>/responsible/<name>",
        "~/.local/bin/glab",
        "~/.local/bin/jq",
        "~/.local/bin/claude",
        "~/.local/share/claude/versions",
        "~/.claude-buzz/shell-snapshots"
      ],
      "allowWrite": ["~/.local/state/buzz/<channel>/responsible/<name>"]
    }
  },
  "permissions": {
    "deny": [
      "Read(~/.config/buzz/**)",
      "Read(~/.bash_secrets)",
      "Read(~/.claude/**)",
      "Read(~/.claude-buzz/.credentials.json)",
      "Read(~/.claude-buzz/projects/**)",
      "Read(~/.config/glab-cli/**)"
    ]
  }
}
```

- `failIfUnavailable=true`：沙箱起不来（缺 bwrap、AppArmor 没放行）就**拒绝启动**，不悄悄退回裸跑。缺省是只警告然后不沙箱运行，那等于没开。
- `denyRead: ["~/"]` 后按需 `allowRead` 精确放回：`.`（工作目录）、buzz CLI 目录、**固定的** skill release 目录、该 agent 自己的责任人配置文件、插件目录、自己的 state 目录，以及下面「基线还要放行的五项只读工具」。`allowWrite` 只给 state 目录（以及该 agent 自己的工具目录）；工作目录默认可写。
- `permissions.deny` 那组 `Read` 规则是给**内置 Read 工具**的（沙箱管不到它）；路径按你机器上的实际位置改。
- 这份配置必须放在项目级／本地级：`allowRead` 里的 `.` 按所在配置的目录解析，搬到用户级配置里会指向配置目录而不是工作目录，工作目录反而被 `denyRead` 挡住。
- 配置在**下一个新会话**生效，不必重启服务：`BUZZ_ACP_SESSION_POLICY=thread` 时每个新 Thread 会话都读取新配置（已在跑的会话下一个 Thread 才换上）。

### `allowRead`／`allowWrite` 怎么生成

每个 agent 各生成一份，规则一致，只有路径不同：

- 读：`.`（cwd）、buzz CLI 所在目录、**固定的** skill release 目录（取自该 agent prompt 里引用的 `releases/<40 位 hex>`）、自己的 `BUZZ_RESPONSIBLE_CONFIG` 文件、`~/.claude-buzz/plugins`（skill 文件），再加下面「基线还要放行的五项只读工具」，以及该 agent 自己工具目录里的**精确路径**。
- **`BUZZ_RESPONSIBLE_CONFIG` 里的 `people_file` 字段要单独放行**（v2 契约起这个字段是必填的）：它是所有 agent 共享的一份文件（本机是 `~/.config/buzz/agents/people.json`），不在 `state_dir` 或配置文件本身所在目录下，只放行配置文件和 state_dir 不会带上它——2026-09-22 neopace 这对 agent 的责任人配置从 v1 迁移到 v2 时就漏了这一条，helper 报 `cannot read people_file: FileNotFoundError`，且不会生成任何提及。迁移到新 channel／新 agent 时，照抄这份配置模板不会自动带上这条，要单独检查。
- 读写：自己的责任人 helper `state_dir`；有自己的工具目录时，只给它需要写的那几个路径。
- 别把 `~/` 放回去、别放整个 `~/.config`、`~/.local` 或 `~/.local/bin`：放回去的目录就是新的泄露面。

### 基线还要放行的五项只读工具（漏了它们，agent 里 `glab`／`jq`／`rg` 全不可用）

`denyRead: ["~/"]` 会把 `~/.local/bin` 下的工具一起屏蔽：沙箱里 `glab`、`jq`、`uv` 都是 `command not found`。另外 Claude Code 的 Bash 工具里，`rg`、以及带 embedded bfs／ugrep 的 `find`／`grep`，是**定义在 shell 快照里的函数**：快照由执行 `~/.local/bin/claude` 生成（它是指向 `~/.local/share/claude/versions/<ver>` 的链接），文件在 `$CLAUDE_CONFIG_DIR/shell-snapshots`。快照目录读不到时 `rg` 就是 `command not found`；`grep`／`find` 会回退到系统命令，所以**不明显**。所以基线 `allowRead` 必须再加这五项，都是只读的可执行文件或快照，不含配置或密钥：

| 路径 | 为什么 |
|---|---|
| `~/.local/bin/glab` | GitLab CLI |
| `~/.local/bin/jq` | JSON 处理 |
| `~/.local/bin/claude` | 快照函数（`rg` 等）经它生成；是链接，目标见下一行 |
| `~/.local/share/claude/versions` | 上面链接的目标目录（`versions/<ver>`） |
| `<CLAUDE_CONFIG_DIR>/shell-snapshots` | 快照文件，`rg`／`find`／`grep` 函数在这里（本机是 `~/.claude-buzz/shell-snapshots`） |

- **放行快照前，先检查你自己的快照里没有密钥类 `export`**（只列变量名，不打印值）：

  ```bash
  grep -h -E '^(export|declare -x)' <快照目录>/* | sed -E 's/^(export|declare -x) ([A-Za-z_][A-Za-z0-9_]*)=.*/\2/' | sort | uniq -c
  ```

  本机 296 份快照里只有 `PATH` 一项导出，其余是 `shopt`／`set`／`unalias` 和函数定义，没有密钥类变量。列出了 `*_TOKEN`／`*_KEY`／`*_PASS*` 之类的名字，就不要放行，先查清是谁把它导出进快照的（通常是 `~/.bashrc` 门禁没挡住，见 runtime-setup.md「白名单只管启动那一刻」）。
- **只放这五个精确路径，不要放整个 `~/.local/bin`**：里面可能有会加载 owner 密钥的 wrapper（如 buzz 的包装器）和其它个人工具。
- **不要放行 glab 的配置目录 `~/.config/glab-cli`**（里面可能是管理员级 token）。已验证：沙箱里 glab 用 agent 自己的 `GITLAB_TOKEN`（加 `GITLAB_HOST`）就能正常访问 GitLab（`glab api version` 返回版本），而 owner 的 glab 配置读不到。开发类 agent 若担心 glab 去碰 owner 配置，可设 `GLAB_CONFIG_DIR` 指向 agent 自己的空目录（desk 类已验证不需要；dev 类是分析结论，**没有实测**）。

## 推广指引：哪些 agent 能开

已在 24 个 Claude Code harness 的 agent 上开启（20 个纯答疑／分诊／只读类：desk、bug、bi、skill-mr 等；4 个开发类经 [buzz-broker.md](buzz-broker.md) 补上 docker 之后开启：kids-creative-dev、plugin-dev、devt-dev、nh-dev），每个都用下面的离线探针验证通过。**没做的几档，以及原因**：

| 档 | 为什么没一刀切 |
|---|---|
| 要跑真实浏览器（Playwright／Chrome 抓取、每日 Chrome 走查）的 investigator | **不能直接套**：沙箱的 seccomp 过滤禁止子进程创建 Unix socket，Chrome 启动就 `FATAL: process_singleton_posix.cc: socket() failed: Operation not permitted`。见下文「已知风险与坑」第 5 条的替代做法；本机一个每日 Chrome 走查的 investigator 因此已回滚、未套沙箱 |
| 开发类 agent（要 `git push`、ssh、docker 测试、Go／Node 工具链） | 沙箱会挡 docker 和 `~/.ssh`（ssh-agent 也用 Unix socket），工具链目录也要放行，不能套用只读类的模板，需要逐个设计；需要 docker／任意命令的可以在沙箱之外接 [buzz-broker.md](buzz-broker.md)（4 个 agent 已验证），沙箱内直接跑 docker 仍然不行、也不应该行 |
| 个人 agent（持有个人登录态） | 排除：它本来就以 owner 本人的登录态工作 |

另外 harness 不是 `claude-agent-acp` 的 agent（如 Grok、Codex）本文无效，见上文「适用与边界」。

### 有自己工具目录、不用浏览器的 agent：在通用基线上追加

已验证的做法（`cs-triage-investigator`：沙箱内用 JWT 调 Troubleshooting 只读接口、写自己的审计日志、读自己的工具脚本都正常）：

- 在通用基线上追加该 agent 的**工具目录 `allowRead`**、**审计／状态目录 `allowWrite`**。
- 若某个放行的目录里还混放着别的进程的状态（如 GitLab→Buzz 同步的游标／队列），对那几个子目录**单独加 `denyRead` + `denyWrite`**：更窄的 deny 在更宽的 allow 之内依然生效。
- `node` 若装在 `~/.nvm/...` 下，要给该版本目录加 `allowRead`，否则工具脚本起不来。**agent 要 shell 出去调用某个全局 npm 装的 CLI（比如 lark-cli）时，先在沙箱外 `which <cli>` 确认它实际落在哪个 nvm 版本**——不一定是这台机器 `SAFE_PATH`／别的 agent 已经放行的那个主版本（`npm install -g` 按当时激活的 nvm 版本走，换个版本装的工具就在另一棵目录树下）。这种情况下 `SAFE_PATH` 和沙箱 `allowRead` 两处都要单独放行那个版本的 `bin/<cli>`（它通常是指向 `lib/node_modules/<pkg>/...` 的符号链接，目标包目录也要一起放行，否则符号链接本身在沙箱里就是 `No such file or directory`，看起来像“这个环境没装这条命令”而不是权限问题）。2026-09-22 在 neopace-investigator 上就踩过这个坑：`lark-cli` 装在 v20，主版本是 v24。
- **沙箱内测出来的结果不能全信**，尤其是间接跑（比如自己拼一个 `claude -p` 加自定义 env 去探测另一个 agent 的沙箱）：探测脚本对 PATH／快照／沙箱内部机制的还原经常和真实 harness 有出入，可能测出和生产完全相反的结论（成功说成失败，或反过来）。**权威验证是让那个 agent 在它自己真实跑着的会话里执行同一条命令**，不是自己搭一个模拟环境替它跑。
- **正规做法：自己写的、会 shell 出去调 `buzz`／`lark-cli` 的工具脚本，绝对路径要写死在脚本自己的默认值或配置里，不要依赖调用方每次都记得传 `--buzz`／`--lark-cli`，更不要指望裸命令名靠 PATH 解析。** 原因不只是「沙箱可能没放行那个目录」：就算 `SAFE_PATH` 和沙箱 `allowRead` 两处都配对了，**PATH 在嵌套的 subprocess 调用链里（Bash 工具 → 你的脚本 → 再 shell 出去调下一层脚本）能不能一路解析到底并不稳定**，同一个沙箱会话里，agent 直接跑 `lark-cli ...` 能成功，脚本内部再套一层 `subprocess.run(["lark-cli", ...])` 却可能解析不到（2026-09-22 在 `publish_report.py` 上实测踩到）。这个仓库已有的正确样例：`buzz-feishu-sync` 的 `config.json` 把 `buzz_cli`／`lark_cli` 存成绝对路径（`buzz_cli` 还配 `buzz_cli_sha256` 校验，防止悄悄换了别的二进制），脚本永远从配置读，不猜 PATH——凡是新脚本要 shell 出去调这两个工具，照这个模式抄，不要用裸命令名做默认值。
- **验证要在沙箱外复核**：被 `denyWrite` 的路径，在沙箱里 `touch` 会返回 exit 0，但沙箱外并没有创建该文件（写入落进不持久的遮罩层）；被 `denyRead` 的子目录读出来是**空目录**而不是报错。所以只看命令退出码会假绿，要回到沙箱外确认文件是否真的存在、目录里是不是真的空。

### 开发类 agent 的额外注意（分析结论，**尚未实测**）

下面是按沙箱规则推出来的，没有在真机上验证过，套用前要逐条实测：

- cwd 是仓根时，沙箱始终拒写 `.git/config` 和 `hooks`。所以 `git push -u`、`git checkout -b x origin/y`、`git branch --set-upstream-to` 会失败。缓解：`GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=branch.autoSetupMerge GIT_CONFIG_VALUE_0=false`，加 `git push origin HEAD`。
- `~/.gitconfig` 读不到会丢 commit 身份（内容通常只有 `user` 与 credential helper）：需要给它 `allowRead`。
- `/tmp` 不可写：改用 `./.scratch` 或 `$TMPDIR`。**不要**开 `allowWrite /tmp`，同 UID 下它是共享的。
- `nohup … &` 预期活不过该条命令。
- docker、Chrome/Chromium 因 Unix socket 限制不可用（见「已知风险与坑」第 5 条，这条已验证）；需要它们的 agent 走沙箱外的窄接口，见 [buzz-broker.md](buzz-broker.md)。
- Go 模块缓存可能含私有模块源码：建议只读共享；构建缓存每个 agent 一份。

## 已实测有效

owner 私钥文件读不到；个人密钥文件内容读不到；个人变量为 0；别的 agent 的工作目录不可见（home 下只剩 `allowRead` 涉及的几项）；`docker.sock` 不可用；Read 工具被 `deny` 规则拒；agent 在沙箱内仍能回帖、登录 Superset、跑自己的工具脚本。

## 已知风险与坑

1. **网络白名单在无头（ACP／headless）模式下不强制。** 白名单靠交互批准，没人点「允许」时白名单外的域名默认放行；`network.strictAllowlist` 只在用户级／托管级／CLI `--settings` 才生效，项目级会被忽略。owner 决定**不限制网络**，所以**网络层没有强制**：不要给「已限制网络」的错觉，也不要在项目级写没有效果的 `allowedDomains`。真要限制网络得另行设计并单独验证，本文没有验证过。
2. **`test -r <被屏蔽的文件>` 会误报 READABLE**：被屏蔽的文件在沙箱里是占位文件。判断要看内容大小：`wc -c < <file>` 会 `Permission denied`。
3. **Bash 命令里只要出现被 `deny` 的路径，整条命令会被权限层拒绝。** 把多条探针用 `;`／`&&` 合成一条，会整条失败、看不出哪条真拒了；探针要一条一条跑。
4. **docker 组等于宿主 root，沙箱是能堵住它的手段之一。** owner 在 `docker` 组、agent 与 owner 同 UID 时，未开沙箱的 agent 里 `docker ps -q` 直接返回容器 id（实测），能挂载宿主目录、起特权容器；开沙箱后返回 `permission denied … docker.sock`（或空）。`docker` 与沙箱不兼容，命令会失败（原因见下一条：Unix socket 被禁），这里反而是好处。owner 在 docker 组时应单独评估，见 [runtime-setup.md](runtime-setup.md)「docker 组要单独评估」。
5. **沙箱的 seccomp 过滤禁止子进程创建 Unix domain socket**（`socket(AF_UNIX, …)` 报 `Operation not permitted`）。后果：docker（`docker.sock`）、Chrome／Chromium（启动时创建进程单例 socket，实测 `FATAL: process_singleton_posix.cc: socket() failed: Operation not permitted`）、dbus、`systemctl --user`、ssh-agent 都不可用。这既是好处也是限制：好处是堵死了 docker 组口子和 systemd --user 控制口；限制是「要跑真实浏览器的 agent」不能直接套这个沙箱。**不要**为此设置 `allowAllUnixSockets`：它会同时重新打开 `docker.sock` 和 `/run/user/<uid>/systemd/private`（systemd --user 管理口，可以起未沙箱化的服务），等于逃逸。推荐替代：把 Chrome 抓取或 docker 命令放到沙箱外的一个窄接口后面（owner 侧的确定性服务／固定脚本，agent 只发请求、取结果文件），docker／任意命令这条路径已经通用化实现，见 [buzz-broker.md](buzz-broker.md)；或者该 agent 暂不套沙箱、只靠 `~/.bashrc` 门禁兜底，并如实记为已知缺口。
6. **回滚**：删掉该 agent 的项目级／本地级 settings 文件（下一个新会话生效）；全局回滚还原用户级 settings 的备份（改之前先备份）。

## 验证

### 推荐：离线探针（不往有其他人的频道发测试消息）

在该 agent 的 **cwd** 里起一个独立的无头 `claude -p`：同一个 claude 二进制，`CLAUDE_CONFIG_DIR` 与 agent 相同，环境按启动器的白名单构造（不能比 agent 多带变量，否则探针会被自己的环境污染），`--dangerously-skip-permissions`，用便宜的模型。给它一段固定 prompt：要求用 Bash 工具**逐条分别**运行下面 8 项探针，只回一行 JSON（每项的结果，不要贴文件内容、不要重试绕过）。

| # | 探针（各自单独一条 Bash） | 期望 |
|---|---|---|
| 1 | `wc -c < ~/.config/buzz/env` | 被拒：`Permission denied` 或 `No such file`；**输出一个字节数**说明没挡住 |
| 2 | `wc -c < ~/.bash_secrets` | `Permission denied`；输出字节数说明没挡住 |
| 3 | `env \| grep -c -E '^(<个人变量名 1>\|<个人变量名 2>\|…)='` | `0` |
| 4 | `ls ~/buzz-agent-work \| wc -l` | `1`（只见自己；路径按你的工作目录父目录改） |
| 5 | `docker ps -q` | `permission denied` 或空 |
| 6 | 用 buzz CLI 读自己频道的 Canvas：`canvas get --channel <CH> \| wc -c` | 大于 0，证明 relay 通路在沙箱里正常（平台 Desk 不固定频道，改用 `buzz channels list`） |
| 7 | **Read 工具**读 `~/.claude/settings.json` | 被 `deny` 规则拒 |
| 8 | tools：`glab --version`、`jq --version`、`rg --version`，**每条各自单独一条** Bash | 三条都打印出版本号才算通过；任一条是 `command not found` 就是基线 `allowRead` 缺了五项只读工具（见上文） |

**验证必须同时测两面：「该挡的挡住了」（探针 1–5、7）和「该用的还能用」（探针 6、8）。** 早期的探针只测了前者，21 个 agent 已经上线后才发现 `glab`／`jq`／`rg` 在沙箱里不可用：`denyRead` 收得越紧，越要证明业务工具还活着。探针 8 是「该用的」探针：它没有反对照（不配沙箱时这三个命令本来就能用），判据就是每条都打印版本号。

**文件类探针一律看内容大小（`wc -c`），不用 `test -r`／`-e` 这类元数据检查**：被屏蔽的文件是占位文件，`test -r` 会误报 READABLE，正／反对照都会因此失真（见上文「已知风险与坑」第 2 条）。`wc -c` 只输出字节数，不会把内容打进日志。

**必须带对照**，否则「全过」可能只是探针本身没测到东西：

- 正对照：已配沙箱的 agent，8 项应全部符合期望。
- 反对照：**未配**沙箱的 agent，应当暴露：探针 1、2 各输出一个非零字节数（不是 `Permission denied`）、看见全部其他 agent 的工作目录、`docker ps` 返回容器 id、Read 工具读到内容（本机实测如此）。反对照没暴露，说明探针有问题。

局限：这个方法不经过 ACP 适配器，但适配器加载同样的三级 settings（见上文），所以配置是否生效的结论可以迁移；适配器自身的行为没有被覆盖。

**探针要拆开逐条跑**：Bash 命令里只要出现被 `deny` 的路径，整条命令都会被权限层拒绝（实测：把 4 条合成一条，被整条拒绝），合并后看不出哪条真被挡了。

### 备选：在频道里 @ agent 让它自己跑

只在没有其他人的私有频道里做。@ 该 agent 请它一条一条执行上表探针，只汇报结果。文件类探针只用 `wc -c`，不用 `cat`：万一沙箱没生效，也不会把密钥打进频道。

任一条没挡住：先回滚，再查 `failIfUnavailable` 是否生效（agent 日志里有没有沙箱启动失败）、`allowRead` 是否把不该放的目录放回来了、配置是不是放在了 agent 实际 cwd 里。
