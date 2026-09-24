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
- 要同步图片的话：发图的 bot（本频道 Desk、每个 agent 的 bot）要有上传图片的权限（开放平台里的 `im:resource`；没有 API 可以加 scope，见 LCV-11）；owner 的 user 登录要能读群消息的资源（下载飞书里的图片）。缺权限时图片计入 `images_failed`，文字不受影响。细节见下文「图片同步」。
- bridge 已部署了「频道人员接口」并且打开了开关（infra/buzz-deploy ADR-0015，`CHANNEL_PEOPLE_API_ENABLED`），owner 是这个频道的 owner 或 admin。
- **上线前置：bridge 已部署 union_id 回填与新的响应字段（infra/buzz-deploy#77：`union_ids`，以及只在开了 `CHANNEL_PEOPLE_EMAILS_ENABLED` 时才有的 `emails`）。**默认的 `identity: "union_id"` 要用 `union_ids`；bridge 还没部署时脚本每一轮都会中止并说明缺什么（什么也不会动），可以先在配置里写 `"identity": "email"`（要 bridge 开 `CHANNEL_PEOPLE_EMAILS_ENABLED`）。见下文「人的身份怎么认」。

## 组成

| 部件 | 身份 | 用途 |
|---|---|---|
| owner 的 lark-cli（默认配置） | owner 本人的 **user** token | 建群并设 owner；加人、减人、拉 agent bot 进群；轮询群消息和话题消息；下载飞书消息里的图片 |
| 本频道 Desk 的独立 lark-cli profile | 本频道 `-desk` 的 **bot** | 默认代发 Buzz 人类、Workflow、非成员上下文及远端 Agent 的消息；发送图片、状态通知和代理表情 |
| 每个 agent 自己的 lark-cli profile | 该 agent 的 PersonalAgent **bot** | 把这个 agent 在 Buzz 上说的话（含图片）发到群里；把它在 Buzz 上打的 reaction 变成飞书表情（也由它自己的 bot 打） |
| buzz CLI | **镜像身份**（每个频道一个，bot 角色，由 owner 用 NIP-OA 背书） | 读频道成员、消息和 reaction；下载 Buzz 上的图片附件（`media get`）；把飞书上人的发言带署名（图片作为附件）发进频道 |
| bridge 的人员接口 | 频道 owner / admin 用**自己的 Buzz key** 做 NIP-98 签名的一个 GET：`<base_url>/bind/api/channels/<频道>/people` | 每一轮取一次本频道现存成员里已验证绑定的人：`people`（`{pubkey: open_id}`，**bridge 应用**的 open_id，本地认不出人，只解析不使用）、`union_ids`（`{pubkey: on_…}`）、`emails`（`{pubkey: [邮箱]}`，bridge 开了 `CHANNEL_PEOPLE_EMAILS_ENABLED` 才有）。绑定会变，所以每轮重取；pubkey → id 的对应关系不落盘 |
| `scripts/buzz_feishu_group_sync.py` | — | 确定性脚本，不含 LLM，提供 `preflight`、`create-chat`、`bind`、`round` 四个子命令 |

**不用 owner 的 Buzz key 转发飞书消息。**否则所有消息都会显示成 owner 说的；而且只认 owner 公钥的 agent（例如个人 agent）会把飞书群里任何人的话当成 owner 的指令。

**每轮开始先核对身份，不符就不发。**
- owner 的 lark-cli profile：`auth status` 返回的 `appId` 和 user `openId`，必须等于配置里的 `owner_app_id` 和 `owner_open_id`，否则整轮拒绝。
- 镜像身份：镜像 env 文件里的 key，用 `buzz users get` 查到的自身 pubkey 必须等于 `mirror_pubkey`，否则整轮拒绝。这能挡住把 env 文件误指向 owner 的配置错误。
- 每个 agent 的 profile：`appId` 必须等于它登记的 `app_id`。本频道 Desk 必须是频道 bot 成员、profile 已验证、bot 已在群里；缺任一条件整轮失败。其他 Agent 身份不符时只暂停该 Agent 的投递，绝不由 Desk 冒充它。

**统一发送策略（2026-09-24）**：每个 Channel ↔ 群绑定必须在 `desk_pubkey` 中明确指定自己的 Desk。没有 Desk、Desk 未验证或 bot 未入群时失败；没有 owner bot 代发模式、自动回退或按名字猜 Desk。人的消息与 Workflow 消息保留原作者署名；配置了本机飞书 bot 的 Agent 仍由自己的 bot 发。owner 的 user token 继续负责群读取和成员操作，切换发送者并不免除这一路凭据的维护。旧版本用 owner bot 发出的未决发送不再重试，旧消息也不由 owner bot 继续编辑或补图；历史飞书消息不会被自动搬到新话题。上线必须逐群配置、验证与回读，单独合并脚本不算所有群已切换。

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
9. 频道里有没有**不归你管的 agent**（别人 owner 的 agent 也在这个频道）？它的凭据永远不会在你机器上，缺省由本频道 Desk bot 代发
   （`buzz_unmanaged_agents` 缺省 `"relay"`，署名 `名字（Buzz·助手）：`，不 @ 任何人）；只有你明确不想让它的发言进群才写 `"skip"`。
   它的 owner 公开了飞书 app_id 的话，它的 bot 也会被拉进群、能被 @，不用你做任何事，见「本机没有凭据的 agent（Desk 代发）」与
   「别人的 agent 的飞书应用（kind:30177 目录）」。
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

每次调用跑一轮，由 owner 的 `systemd --user` timer 每分钟触发一次。同一个 state 目录有文件锁、只服务一个 `channel|chat` 绑定；绑定起点只在首次身份核对成功后写入。完整的读取窗口、话题根补发、正文中和、幂等与图片契约见 [feishu-message-sync.md](feishu-message-sync.md)。

每轮依次执行：

1. 核对 owner、agent 与镜像身份；
2. 对账成员。缺省的双向基线、飞书拉人进 Buzz、Buzz 移人、失败状态消息及安全上限见 [feishu-two-way-sync.md](feishu-two-way-sync.md)；
3. **Buzz → 飞书**：镜像消息、编辑、话题与图片；
4. **飞书 → Buzz**：`unmapped_sender` 缺省按上下文放行；显式把 `feishu_unmapped_senders` 设为 `"skip"`，或被 `feishu_sender_allowlist` 拦下时计入跳过原因，`sender_not_allowed` 只影响飞书 → Buzz；
5. **表情双向同步**，再处理飞书里的 `/approve` / `/deny`。当前规则见 [feishu-two-way-sync.md](feishu-two-way-sync.md)；
6. 推进各自游标；受单轮上限截住或存在未决项时不越过待处理事件。

