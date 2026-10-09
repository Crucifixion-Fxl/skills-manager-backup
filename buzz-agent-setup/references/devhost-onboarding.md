# 申请云端开发机并配置自己的 Codex 与 Buzz agent

给公司同事用：在 Buzz 里向运维型 agent 申请一台云端开发机，登录后在自己的 Linux 账号里登录 Codex、配置自己的 Buzz agent。

**状态：reference candidate。**开发机一侧的配置（账号准入、sshd、每用户独立的 rootless Docker、`codex login --device-auth` 能给出验证码）已在真实开发机上用本地测试账号核对过；**没有用真实 LDAP 密码登录过**，也**没有端到端跑过** Buzz 一侧（申请频道里的对话、agent 注册、`buzz-acp` 真实接单）。标了「待验证」的地方不要当成已确认的事实。

## 1. 申请开发机

在 Buzz 频道「申请开发机」（频道 ID `9bb7f433-8830-4a9f-a16d-e1bf31bab27b`，公开频道，自己加入即可）里发一条消息：

```text
@get-dev-vps 我要申请开发机，我的 LDAP 账号：<你的 LDAP 用户名>
```

- 用 @ 选择器选中 agent `get-dev-vps`，不要只手打文字。
- 只能申请**自己的**账号，不要代别人申请，也不要换别人的账号重试。账号对不上时 agent 会转运维确认（待验证：这是运维对 agent 行为的说明，没有在 agent 上实测）。
- 成功时 agent 会在同一个 Thread 回复：主机 IP、登录名、首次登录方式、可映射端口范围。格式示例（以实际回复为准）：

```text
已为你分配开发机：
- 主机 IP：<IP>
- 登录名：<你的账号>
- 首次登录：ssh <账号>@<IP>，用你的 LDAP 密码
- 可映射端口：<起>-<止>
```

- **可映射端口**：每人一段 20 个连续端口，在开发机上从 40000 起分配。你起的容器、服务只用这段里的主机端口。这是约定，机器上没有强制限制，越界会和别人冲突，也可能暴露你的服务。
- 回复里没有 SSH 来源 IP。连不上时先看第 2 节，再在同一个 Thread 里说现象，运维说明 agent 会先分析、再通知运维（待验证）。

## 2. 登录开发机

```bash
ssh <登录名>@<IP>
```

- 第一次用 **LDAP 密码**登录；这台机器上密码登录对已分配的账号是开着的。
- 一次连接最多试 3 次密码，之后会断开，不要反复试。LDAP 账号是公司统一账号，LDAP 的密码策略开了失败锁定（次数和时长以 LDAP 策略为准），连续输错可能让你暂时登不上其他系统。
- 连不上的常见原因：你所在网络的出口 IP 不在这台机器的 SSH 白名单里（由运维配置）；账号还没分配完成。把现象贴在申请的 Thread 里。
- 首次登录会自动给你的账号准备好各自独立的 rootless Docker，并开启 linger（登出后你的用户服务继续运行）。你没有 sudo，也看不到别人的目录、进程和容器。

登录后建议马上放入自己的公钥，之后用密钥登录（在自己的电脑上执行）：

```bash
ssh-copy-id <登录名>@<IP>
```

开发机的 sshd 配了 `ClientAliveInterval 60`、`ClientAliveCountMax 0`、`TCPKeepAlive yes`，按 sshd 的配置含义是不因空闲而断开（配置值已核对，没有实测过长时间空闲）。你本机的 ssh 客户端或中间网络仍可能断，需要时在自己电脑的 `~/.ssh/config` 里给这台机器加 `ServerAliveInterval 60`。

核对环境（在开发机上）：

```bash
id            # 没有 sudo
docker ps     # 用的是你自己的 rootless Docker
codex --version
which buzz buzz-acp codex-acp
```

## 3. 登录自己的 Codex

每个人用自己的账号，登录状态只存在自己的 `~/.codex` 下，别人看不到。

**ChatGPT 账号（推荐）**——开发机上没有浏览器，用设备码：

```bash
codex login --device-auth
```

- 终端会给出一个链接和一次性验证码。在**自己电脑的浏览器**里打开链接、登录、输入验证码。验证码 15 分钟内有效。
- 第一次用前需要在 ChatGPT 的安全设置里打开设备码登录（个人账号），或由工作区管理员在工作区权限里打开。官方文档把设备码登录标为 beta。
- 登录后核对：`codex login status`。未登录时它返回 `Not logged in`，退出码是 1。

**API Key**：

```bash
printenv OPENAI_API_KEY | codex login --with-api-key
```

key 不要写进命令行参数、脚本或聊天（命令行参数同机其他用户能用 `ps` 看到）。

官方文档说凭据缓存在明文文件 `~/.codex/auth.json` 或系统凭据库，里面是访问令牌，等同密码：不要复制、提交或发给别人。退出登录用 `codex logout`。

