# Buzz Channel ↔ 飞书群：绑定、agent 进群与双向同步（本地 CLI 路线）

需求 engineering/skills#110。可行性验证与方案见 infra/buzz-deploy 的
[`local-cli-feasibility.html`](https://gitlab.addx.ai/infra/buzz-deploy/-/blob/9-local-cli-feasibility/docs/product/agent-identity/local-cli-feasibility.html)，
下文的 LCV-NN 编号都指那份实测证据。

这条路线**只用 owner 本机已经登录的个人 lark-cli 和 buzz CLI**，不经过 feishu-bridge 服务，也不引入新的中心服务。它只覆盖 owner
显式登记的自管频道。「所有频道默认建群」属于平台路线 infra/buzz-deploy#29，不在这里做。

## 何时使用

- owner 想让自己负责的某个 Buzz channel 在飞书里有一个对应的群，频道里的 agent 能在群里被 @，两边的消息互相可见。
- owner 本机已经有：登录好的个人 lark-cli（bot 和 user 两种身份都可用）、buzz CLI、这个频道的 owner 或 admin 权限。
- 运行主机应当是单用户的，或者开启了 `hidepid`。lark-cli 的 `--text` 没有 stdin 选项，所以消息正文会出现在进程参数里，同机的其他用户能读到。
- 要同步图片的话：发图的 bot（owner 的个人应用 bot、每个 agent 的 bot）要有上传图片的权限（开放平台里的 `im:resource`；没有 API 可以加 scope，见 LCV-11）；owner 的 user 登录要能读群消息的资源（下载飞书里的图片）。缺权限时图片计入 `images_failed`，文字不受影响。细节见下文「图片同步」。
- bridge 已部署了「频道人员接口」并且打开了开关（infra/buzz-deploy ADR-0015，`CHANNEL_PEOPLE_API_ENABLED`），owner 是这个频道的 owner 或 admin。
- **上线前置：bridge 已部署 union_id 回填与新的响应字段（infra/buzz-deploy#77：`union_ids`，以及只在开了 `CHANNEL_PEOPLE_EMAILS_ENABLED` 时才有的 `emails`）。**默认的 `identity: "union_id"` 要用 `union_ids`；bridge 还没部署时脚本每一轮都会中止并说明缺什么（什么也不会动），可以先在配置里写 `"identity": "email"`（要 bridge 开 `CHANNEL_PEOPLE_EMAILS_ENABLED`）。见下文「人的身份怎么认」。

## 组成

| 部件 | 身份 | 用途 |
|---|---|---|
| owner 的 lark-cli（默认配置） | 个人应用的 **bot** | 建群（群主设为 owner）；在群里转述 Buzz 上人的发言（默认发成卡片，见「消息卡片」；含图片） |
| owner 的 lark-cli（默认配置） | owner 本人的 **user** token | 加人、减人、拉 agent bot 进群；轮询群消息和话题消息；下载飞书消息里的图片 |
| 每个 agent 自己的 lark-cli profile | 该 agent 的 PersonalAgent **bot** | 把这个 agent 在 Buzz 上说的话（含图片）发到群里；把它在 Buzz 上打的 reaction 变成飞书表情（也由它自己的 bot 打） |
| buzz CLI | **镜像身份**（每个频道一个，bot 角色，由 owner 用 NIP-OA 背书） | 读频道成员、消息和 reaction；下载 Buzz 上的图片附件（`media get`）；把飞书上人的发言带署名（图片作为附件）发进频道 |
| bridge 的人员接口 | 频道 owner / admin 用**自己的 Buzz key** 做 NIP-98 签名的一个 GET：`<base_url>/bind/api/channels/<频道>/people` | 每一轮取一次本频道现存成员里已验证绑定的人：`people`（`{pubkey: open_id}`，**bridge 应用**的 open_id，本地认不出人，只解析不使用）、`union_ids`（`{pubkey: on_…}`）、`emails`（`{pubkey: [邮箱]}`，bridge 开了 `CHANNEL_PEOPLE_EMAILS_ENABLED` 才有）。绑定会变，所以每轮重取；pubkey → id 的对应关系不落盘 |
| `scripts/buzz_feishu_group_sync.py` | — | 确定性脚本，不含 LLM，提供 `preflight`、`create-chat`、`bind`、`round` 四个子命令 |

**不用 owner 的 Buzz key 转发飞书消息。**否则所有消息都会显示成 owner 说的；而且只认 owner 公钥的 agent（例如个人 agent）会把飞书群里任何人的话当成 owner 的指令。

**每轮开始先核对身份，不符就不发。**
- owner 的 lark-cli profile：`auth status` 返回的 `appId` 和 user `openId`，必须等于配置里的 `owner_app_id` 和 `owner_open_id`，否则整轮拒绝。
- 镜像身份：镜像 env 文件里的 key，用 `buzz users get` 查到的自身 pubkey 必须等于 `mirror_pubkey`，否则整轮拒绝。这能挡住把 env 文件误指向 owner 的配置错误。
- 每个 agent 的 profile：`appId` 必须等于它登记的 `app_id`。不符（包括 `auth status` 暂时出错）的 agent 本轮不投递，它的 bot 也保持原样：已在群里的不移出，不在群里的不拉进来。绝不改由 owner bot 代发。

## 人的身份怎么认：union_id（默认）与 email

**为什么不能直接用 bridge 给的 open_id。**飞书的 open_id 按**应用**隔离：bridge 生产应用里的 open_id 和 owner 个人应用里的 open_id 不是一回事。实测：owner 自己在两个应用里的 open_id 就不相等；一个 18 人的群，18 个群成员与 bridge 给出的 21 个映射零交集。所以拿 bridge 的 `people` 去对账群成员、识别飞书发信人，在真实租户里一个也对不上。`people` 只做格式校验，不用来认人。

脚本用配置 `identity` 选一种**两边共通**的身份，缺省是 `"union_id"`：

| | `union_id`（默认） | `email`（可选） |
|---|---|---|
| bridge 要给 | 应答里的 `union_ids`（bridge 已部署 union_id 回填） | 应答里的 `emails`（bridge 开了 `CHANNEL_PEOPLE_EMAILS_ENABLED`） |
| 内部身份空间 | union_id（`on_…`，同一租户下所有应用里都一样） | owner 个人应用的 open_id |
| 人怎么映射 | 直接用 `union_ids[pubkey]` | 每个邮箱用 owner 的 user 身份 `contact +search-user --query <完整邮箱>` 换成 open_id，`users[].email` 或 `enterprise_email` 与查询值**逐字相等**才认（搜索是模糊的，近似的账号一概不认） |
| 成员对账 | 群成员按 `member_id_type=union_id` 列出（bot 另按 open_id 列），加人、移人也用 union_id | open_id |
| owner 本人的 id | owner 自己的 `authen/v1/user_info`（user 身份），并核对其中的 `open_id` 就是配置的 `owner_open_id` | 配置的 `owner_open_id` |
| Buzz→飞书的 @ | `<at user_id="on_…">`（飞书直接认 union_id）；agent 的 bot 仍用群里的 bot 成员 id | `<at user_id="ou_…">` |
| 飞书→Buzz 认发信人 | 见下 | 消息列表里的发信人就是 open_id，直接认 |
| state 缓存 | `idmap`：本应用 open_id → union_id | `emailmap`：sha256(应用 id + NUL + 邮箱) → open_id 或 `miss:<时间>` |
| 权限 | 只需 im（不需要 contact 权限） | user 身份要能搜通讯录 |

**两种模式共同的原则**

- 映射不到的人不猜：计入 `unmapped_members`，只要有人映射不到，本轮就不移人（`removals_withheld`）。**绝不**用显示名、顺序、部分匹配认人。
- **一个身份只能对应一个 pubkey**：bridge 里两个 pubkey 绑到了同一个飞书账号（union_id 相同，或邮箱换出同一个 open_id）时，分不清谁是谁——这些 pubkey 都算映射不到：不进群、不认他们在飞书里的发言、@ 他们的 p tag 不产生 `<at>`，计一次 `identity_conflicts`（需要关注）并计入 `unmapped_members`（所以不移人）。要在 bridge 里解绑其中一把 key，下一轮就恢复；别的人不受牵连。
- 群主永不移出：群信息（用同一种 id）里的 `owner_id` 无论在不在频道里都保留。
- 报告、stderr、错误信息里没有邮箱、union_id、open_id 的完整值（报告只有计数，跳过原因只有名字）。邮箱明文只出现在通讯录搜索的 `--query` 参数里（lark-cli 没有别的传法，见「边界」）；state 里没有明文，只有带应用 id 的 sha256（见 state 表里对它的说明）。
- owner 的 Buzz key 仍然只用来给 bridge 的那一个 GET 签名，不进任何子进程。

**union_id 模式怎么认飞书上的发信人**

飞书的消息**列表**接口忽略 `user_id_type`，发信人和 `mentions[].id` 永远是本应用的 open_id；只有**单条消息**接口 `GET /open-apis/im/v1/messages/{id}?user_id_type=union_id` 会把 `sender.id` 和 `mentions[].id` 换成 union_id，`mentions[].key`（`@_user_1`）两边一一对应。所以：

1. 对要镜像的消息，发信人或被 @ 的人（bot 和 @所有人除外）不在 `idmap` 里时，对**这一条消息**取一次单条视图（owner 的 user 身份），发信人与视图的发信人配对，被 @ 的人按 `mentions[].key` 配对——只按 key，key 在任一边不唯一就不算，**不看名字，也不看顺序**。配对结果存进 `idmap`（只有 id）。
2. 已经在 `idmap` 里的人不再取；本来就要跳过的消息（已删除、系统消息、app 发的、空内容、绑定之前的）不取。每条消息最多取一次。
3. 配对不上一律不映射：发信人配不上（视图里没有这条消息、发信人的 `id_type` 不是 `union_id`、id 不是 `on_…`、发信人类型不是 user……）→ 这条消息不镜像，计 `sender_unpaired`；被 @ 的人配不上 → 不产生 @，计 `mention_unpaired`，消息照发。这些都不算错误。
4. 取单条消息的调用本身失败了（网络错误、接口报错）：这条消息还没发到 Buzz，按「确定没发出」处理——记一次 `errors`，留在重试里，下一轮再取，连续 3 次失败记 `failed`。
5. 缓存里的旧配对被新证据推翻（同一个 open_id 对着别的 union_id，同一个 union_id 对着别的 open_id，或同一份答案自相矛盾）：**两边都不信**——矛盾的缓存项删掉、这次答案也不采信，这条消息本轮不镜像（`identity_conflict`），`identity_conflicts` 加一（需要关注，退出码 3）；下一轮重新配对，消息还在重读窗口里就照常镜像。

**email 模式怎么换邮箱**

- 每个成员的每个邮箱各查一次，结果按 sha256(应用 id + NUL + 邮箱) 存进 `emailmap`：命中就用、不再查；键里带 owner 应用的 id，因为同一个邮箱在不同应用里是不同的 open_id，换应用不会读到旧缓存。
- 邮箱在飞书里找不到（比如同事还没有飞书账号）记 `miss:<时间>`，`EMAIL_MISS_RECHECK_SECONDS`（600 秒）之内不再查，过了再查。
- 一个人有多个邮箱：都指向同一个 open_id 才映射；有的找不到、别的找到了照样映射；指向不同 open_id → 该成员不映射，计 `unmapped_members` 和 `identity_conflicts`（需要关注）；某个邮箱的搜索出错 → 这一轮不映射他，记 `errors`，不缓存，下一轮再查。
- 一轮最多查 `EMAIL_LOOKUPS_PER_ROUND`（50）次，超出的成员这一轮映射不到（所以不移人），下一轮接着查，直到查完；首轮遇到很多没缓存的成员时不会一次打满通讯录接口。
- 缓存的邮箱 → open_id 一直有效，直到 state 被清掉：某个邮箱换了主人（同事离职、邮箱被复用）时，旧主人的 open_id 会继续对应这个邮箱。需要重新核对时，删掉 state 里的 `emailmap`（整个 state 是有界且可重建的：只会让下一轮重新搜一遍）。

## Setup：先采访，再预检，最后绑定

### 1. 一次问齐这些问题

1. 哪个频道？给出 channel UUID。必须是 owner 自己是 owner 或 admin 的 stream/forum 频道。
2. **新建飞书群，还是关联一个已有群？**关联已有群时，要给出群的 `chat_id`（`lark-cli im +chat-search --as user --query <群名>` 可以查到）。
3. 群里有、但不在频道里的人，要不要移出？
   - 私有频道的消息会被群里所有人看到，所以默认**移出**（`remove_extras: true`）。
   - 只有在 owner 明确接受「这些人会看到频道消息」时，才选只加不减。
4. 频道里哪些 agent 要进群？每个 agent 都需要已经注册好自己的 PersonalAgent，见下文「agent 的飞书身份」。
5. 镜像身份用哪个？每个频道一个，用 `mint-agent.py` 铸出，以 `--role bot` 加进频道，并由 owner 用 NIP-OA 背书。这一步也自己做完：`(umask 077; python3 references/scripts/mint-agent.py <channel>-feishu-mirror > <name>.keys.json)`，据此写 0600 的 env 文件（私钥不打印、不进日志），再用镜像身份 `users set-profile` + `channels join`，最后用 owner 身份 `channels add-member --role bot`。
   - 只有本来就要为频道成员服务的 agent，才能把镜像身份加进它的 `BUZZ_ACP_RESPOND_TO=allowlist` 名单。
   - **不要**把镜像身份加进只认 owner 的 agent（例如个人 agent）的名单。否则飞书群里的任何人都能借镜像身份给它下指令。
6. bridge 的地址（`base_url`，必须是 `https://主机[:端口]`，就是 bridge 的 `BIND_PUBLIC_ORIGIN`）和 owner 的 env 文件放在哪里（0600，里面的 `BUZZ_PRIVATE_KEY` 是 hex 或 nsec）。
   - 接口只认**频道的 owner 或 admin**，所以签名用的是**使用者自己的 Buzz 私钥**（npub/nsec，前提是这个人是该频道的 owner 或 admin），而不是镜像身份（它在频道里是 bot 角色，会 404）。这把 key 只在脚本进程里签这一个 GET：不进 buzz 或 lark 子进程的环境、不进输出、不进 state。
   - 确认这个 env 文件的 key 确实是频道 owner / admin：脚本每轮会自己核对，不是就报错并说明原因。
7. 人的身份用哪种（`identity`）？缺省 `union_id`，前提是 bridge 已部署 union_id 回填与新响应字段（infra/buzz-deploy#77）；bridge 开了 `CHANNEL_PEOPLE_EMAILS_ENABLED` 也可以选 `email`。先向 bridge 的负责人确认它现在给不给 `union_ids` / `emails`，别到运行时才发现每轮都中止。
8. 群里不是频道成员的人（没有 Buzz 账号，或还没绑定飞书账号）的发言，默认会让 agent 读到（`feishu_unmapped_senders: "context"`）：署名 `[飞书·非成员]`，图片走成员同一条下载、校验、去元数据与上传路径；飞书里真实选中的 @agent 会成为 Buzz p tag，@ 到人不生效。团队 Channel 的 agent 按 [runtime-setup.md](runtime-setup.md) 配成 `BUZZ_ACP_RESPOND_TO=anyone` 时，这个 p tag 会建立 turn 并让 agent 响应；`owner-only` 或未包含镜像身份的 allowlist agent 仍会被自己的作者门禁挡下。要关闭非成员镜像就显式选 `"skip"`。说清「唤醒不等于授权」与其余风险，见「非成员的发言（仅上下文镜像）」。它与发言人白名单互斥；已配白名单而未写本键时隐式为 `"skip"`。
9. 频道里有没有**不归你管的 agent**（别人 owner 的 agent 也在这个频道）？有的话它的凭据永远不会在你机器上，缺省会把它的发言丢掉
   （`agent_bot_unavailable`）；设 `buzz_unmanaged_agents: "relay"` 可以让镜像 bot 代发（署名 `名字（Buzz·助手）：`，不 @ 任何人），
   见「本机没有凭据的 agent（镜像代发）」。
10. 反过来，Buzz 那边不是验证过的频道成员、也不是配置的 agent 的作者（比如一个 Workflow 自己的签名身份）发的消息，要不要镜像进飞书？缺省不要（`buzz_unmapped_senders: "skip"`，`not_channel_human`，直接丢弃——丢掉一条话题根会让同一 Thread 之后所有回复在飞书里断链）。选 `"context"` 就镜像（署名插入「·非成员」，只有 @ 到频道自己的 agent 才生效，@ 到人不生效），跟第 8 条是同一条 @ 规则的另一半，见「非成员/非 agent 的 Buzz 消息（仅上下文镜像）」。

### 2. 预检：`preflight`

```bash
python3 scripts/buzz_feishu_group_sync.py preflight --config <cfg.json> --mode new
python3 scripts/buzz_feishu_group_sync.py preflight --config <cfg.json> --mode existing --chat-id oc_xxx
```

输出一个 JSON 对象，字段有 `mode`、`ok`、`problems[]`（阻断项）、`warnings[]` 和 `can_remove`。读不到群时还会带上 `error_code`。

**新建群：阻断项**

| 代码 | 含义与处理 |
|---|---|
| `owner_profile_mismatch` | lark-cli 登录的应用或用户和配置不一致。改配置，或者换成 owner 本人的登录 |
| `owner_out_of_app_scope` | bot 查 owner 的通讯录返回 41050，说明 owner 不在个人应用的可用范围内。bot 建群时要把群主设成 owner，范围外会失败。到开放平台后台把 owner 加回可用范围并发版 |
| `bot_missing_im:chat:create` | 个人应用的 bot 没有建群权限。到后台开通这个 scope 并发版，没有 API 可以加 scope（LCV-11）。建群时会带上 `--set-bot-manager`，让 bot 成为群管理员 |

**关联已有群：阻断项**（依据 `GET /im/v1/chats/{chat_id}` 返回的群信息）

| 代码 | 含义与处理 |
|---|---|
| `owner_profile_mismatch` | lark-cli 登录的应用或用户和配置不一致。后面的检查都要以这个用户的身份读群，所以先拦下 |
| `chat_unreadable` | 读不到群信息，同时带出 `error_code`。可能是 owner 不在群里、`chat_id` 写错了，或者登录已失效 |
| `external_chat` / `chat_not_normal` | 外部群、已解散或状态异常的群不能绑定 |
| `topic_mode_unsupported` / `chat_mode_unsupported` | 只支持普通群（`chat_mode=group`），话题群等其他群模式第一版不支持 |
| `cannot_add_members` | 群设成了只有群主或管理员能加人，而 owner 两者都不是 |
| `cannot_remove_members` | `remove_extras: true`，但 owner 不是群主或管理员，移不了人。可以改成只加不减 |
| `bot_limit` | 群里现有的 bot 数加上待加入的 bot 数超过 15 |

**关联已有群：警告**

| 代码 | 含义 |
|---|---|
| `extras_stay_and_see_channel_messages` | 只加不减：群里原有的外人会一直看到频道消息 |
| `moderation_restricted` | 群限制了发言，bot 可能没法说话 |
| `share_card_allowed` / `join_without_approval` | 任何人都能分享群名片，或者入群不需要审批。外人进群后，在下一轮对账把他移出之前，能看到频道消息 |

**退出码**：0 表示可以继续；2 表示有阻断项，参数用法错误时 argparse 也返回 2；1 表示运行出错，原因写在 stderr。

### 3. 绑定：`create-chat` 或 `bind`

```bash
python3 scripts/buzz_feishu_group_sync.py create-chat --config <cfg.json> --name "<群名>"
python3 scripts/buzz_feishu_group_sync.py bind --config <cfg.json> --chat-id oc_xxx
```

- 两个命令都会先重跑预检，不通过就不改任何东西。通过后把 `chat_id` 原子写回配置文件，保持 0600，并输出 `{"chat_id", "warnings"}`。
- `create-chat` 要求配置里的 `chat_id` 是 `null`。它用个人应用的 bot 建私有群，群主设为 owner，bot 自己当管理员（LCV-03）。
- 建群之前，`create-chat` 先在配置旁边写一个 `<config>.create-intent` 文件；写回 `chat_id` 之后再删掉它。建群被明确拒绝时也会删掉。
  - 如果建群成功、但配置没写回（例如进程被杀、磁盘满），这个文件会留下来。之后再跑 `create-chat` 会直接拒绝，不会再建一个群。
  - 恢复办法：用 `lark-cli im +chat-search --as user` 找到已经建好的群，用 `bind` 绑定它，然后删掉意图文件。
- 已经绑定的配置不能再 `create-chat`，也不能 `bind` 到别的群。

### 4. 运行：`round`

```bash
python3 scripts/buzz_feishu_group_sync.py round --config <cfg.json> --state-dir <dir> [--allow-bulk-removal] [--skip-backlog]
```

每次调用跑一轮，由 owner 的 `systemd --user` timer 每分钟触发一次（示例见文末）。

- 同一个 state 目录上有文件锁，已有一轮在跑时新的一轮直接退出。
- 一个 state 目录只服务一个 `channel|chat` 绑定，换了频道或群就拒绝运行。
- **绑定从第一次身份核对通过的那一轮开始算**，之前的消息都不镜像。首轮因身份不符失败，不会提前定下起点。

每轮按下面的顺序执行：

1. **核对身份**：见上文「每轮开始先核对身份」。

2. **成员对账**
   - desired，即应该在群里的人：
     - 频道里的人，也就是角色为 owner、admin、member、guest 的成员。配置里登记过的 agent 和镜像身份，无论角色是什么，都不算人。映射方法是 pubkey → 内部身份（bridge 的人员接口给 `union_ids`，或经通讯录换出的 open_id，见上文「人的身份怎么认」）。**每一轮都取最新的**：某人解绑或离职，下一轮他就映射不到；新同事绑定并进频道，下一轮就被拉进群。取不到就整轮报错（见下面「人员接口失败」），不会猜。
     - agent：角色为 bot、在配置里登记过、而且身份核对通过的 agent，取它登记的 app_id。
     - owner 本人和个人应用的 bot 始终在 desired 里；群主（群信息里的 `owner_id`）也始终保留。
   - actual，即群里现在的成员：`+chat-members-list --as user`（union_id 模式加 `--member-id-type union_id --member-types user`，bot 另列一次，按 open_id）。
   - 差集一律用 owner 的 **user** 身份加减（LCV-04；union_id 模式的 `member_id_type` 是 `union_id`），而且**先删后加**：群里 bot 满 15 个时，先移出离开频道的 agent bot，才有位置给新 agent 的 bot。如果移除被下面的保护规则暂缓了，放不下的 bot 计入 `blocked_bots`，不会去加然后被飞书拒绝。
     - 加人时用 `succeed_type=1`，能加的先加，不可用的 id 只计入 `member_failures`。
     - 每次请求最多 50 人或 5 个 bot。
     - 某一批请求出错只记一次 `errors`，不影响后面的消息同步。
   - **人员接口失败**：整轮中止（退出码 1），不发任何消息、不动群成员，也不写 state——所以首轮失败不会定下绑定的起点。原因写在 stderr，不含响应正文。下面这些都算失败，下一分钟的那一轮会重试：
     - 连不上或超时（15 秒）、HTTP 状态不是 200（401 签名或时钟问题，404 你不是这个频道的 owner / admin 或频道不存在，5xx）、重定向（不跟随）；
     - 响应不是 JSON、超过 1 MiB、频道对不上、`people` 里有格式不对的 key（不是 64 位小写 hex）或 open_id（不是 `ou_…`）；`union_ids` 的值不是 `on_…`、`emails` 的值不是形状合法的邮箱列表（1 到 20 个；契约是小写，大小写不同、首尾有空格的地址也接受，脚本按规范形式——去空格、转小写——使用，和 `gitlab_buzz_people_generate.py` 对同一个应答的宽松处理一致）、这两个字段的 key 格式不对——不论选哪种模式，格式不对就中止；
     - 所选模式需要的字段缺失：union_id 模式没有 `union_ids`（bridge 还没部署 union_id 回填），email 模式没有 `emails`（bridge 没开 `CHANNEL_PEOPLE_EMAILS_ENABLED`），错误里会明说。不用的字段缺席不要紧；
     - union_id 模式下 owner 自己的 `user_info` 读不到、或其中的 `open_id` 不是配置的 `owner_open_id`：不知道群里哪个账号是 owner，整轮中止；
     - 签名用的 key 在频道里不是 owner 或 admin：脚本先自己报清楚，不发请求。
   - 为什么不缓存、也不在接口失败时沿用上一轮的映射：沿用旧映射会把已解绑或已离职的人继续留在群里，或者把新同事当成外人。宁可这一轮什么都不做。
   - **移人的保护规则**：
     - 只移出配置里登记过、而且本轮身份核对通过的 agent bot。群里其他 bot 一律不动。
     - 频道里只要还有映射不到的人，本轮就**不移人**（`removals_withheld: "unmapped_members"`）。因为群里的某个未知成员可能正是他；而一个出了问题的接口会让所有人都显得映射不到（不过接口出错时整轮已经中止，见下）。
     - 一次要移出超过 10 个成员时，整体暂缓（`removals_withheld: "bulk_removal"`）。确认无误后，带上 `--allow-bulk-removal` 再跑一次。

3. **Buzz → 飞书**
   - 读取：`buzz messages get --kinds 9,45001,45003 --since <游标−900s>`，返回里有格式不对的事件时整轮拒绝（丢掉一行会让这一页显得不满，翻页就会提前结束）；如果还有等待重试或结果未知的发送，就再往前读到最早那条的时间。relay 每次最多返回 200 条，并且最新的在前，所以脚本用 `--before` 往前翻页。
   - 积压超过 50 页（约 1 万条）时整轮拒绝，不会跳过任何消息。owner 确认可以放弃这段积压后，带 `--skip-backlog` 跑一轮：到当前为止的 Buzz 积压全部丢弃，以后也不再读取，报告里的 `backlog_skipped` 会列出 `buzz`。
   - 跳过的情况：
     - 镜像身份自己发的事件（回声）；
     - 绑定之前的事件；
     - 作者既不是频道里的人，也不是已核对的 agent（`not_channel_human`）；
     - 频道里没有飞书 bot 的 agent（`agent_bot_unavailable`）——除非它**本机根本没配**且开了 `buzz_unmanaged_agents: "relay"`，见下文「本机没有凭据的 agent（镜像代发）」。
   - 谁来发：agent 的发言用这个 agent 自己的 lark-cli profile 发，符合 ADR-0002 Buzz-first；人的发言用个人应用 bot 发。默认发成**卡片**（见下文「消息卡片」）；配置 `message_format: "text"` 时发文字，正文带「张三（Buzz）：」前缀，p tag 转成飞书 `<at>`。
   - 回复：有 `e` tag 的回复发成飞书话题回复，reply 标记优先于 root 标记，并把父消息登记为要轮询的话题。直接父消息在飞书里没有副本时，先补发话题根再回复，见下面「话题根补发」；补不出根才作为普通消息发出。
   - **话题根补发**（GitLab 通知就是话题回复：同一个 MR 的评论、流水线通过、新提交都回在 MR 那条根消息的话题下。根不在账本里——比绑定起点早、或当时被跳过——回复原来就成了一条条互相没有关联的顶层消息）：回复的直接父消息在账本里就挂直接父；否则找它的**话题根**，飞书里没有根就**先把根补发到飞书，再把回复作为话题回复发在这条根下面**。
     - 怎么找根：回复自己的 `root` 标记就是根 id；只有 `reply` 标记时（buzz 的直接回复都是这样），直接父如果是这一轮读到的、自己就是话题顶层的事件就用它，否则 `buzz messages thread --channel <频道> --event <直接父> --depth-limit 0`（镜像身份读）取话题根。实测 0.5.23 的 `messages thread` 返回整个话题的事件数组（形态和 `messages get` 一样，根在最前，`--event` 是根、直接回复还是嵌套回复都一样），`--depth-limit 0` 只返回根；返回里必须恰好一个顶层事件（已知根 id 时还要就是它），否则当作取话题失败，不猜。
     - 根和普通镜像走**同一条路**：同一个 `route_buzz_event` 决定谁来发（人的根走 owner bot 带署名，agent 的根走它自己的 profile）、同一个幂等键 `b2f-<sha256(event_id)[:40]>`、同一套 pending / retry / failed / unknown 账本语义，账本记 `b2f[根 id] = 飞书消息 id`，所以 reaction 同步能找到它、飞书里对它的回复能映射回 Buzz。**根再老也补**（不受绑定起点限制），但只补有新回复的话题的根：这是给回复补上下文，不是回填历史，话题里其他旧消息不补。补发的根按「现在」计时，不占读取窗口。
     - 同一轮里根一定在它的回复前面发出（读到的事件里就有根、只是同一秒排在后面时，也先发根）。同一个话题一轮只取一次；之后的回复直接在账本里找到根，不再取、不再补。回复发出后话题登记进 `threads` / `polled`，和以前一样。
     - **回复等根，不越过它**：根被飞书拒绝（`retry`），或结果不确定（`pending`：超时、网络错误）时，回复**不发**，留在 `unresolved` 里下一轮再来，根每轮按同一个键重试；根成功后回复接着发在它下面。绝不先把回复当顶层发（会乱序、还会重复）。等根的回复和别的未决项一样，超过 6 小时仍读不回来就关闭并报告一次（`failed`），实际上根的 45 分钟窗口和 3 次上限会先让它退回顶层。
     - **退回顶层**（回复不丢，作为普通消息发出，和补发之前一样）：根不可镜像——路由跳过（`agent_bot_unavailable`、`not_channel_human`、`echo`、`kind`、`empty`，跳过原因照常计入 `skipped`）；根连续 3 次被拒（记 `failed`）；根结果不确定超过 45 分钟窗口（记 `unknown`）；取话题连续 3 次失败。这些情形计 `thread_root_unavailable`（前三种）或 `thread_root_failed`（最后一种）。退回顶层发出的回复，重试时仍是顶层、同一个键。
     - **取话题失败**（buzz 报错：话题根找不到、超时、返回的不是恰好一个根）：这条回复这一轮等，不动账本；每条回复最多试 3 次（`state.attempts` 里的 `b2f-thread:<回复 id>`），第 3 次仍失败就退回顶层。每次失败记 `errors` 和 `thread_root_failed` 各一次。
     - **单轮上限**：每轮最多取 `THREAD_ROOT_LOOKUPS_PER_ROUND`（20）个话题；超出的回复这一轮不发、留在 `unresolved`（`thread_roots_deferred` 计数），并且这一轮的 Buzz 消息游标不前进，下一轮接着补，一条不丢、一条不重。已知根 id 且账本里已有副本、或根就在这一轮读到的事件里的话题不占上限。
     - 没有新增 state 字段：等根的回复只在 `unresolved` 里（`b2f` 里没有它），根用它自己的 `b2f` 条目（pending / retry）；旧版本写的 state 原样可用，旧版本按顶层发出、结果未知的回复重试时仍是顶层。
     - 只改 Buzz → 飞书方向，不改文字以外的格式：根是一条普通的镜像消息，所以（默认）也是一张卡片，标题是根的第一行、正文第一行是根的作者。
   - 图片：事件带的图片附件（`imeta` tag）在文字（默认是卡片）发出之后，由**同一个发送者**作为随后的图片消息发出，文字在话题里时图也进同一个话题。正文里与附件重复的 `![image](url)` 从文字里去掉。补发的话题根（见上面「话题根补发」）也一样：根（默认是一张卡片）带的图片跟在根的卡片后面发出。规则、上限和失败语义见下文「图片同步」。

4. **飞书 → Buzz**
   - 轮询：每轮用 user 身份拉两次群消息。
     - 镜像用：从游标窗口（上一轮时间 −120s，再往前覆盖等待重试的消息）开始，按时间正序读，最多 40 页（2000 条）。离线再久，上一轮之后的消息也都会补回；一轮读不完时整轮报错，不静默截断。owner 确认可以放弃后，带 `--skip-backlog` 跑一轮：到当前为止的飞书积压全部丢弃，`backlog_skipped` 列出 `feishu`。
     - 发现话题用：最近 6 小时里最新的 500 条，只用来发现带话题的根消息，不据此镜像。
     - 两次都按两个信号判断是否读完：`data.has_more` 和 `meta.pagination.complete`（实测被截断时两者都会给）。
   - 话题：每轮轮询最活跃的 10 个，再轮换 10 个最久没轮询过的，所以每个话题都会轮到；超过 7 天没有动静的话题不再轮询。
     - 每个话题有自己的游标（上次完整轮询的时间 −120s），隔几轮才轮到的冷话题也不会漏掉回复。在 Buzz 侧回复而登记的话题，游标从上一轮算起。
     - 话题回复按时间倒序读最新的 500 条（10 页）。两次轮询之间的新回复多于 500 条时，镜像已读到的部分，计一次 `errors`，这个话题的游标不前进。
     - 某个话题出错只计一次 `errors`，不影响其他话题；持续出错的话题排到轮换队尾，不会一直占着名额。
   - 跳过的情况：
     - 已删除的消息、系统消息、`sender_type=app` 的消息（agent 的话本来就来自 Buzz）；
     - 映射不到的发送者（`unmapped_sender`，D-L6）——默认 `feishu_unmapped_senders` 为 `"context"` 时不跳过，改按「非成员」仅作上下文镜像；只有显式 `"skip"` 或受发言人白名单限制时才计这个跳过原因，见「非成员的发言（仅上下文镜像）」；
     - union_id 模式下发信人配对不上（`sender_unpaired`）、缓存与新证据矛盾（`identity_conflict`）；被 @ 的人配对不上只是不产生 @（`mention_unpaired`），消息照发；
     - 已经不在频道里的发送者（`sender_not_in_channel`）；
     - 配置了 `feishu_sender_allowlist` 时，已映射、在频道里、但不在名单里的发送者（`sender_not_allowed`），见「只让特定的人的发言进 Buzz」；
     - 内容为空的消息（`empty`）：先于认人判断，所以不会为它调飞书。
   - 镜像：用镜像身份带「[飞书] 张三：」署名发进频道。
     - `mentions[]` 里当前频道成员的实体会转成 `--mention`：agent 的 bot 对应该 agent 的 pubkey，人对应这个人的 pubkey。@所有人、@owner bot、@自己都不转。只能看 `mentions`，不能匹配正文（LCV-09）。
     - 话题回复用 `--reply-to` 挂到根消息对应的 Buzz 事件下面。
     - 超过 10 分钟的积压，会在正文末尾注明飞书上的原始时间。
     - 图片：人发的 image 消息和富文本里的图，作为图片附件随文字一起镜像（见下文「图片同步」）；文件、音频、视频仍按 lark-cli 渲染出来的文字镜像，不下载、不转发。撤回和删除都不同步。

5. **Buzz reaction → 飞书表情**（排在两个消息方向之后：目标消息在同一轮里刚镜像出去，agent 对它打的 reaction 也能在这一轮落上）
   - **只同步 agent 的 reaction**，由这个 agent 自己的 bot 打，不由 owner bot 或别的 bot 代打：
     - 人的 reaction 不同步（`reaction_human`），飞书上的表情也不同步回 Buzz；
     - 作者不是频道里的 agent（`reaction_not_agent`），或这个 agent 没有可用的飞书 bot（`agent_bot_unavailable`）：跳过。
   - 读取：用镜像身份 `buzz messages get --kinds 7,5`（kind 7 是 reaction，kind 5 是撤销）。游标是**单独的** `react_since`，重读窗口 900 秒，读取起点不早于绑定起点和 `buzz_floor`（用 `--skip-backlog` 丢弃 Buzz 积压的时间点）。
   - 表情映射：先去掉 emoji 的变体选择符（U+FE0E、U+FE0F）和首尾空白，再查表。默认表如下，配置的 `reaction_map` 可以追加或覆盖；查不到（比如 🚀）就跳过并计 `reaction_emoji_unmapped`，不影响同一批里的其他 reaction。

     | Buzz | 👀 | 💬 | ✅ | 👍、`+` | 👌 | 🙏 | 💪 |
     |---|---|---|---|---|---|---|---|
     | 飞书 emoji_type | `GLANCE` | `Typing` | `DONE` | `THUMBSUP` | `OK` | `THANKS` | `MUSCLE` |

     👀 和 💬 是 agent 收到请求时打的两个。
   - 目标：kind 7 里第一个合法的 `e` tag（没有就是 `reaction_no_target`）。目标必须**已经镜像**：Buzz 上的消息用它在飞书的副本，飞书上的消息（镜像进 Buzz 的那条）反查回飞书原消息。目标还没镜像出去（比如飞书限流没发成功）时跳过并计 `reaction_target_unmirrored`，只要那条 reaction 还在重读窗口里，之后每一轮都会再试。绑定起点之前的 reaction 不补（`before_binding`）。
   - 打上之后，账本 `r2f[reaction 事件 id]` 记作者、飞书消息 id、emoji_type 和飞书返回的 reaction_id（用 `|` 连成一串），同一条 reaction 在重读窗口里被读到多次也只打一次。飞书对同一（消息、表情、应用）的创建是幂等的，所以结果不确定后的重试不会叠出两个表情。
   - **撤销**：kind 5 指向那条 kind 7，并且 **kind 5 的作者就是这条 reaction 的作者**，才算撤销：
     - 别人发的 kind 5 不算（`reaction_delete_foreign`），指向不相干事件的 kind 5 直接忽略；
     - 已经打上的，用同一个 agent 的 bot 把飞书表情删掉，账本改记 `removed`，之后不再补回；不论中继撤销后还留着那条 kind 7 还是把它删了都一样；
     - 同一批里先打后撤的，飞书上根本不出现（`reaction_withdrawn`），也记进账本，之后即使读不到那条 kind 5 也不会补打；
     - 同一批里的 kind 7 和 kind 5 按 `created_at` 从旧到新处理：撤销后又打回同一个表情时先删再加。反过来会先拿到旧 reaction 的 id（飞书对同一表情只有一个 reaction），再被删掉，表情就没了；
     - 撤销时配置里已经没有这个 agent：没人能替它删，不调用任何接口，账本记 `removed`，飞书上的表情留着。
   - **失败**：飞书拒绝或结果不确定（超时、传输错误）都下一轮重试；连续 3 次没成功就放弃，账本记 `failed`（撤销记 `removed`），`reactions_failed` 加一。重试只发生在那条 reaction 还在重读窗口（900 秒）里的时候。**表情是装饰，它的失败不触发「需要关注」**。
   - **单轮上限**：一轮最多 50 次飞书调用（打和删合计）。超出的留到下一轮，这一轮游标不前进，一个不丢、一个不重。
   - **读不到**：中继读 reaction 出错时整轮报错（退出码 1），但消息已经先同步完并落盘，下一轮不会重发。积压超过 50 页（约 1 万条）时不卡住整轮：读不完的部分丢弃，游标推到当前时间，`backlog_skipped` 列出 `reactions`（需要关注，退出码 3），不需要 `--skip-backlog`；消息同步照常。丢弃不会推高读取起点，所以 900 秒重读窗口里的积压还会被读到，每轮再报一次，直到它老出窗口。

6. **推进游标**：消息两个方向的游标都推到本轮的墙钟时间，不取「见过的最新消息时间」。（reaction 有自己的游标 `react_since`，在上一步结束时推进，被单轮上限截住的那一轮除外。）
   - 这样时钟偏快的客户端推不动游标；时钟偏慢的客户端，靠 900s（Buzz）和 120s（飞书）的回看窗口补上。
   - 当时跳过的消息，之后也不会回填。

**正文和署名都要中和，只有显式 @ 才能通知**：

- buzz CLI 会把正文里唯一解析得到的 `@名字` 和 `nostr:npub1…` 自动变成通知（`messages send --help`：「uniquely resolved member names still notify」；`gitlab_buzz_sync.neutralize` 也是为此而写）。所以飞书→Buzz 的正文复用 `neutralize`，把 `@` 换成 `＠`、`nostr:` 换成 `nostr：`，通知只来自 `--mention`。
- 飞书会把文本里的 `<at …>` 当成真 @。所以 Buzz→飞书的正文把 `<at` 和 `</at`（不分大小写）换成全角 `＜`，p tag 生成的 `<at>` 在中和之后才附加。
- display name 是用户自己改的，两个方向的署名和 `<at>` 标签文字都要先清洗：
  - 去掉零宽字符、双向控制符等格式字符；
  - 把所有换行（含 U+2028、U+2029、U+0085、VT、FF）压成空格；
  - 尖括号和引号换成全角字符，`@` 和 `nostr:` 按上面的规则中和。
- 正文先去掉双向控制符、再中和 `@` 和 `nostr:`（顺序不能反：夹着控制符的 `nostr:` 中和不掉，控制符去掉以后又变回 `nostr:`），最后对整段文字再中和一遍。从第二行起（按所有换行符分行，包括 CR、U+2028、U+2029、U+0085），形如对方署名格式的行（以「[飞书]」开头，或含有「(Buzz):」，不分大小写，不限前面名字的长度）前面加 `↳ `，一眼看得出这不是另一条镜像消息。
  - 匹配前先做 NFKC 归一化，再去掉格式字符、组合符、变体选择符和空白填充字符（如 U+3164、U+2800），所以全角变体和夹在中间的不可见字符都绕不过去。
  - 陌生人（`feishu_unmapped_senders: "context"`）的正文用更宽的判断，见「非成员的发言（仅上下文镜像）」；成员的正文仍是这里的判断，逐字节不变。
  - 这只是显示层的提示：真正的署名永远在消息第一行，正文里用普通文字冒充别人（例如「老板说……」）无法杜绝，和任何聊天工具一样。

**不重发原则**（发送前先在 state 里记下 `pending:<首次尝试时间>` 并落盘）：

| 方向 | 结果未知（超时、网络错误、没有返回 id） | 确定被拒（CLI 报错，或 relay 回复 `accepted:false`） |
|---|---|---|
| Buzz → 飞书 | 从首次尝试起 45 分钟内，每轮用同一个幂等键、同一个接口重试。飞书对同一个键大约一小时内只发一次，而且按接口计，所以首次是普通发送就一直普通发送，首次是回复某条就一直回复那条。读取窗口会往前覆盖这些未决事件，不受 900s 回看限制。超过 45 分钟仍未成功，记为 `unknown` 终态并报告一次，之后不再重试 | 记为 `retry:<首次尝试时间>`，下一轮重试；同样受「首次尝试起 45 分钟」的限制（先前可能有一次结果未知的发送已经送达），超过就记为 `failed`；连续 3 次都被拒也记为 `failed` |
| 飞书 → Buzz | 立即记为 `unknown` 终态并报告一次，永不重发（buzz CLI 没有幂等键） | buzz 退出码 1（输入错误）、3（鉴权）或 `accepted:false` 时记为 `retry:<时间>`，下一轮重试，即使消息已经滑出游标窗口；连续 3 次都被拒就记为 `failed` |

- 什么算「确定被拒」：buzz CLI 的退出码 1、3 或 `accepted:false`；lark-cli 错误里 `type` 为 `api`、`validation`、`authentication` 或 `permission`。其他情况（超时、`type: network`、输出不是 JSON）一律按结果未知处理，因为请求可能已经到达飞书。
- 幂等键由完整 event id 的 sha256 派生，每个事件一个，最长 44 个字符。卡片和文字用同一个键；卡片被飞书拒绝后回退发出的文字用 `<键>-text`（45 个字符，lark-cli 的上限是 50），见「消息卡片」。图片的键是 `b2f-img-<sha256(事件 id:序号)[:36]>`（44 个字符，飞书的上限是 50），每张图一个，按接口计一小时，规则同上：结果不确定的图用同一个键、同一个接口重试。
- 未决项（等待重试或结果未知）如果之后路由到「跳过」，例如作者离开了频道，立即关闭：可能已送达的记 `unknown`，确定没送达的记 `failed`，并报告一次。
- 未决项超过 6 小时仍读不回来（例如原消息被删了），同样关闭并报告一次，读取窗口不再为它们往回拉。

**输出与退出码**：stdout 是一个 JSON 报告，字段有：

- 成员相关：`added_users`、`removed_users`、`added_bots`、`removed_bots`、`blocked_bots`、`member_failures`、`removals_withheld`、`unmapped_members`；
- `identity_conflicts`：身份互相矛盾的次数（缓存的旧配对与新证据矛盾、一个人的多个邮箱指向不同的账号、两个 pubkey 共用同一个飞书账号）；非零需要关注；
- `backlog_skipped`：本轮丢弃了积压的方向（`buzz`、`feishu` 用 `--skip-backlog`；`reactions` 是自动丢弃），没有就是空列表；
- 消息相关：`to_feishu`、`to_buzz`、`unknown`、`failed`、`errors`；
- `context_to_buzz`：生效模式为 `feishu_unmapped_senders: "context"`（缺省）时就有，是 `to_buzz` 里属于非成员发言的那部分；只是计数，**不会**让退出码变成 3；
- 图片相关：`images_to_feishu`、`images_to_buzz`（发出去的张数）、`images_failed`（下载或发送失败、放弃的张数）、`images_skipped`（一个对象，键是原因，见下文「图片同步」，是策略不是故障）；`images_failed` 非零需要关注，`images_skipped` 不触发；
- 话题根补发：`thread_roots_backfilled`（补发到飞书的根，也计入 `to_feishu`）、`thread_root_unavailable`（根不可镜像或已放弃、回复退回顶层的条数）、`thread_root_failed`（取话题失败的次数）、`thread_roots_deferred`（超过单轮上限、留到下一轮的回复条数）；这四个只是计数，**自己不会**让退出码变成 3（取话题失败另计一次 `errors`，那个会）；
- 卡片相关：`cards_sent`（发成卡片的条数）、`cards_fallback_text`（卡片被飞书拒绝、改发文字的条数）；这两个只是计数，**不会**让退出码变成 3；
- 代发：`relayed_agents`（本机没有凭据、由镜像 bot 代发的 agent 消息条数，见「本机没有凭据的 agent（镜像代发）」；也计入 `to_feishu`）；只是计数，**不会**让退出码变成 3；
- 表情相关：`reactions_added`、`reactions_removed`、`reactions_failed`（放弃的次数）；这三个只是计数，**不会**让退出码变成 3；
- `skipped`：一个对象，键是跳过原因（见上文各处）。

退出码：

| 退出码 | 含义 |
|---|---|
| 0 | 本轮干净 |
| 3 | 本轮跑完了，但有需要关注的事：`errors`、`unknown`、`failed`、`blocked_bots`、`member_failures`、`removals_withheld`、`identity_conflicts`、`images_failed` 或 `backlog_skipped` 至少一项非零或非空 |
| 1 | 运行出错。stdout 仍会输出已完成部分的报告，原因写在 stderr；如果是 lark-cli 登录失效，会提示 owner 重新执行 `lark-cli auth login` |

### 5. 发使用说明（每配置好一个群都必须做，不能省）

首轮**成功**之后——报告里 `errors`、`unknown`、`failed`、`blocked_bots`、`member_failures`、`removals_withheld`、`identity_conflicts` 全是 0（或 `null`），`backlog_skipped` 是空——**给这个飞书群发一条使用说明**（`unmapped_members` 不为 0 不算失败：那是还没绑定飞书账号的人，说明第 4 条已经告诉他们去绑定）。任何一项不干净，先按上面「首轮验收」和各失败原因处理，处理好再发，不要在群还没配好的时候发出一份会误导人的说明，告诉大家「什么场景 @ 哪个助手」。群里的人（尤其是原来就在群里、并没有参与配置的同事）不知道多出来的 bot 是干什么的，不发说明就等于没交付：他们要么不敢 @，要么 @ 错人。

**怎么发**

- **发送者是 owner 应用的 bot**（不是某个 agent）：`lark-cli im +messages-send --as bot --chat-id <群> --text "$(cat guide.txt)" --idempotency-key usage-guide-<配置名>-1 --format json`。带幂等 key，重跑不会发两遍。
- **正文里不要出现 `<at …>`**，助手名字写成纯文本 `@nh-desk`：飞书里 `<at>` 会真的 @ 到 bot，把 agent 唤醒去回答一段说明。bot 发的消息同步脚本会跳过（`sender_type=app`），所以说明不会回流进 Buzz。
- 群被禁言时 bot 发不出去（`230035 Send Message Permission deny`）：让群主放开发言，或者用群主本人的身份（`--as user`）发；不要绕过去。

**写什么（模板，按群改）**

```
【<频道名> 助手使用说明】本群已接入 Buzz 的「<频道名>」频道，直接在群里 @ 助手就行（输入 @ 选人）。什么场景 @ 谁：

• @<入口 agent>：<一句话职责，来自它的 prompt>。不知道找谁先找它。
• @<agent 2>：<一句话职责 + 它不做什么>。
• …

使用小贴士：
1. 一件事发一条消息、@ 一个助手，把背景、链接、期望结果写清楚；回复会在你那条消息的话题里，点开话题查看。
2. 助手收到后会先在你的消息上打个 👀，处理要一点时间（同步大约每分钟一次，复杂的分析可能几分钟）。
3. 本群和 Buzz 频道是双向同步的：已绑定飞书账号的同事在群里的发言会出现在频道里，频道里的消息也会同步到这里。
4. @ 之后一直没反应：先确认你已经在绑定页绑定了飞书账号（<bridge 的 /bind/ 地址>），没绑定的话助手认不出你，你的发言也不会同步到频道。
5. 不确定找谁，就 @<入口 agent>。
```

**写之前逐条核对，别凭印象**

- **职责取自每个 agent 自己的 prompt**（`~/.config/buzz/agents/<agent>.prompt.md` 的第一句和「职责」一节），不是凭名字猜；写「它不做什么」比写「它能做什么」更有用（例如 desk 不写代码、dev 只提 Draft MR、sre 只读调查）。
- **只写这个群里真有飞书 bot 的 agent**（配置的 `agents`）。频道里还有别的 agent 但没有飞书应用时，一句话说明「暂时不同步到本群，在飞书里只能找 <入口 agent>」，别让人 @ 一个不在群里的助手。
- **受限响应模式的 agent**（`BUZZ_ACP_RESPOND_TO=owner-only`，或 allowlist 没有镜像身份，例如 MR 合并前检查员）在飞书里 @ 不会有反应，要写明「自动工作，飞书里 @ 它不会触发」；不要为了让它能被 @ 而临时放宽这类 agent（见「一次问齐这些问题」第 5 条）。团队 Channel 里本来就要响应所有成员的普通 agent 应按 runtime-setup 设为 `anyone`，它会响应镜像身份发出的真实 p tag。
- **入口 agent 要单独点出来**，第 5 条的「不确定就 @ 谁」指向它。
- 群里有不在频道映射里的人、`remove_extras: false` 时，这些人也会看到频道消息：使用说明第 3 条要如实写「双向同步」，让他们知道发言会进频道。
- 默认生效的 `feishu_unmapped_senders: "context"` 群：第 3、4 条要写清——没有绑定飞书账号的同事在群里的发言和图片也会作为「[飞书·非成员]」出现在频道里供 agent 参考；**真实 @助手会触发响应**（选中的 @agent 会产生 p tag，和频道成员一样），但 **@ 不到别的同事**（要能被 @ 到就得成为频道成员并绑定）。不写清「图片会同步、能 @ 到谁、@ 不到谁」，大家会猜不透规则。

**发完之后**

- 用 owner 的 lark-cli 读群里最近的消息，确认说明在、格式对（`+chat-messages-list` 或 `api GET /open-apis/im/v1/messages`）；下一轮 `round` 的报告里 `to_buzz` 不应该多出一条（bot 消息不回流）。
- 把「已发」和发的是哪一版写进这个群的配置记录（记忆 / 运维文档），换绑或废弃时要用（见「换绑到另一个飞书群」）。
- **废弃一个群**时反过来：先发一条废弃通知（指向新群），再把 bot 移出，最后撤回同步发进去的消息（bot 只能撤回自己发的：用各自的 profile `DELETE /open-apis/im/v1/messages/<id> --as bot`）；群被禁言时通知改用群主身份发。

## 消息卡片：Buzz → 飞书默认发成卡片

配置 `message_format`（可选）：`"card"`（缺省）或 `"text"`。`text` 就是卡片出现之前的样子：一条文字消息，人的发言带「张三（Buzz）：」前缀，p tag 转成 `<at user_id=…>`。要退回只改这一个键，下一轮起生效；已经发出去的不动、不重发，state 不用迁移（没有新增字段）。已经在重试的消息（结果未知、或被拒还没发出去的）**按首次尝试的方式重试**：切换 `message_format` 不改它们——首次是文字的仍发文字、首次是卡片的仍发卡片，同一个幂等键下的请求不变；只有切换之后的新消息用新的格式。

**一条被镜像的 Buzz 消息 = 一张短卡片**（卡片 JSON 2.0）。手机上不刷屏是第一目标（jchen 2026-09-23）：折叠之前只有标题和一行灰字（加 @ 行），正文全部收在「展开全文」里，没有副标题、没有预览、没有底部按钮：

| 部分 | 内容 |
|---|---|
| 标题 | **消息的第一行**（#127）：正文第一个非空行，取纯文字——加粗、删除线、斜体、行内代码的记号、行首的标题/引用/列表/任务记号去掉，链接和图片只留文字（地址不进标题）；标题里的转义方括号与链接包装一并清洗（同步为防伪造把标题里的反斜杠和方括号写成 `\\` `\[` `\]`，门牌行是链接 `[#132 \[标题\] …](url)`：链接只留文字，转义还原成原来的字符，与同步的 `_md_escape` 互逆；反斜杠后面是别的字符的不动）；一行、清洗过（同名字的清洗），手机一行要放得下：最宽 36 列（中文、全角、emoji 算 2 列，其余算 1 列，约 18 个汉字），超出取前面放得下的部分加「…」（「…」按 2 列留位）。同步消息的机器行（header 行、`🔔 通知` 行）不进卡片、取标题之前就已去掉，旧 `key: value` 长格式取 `title:` 的值（见下「机器行不进卡片」）；分隔线和只剩记号的行跳过；首个有内容的行是代码围栏就不从代码里取；没有可用的行（空正文、只有 header）就退回发言人。36 列是按手机屏宽估的，没有真机实测，实测后再调 `CARD_TITLE_COLUMNS` |
| 副标题 | 没有（2026-09-23 起；原来的「发言人 · #频道名」挪到正文第一行和摘要里） |
| 正文第一行 | 「发言人 · #频道名」，灰色小字、`plain_text`（名字成不了链接或标签），右边一个小号的「在 Buzz 中打开」链接（markdown，两者用 `column_set` 排在同一行）；链接不合法时只有灰字。发言人是人的 Buzz 显示名（清洗过，没有名字用 pubkey 前 12 位）或 agent 的显示名，各最长 60 个字符；频道名读 `buzz channels get --channel`，第一张要发的卡片才取，一轮最多取一次（缓存在内存里）；取不到就只写发言人，不影响发送。不再有「（Buzz）：」前缀 |
| 颜色 | 人 blue，agent green |
| 消息列表里的一行 | `config.summary`（摘要）：「发言人 · #频道名：内容前约 60 个字符」（取不到频道名就是「发言人：…」），纯文本、一行，超出加「…」；内容是去掉机器行之后的（不以 header 开头，旧格式也没有 `title:` 键名），并和标题走同一套清洗（链接只留文字，加粗、删除线、斜体、行内代码的记号和转义都去掉），清洗在截断之前 |
| @ 行 | 有 p tag 时，正文第一行之后单独一行，见下 |
| 折叠面板 | 消息比标题多出内容时就有（标题被截断、丢了链接地址、是表格行，或者还有别的行）：「展开全文（N 字）」，默认收起，里面是**完整的** markdown（含标题那一行；加粗、列表、表格、代码块、链接、引用都保留）。整条消息只有标题这一行时没有面板 |
| 按钮 | 没有（2026-09-23 起，改成正文第一行的小号链接） |
| 图片 | 卡片里不放图片：事件带的图片附件在卡片之后作为随后的图片消息发出（同一个发送者、同一个话题，见「图片同步」）；正文里与附件重复的 `![image](url)` 已经去掉，别的 `![alt](url)` 改成「[图片：alt] 地址」，所以卡片的 markdown 里没有外链图片语法 |

**机器行不进卡片**（skills#134）：GitLab → Buzz 同步消息里有几行是给程序读的，对飞书上的读者是噪音，卡片的任何位置都没有——标题、正文、折叠全文、`config.summary`。只影响卡片：Buzz 里的消息、同步的去重与读回、文字模式（`message_format: text`）和卡片被拒后回退的文字都不变。中和之后按结构认，不按内容猜：

- **header 行**：整行以 `[gitlab-notify:v1]` 开头。只认同步自己认 header 的两个位置：最后一个非空行（新格式，2026-09-18 起）和第一行（旧格式）；正文中间、句子里、代码围栏里引用的同名文字是作者写的内容，不动。
- **`🔔 通知 @…` 行**（ADR-0012，给 Buzz 客户端做正文 @ 高亮用）：只在带 header 的同步消息里去掉；卡片自己的 @ 行（见下「@ 的三种形式」）不受影响，仍然只在有 p tag 时出现。人在普通消息里写的「🔔 通知 …」不动。
- **旧 `key: value` 长格式**（同步 2026-09-18 之前的写法）：header 在第一行、第二行是 `title: 值`，后面是 `url:` `labels:` `assignees:` `milestone:` `description:`（MR 还有 `branches:` `sha:` `reviewers:` `author:`）各一行。标题取 `title:` 的值、不带键名；折叠全文里这一行同样不带键名。其余键值行照常显示，`url:` 那行保留方便点开，`unmapped:` 是给人看的说明也保留。只按结构认（header 首行 + 第二行是 `title: 值`）：没有 header 的 `title:` 行、header 在末行的 `title:` 行、header 首行但第二行不是 `title:` 的（2026-09-17 的紧凑 MR 格式）都不改。

存量旧格式的消息不迁移（同步对存量 Thread 不变），约 2500 条；每个旧话题第一次有新回复时，它的根会被补发成卡片（`thread_roots_backfilled`），就是上面的旧格式，所以这条规则要长期在。

**「在 Buzz 中打开」链接只能是 https**。飞书桌面端会吞掉 `buzz://` 这类自定义协议（2026-09-17 在 Mac 上实测：点了没反应），所以卡片里不用它——用户自己在正文里写的 `buzz://` 也会被中和成「buzz：//」，整张卡片里没有 `buzz://`。链接指向 bridge 的 `/bind/open` 页面：`<people_api.base_url>/bind/open?e=<事件 id>&c=<频道 UUID>[&t=<话题根事件 id>]`。页面在浏览器里打开，再唤起 Buzz Desktop 定位到这条消息。`t` 只在这条消息在话题里且 e tag 标了 root 时才有（取法与 bridge 的 `threadRoot` 一致：有 root 标记就是它；没有任何标记取第一个；只有 reply / mention 标记就没有）。id 必须是 64 位小写 hex / 小写 UUID，`base_url` 是校验过的 `https://主机[:端口]`；不合法就不放链接，卡片照发，不算失败。

**为什么链接不带签名也可以**：bridge 给自己的通知卡片链接带 `s`（HMAC）和 `n`（频道名），但这个签名只保护「频道名」这个显示文字——页面验不过签名就丢掉 `n`，仍然用 `e` / `c` / `t` 打开（生产实测不带签名是 HTTP 200，页面就是「在 Buzz 中打开」）。所以这里不需要 bridge 的任何密钥，链接里也不带频道名和签名；**不要**去拿或猜那把 HMAC key。

**@ 的三种形式**：p tag 里的每个人（发言人自己和重复的不算，一行最多 20 个），按优先级：

1. 邮箱已知 → `<at email=…></at>`。邮箱来自 bridge 人员接口的 `emails`（bridge 开了 `CHANNEL_PEOPLE_EMAILS_ENABLED` 才有），只含已映射的人（共用同一个飞书账号的不算），只在内存里用于这一轮，不进 state、不进报告；
2. 否则 owner 应用的 open_id 已知 → `<at id=ou_…></at>`：union 模式下是飞书自己配对出的（state 的 `idmap` 反查；那个人得在飞书群里发过言才配得出来），agent 的 bot 是群里的成员 id。email 模式下人只能经邮箱映射，所以总是第 1 种；
3. 都没有（映射不到、bridge 没给邮箱又没配对过）→ 不通知的纯文字 `@名字`（名字同样清洗）。

**绝不能用 union_id**：卡片里 `<at id=on_…>` 会被飞书拒绝（错误码 230099，`ErrCode: 100290 there is an invalid user resource (at/person) in your card`）；文字消息里的 `<at user_id="on_…">` 可以，卡片不行。open_id 又按应用隔离，所以 **agent 自己的 bot 发的卡片不用 owner 应用的 open_id**，只用邮箱（各应用通用）或纯文字。

**用户文字进卡片前一律中和**：`<` 全部换成全角 `＜`（所以 `<at id=all>`〔@所有人〕、伪造的 @、`<font>`、`<a>` 都成不了卡片标签，代码块里的 `<` 也会变成 `＜`）；去掉双向控制符和其他控制字符，各种换行统一成 `\n`，孤立的代理码位换成 `?`；沿用「像另一边署名的行前面加 `↳ `」的标记；名字、频道名和标题（消息的第一行；旧格式是 `title:` 的值）走清洗并限长——标题在清洗之前已经过上面的中和，`@` 变成全角 `＠`、引号和尖括号变成全角；同步消息的机器行是在中和之后按整行去掉的（见上「机器行不进卡片」）。markdown 语法本身（加粗、列表、表格、代码块、链接、引用）**保留**——Buzz 的消息本来就是 markdown。

**30 KB 上限与截断**：飞书拒绝 30 KB 及以上的卡片。整张卡片 JSON 超过 28 KB 时，脚本二分找出折叠面板还能放下多少个字符（按字符截断，不切断字符；截断处落在代码围栏里就先补收尾的围栏），末尾加一行「（内容过长已截断，完整内容请在 Buzz 中打开）」；任何内容出来的卡片都 < 30 KB（名字、频道名、@ 行也都有上限）。标题和正文第一行不受影响。截断时在链接、图片、裸 URL、行内代码、加粗中间就退回到它开始之前（宁可少放一点）；转义（`\[` `\]` `\\`）是文字不是语法，截在转义中间就连半个转义一起去掉。

**飞书拒绝卡片时：回退成文字**。飞书明确拒绝这张卡的内容（lark-cli 错误 `type` 是 `api` 或 `validation`，且不是限流：限流码 230020、11232、99991400）说明这张卡没发出去：立即用**文字发送路径**重发同一条消息——文字内容与 `text` 模式一样，同一个父消息（话题回复还是话题回复）、同一个 bot（agent 的卡片改发文字仍由它自己的 bot），幂等键换成 `<键>-text`；`cards_fallback_text` 加一，`cards_sent` 不加，不算错误。账本里这条消息记 `,text`：之后不论文字被拒还是结果不确定，都只重试文字（文字可能已送达，再发卡片会重复）。总的规则：账本 extra 记的是首次尝试的发送方式（`,card` = 首次是卡片，`,text` = 卡片被拒后改发的文字，没有记号 = 文字模式的普通文字，和旧版本一样），每次重试**原样重复首次尝试**，同一个键、同一个请求。认证、权限、限流这类拒绝说的不是卡片内容，文字也会被拒，所以**不回退**，照旧记一次拒绝、下一轮用卡片重试。**结果不确定的失败（超时、`type: network`）不回退**：卡片可能已经发出去了，用同一个键、同一个接口重试，飞书按键去重。

**飞书 → Buzz 不受影响**：我们的 bot 发出的卡片在飞书里是 `sender_type=app`、`msg_type=interactive`。账本认得自己发出去的消息 id；即使账本丢了，读群消息和话题回复时也按 `sender_type=app` 跳过，不会被当作人的发言镜像回 Buzz，也不会在话题轮询里被当成回复。

## 图片同步

两个方向都同步图片；其他文件类型不同步。所有图片相关的失败都只影响那一张图：文字和别的图照发，也不会造成文字重发。

### Buzz → 飞书

- **来源**：事件的 `imeta` tag（NIP-92 风格，每个字段是「键 值」字符串），实测形态：`["imeta", "url https://<relay>/media/<sha256>.jpg", "m image/jpeg", "x <sha256>", "size 146535", "dim 1366x1200", "blurhash …", "thumb …"]`。Buzz CLI 发带附件的消息时还会自己在正文后面加一行 `![image](<url>)`。`url` 要鉴权（不带签名直接取是 401），所以用镜像身份的 `buzz media get <sha256>[.ext] -o <临时文件>` 下载；脚本**只把 url 里的 `<64 位小写 hex>[.扩展名]` 一段**交给 CLI，不交整个 url（CLI 本来也拒绝非 relay 的 origin）。`x` 必须与 url 里的 sha256 一致，`m` 和 `size` 都不信。
- **逐张校验**（不通过就跳过并按原因计数，不影响别的）：
  | 检查 | 不通过时的原因（`images_skipped`） |
  |---|---|
  | imeta 的形状：`url` 是 `<主机>/media/<sha256>[.ext]`（不是缩略图、没有查询串、没有大写）、`x` 一致 | `bad_imeta` |
  | 声明的 `size` 超过 10 MB（不下载）；或下载下来的字节超过 10 MB | `too_large` |
  | 字节的魔数不是飞书接受的图片格式（jpg / png / webp / gif / bmp / tiff）——不看 `m`，也不看扩展名；svg、html、pdf 都不是 | `not_image` |
  | 字节的 sha256 不是事件写的那个 blob | `hash_mismatch` |
  | 下载下来的不是普通文件（符号链接、目录） | `unreadable` |
  | 同一个事件最多 9 张（去重之后、含不认的形状），多出的只计数 | `over_limit`（按张数计） |
  | 事件的文字没有确认发出（`failed` / `unknown`）——没有说明、人的图没有署名，不发图；放弃文字的那一轮就计（这个事件之后不会再被读到）；文字是在别的路径上被关成终态的（放弃积压、旧版本的 state），事件再被读到时同样结算 | `text_not_sent` |
  | 文字发出以后发送者不能再发了（agent 的 profile 与配置不符、bot 不在群里）——绝不改由别的 bot 代发 | 跳过文字时的原因，如 `agent_bot_unavailable` |
- **发送**：文字先发（默认是一张卡片，见「消息卡片」；`message_format: text` 时是带署名的文字，卡片被飞书拒绝而回退成文字时也一样）；文字发出（或早先已发出）之后，按 imeta 顺序把每张图作为**随后的独立图片消息**发出——卡片本身不放图片（卡片的 markdown 只认飞书自己的 image_key，不认外链），发送者与文字**一致**（人的发言走 owner 应用 bot、agent 的走它自己的 bot），文字是话题回复时图也用 `+messages-reply --reply-in-thread` 发到同一个父消息。lark-cli 只收**相对当前目录**的文件名（绝对路径和 `..` 直接被拒），所以图放在 mkdtemp 的 0700 临时目录里、lark-cli 的 cwd 就是这个目录，文件名是脚本生成的 `img-<序号>.<按魔数认出的扩展名>`；发完立刻删文件，一个事件处理完整个目录删掉，账本里没有任何本地路径。
- **只有图片、没有说明文字**时，文字消息（卡片模式下是一张正文为「[图片]」的卡片）是占位「[图片]」（文字模式下人的仍带署名）：文字消息是这个事件在飞书里的「本体」，话题和账本都挂在它上面。
- **正文里的 markdown 图片语法**（文字与卡片规则一致：卡片的标题与折叠全文、消息列表里的那一行、回退的文字，都用处理过的同一份正文）：与 imeta 重复的 `![image](url)` 去掉（图作为图发）；没有 imeta 的 `![alt](url)`（agent 手写的、外部图片）**不下载**，改成「[图片：alt] 地址」（alt 为空或只是 CLI 自己的占位 `image` 时是「[图片] 地址」；地址不是 http(s) 就不带地址），不会把一串 markdown 原样发出去。
- **账本与幂等**：state 里的 `images`，键 `<事件 id>:<序号>`，值是飞书消息 id、`pending:<首次尝试时间>`、`retry:<首次尝试时间>`、`failed`、`unknown` 或 `skipped`；文字发在哪里也记在这里（键 `<事件 id>:thread`，值是文字所在话题的根消息 id，`-` 表示不在话题里），图片跟着它走——不是每次重新按直接父消息推算，因为直接父在飞书上没有副本时文字挂在话题根下面（见「话题根补发」）；每张图一个幂等键 `b2f-img-<sha256(事件 id:序号)[:36]>`。发送前先落盘 pending。部分成功的事件下一轮只补缺的图；结果不确定（超时、`type: network`、没有消息 id）用同一个键、同一个接口重试，从首次尝试起 45 分钟内，之后记 `unknown` 并报告一次、永不重发；确定被拒或下载失败（本地磁盘写不了——临时目录建不出来、改名 / 写文件出 OSError——也算）记 `retry`，累计 3 次放弃并计入 `images_failed`（同样受 45 分钟限制），磁盘恢复后下一轮补上；未决的图把 Buzz 的读取起点往回拉，事件早已超出 900 秒窗口也读得到，事件读不到（被删）时 6 小时后关掉；`--skip-backlog` 丢弃积压时一并关掉悬着的图。

### 飞书 → Buzz

- **哪些**：人发的 image 消息（lark-cli 渲染成 `[Image: img_…]`）和富文本 post 里的图（`![Image](img_…)`），按 key 在一条消息里去重、最多 9 张。文件、音频、视频（含视频封面）不转发、不下载，仍按 lark-cli 渲染出来的文字镜像。
- **流程**：**先路由、再下载**——发信人认不出、被显式 `"skip"` 或发言人白名单（`feishu_sender_allowlist`）挡下、已删除、内容为空等一切被跳过的消息，不下载、不上传、不写账本、不建临时目录，图片只跟着「会被镜像」的消息走。默认 `feishu_unmapped_senders: "context"` 时，飞书已认证但映射不到频道成员的人的图片也走这条路径，不再特别丢弃。放行以后，用 owner 的 **user** 身份 `im +messages-resources-download --type image` 下载，每张放进 mkdtemp 的 0700 目录里一个空的子目录（那也是这次调用的 cwd）；然后按内容判断（魔数是 jpeg / png / gif / webp、≤ 10 MB，每个事件最多 9 张），**去掉元数据**（下面），写成脚本生成名字的 0600 文件，最后用镜像身份 `buzz messages send --file <路径>`（可重复）连同文字一起发出，一条飞书消息仍是一条 Buzz 事件。成员文字是「[飞书] 张三：…」，非成员是「[飞书·非成员] 张三：…」；去掉图片标记后没有文字时正文是「[图片]」，所以一张图都发不了时读的人也知道飞书那边有张图。
- **运行时边界：附件送达不等于模型已看到图片**。本节保证图片成为 Buzz 事件的 `imeta` 附件；截至已核实的 Buzz Desktop / `buzz-acp` 0.5.23，harness 只把事件正文和 tags 格式化成 ACP 的 `{type: "text"}` content block，没有下载 `imeta` 媒体、也没有生成 ACP image content block。因此真实 @agent 会唤醒它，但不能仅凭本同步脚本声称模型已拿到图片像素或完成视觉识别；那需要单独补 harness 的多模态组装并做真实运行态验收。这个限制对成员和非成员相同。
- **为什么要去元数据**：Buzz 的 relay 会拒绝带元数据的媒体（`422 media contains metadata`）。无损、不重新编码、纯 Python（不依赖 PIL）：
  | 格式 | 去掉什么 | 保留 |
  |---|---|---|
  | jpeg | APP1–APP15（EXIF、ICC、IPTC、XMP 等）、COM 注释、EOI 之后的多余字节 | JFIF（APP0）与其余段、扫描数据原样 |
  | png | iCCP、cICP、eXIf、pHYs、gAMA、iTXt / tEXt / zTXt、tIME 等所有辅助块、IEND 之后的字节 | IHDR、PLTE、tRNS、IDAT、IEND、APNG 动画块（acTL / fcTL / fdAT），块（含 CRC）原样 |
  | webp | ICCP、EXIF、XMP 块，VP8X 里宣告它们的标志位，RIFF 之后的字节 | VP8 / VP8L / VP8X / ALPH / ANIM / ANMF，RIFF 长度重算 |
  | gif | 注释、纯文本扩展、除动画循环（NETSCAPE2.0、ANIMEXTS1.0）以外的应用扩展（XMP 等）、结束符之后的字节 | 图形控制扩展与图像数据 |
  | bmp、tiff | 没法可靠去元数据，不转（`unsupported_format`） | — |
  结构不对（截断、长度越界、缺结束标记）一律不猜（`bad_image`）。去掉 ICC / cICP 以后广色域（如 Display P3）的图颜色会略有变化；CMYK jpeg 的 Adobe 标记（APP14）也会被去掉——这是过 relay 的代价。
- **不信飞书给的任何名字或路径**：`--output` 是脚本生成的相对名；lark-cli 会给它补一个按内容类型推断的扩展名，应答里的 `saved_path` 也不用——读的是那个子目录里**唯一**的文件；读之前拒绝符号链接（`unreadable`）；上传给 Buzz 的文件名也是生成的，扩展名按魔数。路径始终在临时目录里。
- **失败只影响那一张图**：不是图片 / 格式不支持 / 结构坏了 / 超限 / 符号链接按原因计数（`images_skipped`，一次）；下载出错（网络、超时、飞书报错）或本地磁盘写不了（临时目录建不出来、写上传文件出 OSError）计入 `images_failed`（需要关注，退出码 3）。文字和别的图**同一轮照发**，不等重试；只有一张图又失败时文字是占位「[图片]」。飞书 → Buzz 不重试失败的图：Buzz 的发送没有幂等键，事后补图只能是另一条消息，这里不做——`images_failed` 让退出码变成 3，人看到以后可以去飞书里手工补。计数在消息有结果（发出、结果不确定、或被拒三次放弃）时才记，被拒重试的中途不重复记。Buzz 发送本身的规则不变：结果不确定记 `unknown`、永不重发；确定被拒重试三次。

### 上限与前置

- 每张 ≤ 10 MB，格式按上表，每个事件 / 消息最多 9 张；飞书对图片的分辨率也有上限（数值以飞书开放平台文档为准），脚本不检查——超了飞书会拒绝，走上面的重试后计入 `images_failed`。
- 权限：发图的 bot 要有上传图片的权限（`im:resource`），owner 的 user 登录要能读群消息的资源；缺权限时飞书会拒绝（多半是 `permission` 类错误），走同样的重试后计入 `images_failed`。
- 镜像身份能下载的 Buzz 媒体就是它经 relay 的 Blossom GET 鉴权能读的：脚本只按事件里写的 sha256 取，权限由 relay 决定。
- 图片不是装饰：`images_failed` 会让退出码变成 3，`images_skipped` 只是计数。

## 配置（0600，owner-only，不含 secret）

```json
{
  "channel_id": "<uuid>",
  "chat_id": null,
  "owner_open_id": "ou_xxx",
  "owner_app_id": "cli_xxx",
  "mirror_pubkey": "<64 hex>",
  "mirror_env_file": "/abs/.config/buzz/agents/<mirror>.env",
  "people_api": {"base_url": "https://<bridge 的 BIND_PUBLIC_ORIGIN>", "signer_env_file": "/abs/.config/buzz/env"},
  "remove_extras": true,
  "identity": "union_id",
  "agents": {
    "<agent pubkey hex>": {"app_id": "cli_xxx", "lark_config_dir": "/abs/...", "lark_data_dir": "/abs/..."}
  },
  "buzz_cli": "/abs/.local/opt/buzz-0.5.23/usr/bin/buzz",
  "buzz_cli_sha256": "<sha256 of that binary>",
  "lark_cli": "/abs/.../bin/lark-cli"
}
```

- 键是严格校验的：上面列出的每一个键都必须有，不能多；只有 `reaction_map`、`identity`、`message_format`、`feishu_sender_allowlist`、`feishu_unmapped_senders` 和 `buzz_unmapped_senders` 可以不写。`chat_id` 由 `create-chat` 或 `bind` 写入，之前保持 `null`。旧版的 `people_export` 和 `email_domain` 已经取消，留着会被拒绝。
- `identity`（可选）：`"union_id"`（缺省）或 `"email"`，别的值（含大小写不同、空串、`null`）一律拒绝，不会悄悄退回默认。含义与前置条件见上文「人的身份怎么认」。
- `message_format`（可选）：`"card"`（缺省）或 `"text"`，别的值（含大小写不同、空串、`null`、别的类型）一律拒绝，不会悄悄退回默认。`text` 与卡片出现之前逐字节一致，是一键退回的办法。含义见下文「消息卡片」。
- `feishu_sender_allowlist`（可选）：Buzz pubkey 的列表，只有名单里的人在飞书里的发言才会镜像进 Buzz；不写就不限制（和以前逐字节一致）。非空、每项 64 位小写 hex、去重后最多 50 个，别的写法（不是列表、空列表、大写或长度不对、多于 50 个不同的）整份配置被拒，错误里只说键名、不带值。用途和语义见下文「只让特定的人的发言进 Buzz」。
- `feishu_unmapped_senders`（可选）：`"context"`（缺省）或 `"skip"`，别的值（含大小写不同、带空白、空串、`null`、别的类型）整份配置被拒，不会悄悄退回默认，错误信息说明键名和可选值、不带出值。没有这个键时，映射不到的人的发言以「仅上下文」镜像进 Buzz；`"skip"` 显式关闭。它与 `feishu_sender_allowlist` 互斥：显式同时写 `"context"` 与白名单时整份配置被拒；存量配置只写了白名单而没写本键时，白名单的明确限制意图优先，隐式为 `"skip"`。语义和风险见「非成员的发言（仅上下文镜像）」。
- `buzz_unmapped_senders`（可选）：`"skip"`（缺省）或 `"context"`，校验规则与 `feishu_unmapped_senders` 相同（错误信息各自独立，不共用）。它是反方向：`"context"` 让既不是验证过的频道人类成员、也不是配置的 agent 的 Buzz 作者的消息，以「仅上下文」镜像进飞书。两个方向互相独立，可以只开一个、也可以同时开；不与 `feishu_sender_allowlist` 互斥（那份名单管的是飞书 → Buzz 的方向，不影响这边）。语义和风险见「非成员/非 agent 的 Buzz 消息（仅上下文镜像）」。
- `reaction_map`（可选）：`{"🎉": "Party"}`。键是 Buzz 的 emoji（非空、不超过 16 个字符，变体选择符会被去掉，带不带 U+FE0F 一样），值是飞书 emoji_type（只含字母、数字、下划线，不超过 40 位，取值见 lark-cli 的 `lark-im-reactions.md`）。追加或覆盖上面的默认表，写错就整个配置被拒绝。
- `people_api` 恰好是 `{base_url, signer_env_file}`：
  - `base_url` 必须恰好是 `https://主机[:端口]`（没有路径、查询、userinfo 和结尾斜杠）。原因是签名里的 URL 是这个 origin 加请求目标，bridge 用它自己配置的 `BIND_PUBLIC_ORIGIN` 来核对，两边差一个字符都会验不过。
  - `signer_env_file` 是 0600 文件，只读其中的 `BUZZ_PRIVATE_KEY`（hex 或 nsec）。
- 每个 agent 必须有自己的应用和 profile：两个 agent 登记同一个 `app_id`、同一个 `lark_config_dir` 或同一个 `lark_data_dir` 时，配置会被拒绝。
- 所有路径都必须是绝对路径。
  - `buzz_cli` 必须是 `buzz-0.5.23` 发行目录里真正的 ELF 二进制，不能是符号链接，sha256 必须等于 `buzz_cli_sha256`（校验复用 `gitlab_buzz_sync.validate_buzz_cli_path`）。**不能**用 `~/.local/bin/buzz` 这个包装器，它会加载 owner 的 key。
  - `lark_cli` 的文件名必须是 `lark-cli`。
- `mirror_env_file` 是 0600 文件，只读取其中的 `BUZZ_PRIVATE_KEY`、`BUZZ_RELAY_URL` 和 `BUZZ_AUTH_TAG`。
- 子进程的环境变量走白名单：`HOME`、`PATH`、`LANG`、`LC_ALL`、`TZ`、`XDG_CONFIG_HOME`、`XDG_DATA_HOME`，再加上显式追加的变量。
  - 不传 D-Bus 和运行时目录，这样 lark-cli 只会用每个 profile 自己的文件 keychain。
  - secret 不进 argv，也不进 stdout。

## state（0600，不含正文和 secret，没有明文邮箱，也没有「谁是谁」的对应关系）

state 目录必须是本人所有、不是符号链接、组和其他人都无法访问（0700，脚本创建时就是这个权限）。目录里有两个文件：
- `round.lock`：以 0600 创建，打开时不跟随符号链接。
- `state.json`：保存下表这些字段。

两者任何一项不满足，整轮都拒绝运行。`state.json` 必须字段齐全，缺字段、多字段或类型不对时同样整轮拒绝：缺字段的 state 会被当成「什么都还没发」，从而重发最近的消息。唯一的例外是升级：只缺 `r2f` 和 `react_since`（表情同步之前写的 state）、只缺 `idmap` 和 `emailmap`（身份缓存之前写的 state）、或只缺 `images` 和 `img_unresolved`（图片同步之前写的 state：之前没有发过任何图，账本从空开始）时照常读取：`r2f` 从空开始，`react_since` 从 `buzz_since` 起算，所以升级不会把历史 reaction 补发一遍；两个身份缓存从空开始，按需补上（它们只省飞书调用）。只缺其中一个字段不算升级，照样拒绝。

| 字段 | 内容 |
|---|---|
| `binding`、`floor` | 绑定的 `channel|chat`，以及绑定开始的时间 |
| `buzz_since`、`feishu_since` | 两个方向的游标 |
| `buzz_floor`、`feishu_floor` | 用 `--skip-backlog` 丢弃积压的时间点，更早的不再读取 |
| `b2f`、`f2b` | 两个方向的消息 id 映射，值可以是对方的消息 id、`pending:<时间>`、`retry:<时间>`、`failed` 或 `unknown`。Buzz → 飞书的 `pending` / `retry` 后面还记着首次尝试时回复的那条飞书消息（没有就是 `-`），再后面是首次尝试的发送方式：`,card`（首次是卡片，如 `pending:<时间>:-,card`）、`,text`（卡片被拒后改发的文字，如 `retry:<时间>:-,text`，之后只重试文字），没有记号就是普通文字（文字模式、旧版本写的 state）；重试原样重复首次尝试，切换 `message_format` 不改。卡片没有新增任何 state 字段 |
| `attempts` | 每条消息被确定拒绝的次数；表情的重试次数也在这里，键是 `r2f:<reaction 事件 id>`（打）和 `r2f-del:<reaction 事件 id>`（删），成功或放弃后清掉 |
| `r2f` | Buzz 上 reaction 事件 id → `作者 pubkey\|飞书消息 id\|emoji_type\|reaction_id`（已打上），或终态 `failed`（打不上，放弃）、`removed`（已撤销、不再处理） |
| `react_since` | reaction 的读取游标（kind 7 和 5）。单独一个，因为一轮被单轮上限截住时只有它不前进 |
| `idmap` | union_id 模式：本应用 open_id → union_id，来自飞书自己对同一条消息给出的两种 id（按 `mentions[].key` 配对），只有 id；格式不对的整个 state 被拒。open_id 按应用隔离，所以换了 owner 应用后旧应用留下的条目不会被新应用的任何 id 命中，只是占位、最终被裁掉 |
| `emailmap` | email 模式：`sha256(owner 应用 id + NUL + 邮箱)` → open_id，或 `miss:<时间>`（飞书里找不到，过了 `EMAIL_MISS_RECHECK_SECONDS` 才再查）；不落明文邮箱。这个摘要只是查找用的键、让明文不出现在 state 里，**不是保密措施**：邮箱的取值空间小，知道候选邮箱的人能验证；真正的保护是 state 目录和文件只有本人可读（0700 / 0600，同一个 UID 下本来就读得到 owner 的 key 和 lark-cli 的 token） |
| `images`、`img_unresolved` | Buzz → 飞书的图片账本（键 `<事件 id>:<序号>`；超过 9 张的部分记在 `<事件 id>:over`）：飞书消息 id / `pending:<首次时间>` / `retry:<首次时间>` / `failed` / `unknown` / `skipped`，另有 `<事件 id>:thread`（文字发在哪里：话题根的消息 id 或 `-`，图片跟着它走）；以及等待重试或结果未知的图 → 事件时间，用来把读取窗口往前拉。`attempts` 里图片的重试次数键是 `b2f-img:<事件 id>:<序号>`（Buzz → 飞书），结果出来就清掉 |
| `unresolved`、`f_unresolved` | 等待重试或结果未知的项目 → 原始时间，用来把读取窗口往前拉。Buzz → 飞书方向还包括等话题根的回复（`b2f` 里没有它，只有这里；根发出去、或取话题重试用完、或根被放弃之后才发出）和补发的根（按补发的时间计） |
| `threads`、`polled`、`tried` | 飞书话题根消息 id → 最近活跃时间、上次完整轮询的时间（该话题自己的游标）、上次失败或没读完的时间（决定轮换顺序） |

state 是有界的：id 映射（含 `r2f` 与 `images`）最多保留 20000 条（`images` 里还有未决项的图片，连同同一事件的 `:thread` / `:over` 记录不裁：它的 45 分钟窗口和重试次数就在账本值里，裁的是最早的、已经有结果的），话题最多保留 200 个，`idmap` 和 `emailmap` 各最多 5000 条（丢的是最早写入的，丢了只是多查一次）。state 文件损坏、字段类型不对或多出字段时整轮拒绝，不会当成空状态，否则会把所有消息重发一遍。

## systemd --user 示例

两个坑要先知道（2026-09-20 naturehood 上线时踩到，#110）：

- **脚本要用不可变拷贝，不要指到工作树或临时目录**：定时器每几分钟就跑一次，工作树一切分支、临时目录一清就断。把要跑的版本 `git archive` 到一个只读目录（例如 `~/.local/share/buzz-agent-setup/releases/feishu-group-sync-<短 sha>/`，`chmod -R a-w`），单元指向它；升级时换一个新目录再改单元，不覆盖旧的。脚本自己会去找同目录下的 `references/scripts/`，所以要拷整个 `skills/buzz-agent-setup`，不能只拷一个 `.py`。
- **`PATH` 必须带上 lark-cli 的 node 目录**：`lark-cli` 是 node 脚本（`#!/usr/bin/env node`），systemd 用户单元的默认 `PATH` 里没有 nvm 的目录，缺了它每一次飞书调用都会失败。脚本只把 `PATH` 等白名单变量传给子进程，所以要在单元里 `Environment=PATH=…` 写明。

```ini
# ~/.config/systemd/user/buzz-feishu-<channel>.service
[Unit]
After=network-online.target

[Service]
Type=oneshot
Environment=PATH=%h/.nvm/versions/node/<版本>/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 %h/.local/share/buzz-agent-setup/releases/feishu-group-sync-<短 sha>/skills/buzz-agent-setup/scripts/buzz_feishu_group_sync.py round --config %h/.config/buzz-feishu-sync/<channel>/config.json --state-dir %h/.config/buzz-feishu-sync/<channel>/state
# 3 = 本轮完成但需要关注（比如有人映射不到），不算服务失败
SuccessExitStatus=3
TimeoutStartSec=10min
Nice=5

# ~/.config/systemd/user/buzz-feishu-<channel>.timer
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=15s
[Install]
WantedBy=timers.target
```

**同步间隔不要超过 120 秒**（重读窗口）：开着 `feishu_unmapped_senders: "context"` 时，陌生人的话要等窗口，间隔更长的话第一次读到时年龄已经超过窗口、当轮就发，暂时映射不到的成员的发言就得不到自愈（见「非成员的发言（仅上下文镜像）」）。

装好后 `systemctl --user daemon-reload && systemctl --user enable --now buzz-feishu-<channel>.timer`，再 `systemctl --user start buzz-feishu-<channel>.service` 手动跑一轮，用 `journalctl --user -u buzz-feishu-<channel>.service` 看每轮的报告（只有计数，没有邮箱或 id）。验收时可以把间隔临时改成 2 分钟，验完改回 5 分钟。

## 首轮之后怎么验收

首轮（`round` 第一次跑）会**真的改群**：把映射得到的同事拉进飞书群、把 agent 的 bot 拉进群，并给被拉的人发入群通知。所以先用只读方式预览再让频道 owner 确认：要拉几个人、几个 bot、多少人映射不到（没有绑定）、群里原有的外人会不会被移出（`remove_extras`）。确认后再跑，然后按下面四步验收：

1. **飞书 → Buzz**：在飞书群里 @ 一个 agent 说一句话。下一轮之后，Buzz 频道里出现一条 `[飞书] <你的名字>：…`，发信人是镜像身份、被 @ 的是那个 agent；agent 的回复会由它自己的 bot 作为**话题回复**挂在你那条飞书消息下（主列表里看不到，要点开话题）。
2. **reaction**：agent 收到请求会在 Buzz 上打 👀、💬，它们会在那条飞书消息上变成 `GLANCE`、`Typing`；agent 处理完撤销后飞书上的表情随之消失。表情往往只存在几分钟，同步间隔长的话肉眼容易错过——用报告里的 `reactions_added` / `reactions_removed`（`reactions_failed` 应为 0）核对。
3. **Buzz → 飞书**：在 Buzz 频道里说一句话（再 @ 一个人试试）。下一轮之后，飞书群里出现一张卡片：标题是这句话的第一行、下面一行灰字「你的名字 · #频道名」，比标题多出内容的话有收起的「展开全文」，没有底部按钮；点灰字旁边的「在 Buzz 中打开」会在浏览器里打开一个 https 页面并唤起 Buzz Desktop。报告里 `cards_sent` 加一、`cards_fallback_text` 应为 0（不是 0 就说明飞书拒绝了卡片，看下文「飞书拒绝卡片时」）。
4. **报告**：`unmapped_members` 等于频道里没有绑定的人数、`identity_conflicts` 为 0、`errors` / `failed` / `unknown` 为 0。

几个上线后会看到的现象，不是故障：
- **频道里其他机器人发的消息也会镜像进飞书群**：比如 GitLab 通知（流水线通过、评论、状态变更）是用 agent 的身份发到频道的，会由该 agent 的 bot 转到飞书群，量大时群里会比较吵；不想要就不要把该频道绑到飞书群，或者等过滤功能。
- **`remove_extras: false` 时，群里原有的、不在频道映射里的人会一直留着并看到频道消息**（预检的 `extras_stay_and_see_channel_messages`）。私有频道要收紧就设 `true`（会移出他们，也是对真人可见的动作，先确认）。
- 没有绑定的成员拉不进群、也收不到 @ 通知：让他们到 bridge 的绑定页绑定飞书账号，下一轮就会被拉进群。

## 只让特定的人的发言进 Buzz（个人频道 / 只认 owner 的 agent）

**用途。**个人 agent 通常以 owner 的完整权限在本机运行，只认 owner 的公钥（`BUZZ_ACP_RESPOND_TO` 不含镜像身份）。想在飞书里指挥它，前提是「只有 owner 本人的飞书发言才会进 Buzz」。默认情况下，任何已绑定、在频道里的人在群里说的话都会由镜像身份发进频道，所以要在脚本层加一道硬保证：即使以后有别人被拉进这个飞书群，他们的发言也不能进 Buzz。

**配置**：可选键 `feishu_sender_allowlist`，写在配置文件里（其余键的格式见上文「配置」）：

```json
"feishu_sender_allowlist": ["<owner 的 Buzz pubkey，64 位小写 hex>"]
```

**语义**

- **格式**：非空列表，每项是 64 位小写 hex 的 Buzz pubkey，去重后最多 50 个（同一个 pubkey 写两遍不会让名单变宽）；写错——不是列表、空列表、大写、长度不对、超过 50 个——整份配置被拒，脚本不会悄悄退回「不限制」。
- **缺省不限制**：不写这个键，行为和以前逐字节一致（已映射、在频道里的人的发言都镜像）。写了就是白名单：名单外的人一律不镜像。名单是每一轮从配置里重新读的，收窄以后下一轮就生效；已经在重试里的、发信人被移出名单的消息，下一轮按 `sender_not_allowed` 关闭，不再重发。
- **先判身份，再判白名单，两者都要满足**：发信人先要认得出来（映射得上：`unmapped_sender`、`sender_unpaired`、`identity_conflict` 等原因照旧）、在频道里（`sender_not_in_channel`），然后才看名单。名单不能替谁作身份担保——名单里的人如果映射不到，他的发言照样不镜像。
- **被挡下的消息**：不发送、不写任何账本（这条消息在 `f2b`、`f_unresolved`、`attempts` 里都没有记录；只有上面说的、名单收窄之前就已经在重试里的消息，会被记为放弃），报告 `skipped` 里 `sender_not_allowed` 计一次。这是计数，不触发「需要关注」（退出码不变）。消息在 120 秒的重读窗口里会被再读到，所以同一条可能被计数不止一次。它也不会在之后补发，哪怕名单后来放宽了。
- **话题里的回复、带 @ 的消息走同一条路径**，绕不开名单。
- **@ 提及的对象不受名单限制**：名单只管「谁的发言能进 Buzz」，不管「谁能被 @」。名单里的人在飞书里 @ 了别的同事或 agent，p tag 照常产生。
- **只管飞书 → Buzz**。Buzz → 飞书、reaction 同步、成员对账、署名格式（`[飞书] 名字：`）都不受影响，state 也没有新字段。报告和错误信息里没有 pubkey 全值，也没有被挡下那条消息的正文。

**和「不要把镜像身份加进只认 owner 的 agent 的名单」的关系。**那条规则（见「一次问齐这些问题」第 5 条）仍然有效，白名单不是放开它的理由。因为镜像身份发的消息署名是「[飞书] 名字：」，agent 认的是镜像身份的公钥而不是名字，所以只要镜像身份进了这个 agent 的名单，凡是进得了 Buzz 的飞书发言，agent 都会当成命令。有了白名单，这个风险只是**缩小**到「名单里那几个人的飞书账号」（谁拿到这个账号，谁就能指挥它），**不是消除**：名单里的人自己的飞书账号被盗、被人借用，或者他在群里转发了别人的话，仍然会进到 Buzz。所以：

- 名单只放 owner 自己（一个人），不要放不需要的人；
- 同时在飞书里把这个群的「谁可以添加群成员」设成**仅群主**，群里不加别人（`preflight --mode existing` 的 `cannot_add_members` 只是在检查 owner 自己能不能加人，不能代替这个设置）；`remove_extras: true` 也让脚本每一轮把频道外的人移出去，但那是事后补救，不是事前保证；
- 这条通道的命令权限最终由 agent 那一侧的配置决定，脚本的白名单只是其中一道。

**个人频道同时跑着 GitLab todo 同步（ADR-0013）时**：镜像身份是频道里的 bot 成员，而 `gitlab_todo_sync.py` 每轮先做成员闸门——频道里出现「owner、todo 发布者、`done_authors`」之外的成员就在读取任何 todo 之前 fail-closed（错误信息 `personal Channel has a member outside the owner, the publisher and the trusted done authors`，服务失败、todo 停止投递）。所以给个人频道加镜像身份的同一步，要把镜像身份的公钥加进 todo 配置（`~/.config/buzz/todo/<名>.json`，0600）的 `done_authors`，然后 `systemctl --user reset-failed` 并手动启动一次该服务确认 `"status":"ok"`。这不放宽实际权限：镜像发的消息以「[飞书] 名字：」开头，不是 `todo:done:<id>` 回复；能进 Buzz 的只有白名单里的 owner；闸门要求的「频道里恰好一个人类成员」仍由 owner 一个人满足。反过来，不设白名单的群绝不要这样做。

## 非成员的发言（仅上下文镜像）

**用途。**业务群里常有一大批人不是频道成员（没有 Buzz 账号，或还没绑定飞书账号）。他们对 agent 报告的澄清、图片和真实 @agent 都应该进 Buzz，让 agent 看得到并响应，不要求他们先成为频道成员。因为飞书与 Buzz 都是员工认证系统，这条能力自 2026-09-23 起默认开启；但员工身份认证不等于给这条消息额外的 Agent 执行权限。决定的来龙去脉见 ADR-0016。

**配置**：可选键 `feishu_unmapped_senders`，写在配置文件里（其余键的格式见上文「配置」）：

```json
"feishu_unmapped_senders": "context"
```

**语义**

- **取值**：`"context"`（缺省）——映射不到的人的发言由镜像身份发进 Buzz；`"skip"`——显式关闭，发言不镜像并计 `unmapped_sender`。别的值整份配置被拒，脚本不会悄悄退回另一个值。这个键每一轮从配置里重新读，改了下一轮就生效。已配 `feishu_sender_allowlist` 而未写本键时隐式为 `"skip"`；显式把白名单和 `"context"` 同时写入会被拒。
- **署名**：带 `[飞书·非成员]` 标签，整条是 `[飞书·非成员] 姓名：正文`（成员仍是 `[飞书] 姓名：正文`），这个标签是 agent 和读的人区分两者的依据，规则要认标签，不能只看名字——名字是他自己在飞书里设的，可能和成员同名。名字用和成员一样的清洗（没有格式字符、换行、`@`、`nostr:`），清洗后是空的写「飞书用户」。正文后面的行如果长得像我们的署名——`[飞书] …`、`[飞书·非成员] …`，Markdown 转义、加粗、引用、行内代码、列表编号（`1. `）、任务列表框（`- [ ] `）、项目符号 / 箭头 / 竖线 / emoji、标题记号打头也算，全角括号、`【】`、字间空格、别的中点写法、看不见的字符也算——前面加「↳ 」标记。陌生人的正文用这个更宽的判断；成员的正文仍是原来的、逐字节不变（见上文「正文先去掉双向控制符……」）。
- **只是上下文，不是命令**：
  - **能 @ 到 agent，不能 @ 到人**（PO 决定，2026-09-22）：正文里手打的 `@名字`、`nostr:npub…` 和成员的一样被中和（全角＠），Buzz CLI 也解析不出 @；飞书消息里选中的 @ 只有落在**频道里配置了飞书应用的 bot**上才转成真正的 mention（事件带 p tag）——匹配的是 `bot_member_to_pubkey`，和成员路径同一份表、同一个 id 空间，不需要额外的飞书调用；选中的是**人**（哪怕是频道成员）一律丢弃，不产生任何 mention。同一个 agent 被 @ 多次只算一次，@ 多个 agent 各算一次，@ 到没配飞书应用的 agent 静默丢弃、不报错。团队 Channel 的普通 agent 按 runtime-setup 设为 `BUZZ_ACP_RESPOND_TO=anyone`，这个真实 p tag 会建立 turn 并让它响应；`owner-only` 或未包含镜像身份的 allowlist agent 会被自己的作者门禁挡下。`--reply-to` 不会自动给上级消息的作者加 p tag（用真实事件核过），所以话题回复里没被 @ 的人不会被牵连唤醒。
  - **图片和成员走同一条路径**：保留图片 key，用 owner 的飞书 user 身份下载，校验 jpeg / png / gif / webp 魔数与 10 MB 上限，去掉 EXIF / ICC 等元数据，每个事件最多 9 张，再以镜像身份用 `--file` 发进 Buzz；任一张失败只影响那一张（见「图片同步」）。这保证附件进入 Buzz，不代表当前 `buzz-acp` 已把图片像素放进模型输入；视觉识别的 harness 缺口见「图片同步」的运行时边界。
  - **姓名和正文有长度上限**：姓名是他自己在飞书里设的，最多 60 个字符（`CONTEXT_NAME_LIMIT`）；孤立的代理项字符（编码不出来，会让 Buzz CLI 的调用抛异常）换成 U+FFFD。陌生人的正文最多 4000 个字符（`CONTEXT_BODY_LIMIT`），超过的截断并注明原文多少字——Buzz CLI 对一条消息有字节上限，超了会被当成「确定被拒」、重试三次记 `failed`、触发「需要关注」，陌生人不该能做到这一点。成员的正文不动。
  - agent 读到它时**只能当不可信数据**：可以作为证据（「业务方说这是两类人群的通知」），但不是指令，也不能替代 owner 或频道成员的决定。
- **「映射不到」就是「非成员」**：脚本只知道当前频道成员的绑定（bridge 只返回现存成员），所以被移出频道的人、还没绑定飞书账号的成员、绑定有问题的成员（union_id 还没回填、email 模式下地址查询出错或搜到不同的账号、本轮查询预算用完、「没找到」的十分钟复查期内）和真正的陌生人在一轮里分不出来，发言都按 `[飞书·非成员]` 镜像——最低信任的标签，内容不丢；这些情形自己的计数（`unmapped_members`、`errors`、`identity_conflicts`）照旧报告、照旧需要关注。纯函数里的 `sender_not_in_channel` 在真实的一轮里到不了。
- **仍然跳过的**：发信人 id 不是飞书的人（空、乱码、不是 `ou_…`），或 union 模式下飞书没有担保他是谁（`sender_unpaired`）；缓存和新证据矛盾（`identity_conflict`）；**已知是某位成员的账号、只是分不清是谁**——两个 pubkey 绑了同一个飞书账号、一位成员的两个邮箱指向两个账号——也是 `identity_conflict`，不当成陌生人、也不归给谁；已删除、系统消息、app（bot）发的、空内容照旧跳过。
- **查身份出错照旧重试**：查身份的那次飞书调用本身出错，不知道发信人是谁就不能当「非成员」发出去，按「确定没发出」处理——记一次 `errors`、留在重试里，下一轮成功后镜像一次且只有一次。
- **要等一会儿才发**：陌生人的话要等它比重读窗口（120 秒）更老才发，等待期间不写账本、不计数（所以晚两三分钟出现在 Buzz 里）。原因是自愈：暂时映射不到的成员（bridge 的绑定还没出现、地址搜索失败、union_id 未回填）下一轮映射恢复了，就按成员发出——署名 `[飞书] 名字：`、@ 的 agent 照常产生 p tag，和 `"skip"` 一直以来的自愈一样；一发出就定死的话，这条 @ 会永久丢失。每条话最后一次被读到时年龄必然大于窗口，所以一定会发（实际等待约 1 到 2 分钟：飞书的 `create_time` 只到分钟）。**自愈的前提是同步间隔不超过 120 秒**（缺省示例每分钟一轮）：间隔更长时，第一次读到时年龄已经超过窗口，陌生人的话当轮就发。
- **保持顺序**：等待期间，成员那条会叫醒 agent 的消息（带 @）如果晚于一句还在等待的陌生人的话，也一起等，根还在等待的话题里成员的回复也等——先出现陌生人的话、再出现叫醒 agent 的那条，回复挂在根下面；没有 @ 的成员消息、比陌生人的话更早的成员 @ 不等（最多晚 1 到 2 分钟，不丢不重）。不这样的话，agent 被叫醒时读不到那条 @ 所指的话。
- **与 `feishu_sender_allowlist` 互斥**：名单的用途是把别人的话挡在 Buzz 外，两者并用自相矛盾，显式同时写 `"context"` 与名单时整份配置被拒；只写了名单的存量配置隐式为 `"skip"`，函数层面也失败即关闭（有名单时映射不到的人仍是 `unmapped_sender`）。
- **其余和成员发言一样**：话题里的回复挂在根消息对应的 Buzz 事件下面；发送、账本、重试与 `unknown` / `failed` 语义是同一条路径；积压超过 10 分钟的消息注明飞书上的原始时间；镜像进 Buzz 的消息不会被同步回飞书；Buzz → 飞书、reaction、成员对账都不受影响。
- **计数**：生效模式为 `"context"` 时报告多一个 `context_to_buzz`（`to_buzz` 里属于非成员的那部分，没人说话是 0）；显式关闭或因发言人白名单隐式关闭时，报告里没有这个键，形状和以前一样。这是计数，不触发「需要关注」。报告和 state 里没有姓名和正文。
- **不追溯补发**：以前在 `"skip"` 下跳过的发言，之后改成 `"context"` 也不补发（消息在 120 秒的重读窗口里会被再读到，所以恰好在切换前后一两轮里的消息可能被镜像）；之后再关，已经镜像进 Buzz 的留在那里；关的时候还在重试里的消息被放弃（计一次 `failed`，需要关注，和发言人白名单收窄时一样）。

**风险**（默认开启；owner 上线或保留默认值前要知道）

- 群里任何人的话——包括你不认识的人、被别人邀请进来的人——都会出现在**私有频道**里，被频道里的成员和所有 agent 读到。
- **群里任何人都能唤醒 agent**（PO 决定，2026-09-22）：这是本节放宽的关键一点，之前的版本是「没有 @、不唤醒」，现在改成「能 @ 到 agent，不能 @ 到人」——群里的 44 个人（以及以后被别人拉进来、owner 不认识的人）都能直接 @ 一个 agent 让它开始处理，不需要 Buzz 账号、不需要在绑定页验证身份、不需要 owner 批准。这不是"读上下文"的副作用，是**故意放开的能力**。
  - **没有放开的部分**：唤醒不等于授权。agent 被唤醒后，飞书正文仍然是**不可信数据**（和任何频道消息一样）：agent 的既有规则要求不执行消息里的指令、`/approve` 只认注册的 pubkey、GitLab 写操作要先落 Issue 走 `gitlab-issue-sop`、高影响动作要走 ACT 审批。这些防线是 agent 本来就有的，不是为这个功能新增的——如果某个 agent 的 prompt 里写了「被 @ 就照办」或没有做输入校验，现在任何群成员都能触发它，这条 gap 比以前更容易被撞到。
  - **谁能进这个群，owner 就该管起来**：群主是谁、"谁可以添加群成员"设成什么，直接决定了谁能唤醒 agent；这条边界现在完全落在飞书群的成员管理上，Buzz 频道成员管理管不到它。
- 群里的人可以在这些话里放提示词注入（「忽略之前的规则……」），现在他们还能直接 @ 一个 agent 把注入内容送到它面前。agent 的规则里已经把频道消息当不可信数据；给读这类消息的 agent 写规则时，别写「镜像身份发的消息都照办」，也别写「被 @ 就一定是可信请求」。
- 量：群里的每一条非成员发言都会进频道一条，**没有每轮上限**（每条一次子进程、一次 state 写盘）：群里有人刷屏，频道里也会被刷屏，还会连带唤醒 agent 处理一堆消息——发现后关掉开关。群很吵的话，先看 `context_to_buzz` 的量再决定。
- 频道里如果有 `message_posted` 类的 Workflow：非成员的发言也是频道消息，会触发它。上线或保留默认值前检查，别让它对任意消息都动作。
- 「唤醒」靠的是事件带 p tag：`--reply-to` 不会自动给上级消息的作者加 p tag（用真实事件核过），所以话题回复里没被 @ 的人不会被牵连唤醒；harness 是否另有基于话题的唤醒，没有专门做过实验。

**「为什么某人的话没进 Buzz」／「为什么 @ 了 agent 没反应」怎么排查**

1. 看该轮报告的 `skipped`：`unmapped_sender` 是这个人不是频道成员、或还没绑定飞书账号（生效模式为 `"context"` 时，这类人的话不再出现在这里，而是晚两三分钟带 `[飞书·非成员]` 标签出现在频道里）；`sender_unpaired` / `identity_conflict` 是飞书没能确认他是谁；`sender_not_in_channel` 是绑定过但已不在频道；`sender_not_allowed` 是被发言人白名单挡下。
2. 非成员 @ 了一个 agent 却没反应：先看 @ 的对象是不是**人**——@ 到人（哪怕是频道成员）不生效，这是设计使然，不是故障；再看这个 agent 有没有配置飞书应用（`bot_member_to_pubkey` 里没有它就静默丢弃）；最后核对 agent 的作者门禁。团队 Channel 的普通 agent 应是 `BUZZ_ACP_RESPOND_TO=anyone`；`owner-only` 不接受镜像身份，allowlist 只有显式包含镜像身份才接受，而这类受限 agent 不应为了飞书 @ 临时放宽。
3. 想让非成员本人被别人 @ 到（不只是让他读上下文或 @ agent）：让他成为频道成员并在绑定页绑定飞书账号；本节的默认能力只解决「读上下文」和「@ 到 agent」，不能让非成员被别人 @ 到。

## 非成员/非 agent 的 Buzz 消息（仅上下文镜像）

**用途。**Buzz → 飞书方向只放行「验证过的频道人类成员」或「配置好的 agent」，其余作者一律 `not_channel_human`，直接丢弃——即便事件签名完全
有效。一个具体的坏后果：某条 Thread 的根消息由这样一个作者发出（比如一个 Workflow 自己的签名身份，没有 kind:0 profile），根消息一丢，
`_thread_root_parent` 就找不到可挂的父消息，同一 Thread 之后所有回复都只能退回顶层发出——整条 Thread 在飞书里断链。想让这类作者的话也镜像
进飞书，就开这个开关。决定的来龙去脉见 ADR-0017（它沿用 ADR-0016 的 @ 规则，两者是同一个决定的两个方向）。

**配置**：可选键 `buzz_unmapped_senders`，写在配置文件里（其余键的格式见上文「配置」）：

```json
"buzz_unmapped_senders": "context"
```

**语义**

- **取值**：`"skip"`（缺省）——和以前逐字节一致：这样的作者 `not_channel_human`，不镜像；`"context"`——由 owner 应用 bot 发出，只作为读的
  上下文。别的值整份配置被拒。这个键每一轮从配置里重新读，改了下一轮就生效。
- **门槛比飞书那边低**：Buzz 的每一个事件都已经过 relay 的签名校验，作者是谁没有歧义，不需要「平台是否担保这个人」那一层判断（飞书那边的
  `sender_unpaired` / `identity_conflict` 在这边没有对应物）；也不要求能解出 profile 显示名——解不出（比如 Workflow 的签名身份没有 kind:0）
  退回公钥前 12 位，跟既有的人类分支一致。
- **署名**：沿用既有的「名字（Buzz）：」格式，插入「·非成员」区分：`名字（Buzz·非成员）：正文`（成员仍是 `名字（Buzz）：正文`）；卡片模式下
  speaker 显示为「名字（非成员）」，蓝色模板（不是 agent 的绿色模板）。
- **能 @ 到 agent，不能 @ 到人**（与飞书那边的 `feishu_unmapped_senders: "context"` 同一条规则）：正文里 @ 到频道自己配置的、当下有飞书 bot
  的 agent，生成真的 `<at>`（能唤醒它，卡片、文本两条发送路径都生效）；@ 到任何人类成员、或没有飞书 bot 的 agent，一律静默丢弃——不生成
  `<at>`，也不留纯文字 `@名字`：正文（和卡片标题/折叠全文，因为它们是同一段文字）里能解出显示名的这类目标，字面的 `@名字` 会被替换成全角
  `＠名字`（比成员分支更严格：成员分支里映射不到的目标至少留着原始文字，这里连原始文字都不留）；解不出显示名的目标没有字面文字可找，
  正文原样保留，`<at>` 仍然不生成。
- `agent_bot_unavailable`（配置了但当下没有飞书 bot 的 agent）是一个更具体的已知情形，不受这个开关影响，两种模式下都照旧丢弃，不会被
  当成「非成员」镜像。**本机根本没配**的频道 agent 另有开关，见下。

- **话题、账本、重试都不用改**：根消息一旦被镜像，`_thread_root_parent` 自然找得到父消息，回复正常挂上去；去重账本、重试与 unknown 语义、
  飞书 → Buzz 方向都和成员发言一致。
- **报告不新增字段**：镜像的非成员消息计入既有的 `to_feishu`，和成员消息一样计数；这里没有 `feishu_unmapped_senders` 那边的 `context_to_buzz`
  ——Buzz → 飞书这个方向本来就没有「按人群细分计数」的既有约定，不为了对称硬加一个。
- **不追溯补发**：以前因 `not_channel_human` 丢弃的消息，之后再开也不补发（缺省的 `--reply-to`/账本机制不回填历史）。
- **与 `feishu_sender_allowlist` 无关**：那份名单管的是飞书 → Buzz 方向谁的话能进 Buzz，跟这个方向没有交集，两者可以任意组合。

**风险**（owner 决定开之前要知道）

- **任何签名有效的 Buzz 账号都能把话发进这个飞书群**——不需要是频道成员、不需要 owner 批准；这条边界完全落在「谁能给这个 Buzz 频道发消息」上。
- **非成员/非 agent 的 Buzz 作者也能唤醒频道里的 agent**（真实 `<at>`）：唤醒不等于授权，agent 既有的「频道消息是不可信数据」「`/approve`
  只认注册的 pubkey」等规则照常适用，不是这里新增的防线，但攻击面比只有验证成员/agent 能唤醒时更大。
- 署名只靠「·非成员」标签区分，不靠名字；读这类消息的人和别的自动化如果只认名字、不认标签，会把非成员的话当成成员说的。

**「为什么某条消息没转发到飞书」怎么排查**：先看报告的 `skipped.not_channel_human`——开着本节的开关时这个数字应该是 0（那样的作者改走
`to_feishu`）；如果开了还有 `not_channel_human`，多半是撞上了 `agent_bot_unavailable`（配置了但没有飞书 bot 的 agent，这个开关管不到）。

## 本机没有凭据的 agent（镜像代发）

配置 `buzz_unmanaged_agents`（可选）：`"skip"`（缺省，与以前逐字节一致）或 `"relay"`。需求 engineering/skills#143。

**为什么需要**：一个 agent 可以同时属于多个 Channel（M:N），而每个 Channel 的群同步跑在**不同的人**的机器上。agent 的飞书应用凭据只能留在它
自己 owner 的本机（SKILL.md Rule 11 不允许复制给别人），所以别的操作者**永远**没有它的发送能力。缺省行为是把这种消息丢掉
（`agent_bot_unavailable`），结果是群里看不到这个 agent 的任何回复——2026-09-22 `nh-dev` 被拉进别人运行同步的频道时就是这样。

- **只覆盖「本机根本没配」的 agent**：`agents_in_channel()` 给的是频道里所有 `role=bot` 的成员，不看本地配置；只有**不在** `agents` 里的那些
  才代发。配置里有、只是这一轮没验证过或 bot 还没进群的 agent 仍然 `agent_bot_unavailable`：那是暂时且会自愈的状态（下一轮 reconcile 把它的
  bot 拉进群就恢复），代发会让同一个 agent 一会儿自己说话、一会儿被转述。
- **谁来发、怎么署名**：由 owner 的个人应用 bot 代发，沿用既有格式插入「·助手」：`名字（Buzz·助手）：正文`；卡片模式下 speaker 显示为
  「名字（助手）」，蓝色模板（不是 agent 自己 bot 的绿色模板）——让人一眼看出这是被转述的，不是它自己的 bot 在说话。
- **代发不放宽通知**：p tag 一律不生成 `<at>`，卡片也不点名任何人。这与 agent 用自己 bot 发言那条路径一致（那条路径从来不渲染 `<at>`，因为
  open_id 属于另一个应用），所以代发不会让一个 agent 的消息比它自己发时更吵。
- **表情不在范围内**：reaction 仍然只能由 agent 自己的 bot 打，没有凭据就照旧跳过（`route_buzz_reaction` 不接这个开关）。代发只搬运消息，
  不获得该 agent 的任何凭据。
- **报告**：代发的消息计入既有的 `to_feishu`，另有 `relayed_agents` 计数区分「代发」与「自己发」；它只是计数，不影响退出码。
- **上线建议**：新建绑定时，如果频道里有不归你管的 agent，就设成 `"relay"`；存量绑定是否打开是各频道自己的风险决定。这个开关仍然默认关闭，不跟随已改为默认开启的 `feishu_unmapped_senders`。

## 换绑到另一个飞书群

脚本**故意拒绝**把已经绑了群的配置直接改绑到别的群（`config is already bound to another chat`），一个 state 目录也只服务一个「频道 + 群」。真的要换（2026-09-20 naturehood 就换过一次），按下面做，旧配置和旧 state 都留着，出问题能退回：

1. 停 timer（`systemctl --user stop buzz-feishu-<channel>.timer`），确认没有正在跑的一轮。
2. 备份配置（`cp -p config.json config.json.bak-<旧群短 id>`，仍然是 0600）。
3. 先对新群跑 `preflight --mode existing --chat-id <新群>`（只读），并看一眼新群现状：类型、群主是不是 owner、现有多少人、要拉几个人和几个 bot、群里不在映射里的人有多少（`remove_extras: false` 时他们会留在群里并看到镜像消息）。
4. 把配置里的 `chat_id` 改成 `null`，跑 `bind --config … --chat-id <新群>`（它会再预检一次，通过才写回 `chat_id`）。
5. 旧 state 目录改名留档（`mv state state.old-<旧群短 id>`），新建空的 `state`（0700）。新绑定从当下开始，**不补历史消息**；旧群里已经镜像过的消息不会搬过去。
6. 手动跑一轮 `round` 看报告（拉了几个人、几个 bot、`errors` 为 0），再开 timer。
7. **旧群不会被清理**：里面的同事和 bot 都还在，只是不再同步。要不要退群或解散是群主的事，脚本不做。

## agent 的飞书身份

每个要进群的 agent 都需要一个自己的 PersonalAgent 应用：

1. 在 agent 自己的目录下，设置 `LARKSUITE_CLI_CONFIG_DIR` 和 `LARKSUITE_CLI_DATA_DIR`，然后运行 `lark-cli config init --new`。
   - **这一步由你自己跑，不要做成待办交给 owner。**这条命令就是给 agent 用的：它阻塞等待，验证 URL 打在输出里。放后台跑，从输出里取 `https://open.feishu.cn/page/cli?user_code=…`，把这个链接交给 owner 点一下确认即可；确认完命令自己返回。一次起一个，确认完再起下一个。
   - **两种交给 owner 的方式，按现场选：**
     - **owner 在自己已登录飞书的浏览器里点链接**：最直接。给他 URL，并说清应用要叫什么名字（见第 2 条）。
     - **agent 用无头浏览器代做，owner 只扫一次码**：适合一次要建好几个（naturehood 一口气建了 5 个）。飞书登录页只给扫码一种方式（没有账号密码 / 短信），所以 owner 要扫一次登录二维码，之后的建应用、填名字全部由 agent 在同一个浏览器 profile 里完成。
   - 用无头浏览器时的要点（2026-09-20 实测，2026-09-21 补充）：
     - **二维码有效期很短（约 2 分钟）**，而且刷新一次旧码立刻作废。别自动定时刷新，会把 owner 正在扫的码作废；**每次要发码时才刷新，刷完立刻发**。
     - **发码前先确认 owner 在飞书前**：码约 2 分钟就过期，owner 不在就是白发一张、还得重发（2026-09-21 第一张码发出时 owner 不在，没人扫）。先问一句「现在能扫吗」，等到答复再刷新、发码。**发码前后各配一条文字**（用途、扫哪张、有效期），别让 owner 对着一张没有说明的图猜。
     - **两种发码方式都给**：飞书私聊和终端。私聊用 `cd <二维码所在目录> && lark-cli im +messages-send --as bot --user-id <owner> --image ./qr.png`：`--image` 的路径必须是**当前目录内的相对路径**，绝对路径（以及带 `..` 的路径）会失败（返回 `ok:false`），和图片同步里 `--image` 只收相对路径是同一个限制（见「边界」）。终端要把二维码渲染成半块字符（`▀▄█`），**直接贴在回复正文里，不要折叠在工具输出里**。
     - 无头浏览器用 `playwright-core` + 本机已装的 chromium，`launchPersistentContext` 保持登录态；表单名称框是 `input.ud__native-input`，提交是 `button[type="submit"]`，创建完页面显示「创建成功」。
     - 偶发「创建失败」是常态，别当异常处理：2026-09-21 那一批约三分之一次（两次都是重跑一次即成功），skills#124 记的另一批是每 5–7 个 1 个。手工操作时设备码仍然有效，重新加载同一个 URL、重填、再提交即可（nh-bi 第一次失败，第二次成功）；用脚本时，脚本带着 `创建失败` 非零退出并收掉 lark-cli，**没有自带重试**，重跑同一个 agent 即可（会新起一个 `config init --new`、拿新的 user_code）。
     - **用完就收**：结束浏览器进程并删掉登录态目录（`~/.cache/feishu-agent-browser*`），不要在本机留着 owner 的飞书会话。
     - **用脚本，别手工拼**：`FEISHU_BROWSER_DIR=<0700 目录> feishu_register_agent_apps.sh <agent 名>`（先让 `feishu_browser.js` 在那个目录里登录好）。脚本只在 app_id 合法、且回读的 `app_name` 与 agent 名逐字相等时才退出 0 并打印成功行；否则非零退出，`stderr` 里有原因（例如「app_name mismatch: expected 'nh-desk', got '陈敬敏的飞书 CLI'」）。**退出码不是 0 就是没成功**，不要凭输出里看到 app_id 就当成功。
     - **符号链接检查的边界**（jchen 已接受这个残余竞态，不再改逻辑，skills#117）：威胁模型：单用户、0700 目录；符号链接检查是检查前 + 创建后复核，不防同 uid 的进程。同一个用户下的其他进程本来就能读写这些目录，脚本不试图防它。
     - **浏览器会话的两个边界**：`quit.txt`（让会话退出的信号，用完不删）启动时先清掉上一次留下的，否则新会话一登录就退出、owner 得重新扫码；`cmd.txt` 不是合法 JSON 只算这一步失败——`result.txt` 写 `ERR <短原因>`（不回显命令内容），会话继续、浏览器不关、`ready.txt` 保留、不用重新扫码，脚本把非 `OK` 的答复一律当失败。
   - 设备码本身也会过期。owner 隔了十几分钟才去点时，先重起一次命令拿新码，别让他对着旧链接干等。
2. **给 URL 的时候一并说清应用叫什么名字。**确认页上可以直接改应用名称和头像，owner 在那里填掉，比事后补救省一次发版。**名字必须逐个不同**，就用 Buzz 里的 agent 名（`nh-desk`、`nh-dev`…）：群里 bot 显示的就是应用名称，不填的话每个 agent 都叫「<owner> 的飞书 CLI」，谁是谁分不出来。
   - 确认完回读一次，核对名字真的生效了（不打印任何 secret）：
     ```bash
     LARKSUITE_CLI_CONFIG_DIR=… LARKSUITE_CLI_DATA_DIR=… \
       lark-cli api GET /open-apis/application/v6/applications/<app_id> --params '{"lang":"zh_cn"}' --as bot \
       -q '.data.app.app_name'
     ```
   - 补救（没在确认页填、或以后要改）：开发者后台 → 应用信息 → 应用名称 + 头像 → 保存 → **创建版本并发布**（和可用范围一样，不发版不生效）。**没有改名的 API**：`PATCH /application/v6/applications/{app_id}` 只能改分类、refresh token 开关和回调，请求体里没有名称字段（2026-09-20 查官方 API 目录确认；ADR-0016 记的「显示名不可在注册时指定」只对命令行参数成立，确认页是可以填的）。
3. **创建完就把免审 scope 一起开通**：新建的应用只有基线的 36 个 scope（消息、卡片、评论），没有建文档、传图、授权群、云盘、多维表格的权限，后面 agent 要写飞书文档（见 [feishu-doc-report.md](feishu-doc-report.md)）时才发现缺，还要 owner 回来补。所以回读完应用名后，立刻生成申请链接给 owner，点一次开通：

   ```bash
   python3 references/scripts/feishu_scope_apply_url.py <app_id>
   ```

   发给 owner 时用 `lark-cli im +messages-send --markdown`（会包成 post，链接可点），**每个链接前写 agent 名**：一次建好几个应用时，owner 面前是一串长得一样的链接，没有名字就分不出哪个是哪个的。

   清单在 [feishu-agent-app-scopes.txt](feishu-agent-app-scopes.txt)（共 76 个 scope，是 owner 在 jchen-assistant 上实际开通并生效的，都是免审的文档、云盘、知识库、多维表格、白板类）。**没有 API 能给应用加 scope**（LCV-11），只能 owner 点链接；页面提示某个 scope 要管理员审核的，如实告知，不当作已开通。开通后 `lark-cli api GET /open-apis/application/v6/scopes --as bot` 回读核对。已经建好的老应用同样用这条链接补。
4. 可用范围保持默认即可。可用范围外的人照样能 @ 这个 bot，消息也能被 owner 的轮询收到（LCV-10）。
   - 只有 agent 需要私信同事时，才去后台放开可用范围。飞书没有给个人应用改可用范围的 API（LCV-11）。
   - 2026-09-19 实测：可用范围外的人收发消息都正常，包括收到 bot 私信。当时的实测对象是应用创建者，普通同事留到真机试点时再确认一次。
5. 把 app_id 和上面两个目录写进本配置的 `agents`。`round` 每轮都会核对这个 profile 的 `appId`。
6. 回收：删除这两个目录，再到开放平台后台人工删除应用。飞书没有删除应用的 API。

## 边界

- 图片同步的范围和限制见上文「图片同步」：只转图片，每张 ≤ 10 MB、每个事件最多 9 张；飞书 → Buzz 只转 jpeg / png / gif / webp 且要去掉元数据（ICC 也会被去掉）；失败只影响那一张图，`images_failed` 需要关注、`images_skipped` 只是计数。lark-cli 的 `--image` 与 `+messages-resources-download` 都只收相对当前目录的路径，脚本为此给每个事件、每张图建 0700 临时目录，发完就删；同机的其他用户读不到，但同一个 UID 下的进程读得到（和正文、token 一样）。
- 升级到带图片的版本后的第一轮：还在 900 秒重读窗口里、文字早已发出的带图事件，账本里没有它们的图，会被当成还没发而补发出来；更早的不补（游标已经过了）。
- 表情同步是单向的、有范围的：只有 agent 在 Buzz 上打的 reaction 会变成飞书表情。人的 reaction 不同步，飞书上的表情也不进 Buzz；目标消息必须已经镜像；绑定起点之前的不补。
  - 重读窗口是 15 分钟：因为目标还没镜像、或飞书暂时拒绝而没打上的 reaction，只在它还留在窗口里的这段时间会重试；被拒或结果不确定累计 3 次就放弃（`reactions_failed`）。
  - 一轮最多 50 次飞书调用，读不到 reaction 时整轮报错（消息已先同步），见上文第 5 步。
  - agent 的 bot 不在群里、或被飞书限流时，飞书会拒绝这个表情，走上面的重试与放弃。这类失败只进计数，不触发「需要关注」，想知道有没有表情丢了就看 `reactions_failed`。
- 同一个 UID 下的 0600 文件互相可读。agent 的 lark-cli secret 属于普通 agent 凭据的级别（SKILL.md 规则 11），高影响动作仍然走 ACT。
- 依赖 owner 机器在线：
  - 离线期间两个方向的消息，恢复后都会补上：Buzz 每轮最多约 1 万条，飞书每轮最多 2000 条，超过时整轮报错，由 owner 决定是否用 `--skip-backlog` 放弃；
  - 在很久以前（超过 6 小时发现窗口）的消息上新开的话题，只有我们在 Buzz 侧回复过它时才会被轮询到；
  - user token 的刷新期是 7 天，过期后需要重新登录。
- 人员接口要求使用者自己的 Buzz 私钥来签名（PO 2026-09-20 决定：本地运行用个人的 npub/nsec 就可以）。这把 key 在这台机器上本来就有（同一个 UID 下的进程都读得到），脚本只在内存里用它给这一个只读 GET 签名，签名者必须是这个频道的 owner 或 admin。将来如果要在不方便放私钥的环境（共享机、CI）里跑，需要 bridge 另外支持频道级、只读、可撤销的 API access token——那是 infra/buzz-deploy 的另一个决定，现在没有做。
- email 模式下，邮箱作为 `--query` 参数出现在 lark-cli 的进程参数里，同机的其他用户能读到（和消息正文一样，见「何时使用」里关于 `hidepid` 的要求）；脚本自己不打印、不缓存明文。通讯录搜索用的是 owner 的 user 身份，返回什么取决于 owner 在租户里能看到什么。
- union_id 模式认飞书发信人要对每个没见过的人多一次单条消息调用（每人只一次，之后进缓存）。飞书的 `user_id_type` 两种取法必须用同一条消息、按 `mentions[].key` 配对；如果哪天飞书改了这两个接口的行为，配不上的人会被计为 `sender_unpaired` / `mention_unpaired`，不会被认错。
- 卡片里的邮箱（`<at email=…>`）和 email 模式的通讯录搜索一样，会出现在 lark-cli 的进程参数里（只有发那张卡片的那一次调用），同机的其他用户能读到；脚本自己不打印、不写进 state。
- 自动化测试只用 fake runner 和 FakeWorld（它扮演 bridge 并真的验签，也建模飞书的三套 id、群成员列表 / 群信息 / 单条消息的 id 类型、通讯录的模糊搜索、飞书对卡片的态度：30 KB 上限、`<at id=on_…>` 被拒、`<at id=ou_…>` 与 `<at email=…>` 被接受、幂等键按接口一小时），不连真实飞书，也不连生产 relay 或生产 bridge。图片相关的假实现也照实测行为：`buzz media get` 要鉴权、只认 sha256[.ext]、`--file` 会写 imeta 并在正文后加 `![image](url)`、relay 对带元数据的媒体回 422；lark-cli 的 `--image` 与下载都只收相对路径、`--output` 没写扩展名时会补一个、幂等键 ≤ 50 字符并按接口计一小时。
- 签名头由 Python 生成、由 bridge 的 Go 验签器验证，两边用同一个金标准头做契约测试（本仓 L1-FGS-002 与 infra/buzz-deploy 的 L1-FB-NOSTR-022）。
