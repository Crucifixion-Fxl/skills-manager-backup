# 本机接收与 session 话题

以下命令从 Skill 目录运行。路由和主动读箱只用标准库；Codex 自动桥接另需
`websocket-client>=1.8,<2`，可使用 `uv run scripts/daemon.py ...` 或私有 venv。
本机自动模式把收件箱投递到原 Codex session；其他宿主可使用主动读箱模式。

## Codex 自动模式与默认 hello

在下方身份、应用、唯一入口核验完成后，在私有配置增加 `bot_id` 与 `codex_homes`
（已批准的 Codex home 绝对路径数组），将 `bot_transport_approved` 设为 true：

```bash
uv run scripts/daemon.py --config ~/.local/state/feishu-session-collab/config.json
```

此进程启动唯一 `event consume` 接收器，同时连接各 home 已运行的本机控制 socket。
它不重启 Codex。每 10 秒发现已加载、可直接接收用户输入的主会话；内部子 agent
不建个人控制话题。不会将历史 SQLite 中的全部会话当作活跃会话，也不会启动历史任务。
新建/恢复会话和重新建立桥接连接时默认发送 recap hello；已有 session 保留原根话题。
当前接口使用 Unix socket 上的 WebSocket，而非 NDJSON 裸 socket。

单独重新 wire 一个已加载会话同样默认发 recap：

```bash
uv run scripts/collab.py --config ~/.local/state/feishu-session-collab/config.json wire --home <codex-home> --session <session-id> --epoch <this-wire-id>
```

同一次失败重试沿用 epoch，不换 ID 重发。hello 发送正文先持久化，回读前不绑定。
若发送回执未知，暂停该消息，需人工核实；API 已发送但回读失败只重试读取，不能重发。
bot 消息发送者按 `id_type=app_id` 与当前应用匹配，或按 `open_id` 与已核验 bot 匹配。
recap 是目标、最近请求与进展的可见内容摘录；没有任务的会话明确写“尚无任务输入”。

后续已完成的公开 agent 消息会回复原话题。不会同步工具结果、内部推理或完整 transcript。
重连只补最近 3 轮已完成的公开输出；长时间断连更早的进度需在原 session 核对。
首次接入把已完成历史消息作为基线，正在生成的末尾消息待完成后再发送；重新接入
不会提前确认遗漏输出。早期本机版本的错误基线升级时仅补最近 3 轮的最后结果，
保护已有 verified 发送回执，避免把旧进度全部重播。
主人输入通过 `thread/queue/add`，校验客户端 ID、正文与 submission 回执后才 `ack`。
执行中的会话不被中断；空闲时用 `thread/queue/start` 在同一 session 续轮。
此处 ack 证明队列已接收，**不证明 AI 已处理**。投递结果未知时先查队列，未找到不能盲重投。
已登记记录每 30 秒轮询；评论继续以 `record_context` 投递，不能作为主人授权。

推荐用一个用户 systemd unit 管理 daemon，`Restart=on-failure`、`UMask=0077`、
`KillMode=control-group`。单一 daemon 有独立锁，上游 listener 也按 app 加锁。
自动桥接运行时，不要再启动 `listen` 或另一个主动读箱消费者。
hello、输出镜像、评论读取和主人输入分别处理失败；进度发送结果未知不会阻塞主人输入。
现场用 `event status --json` 确认应用只有一个 consumer，核验 hello 回读与 bindings。
端到端验收仍需主人在真实话题中回复；单测和 socket 连通不等于真实入站已通过。

以下步骤说明身份、话题和通用主动读箱模式的操作细节。

## 1. 核对 CLI 与应用

按本机策略确认 Node/CLI 入口和 `@larksuite/cli` 包名；不检查固定版本或哈希。
通过批准的绝对路径读当前 `lark-shared`、`lark-im`、`lark-event` 自带 Skill，
以及 `event schema im.message.receive_v1 --json`。事件输出需要保留
`message_id/chat_id/chat_type/sender_id/sender_type/root_id/thread_id/reply_to/content`。
`content` 通常已经是文本，不要再次 `fromjson`。

首次启动检查 `event status --json`，查同应用的 SDK/hostd 和其他机器。CLI 的本机
多个 consumer 共享一个 UDS bus；本脚本按 app 的文件锁只防止此脚本重复启动，
不能证明跨机器唯一性，也不能阻止别的 SDK。已有统一接收器时由它转交事件，
不要为这个 Skill 再建同应用长连接。不同 app_id 的 open_id 不可直接互用。

本机策略只有 `strict-mode=user` 时保持它；先交付本地路由与草稿，再由用户批准
个人 bot 传输的配置方式。不要擅自更改全局策略，也不要启用未批准的 bot/profile。

配置保存在 `~/.local/state/feishu-session-collab/cli.json`，目录 0700、文件 0600。
将下面的占位值替换为本机批准值（不是凭据）：

```json
{
  "node": "/absolute/approved/node",
  "entry": "/absolute/approved/@larksuite/cli/scripts/run.js",
  "profile": "approved-personal-profile",
  "app_id": "cli_approved",
  "owner_id": "ou_owner_in_this_app",
  "owner_name": "主人真实姓名",
  "bot_transport_approved": false
}
```