> 在开发机上核对到：`codex login --device-auth` 能给出链接和验证码；`codex login status` 未登录时返回 `Not logged in`。**没有在开发机上完成过一次登录**，所以凭据实际落在文件还是凭据库没有确认，以官方文档为准。

## 4. 配置自己的 Buzz agent

下面的步骤**待验证**：开发机上的 `buzz`、`buzz-acp`、`codex-acp` 都已装好，但整条链路（含 `run-agent.py` 启动校验之后的真实接单）没有用真账号跑过。遇到不一致，按「反馈」一节处理，不要自己绕过安全校验。

### 4.1 在自己的电脑上铸 agent 身份（不要在开发机上做）

- 铸身份和发布 agent 的策略事件要用你本人（owner）的 Buzz 私钥。**这把私钥只放在你自己的电脑上**，不要拷到开发机：agent 和你在开发机上是同一个账号，放在那里等于 agent 能读到它。
- 具体命令见 [scripts/README.md](scripts/README.md)「典型流程」：`mint-agent.py <agent 名>` 铸身份（输出含 agent 私钥，必须先 `umask 077` 再重定向到文件），`publish_event.mjs` 发布 kind:30177。agent 名自己起。**两个脚本的名字规则不一样**：`mint-agent.py` 接受 `[A-Za-z0-9][A-Za-z0-9._-]{0,62}`（含点号，最长 63 字符），而启动器 `run-agent.py` 只接受 `[A-Za-z0-9][A-Za-z0-9_-]*`（不含点号，无长度限制）。取名请用两者的交集：字母或数字开头，只含字母、数字、`_`、`-`，最长 63 字符。含点号的名字能铸出身份，但之后启动器会拒绝。
- 发布 30177 时 `respond_to` 按你的需求填，个人使用建议 `owner-only`，`parallelism` 与下面 env 的 `BUZZ_ACP_AGENTS` 一致。
- `mint-agent.py` 从你电脑上的 `~/.config/buzz/env` 读 `BUZZ_PRIVATE_KEY`。**这个文件怎么得到（例如从 Buzz 客户端导出）本文没有验证，也没有写步骤**，不确定就先问频道管理员，不要把私钥贴到聊天里或传到开发机。
- 用完立即删除你电脑上专门为此放私钥的临时文件；铸身份输出的 JSON 用完 `shred -u`（macOS 没有 `shred` 时用 `rm -P`）。
- 你的 agent 要进哪个 Buzz 频道、由谁加成员，按那个频道的管理员的规则来（私有频道需要频道 owner/admin `add-member --role bot`）。

### 4.2 把 agent 的 env 传到开发机

只传 agent 自己的 env 和启动器，不传 owner 私钥。先在开发机上建目录（目录和它的上级不能被组或其他人写）：

```bash
install -d -m 700 ~/.config/buzz/agents
```

再在你的电脑上传（`run-agent.py` 来自 skill 的 `references/scripts/`）：

```bash
scp <本机上的 agent env 文件> <登录名>@<IP>:~/.config/buzz/agents/<agent 名>.env
scp <skill 目录>/references/scripts/run-agent.py <登录名>@<IP>:~/.config/buzz/agents/run-agent.py
```

回到开发机：`chmod 600 ~/.config/buzz/agents/<agent 名>.env && chmod 500 ~/.config/buzz/agents/run-agent.py`，用 `stat -c '%a %U' <文件>` 确认权限和属主。env 必须是 0600、属主是你。

启动器 `run-agent.py` **强制要求**下表「必填」列为「是」的 9 个键（对照 `run-agent.py` 里的 `REQUIRED`），缺一个就拒绝启动，报错只有 `launch contract failed`，不带细节。其余键启动器不强制，但 agent 要正常工作需要配置：

| 键 | 必填 | 说明 |
|---|---|---|
| `BUZZ_RELAY_URL` | 是 | relay 地址，见下 |
| `BUZZ_PRIVATE_KEY` | 是 | agent 的私钥（不是你的） |
| `BUZZ_ACP_AGENT_OWNER` | 是 | 你的 pubkey（hex） |
| `BUZZ_ACP_AGENT_COMMAND` | 是 | `/usr/local/bin/codex-acp` |
| `BUZZ_ACP_BINARY` | 是 | 固定的 `buzz-acp` 绝对路径，见 4.3 |
| `BUZZ_ACP_BINARY_SHA256` | 是 | 上面那个文件的 sha256 |
| `BUZZ_ACP_RECOVERY_REVISION` | 是 | 40 位 hex，当前 release 的 commit |
| `BUZZ_ACP_SYSTEM_PROMPT_FILE` | 是 | 绝对路径的 prompt 文件，0600 |
| `BUZZ_RESPONSIBLE_CONFIG` | 是 | 绝对路径的责任人 helper 配置，见 `scripts/buzz-responsible-mentions.example.json` |
| `BUZZ_AUTH_TAG` | 否（缺了 relay 认不出 agent 的 owner 背书） | 铸身份输出的 `auth_tag`，**整段必须包单引号** |
| `BUZZ_ACP_MEDIA_MODE` | 否（但不配图片代理时启动器要求显式写） | `stock_text_only` |
| `CODEX_HOME`、`CODEX_PATH` | 否（但适配器是 `codex-acp` 时启动器应当检查，见下） | 见下 |
| `INITIAL_AGENT_MODE` | 否 | `agent-full-access`（见 [troubleshooting.md](troubleshooting.md)「根因 B（Codex）」） |
| `BUZZ_ACP_RESPOND_TO` | 否 | 与 30177 一致，如 `owner-only` |
| `BUZZ_ACP_AGENTS` | 否 | 与 30177 的 `parallelism` 一致 |

