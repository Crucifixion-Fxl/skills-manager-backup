# 飞书群 ↔ 频道：成员与表情双向同步，飞书里同意 agent 入群

`buzz_feishu_group_sync.py` 的双向部分（[ADR-0020](../../../docs/05-adr/0020-sync-feishu-group-membership-and-reactions-both-ways.md)，engineering/skills#148）。群同步的其余部分（身份、Setup、配置、state、systemd）见 [feishu-group-sync.md](feishu-group-sync.md)。

以前成员和表情都只有一个方向：以 Buzz 频道为准改飞书群，人的表情两边都不同步。现在缺省两个方向都走：在飞书群里拉人、拉 agent、移人，频道跟着变；两边都能看到对方的表情；agent 的 owner 在飞书里就能同意 agent 入群。

## 开关

| 配置键 | 取值 | 缺省 | 说明 |
|---|---|---|---|
| `membership_sync` | `"two_way"` / `"buzz_to_feishu"` | `"two_way"` | `"buzz_to_feishu"` 回到单向对账（以前的行为） |
| `reaction_sync` | `"two_way"` / `"agents_only"` | `"two_way"` | `"agents_only"` 回到只同步 agent 的 Buzz 表情；飞书里的 `/approve` 转发也跟着关 |
| `people_cache_file` | 绝对路径 | 不设 | 本机所有群同步共用的人员缓存，见「认人」 |
| `accept_feishu_approvals`（入群申请配置） | `true` / `false` | `true` | 见「飞书里同意 agent 入群」 |

三个开关各自独立，写一行就能单独回退。

## 成员双向同步

开关是 `membership_sync`：缺省 `"two_way"`；写 `"buzz_to_feishu"` 回到以前的单向对账，下面这些都不做。

### 三方对比

每轮比三份名单：Buzz 频道成员、飞书群成员，以及上一轮结束时的快照（state 的 `buzz_seen` 与 `feishu_seen`）。只在一边变了的，把变化带到另一边；两边都没变的不动。

| 变化 | 动作 |
|---|---|
| 飞书里新拉进一个人 / 一个 agent 的 bot，认得出是谁，他不在频道里 | 同步主机用 `people_api` 的 signer key（频道 owner/admin）在进程内签 **kind 9000**（`h` 本频道、`p` 他、`role` 人是 `member`、agent 是 `bot`），`POST /events` 加进频道 |
| 飞书里移出一个人 / 一个 agent 的 bot，他还在频道里 | 签 **kind 9001** 移出频道（频道 admin 也一样，PO 决定） |
| Buzz 里有人离开频道 | 按快照里记下的他的飞书身份，把他移出群——**不看 `remove_extras`** |
| Buzz 里有人加进频道 | 照旧拉进群 |

因为飞书的变化而改了频道成员时，镜像身份在频道里发一行说明（例如「飞书群同步：新加入 张三、nh-dev；移出 李四」），每轮最多一条。

### Agent 入群后的自我介绍

成员同步完成后，新进群的 Agent 会在本群只介绍一次自己。升级后的第一轮把已有 Agent 记为基线，不会让整群 Agent 一起刷屏；
以后同一个 Agent 离群再回来也不重复。

- 本机配置且飞书身份验证通过的 Agent，由它自己的 bot 发；目录里的远端 Agent 没有把凭据交给同步主机，因此由群助手明确标注
  “根据 Agent 的公开资料代发”，不会冒充它。
- 内容只取 Agent 自己公开签名的 kind:0 `display_name` / `about`，以及 owner 签名的 kind:30177 `respond_to`。
  不读取、不发送 agent prompt、system prompt 或 `instruction`。`about` 就是公开 description，应该同时说清职责、适合怎样 @ 它。
- `respond_to` 会翻成用户语言：`anyone` 是“可以回应群里的任何成员”；`allowlist` 是“只回应已授权成员”；
  `owner-only` 是“只回应我的所有者”；`nobody` 是“不接受群内 @，只执行已配置的自动任务”。受限模式同时说明未授权者会收到
  未响应原因，以及联系 Agent 所有者申请授权（或请已授权成员代发）的办法。
- 介绍发送失败会进入成员同步状态消息，明确说明公开资料或飞书发送暂不可用、下一轮自动重试。重试复用同一个飞书幂等键；
  超出安全重试窗口、结果仍无法确认时停止自动重发，提示管理员检查群消息，避免重复介绍。

### 兜底