只有明确获准使用该 bot 传输时才把最后一项设为 true。这是本 Skill 的启动声明，
不会更改 CLI strict-mode。个人通知和 Task 读取永远显式使用同一批准 profile 的 user 身份。
不同应用的单独 bot 传输尚未由随附 listener 实现；若要求分应用，接入外部接收器并
分别验证身份映射，不能把 owner 的一个 app open_id 拷到另一个 app。

## 2. 建立话题

在 bot 身份已获准的前提下，通过批准 Node/CLI 的 argv 调用以下命令（不使用 shell
拼接用户文字；正文从 stdin 输入）。`--profile` 放在域命令前：

```text
<node> <entry> --profile <profile> whoami
<node> <entry> --profile <profile> im +messages-send --as bot --user-id <owner_open_id> --text - --idempotency-key <session-root-key>
<node> <entry> --profile <profile> api GET /open-apis/im/v1/messages/<root_message_id> --as bot
```

检查根消息的 chat_id、正文、应用发送者与发送回执一致；发送目标必须是主人单聊。
session ID 从当前宿主获取，不从消息正文猜测；没有可靠 ID 就由本机登记稳定 ID。
根消息回读成功后登记：

```bash
python3 scripts/collab.py bind --session <session-id> --app <app-id> --owner <owner-open-id> --chat <dm-chat-id> --root <root-message-id>
```

API 返回 thread_id 时加 `--thread`；第一次已知 root 的回复出现新 thread_id 时，
脚本会在身份校验后登记它。根消息/话题不能绑定两个 session，session 身份不能覆写。

进度、问题、结果用同一 bot 回复原根消息；当前 CLI 命令是
`im +messages-reply --as bot --message-id <root> --reply-in-thread --text - --idempotency-key <stable-key>`。
按当前 help 和自带 `lark-im` reference 执行并回读，再登记后续消息 ID：

```bash
python3 scripts/collab.py alias --session <session-id> --message <bot-followup-message-id>
```

## 3. 唯一接收器

已确认不存在争抢入口且获准使用 bot 后，由本机宿主启动一个常驻工具进程：

```bash
python3 scripts/collab.py --config ~/.local/state/feishu-session-collab/cli.json listen
```

它内部调用 `event consume im.message.receive_v1 --as bot`，保持子进程 stdin
打开，等待 stderr 的 `[event] ready event_key=im.message.receive_v1` 后才声明已就绪。
不要用 `--quiet` 隐藏丢事件诊断。stderr 显示就绪和异常，stdout 是不带消息正文的
路由结果；符合条件的正文仅存入本机 SQLite。

仅支持文本/富文本消息。图片和文件不自动下载、不作为控制指令；需要时让主人在
话题中用文字说明，再按已授权任务处理附件。SDK 原始 V2 信封需先转换为 CLI flat
schema；不能直接喂入。受信任的外部统一入口可把此 shape 通过本机 stdin 转交：

```bash
python3 scripts/collab.py replay --app <app-id> < owner-events.ndjson
```

`replay` 不连接飞书，用于外部入口转交/本地验收。绑定和事件输入都是本机可信控制面，
不能暴露给网络或让他人直接写入；脚本不提供远程 HTTP bind 入口。

## 4. AI 读箱与等待

```bash
python3 scripts/collab.py inbox --session <session-id>
python3 scripts/collab.py inbox --session <session-id> --wait 30
python3 scripts/collab.py ack --session <session-id> --id <inbox-entry-id>
```

AI 将 `owner_message` 视为已校验主人的新输入，将 `record_context` 视为带来源的
协作数据。按当前任务范围处理，处理后显式确认；未确认的消息重启后仍在。
一个 session 只允许一个宿主消费：脚本不是多消费者抢占队列，两个宿主同时读箱可能
执行两次。重启后检查未确认消息和执行回执；`ack` 仅确认输入被处理，不证明外部动作成功。

工作时在工具返回、进度汇报前检查；需要主人答复时，在原 bot 话题提出具体问题，
使用等待工具。宿主续轮才能持续待命，Skill 文本不能自行唤醒结束的 Codex。

session 正常结束前发最终状态、处理待确认记录，再显式执行
`python3 scripts/collab.py finish --session <session-id>`。关闭后不再接收新输入；
新任务建新 session 根消息。不要仅因暂时空闲就关闭。

## 5. 断线与验收

飞书接收进程退出时由宿主报告当前无法接收，并按相同入口恢复。SDK/bus 是否重连、
断线时是否漏事件需现场核验；此脚本没有自动读取主人聊天历史补洞。漏消息可请主人
在原话题重新回复，或在授权范围内精确核对该话题；不会转而读取协作者私聊。

现场至少验证主人真实回复入箱、嵌套回复、非主人拒绝、同应用唯一连接和恢复、AI
等待时读到输入并继续工作。持久化和单元测试不是这些现场结果的替代品。
