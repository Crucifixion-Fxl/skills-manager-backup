# 入群申请：把 agent 拉进频道就是申请，owner 在群里同意后自动开通

本页是 [ADR-0018](../../../docs/05-adr/0018-treat-a-bot-invite-as-a-join-request-the-agent-owner-approves-in-the-channel.md) 的运行手册。脚本是 `scripts/buzz_agent_join_requests.py`，由 owner 主机上的 `systemd --user` 定时任务每 120 秒运行一次，路径里没有大模型。

## 适用范围

- **管**：设了 `BUZZ_ACP_CHANNELS` 清单的业务角色 agent（`-desk`／`-dev`／`-bug`…），它们的邀请策略保持 `anyone`。
- **不管**：平台类 agent（不设清单）必须设成 `owner_only`，executor 设成 `nobody`。设置方法（`buzz channels set-add-policy`，用 agent 自己的身份执行）见 [runtime-setup.md](runtime-setup.md)「谁能把 agent 拉进频道」。
- 只支持 Linux 的 `systemd --user`；macOS launchd 版本没有做。

## 频道里看到的流程

1. 频道 admin 在 Buzz Desktop 里把 agent 加为 bot 成员（或执行 `channels add-member --role bot`）。
2. 两分钟内，agent 在频道里发一条申请，自成一个新 Thread，写清：我是谁、owner 是谁、开通后在本群能做什么不能做什么、怎么同意、多久过期。「能做什么」来自配置里的能力清单，和该频道 Canvas「## 代码仓库」清单做比对，列出有权限和没权限的仓库（各最多 10 个，其余写「等 N 个」；Canvas 里最多取 500 个仓库）。
   - owner 已经是本群成员：这条申请 @owner（Buzz 的 @ 会经飞书 bridge 推到 owner 的飞书）。
   - owner 不在群里（或只是 guest）：申请里请管理员先把 owner 加进来；owner 进群后，agent 在同一个 Thread 补一条 @owner，只补一次。
3. owner 在**这条申请消息上**点 ✅ 同意、❌ 不同意；或者在这个 Thread 里回复一行 `/approve JOIN-<id>` 或 `/deny JOIN-<id>`（`<id>` 在申请里写着）。
4. 同意后，脚本改配置、在 agent 空闲时重启它，确认订阅成功后在 Thread 里回「已开通」，并列出还要人补的事（见下文）。
5. owner 不同意，或者过期（默认 7 天）没有答复：agent 在 Thread 里说明，然后退群。
6. 几种特殊情况：
   - **owner 自己拉的**：邀请本身就是 owner 签名的动作，不再等审批：agent 先在频道发一条「正在开通」，然后直接开通。
   - **在飞书群里加的**：双向群同步用可信镜像签一条 `kind 9000` 把 agent 加进 Buzz。可信镜像只证明这条变更来自频道 owner/admin 管理的飞书桥，认不出具体是哪位飞书成员操作，所以仍须 owner 审批，不会静默开通；申请和 owner 的答复都会同步回飞书。已验证不可信镜像仍按普通成员邀请处理，agent 会说明原因和正确做法后退群；镜像目录超时、非 200、格式异常或签名资料暂缺则是“暂时无法验证”，agent 留在群里但不开通，说明原因并在下一轮重试，不能谎报成不可信。
   - **普通成员拉的**：agent 说明「只有本群管理员邀请才会转给 owner」，然后退群。open 频道里任何成员都能拉人，这是为了不让 owner 收到谁都能发起的申请。
   - **解释不了的成员身份**（找不到加人记录、agent 自助加入、加人不带 `role=bot`、最新的事件是移除）：记为 `NO_INVITE`，不猜测、不退群，但会在原群说明**无法确认邀请来源**、尚未开通，并请频道管理员移出后以 bot 身份重新邀请；同一情况只提示一次，之后出现新邀请就按新申请处理。
   - **DM**：私信 agent 时 relay 会建一个 DM 频道，它也会出现在 agent 的成员频道列表里；脚本先用 `dms list`（最多 200 个）排除，再要求候选频道的当前成员里能读到 owner/admin，才把它当作可通知的群。线上 `dms list` 可能漏掉已有私聊，而二人私聊只有 member 角色，这层证明避免把群管理提示发进私聊。`dms list` 失败时本轮整体 fail-closed，不发现新频道、不发任何未确认目标；已知群的待通知状态先持久化，下一轮能安全区分群聊和私聊后再补发，私聊永远不收群管理提示。
   - **owner 手工把频道加进了清单**：待审批或待退群（还没发退出消息）的记录直接记为已开通（outcome `manual`），不再过期退群。
   - **原本就在清单里、后来被 owner 手工移出的频道**：首轮已记为基线，脚本不会把它加回。

