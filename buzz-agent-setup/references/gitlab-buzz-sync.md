# GitLab → Buzz 全量变更同步（按频道配置）

**发布状态**：reference candidate。按 [ADR-0008](../../../docs/05-adr/0008-run-gitlab-sync-from-owner-systemd-timer.md) 的 owner-host scheduler 边界，生产触发器是 Linux `systemd --user` timer 或 macOS `launchd` job，同步路径里没有 LLM；公开 Buzz Workflow schedule 保持 disabled/decommissioned，Desk 不再用 heartbeat 跑同步。
- 在最终 revision 上重跑并保存 L2-2、L2-3、L3 本地证据之前，任何频道都不启用 timer。
- 之后先按测试方案 §7 阶段 0 手动运行一轮：Linux 用 `systemctl --user start` service，macOS 用 `launchctl bootstrap` + `kickstart`、等待 exit 后 `bootout`；external receipt v3 全绿后才启用 300 秒周期任务。
- 每个业务 Channel 各自一个 timer，各自通过阶段 0。

适用场景：GitLab 项目 webhook 能推送的每一类变更都同步到业务频道。
- 每个 Issue 对应唯一一个 Thread；MR 的事实只发进 binding 指向的一个 Thread（closes 的 Issue → 分支名白名单 Issue → 第一条 origin → 分支族 → 自开门牌，[ADR-0015](../../../docs/05-adr/0015-deliver-an-mr-to-one-thread-and-cross-link-the-others.md)），其他关联的 Issue／origin 只在 MR 首次出现时各收一条交叉链接；未关联的 MR 按分支族共用 Thread（同源分支，或同一改动的 `<base>-<target>` 扇出，2026-09-18 起）；存量已绑定的 per-MR Thread 不搬迁，按旧规则多发出去的消息也不清理；状态变化回到原 Thread，由 Desk 的确定性本地 route gate 按当前可信 Canvas 规则指派角色 Agent。
- 新 Issue 的首条状态事实就是 Thread root（只发一条，不写「首次同步」），后续状态卡与评论归入该 Thread；MR 🔀 / 分支 🌿 / 里程碑 🎯仍使用纯主题门牌。已有 Issue 门牌 Thread 不搬迁；见 [ADR-0021](../../../docs/05-adr/0021-use-the-first-issue-fact-as-the-thread-root.md)。
- **通知政策（2026-09-18，频道 owner 确认，同日两次修订）**：Buzz 承载 Issue / MR / milestone 三类主题 Thread，加上**发布类**即时通知（tag 建删、新 Release——13:16 owner 确认恢复）、**失败类**即时通知（部署失败或受阻、默认分支流水线失败）与项目 access token 7 天内到期提醒。push、feature flag、wiki、成员变化、commit/snippet 评论、流水线与部署的 happy path **一律不通知**（见「顶层通知」）。MR 的交叉链接（`change:xref`，见「MR Thread」）是 Issue／origin Thread 里的一条 Desk 回帖，不是顶层通知，不在这份清单里，也不受 `mute_events` 影响。

