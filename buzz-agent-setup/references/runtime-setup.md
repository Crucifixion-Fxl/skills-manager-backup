# Buzz Channel／Agent／Workflow 运行时配置

本页用于实际创建 Channel、铸 Agent 身份、配置 harness、Workflow、Onboarding 与正负向验证。协作模型先读 [fchac-model.md](fchac-model.md)，高影响动作授权先读 [act-authorization.md](act-authorization.md)。

所有自动化与验证示例都必须调用当前实际安装的 **Buzz 0.5.23 原始 ELF 绝对路径**：

```bash
BUZZ_CLI=/home/jchen/.local/opt/buzz-0.5.23/usr/bin/buzz
test -x "$BUZZ_CLI"
test "$(dd if="$BUZZ_CLI" bs=4 count=1 status=none | od -An -tx1 | tr -d ' \n')" = 7f454c46
```

CLI 没有可信的 `--version`，不要调用它判断版本；用不可变安装目录 `buzz-0.5.23` 固定来源。**不得使用 `buzz` 的 PATH 解析结果或 `~/.local/bin/buzz`**，后者可能是自动加载 owner key 的 wrapper。

## 1. 创建 Channel

```bash
"$BUZZ_CLI" channels create --name <name> --type stream --visibility open --description "..."
"$BUZZ_CLI" canvas set --channel <CH> --content -
```

Canvas 最先写「## 代码仓库」清单，再写 FCHAC 模型规定的四张表。清单只有三列，不含 Agent 权限列；每个 Agent 的档位按 [agent-credentials.md](agent-credentials.md) 的角色最小档决定，GitLab token 按「清单 × 角色最小档」申请：

```markdown
## 代码仓库

| 仓库路径 | project id | 用途 |
| --- | --- | --- |
| `<group>/<project>` | `<id>` | `<这个仓库在本业务里放什么>` |
```

增删仓库或调整权限时，先改表，再申请或收回 token。建完立刻问：“这个 Channel 要拉谁？他们已有 Buzz 账号吗？”

人不能由 Agent 代建身份：本人在公司网络／VPN 用 Buzz Desktop 生成密钥对，仅把 npub 给管理员吕强／qlv；管理员执行 `buzz-admin add-member`，本人再连接 `wss://buzz-sg.addx.live` 并 join Channel。Agent 则由 owner 的 NIP-OA 背书自助入场，不走人的路径。

## 2. 配置前从 Skill 反推权限

列出该 Agent 会调用的 `addx:*` Skill，逐个读取其认证／环境变量章节，再生成两份产物：

1. Agent env：未申请凭据写**注释占位**，注明 Skill、申请路径和最小 scope。
2. Agent prompt：凭据状态表（✅／❌）与硬规则：“缺凭据时说清平台、用途和对应 Skill，然后停下；不借别的凭据，不用人的账号。”

平台与逐 Agent 结果见 [fchac-model.md](fchac-model.md)，申请／交接细节见 [agent-credentials.md](agent-credentials.md)。NineData 不进入本 Agent 模型。

无论角色的其他 Skill 是什么，**所有注册 Agent**都必须加载 `gitlab-issue-sop`，单仓 Agent 在自己的 env 中保留独立 `GITLAB_TOKEN` 占位，并由 GitLab 管理员授权项目级 Planner（15）+ `api` 身份，用于创建／评论／更新／关闭／重开 Issue。多仓 Agent 使用 owner 固定的项目 token map 与命名变量（见下文），不把项目范围合并成 Group token；Issue／代码操作也必须先按目标项目选择对应变量。需要更高档位时只提升该 Agent 自己的身份；Issue 基线不授予 push、merge 或部署能力。确定性 Agent Step、route-writer、签名 sidecar 和 ACT broker **不是 Agent**，不继承该权限，也不得另获一套 Issue 权限；Desk-owned sync 只使用 Desk 已有权限。executor LLM 的 Planner Issue token 与 ACT broker 的高权限 service identity 必须分别注入。

反推权限不等于获得权限：请求 Agent 只能准备 scope 与 `ACT-CREDENTIAL`。新 token、续期或扩 scope 必须由目标平台管理员明确授权，Agent 不能给自己发 token。交互式 GitLab 配置中，管理员在当前任务明确给出 project／Agent／profile／到期日后，可信配置进程直接复用该用户本地已登录的 `glab` 调 API；授权判断由当前任务调用层完成，CLI reference 只是证据。helper 不能暴露给注册 Agent，operator 的 `glab` 配置也必须在 Agent 不可读的独立 principal／容器／工具沙箱中；同 UID、同可读 HOME 不合格。非交互路径才由同 Thread 获批 ACT 的 executor／broker 执行。GitLab 不要默认引导到 UI。

### 用本地 glab 签发并注入 GitLab token

单仓先建立目标 Agent 的 0600 env，并保留唯一空占位 `#GITLAB_TOKEN=`。管理员明确授权精确参数后运行：

```bash
python3 skills/buzz-agent-setup/scripts/provision_gitlab_agent_token.py \
  --host gitlab.addx.ai \
  --project <group/project> \
  --agent-name <agent-name> \
  --profile <planner|reporter|developer> \
  --token-name <unique-token-name> \
  --expires-at <YYYY-MM-DD-within-365-days> \
  --env-file "$HOME/.config/buzz/agents/<agent-name>.env" \
  --receipt "$HOME/.config/buzz/agents/receipts/<unique-receipt>.json" \
  --authorized-admin <local-glab-username> \
  --authorization-ref <non-secret-current-task-or-ACT-evidence-reference> \
  [--external auto|yes|no]   # 默认 auto：planner/reporter external，developer 非 external
```

固定 profile：Planner＝15＋`api`；Reporter＝20＋`api,read_repository`；Developer＝30＋
`api,write_repository`。`--token-name` 是可读标签，helper 会追加 128-bit 随机 operation id 作为 GitLab 上的
实际 token 名。helper 只接受本地 `glab` 回读为匹配 username 的实例管理员：这是因为设置 bot 的 external 标记需要
实例管理员权限。它拒绝带注册 Agent runtime marker 的进程，丢弃父进程中的 GitLab token／host override，
不会打印新 token，也不会覆盖已有非空 `GITLAB_TOKEN`。整个事务持有同 env 目录的 0600 sidecar lock；写入和
回滚都先比较当前内容，只回滚本操作写入的版本。创建响应必须精确回显随机 token 名、有效期、access level 和 scopes。

远端 POST 前，helper 在 receipt 目录 fsync 一个无密钥 PENDING journal；捕获到的普通异常、`KeyboardInterrupt`
或 `SystemExit` 会恢复本操作写入的 env 并撤销 token。若结果模糊，只在精确随机 operation 名唯一命中时撤销；
零个或多个命中都保留 `MANUAL_RECONCILIATION_REQUIRED` journal，不把“暂时没查到”冒充成功清理。SIGKILL、掉电
等不可捕获终止同样留下 PENDING journal，其中只有 token id/hash 与精确对象定位，不含 token 值；管理员用本地
`glab api` 对账后再重试。receipt path 必须作为该 Agent／token 标签的稳定事务键；检测到同 path 的未决 journal
就在任何 GitLab 调用前 fail closed，不能换一个 receipt path 绕过。不要用 UI 作为失败回退。

helper 生成的是 provisioning receipt。随后仍要更新 prompt 的凭据状态、重启 Agent，在真实项目跑该角色的
正／负向 canary，并把 GitLab 版本、canary 对象、运行 PID／worker 数补进最终 live receipt。Developer
至少验证 topic branch push 成功、protected branch 直推失败、`userPermissions.canMerge=false`；Planner
至少验证 Issue 基线成功且真实 Git 读、写都被拒；Reporter 验证 Git 读取成功、写入被拒。若 GitLab API 的某个职责外入口意外成功，
按平台行为预算记录，不能把 prompt 禁令写成平台强制隔离。

#### 多仓 Agent 的项目 token map

多仓配置文件只包含非 secret 的确定性绑定，必须是当前用户拥有、非 symlink、0600 的 JSON：

```json
{
  "version": 1,
  "host": "gitlab.addx.ai",
  "projects": [
    {"project_id": 388, "project_path": "FAC/factory-app", "token_env": "FAC_FACTORY_APP_GITLAB_TOKEN", "profile": "developer"}
  ]
}
```

用 [`provision_gitlab_agent_tokens.py`](../scripts/provision_gitlab_agent_tokens.py) 的 `--mapping` 一次签发所有条目；目标 env 必须为每个 `token_env` 保留一个空占位（例如 `#FAC_FACTORY_APP_GITLAB_TOKEN=`）。helper 按 profile 设置 bot 可见性（Planner／Reporter external，Developer non-external），逐项目验证精确 membership；仅对 external bot 验证 Internal 可见性无越界，失败只回滚本次事务。轮换执行同一 helper 的 `--rotate`，必须保持每个项目的 bot user id 和可见性不变；部分远端完成会保留无密钥 journal。