**邀请人怎么认**：读该频道 30 天内的成员事件——9000 加人、9001 移除（成员写在第一个 `p`）、9021 自助加入、9022 退出（成员本人签名），只看关于这个 agent 的，**以最新的一条为准**，更早的邀请不能顶替更新的默认角色加人、移除或自助加入。事件的 `created_at` 是客户端写的，relay 允许和服务器时间相差 900 秒，所以和最新一条相差 900 秒以内的事件一起算；同一个群同步 stream 发出的 9000/9001 带随机持久的 `feishu-member-stream` 和递增的 `feishu-member-seq`，在这个模糊窗口内按序号而不是随机 event id 排先后，因此同一秒的 add → remove → add 仍认最后一次，owner/admin key 合法轮换也不会断序；不同同步进程的随机 stream 不会串线。窗口里有自助加入、或最新的不是加人，记 `NO_INVITE`；有一个加人者既不是 owner、频道 owner/admin，也不是由频道 owner/admin 持有的可信镜像，就按普通成员处理；加人不带 `role=bot` 记 `NO_INVITE`；加人者全是 owner 才自动开通，频道 owner/admin 或可信镜像发起的则发申请。一个申请结束（退群、撤回）或记为 `NO_INVITE` 后，只有比上次看到的最新事件更新的事件才会重开；游标同时记事件 id，同一 stream 的更大序号即使客户端时间更早也算更新，同一 stream 的较小序号不会因客户端时间较大而重放。旧事件滚出 30 天扫描窗口不会让结论翻转；重开退群的记录时忽略 agent 自己签的退群（9022），它不能挡住管理员稍后的重新邀请。已知的保守之处：15 分钟内被移除过的旧邀请仍在窗口里，owner 在 15 分钟内重新拉人，可能因为那条旧的普通成员邀请被判为拒绝而退群，稍后再拉即可。

每条业务消息的最后一行是机器可读的 `buzz-join:v1 JOIN-<id>`（后面可能跟 `notify`／`active`／`closed`）。故障与恢复消息使用带唯一 incident id 的 `buzz-join-failure:v1` 标记：同一次故障的重试会认领同一条消息，恢复后再次发生的同类故障会得到新标记，不会误认旧消息。脚本靠这些标记在崩溃后找回已经发出的消息，不会重复发。

### 审批只认 owner 本人：在 Buzz 里签名，或在飞书里经可信镜像转来

- 脚本不假定 relay 已经验过签名：owner 的审批 reaction 与回复、关于 agent 的成员事件、崩溃找回时认领的消息，都在本地重算事件 id 并验签，验不过的一律不计。
- 其他成员点 ✅ 不算，频道 admin 也不能代 owner 同意。
- 打在别的消息上不算；撤回的 reaction 不算；早于申请或晚于过期时间的信号不算；同时有同意和不同意时，以时间最早的为准。✅ 带不带 U+FE0F 变体选择符都视为同一个。
- 回复必须在申请的 Thread 里，首行整行等于 `/approve JOIN-<id>` 或 `/deny JOIN-<id>`；后面加字、引用或 id 不对都不算。
- 申请消息被删、读不到信号时，过了期限就按过期处理（退群是安全的一侧）。
- **飞书里的回答**（ADR-0020，配置 `accept_feishu_approvals`，缺省 `true`）：owner 在飞书里对申请打的 ✅/❌，由群同步的镜像身份在 Buzz 的申请消息上打成 ✅/❌，带 `["feishu-author", <这个人的 pubkey>]`；owner 在申请话题里回复的一整行 `/approve JOIN-<id>` / `/deny JOIN-<id>`，由镜像签成话题回复（「[飞书] 名字：…」署名），带 `feishu-author` 与 `["join", "JOIN-<id>"]`。脚本另外认这两种信号，条件是：`feishu-author` 等于 agent 的 owner；作者是本频道的**可信镜像**——频道的 bot 成员，它的 kind:30177 由它的 NIP-OA owner 签名并声明 `"feishu": {"mirror": true}`，这个 owner 是本频道的 owner 或 admin（脚本用 agent 自己的身份向 relay 查一次）；回复必须带 `join` 且等于这个申请、去掉署名后首行整行是命令；表情打在申请消息上、带 `join` 时也要等于这个申请；在期限内、本地验签通过。relay 对同一身份、同一目标、同一表情只留一个 reaction：别人先在飞书里点了 ✅，owner 再点会被合并掉，这时请 owner 用 `/approve` 回复。镜像转来的普通发言本身不算。关掉 `accept_feishu_approvals` 就只认 owner 在 Buzz Desktop 或 CLI 里自己签的。已接受的风险：跑同步的那台机器（频道 owner/admin 的）技术上能替 owner 伪造一条同意，见 ADR-0020 与 [feishu-two-way-sync.md](feishu-two-way-sync.md)「飞书里同意 agent 入群」。