发送前先在 state 落 `pending:<首次尝试时间>`。结果未知与确定被拒的重试、终态及退出码以下面的契约为准。

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
- 消息相关：`to_feishu`、`to_buzz`、`messages_updated`（Buzz 编辑事件在原飞书消息上更新成功的条数）、`unknown`、`failed`、`errors`；
- `context_to_buzz`：生效模式为 `feishu_unmapped_senders: "context"`（缺省）时就有，是 `to_buzz` 里属于非成员发言的那部分；只是计数，**不会**让退出码变成 3；
- 图片相关：`images_to_feishu`、`images_to_buzz`（发出去的张数）、`images_failed`（下载或发送失败、放弃的张数）、`images_skipped`（一个对象，键是原因，见下文「图片同步」，是策略不是故障）；`images_failed` 非零需要关注，`images_skipped` 不触发；
- 话题根补发：`thread_roots_backfilled`（补发到飞书的根，也计入 `to_feishu`）、`thread_root_unavailable`（根不可镜像或已放弃、回复退回顶层的条数）、`thread_root_failed`（取话题失败的次数）、`thread_roots_deferred`（超过单轮上限、留到下一轮的回复条数）；这四个只是计数，**自己不会**让退出码变成 3（取话题失败另计一次 `errors`，那个会）；
- 卡片相关：`cards_sent`（发成卡片的条数）、`cards_fallback_text`（卡片被飞书拒绝、改发文字的条数）；这两个只是计数，**不会**让退出码变成 3；
- 代发：`relayed_agents`（本机没有凭据、由本频道 Desk bot 代发的 agent 消息条数，见「本机没有凭据的 agent（Desk 代发）」；也计入 `to_feishu`）；只是计数，**不会**让退出码变成 3；
- agent 目录（ADR-0019）：`directory_agents`（这一轮从 relay 查到公开了飞书 app_id 的外来 agent 个数）、`directory_failed`（查询失败的次数，0 或 1：连不上、非 200、应答不是事件数组；这一轮不用目录，外来 agent 照样代发；双向成员同步会在频道和群的状态消息中说明本轮没有拉取远端 agent bot、下一轮自动重试）、`directory_conflicts`（被弃用的 app_id 个数：两个 agent 声称同一个，或和本机配置的 agent / owner 应用撞了）；三个都只是计数，**不会**让退出码变成 3，见「别人的 agent 的飞书应用（kind:30177 目录）」；
- 成员双向同步（ADR-0020）：`members_to_buzz`（因为在飞书里被拉进群而加进频道的人 / agent 个数）、`members_removed_from_buzz`（因为被移出群而移出频道的个数）、`members_refused`（agent 的 `channel_add_policy` 不让加的次数，群里提示一次）、`members_protected`（在飞书被移出、但频道里不移出的 owner / 同步签名身份，群里提示一次）、`members_unresolved`（被拉进群、但认不出 Buzz 账号的人数，群里提示一次去绑定）、`people_cache_failed`（`people_cache_file` 读写失败的次数）、`member_events_blocked`（为避免重复 9000/9001 而暂停的原因 → 次数）；前六个只是计数，最后一个同时计入 `member_failures` 与 `errors`，令退出码变成 3。双向模式会把目录失败、列表不完整、安全上限、未绑定、bot 名额不足和成员写入失败汇总到同一条频道 / 群状态消息，后续失败与恢复都原地更新；Buzz 暂时不可写时先直接提示飞书，恢复后补建 Buzz 根事件而不重复发群消息。见「成员双向同步」；
- Agent 入群介绍：`agent_intros_sent`（本轮成功）、`agent_intros_relayed`（其中由群助手按公开资料透明代发）、`agent_intro_failures`（本轮公开资料或发送失败）、`agent_intro_unknown`（超过幂等重试窗口仍无法确认结果）、`agent_intro_stopped`（连续明确失败后停止重试）。后三项会计入 `errors` 并进入同一条成员同步状态消息；正文与状态只使用公开资料，不包含 prompt / instruction。见 [feishu-two-way-sync.md](feishu-two-way-sync.md)「Agent 入群后的自我介绍」；
- 表情相关：`reactions_added`、`reactions_removed`、`reactions_failed`（放弃的次数）；双向（ADR-0020）另有 `reactions_to_buzz`（镜像把飞书里人打的表情打到 Buzz 的次数）、`reactions_withdrawn_in_buzz`（飞书里撤回、镜像在 Buzz 也撤回的次数）、`approvals_to_buzz`（飞书里的 `/approve` / `/deny JOIN-<id>` 转成镜像在申请上打的 ✅ / ❌ 的次数）；这六个只是计数，**不会**让退出码变成 3；
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

- **发送者是本频道 Desk bot**：设置 `LARKSUITE_CLI_CONFIG_DIR=<Desk config>` 与 `LARKSUITE_CLI_DATA_DIR=<Desk data>`，再执行 `lark-cli im +messages-send --as bot --chat-id <群> --text "$(cat guide.txt)" --idempotency-key usage-guide-<配置名>-1 --format json`。带幂等 key，重跑不会发两遍；owner 应用不代发这份说明。
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

**一条被镜像的 Buzz 消息 = 一张短卡片**（卡片 JSON 2.0）。手机上不刷屏是第一目标（jchen 2026-09-23）：通常折叠之前只有标题和一行灰字（加 @ 行），正文收在「展开全文」里；GitLab 同步消息末尾的「状态记录」例外，它在折叠面板之后直接显示，见下表。没有副标题和预览；普通消息没有底部按钮，GitLab 同步消息有一个确定性导航按钮：