运行时由 owner launcher 通过 `BUZZ_GITLAB_PROJECT_TOKEN_MAP` 固定 map 路径，再走 [`gitlab_project_token.py`](../scripts/gitlab_project_token.py) 的 `--project-id` 或 `--project-path`；不能传 `--host`、`--token-env` 或完整 URL，也不能用 `--config` 覆盖 launcher 已固定的路径。wrapper 从 map 解析 host／env，先回读 token 对应的目标项目再调用项目相对 API；map 外的 FAC 或其它 Internal 项目直接拒绝。普通 Agent 的多仓 GitLab 写入在真实 L4 四项目 canary 和 receipt 完成前保持关闭。

写入门禁是代码强制的：`POST`／`PUT`／`PATCH`／`DELETE` 没有 owner 固定的 `BUZZ_GITLAB_PROJECT_TOKEN_L4_RECEIPT`、`BUZZ_GITLAB_PROJECT_TOKEN_L4_HEAD_SHA` 和 `BUZZ_GITLAB_PROJECT_TOKEN_PROVISIONING_RECEIPT` 时一律拒绝。receipt 必须是当前用户拥有、0600、非 symlink 的 `l4-project-write-receipt-v1` 文件，精确绑定当前 map SHA-256、head SHA 和 provisioning receipt SHA-256，覆盖全部写方法；Developer 项目还必须回执 token/bot/access/scope/membership 身份、GitLab 版本与 revision、验证时间／执行人、canary note id、runtime pid/worker，以及四个方法的 2xx 响应摘要；Planner／Reporter 为 `not_applicable`。provision receipt 的 `ordinary_agent_gitlab_writes_enabled=false`／`l4_canary=pending` 只表示尚未放量，不能绕过门禁；provisioning receipt 变化（包括轮换）会使旧 L4 receipt 失效；没有 CLI 覆盖开关。该 wrapper 门禁不等于同 UID OS 隔离，高影响 executor token 仍必须由独立 broker 持有。

### 手工 `glab api` 要显式指定 host

上面的 helper 自己写死 `--hostname`，并丢弃父进程继承的 `GITLAB_HOST`，不受这个坑影响。但签发之外的**手工** `glab api`（吊销 token、标 external、查成员、回读）没有这层保护：在非 git 仓目录里（例如 `~/.config/buzz/agents`）glab 无法从 remote 推断实例，默认打 gitlab.com。表现是写请求「偶发 401 / 空输出」——这不是 token 或权限问题，别去查 token。

```bash
# 在非 git 仓目录里手工调 API：命令里显式带 host
GITLAB_HOST=gitlab.addx.ai glab api "projects/<id>/access_tokens"
# 或者先 cd 到该实例的 git 仓再执行；helper 内部用的是 glab api --hostname gitlab.addx.ai
```

写请求之后一律再 GET 回读；空输出不能当成功。

## 3. Agent 可被 @ 的三个条件

| 条件 | 配置 | 缺失后果 |
|---|---|---|
| Channel role | 该频道的 owner/admin（open 频道里任何成员）用上述 `BUZZ_CLI` 执行 `channels add-member --channel <CH> --pubkey <hex> --role bot`；能不能加还受 agent 自己的 `channel_add_policy` 限制，见下文「谁能把 agent 拉进频道」 | 不进入 Agent 目录 |
| kind:0 profile 带 NIP-OA owner 背书 | Agent 启动 env 含自己的 `BUZZ_AUTH_TAG` | owner 身份不可验证 |
| owner 签发 kind:30177 策略 | `d` tag 是 Agent pubkey；content 含 name／parallelism／respond_to | Desktop 隐藏，无法 @ |

CLI 创建的 Agent 不会自动发 30177。用 [references/scripts](scripts/README.md) 的 `mint-agent.py` 与 `publish_event.mjs` 铸身份、签名并经 NIP-42 发布。

### 谁能把 agent 拉进频道：`channel_add_policy`

对照 relay-v0.2.1 源码核对过（ADR-0018）：

- open 频道里任何成员都能用 `channels add-member --role bot` 把别的身份加进来；private 频道只有该频道的 owner/admin 能加。加成 bot 角色没有额外限制。
- 能不能加还要看**被加的 agent 自己**的 `channel_add_policy`：`anyone`（默认值）、`owner_only`（只有它的 NIP-OA owner 能加）、`nobody`（谁都不能加）。策略由 agent 用自己的身份执行 `buzz channels set-add-policy` 设置，发的是 kind:10100。CLI 读不出当前值，所以注册时显式设一次，并记进 receipt：

```bash
( set -a; source <name>.env; set +a
  "$BUZZ_CLI" channels set-add-policy --policy <anyone|owner_only|nobody> )
```

- 按 agent 类型设：
  - **业务角色 agent 保持 `anyone`**：它固定了 `BUZZ_ACP_CHANNELS`，被拉进清单外的频道也不会回应。频道 admin 的邀请就当作入群申请：`buzz_agent_join_requests.py` 在群里发申请，owner 在同一个 Thread 同意后由它改配置、重启，见 [agent-channel-join.md](agent-channel-join.md)。
  - **平台类 agent 设 `owner_only`**：它不固定清单，被拉进哪个频道就在哪个频道回应，所以只能由 owner 拉。
  - **executor 设 `nobody`**：永远不经邀请进入新频道。
- 被加进去之后，relay 给 agent 发入群通知（kind:44100）。harness（desktop-v0.5.23）收到后：频道在 `BUZZ_ACP_CHANNELS` 里，或者没设这个变量，就动态订阅，不用重启；不在清单里只打一行 debug 日志就跳过，agent 出现在成员列表里但谁 @ 它都不回。清单本身只在启动时读一次，改了要重启。

## 4. Env 与注册

普通角色／Desk Agent 一 Agent 一份 `~/.config/buzz/agents/<name>.env`，权限 0600：

```bash
BUZZ_RELAY_URL=https://buzz-sg.addx.live
BUZZ_PRIVATE_KEY=nsec1...
BUZZ_AUTH_TAG='["auth","<owner_pub>","","<sig>"]'
BUZZ_ACP_BINARY=<readlink -f 后的绝对 buzz-acp 路径>
BUZZ_ACP_BINARY_SHA256=<该文件的 64 位 sha256>
BUZZ_ACP_AGENT_COMMAND=<absolute-path>/acp-media-proxy/<sha256>/claude-agent-acp
BUZZ_ACP_MEDIA_ADAPTER_COMMAND=<absolute-path>/node_modules/.bin/claude-agent-acp
BUZZ_ACP_MEDIA_BUZZ_CLI=<absolute-path>/buzz
BUZZ_ACP_AGENT_ARGS=
BUZZ_ACP_RESPOND_TO=anyone
BUZZ_ACP_ALLOWED_RESPOND_TO=anyone
BUZZ_ACP_SESSION_POLICY=thread
BUZZ_ACP_AGENTS=4
BUZZ_ACP_SYSTEM_PROMPT_FILE=<absolute-path>/<name>.prompt.md
BUZZ_ACP_CHANNELS=<CH>
BUZZ_ACP_AGENT_OWNER=<owner_pub>
```

`BUZZ_ACP_BINARY`／`BUZZ_ACP_BINARY_SHA256` 固定最终启动的 `buzz-acp` ELF，不从 `PATH` 猜版本；升级前先对 canonical regular executable 执行 `file`（必须是 ELF）和 `sha256sum`，把路径和摘要一起原子写进 0600 env，再让审计器用 launcher 的同一 fd 复核并执行。二者缺一、摘要不匹配或脚本/shebang 入口都会拒绝启动，避免另有未固定的解释器链。

图片代理默认启用，且 shim basename 必须与真实 adapter 相同；安装、能力协商、限制、L4 和回滚见 [acp-media-proxy.md](acp-media-proxy.md)。显式恢复 stock text-only 行为时，把 `BUZZ_ACP_AGENT_COMMAND` 改回 `BUZZ_ACP_MEDIA_ADAPTER_COMMAND` 的值，删除两个代理用的 `BUZZ_ACP_MEDIA_ADAPTER_COMMAND`／`BUZZ_ACP_MEDIA_BUZZ_CLI` 键，并写入 `BUZZ_ACP_MEDIA_MODE=stock_text_only`。没有这个显式标记，缺图片代理是升级失败，不会以 N/A 混过去。

`BUZZ_AUTH_TAG` 的**单引号不能省**：`mint-agent.py` 输出的 `auth_tag` 是裸 JSON，`source` 会吃掉里面的双引号，背书损坏后表现为 `Auth failed: restricted: not a relay member` 重启循环，见 [troubleshooting.md](troubleshooting.md)。

`respond_to` 必须与 30177 一致；团队 Channel 用 `anyone` 或显式 allowlist，不能依赖默认 `owner-only`。

### 平台 Desk：一个 Agent 加入多个 Channel