## 开通时改了什么

同意后按固定顺序改三处，全部幂等（已经有了就不再加）。前两处做不了只列为待补；env 清单是让 agent 在新频道回应的开关，放在最后改，不会出现半开通：

1. **prompt 频道表**：env 里 `BUZZ_ACP_SYSTEM_PROMPT_FILE` 指向的 prompt 文件，在一对标记之间追加一行。这一行**只写频道 UUID**、加入日期、JOIN id 和 owner 配置里有权限的仓库名；频道名和 Canvas 里的任何文字都不写进去——它们由频道成员控制，写进 system prompt 就成了提示注入面。prompt 读不到、没有这对标记或写入失败就不动 prompt，列为待补。
2. **责任人配置**：env 里 `BUZZ_RESPONSIBLE_CONFIG` 指向的配置，`channels` 追加该频道，否则 agent 在新频道里通知人会 fail closed。配置读不到、格式不对或写入失败时列为待补。
3. **env 清单**：agent env 里唯一一行 `BUZZ_ACP_CHANNELS=` 追加该频道，保留原来的引号和 `export` 前缀，文件保持 0600。和 `provision_gitlab_agent_token.py` 共用 env 目录里的同一把锁；和本轮开头读到的内容比较，被别的工具改过就本轮失败，不覆盖，下一轮重来。

然后**只在 agent 空闲时重启** `buzz-local-<name>.service`：

- 单元必须是 loaded 且 active；停掉的单元不会拉起，本轮报错，等 owner 处理。
- harness 的 INFO 日志里没有「回合开始/结束」这类行，一个回合可以几十分钟不写日志，所以不看日志判断忙闲，而是看单元的 cgroup：只剩 `buzz-acp` 一个进程才算空闲；有 agent 子进程（回合进行中，或 agent 池还热着）就推迟到下一轮。**不设上限，从不强制重启**，所以一直很忙的 agent 要等池空闲回收后才会开通；急的话 owner 可以手动重启。
- 一次重启覆盖这一轮所有已开通的频道。重启后单元必须是 active，且日志在重启之后出现 `subscribed to channel <频道 ID>`，才在 Thread 回「已开通」。日志来源：配置了 `log_file` 就读这个文件在重启前记下的位置之后新增的部分；没配就读 journal，重启前先取 journal cursor，核对只看 `--after-cursor` 之后的条目，重启前同一秒的旧行不算。核对不到就本轮失败，下一轮只重试核对，不重复改文件、不重复重启。
- 核对时如果发现 env 里已经没有这个频道（被别的工具覆盖，例如 harness-failover 改写 env 时不拿这把锁），就退回「已同意」，下一轮重新写入并重启；最多重新写入 2 次，之后不再重启，只报错提示检查改写 env 的工具。

「已开通」里列出的待补项：

- 请频道 admin 在 Canvas 的 Agent 表加一行（给出 agent 名、pubkey、一句话主业）；
- 本群 Canvas 列出、但 agent 没有权限的仓库：owner 另走 token 签发，见 [agent-credentials.md](agent-credentials.md)；
- prompt 或责任人配置没改成：owner 手工补。

### prompt 约定

把频道表放在一对标记之间，出口授权写成指向这张表，不在别处再列频道名：

```markdown
<!-- buzz-agent-channels:v1 -->
| Channel | ID | 性质 |
|---|---|---|
| `naturehood` | `<频道 UUID>` | 你的主战场：代码仓 … |
<!-- /buzz-agent-channels:v1 -->

## 协作出口授权
Owner 已授权你把脱敏结论和受控链接回复到触发事件所在 Channel（见上方频道表）的原 Thread。
```