- **首轮只记基线**：第一次开启（state 里还没有快照，或从旧版本升级）的那一轮完全按以前的单向对账跑，结束时记下快照，群里原有的人不会被批量加进频道。首轮的多余成员清理被批量上限暂停时，下一轮继续按基线处理。
- **身份空间变了就重新记基线**：改了 `identity`（union_id ↔ email）或换了 owner 应用，飞书用户的 id 全部换了形态；这一轮不带过任何变化，重新记基线，不会把所有人当成「移出了又加进来」。
- **冲突以 Buzz 为准**：同一轮里一边加、一边删同一个人，Buzz 侧的删除生效；Buzz 里刚加进来、飞书里却不在的人，照旧拉回群。
- **只删判定得了的**：只有成员列表明确给出 `has_more: false`、`truncations: []`、对应 `user_total` / `bot_total`
  是非布尔整数且与列表长度相等，才判定为读全。任一字段缺失、错类型、截断或人数对不上时，不判定任何人被移出，
  也不把「消失」的人加回群，报告 `removals_withheld: "member_list_incomplete"`；认不出身份的人不删。
- **批量上限**：一轮里两个方向合计要移出的人数超过 `BULK_REMOVAL_LIMIT`（10）时全部暂停，报告 `removals_withheld: "bulk_removal"`；带 `--allow-bulk-removal` 跑一轮才执行。被暂停的人不会被加回群，下一轮还能判定。
- **不移出的**：频道 owner（relay 不允许）、同步用的签名身份（移掉它同步就没有管理权限了）、镜像身份。在飞书里移出他们，只在群里提示一次，报告 `members_protected`。
- **agent 不让加**：它的 `channel_add_policy` 是 `owner_only` / `nobody` 时 relay 拒绝（`policy:owner_only …`）。群里提示一次，报告 `members_refused`，之后不再尝试；它的 bot 离开群、再被拉进来一次才重试。
- **写失败**：计入 `member_failures` 与 `errors`（需要关注）。发送前先在 `member_events` 持久化完整的已签名
  9000/9001；每个新操作带随机 `feishu-member-op`、本 binding 随机且持久的 `feishu-member-stream`，以及该 stream
  持久递增的 `feishu-member-seq`，所以同一秒内 add → remove → add 既是三个 id，入群申请也能在 owner/admin key
  合法轮换后继续按序号判断真正先后。HTTP 结果未知时，
  下一轮仍需要这项变化才逐字节重发原事件。成员列表显示生效或飞书侧撤回意图后清账；signer 已变化、事件超过 Relay 的
  900 秒窗口、未知结果后的重试又被拒，或未决项已达 256 条时一律失败关闭，不生成新事件，规范状态消息会说明原因和处理办法。
  “未知后重试又被拒”会把暂停原因一同持久化，后续轮次不再 POST；只有成员列表证明已生效，或飞书侧撤回原意图，才清账解锁。

### 失败状态消息

双向成员同步遇到会影响本轮结果的问题时，镜像身份会在 Buzz 频道发布一条带 `feishu-sync-status:membership`
标记的状态消息，并把同一正文同步到飞书群。后续失败轮次用 kind:40003 编辑这条 Buzz 根消息，飞书里也更新原消息；
恢复后的第一轮再把同一条消息改为“已恢复”，不会每轮刷一条新消息。

提示只写安全、可行动的原因：Agent 目录暂时不可读、飞书成员列表未确认完整、移出人数超过安全上限、身份未绑定、
bot 名额不足，或成员写入没有被飞书 / Buzz Relay 接受；不带服务端原始响应、凭据或成员标识。`members_refused`、
`members_protected`、`members_unresolved` 仍使用上面的单次群提示，因为这些是对象级策略结果，不是每轮重试的故障。

如果状态事件暂时写不进 Buzz，则本频道 Desk bot 直接在飞书发送或更新同一条兜底消息。以后 Buzz 恢复时补建规范根事件，并把它
关联到已有的飞书消息后原地更新，不会再发第二条。只有 Buzz 与飞书同时不可写时，才无法把提示送达并在 JSON 报告中
另计一次错误；飞书兜底成功时不为 Buzz 暂时不可写重复增加错误，原来的成员同步问题仍保留在报告里。

### 认人