平台 Desk（如 `gitsecops-desk`，模型见 [fchac-model.md](fchac-model.md)「平台 Desk」）是 M:N 里「一个 Agent、很多 Channel」的例子，配置和普通 Desk 只有这几处不同：

- **平台类 Agent 不固定 `BUZZ_ACP_CHANNELS`，env 里不设这个键。** 平台类指跨 Channel 接需求的平台 Desk（`gitsecops-desk`）和技术平台类 dev（`skill-dev`）：它们可被拉进任何 Channel，订阅面就是它当前是 bot 成员的全部 Channel（实测：`skill-dev` 该项留空时同时服务 skill 与 golf 两个 Channel；完全不设键与留空是否等价没有单独验证，同样以启动日志为准）。**它的 `channel_add_policy` 必须设成 `owner_only`**（见上文「谁能把 agent 拉进频道」）：这样只有 owner 能用 `channels add-member --role bot` 拉它进频道，「成员一变订阅面就变」才是 owner 的动作。保持默认的 `anyone`，任何频道的 owner/admin（open 频道里任何成员）都能把它拉进去，它会立刻开始回应（skills#144 更正了此前「只有 owner 能加成员」的说法）。私有 Channel 它自己不能 `join`（`restricted: channel is private`）。固定清单反而让「拉进新 Channel」多一步 env 编辑＋重启，与平台 Agent「随叫随到」的定位冲突。业务 Channel 的角色 Agent（`-desk`／`-dev`／`-bug`…）不适用本条，仍固定 `BUZZ_ACP_CHANNELS` 到自己的业务 Channel（见上文 env 示例）。
- **启动日志核对**：`subscribed to channel` 日志行（后跟 Channel UUID）的条数必须等于它当前的 bot 成员 Channel 数（以 owner 的成员名单为准）。多出来的说明它被拉进了不该进的 Channel，由 owner `channels remove-member` 移出；少了说明新加入的 Channel 还没订阅上（见本节最后一条）。30177 只发一份，`parallelism` 与 `BUZZ_ACP_AGENTS` 一致。
- **不固定清单后，靠 prompt 与配置守边界**：
  - prompt 必须写「每个 turn 先读**触发事件所在 Channel 的 Canvas**，不读别的 Channel」，回复只回原 Thread（即**触发事件的 Channel** 里的那个 Thread），不向其它 Channel 发消息，跨 Channel 只交换中央仓 Issue 链接。
  - 责任人 helper 配置（`BUZZ_RESPONSIBLE_CONFIG`）的 `channels` 里给**每个新拉进的** Channel 追加其 UUID，否则该 Channel 的责任人通知全部 fail closed。
- prompt 里的 `buzz messages send` 模板必须带 `--reply-to <THREAD_ROOT>`（取法与例外见 [SKILL.md](../SKILL.md) 第 3 步）；只回原 Thread 的要求要落到这个参数上，光写文字模型会漏。
- **平台 Desk 只签中央仓的 Planner token**，不签任何业务仓 token（见 [agent-credentials.md](agent-credentials.md)）。注意 helper 启动时**必须**读到一个非空的 `GITLAB_TOKEN`（即使只发 `canvas_alias`，缺失就直接 `missing GitLab token environment variable`），所以平台 Desk 要先签好中央仓 token 才能 @ 人；中央仓通常是 public，Issue 只放证据链接。
- 加一个业务 Channel = 三步：owner `add-member --role bot` → 在责任人 helper 配置的 `channels` 追加该 Channel 的 UUID → 在该 Channel 的 Canvas 追加「平台 Agent」一节（pubkey、怎么找它、`-dev` 转交格式）。harness（desktop-v0.5.23）收到入群通知就动态订阅新频道，不用重启：以日志里的 `membership notification: subscribing to new channel`（带该 Channel UUID）核对；没出现再查它在该频道的角色是不是 `bot`、30177 与 `respond_to` 是否一致。这行日志是从源码读出来的，本机日志里还没实际出现过；它和启动时的 `subscribed to channel` 是两种写法，所以不重启时，上面「启动日志核对」数到的条数会比实际订阅少。

用 Agent 自己身份注册档案并 join，再由 owner 设 bot role：

```bash
( set -a; source <name>.env; set +a
  "$BUZZ_CLI" users set-profile --name <name> --about "..."
  "$BUZZ_CLI" channels join --channel <CH> )
"$BUZZ_CLI" channels add-member --channel <CH> --pubkey <agent_hex> --role bot
```

Harness 必须用官方 `@agentclientprotocol/claude-agent-acp`；Codex 用 `@agentclientprotocol/codex-acp`，并在同一份 0600 env 里加 `INITIAL_AGENT_MODE=agent-full-access`——Buzz 显示的 `bypassPermissions` 不会传到 Codex 会话，内层默认 `workspace-write` + `network_access: false`，agent 能收消息、能加表情，但回帖命令 DNS 失败（详见 [troubleshooting.md](troubleshooting.md)「永远不回复」根因 B）。不要使用 zed 的旧 Claude adapter，它会丢 turn 完成信号。

### GitLab 同步不在 Agent runtime 里运行

按 [ADR-0008](../../../docs/05-adr/0008-run-gitlab-sync-from-owner-systemd-timer.md)，GitLab → Buzz 同步由 owner 的 `systemd --user` timer 直接运行 `gitlab_buzz_sync_timer.py`，同步、路由与摘要路径里没有 LLM。因此 Desk 和其它角色 Agent 一样使用普通 buzz-acp、普通 `claude-agent-acp`／`codex-acp` 与上面的 env；不需要为同步配置 heartbeat、专用 buzz-acp 构建、只读 Agent 模式、专用 Codex home 或 exec 规则，也不需要为此采集 runtime receipt。

- Desk prompt 只追加 [普通 Desk 片段](gitlab-buzz-sync.desk-prompt.md)：解释同步由 timer 完成，不运行 runner／publisher；Channel 消息（包括「@Desk gitlab sync」）不能触发同步。
- timer 与 Desk 同一 Unix UID、读同一份 Desk 0600 env（Desk 私钥、`BUZZ_AUTH_TAG`、Desk GitLab token、`BUZZ_DESK_RUNNER_MANIFEST`）；需要多仓 Agent 时，owner launcher 额外白名单 `BUZZ_GITLAB_PROJECT_TOKEN_MAP`，如放量写 canary 再固定 `BUZZ_GITLAB_PROJECT_TOKEN_L4_RECEIPT`、`BUZZ_GITLAB_PROJECT_TOKEN_L4_HEAD_SHA`、`BUZZ_GITLAB_PROJECT_TOKEN_PROVISIONING_RECEIPT`，且只从 owner 固定 env 注入，不能由消息、Issue、Canvas 或模型设置。白名单 launcher 丢弃其它已导出变量、secret 只留在进程环境不进 argv；unit/plist、launcher、启停与回滚见 Linux [systemd](systemd/README.md) 或 macOS [launchd](launchd/README.md)。
- `INITIAL_AGENT_MODE=agent-full-access` 仍只是 Codex adapter 能执行回帖命令的通用前提（见上文与 troubleshooting），与同步无关；同步的放量证据是 timer 的 L4 receipt v3，schema 与检查见 `tests/fixtures/naturehood-gitlab-buzz-release-receipt.schema.json` 和 `tests/test_naturehood_gitlab_buzz_release_receipt.py`。

## 5. 凭据清白启动

私钥、`BUZZ_AUTH_TAG`、GitLab／SaaS token 不进 Git、消息、日志或 argv。所有持久 agent 只使用 [canonical `run-agent.py`](scripts/run-agent.py)：由固定 `/usr/bin/python3` 直接执行，不经过 shell，也不读取父进程的 `HOME`、`PATH`、`BASH_ENV` 或其它环境。launcher 从 OS 账号数据库取得 home/uid，用 `O_NOFOLLOW` 打开 owner 0600 env，在同一 fd 上验证类型、owner、mode、大小和读前后稳定性；env 值按 literal shell word 解析，绝不执行 `source` 或展开 `$VAR`，然后从空字典构造 buzz-acp 环境。

```bash
REL=<immutable-release>
install -m 0500 "$REL/references/scripts/run-agent.py" ~/.config/buzz/agents/run-agent.py
/usr/bin/python3 -I -m py_compile "$REL/references/scripts/run-agent.py"
```

env 的 `AGENT_WORKDIR` 必须是 `~/buzz-agent-work/` 下的 canonical、当前用户所有且组/其他人不可写的 Git checkout。`BUZZ_AGENT_SAFE_PATH` 每一段必须是绝对 canonical 目录、可信 owner 且组/其他人不可写；任何 agent 要调用全局 npm CLI（如 lark-cli），先用 owner shell 的 `which <cli>` 找到真实版本目录，再把该目录追加在既有条目之后。launcher 同样回读 proxy、真实 adapter、Buzz CLI、Claude wrapper 和最终 buzz-acp 的 canonical owner/mode；Claude 的 `HARNESS_CLAUDE_WRAPPER` 与 `CLAUDE_CODE_EXECUTABLE` 必须解析到同一文件，并显式提供与 wrapper 对应的 canonical `CLAUDE_CONFIG_DIR`（`claude-buzz` 只能用 `~/.claude-buzz`，`claude-glm` 只能用 `~/.claude-glm`）。只设置 `PWD` 不算切换目录，launcher 会在 exec 前真实 `chdir` 到已校验 checkout。