脚本追加的行形如：

```markdown
| — | `<频道 UUID>` | <日期> 经 owner 同意加入（JOIN-<id>）。你在这里有权限的仓库：…；该频道其它仓库你没有权限。缺权限的请求如实说明并停下，不借别的凭据。 |
```

第一列（频道名）固定写 `—`；owner 想要可读的名字，自己手工改这一格。

## 配置

`BUZZ_JOIN_CONFIG` 指向 owner 维护的 0600 JSON，示例见 [scripts/buzz-agent-join.example.json](scripts/buzz-agent-join.example.json)。键是严格校验的，不认识的键一律拒绝。

| 字段 | 说明 |
|---|---|
| `version` | 固定为 `1` |
| `owner_pubkey` | 这些 agent 的 owner。每个 agent env 里的 `BUZZ_ACP_AGENT_OWNER` 必须等于它，否则该 agent 本轮报错、不处理，其他 agent 照常 |
| `buzz.cli_path`／`buzz.cli_sha256` | 固定的 `buzz-0.5.23` 原始 ELF 与它的 SHA-256，不能是 `~/.local/bin/buzz` wrapper |
| `state_dir` | 0700 状态目录，建议 `~/.local/state/buzz-join` |
| `request_ttl_seconds` | 可选，申请过期时间，默认 604800（7 天），范围 3600–2592000 |
| `accept_feishu_approvals` | 可选，布尔值，缺省 `true`：也认可信镜像转来的、owner 在飞书里的 ✅/❌ 与 `/approve`（ADR-0020，见上文「审批」） |
| `agents[].name` | agent 名，出现在频道消息里 |
| `agents[].env_file` | agent 自己的 0600 env 文件；prompt 路径和责任人配置路径从它的 `BUZZ_ACP_SYSTEM_PROMPT_FILE`、`BUZZ_RESPONSIBLE_CONFIG` 读 |
| `agents[].unit` | 托管它的 systemd 用户单元，如 `buzz-local-nh-dev.service` |
| `agents[].log_file` | 可选：该单元的追加日志文件（[runtime-setup.md](runtime-setup.md)「用持久用户单元托管 Agent 进程」里的 `StandardOutput=append:`）。单元输出到 journal 的不填 |
| `agents[].capabilities.summary` | 一句话主业，会发进频道 |
| `agents[].capabilities.repos` | agent 的 GitLab token 实际覆盖的仓库路径，用来和频道 Canvas 的「## 代码仓库」比对；只有这里的仓库名会写进 prompt |

**首次运行**：每个 agent 当时已经在的所有频道（清单内外都算）记为 `BASELINE`，不发消息、不退群，只在输出里计数；之后手工加进清单的频道也会记 `BASELINE`。某个频道从成员列表里消失满 600 秒，才删它的基线（之后再被邀请就按新申请处理）或把进行中的申请记为撤回，防止一次读不全就误判；撤回时记下原状态，频道再出现且期间没有新的成员事件就恢复原状态继续（已同意、已写进 env 的记录总是恢复并完成开通）；成员列表为空时本轮直接跳过这个 agent。

## systemd 单元

`<immutable-release>` 是已评审的 40 位 commit 固定副本（owner 所有、组和其他人不可写），必须含 `scripts/buzz_agent_join_requests.py` 及其依赖 `scripts/gitlab_buzz_sync.py`、`scripts/buzz_responsible_mentions.py`、`references/scripts/nostrkit.py`。不要用自动更新的插件目录。

```ini
# ~/.config/systemd/user/buzz-agent-join.service
[Unit]
Description=Buzz agent join requests (ADR-0018)

[Service]
Type=oneshot
UMask=0077
NoNewPrivileges=yes
TimeoutStartSec=10min
Environment=BUZZ_JOIN_CONFIG=%h/.config/buzz/join/config.json
ExecStart=/usr/bin/python3 <immutable-release>/scripts/buzz_agent_join_requests.py
```

```ini
# ~/.config/systemd/user/buzz-agent-join.timer
[Unit]
Description=Run Buzz agent join requests every 120 seconds

[Timer]
OnActiveSec=1min
OnBootSec=2min
OnUnitActiveSec=120
Persistent=true
Unit=buzz-agent-join.service

[Install]
WantedBy=timers.target
```