| 部分 | 内容 |
|---|---|
| 标题 | **消息的第一行**（#127）：正文第一个非空行，取纯文字——加粗、删除线、斜体、行内代码的记号、行首的标题/引用/列表/任务记号去掉，链接和图片只留文字（地址不进标题）；标题里的转义方括号与链接包装一并清洗（同步为防伪造把标题里的反斜杠和方括号写成 `\\` `\[` `\]`，门牌行是链接 `[#132 \[标题\] …](url)`：链接只留文字，转义还原成原来的字符，与同步的 `_md_escape` 互逆；反斜杠后面是别的字符的不动）；一行、清洗过（同名字的清洗），最多 72 列（中文、全角、emoji 算 2 列，其余算 1 列，约 36 个汉字），由飞书手机客户端自然显示为手机两行；超出取前面放得下的部分加「…」（「…」按 2 列留位），不在标题里人工插入换行。同步消息的机器行（header 行、`🔔 通知` 行）不进卡片、取标题之前就已去掉，旧 `key: value` 长格式取 `title:` 的值（见下「机器行不进卡片」）；分隔线和只剩记号的行跳过；首个有内容的行是代码围栏就不从代码里取；没有可用的行（空正文、只有 header）就退回发言人。需要调整时改 `CARD_TITLE_COLUMNS` |
| 副标题 | 没有（2026-09-23 起；原来的「发言人 · #频道名」挪到正文第一行和摘要里） |
| 正文第一行 | 「发言人 · #频道名」，灰色小字、`plain_text`（名字成不了链接或标签），右边一个小号的「在 Buzz 中打开」链接（markdown，两者用 `column_set` 排在同一行）；链接不合法时只有灰字。发言人是人的 Buzz 显示名（清洗过，没有名字用 pubkey 前 12 位）或 agent 的显示名，各最长 60 个字符；频道名读 `buzz channels get --channel`，第一张要发的卡片才取，一轮最多取一次（缓存在内存里）；取不到就只写发言人，不影响发送。不再有「（Buzz）：」前缀 |
| 颜色 | 人 blue，agent green |
| 消息列表里的一行 | `config.summary`（摘要）：「发言人 · #频道名：内容前约 60 个字符」（取不到频道名就是「发言人：…」），纯文本、一行，超出加「…」；内容是去掉机器行之后的（不以 header 开头，旧格式也没有 `title:` 键名），并和标题走同一套清洗（链接只留文字，加粗、删除线、斜体、行内代码的记号和转义都去掉），清洗在截断之前 |
| @ 行 | 有 p tag 时，正文第一行之后单独一行，见下 |
| 折叠面板 | 消息比标题多出内容时就有（标题被截断、丢了链接地址、是表格行，或者还有别的行）：「展开全文（N 字）」，默认收起，里面是除末尾「状态记录」外的**完整** markdown（含标题那一行；加粗、列表、表格、代码块、链接、引用都保留）。整条消息只有标题这一行时没有面板 |
| 状态记录 | 只认 GitLab 同步消息中严格位于可读正文末尾、机器 header 之前的 `状态记录`，且其后至少有一条 `- ` 记录；这一整段不进折叠面板，作为最后一个**内容区**直接显示。普通消息的同名文字、或不在末尾的区块照常折叠。异常超长时明确标记「仅显示最近部分」，优先保留最近记录，卡片仍小于 30 KB |
| 按钮 | 普通消息没有底部按钮。结构合法的 GitLab 同步消息在绝对底部显示默认样式的「在 GitLab 中打开」按钮；目标是同步器生成的 HTTPS 对象 URL。状态记录仍是按钮前的最后一个内容区 |
| 图片 | 卡片里不放图片：事件带的图片附件在卡片之后作为随后的图片消息发出（同一个发送者、同一个话题，见「图片同步」）；正文里与附件重复的 `![image](url)` 已经去掉，别的 `![alt](url)` 改成「[图片：alt] 地址」，所以卡片的 markdown 里没有外链图片语法 |

**GitLab 按钮不信任普通正文。**只有同步 header 位于同步器规定的首行或末行时才解析目标；当前格式从同步器生成的 headline markdown 链接取 URL，旧格式或编辑聚合格式可从 `url:`、`MR:`、`Issue:`、`Pipeline:`、`Comment:`、`GitLab:` 行或独立对象 URL 取。目标必须是无账号密码的 HTTPS URL，且路径包含 GitLab Web 对象的 `/-/`。普通 Buzz 消息里的链接、正文中间伪造的 header、HTTP URL 都不能产生按钮。Comment 的按钮指向对应 `#note_<id>`。只承载目标的独立/带标签 URL 行从卡片正文移到按钮，不在折叠区重复；headline 链接保留，因为它还承载标题。Buzz 原消息、文字模式与卡片失败后的文字回退都不改。

**Comment 仍是消息，不是状态。**Issue/MR comment 继续由 GitLab 同步器作为 canonical Thread 里的独立回复投递，飞书侧也同步成该话题里的消息；comment 不写进 reaction，也不追加到主卡片的「状态记录」。主卡片的原位编辑只承载 Git 状态、字段/流水线等确定性时间线。

当一次状态变化确实需要给责任人真实 `p` tag 时，Buzz edit 本身无法新增 tag，同步器会另发一条带时间和变更标识的极简「Git 状态需要你关注」回复；飞书会同步这条注意力提醒，但不会重复完整状态正文。没有收件人时，状态变化只 update 原卡。

**机器行不进卡片**（skills#134）：GitLab → Buzz 同步消息里有几行是给程序读的，对飞书上的读者是噪音，卡片的任何位置都没有——标题、正文、折叠全文、`config.summary`。只影响卡片：Buzz 里的消息、同步的去重与读回、文字模式（`message_format: text`）和卡片被拒后回退的文字都不变。中和之后按结构认，不按内容猜：

- **header 行**：整行以 `[gitlab-notify:v1]` 开头。只认同步自己认 header 的两个位置：最后一个非空行（新格式，2026-09-18 起）和第一行（旧格式）；正文中间、句子里、代码围栏里引用的同名文字是作者写的内容，不动。
- **`🔔 通知 @…` 行**（ADR-0012，给 Buzz 客户端做正文 @ 高亮用）：只在带 header 的同步消息里去掉；卡片自己的 @ 行（见下「@ 的三种形式」）不受影响，仍然只在有 p tag 时出现。人在普通消息里写的「🔔 通知 …」不动。
- **旧 `key: value` 长格式**（同步 2026-09-18 之前的写法）：header 在第一行、第二行是 `title: 值`，后面是 `url:` `labels:` `assignees:` `milestone:` `description:`（MR 还有 `branches:` `sha:` `reviewers:` `author:`）各一行。标题取 `title:` 的值、不带键名；折叠全文里这一行同样不带键名。其余键值行照常显示，`url:` 那行移到卡片底部的 GitLab 按钮，`unmapped:` 是给人看的说明仍保留。只按结构认（header 首行 + 第二行是 `title: 值`）：没有 header 的 `title:` 行、header 在末行的 `title:` 行、header 首行但第二行不是 `title:` 的（2026-09-17 的紧凑 MR 格式）都不改。

存量旧格式的消息不迁移（同步对存量 Thread 不变），约 2500 条；每个旧话题第一次有新回复时，它的根会被补发成卡片（`thread_roots_backfilled`），就是上面的旧格式，所以这条规则要长期在。