### 白名单只管启动那一刻：`~/.bashrc` 会把个人密钥灌回 agent 的 Bash 工具

上面的 canonical launcher／空环境白名单**只决定 buzz-acp 进程启动那一刻的环境**。Claude Code／ACP 类 agent 每次调 Bash 工具，都会另起一个 login／interactive shell，这个 shell 要读 `~/.profile`、`~/.bashrc`。如果 `~/.bashrc`（或它 source 的文件）里有 `source ~/.bash_secrets` 这类「加载人的个人密钥文件」的行，owner 私钥、云服务 token、个人 SaaS 密码就在**每一次** Bash 调用里被重新灌进 agent 的 shell：启动器的白名单形同虚设，`/proc/<pid>/environ` 上看不出来，agent 自己 `env` 一下却全在。agent 与 owner 通常是同一个 Unix UID，文件权限（0600）也拦不住它直接读那个文件。这个洞是一次真实的隔离事故里发现的（泛称：owner 私钥、云服务 token、个人 SaaS 密码进了 agent 的 shell），不是理论风险。

**不要再要求 agent「调 shell 工具时设 `login: false`」**：Claude Code／ACP 的 Bash 工具入参只有 `command` 和 `description`，没有 `login` 参数，这条要求根本做不到，写进 prompt 或 skill 只会制造已防护的错觉。防线必须放在主机侧（下面的门禁），并配合 owner 声明的预期身份变量（如 `SUPERSET_EXPECTED_USER`）做运行时比对。

**检测**（只列变量名，不打印值；用 owner 的账号在普通终端跑，模拟 agent：带 agent 标记、从 `env -i` 起）：

```bash
# 1. 有哪些疑似密钥变量会进 agent 的 login shell（期望：无输出）
env -i HOME="$HOME" USER="$USER" PATH=/usr/bin:/bin BUZZ_ACP_AGENT_OWNER=x bash -lc 'env' \
  | grep -E '^[A-Za-z_][A-Za-z0-9_]*=' | cut -d= -f1 | grep -i -E 'key|token|secret|pass'

# 2. 是哪个文件几行设置的（只输出「文件:行号 变量名」，值已丢弃）
env -i HOME="$HOME" USER="$USER" PATH=/usr/bin:/bin BUZZ_ACP_AGENT_OWNER=x \
  PS4='+SRC=${BASH_SOURCE[0]}:${LINENO}: ' bash -lxc true 2>&1 \
  | sed -nE "s/^\++(SRC=[^ ]*) .*[ ']([A-Za-z0-9_]*(KEY|TOKEN|SECRET|PASS)[A-Za-z0-9_]*)=.*/\1 \2/Ip"
```

第 1 条先过滤成 `NAME=` 行再 `cut`，是因为多行的值（如私钥）的续行没有 `=`，直接 `cut` 会把续行整行打出来。第 2 条不要去掉 `sed` 只看原始 `-x` 输出：xtrace 会把赋值的**值**一起打印到终端。

**修法（门禁）**：把 `~/.bashrc` 里 source 个人密钥文件的那一行换成下面的写法，让带 agent 标记的进程不加载它：

```bash
if [ -z "${BUZZ_PRIVATE_KEY:-}" ] && [ -z "${BUZZ_ACP_AGENT_OWNER:-}" ] && [ -r "$HOME/.bash_secrets" ]; then . "$HOME/.bash_secrets"; fi
```

- 每个 agent 的 env 都带 `BUZZ_PRIVATE_KEY`（env 里还有 `BUZZ_ACP_AGENT_OWNER`），所以任一标记存在就认定是 agent。owner 自己的交互 shell 没有这两个变量，行为不变。
- 原位替换，不要把 source 行留在别处：agent 的 Bash 工具起的是**非交互** login shell，事故主机上个人密钥文件恰好在 `~/.bashrc` 最前面、任何「非交互就 `return`」之前就被 source，所以非交互 shell 也中招。门禁行放在这个最前面的位置，才能同时管住交互与非交互两种 shell。`~/.profile`、`~/.bash_profile`、`~/.bash_login`、`BASH_ENV` 指向的文件里若也有类似的 source 行，一并换掉。
- 实测对**运行中**的 agent 立即生效，不用重启（Claude Code 的 shell 快照没有把这些变量固化下来）；仍要重跑上面的检测命令回读：第 1 条应无输出，同时在 owner 自己的交互 shell 里确认个人变量还在。

**更强的做法（长期建议）**：给 agent 换独立的 Unix 用户。同 UID 下没有硬边界，门禁只是把「个人密钥文件被自动加载」这条明路堵上，agent 仍然能直接读同 UID 的任何文件；独立用户才是内核强制的边界（需要 sudo、迁移量大，与上文「Executor 的强制隔离」的要求同源）。在那之前可以叠加 Claude Code 内置沙箱做本地加固，见 [agent-sandbox.md](agent-sandbox.md)。

**docker 组要单独评估**：如果 owner 在 `docker` 组，同 UID 的 agent 也能用 `/var/run/docker.sock`，等同宿主 root（挂载宿主目录、起特权容器），上面所有文件权限与门禁都被绕过。要么把 owner 移出 docker 组／改用 rootless docker，要么用沙箱屏蔽 docker.sock（内置沙箱与 `docker` 不兼容，这里反而是好处）。

### 用持久用户单元托管 Agent 进程

把同一份 canonical launcher 装成 `~/.config/buzz/agents/run-agent.py`（0500），再让每个 agent 的**持久 systemd 用户单元**由固定 `/usr/bin/python3` 直接执行。不要创建 per-agent shell wrapper，也不要用 `systemd-run` 起：后者创建瞬时单元，重启机器后不会自动恢复，agent 悄悄离线。

瞬时单元还有**安全后果**：单元文件写在 `/run/user/<uid>/systemd/transient/`（目录 0755，文件 0644），里面是起单元时带进去的**整份环境**（私钥、token、密码都在），同机其它用户都读得到。有密钥的 agent 一律用下面的**持久单元 + 0600 env 文件**。**例外**是确需继承会话环境的个人助手（见 [personal-channel.md](personal-channel.md)）：要评估同机其它用户的风险（多用户主机不要这样做），必要时轮换里面出现过的密钥。证据：2026-09-21 审计发现个人助手的瞬时单元文件是 0644，内含 owner 私钥、云服务 token 和 Superset 密码（值不写进任何文档或 issue）。自查只看文件名与权限，不打印内容：`stat -c '%a %n' /run/user/$(id -u)/systemd/transient/*.service`。

```ini
# ~/.config/systemd/user/buzz-local-<name>.service
[Unit]
Description=Buzz agent <name>

[Service]
Type=exec
UMask=0077
NoNewPrivileges=yes
UnsetEnvironment=LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH PYTHONHOME PYTHONPATH PYTHONINSPECT PYTHONSTARTUP BASH_ENV ENV NODE_OPTIONS PERL5OPT RUBYOPT GLIBC_TUNABLES GCONV_PATH LOCPATH NLSPATH MALLOC_TRACE RES_OPTIONS HOSTALIASES TZDIR
ExecStart=/usr/bin/python3 -I %h/.config/buzz/agents/run-agent.py <name>
Restart=on-failure
RestartSec=5s
StandardOutput=append:%h/.config/buzz/agents/<name>.log
StandardError=append:%h/.config/buzz/agents/<name>.log

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now buzz-local-<name>.service
systemctl --user restart buzz-local-<name>.service   # 改 env／prompt 后重启，并核对启动日志
```

- 单元文件不含 secret，也不用 `EnvironmentFile=`：`UnsetEnvironment=` 在解释器／dynamic loader 启动前清掉高风险继承变量，固定解释器的 `-I` 再忽略 Python user site；secret 只由 canonical `run-agent.py` 从同一 fd 读取 0600 env，并放进它从空字典构造的白名单环境。
- 无人登录的主机也要让用户单元开机即起，需要开了 linger：`loginctl enable-linger <user>`（本机已开）。
- 日志固定追加到 `~/.config/buzz/agents/<name>.log`，排查命令见 [troubleshooting.md](troubleshooting.md)。
- 退役时对应停止、`disable` 并删除该单元，见「9. 退役 Agent」。

### Executor 的强制隔离

上述 0600 只防其他 Unix 用户读取，**不能隔离同一 UID 下的 Agent**。所以 executor 不能照搬普通 Agent 的部署方式：