- **agent**：飞书里只看得到 bot 的 app_id。依次查本机配置、ADR-0019 的目录；都不认识时全量读一次 relay 上的 kind:30177（和它们 agent 的 kind 0，按 ADR-0019 的规则校验）建 app_id → pubkey 的反查表。查不到的 bot 是别人的普通 bot，记下来，不再为它查。
- **人**：bridge 的人员接口**只回答本频道现存成员**，频道外的人它不告诉你是谁。所以一个人被拉进群时，按顺序看：
  1. 他已经是频道成员（bridge 认得）；
  2. 本频道 30 天内见过他（离开频道、又被拉回群的老成员；state 的 `people_seen`）；
  3. 配了 `people_cache_file` 时，本机任何一个群同步最近 30 天见过他。

  都认不出：他留在群里，本频道 Desk bot 在群里提示一次，请他先到 `<people_api.base_url>/bind/` 绑定，再请频道管理员在 Buzz 里把他加进频道；报告 `members_unresolved`。他以后能被认出（例如在本机同步的别的频道里出现），下一轮就会自动加进来。要做到任何绑定过的人都能从飞书拉进来，需要 bridge 提供按飞书身份查 pubkey 的接口（后续工作）。
- **别的机器的镜像**：同一个频道被两台机器同步到两个群时，对方的镜像在本机看来是「本机没配置的 bot 成员」。它的 kind:30177 由它的 NIP-OA owner 签名、声明了 `"feishu": {"mirror": true}`，就按镜像处理：不是 agent，它转进 Buzz 的飞书消息不再被本机代发一份（跳过原因 `other_mirror`），不进目录，不渲染成 `<at>`，成员同步也不碰它。这条判定不要求它的 owner 是频道 admin。

### `remove_extras` 的新含义

双向时群也是一个来源：只在群里的人不再是「多余的」（他可能去绑定，然后加进频道）。所以 `remove_extras: true` 只在记基线的那一轮清理一次；之后离开频道的人由上面的规则逐个移出群，`remove_extras: false` 也照样移出。

### 提醒：不限频道的 agent 会直接开始回复

没设 `BUZZ_ACP_CHANNELS`、订阅全部频道的 agent 不走入群申请（入群申请脚本会跳过它）。这样的 agent 如果 `channel_add_policy` 还是缺省的 `anyone`，在飞书里被拉进群、同步加进频道后，会**立刻开始回复，不经过任何审批**。防线是 ADR-0018 已经要求的：平台类 agent 设 `owner_only`，executor 设 `nobody`（`buzz channels set-add-policy`），这样同步去加它时 relay 会拒绝，群里只收到一条提示。

## 表情双向同步

开关是 `reaction_sync`：缺省 `"two_way"`；写 `"agents_only"` 回到只同步 agent 的 Buzz 表情（由它自己的 bot 打），飞书里的 `/approve` 转发也不做。

- **Buzz → 飞书**：频道里的人（以及本机没有凭据、由本频道 Desk bot 代发的 agent）打的表情，由 **Desk bot** 打到飞书对应消息上。飞书对同一条消息、同一个表情、同一个操作者只留一个，所以按「消息 × 表情」计数：第一个人打时加，最后一个人撤回时才去掉。有自己 bot 的 agent 照旧用自己的 bot。
- **飞书 → Buzz**：两边都有副本的消息登记在 state 的 `rwatch` 里，读 24 小时；入群申请（正文里有 `buzz-join:v1 JOIN-…`）读 7 天，最多 200 条。每轮用 `im reactions batch_query`（owner 的 user 身份，每条最多 10 个，多的翻页）读一次。已绑定的人打的表情，由镜像身份在 Buzz 对应事件上打同一个（kind 7，带 `feishu-author` 标签：`["feishu-author", <这个人的 pubkey>]`，在进程内签名，请求带镜像的 x-auth-tag）；他在飞书里撤回，镜像就发 kind 5 撤回自己那条。
- **防回声**：bot 在飞书上打的表情（Desk bot、agent bot）不回 Buzz；镜像（以及别的机器的镜像）在 Buzz 上打的表情不回飞书。
- **对照表**：只同步 `reaction_map` 里有对应的表情（缺省多了 ❌ → CrossMark，与 ✅ → DONE 成对），没有对应的记一次 `reaction_emoji_unmapped`。
- **同一表情只留一个**：relay 对同一身份、同一目标、同一表情只留一个 reaction，所以飞书里几个人打同一个表情，Buzz 上是镜像的一个（带第一个人的 `feishu-author`），最后一个人撤回才撤。
- **不重发**：镜像打表情前先在 `f2r` 记 `pending:<首次时间>`，应答丢了下一轮用首次的时间重发同一个事件，relay 说已有就算送达；超过 relay 的 900 秒时钟窗口才放弃。成员事件（9000/9001）在 `member_events` 保留完整签名事件：窗口内逐字节重发；超窗、signer 改变，或一次未知结果后的精确重试被拒时停住并提示人工核对，不用新 id 猜测旧请求是否生效，也不会在下一轮偷偷恢复重试。
- **限制**：两边显示的打表情者是 Desk bot 或镜像，不是本人；几个人打同一个表情合并成一个；只读 `rwatch` 里的消息；某条消息一轮读不全（翻页超限、飞书说查不了）时，这一轮不判定它的撤回。