依据与相关文件：
- 架构：buzz-deploy [gitlab-buzz-bridge.html](https://gitlab.addx.ai/infra/buzz-deploy/-/blob/main/docs/architecture/gitlab-bridge/gitlab-buzz-bridge.html)；产品场景：[gitlab-buzz-bridge.html](https://gitlab.addx.ai/infra/buzz-deploy/-/blob/main/docs/product/gitlab-bridge/gitlab-buzz-bridge.html)。
- 决策：[ADR-0004：Desk-owned Agent Step](../../../docs/05-adr/0004-run-gitlab-sync-as-desk-owned-agent-step.md)（sync/route/outbox/binding/Desk 身份契约）；[ADR-0008：owner systemd timer 运行同步](../../../docs/05-adr/0008-run-gitlab-sync-from-owner-systemd-timer.md)（谁来启动，取代 ADR-0005 的 Desk heartbeat 与受限 runtime 门禁）；被取代的独立服务选择保留在 [ADR-0001](../../../docs/05-adr/0001-buzz-agent-setup-gitlab-sync-audience-and-identity.md)。public 与 private 项目都可同步；`gitlab_buzz_sync.py` 继承 launcher 的白名单环境变量并以 Desk 身份发布。受众精确对账已由 [ADR-0006](../../../docs/05-adr/0006-channel-membership-is-the-audience-consent.md) 废除。
- 用户故事与测试方案：skills 仓 `docs/04-user-stories/buzz-agent-setup-gitlab-buzz-sync.md`、`docs/plans/2026-09-13-buzz-agent-setup-gitlab-buzz-sync-test-plan.md`。
- 频道配置示例：[scripts/gitlab-buzz-sync.example.json](scripts/gitlab-buzz-sync.example.json)；普通 Desk 提示词片段（只解释同步状态，不运行同步）：[gitlab-buzz-sync.desk-prompt.md](gitlab-buzz-sync.desk-prompt.md)；timer 部署：Linux [systemd](systemd/README.md)，macOS [launchd](launchd/README.md)；Canvas 路由块：[gitlab-buzz-routing-canvas.md](gitlab-buzz-routing-canvas.md)；webhook 唤醒模板（未启用；ADR-0008 后 Desk 不再执行同步，该模板只保留为历史参考）：[workflows/webhook-wake-desk.yaml](workflows/webhook-wake-desk.yaml)。

### 术语对照

用户故事用中文说法，本页和测试方案用实现里的说法：

| 用户故事 | 本页 / 测试方案 | 含义 |
|---|---|---|
| Desk | Desk（`<business>-desk`） | 频道前台 Agent，普通 Buzz Agent；不运行同步，只解释同步状态、分诊与维护 Issue |
| Sync step | Desk-owned Agent Step | timer 启动的 `gitlab_buzz_sync.py` 子进程；使用 Desk 身份的凭据并以 `publisher_pubkey` 发布事实（ADR-0004 契约不变） |
| Sync timer | Linux `gitlab-buzz-sync-<channel>.timer` / macOS `ai.addx.gitlab-buzz-sync.<channel>` | owner 主机调度器，每 300 秒启动一次 `gitlab_buzz_sync_timer.py`（ADR-0008） |
| 根消息 | root | 每个 Issue、每个 MR 在频道里唯一的那条顶层消息，Thread 挂在它下面 |
| 绑定记录 | binding note | bot 在 GitLab Issue/MR 里写的评论，指向 root |
| 路由变化 / 内容变化 / 活动 | `change:routing` / `content` / `activity` | Issue 回帖类别 |
| 生命周期 / 更新 / 活动 | `change:lifecycle` / `update` / `activity` | MR 回帖类别；另用 `transition:reviewable|none` 表示这次是否刚变为可评审 |
| 即时通知 / 摘要 | instant / digest | 顶层通知的两种形式。2026-09-18 政策后 live 记录只有 instant；digest（每轮摘要）不再产生，机制保留给回滚 |
| 归并 | merge-in | 2026-09-17 的旧规则（关联 Issue 的 MR 事实各发一份）；2026-09-21 起改为只发进 binding 的一个 Thread、其余关联处一条交叉链接（xref），见 ADR-0015 |
| 起始时间 | `since` | 早于它创建且未绑定的对象不回灌 |

## 运行方式

```text
Linux: systemd --user gitlab-buzz-sync-<channel>.timer（OnUnitActiveSec=300，Persistent=true）
macOS: launchd ai.addx.gitlab-buzz-sync.<channel>（StartInterval=300）
  ──▶ gitlab-buzz-sync-<channel>.service（oneshot）
  ──白名单 launcher：校验 0600 env，丢弃白名单外的全部环境变量，secret 只在环境不进 argv──▶ /usr/bin/python3 <immutable-release>/scripts/gitlab_buzz_sync_timer.py（零参数）
timer 入口 ──gitlab_buzz_desk_runner.py（进程内，BUZZ_DESK_RUNNER_MANIFEST）──▶ owner manifest
runner ──gitlab_buzz_sync.py（manifest 固定 config/state）──▶ GitLab + Buzz
Desk 身份 ──root / 回帖（header 在末行，不 @ Agent）──────────▶ Issue / MR Thread
Desk 身份 ──即时通知──────────────────────────────────────▶ 频道顶层
Desk GitLab 身份 ──binding note（Buzz 链接 + 隐藏标记）─────▶ GitLab Issue / MR
runner ──gitlab_buzz_route_reply.py --scan-once（同一轮）───▶ 读取最新 Canvas + Desk facts
Desk 身份 ──reply-to + 唯一 Role p tag─────────────────────▶ 原 Thread
timer 入口 ──template_summary → gitlab_buzz_summary_publish（不变的 gates）──▶ 频道顶层一条摘要（政策后仅排空升级前遗留）
```

- **不经过 LLM**（[ADR-0008](../../../docs/05-adr/0008-run-gitlab-sync-from-owner-systemd-timer.md)）：同步、路由和摘要都是确定性代码。Linux `systemd --user` timer 用 `OnUnitActiveSec=300` 与 `Persistent=true`；macOS `launchd` job 用 `StartInterval=300`。两者都只把 `/usr/bin/python3 <immutable-release>/scripts/gitlab_buzz_sync_timer.py` 交给同一 owner launcher，入口不接受任何参数。启停命令见 Linux [systemd](systemd/README.md) 或 macOS [launchd](launchd/README.md)。
- **凭据与环境**：launcher 先确认 Desk 身份的 env 文件归当前用户所有、是 regular、非 symlink、权限恰为 0600，再 source 它并 unset 白名单外的每个已导出变量，只留 `HOME`、`USER`、`LOGNAME`、`PATH`、`LANG`、`BUZZ_RELAY_URL`、`BUZZ_PRIVATE_KEY`、`BUZZ_AUTH_TAG`、`BUZZ_DESK_RUNNER_MANIFEST`、配置指定的 GitLab token 变量，以及（启用多仓 runtime wrapper 时）owner 固定的 `BUZZ_GITLAB_PROJECT_TOKEN_MAP` 后 exec 入口。secret 只在进程环境（`/proc/<pid>/environ` 为 0400）里，从不进 argv（`/proc/<pid>/cmdline` 全员可读）。timer 与 Desk 同一 Unix UID、同一身份和 token；不新增身份、listener、loopback 或公开 Workflow tick。
- **一轮**：入口先调用一次 runner（按 `BUZZ_DESK_RUNNER_MANIFEST` 固定的顺序跑 sync，再跑 Canvas route gate）。runner 返回 `ok`、`degraded` 或 `locked` 后，入口逐个发布已认领的 summary request（单轮最多 20 条）：`template_summary` 只根据 facts 生成一条单行文本，再交给未改动的 `gitlab_buzz_summary_publish` 校验 `facts_sha256` 回执、字符过滤、计数／ref 一致性并严格回读。runner 或 publisher 首错即停止本轮，退出码 1。**2026-09-18 政策后 sync 不再产生新的 summary request**；这一段只为排空升级前遗留在 outbox 的 pending 摘要（确定性模板，发完即止）与政策回滚保留。
- **静默**：空轮零 Channel 消息；失败不写业务 Channel，只在 owner 私有日志留脱敏 JSON（Linux `journalctl --user`；macOS plist 指定的 0600 stdout/stderr）。同一时间只运行一轮：调度器不并发同一 job；绕过调度器并行直接运行入口时，per-project lock 让后到者返回 `locked`。
- **Desk 是普通 Agent**：Desk 用普通 buzz-acp 与 claude-agent-acp／codex-acp runtime，负责 Channel 入口、谁负责什么、分诊与 Issue 维护；它的 prompt 不运行 runner 或 publisher，Channel 消息（包括「@Desk gitlab sync」）不能触发同步，见 [gitlab-buzz-sync.desk-prompt.md](gitlab-buzz-sync.desk-prompt.md)。
- `gitlab_buzz_sync.py` 仍是 ADR-0004 的 Desk-owned Agent Step：子进程只继承白名单变量，以 Desk 身份发布。Desk LLM 与 timer 同属一个 UID 凭据边界；这里不宣称 GitLab token 或私钥对 Desk 不可见。
- config、cursor/outbox 与 route state 都按 repo／Channel 分域并持久化；状态持久化不要求额外 Linux 账号或常驻服务。同步和路由消息都使用 Desk 的 Buzz 身份。
- Issue/MR↔Thread 的跨系统绑定事实是 GitLab 里 bot 写的 binding note：
  - 可见正文是 `buzz://message?…` 链接；
  - 隐藏标记（HTML 注释）是 `gitlab-buzz-binding:v1 {"project_id","object":"issue|mr","iid","channel_id","root_event_id"}`；
  - 只认配置的 bot 写的 note。
- 每项外部写入之前先落盘 `PENDING`，严格 readback 后变成 `ACKED`。遗留 `PENDING` 必须先从目标侧证据恢复；全部必要投递 ACK 后才推进 cursor。
- **默认路由**：relay 0.2.1 已有 CLI `--reply-to`，所以不需要 Workflow，也不需要等 Workflow 的 `reply_in_thread`。本地 gate 不监听任何端口；每轮 runner 在同步命令之后运行 `gitlab_buzz_route_reply.py --scan-once`，读取 raw kind `40100` 完整 Canvas 事件和 Desk facts，用 Desk 身份在 canonical root 下回复。
- **Canvas 信任边界**：只取排序后最新的 Channel Canvas；本地重算其 NIP-01 event id、验证 BIP-340 signature，且作者必须在代码配置的 `canvas_admin_pubkeys` allowlist。若最新 Canvas 签名／作者不可信、表结构错误、Role 不存在或指向 executor，则整轮 fail closed，不能退回更老的可信 Canvas。Canvas 只放 route id、完整 header prefix、Role id 和 reason；publisher、admin allowlist、Role mention/pubkey、Agent prompt/skills/SaaS scope 都留在代码配置。
- **Role 准入**：Role Agent 的 `respond_to` 必须允许 Desk sender。`owner-only` 仅在 Desk 符合该 owner policy 时成立；不允许为“让路由能跑”静默改成 `anyone`。
- **HTTP 降级**：这是非默认路径；只有无法在 timer 中直接执行固定本地脚本时，才评估同一脚本的 `call_webhook` adapter。`https://gitlab-buzz.example` 是占位域名，不是现有服务；该路径引入 Channel 成员可读的 anti-abuse bearer、独立 sender 身份与公网 HTTPS ingress，必须单独过 L4，并由同 scope 的共享 mode lease 阻止它与默认本地 gate 同时写。**Naturehood 配置保持 fallback disabled。**

## 消息协议

**事实消息**（Thread 内的快照、活动回帖与顶层即时通知）机器 header 在**最后一行**，字段顺序固定、整行完整匹配；人读正文（图标 + 变化短语 + 标题链接，issue #78 起）在 header 之前。GitLab 来源的文本不能占据末行。存量消息（2026-09-18 及更早发出的）仍认物理首行 header，解析器双读。脚本要比较的字段保持机器可读，详见各节。

脚本专用字段挂在 header **必填段之后**的可选 trailer，不进人文读正文：`[desc:<12 位 hex 或 ->]`（描述指纹）、`[note:<id>]`（评论去重）、`[events:<key,key>]`（活动去重）；紧凑状态 overlay 还可带 `[reaction:<token>]`（可恢复的当前 reaction）、`[rev:<正整数>]`（同秒因果顺序）和 `[route:skip]`（只追加历史，不触发 Role）。顺序固定为 desc → note → events → reaction → rev → route，缺项整段省略；后三项只由紧凑状态覆盖层写入。Canvas 路由仍按必填段前缀匹配，trailer 在末尾不破坏 prefix。存量消息把 `desc` / `note:` / `events:` 写在正文；读回去重时 header trailer 优先，正文页脚仍双读。

**门牌（plaque，issue #78 起）**用于 MR、分支、里程碑和旧 Issue Thread 的顶层 root：纯主题卡，**没有 header**——`<图标> **<#iid 标题>**` / 项目 `path_with_namespace` / 末行裸对象 URL。末行 URL 是门牌唯一的机器身份，用于崩溃恢复找回与频道窗口扫描；可变字段不上门牌，门牌本身不覆盖。新 Issue 自 [ADR-0021](../../../docs/05-adr/0021-use-the-first-issue-fact-as-the-thread-root.md) 起直接用带机器 header 的首条状态事实作 root；其 id 同时是 binding 的 `root_event_id` 与紧凑状态卡的编辑目标。旧门牌与更早的事实 root 都照常回读，不迁移。

**紧凑状态卡（`compact_status_updates: true`）**：Issue/MR 第一个事实仍发一条 kind `9`；新 Issue 的这条就是 root。之后的 Git 状态、字段、提交、批准和 MR 流水线结果不再各发一份完整状态回复，而是向这条事实发布 kind `40003` 完整替换层。Buzz 0.5.23 的 `messages thread --event <root>` 不能单独证明每条 edit，因此同步器还必须从该 Thread 最早 GitLab 事实的时间起，分页执行 `messages get --channel <channel> --kinds 40003`，只合入指向本 Thread publisher-authored GitLab kind `9` 的覆盖层；不能只依赖 thread 查询。adapter 对所有 publisher-authored GitLab kind `9` 候选和每条入选 kind `40003` 强制核对 exact Channel、canonical NIP-01 id 与 BIP-340 signature，relay 不能靠伪造 `pubkey` / 高 `rev` 劫持编辑目标。可读正文末尾追加精确到秒的 UTC `状态记录`（按时间排序，最多最近 100 条，内容超限时优先保留最近记录），当前 Git 状态从首条事实开始就同时以 Desk reaction 表示；字段内容变化不覆盖已有状态 reaction。overlay header 用 `reaction` 明确记录当前 reaction，重启自愈不从可能保留的旧 headline 猜；同秒 edit 由单调 `rev` 排序，不拿 event id 哈希推断因果；只追加旧流水线等历史而保留当前 headline 的 overlay 带 `route:skip`，不会把当前 Role 再唤醒。若旧发布器曾为当前层与 `route:skip` 历史层写出相同 `rev`，读取器只在“恰好一个非 skip 层”时恢复该当前层；同 target、同 rev 且替换正文逐字相同的多个已签名事件也收敛为同一状态；其余 revision 冲突仍失败关闭。旧事实没有样式标题时，迁移记录其有界单行摘要，不拖停整个 Channel。edit、reaction 和所需提醒在写入前作为一组落 durable outbox，严格回读；未尝试的 continuation 可在重启后安全执行，已尝试但结果未知的写入仍失败关闭。存量 Thread 升级后以该对象最新的非评论状态事实为主卡，已有旧回复不删除。

Comment 不是状态：Issue/MR comment 始终继续作为 canonical Thread 中独立的 kind `9` 回复发送，不写进主卡的 `状态记录`，也不改变 reaction。飞书桥把 kind `40003` 映射成原飞书消息的 update，而 comment 仍映射成新消息；状态记录位于折叠区之外、GitLab 导航按钮之前。

唯一的状态类新回复例外是**真实注意力提醒**：pinned Buzz CLI 的 `messages edit` 不能给 kind `40003` 新增 `p` tag；当一次状态变化确实要通知责任人时，同步器另发一条带真实 `p` tag、UTC 时间和唯一变更标识的极简 `🔔 Git 状态需要你关注` 回复。它不是重复状态正文，飞书也会把它显示为一条极简提醒；没有收件人时不发。

**work_items URL（engineering/skills#101）**：新版 GitLab 给部分 Issue（含 Task）的 `web_url` 是 `<项目>/-/work_items/<iid>`。识别时 `/-/issues/<iid>` 与 `/-/work_items/<iid>` 都是 issue；新事实 root 的标题链接保持 GitLab 给的原样，header 提供机器身份。旧门牌末行按当时规则可能是规范的 `/-/issues/<iid>`，也可能是 `/-/work_items/<iid>`，两种都继续可读，不搬迁。此前门牌末行是 work_items 形式的 issue 会被判成「bound root is not a readable Desk root」，整轮报错。

### Issue Thread

- header：`[gitlab-notify:v1][object:issue][type:<t>][status:<s>][state:<opened|closed>][change:<routing|content|activity>][project:<id>][issue:<iid>]`，可选 trailer 固定顺序为 `[desc:…][note:…][events:…][reaction:…][rev:…][route:skip]`（各段均可省略；后三段仅紧凑状态 overlay）。
- type/status 取自 `type::` / `status::` 单值 label，缺失或多值记为 `unknown`。
- **新 Issue 的首个事实就是 root**（ADR-0021）：一条 kind `9` 状态卡含 `change:routing` header、标题链接和当前字段，binding note 绑它的 id；没有第二条“首次同步”回复。之后状态变化按配置回复 root 或以 kind `40003` 原位更新 root，评论仍回复 root。描述或人类评论带 origin（见 MR「origin 绑定」）且尚未绑定的新 Issue / Task 不另开 root，首个事实回复那个已有 Thread（Desk 门牌／事实，或人发的顶层消息，第一条作 binding；ADR-0014）。已有门牌 Thread 不搬迁。
- 快照正文（issue #78 起的样式，依次）：
  1. `<图标> **<变化短语>** · [#<iid> <标题>](<url>)`：首条为 `📋 **已打开**` 或 `📋 **已关闭**`，后续短语由 change 分类查固定映射表（状态流转 → 已打开|已关闭 / 归类变更 / 标题描述更新 / 字段更新），无 LLM；标题里的 `[ ] \` 转义，GitLab 标题无法伪造 markdown 链接；
  2. `labels <a,b> · assignees <x> · milestone <m>`：只列非空项，全空整行省略。描述指纹在 header `[desc:…]`，不进人读行。
- 列表或值为空时视为 `-`（回读缺省）；名字恰好是 `-` 的 label 写成全角 `－`，否则每轮都会被判成 label 变化重发。label、milestone 值里的 `·` 写成 `•`（MR 同理），分隔符不会被拆开或伪造。
- 上一版从 Desk 在该 Thread 里的最新快照消息还原：新样式解析器优先，2026-09-18 之前的旧 `key: value` 长格式仍能读回，不会因格式变化多发 update。
- 变化分三类：
  - type/status/state 变化是 `change:routing`；
  - 只改标题是 `change:content`；
  - 其他 label、assignee、milestone、描述变化是 `change:activity`，正文就是上面的快照。
- **评论**也是 `change:activity`：快照之后是人读评论，按 header `[note:<id>]` 去重：
  - `by: <作者用户名>`；
  - 空行；
  - GitLab 评论正文（保留换行，Buzz 按 Markdown 渲染；`@` / `nostr:` 中和；HTML 注释去掉；看起来像 header / `note:` / `events:` 的行做全角替换，不能伪造去重键）；最长 4000 字，超出截断并加 `…`；
  - 末行 header，带 `[note:<id>]`。存量评论仍可能在正文有 `note: <id>` 行，读回双读。
- 评论消息（Issue 与 MR 同）的**标题链接带 GitLab 评论锚点 `#note_<id>`**，点开直接落在那条评论上（issue #115）；非评论消息的标题链接仍是对象本身的 `web_url`。链接只用于展示，读回上一版事实和 note 去重都不读它。
- 不发的评论：system note、internal 或 confidential 评论、配置的 bot 写的 note，以及任何人写的 binding note（包括换 bot 之前旧 bot 写的）。

### MR Thread

- header：`[gitlab-notify:v1][object:mr][state:<opened|merged|closed|locked>][draft:<yes|no>][change:<lifecycle|update|activity>][transition:<reviewable|none>][project:<id>][mr:<iid>]`，可选 trailer 固定顺序为 `[desc:…][note:…][events:…][reaction:…][rev:…][route:skip]`（各段均可省略；后三段仅紧凑状态 overlay）。
- **MR 的事实只发进一个 Thread（2026-09-21 决策，[ADR-0015](../../../docs/05-adr/0015-deliver-an-mr-to-one-thread-and-cross-link-the-others.md)；取代 2026-09-17「归并」把事实各发一份进关联 Issue 的 Thread 的做法，那条规则从未写成 ADR）**：MR 的事实——首条事实、生命周期／更新回帖、评论、流水线与批准活动、kind 40008 Diff——**只发进 binding 指向的一个 Thread**。落点顺序：GitLab `closes_issues`（第一个）→ 分支名白名单（`<word>-<iid>-<slug>`／`<iid>-<slug>`）→ 第一条 origin → 分支族 → 自开 🔀 门牌；Issue 需存在，被 exclude（如 confidential）或 Thread 读不到／已满的跳过并计入 `skipped.excluded`，改用下一个候选；关联的 Issue 全被跳过又没有 origin 的 MR 不发布（也不自开门牌）。落点是 origin 而它的 Thread 已满（500 回帖）时，该 MR 按对象级隔离停摆（不写 binding、不发任何消息），不改投别处。首条事实 reply-to 落点 Thread 的根，后续事实 reply-to 该 MR 在此 Thread 的上一条（子链成组，多 MR 互不穿插）；binding note 绑 `(project, mr, iid) → 落点 root`，且在发出 MR 事实**之前**写入，崩溃重跑走 update 路径、不会另开 per-MR root。人类 @、`unmapped` 行与 `transition:reviewable` 只出现在这个 Thread，所以 Canvas route gate 只指派一次评审，发 Diff 不唤醒 Review Role。无关联或存量已绑定的 MR 维持 per-MR root（或走 origin／分支族），Diff 也留在绑定 Thread。
- **`related_merge_requests` 反查只用来生成链接**（ADR-0015）：反查命中的 Issue（Issue 文本里提到 MR 号就算）不再决定 MR 落在哪个 Thread，也不写 binding；它出现在首条事实的 `issues:` 行里，并收到一条交叉链接。只有反查命中的 MR 自开门牌（或进 origin／分支族）。反查命中但还没有 Thread 的 Issue（早于 `since` 的存量、停摆中）不会为了一条链接被建出 Thread：没有交叉链接，也不进 `issues:` 行；被 exclude 的跳过并计入 `skipped.excluded`。没有 binding 的存量 MR 出现流水线等活动时，反查命中同样不会让它绑进 Issue Thread，活动计入 `unbound`。
- **交叉链接（`change:xref`，ADR-0015）**：落点 Thread 之外的每个关联 Thread——第二个及之后的 closes／白名单 Issue、反查命中的 Issue（与前者合计最多 3 个 Issue）、第二条及之后的 origin——在 MR **首次出现**（新 MR 的那一轮）各收**一条**交叉链接，reply 在该 Thread 的根下。
  - 人读：`🔗 **MR 关联 · 事实在别处** · [!<iid> <标题>](<MR url>) · <作者>`，第二行 `之后的状态、评论、新提交、流水线和 Diff 只在那个 Thread：<落点 Thread 的 buzz:// 链接>`；标题照常做 `[ ] \` 转义、`@`／`nostr:` 中和。
  - header：`[gitlab-notify:v1][object:mr][state:<opened|merged|closed|locked>][draft:<yes|no>][change:xref][transition:none][project:<id>][mr:<iid>]`，`state`／`draft` 取发送那一刻的 MR 值，没有 `desc`／`note`／`events` trailer。**xref 不是事实变化**：没有快照，不带 @ 与 `p` tag，永远是 `transition:none`；读回「上一版事实」、事实链的接续点和「组 Thread」判断都跳过它，Canvas 路由前缀也匹配不上它。
  - 幂等：发之前读该 Thread，已有 Desk 发的、同 project 同 MR 的 `change:xref` 就不再发（见「去重与恢复」）。交叉链接先于 binding 写入：binding 前崩溃，重跑既不重发也不丢；binding 后崩溃走 update 路径，不补发。自开门牌的 MR 例外：门牌先发，门牌之后、交叉链接之前崩溃，重跑按门牌 URL 找回并走 update 路径，那条交叉链接不补发（best-effort，ADR-0015 后果）。读不到或已满的 Thread 跳过并计入 `skipped.excluded`，不停摆。
  - 之后的生命周期、评论、新提交、流水线、Diff 都不再发进这些 Thread。**已有 binding 的 MR 不扩散**：MR 有了 binding（自己的门牌、Issue Thread、origin、分支族）之后才出现的 Issue 或 origin（例如 MR 先有自己的 Thread、Issue 后建），既不收事实也不收交叉链接——后到的 Issue 不补交叉链接（ADR-0015 决定 4）。
  - 存量不搬迁：按旧规则已把事实发进多个 Thread 的 MR，binding 不变，已发出的消息不动，之后只在 binding Thread 更新；非 binding 的那些 Thread 停在最后一次的状态。
- **origin 绑定**：尚未有 binding 的 Issue（含 Task work item）/ 未关联 Issue 的 MR / 尚未有本地绑定的 milestone，描述或人类评论里可以指向本频道里已经存在的 Thread：Desk 门牌或事实，或**人发的顶层消息**（Feishu 镜像身份、别的 Agent 发的也一样；最常见的是讨论需求的那个话题，ADR-0014）。Writer 同时写两行（人读 + 机器）：

  ```
  buzz://message?channel=<本频道 UUID>&id=<root>&thread=<root>
  <!-- gitlab-buzz-origin:v1 {"channel_id":"<本频道 UUID>","root_event_id":"<64 位小写 hex>"} -->
  ```

  Reader 认 HTML 注释和 `buzz://message?…` 深链（`thread` 优先于 `id`，跟帖会走到该 Thread 的 root）。**HTML 注释是机器标记，可以指向人的顶层消息；裸深链只是提示（ADR-0011），仍只认指向本频道 Desk 门牌／事实的**：指向人类消息（人们常常只是贴一条人的消息链接「引用一下」，不能因此改变绑定）、读不到（Buzz CLI 退出码 1）、别的频道，或写成占位符／省略号（`buzz://message?…`、`&id=<root>`）的一律当普通文字忽略，不停摆、不记 warning。同一目标被标记和裸链各写一次（生产方写的两行）时合并成一条，按标记算。来源是描述 + 非系统／非机密／非 bot／非 binding 的评论。GitLab 文本不可信，因此：
  - 注释键集合固定；JSON 非法或字段不合法（**格式非法**）fail closed（该对象 stall，见「对象级隔离」）；同一文本里多条有效 origin 去重后**都回复**，不再因指向不同 Thread 而整轮失败；
  - `channel_id` 必须等于本同步频道；
  - **标记指向的根**必须是：kind 9、恰好一个 `h` 标签且等于本频道 UUID、**没有 `e` 标签**（顶层，不是回帖）、`buzz messages thread --channel <本频道> --event <root>` 读得到。**作者不限**（人、Feishu 镜像身份、别的 Agent）；Desk 自己发的消息只认门牌（末行是 GitLab Issue / MR / 里程碑 URL）或带合法同步 header 的事实，Desk 的普通发言（它也在讨论里说话）不是根。人的顶层消息只按结构判断，内容不当门牌／header 去解析。标记点名一条回帖时：Desk 的事实回帖、以及 Desk 门牌 Thread 里任何人的回帖，先走到该 Thread 的根（老规则）；根不是 Desk 的，点名的回帖不算顶层，见下一条；
  - **失败模式：回退，不停摆**（ADR-0014）。格式合法的标记指向的根不可用——读不到（被删或退出码 1）、是回帖（根也不是 Desk 的）、`channel_id` 不符、不是顶层 kind 9 频道消息、是 Desk 的普通发言——等同没有 origin：对象照常同步，**自开 root 并写 binding**（Issue 是事实卡，MR / milestone 是门牌），同步报告的 `origin_fallbacks` 记一条 `{project, object, iid, reason}`（每个对象每个原因一条，`status` 仍是 `ok`，对象没有落后）。仍按老办法的：标记**格式非法**（JSON 非法、键集合不对、`channel_id` 不是 UUID、`root_event_id` 不是 64 位 hex）只让**该对象**停摆（`stalled`，不写 binding、不发任何消息，见「对象级隔离」，ADR-0009），其余对象照常同步；CLI 退出码 2+（relay／认证失败）不是数据问题，仍整轮失败；裸深链不可用一律忽略。回退原因带对象编号，形如 `issue 81 origin root cannot be read (…)`、`issue 81 origin root is a reply, not a top-level message in this channel`：修复方法是把那个对象描述（或评论）里的标记改成话题的**顶层根**（不是话题中间的回帖 id），或直接删掉；binding 已写的对象不会因此搬家；
  - 系统 note、internal／confidential 评论、bot 评论和 binding note 不认 origin；
  - MR 落点优先级（ADR-0015）：GitLab `closes_issues`（第一个）→ 分支名白名单 → **第一条** origin → 分支族 → 自开门牌，`related_merge_requests` 反查不参与；MR 描述里指向多个 Thread 时，只有第一条 origin（binding）收事实，其余各收一条交叉链接。Issue / Task / milestone 在尚未绑定且有 origin 时绑到**第一条** origin Thread，否则自开 root（Issue / Task 为首条事实，milestone 为门牌），其余有效 origin 每轮各投一份本轮新事实（独立去重，mention / `transition:reviewable` 只在 binding 指向的第一条；这条只适用于 Issue / Task / milestone）；
  - **人的顶层消息作根**：binding note 的 `root_event_id` 就是那条消息，事实**回复在该话题里**，不再另开对象 root；不在人的消息下伪造门牌行，人的消息不会被编辑——首个事实的标题行本来就带 `[#N 标题](URL)`，话题里的人第一眼就能看到这个对象的标题和链接。一个话题里可以有多个对象（Issue、MR），每个对象的事实按各自 header 分开去重；`transition:reviewable` 每个话题仍只指派一次评审；话题里参与者写的、看起来像 header 的文字不算 Desk 事实（只认 Desk 发的）；绑定了人的话题的对象，话题回帖到 500 条上限时该对象 stall（沿用上限）。绑定根之后每轮都要读得到：话题根被删，该对象 stall（`bound root cannot be read`）；
  - 已有 binding 的对象不因后来加上 origin 而搬家（例如已绑在门牌线程的对象不会被挪进讨论话题）；Issue / Task / milestone 后来评论里的 origin 仍会各收一份本轮事实，**MR 不会**：已绑定 MR 后来评论里的 origin 既不收事实也不收交叉链接（ADR-0015）。
  Buzz Agent 从频道话题开 Issue / Task / Milestone / 未关联 MR 时，`gitlab-issue-sop` / `gitlab-mr` 在通过下面「origin 写入前检查」（根是本频道顶层 kind 9 消息，人的也行）时写这两行，包括话题根是人类消息；读不到、是回帖、别的 Agent 的无关发言或核对不了就省略两行。GitLab 网页是否把 `buzz://` 自动成链尚未验证（L4），但桌面端和 CLI 能打开。
- **分支族分组（2026-09-18 决策，issue #77）**：未关联 Issue 的 MR 按「分支族」共用一个 Thread。组键有两个：MR 的**源分支全文**（同分支必同组，不限作者）与**去后缀键**——源分支以 `-<目标分支名>` 结尾且该后缀恰等于此 MR 自己的目标分支时，去掉后缀的基名也是组键（`fix/x-staging`→`staging` 与 `fix/x-master`→`master` 归并为 `fix/x`）；去后缀键仅**同作者**命中，防误合。组内最早开出 per-MR root 的 MR 是组锚点（先到先得，槽位不迁移；历史 per-MR root 会被幂等补登记为锚点，之后的 sibling 直接加入）：sibling 的 binding note 绑 `(project, mr, iid) → 组 root`，事实在组 Thread 内自成子链（机制同 merge-in），组 Thread 读不到／已满时退回自开 root。`transition:reviewable` 每个组 Thread 只指派一次（同「只指派一次评审」决策；单 MR 独占 Thread 的既有行为不变）。
- 快照正文是样式化紧凑格式（issue #78 起，演进自 2026-09-17 的紧凑格式；字段之间用 ` · ` 分隔）：
  1. `<图标> **<变化短语>** · [!<iid> <标题>](<url>) · <作者>`：短语由 change/transition 查固定映射表（👀 可评审 / 🔀 新建 · 状态 / 🔄 状态流转 / 📦 新提交 / ✏️ 字段更新 / 活动类见下），标题里的 `[ ] \` 转义；
  2. `<源分支> -> <目标分支> · sha <head 提交前 12 位，取不到时 -> · labels <a,b> · reviewers <x> · assignees <y> · milestone <m>`：只列非空的项。描述指纹在 header `[desc:…]`（空描述为 `-`），不进人读行。
- **无关联 MR 的自有 Thread root 是 🔀 门牌**（issue #78 起）：首个 lifecycle 事实 reply 门牌；分支族锚点同理是组内最早 MR 的门牌。binding note 绑门牌 id，MR 组键注册也指向门牌。
- 读回时 sha 按前 12 位比较；缺省的 labels / reviewers / assignees / milestone 视为 `-`。新样式解析器优先；2026-09-17 紧凑格式与更早的 `key: value` 长格式仍能读回，不会因为格式变化多发 update。
- 快照之后的可选行：
  - `issues: <Buzz 链接,…>`：只在 root 上出现，见「Issue 链接」；
  - `unmapped: <用户名,…>`：有候选人没被 @ 时出现，是人读正文的最后一行（header 仍在整条消息末行）。
- 变化分三类：
  - state 或 draft 变化是 `lifecycle`；
  - 新提交（sha）、label、reviewer、assignee、milestone、标题、描述、源或目标分支变化是 `update`；
  - MR 评论、MR 流水线终态、批准是 `activity`。
- `transition:reviewable` 是 Bridge 根据上一条已确认快照计算的转移事实，只用于非 draft 新建、draft→ready、closed→opened 且非 draft；其余包括 `locked`→`opened`、update 和 activity 都是 `transition:none`。Desk route gate 不根据当前 state 猜测上一状态。
- **活动回帖的格式**：
  - 评论：快照之后是 `by:`、空行、Markdown 正文，末行 header 带 `[note:<id>]`；没有 `events` trailer，按 note id 去重。
  - 流水线与批准：快照标题已经说明通过/失败/已批准，不再重复 `event:` 行；流水线标题固定为 `<图标> **#<pipeline_id> <通过／失败／取消>** · …`，ID 紧跟图标、位于事件描述之前；知道操作人时加 `by:`，流水线失败时加 `jobs: <失败 job 名,…>`，去重键在 header `[events:<key>]`。MR Thread 里按整个 Thread 的 events 去重，所以流水线重试后以同一终态结束不会再发。
- **活动回帖不是快照**。判断 lifecycle/update 时，只和 Thread 里最新一条 root、lifecycle 或 update 消息比较。活动回帖用本轮读到的 MR 快照渲染，所以同一轮里 draft→ready 加流水线结束，也不会吞掉 lifecycle 回帖和它的 @。
- **MR 流水线**只指 merge request pipeline（ref 为 `refs/merge-requests/<iid>/head`），全部终态进 MR Thread。同一源分支上的 branch pipeline 不通知（2026-09-18 政策）；默认分支失败是即时通知。
- MR 未绑定时，流水线和批准活动计入 `unbound`，不发。
- 命中 `exclude` 的 MR 不发任何 Thread 消息，流水线和批准活动也不发：
  - 活动先判 exclude，再看 binding 和 Thread，所以 excluded MR 的 binding 损坏不会让整轮失败；
  - 每个 MR 每轮只计一次 `skipped.excluded`；
  - 本轮没被列出、也没有 binding 的 MR，活动照旧计入 `unbound`，这时不判 exclude。
- **Issue 链接**写在 MR 的首条事实上（`issues:` 行）：
  - 取 GitLab `closes_issues` 返回的、`project_id` 等于本项目、且本频道已绑定的 Issue；再加 `related_merge_requests` 反查命中的 Issue 的 Thread（ADR-0015：反查只用来生成链接）；
  - 其他项目的、未绑定的、读回 404 的 Issue 都不链接；
  - `issues:` 链接行不看源分支名；分支名白名单只用于落点判断，不生成链接。

### origin 写入前检查（Writer 必读）

origin 的正确性是**写入方**（`gitlab-issue-sop`、`gitlab-mr`、里程碑治理，以及手写的人）的责任。2026-09-19 事故：项目 1021 的 issue 81–99 被批量写入指向**人类消息**的 origin，而当时的规则只认 Desk 门牌，skill 频道整轮报错约 1 小时（engineering/skills#101、#103）。之后 ADR-0009／0011 把危害压到一个对象，ADR-0014（2026-09-20）又把规则改成：**标记可以指向人的顶层消息**，讨论需求的话题就是 Issue 通知该去的地方（skills#125：Issue 通知另开了门牌线程，没回到讨论里）。所以：

1. **先检查，再写。** Agent 是在**当前话题**里被要求开 Issue / Task / MR / Milestone 时，只有确认要写的根事件同时满足下面几条，才写 origin（**两行都写**，话题根是人的消息也一样）：
   - kind 9；
   - 恰好一个 `h` 标签，且等于本频道 UUID；
   - 没有 `e` 标签（顶层，不是回帖）；
   - 读得到：`buzz messages thread --channel <本频道 UUID> --event <root>`（也可以 `--link <buzz://message 链接>`）返回的事件里有 `id` 等于该根的那条；
   - 作者若是本频道 Desk（`pubkey` 等于同步配置里的 `publisher_pubkey`），内容必须是门牌（最后一行是 GitLab Issue / MR / 里程碑的 URL）或以 `[gitlab-notify:v1]` header 开头的同步事实；Desk 的普通发言不是根。人、Feishu 镜像身份、别的 Agent 发的顶层消息不看内容。

   这与同步里的 `origin_top_level_root`（人和别的 Agent）、`origin_canonical_root`（Desk 自己的、以及裸深链）是同一条规则。**写话题的顶层根**（读 `messages thread` 返回的、没有 `e` 标签的那条），不要写话题中间某条回帖的 id：人的话题里点名回帖会被当作「不是顶层」而回退成自开 root（Issue 为首条事实）。
2. **省略。** 不是在当前话题里被要求开的（批量导入、无来源）、根读不到、是回帖、是别的 Agent 的无关发言，或者上面任何一条核对不了——**两行都省略**，包括没有 HTML 注释的裸链接。同步会给这个对象自己建门牌并在 GitLab 里写 binding，人从门牌就能回到 Buzz。批量创建时对每个对象各自检查，不要把同一个未核实的根复制到一批对象。
3. **裸 `buzz://message?…` 链接只是提示**（ADR-0011）：完整且指向本频道 Desk 门牌／事实的会被当作 origin，不需要 HTML 注释，放进反引号、括号或引用块也一样会被识别；指向人类消息、读不到或占位符／省略号写法的会被忽略，不会让对象停摆，也不会把对象绑到那个话题（要绑到话题，只有 HTML 标记可以）。仍不要在描述或评论里为了「引用一下」贴人类消息的 `buzz://message` 链接；要引用就写频道名加一句话描述。
4. **已经写错或想撤回**：格式非法的标记（JSON 坏了、键不对）会让**该对象**停摆，修法是改对或删掉标记，下一轮自动恢复；格式合法但根不可用的标记不会停摆，对象照常自开 root 并在报告的 `origin_fallbacks` 里留一条，把标记改成话题的顶层根或删掉即可（binding 已写的对象不会搬家）。想让已绑到话题的对象改回自己的 Thread：删掉描述里的标记，再删掉 GitLab 里同步 bot 写的 binding note，下一轮会新开对象 root（Issue 为首条事实，MR／里程碑为门牌）。


### 顶层通知

**2026-09-18 通知政策（频道 owner 确认，`NOTIFIED_OBJECTS` 是权威清单；同日 13:16 恢复 tag/Release）**：脚本只为以下对象产出记录——Issue、MR、note（Issue/MR 评论）、milestone、pipeline（MR 终态 + 默认分支失败）、deployment（仅失败/受阻）、tag（建/删/移动）、release（新建/删除）、access_token。其余类型（push、feature_flag、wiki、member、commit/snippet 评论、pipeline/deployment 的 happy path）**不产生记录**：轮询路径直接丢弃，webhook 路径返回 unsupported，不再唤醒、不再发消息。

- header：`[gitlab-notify:v1][object:<pipeline|deployment|access_token|milestone|tag|release>][event:<e>][project:<id>]`。脚本只发这 6 个顶层 object 值：

| object | event | 何时发 |
|---|---|---|
| `pipeline` | `failed` | 默认分支流水线失败，即时 |
| `deployment` | `failed` / `blocked` | 部署失败或受阻，即时；`running` / `success` / `canceled` 不通知；频道可用 `mute_events` 屏蔽（见「频道配置」） |
| `access_token` | `expiring` | 0–7 天内到期的有效 token，每个 token 每个 UTC 日一条，直到到期；需要 Maintainer 权限 |
| `milestone` | `created` / `opened` / `closed` / `reopened` / `expired` / `destroyed` | 里程碑事实，**reply 🎯 门牌 Thread**（不是顶层单条） |
| `tag` | `tag_created` / `tag_deleted` / `pushed` | tag 新建、删除或移动（发布信号，13:16 恢复），即时 |
| `release` | `created` / `deleted` | 新 Release / 删除 Release（13:16 恢复），即时；`updated`（改发布说明）不通知 |

- **不再通知的类型**（2026-09-18 政策砍掉，历史版本发过）：`feature_flag`（开关变化）、push / 分支 pipeline / 部署的 happy path、wiki、成员变化、commit/snippet 评论、每轮摘要（`object:activity digest`）。tag 与 Release 曾在当日上午版本被砍、13:16 经 owner 确认恢复（发布信号）。被砍类型的渲染与路由代码保留在脚本里（可回滚），但 live 数据不再进入。
- **即时通知**正文依次是（issue #78 起样式化）：
  - pipeline 固定为 `<图标> **#<pipeline_id> <短语>**`（如 `❌ **#224905 主分支流水线失败**`），ID 紧跟图标、位于事件描述之前且不在末尾重复；其他类型仍是 `<图标> **<短语>** · <标题>`（如 `🔴 **部署失败** · pages-publisher`）；短语查固定映射表；
  - 裸 URL 行（自动成链）；
  - `ref:`（有分支或 tag 时）、`by:`（知道操作人时）、`commits:`（push 类带提交数时）；
  - 去重键在 header `[events:<key>]`（存量消息仍可能在正文有 `events:` 行，读回双读）。
- **里程碑 Thread（2026-09-18 政策新增）**：每个 milestone 一个 🎯 门牌 Thread（门牌规则见「消息协议」；URL 是 `<web_url>/-/milestones/<iid>`）。里程碑事件（创建/启动/关闭/重开/到期/删除）作为事实 reply 门牌，正文同即时通知样式（固定短语表，无 LLM），按 `events:` key 去重。milestone 没有 GitLab notes，绑定不在 GitLab 侧，而在本地 `milestone-bindings-<project_id>.json`；状态文件丢失时按门牌 URL 全频道搜索找回原 Thread（同 Issue/MR root 找回语义，命中搜索上限按首错停轮），不开重复 Thread。events API 的 milestone 事件缺 `target_iid` 时按标题精确匹配项目 milestones 列表归位；匹配不到记 `degraded[]`（`<project>:milestone_identity:N unresolved`）并计入 `skipped.milestone_identity`，不静默丢。
- **摘要机制（保留但不投喂）**：归因梯子（Issue→MR→分支 Thread）与 summary request → `template_summary` → `gitlab_buzz_summary_publish` 的整条链路仍在代码里且被单测覆盖（合成 digest 记录），供政策回滚；live 记录不再产生 digest placement，新频道升级后 outbox 里遗留的 pending 摘要会被 publisher 按确定性模板排空一次。机制细节见本节历史描述（保留在 git 历史与本 MR 的上一版）。
- **key 形式**：`event-<GitLab event id>`（issue/MR/note/milestone/tag 活动流事件）、`pipeline-<id>-<status>`、`deployment-<id>-<status>`、`release-<project>-<tag 的 hash>`、`access_token-<id>-<UTC 日期>`。Release 的 key 带项目 id，同一频道两个项目的同名 tag 各发各的。

### 文本中和与 @

- GitLab 来源的文本（标题、label、用户名、分支、评论摘录）发送前做两处替换：
  - `@` 转成全角 `＠`；
  - `nostr:` 转成 `nostr：`（全角冒号）。

  这样 Buzz CLI 不会把它们解析成 mention 或 `p` tag。
- 脚本只 @ 人、从不 @ Agent。@ 由每类消息各自的责任人决定（下表；决策见 buzz-deploy ADR-0018）：候选来自 GitLab 结构化字段，评论里只认逐字等于项目成员用户名的 `@username`。所有 @ 走同一道闸门，见下文「可 @ 的人」。

  | 消息 | @ 谁 | 不 @ |
  |---|---|---|
  | Issue 新建 | 已映射的 assignee | 新建时已关闭 |
  | Issue 新增 assignee | 只 @ 新增的人 | 无变化 |
  | Issue 评论 | 评论点名的项目成员 → assignee → 作者；去掉评论人 | 评论人不是项目成员时，正文点名不算 |
  | MR 变可评审 / 新增 reviewer | 见下文 **@ 全集**、**新增 reviewer**（不变） | 见下文 |
  | MR 合并 / 关闭 | MR 作者 | 操作人（`merge_user`/`merged_by`/`closed_by`）就是作者；第一次同步到时已是终态 |
  | MR 评论 | 评论点名的项目成员 → 作者 → reviewers；去掉评论人 | 同 Issue 评论 |
  | MR 被批准 | MR 作者 | 批准人就是作者 |
  | MR 终态流水线失败 | 触发人（`GET pipelines/:id` 的 `user`，每条失败流水线至多读一次） | success、canceled 等非失败终态 |
  | 默认分支流水线失败（顶层） | 触发人 | — |
  | 部署失败 / 受阻（顶层） | 部署人（deployments 列表的 `user`） | — |
  | tag、Release、milestone、即将过期的 access token | 不 @（广播类；access token 的 API 不返回人类所有者） | 一律不 @ |

  没有对应消息的对象（push、feature_flag、wiki、member、commit/snippet 评论、digest）谈不上 @。
- **可见提示行**（ADR-0012）：带 `p` tag 的消息在机器头部行之上多一行 `🔔 通知 @周旭兆 @刘海旗`，按 @ 的顺序、去重；`p` tag 仍只由闸门产生、仍用 `--mention` 发送、回读仍核对完整集合，这一行只是把结果显示出来。每个人的显示名取自 `buzz users get --pubkey`（每轮对当前频道成员读一次），写成 `@显示名` 只在它是一个单纯的词（Unicode 字母数字、`.`、`-`，至多 32 个字符，无空格／冒号／引号）、不是保留词（`all` `everyone` `here` `channel`）、且**不被任何其他频道成员的显示名包含**（大小写不敏感）时；其余情况（重名、互相包含、含特殊字符、没有档案、读档案失败）退回**没有 `@` 的纯文字**用户名（不在 `people` 里的用 pubkey 前 8 位），同样走文本中和。读档案失败不会让整轮失败，摘要里的 `name_fallbacks` 记次数。没有 `p` tag 的消息不加这一行；正文接近 CLI 大小上限（65,000 字节）时省略这一行；`note:`/`events:` 机器行仍是最后一个正文行，头部行仍是最后一行；已发出的旧消息不改。
- **每条消息各自计预算**：一条消息最多 @ 3 人、去重；点名的人排在结构化来源前面；不做同一 Thread 内跨消息的去重。
- **评论里的 `@username`**（只在 Issue / MR 评论上识别）：
  - `@` 前一个字符不能是字母、数字或 `_ . - @ / :`（邮箱、URL、`@group/子组` 都不算）；用户名取 `[A-Za-z0-9_.-]+` 后去掉结尾的 `.`、`-`；
  - 必须**区分大小写地逐字等于**本项目 `members/all` 里某位成员的用户名，不做前缀、大小写归一、显示名或模糊匹配；
  - **评论人自己必须是项目成员**，否则正文里的点名一律忽略（公开项目里外人不能借评论 @ 频道成员）；结构化来源不受影响；
  - 围栏代码块、行内代码、`>` 引用行、HTML 注释里的 `@` 不算；`@all`、`@everyone`、`@here`、`@channel`、`nostr:` 不算；评论人自己、project/group bot 用户名不算；
  - 不产生 `unmapped:` 行：点名本来就在评论正文里可见。
- 人类 @ 只出现在 binding 指向的 Thread：MR 的事实本来就只发进这一个 Thread，其他关联 Thread 的交叉链接不带 `p` tag；Issue / Task / milestone 的多个 origin 里，只有 binding（第一条）带 `p` tag。
- **@ 全集**：MR 变为可评审时。可评审只有一条规则：当前是 `opened` 且非 draft，并且上一次同步记下的状态是下面之一：
  - 没有（第一次同步到）；
  - draft；
  - `closed`（重开）。

  全集的算法：
  - 候选是 reviewers ∪ `access_level ≥ 40` 的项目成员；
  - 排除作者和 GitLab bot 成员。`members/all` 的响应里没有 bot 标记，脚本按用户名识别 project/group access token 的 bot 用户（形如 `project_<id>_bot_<hash>` / `group_<id>_bot_<hash>`）；
  - 候选里可 @ 的人被 @，其余写进 `unmapped:` 行。
- **不 @ 全集的情况**：
  - 第一次同步到时已是 `merged`、`closed` 或 `locked` 的 MR：root 不 @ 任何人，也不写 `unmapped:`；
  - 之后从 `closed` 重开且非 draft：算变为可评审，lifecycle 回帖 @ 全集；
  - 重开时仍是 draft：等 draft→ready 再 @；
  - `locked`→`opened`（例如合并失败后 GitLab 解锁）不算变为可评审，不再 @。
- **新增 reviewer**：只在 MR 是 `opened` 且非 draft 时 @，只 @ 新增、可 @、且不是作者或 GitLab bot 用户名的 reviewer；这条回帖不写 `unmapped:`。draft 状态下新增的 reviewer 这时不 @，等 draft→ready 时随全集一起 @。
- **可 @ 的人**：生效的 `people`（配了 `people_file` 时是共享文件加内联 `people`，内联优先）里有、**当前是频道成员**（每次发送前新读）、频道角色是 owner/admin/member（`guest` 和 `bot` 不算）、不是 Agent。这道闸门对上表每一行都一样，发送后回读核对完整 `p` tag 集合。它与责任人 helper 共用同一个 `HUMAN_ROLES`（`buzz_responsible_mentions.py`）：频道 admin 是能处理事项的真人管理员，2026-09-21（skills#136）前被误判成 `not_human_member`，reviewer／assignee 是 admin 时既没有 `p` tag、`unmapped:` 里还写着他。
  - 映射了但不是成员的、频道角色是 `guest` 或 `bot` 的（Agent 加入频道就是 `bot`），以及未映射的，都只写进 `unmapped:`，写成 `用户名(原因)`：`not_channel_member`、`not_human_member`（guest／bot）、`profile_not_found`。
  - 「从不 @ Agent」靠频道角色保证，不依赖可选的 `agent_pubkeys`。`agent_pubkeys` 是配置校验时多加的一道防线：`people` 映射到其中的 pubkey 时直接拒绝配置。
- **成员要求**：Buzz 拒绝 @ 非频道成员，所以要被提醒的人必须先加入频道；不加入时只会出现在 `unmapped:` 里。

## 频道配置

配置是严格校验的：

- 顶层只允许下表这些键，`gitlab`、`buzz`、`diff` 里也不允许未知键，否则 `status: error`；
- `exclude` 必须是列表。

| 键 | 必填 | 说明 |
|---|---|---|
| `channel_id` | 是 | 频道 UUID（小写） |
| `publisher_pubkey` | 是 | Desk identity 的 64 位小写 hex pubkey；启动时从 launcher 注入的 Desk `BUZZ_PRIVATE_KEY` 推导并精确核对，任何外部调用前不符即失败 |
| `since` | 是 | 起始时间（UTC）。早于它创建且没有绑定的对象不回灌 |
| `audience` | **已废除（ADR-0006）** | 频道成员身份本身即受众授权；配置中不得再出现 `audience`（含 `audience.allowed_pubkeys`），出现即被 `validate_config` 拒绝，owner 需删除该块 |
| `include_confidential` | 否，默认 `false` | 是否同步 confidential Issue |
| `compact_status_updates` | 否，默认 `false` | `true` 时把 Issue/MR 后续状态、字段、提交、批准和 MR 流水线原位编辑到同一事实卡并追加带 UTC 时间的「状态记录」，reaction 表示当前 Git 状态；comment 仍发独立消息。生产 Channel 推荐开启；默认关闭只为兼容尚未升级 edit/reaction 消费端的部署 |
| `exclude` | 否，默认 `[]` | 每项是 `{"assignee_username": "…"}` 或 `{"label": "…"}`；命中的 Issue/MR 整个跳过 |
| `mute_events` | 否，默认不屏蔽 | 本频道不要的**顶层即时通知**，每项 `"<object>:<event>"` 或 `"<object>:*"`。可屏蔽的只有脚本真会发的组合：`pipeline:failed`（默认分支失败）、`deployment:failed` / `deployment:blocked`、`access_token:expiring`、`tag:tag_created` / `tag:tag_deleted` / `tag:pushed`、`release:created`（Release 删除通知目前只有 webhook 路径会产出，轮询不发，所以不能屏蔽）；object 或事件名写错、写了 issue/mr/note/milestone/sync 等线程与系统记录，整份配置被 `validate_config` 拒绝（`status: error`，零发送），不会悄悄什么都没屏蔽。命中的记录在产出时丢弃，不发也不占去重键；**取消屏蔽后不会回补屏蔽期间的通知**（cursor 只看扫描时间；例外：`access_token:expiring` 是当日快照，取消屏蔽后下一轮会发当天仍在 0–7 天到期窗口内的 token 通知）。MR 流水线结果是 MR Thread 里的事实，不受 `pipeline:*` 影响。例：`["deployment:blocked"]`（手动部署 job 在等人点，pipeline 状态是 `manual`，每次 main 推送来一条）。注意手动 job 失败时 pipeline 仍是 `manual`，不会有「主分支流水线失败」，屏蔽 `deployment:failed` 就等于没人收到这类部署失败。只对轮询路径生效：webhook 归一化目前没有生产调用方，将来接上发送方时需要一并接 mute |
| `diff` | 否 | `{"enabled": false, "private": false}`。非 public（private、internal）项目还需要 `private: true` |
| `agent_pubkeys` | 否 | 频道里 Agent 的 hex pubkey 列表；`people` 映射到它们时拒绝配置。不填也不会 @ Agent：频道角色为 `bot` 的成员一律不 @ |
| `people` | 否 | GitLab 用户名 → Buzz hex pubkey，只填人；不能填 Desk 或 Agent。配了 `people_file` 时是本频道的局部覆盖，同名以这里为准 |
| `people_file` | 否 | 本机共享映射文件的**绝对路径**：一份 `{"<gitlab用户名>": "<64hex>"}` JSON 对象，多个频道配置共用一份；必须是 owner-only 0600 普通文件、非 symlink，路径含 `..` 或符号链接即拒绝。生效的 people = 共享文件 ∪ 内联 `people`（同名内联优先），并对合并后的整份映射跑与内联 `people` 相同的 humans-only 规则（用户名、64 位 hex、不许是本频道的 Desk/`agent_pubkeys`），报错里不带 pubkey 值。文件缺失、权限不对、不是 JSON 对象、条目不合格都是 fail closed：`validate_config` 拒绝整份配置，Syncer 取用时再校验一次（校验后被换掉的文件同样被拒），不会当成「没有人」悄悄放行。每轮重新读取，改完无需重启 timer |
| `gitlab.base_url` | 是 | GitLab origin（https；http 仅限回环地址） |
| `gitlab.token_env` | 是 | 存 project access token 的环境变量名：大写，不以 `BUZZ_` 开头，不与 CLI 环境白名单重名。同频道多份配置时每份各用一个变量 |
| `gitlab.bot_user_id`、`gitlab.bot_username` | 是 | token 对应的 bot 用户，`GET /user` 必须一致。project access token 的 bot 用户名形如 `project_<id>_bot_<hash>` |
| `gitlab.projects` | 是 | 项目 id 列表，不能重复；全部项目的 id 与 visibility 在任何写入前先读完，每次 Buzz message/diff 与 GitLab binding note 新建或重试边界再读该事实所属项目，本轮可见性变更则在落新 PENDING 或重试前 fail closed。锁按每个「频道 + 项目」获取，缓存与 outbox 按整份配置 scope 隔离 |
| `buzz.cli_path`、`buzz.cli_sha256` | 是 | `buzz-0.5.23` 原始 ELF 的绝对路径与 SHA-256；不能是 symlink、wrapper 或 `~/.local/bin/buzz` |

### people 的生成器（buzz-deploy #51、#77）

`people` 可以用 `scripts/gitlab_buzz_people_generate.py` 生成，不必逐人手填：它把 GitLab 项目成员的用户名，和 bridge 知道的「邮箱 → Buzz 公钥」按 `username@a4x.io` 连起来。邮箱有两个来源，**互斥、二选一**：

| 来源 | 参数 | 定位 |
|------|------|------|
| bridge 的签名接口 | `--people-api-base-url` + `--signer-env-file` | **推荐**：每次运行按需取，只含该频道的现存成员，绑定变了下次就是新的 |
| `ops export-people` 的导出文件 | `--export` | **回退 / 离线**：bridge 接口还没部署、或本机连不上 bridge 时用；要有生产权限的人导出，绑定一变就过期 |

**推荐：API 模式**（owner 手动跑）：

```bash
python3 scripts/gitlab_buzz_people_generate.py \
  --config <频道配置.json> \
  --people-api-base-url https://<bridge 的 BIND_PUBLIC_ORIGIN> \
  --signer-env-file <0600 env 文件，内含 BUZZ_PRIVATE_KEY> [--dry-run]
```

- **前置**：bridge 已部署带 `emails` 的人员接口（infra/buzz-deploy#77），并且开了 `CHANNEL_PEOPLE_EMAILS_ENABLED`。**部署并打开开关之前用不了 API 模式，先用下面的 `--export` 回退。**
- **接口**：对配置里的 `channel_id` 发 `GET {base_url}/bind/api/channels/{channel_id}/people`，带 NIP-98 签名（kind 27235，`u` 与 `method` 各按本次请求签，时间是发请求那一秒）。响应 `{"channel", "as_of", "people": {pubkey: open_id}, "union_ids", "emails": {pubkey: [邮箱…]}}`；`emails`（小写、去重、排序）只在 bridge 开了开关时才有，且只含**该频道现存成员**——所以映射天然按频道限定：不在这个频道里的人不会被映射，也就不会被 @。
- **签名者必须是该频道的 owner 或 admin**（接口对别人一律 404）。`--signer-env-file` 是绝对路径、owner-only 0600 的普通文件（符号链接、权限过松、缺 `BUZZ_PRIVATE_KEY` 都拒绝），只读其中的 `BUZZ_PRIVATE_KEY`（hex 或 nsec）；它和 `buzz_feishu_group_sync.py` 的 `people_api.signer_env_file` 是同一类文件，可以是同一个。key 只在脚本进程里签名，**不进任何子进程环境、不写盘、不打印**，GitLab token 也不会发给 bridge。
- `--people-api-base-url` 必须恰好是 `https://主机[:端口]`（没有路径、查询、userinfo 和结尾斜杠），且与 bridge 的 `BIND_PUBLIC_ORIGIN` 逐字一致——签名里的 URL 是它加请求目标，差一个字符验签就过不了（401）。传输层复用 feishu 同步的 `_http_get`：不跟随重定向、不走环境代理、只认 http(s)、正文超过 1 MiB 拒绝、超时 15 秒。
- **fail closed，整次运行、什么文件都不改**：响应里没有 `emails`（bridge 没开开关）、状态码不是 200（401 验签失败、404 签名者不是 owner/admin 或频道不存在、5xx …）、超时或网络错误、响应不是合法 JSON / 不是这个频道 / 有畸形的 `people` 或 `emails`、响应过大——一律报错退出（退出码 1）。报错点名是哪个频道（频道 id 不是敏感信息），**不回显响应正文、邮箱、key 或 Authorization 头**。
- 匹配规则：`username@a4x.io` 精确匹配、localpart 兜底（`--domain` 改域名）。localpart 兜底时**不同邮箱**（可能是不同的人）对到不同 key，仍然歧义即 `unmapped`、绝不猜；**同一邮箱绑了多把 key 的处理见下一条**。**`--config` 必须是绝对路径**的 owner-only 0600 文件（与 `load_config` 同款校验）。
- **同一邮箱绑了多把 key：选一把**（2026-09-20 决策，原则是「要保证用户可以收到消息」）：同一个邮箱绑了两把 pubkey 只可能是同一个人有两把已验证的 key，@ 哪一把都能通知到他，丢掉反而让他一条都收不到（以前是不写、进 `unmapped`）。**只适用于 API 模式**，也只适用于来自同一个邮箱的多把 key（用户名精确匹配到的地址；或 localpart 兜底时只找到一个邮箱）；不同用户名互不相干。写一把：先把会被拒的（Desk / agent / publisher）剔除，先剔除再选，所以它们不会因为「频道最多」或 hex 最小被选中；剩下的里选**在最多个已配置频道里是成员的那把**（某个频道接口响应的 `emails` 里出现这把 key 就算该频道的成员，不要求它在那个频道绑的也是这个邮箱；同一频道被多份配置指向只算一个），并列取 **hex 字典序最小**的，所以同样的输入永远得到同样的输出。`warn` 里加一条，只写用户名、选中那把的前 8 位和放弃了几把（如 `alice: address maps to several keys, chose 22222222, dropped 1`），不写邮箱、不写完整 pubkey。手填优先、不删除照旧：已有条目原样保留，手填的是候选之一就不警告。**`--export` 模式不变**：导出是 `{邮箱: pubkey}`，一个邮箱只有一个值，本来就没有这种情况。
- bot 判定是完整命名边界 `project_<id>_bot_<hex>` / `group_<id>_bot_<hex>`：`project_1312_botany` 这样长得像 bot 的普通用户名照常入列。
- **手填条目永远赢**（同名不同 pubkey 只警告不覆盖），**永不删除**任何条目；映射到 Desk / Agent pubkey（含 publisher）的条目拒写（否则下轮 `validate_config` 拒绝整个配置）。两个来源都一样。
- 配置写回复刻 `atomic_write_json` 语义（0600 tmp + `os.replace` + 目录 fsync），写前跑同款 owner/0600/非 symlink 校验，写后整份 `people` 过 humans-only 规则——写出的配置 sync 一定能加载。整次运行持有 `<config>.people.lock` 的 flock（非阻塞）：并发第二实例立即报错退出，读-合并-替换不会静默丢失另一进程写入的条目。config 每轮新鲜加载，改完无需重启 timer；immutable release 无需变更。
- stdout 只报计数、用户名与布尔标志（`added / kept_manual / rejected_agent_key / unmapped / warn / changed / written / dry_run`，另有 `source`：`api` 或 `export`；API 模式再加 `channels`：取了几个频道），不出现任何邮箱、pubkey、头或响应正文。成员拉取按 `per_page=100` 分页，**单项目上限 50 页（5000 人）**，超限报错退出，不静默截断。

**`--export` 回退 / 离线**：

```bash
python3 scripts/gitlab_buzz_people_generate.py \
  --config <频道配置.json> --export <feishu-bridge ops export-people 的 0600 文件> [--dry-run]
```

- 导出来自 bridge 的 `ops export-people`（ADR-0012：`{"<邮箱>": "<64hex>"}`，冻结格式），**必须是绝对路径**的 owner-only 0600 文件，导出文件不进仓。这条路径不加载 bridge 客户端、不读签名 key、不发任何 bridge 请求，行为与引入 API 模式之前完全相同；上面的匹配规则、手填优先、不删除、拒写与写回语义都适用。
- 与 API 参数**互斥**：同时给 `--export` 与 `--people-api-base-url` / `--signer-env-file` 直接报错退出；`--people-api-base-url` 与 `--signer-env-file` 也必须成对给出，三者都没有同样报错。

**参数**：

| 参数 | 必填 | 说明 |
|------|------|------|
| `--config` | 是 | 频道配置（owner-only 0600，绝对路径）；可重复，重复时必须配 `--people-file` |
| `--people-api-base-url` | API 模式 | 推荐。bridge 的公开 origin，恰好 `https://主机[:端口]`；须与 `--signer-env-file` 成对 |
| `--signer-env-file` | API 模式 | 推荐。含 `BUZZ_PRIVATE_KEY`（hex 或 nsec）的 0600 env 文件绝对路径，签名者须是各频道 owner 或 admin |
| `--export` | 回退 | `ops export-people` 的 0600 JSON 文件；与上面两个参数互斥 |
| `--people-file` | 否 | 共享 people 文件的绝对路径，见下节；不带时写回该配置的内联 `people` |
| `--domain` | 否 | 公司邮箱域名，默认 `a4x.io` |
| `--dry-run` | 否 | 只报告、不写任何文件（API 模式仍会读 bridge 接口） |

### 共享 people 文件（多频道共用一份映射）

「GitLab 用户名 → Buzz 公钥」是每个人只有一份的全局事实，不必在每个频道配置里各存一份。把它放进一个本机共享文件，各频道配置用 `people_file` 引用；`people` 内联只留给该频道的局部覆盖。

生成器的共享模式：`--config` 可重复，配合 `--people-file`：

```bash
python3 scripts/gitlab_buzz_people_generate.py \
  --config <频道A配置.json> --config <频道B配置.json> ... \
  --people-file <共享文件的绝对路径> \
  --people-api-base-url https://<bridge 的 BIND_PUBLIC_ORIGIN> --signer-env-file <0600 env 文件> \
  [--dry-run]
# 回退：把最后一行的两个 API 参数换成  --export <feishu-bridge ops export-people 的 0600 文件>
```

- 取所有给出配置的 `gitlab.projects` 成员的并集（每份配置用自己的 `gitlab.token_env` 与 `base_url`，同一项目只拉一次），按 `username@a4x.io` 精确匹配邮箱，写**一份**共享文件；各频道配置本身一字不改。
- **API 模式的共享运行**：对每份配置的 `channel_id` 各取一次接口（同一频道的多份配置只取一次），把各频道的 `emails` 合并后再匹配。**签名者必须是每一个频道的 owner 或 admin**：只要有一个频道取不到（例如在那里不是 owner/admin，接口回 404）、没有 `emails`、响应不合法，整次运行失败、共享文件不建也不改，报错点名是哪个频道。
- **跨频道的同一邮箱多把 key**：与上面「同一邮箱绑了多把 key：选一把」是同一条规则，各频道的 `emails` 合并后再选，所以在更多个频道里都是成员的那把优先；共享文件里已有的手填条目不受影响。同一个 pubkey 在两个频道用了不同域名的邮箱不算冲突；localpart 兜底时不同邮箱各对到不同 key 仍不猜、进 `unmapped`。
- **手填条目永远赢**：共享文件里已有的条目优先于邮箱来源，同名不同 pubkey 只警告；**永不删除**任何条目，包括已不在任何项目里的人。
- 拒写是取并集的：某个 pubkey 只要是任一给出配置的 Desk（`publisher_pubkey`）或 `agent_pubkeys`，就不写进共享文件；共享文件里已有的条目若撞上这些 pubkey，整次拒绝且什么都不写。
- 写入复刻 `atomic_write_json` 语义（0600 tmp + `os.replace` + 目录 fsync）；`--people-file` 必须是绝对路径且目录已存在；已存在的共享文件同样要过 owner/0600/非 symlink 校验。整次真写运行持有 `<共享文件>.people.lock` 的 flock（非阻塞），并发第二实例立即报错退出。`--dry-run` 不取锁，不建也不改任何文件（包括锁文件）。
- stdout 与单配置模式一样只报计数、用户名与布尔标志，另加 `configs / projects / usernames` 三个计数；不出现邮箱或 pubkey。
- 不带 `--people-file` 时只能给一个 `--config`，行为与上一节完全相同（写回该配置的内联 `people`）；给多个 `--config` 而没有 `--people-file` 直接报错。

**从各频道内联 people 迁到共享文件**（owner 手动，逐步可回退）：

1. 准备邮箱来源：推荐 API 模式（bridge 已部署 #77 并开了 `CHANNEL_PEOPLE_EMAILS_ENABLED`，且你的 Buzz key 是每个频道的 owner 或 admin）；不满足就拿 `ops export-people` 的 0600 文件（生产步骤见 buzz-deploy #62）走回退。
2. 先 `--dry-run` 跑共享模式，看 `added` 与 `unmapped` 是否符合预期。
3. 去掉 `--dry-run` 真写，得到共享文件；此时各频道配置还没有变化，行为不变。
4. 逐个频道：在配置里加 `"people_file": "<绝对路径>"`；只保留真正需要局部覆盖的内联条目，其余重复的内联 `people` 删掉。每改一个频道，下一轮 sync 就按新配置加载；`validate_config` 报错时整份配置被拒、不会发送，改回即可。
5. 之后新增的人只需重跑第 3 步，所有引用该文件的频道下一轮就生效。

**从 `--export` 迁到 API 模式**（owner 手动，逐步可回退；没有什么要迁移的数据，生成的 `people` / 共享文件格式不变）：

1. 确认前置：bridge 已部署带 `emails` 的接口并开了 `CHANNEL_PEOPLE_EMAILS_ENABLED`；没有就停在 `--export`，别的都不用动。
2. 找一个 0600 env 文件放 `BUZZ_PRIVATE_KEY`（可以直接用 `buzz_feishu_group_sync.py` 的 `people_api.signer_env_file` 那一份），确认这把 key 是要处理的每个频道的 owner 或 admin。
3. 把原命令里的 `--export <文件>` 换成 `--people-api-base-url https://<BIND_PUBLIC_ORIGIN> --signer-env-file <env 文件>`，先加 `--dry-run` 跑一遍，`added`、`unmapped` 与之前对得上（API 模式只含频道现存成员，所以 `unmapped` 可能更多，那是预期：不在频道里的人本来就不该被 @）。
4. 报错时按报错里的频道 id 处理：404 是这把 key 在那个频道不是 owner/admin；「without emails」是 bridge 没开开关；401 看本机时钟与 `BIND_PUBLIC_ORIGIN` 是否逐字一致。整次运行没有改任何文件，修好后重跑即可。
5. 去掉 `--dry-run` 真写；用不上的旧导出文件按原约定删掉。任何时候都能把命令改回 `--export`。

### 同频道多份配置

一个频道可以跑多份配置。常见做法是每个项目一份，各用自己的 project access token：

- 各份配置的 `channel_id`、`publisher_pubkey` 可以相同，但 `gitlab.projects` 必须互不重叠；`gitlab.token_env`、`gitlab.bot_user_id`、`gitlab.bot_username` 各填自己 token 的变量名和 bot 用户。
- 每份配置使用自己的 0600 config 与 0700 repo／Channel state，并在 owner 的 0600 runner manifest 中固定执行顺序；Desk prompt 不列这些路径。凭据变量来自同一 launcher 的显式白名单。
- disjoint 项目各记自己的 cursor/outbox，互不锁住；任何两份配置只要在同 Channel 重叠一个项目，就竞争同一 per-project lock，后到者返回 `locked`，不能并发写。
- **同一项目不要放进同一频道的两份配置**：锁只防并发，不会把两套 bot identity 与 cursor 合并；配置审查仍必须拒绝重叠。
- 也可以用 group access token，一份配置覆盖整个 group 的项目。但它能读写 group 下所有项目，超出 ADR「token 按项目签发」的前提，泄露后影响面更大；启用前先按 ADR 的触发条件重新评估。
- 这条仅描述既有 GitLab→Buzz 同步的兼容配置，不是普通多仓 Agent 的授权方案；普通 Agent 必须使用项目 token map，禁止为了跨仓便利新增 Group token。
- 拆分或合并配置会改变 scope，缓存与 outbox 文件随之换掉：先停用该 Channel 的调度者（Linux `systemctl --user disable --now`；macOS `launchctl bootout`），确认没有 `PENDING`，再更新 owner manifest；新配置从 `since` 扫描，已发过的按 binding note 和频道消息去重。摘要类记录的已发布 key 由**按 project 的无界 acked journal**（`state_dir/acked-summary-<project_id>.json`）保留，并与 state 目录内所有旧 scope outbox 的 ACK 行取并集，因此同 state 根下拆分／合并或 outbox ACK 截断（最多保留 1000 条）都不会重复发布；换了 state 根才需要人工核对。

## 运行面

### 命令与环境

Runner 内部的 Desk-owned Agent Step（每份配置由 owner manifest 固定）：

```bash
/usr/bin/python3 <SKILL_DIR>/scripts/gitlab_buzz_sync.py \
  --config <频道配置.json> --state-dir <repo-channel-state目录>
```

timer 入口 `gitlab_buzz_sync_timer.py` 不接受参数；config、state 与 release 路径由 `BUZZ_DESK_RUNNER_MANIFEST` 固定，不能从 prompt、消息文本、Canvas 或 GitLab 内容拼接。同一 scheduler job 的手动触发与周期触发不并发；只有绕过调度器并行直接运行入口时，per-project 非阻塞 lock 才让后到者返回 `locked`。

操作者可在同一 launcher 白名单 env 中运行 `gitlab_buzz_sync.py --config … --state-dir … --dry-run` 做预检。dry-run 读两侧但不写 outbox、cache 或外部系统，对新 MR 会跳过只为写入需要的成员、关联 Issue 与 diff 读取。

普通主机部署是每个 Channel 一对 `gitlab-buzz-sync-<channel>.service`／`.timer`，见 [部署 runbook](systemd/README.md)；不部署常驻 sync daemon。release 必须是 40 位 commit 固定副本并包含 timer 入口、runner、summary publisher、sync 与 route closure；dry-run 和 L4 完成前不 enable timer。公开 Workflow schedule 始终停用。

`<SKILL_DIR>` 必须是固定 commit 的 skills checkout，或者一份拷贝。不要指向会自动更新的 plugin marketplace 目录，否则 skill 仓合入的改动会不经试点直接跑进频道。

必需环境变量只来自 Desk 身份的 owner 固定 0600 env，由白名单 launcher 丢弃其余变量后留在进程环境里，不写在命令行（argv）或配置：

| 变量 | 用途 |
|---|---|
| `BUZZ_PRIVATE_KEY` | Desk identity 私钥，必须能推导出配置的 `publisher_pubkey` |
| `BUZZ_AUTH_TAG` | Desk 的 NIP-OA owner 背书，随 Buzz CLI 子进程一起使用 |
| `BUZZ_RELAY_URL` | relay origin（wss/https；ws/http 仅限回环地址） |
| `BUZZ_DESK_RUNNER_MANIFEST` | owner 固定的 0600 runner manifest 绝对路径 |
| `gitlab.token_env` 指定的变量 | GitLab token，默认即 Desk 的 `GITLAB_TOKEN`；启用同步时取 reporter profile（Reporter·`api,read_repository`），见 [agent-credentials.md](agent-credentials.md)「GitLab 角色最小档」。值里有空白或控制字符（例如 CRLF 换行的 env 文件留下的 `\r`）时启动即 `status: error`，错误里只有变量名，不含值 |

Buzz CLI 子进程只继承白名单变量（`HOME`、`PATH`、`LANG`、`BUZZ_RELAY_URL`、`BUZZ_PRIVATE_KEY`、`BUZZ_AUTH_TAG` 等），拿不到 GitLab token。GitLab token 只放在 `PRIVATE-TOKEN` 请求头里，脚本拒绝 HTTP 重定向。GitLab 单请求 30 秒、单响应 4 MiB、单列表 100 页、整轮 10 分钟；超出任一预算都失败且不推进 cursor。

### 状态文件

新建的 state 目录权限是 0700，lock、cache、outbox 都是 0600。打开锁文件、写 JSON 的临时文件时不跟随 symlink。

- **scope 摘要**：`channel_id` 加排序后的 `gitlab.projects`，取 SHA-256 前 16 位。cache 与 outbox 使用这个 scope。
- **锁**：每个 `channel_id + project_id` 一个非阻塞 `flock`，按排序顺序全部获取。同 Channel 的项目集合只要有重叠就不能并发；拿不到时返回 `status: locked`，零外部调用。
- **缓存** `gitlab-buzz-sync-<摘要>.cache.json`：
  - 存游标（本轮扫描起点 − 60 秒）以及绑定的频道、`since`、项目集合。
  - 频道、`since`、项目集合任一与配置不符，或游标晚于 GitLab 服务器时间，就忽略整个缓存，从 `since` 扫。
  - 本轮 `status: error`、`locked` 或 dry-run 时不推进。缓存丢失只会从 `since` 多读；去重仍依赖 binding、频道证据与 outbox。
- **outbox** `gitlab-buzz-sync-<摘要>.outbox.json`：Buzz message、edit、status reaction、diff 与 GitLab binding note 的完整确定性 payload 在动作前写为 `PENDING`，严格 readback 后转为 `ACKED`。同一紧凑状态变更的 edit、reaction、注意力提醒先一次原子落盘；`attempted:false` 表示崩溃前明确还未调用外部写入，可安全续跑，已尝试但无法证明完成的 message/edit/diff 继续失败关闭。reaction 与 binding note 可幂等补偿；明确的本地/relay 拒绝会清掉对应 pending，edit 的明确零写入拒绝会原子取消整组未执行 continuation。ACK 记录最多保留 1000 条。
- **summary request**（同文件，`kind: summary_request`）：`PENDING` → runner 认领 → `SUMMARIZING` → publisher 绑定 prose 后 → `PUBLISHING`（`publication` 含 `content`、`content_sha256`、`phase`）→ 严格 readback 后 `ACKED`。`phase: bound` 表示尚未证明发出：明确被拒的发送只重发该绑定内容；一旦发送结果未知（超时、accepted 但不可读）转 `phase: unproven`，之后只允许 readback 证明，绝不重发。存在任何 pending summary request 时，本轮扫描提前返回，cursor 不推进。**2026-09-18 政策后不再产生新 request**；升级前遗留的 pending 会被 publisher 按模板排空。
- **branch bindings** `branch-bindings-<project_id>.json`（每 project 一份）：feature 分支 Thread 的 `规范化分支名 → root` 缓存（归因梯子专用；政策后梯子不投喂，文件不再被读取，保留以防回滚）。
- **milestone bindings** `milestone-bindings-<project_id>.json`（每 project 一份，2026-09-18 政策新增）：`milestone iid → 🎯 门牌 root` 缓存。恢复梯子是「绑定文件 → 按门牌 URL 全频道搜索（`messages search`，同 Issue/MR root 找回）→ 新建门牌」；找到后重新锚定，不开重复 Thread。文件丢失只触发一次找回；读不出或损坏（非法 JSON、schema 不符）时整轮 fail-closed 退出，不静默重建，人工移走坏文件后下一轮恢复。
- **MR group bindings** `mr-group-bindings-<project_id>.json`（每 project 一份，issue #77）：分支族组键（源分支全文 / 去后缀基名）→ `{root, author}` 锚点缓存，先到先得、槽位不迁移。生命周期同 branch bindings：文件丢失只影响之后的新 sibling（自开新 Thread，既有 binding note 不受影响）；读不出或损坏时整轮 fail-closed 退出。
- **单机假设**：`flock` 只协调同一主机和 state 根。每份配置只能有一个部署域；HA 前必须改成共享 lease／幂等存储。

### stdout 与退出码

stdout 只有一个 JSON 对象，不含 Issue 标题，也不含任何 secret。

| 字段 | 说明 |
|---|---|
| `status` | `ok`：全部必要写入 ACK；`degraded`：轮次完成（cursor 推进、投递照常），但有关联查询降级或有对象被 stall，见 `degraded` 与 `stalled`；`error`：首个失败终止；`locked`：有重叠 Channel/project 正在运行，本轮零调用 |
| `error` | 只在 `error` 时出现。已脱敏的错误原因：`BUZZ_*`、名字含 TOKEN/KEY 的变量，以及 `gitlab.token_env` 的值都替换成 `***`。意外异常只输出 `unexpected <异常类型名>`，不带异常消息（消息里可能有路径或密钥） |
| `dry_run` | 是否 dry-run |
| `created` / `updated` | 新 Issue root 数；Issue routing/content 回帖数 |
| `activity` | Issue 与 MR 的活动回帖数（评论、字段变化、流水线、批准） |
| `mr_created` / `mr_updated` | 新 MR root 数；MR lifecycle/update 回帖数 |
| `mr_xrefs` | 本轮发出的 MR 交叉链接（`change:xref`）条数（ADR-0015）；重跑为 0 |
| `recovered` | 找回 root 并补写 binding note 的对象数 |
| `unchanged` | 读过、但无需发消息的对象数 |
| `skipped.confidential` / `skipped.excluded` / `skipped.backfill` | 各类跳过的计数；同一 MR 每轮最多计一次 `excluded` |
| `links` | 本轮写过的 Thread 的 Buzz 链接 |
| `notified.instant` / `notified.milestone` | 顶层即时消息条数；milestone 门牌 Thread 事实回帖数 |
| `skipped.milestone_identity` | 缺 target_iid 且标题匹配不到 milestone 的事件数（同时记入 `degraded`） |
| `summary_requests` | 待 timer 入口发布的 summary request 公开视图（request_id、project_id、facts、facts_sha256）。政策后恒为空，除非 outbox 里有升级前遗留 |
| `gaps` | 固定缺口清单，见「已知缺口」 |
| `unbound` | 因 MR 未绑定而没发的 MR 活动记录数 |
| `unavailable` | 读不到的可选接口，形如 `<project>:access_tokens`。只记录，不发通知 |
| `degraded` | 本轮发生过的降级信号（去重），形如 `<project>:related_query:HTTP 500`、`<project>:milestone_identity:N unresolved`、`<project>:issue:<iid>:stalled`。非空时 `status` 为 `degraded`，退出码仍为 0 |
| `diffs` / `diff_skipped` | 发出的 diff 文件数；跳过的文件数 |
| `stalled` | 本轮被 stall 的 Issue／MR：`[{project, object, iid, reason}]`。`reason` 已中和 `@` 与 `nostr:`，带对象编号；只有 `project/object/iid` 会写进 cache（事件驱动分组另存记录，见「对象级隔离」），下一轮据此重读这些对象 |
| `origin_fallbacks` | 本轮因格式合法的 origin 标记指向的根不可用（读不到、回帖、channel 不符、不是顶层消息、Desk 的普通发言）而**回退成自开 root**的对象：`[{project, object, iid, reason}]`（每个对象每个原因一条，`reason` 已中和 `@` 与 `nostr:`）。不改变 `status`，对象照常同步；没有时是空列表。Desk runner 只把条数带进自己的结果（`origin_fallbacks: N`，没有回退时没有这个键），原因文本留在 sync 子进程的报告里。`--dry-run` 同样会列出（ADR-0014） |

退出码：
- `ok`、`degraded`、`locked` 为 0，`error` 为 1。
- 命令行参数本身写错时，是 argparse 的用法错误（退出码 2），stdout 为空。

### 对象级隔离（ADR-0009，事件分组与 owner 通知见 ADR-0010）

**只让坏对象停摆，不让整个频道停摆。** Issue、MR、MR 分组和 milestone 上只影响这一个对象的数据问题按下面处理：

- **范围**：origin 标记**格式非法**（JSON 非法、键集合不对、`channel_id` 不是 UUID、`root_event_id` 不是 64 位 hex；格式合法但根不可用的标记不在此列，见「origin 绑定」的回退，ADR-0014）、绑定根被删／读不到（Buzz CLI 退出码 1）或不是可读的根（Desk 发的门牌／事实要属于本对象；人发的顶层消息按结构可读；回帖、别的频道、别的 kind 都不行）、binding 冲突、Thread 回帖达到 500 条、对象字段类型异常、关联 Issue 被排除等，以及门牌 URL 与对象不符。
- **处理**：该对象本轮零写入（不建 root、不写 binding、不回帖、不发 mention），进 `stalled`，`status` 记为 `degraded`（退出码 0）；cursor 照常推进，`stalled` 写进 cache，**下一轮即使 GitLab 没再更新它也会被重读并重试**，数据修好就自动补做；对象被删（404）则移出。
- **通知**：每个 stalled 对象、每个原因、每个 UTC 日期，频道里最多发一条顶层通知 `⚠️ 同步卡住（该对象本轮被跳过）· <原因>`（header `[object:sync][event:stalled]`，key `sync_stalled-<project>-<kind>-<iid>-<原因摘要>-<日期>`），**@ 频道 owner**（ADR-0010）：owner 取自新读的频道成员表里角色为 `owner` 的人，不要求在 `people` 里映射，Agent（`agent_pubkeys`）和 Desk 自己不 @，没有 owner 就不 @；跨轮次靠频道扫描去重，所以同一天不会重复 @；`--dry-run` 只报告不发送。
- **事件驱动的分组**（MR 分组活动、milestone 线程，ADR-0010）：它们的事件在 cursor 越过后无法重新推导，所以该分组 stalled 时把**它的事件记录（每个分组最多最新 200 条）存进 cache 的 `stalled_groups`**，下一轮把这些记录放到新事件前面重新处理，修好数据就补发（去重靠 Thread 里已发的 `events` key）；对象被删或分组不再需要时，记录随成功处理自动清掉。
- **仍然整轮失败**（首个失败终止，cursor 不前进）：传输、认证、身份、配置、资源预算、relay 写入失败、无法判断是否已发送的不确定状态、Buzz CLI 退出码 2+、锁冲突。这一档与旧语义（ADR-0004 / ADR-0001 / ADR-0008 的「首个失败立即停止」）一致。
- `--dry-run` 对新 Issue、新 MR 和新 milestone 同样校验 origin（只读），预演能看到会 stalled 的对象；它不写 cache，所以不保留分组记录。

### 失败关闭与投递恢复

`status: error` 时退出码 1，cursor 不前进。**首个失败**立即停止后续对象、项目与外部写入；已经严格 readback 并记为 `ACKED` 的事实保留。单个 Issue／MR 的数据问题不在此列，见上一节「对象级隔离」。触发条件包括：
- token 身份不符，或任一配置项目不可读；
- 配置非法（含 `gitlab.projects` 有重复 id），token 值含空白或控制字符，或 CLI 校验失败；
- 缺少必需的环境变量；
- GitLab/relay 不可达、连接被断，或返回非 JSON 等非预期格式。包括处理 MR 流水线、批准活动时读该 MR 的评论：这类传输错误同样整轮失败，不当成对象问题；
- GitLab 任何必需读取／写入失败、字段或分页契约畸形、资源预算耗尽。例外：路由归属用的关联查询与 milestone 标题归位失败时只把该条记录降级并记 `degraded[]`，不触发整轮失败（关联查询属于归因梯子，政策后仅回滚时可达）；
- Buzz CLI 非零退出、频道／Thread 扫描不完整或畸形；
- 频道消息扫描失败，见「去重与恢复」；
- 写入后回读不一致；
- 其他意外异常。

timer 不把这些错误写入业务 Channel：只在 owner 可读的 user journal（`journalctl --user -u gitlab-buzz-sync-<channel>.service`）与 L4 receipt 留一条脱敏记录。没有已实现的私有告警 sink 时不声称发送定向告警；持续失败也不得形成公开错误循环。

每项动作开始前写 `PENDING`。如果动作已到达目标侧但进程在 ACK 前退出，下轮先查找 author、Channel、Thread/tag 或 note author/body 完全匹配的证据；命中则记 `ACKED` 且不重复发送。Buzz message/diff 未命中时保持 PENDING 并报错；binding note 正文是绑定同一 root 的确定性事实，只能在重读项目 visibility 后重试，严格回读后 ACK。只有所有必要动作 ACK 后写 cache cursor。

private／internal 项目在整轮预检以及每个 Buzz message/diff 和 GitLab binding note 新建或重试边界，都重新读取项目 visibility；成员对账已按 [ADR-0006](../../../docs/05-adr/0006-channel-membership-is-the-audience-consent.md) 废除——频道成员身份本身即受众授权，成员增减是频道管理员的授权行为，同步不再做、也不再被打断于 `channels members` 对账。Issue 列表快照通过过滤后，投递前还要 `GET` 详情并重做 confidential/exclude 判断；这缩小列表分页期间的权限状态漂移窗口。Buzz 已发布事实不可撤回，因此对象删除与 confidential 切换仍要按上线前 L4 演练和 retention 决策处理。

别的频道、别的对象的 binding note（例如 clone／move 带来的评论）不是本对象绑定，直接忽略；畸形、冲突或可信 root 丢失则按首错失败，不创建替代 Thread。

### 去重与恢复

- **Issue/MR**：binding note 是跨系统绑定事实。root 已发、note 未写时，按「Bridge 作者 + 频道 + 对象 URL 路径尾」全文搜索 root 找回（新 Issue 事实 root 按 header 核对，旧门牌按末行 URL 核对；命中上限按首错停轮），只看该对象 `created_at` − 15 分钟之后的 publisher 消息。候选顶层消息按「header 的 (project, object, iid) 或门牌末行 URL 精确相等（work_items 与 issues 形式视为同一 issue URL）」判定，多于一个候选拒绝恢复；Issue 在规范 URL 没有命中时，会在同一时间窗内再按 work_items 形式搜一次，找回事实 root 或旧门牌。**搜索词是 URL 的路径尾**（`/-/issues/N`、`/-/merge_requests/N`、`/-/milestones/N`）：relay 0.2.1 全文搜索对完整 URL 命不中（engineering/skills#106），路径尾能命中，命中后再在本地按精确 URL 过滤；「命中上限」看的是 relay 的原始命中数。outbox 同时约束 crash recovery：PENDING 的 Buzz 消息按同样证据证明已发布后 ACK；无 header 的门牌投递在 outbox payload 里显式带 `project_id`，投递闸门的可见性复查以此为准。
- **Thread 内**：评论按 header `[note:<id>]`（存量正文 `note:` 行）去重，MR 活动按 header `[events:<key>]`（存量正文 `events:` 行）去重，Diff 按「同 commit + file 的 kind 40008」去重。只认 Bridge 发的、带合法同步 header 的消息；新消息读 header trailer，存量（header 在首行）仍扫正文全部 `note:` / `events:` 行。
- **MR 交叉链接**（ADR-0015）：发之前读目标 Thread，已有 Desk 发的、同 project 同 MR iid 的 `change:xref` 消息（header 里有 `xref`，`mr_xref_posted`）就不再发；参与者写的、看起来像 header 的文字不算。交叉链接先于 binding 写入，所以崩溃重跑既不重发也不丢；binding 备注丢失、MR 重新走「新 MR」路径时同样不重发。它没有 `note`／`events` trailer，不参与评论与活动的去重，也不算「上一版事实」。
- **摘要 request**：prose 摘要没有 header 和 `events:` 行，频道回读永远无法证明它已发布；去重只依赖 owner state——按 project 的无界 acked journal，加上 state 目录内所有 outbox 的 ACK 行并集（含旧 scope）。runner 认领时同一个全局固定清单里出现两个已认领 request、或已认领者不是全局最早，都会按首错失败。
- **同一轮内**：同一个 key 的记录只处理一次（offset 翻页期间有记录移位时，可能被列出两次）。
- **顶层**：
  - 只认 Bridge 发的**顶层**消息（不带 `e` tag）、且带合法同步 header 的消息里的 events 键（header trailer 或存量正文 `events:` 行）；Thread 回帖里的 events 不算，不会压掉顶层通知。
  - 回看窗口：普通通知从游标前 10 分钟开始；按日键控的 access-token 通知从扫描当天 UTC 零点前 15 分钟开始，容忍 timer 主机与 GitLab 的时钟偏差。
  - 频道一轮最多扫描两次，每个窗口一次，所有项目共用结果：先扫到更早的窗口时，后面的项目直接复用。
  - 按时间向前分页读取本频道消息，每页 200 条，最多 500 页（约 10 万条）。
  - **扫描失败会整轮失败，并且每轮重复**，直到这批消息移出回看窗口。两种情况：
    - 读满 500 页（约 10 万条）仍没读完窗口；
    - 同一秒内的消息占满一整页（200 条），翻页边界无法确定。

    没有缓存（首轮）时，错误里会提示把配置的 `since` 调近；改 `since` 会让缓存失效、从新的 `since` 重扫，代价是更早创建且未绑定的对象不再回灌。
  - **流水线重试会再发**：顶层的流水线记录（默认分支失败的即时通知）以 `pipeline-<id>-<status>` 为 key，但只回看到游标前 10 分钟。流水线被重试后又以同一终态结束时，更新时间进了新窗口，原通知已在窗口外，所以会再发一次。MR 流水线活动按整个 MR Thread 去重，不会再发。

### 停用与回滚

1. **立即停用同步**：Linux 用 `systemctl --user disable --now gitlab-buzz-sync-<channel>.timer`，macOS 用 `launchctl bootout gui/$(id -u)/ai.addx.gitlab-buzz-sync.<channel>`；再确认对应 service/job 不在运行（正在跑的一轮先等待自行结束，不要 kill 在写的进程）。业务 Channel 没有需要删除的 tick。若发现遗留 schedule/webhook Workflow，删除并回读确认不存在。
2. **停路由**：timer 停用期间，在 owner manifest 中禁用 route mode；之后重新 enable timer 时只同步不指派。保留 route config、cursor 和 operation ledger；这不会删除已发布事实。
3. **保存恢复状态**：cursor、outbox 与 binding 保持原样；检查同步 outbox 与 route operation ledger 没有无法解释的 `PENDING`；保留 0600 config/env 与 0700 state，不删除 Desk membership 或 GitLab token，直到确认无需回滚。重新 enable timer 后从这些状态续上。
4. **回滚脚本版本**：调度者保持停用，把 unit/plist 的 `<immutable-release>` 与 manifest 的 `release_dir` 一起切回上一个已验证 commit；Linux `daemon-reload` 后手动 start，macOS 重跑 `plutil -lint` 与 launchd 阶段 0；核对旧版 manifest、config/outbox schema，重新 L4 通过后再启用。
   - 频道配置必须与旧版本兼容：旧脚本同样严格校验未知键，新版本才有的配置键会让旧脚本每轮 `status: error`。回滚前先删掉这些键。
   - 首次上线时没有更早的已验证版本可回滚，只能按第 1–3 步停用。
   - 回滚到 ADR-0008 之前的 heartbeat 版本不受支持：同时开 timer 与 Desk 同步会形成两个调度者；任何时候只启用一个。
5. **不清理**已经发出的频道消息和 GitLab binding note。重新启用后，先恢复 outbox，再按 binding 续上。

### 与旧 `gitlab-issue-notify.py` 的切换清单

本机的旧 notifier（systemd user timer，按 `SOURCES` 把项目映射到频道，以单独的通知身份播报）和新同步如果对同一频道同时开，会重复播报。

1. 在旧 notifier 的 `SOURCES` 里找出映射到目标频道的项目。
2. **先**从 `SOURCES` 删掉这些项目，等过一个 timer 周期，确认旧身份不再往该频道发消息。
3. 新同步的 `since` 设在切换时刻附近，避免回灌旧 notifier 播过的历史；先跑一次 `--dry-run` 看计数。
4. 按测试方案阶段 0 手动运行一轮（Linux systemd 或 macOS launchd）并通过 external receipt v3 后，再启用周期任务；不得创建公开 schedule Workflow。
5. 其他频道继续由旧 notifier 播报，直到各自完成切换；全部切完后退役旧 notifier。

## 角色与权限

bot token 就是 Desk 的 `GITLAB_TOKEN`：启用同步时取 reporter profile（Reporter·`api,read_repository`），未启用同步的 Desk 为 Planner·`api`。`api` scope 是写 binding note 必需的；接受的风险见 ADR。一个频道同步多个项目、又想各用各的 token 时，按「同频道多份配置」拆开。group access token 只保留为历史兼容选项，覆盖整个 group、范围更宽，先按 ADR 重新评估；它不替代普通多仓 Agent 的项目 token map。

| GitLab 数据 | 最低角色 | 读不到时 |
|---|---|---|
| Issue、MR、评论、events、milestones、releases、pipelines 与 jobs、deployments、成员；写 binding note | Reporter（写 note 需要 `api`） | `status: error` |
| project access tokens 列表 | Maintainer | 计入 `unavailable`，不发任何通知 |

试点 bot 保持 Reporter，所以试点里不发 token 到期提醒（feature flag 通知已在 2026-09-18 政策中整体停发）。要打开 token 提醒，就得提升 bot 角色、扩大 token 权限，先按 ADR 的触发条件重新评估。

**token 到期要轮换（rotate），不要删掉重建**：
- GitLab 轮换 project access token 时，bot 用户不变，只换 token 值。更新 Desk 身份的 0600 env（timer 下一轮自动读到）并重启 Desk Agent，配置不用改。
- 删掉 token 再新建，会生成一个新的 bot 用户（新的 user id 和用户名）。脚本只认配置里的 bot 写的 binding note，旧 bot 写的全部不再被认：
  - `since` 之后更新过的对象会再建一个 root、再写一条 binding note；
  - `since` 之前创建的对象被当成存量，静默跳过，不再同步。

  旧 bot 写的 binding note 不会被当成评论转发。万一已经重建，先停用，再和 PO 决定是否清理重复的 root。

## Canvas 路由

生产同步由 owner 主机调度器（Linux systemd 或 macOS launchd）启动，不产生 relay tick；旧的 `@<desk-name> gitlab sync` schedule Workflow 已退役，看到同名 Workflow 应删除并回读确认不存在。Role 路由规则也不创建为 Workflow，而是由 Channel admin 在 Canvas 的唯一版本块中编辑，格式见 [gitlab-buzz-routing-canvas.md](gitlab-buzz-routing-canvas.md)。本地 route gate 每轮只读取一次 raw kind `40100` 完整事件，把该 policy snapshot 绑定到所有 route operation。

Issue 前缀必须锚定到 `[gitlab-notify:v1][object:issue][type:…][status:…][state:opened][change:routing]`；MR review 前缀必须锚定到 `[gitlab-notify:v1][object:mr][state:opened][draft:no][change:lifecycle][transition:reviewable]`。匹配的是 header 行（新消息末行，存量首行），标题和评论不能改变该行。

MR review 命中非 draft 新建、draft→ready、以及重开且非 draft；不命中重开时仍 draft、首次扫描已 merged/closed/locked、`locked`→`opened` 的 `transition:none`、pipeline、approval 或 comment activity。

Canvas 里的 `role` 是代码 registry 的稳定 id，不是 `@display-name` 或 pubkey。未知 Role、executor Role、重复 route id、重复 prefix、非完整 prefix、附加列或第二个 routing block 都是配置错误；整轮不发 mention。

### 默认：Desk 本地 Canvas route gate

runner 在同步 step 返回 `ok`、`degraded` 或 `locked` 后执行：

```bash
python3 <SKILL_DIR>/scripts/gitlab_buzz_route_reply.py \
  --config <ROUTE_CONFIG> --state-dir <ROUTE_STATE_DIR> --scan-once
```

本地配置从 `references/scripts/gitlab-buzz-route-writer.example.json` 复制。`scan_since` 固定启用边界；`sender_pubkey` 和 `publisher_pubkey` 都是 Desk；每个 Channel 配置可信 Canvas admin pubkeys 和稳定 `roles`。脚本用固定 digest 的 Buzz CLI 执行 `messages get --kinds 40100`，因为 `canvas get` 只返回 content，无法核验 author/envelope；再扫描 Desk 的 kind `9,40003` facts：kind `40003` 以 edit id 作为幂等 source、验证其签名与唯一原事件，并回到原事实所在 canonical Thread；最后用 `--reply-to` 和显式 Role `p` tag 回复。因此状态原位更新由 kind `40003` scan 触发，不能只监听 `message_send`；带 `route:skip` 的纯历史 overlay 明确不触发。

每个 source+route+policy 使用 owner-only durable operation：先 `PENDING`，CLI accepted 后保存 event id，严格 readback 后 `ACKED`。send 附近退出或 relay 暂时读不到时，只能按 Desk 签名 marker、精确 Channel/root/Role tag 恢复，不能自动重发；路由 cursor 仅在全轮成功后推进。Canvas 修改只影响之后扫描到的事实，不追溯 `scan_since` 或 durable cursor 之前的历史。

### 降级：relay 0.2.1 HTTP route-reply adapter

兼容模板是 `references/workflows/route-type-status-fallback.yaml`，服务是
`scripts/gitlab_buzz_route_reply.py`，配置从
`references/scripts/gitlab-buzz-route-reply.example.json` 复制。它保留相同的 Desk publisher 作者 + 完整 header 行
前缀判断，但用 `call_webhook` 只把 `channel_id`、`message_id` 和固定 `route_id` 交给服务；服务端仍回读最新可信 Canvas，把 `route_id` 解析到 Canvas 的完整 header 前缀与 code-owned Role mention/pubkey，再用 Buzz CLI 的 `--reply-to` 回到 canonical Thread。

这个旧 Workflow 只有 `message_posted` trigger，不把 kind `40003` edit 当成新消息；因此启用它的同步配置必须保持 `compact_status_updates: false`。紧凑状态模式只能使用上面的默认本地 scanner，除非将来另一个 edit trigger 已完成真实 L4。模板用 `str_contains(trigger_text, "\n[gitlab-notify:v1]…")` 匹配当前格式末行 header；旧的 `str_starts_with(header)` 对当前首行人读 headline 永远不成立。

这不是把信任交给 HTTP 请求：服务回读 relay 事件，验证 Channel、publisher pubkey、kind、完整路由
前缀和 canonical root；请求不能选择 Role、reason 或 pubkey。配置拒绝 executor Role，发送时显式传入并回读核对唯一 `p` tag，不能依赖可重名的显示名解析。HTTP sender 不能拿 GitLab token，也不能改 binding 或同步游标。Role Agent 的 `respond_to` 必须明确允许该 sender；不能靠降级服务绕过 owner policy。

`X-Route-Secret` 的实际值虽然不进 Git，却会以明文保存在 Workflow 定义中；本地真实 relay 已验证
owner、Desk bot 和 Role bot 均能通过 `workflows get` 读到。它只是降低公网 endpoint 滥用的每频道
bearer，不是对 Channel 成员保密的 secret：不得复用 GitLab token、Buzz 私钥、Vault token 或其他
系统凭据；成员移除、值泄露或服务切换时必须轮换。真实 bearer 从服务环境变量
`GITLAB_BUZZ_ROUTE_SECRET` 注入，日志、响应和 Buzz 子进程 env 都不得回显。

一个 route-reply 进程只允许配置一个 Channel，确保一把 bearer 不能认证第二个 Channel。listener 最多
同时处理 32 个请求，client body read 为 5 秒，另有每进程每 60 秒 120 次的硬上限；超限返回 429。
每个请求向 stderr 输出一条 JSON 审计记录，只含 status、固定 path 以及格式合法的 Channel/source/route
坐标，不记录 header、bearer 或任意正文。进程级限流只用于 fail-safe，不能替代 HTTPS ingress 的来源
限流、request buffering、超时、body cap 和告警。

启动时必须显式传 `--state-dir <owner-only-dir>`。服务按 Channel + sender 身份分 scope，使用跨进程
文件锁，并为每个 source+route 永久保留一个 0600 状态记录：先落盘 `PENDING`，再调用 CLI；CLI 返回
accepted event id 后先补记 id，再做严格 readback，最后转 `ACKED`。如果进程在 send 附近退出，或 relay
暂时读不到已接受事件，后续请求只允许通过独立 route 身份签名的 marker 与精确 Channel/root/Role `p` tag
恢复，**不能自动重发**。尚无 event id 且目标侧也不可见的 PENDING 会保持失败关闭，需要操作者在同一隔离
principal 内核对 relay 后再决定处置；不能为“恢复服务”直接删除状态文件。状态目录随服务备份和回滚保留，
直到 fallback Workflow 已删除、在途调用清零且 HTTP sender 身份撤销后才可按保留策略归档。

本地 Canvas gate 与 HTTP fallback 不得同时启用；两条路径必须在发送前获取同一 repo／Channel scope 的共享 mode lease，冲突方以 zero-write 退出。启用兼容路径需要独立 HTTP sender 身份、固定 CLI digest、0600
单 Channel 配置、0700 state dir、loopback listener、受控 HTTPS ingress、两层限流和结构化审计；L3 直接 HTTP roundtrip 通过后，仍必须在
L4 证明真实 relay `call_webhook` → 公网 HTTPS → Buzz 的整条链。停用时先删 fallback Workflow，确认
无在途调用，再停止服务、轮换 bearer、撤销 HTTP sender Channel membership并归档幂等状态；切回本地 Desk gate 时同样先停旧规则再启新规则。Naturehood 不创建 fallback Workflow、不运行 HTTP writer，也不配置 fallback sender membership。

webhook 唤醒模板见 `references/workflows/webhook-wake-desk.yaml`（未启用）：
- webhook 只作唤醒信号，secret 放在 `X-Webhook-Secret` 请求头里。
- 事件类型判断写在步骤的 `if:` 里：relay 的 webhook 触发器不带字段，写在 `trigger` 下的 `filter` 会被静默丢弃。GitLab 端也只勾选同样的事件。

下发命令：`buzz workflows create --channel <CHANNEL_UUID> --yaml "$(cat <file>)"`。CLI 用 `buzz-0.5.23` 的绝对路径，不用 `~/.local/bin/buzz`。

### 没有 `status::` 标签的仓库：`message_posted` Workflow 唤醒角色 Agent

Canvas 路由按 header 里的 `type` / `status` 匹配，缺失的 `status` 记为 `unknown`，而 `unknown` 没有匹配的路由规则就不会指派。业务仓不用 `status::` 标签、又要「新 Issue 一到就让某个 Agent 处理」时（例：Zendesk 客诉工单仓，只有 `type::bug` 和 `source::` 标签），改用 `message_posted` Workflow 去匹配 Desk 发出的**首条事实**：[模板](workflows/issue-first-sync-wake.yaml)。

- **这是 SKILL.md Rule 8（自动路由只由 Desk 的确定性 gate 指派）的一个窄例外**：Workflow 不经过 Desk 的路由 gate，所以要自己守住 gate 原本守的东西。`<role-agent>` 必须是非 Desk、非 executor 的角色 Agent；它的 kind:30177 `respond_to` 要允许 Workflow 发送者（否则唤醒被静默丢弃）；Canvas 里保留下面那条永远不命中的占位路由，两条路径不会同时唤醒。
- **匹配串来自同步脚本自己的渲染**：
  - 首条已打开状态锚在消息**第一行开头**（`str_starts_with(trigger_text, "📋 **已打开**")`）。后续状态流转以 🔄 开头，评论镜像消息以 💬 开头；评论正文（Zendesk 客户写的文字）进不了消息开头，所以伪造不出首条事实；只用 `str_contains` 的话，评论里原样写出那几个字符串就能反复唤醒（已用真实渲染验证）。
  - type／state／project 匹配 header（在消息**末行**，只能 `str_contains`）。
  - 标签匹配是整条消息的**子串**匹配，标题里写了同样的字、或标签是它的前缀（`source::x-v2`）也会命中：不是安全边界，Agent 处理前回 GitLab 核对标签。标签文字必须是渲染后的写法（`·`→`•`、`,`→`，`、`@`→`＠`、连续空白折成一个空格，含 `"` 会破坏过滤串），标签名避开这些字符。
  - 两个 `type::` 标签、大写 `type::Bug`、创建时已关闭的 Issue 都不匹配（前两者渲染成 `unknown`，后者 `state:closed`）。
  - 过滤串由 `tests/test_issue_first_sync_wake_template.py` 拿真实渲染结果整条求值回归（含评论伪造的负例）；没有在真实 relay 上回归 `str_starts_with` 对 emoji 前缀的处理时，以第一条真实唤醒是否出现为准。
- **必须带 `trigger_author == "<Desk pubkey>"`**：既校验来源，也防循环（Workflow 自己的唤醒消息和 Agent 的回复都不是 Desk 发的）。
- **唤醒消息是顶层消息**（relay 0.2.1 的 Workflow 没有 `reply_in_thread`），挂在它下面的回复没人看得到。Agent 的回帖（含失败说明）必须 `--reply-to <触发消息所在 Thread 的根>`：用 `buzz messages thread --event {{trigger.message_id}}` 读出，最早且没有 e tag 的那条就是 root（新 Issue 是首条事实，旧 Issue 或 MR 可能是门牌）。**这条要同时写进 Agent prompt 和唤醒文本**，只写通用模板时实测 Agent 会回到唤醒消息的 Thread。`{{trigger.message_id}}` 在 0.2.1 上会渲染成完整事件 id（2026-09-20 实测）；`buzz workflows runs` 的列表始终为空，以唤醒消息是否出现为准。
- **产出写回 GitLab 评论**（评论首行带 marker 做幂等），同步会把评论镜像进该 Issue 的 Thread；Agent 先查 marker，已处理就不重复。
- **runner manifest 仍要求 `route`**，Canvas 路由表至少要有一条规则。不用 Canvas 路由时放一条**永远不会命中**的占位规则：`trigger_prefix` 必须是**完整**的支持前缀（不是 `[status:route-disabled]` 这种片段，片段会让整轮路由 fail closed），`role` 是真实的非 executor 角色，`status` 用本仓不存在的值：

  ```
  | placeholder-never-matches | `[gitlab-notify:v1][object:issue][type:bug][status:route-disabled][state:opened][change:routing]` | `<role-id>` | `placeholder` |
  ```

  测试用 Canvas 解析器验证这个写法。
- **兜底**：Workflow 引擎曾出现静默停摆（upstream #6490）。事件不能漏的话，另配一条低频 schedule，扫「还没有处理评论的新 Issue」。

## 调用的 GitLab 接口

| 用途 | 接口 |
|---|---|
| 身份、项目 | `GET /user`（同时取响应头 `Date` 作扫描时间）；`GET /projects/:id`，每个配置项目在任何写入前各读一次 |
| Issue 与评论 | `GET /projects/:id/issues?state=all&order_by=created_at&sort=asc&updated_after=`、`GET …/issues/:iid`、`GET …/issues/:iid/notes`、`POST …/issues/:iid/notes` + 按 id 回读 |
| MR 与评论 | `GET /projects/:id/merge_requests?state=all&order_by=created_at&sort=asc&updated_after=`、`GET …/merge_requests/:iid`（活动渲染）、`…/notes`、`POST …/notes` + 回读、`…/closes_issues`、`…/diffs` |
| MR→Issue 关联反查 | `GET /projects/:id/issues/:iid/related_merge_requests`：每轮每个 Issue 最多查一次并缓存，多 MR 共享结果；结果只用来生成链接（`issues:` 行与交叉链接），不决定落点与 binding（ADR-0015） |
| 提醒人 | `GET /projects/:id/members/all`（`access_level ≥ 40`；响应没有 bot 标记，按用户名排除 project/group bot） |
| 活动流 | `GET /projects/:id/events?after=<游标前一天>&sort=asc`，本地再按 `created_at` 过滤 |
| milestone 列表 | `GET /projects/:id/milestones?state=all`（每轮至多一次，按标题归位缺 `target_iid` 的 milestone 事件） |
| 流水线与 job | `GET /projects/:id/pipelines?updated_after=&order_by=id&sort=asc`（按 id 升序翻页，只取游标后更新的）、`GET …/pipelines/:id/jobs?scope[]=failed` |
| 部署 | `GET /projects/:id/deployments?updated_after=&order_by=updated_at&sort=asc`（GitLab 16.0 起 `updated_after` 必须配 `updated_at` 排序，否则 400） |
| Release | `GET /projects/:id/releases?order_by=created_at&sort=desc&per_page=100`：**只读一页，即最新 100 个**（13:16 恢复） |
| 可选（需要 Maintainer，无权限记入 `unavailable`） | `GET /projects/:id/access_tokens` |

feature flags 接口在 2026-09-18 政策后不再调用。

除 Release 外，列表接口都按 `x-next-page` 翻页。写 note 后按 id 回读作者与正文。

## 已知缺口

### 轮询覆盖不到的变化

以下变化不发送，stdout 的 `gaps` 每轮都固定列出（政策 2026-09-18 更新：commit/snippet 评论本身已停发，不再是缺口）：

| `gaps` 值 | 含义 |
|---|---|
| `emoji` | 表情回应 |
| `release_update_delete` | Release 的更新（webhook-only，政策上也不通知）；删除靠 webhook 即时，轮询看不到 |
| `intermediate_states` | 轮询间隔内的中间状态 |
| `mr_unapproval` | 取消批准 |
| `discussion_resolution` | 讨论 resolve |
| `auto_merge_setting` | 自动合并设置 |

**政策性停发**（不在 `gaps` 里，见「顶层通知」）：push、feature flag、wiki、成员变化、commit/snippet 评论、流水线与部署的 happy path。这些是频道 owner 的明确决策，不是技术缺口；要恢复某一类，改 `NOTIFIED_OBJECTS` 相关的记录构造器即可（梯子与摘要机制保留在代码里）。

GitLab Premium/Ultimate 专属事件，以及只有 group webhook 才有的事件（group 成员、子组、项目），也不在范围内。

Issue/MR 快照之外的字段变化不单独发消息，也不在 `gaps` 里：截止日期、工时（估算与已用）、讨论锁定、MR 的合并选项（squash、合并后删除源分支）。它们和快照字段同时变化时，会随那条回帖一起被看到。

### 实现与运维

- **资源预算是硬边界**：GitLab 单响应 4 MiB、单列表 100 页、整轮 10 分钟。超过预算不会漏着继续，而是首错停止；大项目要缩短 `since` 或按项目拆分 service。
- **部署按 `updated_at` 做 offset 翻页**（GitLab 要求的排序）：扫描期间若有部署被更新并移到末尾，窗口内超过一页（100 个）时，个别部署状态可能漏发。流水线按 id 翻页，没有这个问题。
- **Thread 回帖达到 500 条**时无法证明完整历史，该对象 stall（其余对象照常，见「对象级隔离」）；需要归档／迁移 Thread 后人工恢复。
- **存量多发的 Thread 停在最后一次的状态**（ADR-0015）：MR 按旧规则被发进多个 Issue Thread 的，非 binding 的 Thread 不再更新（例如 MR 已合并，那里仍写「已打开」），也没有交叉链接；不搬迁、不清理，要不要补一条说明另行决定。
- **归因梯子的 push／pipeline digest 落点没有改**：`_place_git_activity` 仍会把 digest 记录发进 MR 关联的每个 Issue Thread；政策后 live 数据不再进入这条路径，随 #80 下线。
- **锁只在单机、单 state 根内有效**，按每个「频道 + 项目」隔离；两台主机或两个 state 根仍不能同时服务同一 scope。
- **频道消息扫描有上限**：读满 500 页仍没读完，或同一秒的消息占满一页时整轮失败，并持续到这批消息移出回看窗口（见「去重与恢复」）。
- **顶层流水线通知在重试到同一终态后会再发一次**（见「去重与恢复」）。
- **失败只进私有日志**：timer 一轮失败时业务 Channel 保持静默，owner 日志／receipt 留脱敏记录；没有私有 alert sink 时不会发送定向告警。Linux 看 `systemctl --user list-timers` 与 journal，macOS 看 `launchctl print` 与 plist 指定的 0600 日志。
- **webhook 唤醒模板未接入 timer**：历史模板只会 @Desk，而 Desk 不再运行同步；要做事件驱动，需要另行设计触发 timer 的入口。
- **GitLab 18.0 不发 milestone webhook**：milestone 变化只靠 timer 轮询进入 🎯 门牌 Thread。
- **重建 bot token 会丢掉旧绑定**：见「角色与权限」，要轮换，不要重建。
- **脚本是 2000 多行的单文件**。
- GitLab 是否把 `buzz://` 渲染成可点链接，尚未验证（L4 记录）。