- executor LLM 与所有角色 Agent／Desk 使用不同的非特权 OS principal 或独立容器；禁止共享可读 home／secret volume、补充组、Docker socket、host PID namespace、sudo、ptrace 或可进入对方 namespace 的能力。
- 平台高影响写 token 不进入 executor LLM 的 env。token 只由更小的确定性 ACT action adapter／credential broker 读取；该 broker 使用独立 principal／容器和持久 ledger。
- Agent 调 broker 时只能传 `act_id`。broker 必须自行从 Buzz 回读原始 proposal／approval event、核验 pubkey／Channel／root／时序／digest／平台与职能，再读取平台 current state、原子占用 ledger并执行 canonical payload；不得接受 Agent 提供的 `approved=true`、任意 URL／命令或替换 payload。
- 在 broker 和真实拒绝 E2E 未完成前，不给 executor service identity 签发或装载写 token，executor 保持禁用。当前候选中的 ACT parser／ledger／action adapter 仍是 TODO。

最低负向验收：以角色 Agent UID 和 executor LLM UID 读取 broker secret 文件及 `/proc/<broker_pid>/environ` 必须得到权限拒绝；尝试绕过 broker 直连目标平台必须因无 token／网络策略失败；错 pubkey、跨 Thread、过期、digest 变化与重放必须在 broker 内被拒绝。若宿主 threat model 允许 root／宿主管理员读取容器 secret，应明确它是 break-glass 运维边界，而不是日常 Agent 权限。

## 模块运行位置

> **已取代（部分行）**：下表 `issue_thread_router.py` 两行的旧 Desk router 描述只保留作参考。现行拓扑见 [gitlab-buzz-sync.md](gitlab-buzz-sync.md)：owner 的 systemd --user timer 以 Desk 身份运行确定性同步，同一轮调 Canvas route gate；同步路径里没有 LLM（ADR-0008）。

| 模块 | 运行位置 | 边界 |
|---|---|---|
| `buzz` CLI／Desktop | 操作者 Terminal | 经 relay API 配置、手动触发和查看 Workflow；不是 scheduler，不需常驻 |
| Buzz schedule | Buzz relay runtime | relay 保存其它 Agent 的 schedule Workflow 并按业务 Channel 唤醒；GitLab 同步不使用它，不做 GitLab 拉取或权限判断 |
| Desk Agent | Agent worker 主机 | 普通 Agent：Channel 入口、分诊、Issue 维护；不运行同步脚本、不解释 Canvas 路由、不手工推进 cursor |
| `gitlab-buzz-sync-<channel>.timer`／`.service` | Desk 所在主机的 systemd --user timer，同一 UID | 每 300 秒经白名单 launcher 运行零参数 `gitlab_buzz_sync_timer.py`；只带 Desk 身份白名单 env；失败只进 user journal |
| `gitlab_buzz_desk_runner.py` | timer 入口内的固定步骤 | 只从 0600 env 的 `BUZZ_DESK_RUNNER_MANIFEST` 读取 owner manifest；按固定顺序调用 sync／route，prompt 和业务消息不接触 config/state argv |
| `gitlab_buzz_sync.py` | runner 的固定子进程 | Desk-owned Agent Step；继承 launcher 的 Desk 身份白名单环境变量，完成 GitLab 扫描、binding、outbox/cursor，并以 Desk identity 发布 facts |
| `gitlab_buzz_route_reply.py --scan-once` | runner 的固定子进程 | 读取最新可信 raw Canvas，按 header 行（新消息末行，存量首行）匹配，在 canonical Thread 用唯一 Role `p` tag 回复；无 route Workflow／listener／bearer |
| 0600 sync/route config／0700 repo-channel state | Desk principal | 固定项目、代码信任锚、Role identity、cursor、outbox 与 operation ledger；secret 只在 Desk 的 0600 env |
| `issue_thread_router.py` | 与 Desk 相同的 Agent worker，本地子进程 | 确定性拉取、new/update、binding、action/checkpoint；没有独立 Agent 身份 |
| 0600 state／lock | 同一 Desk worker | 每 Channel 独立；保存 waterline、snapshot、`last_checkpoint_change_id`、outbox 与 ack ledger |
| 配置／Skill／deployment baseline | Git | 通过 MR 评审与版本化，运行时只读 |
| Issue 与三类 Desk Notes | GitLab SaaS | source event 与跨系统可恢复事实 |
| Channel／Thread／receipt | Buzz relay | 协作上下文、Thread binding 的另一端与 action receipt |
| 角色 Agent | 各自 Agent worker | 可加入多个 Channel，使用自身 scoped Git／SaaS identity |
| 职能 executor LLM | 独立职能 runtime | 只提交 `act_id`，不持有平台高影响写 token |
| ACT broker／action adapter | 隔离 OS principal 或容器 | ACT broker／action adapter 运行在隔离 OS principal 或容器；独立校验 approval／digest／ledger 后调用 SaaS |

本机即使有 `buzz` wrapper，Agent、Desk polling component 和测试也一律绕过它，直接调用上述 0.5.23 ELF。owner 的交互 wrapper 不得出现在自动化配置、脚本或测试里。

改 Agent prompt 后必须重启；harness 只在启动时读取一次，不重启会静默运行旧规则。

### 发消息前的责任人注意力门禁

每个 Agent prompt 都必须继承同一**注意力预算**：只有需要某个人行动、评审、决定或解除阻塞才通知；普通进展、背景与 FYI 不 @ 人。候选只来自 GitLab 结构化字段或站立席位的 GitLab 用户名（`person` locator），用户名经 owner 管理的 `people_file` 映射；禁止读取自由文本里的 `@name`，不得 `@all`。

定时 Workflow 的**最终报告**例外：视为需要人看。helper sources 只用站立受众的 `person` locator（`channel_admin`；进展／数据再加 `pm`；周期结果再加 `core_eng`）。ack 不加。GitLab 同步不是 Workflow。见 [scheduled-workflows.md](scheduled-workflows.md)。

把下面的通用片段追加到**所有注册 Agent** 的 owner prompt；它不属于 GitLab sync tick 片段：

```text
## 责任人通知（所有 Agent 通用）

只有需要某个人行动、评审、决定或解除阻塞时，才准备一份 0600 structured request JSON，并只运行：

python3 <FIXED_SKILL_RELEASE>/scripts/buzz_send_with_responsible_mentions.py --input <STRUCTURED_REQUEST_JSON>

owner 已在本 Agent 的 0600 runtime env 固定注入 BUZZ_RESPONSIBLE_CONFIG；不要读取、覆盖或把它放进消息。不要给命令追加 --mention、--content、Channel、Thread、source、用户名、pubkey 或任何业务值。

request 只能是：
{"version":1,"channel_id":"<uuid>","reply_to":"<64hex-root>","content":"<不含 @ 或 nostr: 的行动正文>","sources":[<结构化 locator>]}

允许的 source locator：
- GitLab：{"kind":"gitlab","project_id":<整数>,"object":"issue|mr","iid":<整数>,"field":"assignees|reviewers|author|milestone_owner"}；reviewers 仅用于 mr。
- 站立席位：{"kind":"person","username":"<GitLab 用户名，必须在 people_file>"}。

禁止 free_text source、正文名字、@all/@everyone、bot、非成员或猜测 pubkey。helper 会重读当前 GitLab 与 `people_file`、精确解析当前 Channel human member、去重并限制最多 3 人；不能通知者写入未通知原因。只有 helper 可以生成 p tags、发送并严格回读。status=sent/duplicate 才算完成；status=error 或命令失败时停止并报告，不要自行降级调用 buzz messages send。

定时 Workflow 最终报告必须带站立受众 person locator（channel_admin，进展/数据加 pm，周期结果加 core_eng）；ack 与交互 FYI 不加。
报告里需要某个人行动的事项，按 workflow 正文要求在同一 Thread 另发行动消息，sources 用该项责任人的 GitLab 用户名作 person locator。
```

request 中的 `content`、Channel、Thread 与 sources 都是数据文件内容，不进入 shell argv 或 env；argv 只含固定脚本与 input 文件路径。`BUZZ_RESPONSIBLE_CONFIG` 指向 owner 固定的 0600 config，配置含 sender identity、owner-only state dir、GitLab fixed origin/token variable/project allowlist、Channel allowlist、`people_file` 路径与固定 Buzz CLI digest。Agent 不得动态构造或替换该配置路径。

**`BUZZ_RESPONSIBLE_CONFIG` 指向的配置文件长什么样**：完整示例见 [scripts/buzz-responsible-mentions.example.json](scripts/buzz-responsible-mentions.example.json)（全是占位值，不含真实 pubkey、token 或路径；以 `buzz_send_with_responsible_mentions.py` 的 `load_config` 为准）。键是**严格**校验的，顶层与每一层都不能多、不能少，多一个或缺一个都报 `… keys … do not match the contract`。