**「在 Buzz 中打开」链接只能是 https**。飞书桌面端会吞掉 `buzz://` 这类自定义协议（2026-09-17 在 Mac 上实测：点了没反应），所以卡片里不用它——用户自己在正文里写的 `buzz://` 也会被中和成「buzz：//」，整张卡片里没有 `buzz://`。链接指向 bridge 的 `/bind/open` 页面：`<people_api.base_url>/bind/open?e=<事件 id>&c=<频道 UUID>[&t=<话题根事件 id>]`。页面在浏览器里打开，再唤起 Buzz Desktop 定位到这条消息。`t` 只在这条消息在话题里且 e tag 标了 root 时才有（取法与 bridge 的 `threadRoot` 一致：有 root 标记就是它；没有任何标记取第一个；只有 reply / mention 标记就没有）。id 必须是 64 位小写 hex / 小写 UUID，`base_url` 是校验过的 `https://主机[:端口]`；不合法就不放链接，卡片照发，不算失败。

**为什么链接不带签名也可以**：bridge 给自己的通知卡片链接带 `s`（HMAC）和 `n`（频道名），但这个签名只保护「频道名」这个显示文字——页面验不过签名就丢掉 `n`，仍然用 `e` / `c` / `t` 打开（生产实测不带签名是 HTTP 200，页面就是「在 Buzz 中打开」）。所以这里不需要 bridge 的任何密钥，链接里也不带频道名和签名；**不要**去拿或猜那把 HMAC key。

**@ 的三种形式**：p tag 里的每个人（发言人自己和重复的不算，一行最多 20 个），按优先级：

1. 邮箱已知 → `<at email=…></at>`。邮箱来自 bridge 人员接口的 `emails`（bridge 开了 `CHANNEL_PEOPLE_EMAILS_ENABLED` 才有），只含已映射的人（共用同一个飞书账号的不算），只在内存里用于这一轮，不进 state、不进报告；
2. 否则 owner 应用的 open_id 已知 → `<at id=ou_…></at>`：union 模式下是飞书自己配对出的（state 的 `idmap` 反查；那个人得在飞书群里发过言才配得出来），agent 的 bot 是群里的成员 id。email 模式下人只能经邮箱映射，所以总是第 1 种；
3. 都没有（映射不到、bridge 没给邮箱又没配对过）→ 不通知的纯文字 `@名字`（名字同样清洗）。

**绝不能用 union_id**：卡片里 `<at id=on_…>` 会被飞书拒绝（错误码 230099，`ErrCode: 100290 there is an invalid user resource (at/person) in your card`）；文字消息里的 `<at user_id="on_…">` 可以，卡片不行。open_id 又按应用隔离，所以 **agent 自己的 bot 发的卡片不用 owner 应用的 open_id**，只用邮箱（各应用通用）或纯文字。

**用户文字进卡片前一律中和**：`<` 全部换成全角 `＜`（所以 `<at id=all>`〔@所有人〕、伪造的 @、`<font>`、`<a>` 都成不了卡片标签，代码块里的 `<` 也会变成 `＜`）；去掉双向控制符和其他控制字符，各种换行统一成 `\n`，孤立的代理码位换成 `?`；沿用「像另一边署名的行前面加 `↳ `」的标记；名字、频道名和标题（消息的第一行；旧格式是 `title:` 的值）走清洗并限长——标题在清洗之前已经过上面的中和，`@` 变成全角 `＠`、引号和尖括号变成全角；同步消息的机器行是在中和之后按整行去掉的（见上「机器行不进卡片」）。markdown 语法本身（加粗、列表、表格、代码块、链接、引用）**保留**——Buzz 的消息本来就是 markdown。

**30 KB 上限与截断**：飞书拒绝 30 KB 及以上的卡片。整张卡片 JSON 超过 28 KB 时，脚本二分找出折叠面板还能放下多少个字符（按字符截断，不切断字符；截断处落在代码围栏里就先补收尾的围栏），末尾加一行「（内容过长已截断，完整内容请在 Buzz 中打开）」；任何内容出来的卡片都 < 30 KB（名字、频道名、@ 行也都有上限）。标题和正文第一行不受影响。截断时在链接、图片、裸 URL、行内代码、加粗中间就退回到它开始之前（宁可少放一点）；转义（`\[` `\]` `\\`）是文字不是语法，截在转义中间就连半个转义一起去掉。若卡片仅靠可见的「状态记录」就超限，先把它缩成「状态记录」＋明确的截断说明＋尽可能多的最近记录；它仍然直接显示，不移入折叠面板。

**飞书拒绝卡片时：回退成文字**。飞书明确拒绝这张卡的内容（lark-cli 错误 `type` 是 `api` 或 `validation`，且不是限流：限流码 230020、11232、99991400）说明这张卡没发出去：立即用**文字发送路径**重发同一条消息——文字内容与 `text` 模式一样，同一个父消息（话题回复还是话题回复）、同一个 bot（agent 的卡片改发文字仍由它自己的 bot），幂等键换成 `<键>-text`；`cards_fallback_text` 加一，`cards_sent` 不加，不算错误。账本里这条消息记 `,text`：之后不论文字被拒还是结果不确定，都只重试文字（文字可能已送达，再发卡片会重复）。总的规则：账本 extra 记的是首次尝试的发送方式（`,card` = 首次是卡片，`,text` = 卡片被拒后改发的文字，没有记号 = 文字模式的普通文字，和旧版本一样），每次重试**原样重复首次尝试**，同一个键、同一个请求。认证、权限、限流这类拒绝说的不是卡片内容，文字也会被拒，所以**不回退**，照旧记一次拒绝、下一轮用卡片重试。**结果不确定的失败（超时、`type: network`）不回退**：卡片可能已经发出去了，用同一个键、同一个接口重试，飞书按键去重。

### Buzz 编辑原位同步到飞书

Buzz 的 `kind:40003` 是编辑覆盖层：正文是替换后的完整内容，唯一合法的裸 `e` tag 指向原事件。同步脚本把它当控制事件，**不发第二条飞书消息**：