## 飞书里同意 agent 入群

agent 的入群申请（ADR-0018）会被镜像到飞书群。agent 的 owner 在飞书里就能回答：

- 在申请消息上打 ✅ 或 ❌；
- 或者在申请的话题里回复一整行 `/approve JOIN-<id>` / `/deny JOIN-<id>`。

群同步把前者当普通表情带进 Buzz（镜像在申请上打 ✅/❌，带 `feishu-author`）；后者不走 `buzz messages send`（它加不了标签），由镜像在进程内签成话题回复（kind 9，「[飞书] 名字：/approve JOIN-<id>」），带 `feishu-author` 和 `["join", "JOIN-<id>"]`，只发这一条；发送用首次的时间，应答丢了重发的是同一个事件，relay 不会多存一条。

**为什么 `/approve` 不转成表情**：relay 对同一身份、同一目标、同一表情只留一个 reaction。别人先在飞书里点了 ✅，镜像在申请上就已经有一个 ✅（带的是那个人），owner 再点的 ✅ 会被合并掉、不算同意。遇到这种情况请 owner 在话题里回复 `/approve JOIN-<id>`，它是一条独立的回复，不受影响。

入群申请脚本认这样的表情和回复，条件是（见 [agent-channel-join.md](agent-channel-join.md)）：`feishu-author` 等于 agent 的 owner；作者是本频道的**可信镜像**——频道的 bot 成员，它的 kind:30177 由它的 NIP-OA owner 签名并声明 `"feishu": {"mirror": true}`，而这个 owner 是本频道的 owner 或 admin；回复必须带 `join` 且等于这个申请、首行去掉「[飞书] 名字：」后整行是命令，表情带 `join` 时也必须等于这个申请；期限与验签同 owner 自己签的信号。入群申请配置 `accept_feishu_approvals`（缺省 `true`）写 `false` 可以关掉。

**上线时必须先声明镜像**：用发布 helper 的 `--mirror` 在镜像身份的 kind:30177 里写上标记。镜像本来没有 30177，helper 会新建一条（name 取它 kind 0 的名字、parallelism 1、respond_to `"owner-only"`——Buzz Desktop 能解析的最保守的值；镜像不跑 harness，谁 @ 它都不会回）。只有镜像 kind 0 里 NIP-OA 声明的 owner 才能声明它。

```bash
python3 <skill>/scripts/buzz_agent_feishu_app.py --owner-env ~/.config/buzz/env \
  --agent <镜像的 pubkey> --mirror --dry-run      # 先看会不会新建
python3 <skill>/scripts/buzz_agent_feishu_app.py --owner-env ~/.config/buzz/env \
  --agent <镜像的 pubkey> --mirror
```

没做这一步：飞书里的同意不算数，别的机器也会把这个镜像当成 agent 代发。helper 按 owner pubkey 在实际 Unix 用户的
`~/.local/state/buzz-agent-feishu-app/locks/` 持有稳定锁，文件名是 owner pubkey 加 `.lock` 后缀；同一时间只允许一个运行改
这个 owner 的 30177（两个运行读到同一个旧版本，后发的会冲掉先发的改动）。锁不受 `HOME`、`--backup-dir` 或
`--dry-run` 影响，拿不到锁就退出码 1，稍后再跑。

**已接受的风险**（ADR-0020）：跑同步的那台机器（频道 owner/admin 的）技术上能替 agent owner 伪造一条同意；bridge 的「飞书账号 ↔ pubkey」绑定也进了审批的信任链。

## state 与报告

新字段（旧 state 缺它们时按「还没记过基线」读入）：