**类型是坑点：`version` 是整数 `2`、`projects` 是整数 project id 列表，写成字符串会被拒绝**（如 `"version": "1"`、`"projects": ["2048"]`）。照抄别人配置时最常见的就是这两处，先对照下表。

| 字段 | JSON 类型 | 约束 |
|---|---|---|
| `version` | 整数 | 恰好 `2`（`1` 是旧的 Canvas alias 版本，已被拒绝）；**`version` 是整数 `2`**，写成字符串 `"2"` 会被拒绝（`config keys or version do not match the contract`） |
| `sender_pubkey` | 字符串 | 64 位小写 hex；必须等于运行环境 `BUZZ_PRIVATE_KEY` 推出的公钥，不一致启动即失败 |
| `state_dir` | 字符串 | 绝对、已规范化路径（无 `..`、无 symlink）；目录不存在时 helper 会以 0700 创建，已存在必须归本用户且 0700 |
| `gitlab.base_url` | 字符串 | 精确 origin，无路径／query／凭据；`https`，`http` 仅限 loopback |
| `gitlab.token_env` | 字符串 | 环境变量**名**（不是 token 值）：大写字母开头、只含大写字母／数字／下划线，不以 `BUZZ_` 开头；值从 Agent 的 0600 env 读取 |
| `gitlab.projects` | 整数数组 | **`projects` 是整数 project id 列表**，非空、无重复；写成字符串（如 `["2048"]`）会被拒绝（`gitlab.projects must be a non-empty unique project list`） |

`gitlab.token_env` 仍是 GitLab→Buzz 同步配置的单项目 token 入口；它不允许被模型动态替换。多仓 Agent 的项目级访问另由 0600 `project token map` 绑定 `project_id`／`project_path`／`token_env`，并只经 `gitlab_project_token.py` 解析，不能把 `gitlab.projects` 列表当成 token 权限范围。
| `channels` | 字符串数组 | 允许发送的小写 Channel UUID，非空、无重复 |
| `people_file` | 字符串 | 绝对路径；GitLab 用户名 → Buzz pubkey 的 JSON 对象，必须是 0600、归本用户、非 symlink，每次发送现读；只能列真人，不能含 Desk／agent／本 helper 的 sender pubkey。与 GitLab 同步的 `people_file` 是同一份，由 `gitlab_buzz_people_generate.py` 生成 |
| `buzz.cli_path` | 字符串 | 绝对路径，必须是路径含 `buzz-0.5.23` 目录、文件名 `buzz` 的原始 ELF；属主为 root 或本用户且 group／world 不可写；不能是 `~/.local/bin/buzz` wrapper 或 symlink |
| `buzz.cli_sha256` | 字符串 | 上述 ELF 的 64 位小写 SHA-256（`sha256sum <cli_path>`）；不匹配即拒绝 |

文件本身必须是 **0600**、归运行 Agent 的用户所有、非 symlink；**`state_dir` 是 0700**。示例里的 `/abs/...`、`example.test`、全 0 的 hex 都要换成本机／本 Channel 的真值；`sender_pubkey` 用真实 pubkey，不要照抄别人的配置。

**责任人怎么解析**：helper 把候选 GitLab 用户名（`gitlab` locator 从 MR／Issue 字段取出的，或行动消息、站立席位用的 `person` locator）在 `people_file` 里查成 Buzz pubkey，不再看 Canvas 别名表，也不再按「同名 Buzz profile」匹配。查不到写 `未通知：<用户名>(profile_not_found)`。`people_file` 由 `gitlab_buzz_people_generate.py` 按用户名和邮箱前缀生成，人员变动后要重跑生成器才会更新。查表之后，发送时仍核对该人是当前 Channel 的 human member。行动消息与定时报告的站立席位共用同一个 helper 和「每条最多 3 人」预算，见 [scheduled-workflows.md](scheduled-workflows.md)。

helper 只接受 `people_file` 里的唯一 pubkey 且仍为**当前 Channel human member**的 pubkey，role 必须是 owner/admin/member：`channel_admin` 席位上的人常是频道 admin，admin 是能处理事项的真人管理员所以放行；guest 是受限角色、bot 是 Agent，都不算，原因写 `not_human_member`（不在频道里是 `not_channel_member`）。GitLab 同步 @ 人共用同一个判断（[gitlab-buzz-sync.md](gitlab-buzz-sync.md)「可 @ 的人」）。它在同一 Thread／动作中去重、最多 3 人；不能解析、不是成员、是 guest 或 bot、超出预算时正文写 `未通知：<原因>`。helper 内部使用显式 `p` tag，并在发送后回读自己的 author、Channel、`e` root 和完整 tag 集合；Agent 不能自行传 `--mention`，正文中的 `@显示名` 也不能代替 tag。

### 验证运行时实际加载的 Skill revision

不要用“扫描 plugin cache 后挑目录名最大／mtime 最新的一份”证明 Agent 已加载新 Skill。cache 可以同时保留多个历史 revision；**只有 Agent harness 的已安装插件清单选中的版本及其 install metadata／registry 才是本次验收对象**。更新 plugin 后先解析唯一启用记录，再检查必需 Skill，最后重启 Agent 并验证进程启动时间晚于 prompt 与 plugin revision 变更时间。

**先搞清 agent 读的是哪一份。** 每个 harness 用**自己的配置目录**和自己的插件安装。owner 交互会话用的 `~/.claude`（插件 `addx@addx-engineering`）不是 agent 读的：拿它的 registry 验收，验的是另一份东西，agent 照样在跑旧 Skill。本机的 harness 与 [harness-failover](../../harness-failover/SKILL.md) 的 profile 一一对应：

| harness | 配置目录（registry 在 `<目录>/plugins/installed_plugins.json`） | 更新（装完**要重启 agent 才生效**） |
|---|---|---|
| Claude，`claude-buzz` 包装器（默认） | `~/.claude-buzz`，包装器设 `CLAUDE_CONFIG_DIR`；插件 `addx@addx`，marketplace `addx` | `claude-buzz plugin marketplace update addx && claude-buzz plugin update addx@addx` |
| Claude failover，`claude-glm` 包装器 | `~/.claude-glm`，同样 `CLAUDE_CONFIG_DIR`；同一个插件 id | 同上，命令里的 `claude-buzz` 换成 `claude-glm` |
| Codex，`codex-buzz` | `~/.codex-buzz`，`CODEX_HOME` | `CODEX_HOME=~/.codex-buzz codex plugin marketplace upgrade addx` |
| Grok | `~/.grok` | `grok plugin update` |

`codex-acp` 的同一份 0600 agent env 还必须写入 canonical `CODEX_PATH`（先用 `readlink -f -- "$(command -v codex)"` 解析）和 canonical `CODEX_HOME`。本机完整审计会校验这两个路径和该 agent 的 `AGENT_WORKDIR`／`BUZZ_AGENT_SAFE_PATH`，但绝不执行 Codex。它只读 `CODEX_HOME/config.toml` 的 `plugins."addx@addx".enabled` 与 canonical Git marketplace，再要求 `plugins/cache/addx/addx/` 只有一个安装目录并核对静态 metadata／完整 Skill tree；不能用审计进程 PATH 里碰巧找到的另一个 Codex，也不能从多份 cache 猜实际版本。

**marketplace 源必须是 git 远端**（`git@gitlab.addx.ai:engineering/skills.git`），或一个专用的、只跟 `origin/main` 的 detached worktree；**不能指向开发者的工作树目录**（例如把 marketplace 指到 `~/skills` 这样的本地目录）。`plugin update` 装的是源目录的**当前 HEAD**，工作树停在哪个功能分支就装哪个：2026-09-21 `~/.claude-glm` 就这样被「更新」到工作树当时停着的 09-11 旧功能分支，比更新前还旧。检查：`claude-buzz plugin marketplace list` 里 `addx` 的 `Source:` 应是 `Git (…)`。

不更新的代价：同日发现 `~/.claude-buzz` 停在 09-18 的 `a74b604b`，落后 main 342 个提交，缺 Issue 先行、ADR-0014、Desk 职能边界和 `gitlab-pipeline-health`；7 个 `-dev` 已在报 `Unknown skill: gitlab-pipeline-health`。skill 合并进 main 后 agent 不会自动跟上，要按上表更新，并纳入[本机升级清单](local-upgrade-runbook.md)。

Claude Agent 可用本 Skill 自带的只读解析器生成不含 secret 的 receipt。各 harness 用各自的 registry；`CLAUDE_CONFIG_DIR` 要先 `export`（同一条命令前面的 `VAR=值` 赋值影响不了命令行里 `$CLAUDE_CONFIG_DIR` 的展开）：

```bash
export CLAUDE_CONFIG_DIR="$HOME/.claude-buzz"   # claude-glm 的 agent 换成 "$HOME/.claude-glm"
python3 skills/buzz-agent-setup/scripts/resolve_plugin_install.py \
  --registry "$CLAUDE_CONFIG_DIR/plugins/installed_plugins.json" \
  --plugin-id addx@addx \
  --require-skill buzz-agent-setup \
  --require-skill gitlab-issue-sop
```