- 原事件必须已经由当前绑定镜像、仍能从本轮消息或它的精确 Thread 回读、类型是 `9` / `45001` / `45003`，而且编辑者与原作者的 pubkey 相同；多目标、跨作者、目标未镜像都跳过，绝不猜。
- 更新由原来发送消息的应用完成：Agent 消息用该 Agent 的 bot，新策略下代发的消息用本频道 Desk bot。旧 owner bot 消息不再由同步器编辑。卡片走 `PATCH /open-apis/im/v1/messages/{message_id}`，文字走 `PUT`；都是完整内容替换，原 `message_id`、话题位置和 Buzz 深链不变。
- 普通编辑按 `created_at` 旧到新应用；GitLab compact overlay 在同一秒内按机器头里的 `rev` 递增应用，保证最终卡片是最新状态。没有 `rev` 的旧事件维持稳定的兼容顺序。
- 新发卡片的 `config.update_multi` 固定为 `true`，这是飞书允许后续共享更新的前置条件。升级前已经发出的卡片没有这个声明，state 也不知道其实际发送模式，因此不回填、不试错，记 `edit_mode_unknown`；升级后新发的消息才可自动编辑。
- 确定被拒时最多重试 3 次；网络超时等结果未知时保留 pending，用同一完整内容再次更新不会产生重复消息。首次尝试起 45 分钟后关闭，未决事件会把 Buzz 读取起点往回拉。飞书自身还限制卡片只能更新发送后 14 天内的消息，普通消息的可编辑时间由企业管理员设置。
- `messages_updated` 只计成功更新；原消息仍在 `b2f`，编辑事件的结果在 `e2f`。编辑只替换文字/卡片本体，不增删原消息后面已经同步的独立图片消息。

**飞书 → Buzz 不受影响**：我们的 bot 发出的卡片在飞书里是 `sender_type=app`、`msg_type=interactive`。账本认得自己发出去的消息 id；即使账本丢了，读群消息和话题回复时也按 `sender_type=app` 跳过，不会被当作人的发言镜像回 Buzz，也不会在话题轮询里被当成回复。

## 图片同步

两个方向都同步图片，其他文件类型不同步。每张 ≤ 10 MB、每个事件最多 9 张；飞书 → Buzz 只接受 jpeg / png / gif / webp，并在上传前去除元数据。图片的来源、魔数与 hash 校验、临时目录、话题归属、幂等账本、失败与模型输入边界见 [feishu-message-sync.md](feishu-message-sync.md)。

`images_failed` 会让退出码变成 3；`images_skipped` 是策略计数。图片失败不重发已经成功的文字或其他图片。

## 配置（0600，owner-only，不含 secret）