单元里没有任何密钥：脚本从配置列出的各 agent env 文件读取它们的身份，只交给 Buzz CLI 子进程的环境，不进 argv、日志和消息。子进程的 HOME、PATH、代理等只取自这个定时任务自己的环境，`BUZZ_*` 身份只取自该 agent 的 env。`systemctl`／`journalctl` 用 `/usr/bin` 下的绝对路径，只带 HOME、PATH、`XDG_RUNTIME_DIR` 等最小环境。脚本和 agent 是同一个 Unix 用户，读写这些文件本来就在 owner 的权限内。`TimeoutStartSec` 防止 relay 不通时一轮卡住（每个 CLI 调用最多 45 秒）。

```bash
systemctl --user daemon-reload
systemctl --user start buzz-agent-join.service      # 先手动跑一轮
journalctl --user -u buzz-agent-join.service -n 20  # 每轮一行 JSON
systemctl --user enable --now buzz-agent-join.timer
# 停用：状态目录保留，重新启用会接着处理
systemctl --user disable --now buzz-agent-join.timer
```

每轮输出一行 JSON：`status` 是 `ok`、`error` 或 `locked`（上一轮还没跑完，退出码 0），按 agent 给出本轮的 `baseline`／`requested`／`approved`／`manual`／`active`／`left`／`deferred`／`withdrawn`／`drift` 计数、各状态的积压数 `states`，以及脱敏的 `error`（带频道 ID 前缀）。`dms list` 或成员频道列表失败会令该 agent 本轮为 `error`，不会降级成继续猜测。`drift` 表示一个已开通的频道后来被人从清单里删掉了，脚本不处理，留给 owner；`withdrawn` 表示进行中的申请因为 agent 被移出频道而作废。

群内流程失败不能静默：脚本用 agent 身份在发生问题的**原群**或申请 Thread 发一条不含路径、server answer、成员输入或 secret 的状态，明确写出**失败原因**、当前没有开通或不能响应、下一轮是否自动重试，以及联系 Agent owner 的**恢复方法**。同一种持续故障只发一次；发送结果不确定时先持久化，再用同一机器标记读回或重试，避免既丢提醒又刷屏。故障恢复后会回复恢复状态，最终仍以「已开通」为准。只有连群消息通道本身也不可用时当下无法提醒；待发送状态会保留，通道恢复后补发。一个频道出错不影响同一个 agent 的其他频道，一个 agent 出错也不影响其他 agent，本轮仍以非零退出并保留 journal 证据。

## 上线前核对

- 每个纳管的 agent：env 里有 `BUZZ_ACP_CHANNELS`；`BUZZ_ACP_AGENT_OWNER` 等于配置里的 owner；prompt 已经加了标记块；`capabilities.repos` 和它实际持有的 token 一致；`unit` 与 `log_file` 确实是这个 agent 的（配错会重启别的 agent，或永远核对不到订阅）。
- 平台类 agent 已设 `owner_only`，executor 已设 `nobody`。
- 手动跑一轮：输出里每个 agent 的 `baseline` 等于它当前所在的频道数，频道里没有任何新消息。
- 真实频道验证另开 issue：admin 邀请 → 申请出现 → owner ✅ → 「已开通」；再测不同意、过期、普通成员邀请、owner 不在群、agent 忙时推迟。

## 已知限制

- `respond_to` 是 agent 级别的设置，不能按频道区分；某个频道需要不同策略，只能另起一个 agent 身份。
- 一直很忙的 agent 可能迟迟不重启（从不强制重启，为了不打断回合）。
- 开通后 agent 被移出频道、之后再被拉回，会直接恢复回应：频道还在清单里，harness 收到入群通知就订阅。要收回，owner 从清单里删掉这个频道。
- 和现状一样，同一 Unix 用户下没有硬边界：被注入的 agent 进程能直接改自己的 env。审批只认 owner 的签名，agent 的密钥伪造不了这一步。
- open 频道里任何成员都能让 agent 进群再退群，产生两条消息；不经 owner 同意不会开通。
- prompt 和责任人配置在 env 目录锁内改写，但别的工具改它们时不一定拿这把锁；被覆盖后要 owner 手工补。
- 每个待审批的申请每轮都会从申请时间起重读该频道的 reaction；`NO_INVITE` 的频道每轮都会重读 30 天的成员事件。频道很多、很活跃时单轮会变慢。
- 没做：飞书群绑定检查（该频道绑了飞书群时提醒 agent 进飞书群）、自动写频道 Canvas、owner 不在群时的 DM 兜底通知、平台类 agent 纳入同一流程。