receipt 里的 `git_commit_sha` 要等于你要的 `origin/main` 提交。

交互式升级／诊断仍可单独使用 `resolve_codex_install()`：输入操作者显式取得的 `codex plugin list --json` 对象和 Codex cache root，用唯一 `installed=true, enabled=true` 记录定位版本，再读取该记录的 local marketplace snapshot，要求其上游类型为 Git、tracked worktree 干净且 HEAD 是完整 40 位 revision，并逐字比较必需 Skill 的 snapshot／cache 文件；安装目录带 `.codex-marketplace-install.json` 时还要与 snapshot revision 一致。这个 resolver 不在 P1 只读审计进程中执行。两条路径都会要求真实且非 symlink 的绝对目录，并校验每个 `SKILL.md` 的 frontmatter `name`。不能拿 Claude registry 验收 `codex-acp`，不能拿 Codex cache 猜 Claude runtime，也不能拿 marketplace checkout 代替已安装产物；同名 local marketplace 遮蔽 Git marketplace时必须 fail closed，不能把旧 clone 误报成新版本。

## 6. Session 与并行

- 角色 Agent 默认 `BUZZ_ACP_SESSION_POLICY=thread`，分诊／答复并行 4–5，开发 2–3且每个子进程独立 worktree，investigator 2–4。
- executor 使用 Channel scope，固定并行 1，并以 ACT ledger 幂等。
- `BUZZ_ACP_AGENTS=N` 必须与 kind:30177 `parallelism=N` 一致。
- 多 Agent 同 Channel 保持 `BUZZ_ACP_SUBSCRIBE=mentions`，否则会抢答。
- 编码 Agent 使用 queue；答疑／分析可用 steer。等待 CI 的 Agent 可提高 max turn duration。
- `BUZZ_ACP_SESSION_POLICY` 需要 Buzz Desktop 附带 harness ≥0.5.22；旧版会静默忽略。启动日志必须出现 `session_policy=thread`。

## 7. Workflow

Workflow 负责主动唤醒，不做权限决策：

- `schedule`：没人 @ 也该产出，例如 Desk 进展分析、Bug 模式分析、BI 复盘、SRE／investigator 巡检，以及 `-dev` 只读分析（周 pipeline 健康、月度架构坏味道）。平台 Desk 在职能 Channel 里还有一个只读的平台反馈周报，且必须排在各业务 Channel 的 pipeline 分析与转交之后（顺序与转交协议见 [scheduled-workflows.md](scheduled-workflows.md)）。目录见 [scheduled-workflows.md](scheduled-workflows.md)。cron 的星期字段按 relay 约定写：1=周日，周一=2，周一到周五=2-6，名字写法未验证（见 [scheduled-workflows.md](scheduled-workflows.md)「cron 的星期字段」）。
- 普通非 GitLab 外部事件可按来源使用 webhook／轮询唤醒角色 Agent 或 investigator。GitLab 变更同步见 [gitlab-buzz-sync.md](gitlab-buzz-sync.md)：不用 Workflow，也不唤醒 Desk；owner 的 systemd --user timer 运行 `gitlab_buzz_sync_timer.py`，同一轮依次运行确定性 sync 与 Canvas route gate，Thread 内指派不创建路由 Workflow。
- 无：Feature、QA 和所有 executor；executor 只接同 Thread 已获批 ACT。`-dev` 禁止实现／写仓 schedule。

```bash
"$BUZZ_CLI" workflows create --channel <CH> --yaml "$(cat wf.yaml)"
```

可复用方法、基线选择、判据和报告骨架写在对应 Skill；项目、领域背景、时区、默认指标与长期基线政策写在 Canvas；Workflow 保留 Agent mention、Skill 名，以及复盘对象、复盘周期、分析时点、业务日历切点、对比窗口、full refresh 等 Workflow 专属信息，不重复 Channel ID、project、权限或通用方法；Agent prompt 只放身份、安全边界、凭据状态和项目／数据出口 allowlist。所有已部署 prompt 必须逐字包含 [agent-prompt-contract.md](agent-prompt-contract.md) 的 common 与对应角色片段；该文件是漂移审计直接读取的 SSOT，不得另抄一份检查表。定时 Workflow 缺少适用的运行参数时，配置验收必须失败，不能让 Agent 临场猜测。任何统计都必须拉全分页，报告总数可追到 `total`／`hasMore`；拉不全时明确样本数／总数。

如果 Agent 要把内部分析结果发回 Buzz，owner prompt 必须显式写出数据出口 allowlist：允许的脱敏聚合内容、受控链接类型、固定 Channel 和同 Thread 约束。该窄路径可以写明不需要逐次披露审批；实际读者仍由 Channel ACL 决定，prompt 不复制成员名单。其它 Channel、私信、外部系统、原始行、标识符、secret 与未受控链接仍然 fail closed。Workflow 不能授予披露权限，消息正文也不能扩大这项 standing authorization。正向 L4 必须证明完整合规报告能自动回帖，负向 L4 必须证明越界目的地或敏感内容仍被拦截。

外部持续播报用正规铸造的 Agent 身份和上述 0.5.23 原始 ELF 直接执行 `messages send`，不要用 PATH wrapper 或无档案的 relay 系统身份。payload 需在 adapter 拍平，避免 `author`、`text`、`timestamp`、`channel_id`、`message_id`、`is_reply` 等保留字段。原生 Workflow 动态 `reply_to` 的已知限制见 [issue-thread-routing.md](issue-thread-routing.md)。

用原始 ELF 执行 `workflows delete` 后若只返回 `accepted=true` 但未删除，应 update 成 webhook＋noop 并加“已废弃”前缀；不要留下旧 schedule。update 必须带 `--channel`。详见 [scheduled-workflows.md](scheduled-workflows.md)「Workflow 维护须知」。

### 旧 Desk Issue poller（已取代）

> **已取代**：本小节描述旧 `issue_thread_router.py`：Desk 当 router、public-only 受众门禁、独立 service identity／签名 sidecar 门禁。它不再是现行做法，原文只保留作参考，新频道不要按它配置。现行做法见 [gitlab-buzz-sync.md](gitlab-buzz-sync.md) 与 [ADR-0004](../../../docs/05-adr/0004-run-gitlab-sync-as-desk-owned-agent-step.md)；ADR-0001 的独立服务拓扑保留为可追溯历史。

- **Issue 自动化的首个 canonical 实现不是 GitLab webhook，也没有 router Agent：Buzz relay scheduler 按 Channel 的 schedule 唤醒 Desk，Desk 与 polling component 跑在同一 Agent worker turn；针对当前 GitLab 18.0，从已核验身份的 GitLab 响应取 `Date - 1s` 上界，按 `created_at ASC` 拉稳定的 `state=all` Issue universe，本地筛 `updated_at`，再以首次显式初始化且 pin 回 Git 配置的 `deployment_baseline.max_iid` 区分 new、以 durable waterline 与 digest 去重。new 或路由事实／内容／policy 真正变化时才写 GitLab snapshot checkpoint；checkpoint 内嵌最小 routing policy 及 digest，action Note 固定同一 policy digest。历史 checkpoint 用自身 policy 验证；已完成 action 按 change id 使用匹配 checkpoint 的 policy 验证，无 checkpoint 的 crash-window action 只在 digest 仍等于当前 policy 时恢复。routes／Agent pubkey 变化保留非法跳步记忆；同 target 换 pubkey 会触发重新指派。任何旧 policy 的 pending outbox 都在轮询与 stdout replay 前 fail closed，必须先完成、迁移或取消；`status_order` 变化必须显式迁移。GitLab Note 推进的 `updated_at` 单独变化只吸收到 worker 本地 state，不再写 checkpoint／action，避免自触发循环。state 丢失时只有携带同一 Git baseline 并从 binding/action/checkpoint Notes＋Buzz roots/receipts 完整对账，才能原子重建；缺 pin 或恢复未完整即 fail closed，模糊 checkpoint-less 历史也必须拒绝。当前只允许 id／URL 回读一致且 `visibility=public` 的 GitLab project；Issue 的 `confidential` 必须是显式布尔 `false`，state 必须是精确 `opened|closed`；private／internal project 或字段缺失／null／畸形都在任何 Buzz／action Note write 前 fail closed，snapshot checkpoint Note 适用同一门禁。** Webhook 只保留为未来可替换 source adapter。
- Desk worker 的持久化协议还要保存 `last_checkpoint_change_id`；action Note 固定 `source_snapshot + previous_change_id + policy_digest`。source ID 保留 `updated_at` 以区分真实 A→B→A，业务 fingerprint 只在同一 predecessor 下吸收 Desk Note 造成的时间漂移。每个候选处理前执行 `GET project → GET Issue → GET project` fresh audience gate，刷新时间越过 scan boundary 就 defer；输出 Desk action 前再门禁，并把 raw title／description／labels 只放进 `untrusted_issue` JSON 数据帧，不放进 Buzz 协议消息或 machine Notes。root／lifecycle／receipt marker 只接受 LF 物理第一行逐字相等，不 trim、不做 substring。处理前先稳定读取完整 GitLab Note 集合，并把 action 严格接到前后 checkpoint；这样服务端 action 已写但本地 append 失败、checkpoint 已写但最终 local save 失败，以及 policy epoch 后的下一业务 update 都能幂等恢复。
- pending action 若在 Desk 输出前已落后 fresh Issue，worker 以本地 `source_stale=true, required_outcome=desk-only` guard 清空建议投递 pubkey；ack 再做 audience/content fresh-check，只接受零 mention 的 `desk-only`，随后下一轮处理新 snapshot。guard 可跨本地 replay，且不进入 immutable GitLab action Note；state-loss recovery 从 source 与当前 Issue 重新派生。
- 当前 reference candidate 仍复用 Desk 身份写 machine facts；author/pubkey 校验不能隔离受 prompt injection 影响的 Desk。生产启用前必须把协议写入迁到 LLM 不可访问的独立 service identity／签名 sidecar；它不是 router Agent。**发布状态：reference candidate；真实 public GitLab＋Buzz E2E 未完成；独立 service identity／签名 sidecar 未完成；schedule 保持关闭。** sidecar 还必须在实际 mention 发送边界执行 fresh audience gate，以 delivery nonce 绑定 receipt，并定义 late-stale routed receipt 的唯一收敛规则。