```json
{
  "channel_id": "<uuid>",
  "chat_id": null,
  "owner_open_id": "ou_xxx",
  "owner_app_id": "cli_xxx",
  "desk_pubkey": "<本频道 Desk 的 64 位小写 hex 公钥>",
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

- 键是严格校验的：上面列出的每一个键都必须有，不能多；只有 `reaction_map`、`identity`、`message_format`、`feishu_sender_allowlist`、`feishu_unmapped_senders`、`buzz_unmapped_senders`、`buzz_unmanaged_agents`、`membership_sync`、`reaction_sync` 和 `people_cache_file` 可以不写。`chat_id` 由 `create-chat` 或 `bind` 写入，之前保持 `null`。旧版的 `people_export` 和 `email_domain` 已经取消，留着会被拒绝。
- `desk_pubkey` 必填，必须在 `agents` 中，且该 Agent 的 `app_id` 必须与 `owner_app_id` 不同；它指向该绑定唯一的默认代发者。配置多个平台 Desk 的频道也要明确选一个，不按显示名猜。配置缺失或该 Desk 不在 Buzz Channel／飞书群时整轮失败，不能退回 owner bot。

**存量绑定迁移**：先逐个停用该绑定的 `buzz-feishu-<channel>.timer` 并等正在运行的 service 结束；确认 Desk 已在 Buzz Channel 中为 bot、它的独立 Feishu profile 与 app 已配置在 `agents` 中、bot 已入对应飞书群。用新 release 的脚本执行 `migrate-desk --config <cfg.json> --desk-pubkey <64 hex>` 做只读 dry-run；输出 `status=ready` 才加 `--apply`。应用时脚本先在同目录写 0600 的 `config.json.bak.desk-<UTC>`，再原子写入 `desk_pubkey`；回读 `load_config`、备份与目标文件权限，最后手动跑一轮 `round` 并核对真实发送者和 Thread，成功后才重启 timer。已迁移同一 Desk 再执行只返回 `already_migrated`，不同 Desk、配置无效、备份同名时拒绝。失败时保持 timer 停止；恢复备份也只能在停止状态做，不能重启旧 owner bot 发送策略。正负向行为由 `DeskConfigMigration` 测试覆盖。所有运行中群都逐一执行，不能只迁一个样例。
- `identity`（可选）：`"union_id"`（缺省）或 `"email"`，别的值（含大小写不同、空串、`null`）一律拒绝，不会悄悄退回默认。含义与前置条件见上文「人的身份怎么认」。
- `message_format`（可选）：`"card"`（缺省）或 `"text"`，别的值（含大小写不同、空串、`null`、别的类型）一律拒绝，不会悄悄退回默认。`text` 与卡片出现之前逐字节一致，是一键退回的办法。含义见下文「消息卡片」。
- `feishu_sender_allowlist`（可选）：Buzz pubkey 的列表，只有名单里的人在飞书里的发言才会镜像进 Buzz；不写就不限制（和以前逐字节一致）。非空、每项 64 位小写 hex、去重后最多 50 个，别的写法（不是列表、空列表、大写或长度不对、多于 50 个不同的）整份配置被拒，错误里只说键名、不带值。用途和语义见下文「只让特定的人的发言进 Buzz」。
- `feishu_unmapped_senders`（可选）：`"context"`（缺省）或 `"skip"`，别的值（含大小写不同、带空白、空串、`null`、别的类型）整份配置被拒，不会悄悄退回默认，错误信息说明键名和可选值、不带出值。没有这个键时，映射不到的人的发言以「仅上下文」镜像进 Buzz；`"skip"` 显式关闭。它与 `feishu_sender_allowlist` 互斥：显式同时写 `"context"` 与白名单时整份配置被拒；存量配置只写了白名单而没写本键时，白名单的明确限制意图优先，隐式为 `"skip"`。语义和风险见「非成员的发言（仅上下文镜像）」。
- `buzz_unmapped_senders`（可选）：`"skip"`（缺省）或 `"context"`，校验规则与 `feishu_unmapped_senders` 相同（错误信息各自独立，不共用）。它是反方向：`"context"` 让既不是验证过的频道人类成员、也不是配置的 agent 的 Buzz 作者的消息，以「仅上下文」镜像进飞书。两个方向互相独立，可以只开一个、也可以同时开；不与 `feishu_sender_allowlist` 互斥（那份名单管的是飞书 → Buzz 的方向，不影响这边）。语义和风险见「非成员/非 agent 的 Buzz 消息（仅上下文镜像）」。
- `buzz_unmanaged_agents`（可选）：`"relay"`（缺省，ADR-0019 起）或 `"skip"`，别的值整份配置被拒、错误里不带值。它管的是**本机配置里根本没有的**频道 agent（别人 owner 的 agent）说的话：`"relay"` 由本频道 Desk bot 代发，`"skip"` 丢弃（`agent_bot_unavailable`，ADR-0019 之前的缺省）。语义见「本机没有凭据的 agent（Desk 代发）」。
- `membership_sync`（可选）：`"two_way"`（缺省，ADR-0020）或 `"buzz_to_feishu"`（以前的单向对账）；`reaction_sync`（可选）：`"two_way"`（缺省）或 `"agents_only"`（只同步 agent 的 Buzz 表情）；`people_cache_file`（可选）：本机所有群同步共用的人员缓存的绝对路径。别的值整份配置被拒、错误里不带值。成员与表情的双向同步、飞书里同意 agent 入群，见 [feishu-two-way-sync.md](feishu-two-way-sync.md)。**注意**：双向时在飞书群里拉进一个 agent 就会把它加进频道；没设 `BUZZ_ACP_CHANNELS`（订阅全部频道、不走入群申请）、`channel_add_policy` 还是缺省 `anyone` 的 agent 加进来后会**直接开始回复**，不经任何审批。防线是 ADR-0018 已要求的：平台类 agent 设 `owner_only`、executor 设 `nobody`，这样 relay 会拒绝、群里只收到一条提示。
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

## state（0600，不含正文和 secret，没有明文邮箱；「谁是谁」只在双向成员同步时为本频道成员记）

state 目录必须是本人所有、不是符号链接、组和其他人都无法访问（0700，脚本创建时就是这个权限）。目录里有两个文件：
- `round.lock`：以 0600 创建，打开时不跟随符号链接。
- `state.json`：保存下表这些字段。

两者任何一项不满足，整轮都拒绝运行。`state.json` 必须字段齐全，缺字段、多字段或类型不对时同样整轮拒绝：缺字段的 state 会被当成「什么都还没发」，从而重发最近的消息。唯一的例外是升级：只缺 `r2f` 和 `react_since`（表情同步之前写的 state）、只缺 `idmap` 和 `emailmap`（身份缓存之前写的 state）、或只缺 `images` 和 `img_unresolved`（图片同步之前写的 state：之前没有发过任何图，账本从空开始）时照常读取：`r2f` 从空开始，`react_since` 从 `buzz_since` 起算，所以升级不会把历史 reaction 补发一遍；两个身份缓存从空开始，按需补上（它们只省飞书调用）。只缺其中一个字段不算升级，照样拒绝。同样，只缺双向同步的十一个字段（`members_synced`、`feishu_seen`、`buzz_seen`、`member_notes`、`member_events`、`member_event_stream`、`member_event_seq`、`member_event_blocks`、`people_seen`、`rwatch`、`f2r`，ADR-0020 之前写的 state）时照常读取，下一轮只记基线；已有双向快照、缺后来增加的成员事件账本四字段时，它们从空账本、空 stream、序号 0 开始。上一版已有序号但尚无 stream 时，仅当未决账本为空才安全升级；下一次成员写入生成 stream，未决旧事件则失败关闭。只缺 `agent_intros_initialized` 与 `agent_intros` 时按升级读取：下一轮把群里已有 Agent 记为介绍基线，不集体刷屏。见 [feishu-two-way-sync.md](feishu-two-way-sync.md)「state 与报告」。

| 字段 | 内容 |
|---|---|
| `binding`、`floor` | 绑定的 `channel|chat`，以及绑定开始的时间 |
| `buzz_since`、`feishu_since` | 两个方向的游标 |
| `buzz_floor`、`feishu_floor` | 用 `--skip-backlog` 丢弃积压的时间点，更早的不再读取 |
| `b2f`、`f2b` | 两个方向的消息 id 映射，值可以是对方的消息 id、`pending:<时间>`、`retry:<时间>`、`failed` 或 `unknown`。Buzz → 飞书的 `pending` / `retry` 后面还记着首次尝试时回复的那条飞书消息（没有就是 `-`），再后面是首次尝试的发送方式：`,card`（首次是卡片，如 `pending:<时间>:-,card`）、`,text`（卡片被拒后改发的文字，如 `retry:<时间>:-,text`，之后只重试文字），没有记号就是普通文字（文字模式、旧版本写的 state）；重试原样重复首次尝试，切换 `message_format` 不改 |
| `b2f_modes` | 由支持编辑同步的版本成功发出的 Buzz 消息实际在飞书里的类型：`card` 或 `text`。旧 state 迁移为空，不猜历史消息类型 |
| `e2f`、`edit_unresolved` | Buzz 编辑事件的更新结果与未决发现时间。成功值仍是原飞书 `message_id`；pending / retry 额外记原 Buzz id、飞书 id 和发送模式，保证重试仍更新同一个对象 |
| `attempts` | 每条消息被确定拒绝的次数；表情的重试次数也在这里，键是 `r2f:<reaction 事件 id>`（打）和 `r2f-del:<reaction 事件 id>`（删），成功或放弃后清掉 |
| `r2f` | Buzz 上 reaction 事件 id → `作者 pubkey\|飞书消息 id\|emoji_type\|reaction_id`（已打上），或终态 `failed`（打不上，放弃）、`removed`（已撤销、不再处理） |
| `react_since` | reaction 的读取游标（kind 7 和 5）。单独一个，因为一轮被单轮上限截住时只有它不前进 |
| `idmap` | union_id 模式：本应用 open_id → union_id，来自飞书自己对同一条消息给出的两种 id（按 `mentions[].key` 配对），只有 id；格式不对的整个 state 被拒。open_id 按应用隔离，所以换了 owner 应用后旧应用留下的条目不会被新应用的任何 id 命中，只是占位、最终被裁掉 |
| `emailmap` | email 模式：`sha256(owner 应用 id + NUL + 邮箱)` → open_id，或 `miss:<时间>`（飞书里找不到，过了 `EMAIL_MISS_RECHECK_SECONDS` 才再查）；不落明文邮箱。这个摘要只是查找用的键、让明文不出现在 state 里，**不是保密措施**：邮箱的取值空间小，知道候选邮箱的人能验证；真正的保护是 state 目录和文件只有本人可读（0700 / 0600，同一个 UID 下本来就读得到 owner 的 key 和 lark-cli 的 token） |
| `images`、`img_unresolved` | Buzz → 飞书的图片账本（键 `<事件 id>:<序号>`；超过 9 张的部分记在 `<事件 id>:over`）：飞书消息 id / `pending:<首次时间>` / `retry:<首次时间>` / `failed` / `unknown` / `skipped`，另有 `<事件 id>:thread`（文字发在哪里：话题根的消息 id 或 `-`，图片跟着它走）；以及等待重试或结果未知的图 → 事件时间，用来把读取窗口往前拉。`attempts` 里图片的重试次数键是 `b2f-img:<事件 id>:<序号>`（Buzz → 飞书），结果出来就清掉 |
| `unresolved`、`f_unresolved` | 等待重试或结果未知的项目 → 原始时间，用来把读取窗口往前拉。Buzz → 飞书方向还包括等话题根的回复（`b2f` 里没有它，只有这里；根发出去、或取话题重试用完、或根被放弃之后才发出）和补发的根（按补发的时间计） |
| `threads`、`polled`、`tried` | 飞书话题根消息 id → 最近活跃时间、上次完整轮询的时间（该话题自己的游标）、上次失败或没读完的时间（决定轮换顺序） |
| `members_synced`、`feishu_seen`、`buzz_seen`、`member_notes`、`member_events`、`member_event_stream`、`member_event_seq`、`member_event_blocks`、`people_seen`、`rwatch`、`f2r` | 双向成员与表情同步（ADR-0020）：快照、提示记录、9000/9001 精确重试的完整签名事件、与 signer 解耦的随机事件流、事件顺序高水位、持久暂停原因、本频道见过的人、读表情的消息、镜像打到 Buzz 的表情。`member_events` 最多 256 条，未知项不自动淘汰；达到上限就暂停新写入并提示。双向时 `buzz_seen` / `people_seen` 为**本频道成员**记 pubkey ↔ 飞书 id；单向（`"buzz_to_feishu"`）时不落任何对应。逐项见 [feishu-two-way-sync.md](feishu-two-way-sync.md)「state 与报告」 |
| `agent_intros_initialized`、`agent_intros` | 入群介绍的升级基线与每个 Agent 的一次性幂等账本；pending/retry 在安全窗口内复用同一个飞书幂等键，成功后保存 message_id |

state 是有界的：id 映射（含 `r2f` 与 `images`）最多保留 20000 条（`images` 里还有未决项的图片，连同同一事件的 `:thread` / `:over` 记录不裁：它的 45 分钟窗口和重试次数就在账本值里，裁的是最早的、已经有结果的），话题最多保留 200 个，`idmap` 和 `emailmap` 各最多 5000 条（丢的是最早写入的，丢了只是多查一次）。state 文件损坏、字段类型不对或多出字段时整轮拒绝，不会当成空状态，否则会把所有消息重发一遍。

## systemd --user 示例

两个坑要先知道（2026-09-20 naturehood 上线时踩到，#110）：

- **脚本要用不可变拷贝，不要指到工作树或临时目录**：定时器每几分钟就跑一次，工作树一切分支、临时目录一清就断。按 [local-upgrade-runbook.md](local-upgrade-runbook.md) 把完整 `skills/buzz-agent-setup` 原子安装到 `~/.local/share/buzz-agent-setup/releases/<40 位 SHA>/` 并 `chmod -R a-w`；单元指向它，升级时换一个新目录，不覆盖旧的。release 根下直接是 `scripts/` 与 `references/`，不再使用 `feishu-group-sync-<短 sha>/skills/buzz-agent-setup/` 旧布局。
- **`PATH` 必须带上 lark-cli 的 node 目录**：`lark-cli` 是 node 脚本（`#!/usr/bin/env node`），systemd 用户单元的默认 `PATH` 里没有 nvm 的目录，缺了它每一次飞书调用都会失败。脚本只把 `PATH` 等白名单变量传给子进程，所以要在单元里 `Environment=PATH=…` 写明。