| 字段 | 内容 |
|---|---|
| `members_synced` | 第一次记快照的时间；0 表示还没记过，下一轮只记基线 |
| `feishu_seen` | 上一轮结束时的群成员：飞书 key（`u:<union_id>`、`o:<owner 应用>:<open_id>`、`b:<app_id>`）→ `""`，只有飞书 id |
| `buzz_seen` | 上一轮结束时的频道成员：pubkey → 他的飞书 key（不知道时是 `""`） |
| `member_notes` | 已经在群里提示过的事（`unresolved:` / `protected:` / `refused:` 加对象）→ 时间，30 天后丢 |
| `member_events` | HTTP 结果未知的 9000/9001 操作 → 完整已签名事件（含独立操作 nonce、持久 stream 和序号）；逐字节精确重试，看到结果或意图撤回后删除；最多 256 条，满时不淘汰未知项而是暂停新写入 |
| `member_event_stream` | 本 binding 随机生成并持久保存的成员操作流 id；与 signer 解耦，合法换 key 后仍是同一条有序流 |
| `member_event_seq` | 本 stream 已分配的成员操作序号高水位；新操作持久递增，同一 Relay 时钟窗口内据此排序 |
| `agent_intros_initialized` | 是否已经为本 binding 记录过升级基线；false 时下一轮只把已有 Agent 记为 baseline |
| `agent_intros` | Agent pubkey → `baseline`、发送中的 pending/retry、成功的飞书 message_id 或终态；保证每个 binding 只介绍一次 |
| `member_event_blocks` | 已暂停的未决操作 → 原因；目前 `retry_refused` 表示“首次结果未知，随后精确重试被 Relay 拒绝”，成员事实或源意图收敛前不再 POST |
| `member_notice_event` | 成员同步状态在 Buzz 的规范根事件 id；后续轮次用 kind:40003 编辑它 |
| `member_notice_feishu` | Buzz 暂时不可写时直接发送的飞书兜底消息 id；补建根事件后与它关联 |
| `member_notice_content` | 最近一次状态正文；兜底尚未补建到 Buzz 时保留 |
| `member_notice_active` | 上一轮是否处于成员同步失败状态；用于恢复后的第一轮发布“已恢复” |
| `people_seen` | 本频道见过的人：飞书用户 key → `<pubkey>|<最后见到的时间>`，只在 30 天内用 |
| `rwatch` | 读表情的消息：飞书消息 id → `<Buzz 事件 id>|<读到什么时候>` |
| `f2r` | 镜像打到 Buzz 的表情：`<消息>|<操作者>|<emoji_type>` → 镜像那条 kind 7 的 id（多人同一表情共用一个）、`pending:<首次时间>`、`failed` 或 `skipped` |

**「谁是谁」的变化**：以前 state 不落任何 pubkey ↔ 飞书 id 的对应。双向同步要把离开频道的人移出群（那时 bridge 已经不再告诉你他是谁）、要认出被拉回群的老成员，所以为**本频道的成员**（现在的和 30 天内的）记下这个对应（`buzz_seen`、`people_seen`）；群快照 `feishu_seen` 只有飞书 id；邮箱和 bridge 应用的 open_id 照样不进 state。单向（`"buzz_to_feishu"`）时和以前一样，一个对应都不落。`people_cache_file` 是另一份 0600 文件，存的是本机所有群同步见过的人，同样只在本人可读的目录里。

报告字段：`members_to_buzz`、`members_removed_from_buzz`、`members_refused`、`members_protected`、`members_unresolved`、`people_cache_failed`、`member_events_blocked`、`reactions_to_buzz`、`reactions_withdrawn_in_buzz`、`approvals_to_buzz`。`member_events_blocked` 按 `signer_changed` / `expired` / `ledger_full` / `sequence_exhausted` / `retry_refused` 计数，并随 `member_failures` 令退出码变 3；其余这些字段只是计数（含义见 [feishu-group-sync.md](feishu-group-sync.md)「输出与退出码」）。

## 已知限制

- 从飞书拉进来的人，只有本机认得出的才会加进频道（见「认人」）；彻底解决要 bridge 提供查询接口。
- 表情的「打的人」显示为 bot / 镜像，多人同一表情合并；24 小时（入群申请 7 天）之外的消息不读表情。
- 同一分钟里对同一个人两边做相反操作时，结果以下一轮为准收敛（冲突规则见上）。
- 目录或反查表读不到的那一轮：别的机器的镜像认不出（会像以前一样被代发一次），新拉进群的别人的 agent 这一轮加不进频道。