## 8. 正／负向验证

配置只有在最终状态跑完验证套件才算完成。每个正向能力都要有“配错会失败”的负向断言：

> **已取代（一行）**：下表最后一行「Desk Issue poller」属于旧 `issue_thread_router.py`，只保留作参考。现行 GitLab 同步的验证见 [gitlab-buzz-sync.md](gitlab-buzz-sync.md) 与测试方案 `docs/plans/2026-09-13-buzz-agent-setup-gitlab-buzz-sync-test-plan.md`。其余行仍然有效。

| 配置 | 正向 | 必须的负向 |
|---|---|---|
| 身份 | nsec 派生 pubkey 等于声明值 | 篡改 NIP-OA 签名一位后验签失败 |
| @ 可达 | role=bot、profile、30177 均存在 | 30177 `respond_to` 与 env 不同即失败 |
| env | 普通 Agent 拿到自身变量；executor broker 拿到写 token | 普通 Agent／executor LLM 的 `/proc/<pid>/environ` 不含平台写 token；跨 UID 读取 broker env／secret 被内核拒绝；只打印变量名 |
| prompt | 路径存在 | 进程启动时间不得早于 prompt mtime |
| token | 允许操作成功 | 明确禁止的写操作被平台权限拒绝 |
| Git | 工作副本是目标仓 | 用 Agent token 真正 `git ls-remote`；保护分支不能被 Dev 直推／merge |
| Superset | 本业务查询与 dashboard／chart 写成功 | dataset／database／connection 写、跨业务数据被拒 |
| Dagster | allowlist run 成功 | definition／schedule／sensor／allowlist 外任务被拒 |
| ACT | 合法平台管理员批准后，隔离 broker 执行一次 | 角色 Agent／executor LLM 直连平台无凭据；跨 Thread、错 pubkey、过期、payload 变化、重放、普通 webhook 均由 broker 拒绝 |
| Desk Issue poller | public project 的新 Issue 只建一个 root Thread；用 Git 中 pin 的 deployment baseline、三类 GitLab Notes 与 Buzz facts 原子恢复 state；Note-only `updated_at` 连续轮询不新增 checkpoint；routes／Agent pubkey 变更仍可按旧 policy 验证历史，保留状态机判定并在身份变化时重指派 | project id／URL／visibility 不匹配、confidential Issue、executor target、非法跳步、错 Channel／pubkey、缺失／冲突 baseline、模糊 checkpoint-less 历史、旧 policy pending action、未显式迁移的 `status_order` 变化、部分恢复落盘或失败后 cursor 跨越均拒绝 |

Prompt 引用的 Skill 既要存在于 SSOT，也要存在于 Agent 实际加载的发布产物；发布路径从插件注册表现读，不能写死旧快照。

FCHAC setup 的真实 E2E 只接 GitLab 与 Buzz。其他 SaaS 使用 mock contract 验证 allow／deny 与 token 选择，不重复验证各 SaaS Skill 已证明的 API 能力，也不接生产凭据。

离线测试与身份自检：

```bash
python3 references/scripts/nostrkit.py
python3 skills/buzz-agent-setup/scripts/run_offline_tests.py
```

普通临时写探针按创建者 ownership 清理；负向探针优先无副作用接口。**已登记为 retained 的长期 FCHAC Project／Channel 永不删除或归档，其下的 Thread、Issue、MR、comment 与其他 evidence 默认保留。** 只有人明确给出 exact child 标识并要求清理时，才可清理该子对象；不得把“探针自清理”扩大成删除 retained 父对象或批量 evidence。运行中任一步改变最终配置后，整套验证要重跑，不能沿用中间态 PASS。

## 9. 退役 Agent（完整清理清单）

退役 = 这个 agent 在每一处留下的痕迹都要清掉，只做其中几步会留下能用的凭据或悬空引用。按下面顺序做，每一步做完回读，再进下一步：

1. **停进程**：`systemctl --user stop buzz-local-<name>.service`；持久单元还要 `systemctl --user disable buzz-local-<name>.service`，否则重启机器后会被拉起来。
2. **owner 把它移出 Channel**：`"$BUZZ_CLI" channels remove-member --channel <CH> --pubkey <agent_hex>`，它所在的每个 Channel 都做一遍，再用 `channels members --channel <CH>` 回读。
3. **吊销它的 GitLab token**：每一个都要撤销，包括 `GITLAB_TOKEN` 和各仓的 `GITLAB_<REPO>_READ_TOKEN`（各在自己的项目上，逐个回读）。project／group access token 按 id 撤销，`DELETE /projects/<id>/access_tokens/<token_id>`（group 用 `/groups/<id>/access_tokens/<token_id>`）。先 GET 列表找到 id，撤销后再 GET 回读该 token 已 `revoked`。手工 `glab api` 要带 `GITLAB_HOST=gitlab.addx.ai`，见上文「手工 `glab api` 要显式指定 host」。不要把 token 值写进 issue、日志或聊天。
4. **删 relay 上的 kind:30177 策略**：owner 发一个 **kind 5** 删除事件。kind 5 **只能带一个目标**：用 `a` 标签指向那个可替换事件，`["a","30177:<owner_pubkey>:<agent_pubkey>"]`；同时给 `e` 和 `a` 会被拒，报 `invalid: deletion events must reference exactly one target via e or a tag`。用 `references/scripts/publish_event.mjs` 发布（内置 WebSocket 要 node ≥22，本机是 `~/.nvm/versions/node/v24.14.0/bin/node`，系统 node 20 跑不了）：

   ```bash
   EVENT="$HOME/.config/buzz/agents/<name>-30177-delete.json"
   ( umask 077; printf '%s' '{"kind":5,"tags":[["a","30177:<owner_pubkey>:<agent_pubkey>"]],"content":"retire <name>"}' > "$EVENT" )
   # owner 私钥只经环境变量交给 node，做法同 scripts/README.md 的「AI 非交互执行第 4 步」
   BUZZ_OWNER_SECKEY=<hex> ~/.nvm/versions/node/v24.14.0/bin/node references/scripts/publish_event.mjs wss://buzz-sg.addx.live "$EVENT"
   ```

   然后用 REQ `{kinds:[30177],authors:[<owner>]}` 回读，确认该 agent 的策略已消失，而不是只看 `OK true`。
5. **删本机配置与 state**：agent 的 env、prompt、日志、systemd 单元文件（`~/.config/systemd/user/buzz-local-<name>.service`，删后 `systemctl --user daemon-reload`）、责任人 helper 配置里它的条目，以及它的 state 目录；共享启动脚本 `run-agent.py` 仅在最后一个持久 agent 删除后才移除。env 里有私钥和旧 token，删前不要打印内容。
6. **删工作目录**：先在里面 `git status`、`git log --branches --not --remotes`，确认没有未提交改动和未推送提交；有就先处理（推送或另存），再删。
7. **清掉别处对它的引用**：Channel Canvas 的 Agent 表、频道描述、其它 agent prompt 里写的转交对象、同步路由配置（`route.json` 里的 role→mention）。漏了这一步，别人会继续 @ 一个已经不存在的 agent，或让 route gate 把消息路由给它。

只清理自己创建的对象；别人的 agent、共享 release 目录与仍在使用的配置不动。降权（不是退役）不走本节，见 [agent-credentials.md](agent-credentials.md)「bot 的 access level 改不了」。