```ini
# ~/.config/systemd/user/buzz-feishu-<channel>.service
[Unit]
After=network-online.target

[Service]
Type=oneshot
UMask=0077
NoNewPrivileges=yes
Environment=PATH=%h/.nvm/versions/node/<版本>/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 %h/.local/share/buzz-agent-setup/releases/<40 位 SHA>/scripts/buzz_feishu_group_sync.py round --config %h/.config/buzz-feishu-sync/<channel>/config.json --state-dir %h/.config/buzz-feishu-sync/<channel>/state
# 3 = 本轮完成但需要关注（比如有人映射不到），不算服务失败
SuccessExitStatus=3
TimeoutStartSec=10min
Nice=5

# ~/.config/systemd/user/buzz-feishu-<channel>.timer
[Timer]
OnActiveSec=1min
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=15s
Unit=buzz-feishu-<channel>.service
[Install]
WantedBy=timers.target
```

**同步间隔不要超过 120 秒**（重读窗口）：开着 `feishu_unmapped_senders: "context"` 时，陌生人的话要等窗口，间隔更长的话第一次读到时年龄已经超过窗口、当轮就发，暂时映射不到的成员的发言就得不到自愈（见「非成员的发言（仅上下文镜像）」）。

装好后 `systemctl --user daemon-reload && systemctl --user enable --now buzz-feishu-<channel>.timer`，再 `systemctl --user start buzz-feishu-<channel>.service` 手动跑一轮，用 `journalctl --user -u buzz-feishu-<channel>.service` 看每轮的报告（只有计数，没有邮箱或 id）。验收和长期运行都保持 `OnUnitActiveSec=1min`；如需临时降频也不得超过 120 秒，不能改回 5 分钟。

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

## 发言人与代发策略

以下策略的完整语义、风险与验收见 [feishu-routing-policy.md](feishu-routing-policy.md)：

- `feishu_sender_allowlist`：只让指定 Buzz pubkey 的人的飞书发言进入 Buzz；
- `feishu_unmapped_senders`：缺省 `"context"`，把飞书非成员的发言作为不可信上下文镜像；
- `buzz_unmapped_senders`：决定 Buzz 侧非成员 / 非 agent 作者是否只作上下文镜像；
- `buzz_unmanaged_agents`：`"relay"`（缺省）或 `"skip"`；本机无凭据的 agent 缺省由镜像身份代发。

真实飞书 @agent 才产生 p tag；能唤醒不等于获得授权。平台类 agent 应设 `owner_only`，executor 应设 `nobody`；未设置 `BUZZ_ACP_CHANNELS` 且仍是 `anyone` 的 agent 被从飞书拉入频道后会直接开始回复。

## 别人的 agent 的飞书应用（kind:30177 目录）

决定见 ADR-0019（engineering/skills#147），取代 infra/buzz-deploy#96 里「bridge 提供 agent 映射接口」的方案。