- `BUZZ_RELAY_URL`：[runtime-setup.md](runtime-setup.md) 的 env 示例写的是 `https://buzz-sg.addx.live`，[scripts/README.md](scripts/README.md) 发布事件用的是 `wss://buzz-sg.addx.live`。另一台部署 agent 的运维报告：`buzz-acp` 用 `https://` 会报 `URL scheme not supported` 并进入重启循环，改 `wss://` 后正常。**我们在开发机上没能复现或证实这一点**（用 `buzz-acp` 0.5.27 对一个关闭的本机端口试了 `http/https/ws/wss`，都在连 relay 之前就因 agent 启动失败退出）。建议先用 `wss://`；`buzz` CLI 两种写法都能连到 relay（用假密钥只得到 `relay_membership_required`，说明地址被接受）。
- `CODEX_PATH`：先 `readlink -f "$(command -v codex)"` 得到真实路径。**启动器对这两个键的检查按适配器的文件名触发**，而 npm 安装的 `codex-acp` 解析后叫 `index.js`，检查会被静默跳过（在开发机上复现过：去掉 `CODEX_HOME` 仍然通过）。所以不要指望启动器替你发现漏配，自己对照本节。
- `CODEX_HOME`：用你自己的目录，比如 `~/.codex`，必须是 canonical、属主是你、其他人不可写。
- `AGENT_WORKDIR` 必须在 `~/buzz-agent-work/` 下，并且是 Git 仓库：`mkdir -p ~/buzz-agent-work/<agent 名> && git -C ~/buzz-agent-work/<agent 名> init`。

### 4.3 安装 `run-agent.py` 并启动

- 把 skill 里的 [`run-agent.py`](scripts/run-agent.py) 装成 `~/.config/buzz/agents/run-agent.py`，权限 0500。
- 开发机上 `/usr/local/bin/buzz-acp` 是公共安装的二进制（`desktop-v0.5.27` 构建，root 属主）。用假密钥在开发机上验证过：把它的路径和 `sha256sum` 写进 env 后，`run-agent.py` 的启动校验（`prepare_launch`）能通过。
- 但这个构建里**没有找到**启动器要求的自动恢复（`BUZZ_ACP_RECOVERY_REVISION`）对应的字符串，skill 其余部分固定的是 `buzz-0.5.23`。两者是否兼容**未验证**。要严格按 skill 的版本，请按 [local-upgrade-runbook.md](local-upgrade-runbook.md) 取对应二进制放到你自己的目录，再把路径和 sha256 写进 env。
- 持久单元写法见 [runtime-setup.md](runtime-setup.md)「用持久用户单元托管 Agent 进程」（`ExecStart=/usr/bin/python3 -I %h/.config/buzz/agents/run-agent.py <agent 名>`）；linger 已在首次登录时开启，登出后单元继续运行。

```bash
systemctl --user daemon-reload
systemctl --user enable --now buzz-local-<agent 名>.service
journalctl --user -u buzz-local-<agent 名> -n 50 --no-pager   # 看启动日志；不要贴出整份 env
```

验证：到你的 agent 所在频道里 @ 它，看它是否在原 Thread 回复。排障见 [troubleshooting.md](troubleshooting.md)。

## 5. 约定与限制

- 你的账号上跑的容器、服务只用申请回复里的端口范围。
- 资源有每用户上限（当前 CPU 2 核、内存 8G、进程数 8192，会随运维调整）；一台机器同时只放有限个用户。
- 容器数据和镜像都在你自己的家目录里，磁盘没有按用户配额。
- 开发机上除你自己的 Docker 外，没有共享的 Docker。别人的容器你看不到，也不要尝试。
- 不再使用时，在申请的 Thread 里说一声，请运维释放。（待验证：运维侧有释放账号的操作，但用户怎么触发释放、agent 会怎么回复，没有实测。）

## 反馈

照这份文档做遇到走不通的地方（命令不对、说明缺了、有更好的做法），按 SKILL.md「反馈闭环」处理：在 `engineering/skills` 先查重再提 issue，写清照着哪一节做的、实际发生了什么。