**为什么需要**：代发只解决「看得到别人的 agent 的回复」。要在飞书里 **@ 到**它，它自己的 bot 得在群里、同步脚本得知道那个 bot 是谁——
而「哪个 agent 对应哪个飞书应用」以前只写在 agent owner 本机的 `agents` 里，别的操作者无从得知，飞书里的 `@nh-dev` 只能当文字转发，
agent 永远不会被唤醒。

**怎么公开**：agent 的 owner 把 `"feishu": {"app_id": "cli_…"}` 写进这个 agent 的 kind:30177（owner 签名、relay 上全员可读，relay 不校验
content，Buzz Desktop 解析时忽略它不认识的字段）。用 `scripts/buzz_agent_feishu_app.py`，见下文「agent 的飞书身份」第 6 步。app_id 不是机密：
拿到它换不来任何发送能力，凭据仍然只在 owner 本机。

**同步脚本怎么读**：每轮只在频道里有「本机没配置的 bot 成员」时，用 `people_api.signer_env_file` 那把 key（频道 owner/admin 的个人 key，
只在进程内签名）签一次 relay 的 `POST {BUZZ_RELAY_URL}/query`，一起取这些 agent 的 kind 0 与 kind 30177：

- `/query` 回来的每条事件先重算 NIP-01 规范 id，再验 BIP-340 `sig`；HTTP 200 不是事件信任边界。验证失败的条目
  直接丢弃，不参与最新 head 选择。发布 helper 读 30177 与镜像 profile 时也执行同样验证。
- 30177 的作者必须等于该 agent **最新** kind 0 里 `auth` 标签声明的 owner（与 Desktop 选 30177 的规则相同）；取 owner 签的最新一条，
  同一时间取 id 最小的；最新那条没写 app_id 就是没有，不回退到更早的。
- app_id 格式不合法的不用；**两个 agent 声称同一个 app_id** 时谁都不用；与本机配置的 agent 或 owner 应用撞了也不用（本机配置永远优先）。
  这几种计入 `directory_conflicts`。
- `auth` 标签里的 NIP-OA 背书签名不另外验；但 kind 0 和 30177 事件本身的 id / sig 都必须验证通过。
- **读不到**（连不上、非 200、429、应答不是事件数组）：这一轮不用目录，`directory_failed` 加一，**不**影响退出码；外来 agent 照样代发，
  其余同步照常。

**查到以后**：

- 它的 bot 按 app_id 由 owner 的 user 身份拉进群（和本机配置的 agent 一样，占群里 bot 名额，满了计 `blocked_bots`）；飞书那边如果因为
  这个应用的可用范围等原因拒绝，计入 `member_failures`，照常重试。
- 飞书里 @ 它的 bot（真 mention）→ Buzz 里对它 pubkey 的 p tag，能唤醒它（它愿不愿意回应仍由它自己的 `respond_to` 与频道清单决定）；
  人在 Buzz 里 @ 它 → 飞书里对它 bot 的 `<at>`。
- 它自己的 Buzz 消息由本频道 Desk bot 发到飞书群（本机没有它的凭据，`buzz_unmanaged_agents` 缺省 `"relay"`），卡片保留该 Agent 的原作者署名；镜像只负责飞书 → Buzz 的消息。它打的 Buzz reaction 在 `reaction_sync` 缺省的 `"two_way"` 下由同一个 Desk bot 打到飞书，`"agents_only"` 下跳过。owner bot 不代发消息或 reaction。
- 30177 里声明了 `"feishu": {"mirror": true}` 的身份是别的机器的镜像，不是 agent：不进目录、它的消息跳过（`other_mirror`），见
  [feishu-two-way-sync.md](feishu-two-way-sync.md)「认人」。

**已接受的风险**：频道成员可以用自己名下的 agent 声称别人 agent 的 app_id。后果只是在同时有这两个 agent 的频道里把对那个 bot 的 @
认给声称者，不泄露、不授予任何能力；真正的 agent 也在频道里时两边都会被弃用（`directory_conflicts`）。

**已知限制**：

- 映射要 agent 的 owner 主动公开；没公开的 agent 仍然只能代发、@ 不到（`directory_agents` 能看出查到了几个）。
- 目录里的 agent **离开频道后**：双向成员同步（`membership_sync` 缺省 `"two_way"`，ADR-0020）按快照里记下的 app_id 把它的 bot 移出群；
  单向（`"buzz_to_feishu"`）时仍不会自动移出，要手动移出。
- 每个有外来 agent 的频道每轮多一次 relay 请求（几 KB）。

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
6. **把 app_id 公开到这个 agent 的 kind:30177**（ADR-0019），这样它被拉进**别人**运行同步的频道时，那边也能把它的 bot 拉进群、在飞书里 @ 到它。
   由 agent 的 owner 运行，先 `--dry-run` 看一眼再正式发：

   ```bash
   python3 scripts/buzz_agent_feishu_app.py --owner-env ~/.config/buzz/env --agent <agent pubkey hex> --app-id <cli_…> --dry-run
   python3 scripts/buzz_agent_feishu_app.py --owner-env ~/.config/buzz/env --agent <agent pubkey hex> --app-id <cli_…>
   ```

   - 它只改已有的 kind:30177（只合并 `feishu.app_id`，别的字段原样），relay 上没有 owner 签的这条就拒绝、不新建；旧事件备份在
     `~/.local/state/buzz-agent-feishu-app/`（0600）。输出 `published` / `unchanged` / `would_publish`，不打印 key。
   - app_id 不是机密，公开它不给别人任何发送能力；凭据仍只在 owner 本机。
7. 回收：删除这两个目录，再到开放平台后台人工删除应用。飞书没有删除应用的 API。

## 边界

- 图片同步的范围和限制见上文「图片同步」：只转图片，每张 ≤ 10 MB、每个事件最多 9 张；飞书 → Buzz 只转 jpeg / png / gif / webp 且要去掉元数据（ICC 也会被去掉）；失败只影响那一张图，`images_failed` 需要关注、`images_skipped` 只是计数。lark-cli 的 `--image` 与 `+messages-resources-download` 都只收相对当前目录的路径，脚本为此给每个事件、每张图建 0700 临时目录，发完就删；同机的其他用户读不到，但同一个 UID 下的进程读得到（和正文、token 一样）。
- 升级到带图片的版本后的第一轮：还在 900 秒重读窗口里、文字早已发出的带图事件，账本里没有它们的图，会被当成还没发而补发出来；更早的不补（游标已经过了）。
- 表情缺省双向同步：Buzz 上 agent 的 reaction 进飞书，飞书上人的 reaction 由镜像身份写回 Buzz；范围、重读窗口、撤销、防回声和 `agents_only` 退回模式见 [feishu-two-way-sync.md](feishu-two-way-sync.md)。
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
