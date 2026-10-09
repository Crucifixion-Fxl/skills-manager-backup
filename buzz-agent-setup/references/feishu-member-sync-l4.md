# 飞书群 ↔ 频道成员同步：L4 体验验收（L4-148）

engineering/skills#148 的完整用户故事：在飞书群里拉 agent → 频道出现它并发起入群申请 → owner 在飞书点 ✅ 或回复 `/approve` → agent 刷新订阅、开通、能回复；在飞书里移人 / 移 agent，频道跟着变，反过来也一样；人的表情两边互通。本文件是这个故事的 L4 用例目录；机制见 [feishu-two-way-sync.md](feishu-two-way-sync.md)、[agent-channel-join.md](agent-channel-join.md)。

## 分层依据

- L1/L2：`test_buzz_feishu_group_sync_two_way_members.py`、`…_two_way_reactions.py`、`test_buzz_agent_join_requests*.py` 覆盖每条规则的正负例（三方对比、基线、批量暂停、签名身份保护、镜像信任、CheckMark 重评等）。
- 不做 L3：这两个脚本的外部边界是 lark-cli + 真实飞书，做 localstack L3 要另造一套假飞书 API，而且测不到飞书客户端的真实行为。取而代之的是下面这份可以机器复核的 L4 回执。
- L4：真实 Buzz relay、真实测试飞书群、真实 agent。每条 Buzz 事件都以原始签名事件的形式存进回执，oracle 会重新校验 NIP-01 id 和签名；飞书消息、表情、成员名单用 lark-cli 读回。回执不能凭记忆手写。

## 前提

- 频道「Buzz×飞书 L4 编辑同步测试」（`613a9560-d423-4d14-ab3d-f4fc29aecb7a`）和它绑定的测试飞书群；群同步和入群申请的 timer 都在跑，而且是待验收的 release。
- 一个一次性测试 agent：有自己的飞书应用（app_id 公开在 kind:30177），owner 是 PO，它的 `respond_to` 允许 owner 和同事。
- 一位同事的飞书账号，已经在 bridge 绑定（08 用）；另一个频道里的慢任务（06 用）。
- 固定的 Buzz CLI 绝对路径和测试身份；不用 `~/.local/bin/buzz`。

## 用例

每条用例都是 Given / When / Then，后面跟着回执里必须有的证据字段。时间一律是 Unix 秒。

### L4-148-01 在飞书里拉 agent → 出现在频道并发起申请
- Given：agent 的 bot 不在群里。When：PO 在飞书把它拉进群。
- Then：
  - 同步签名身份签了 kind 9000（`p`=agent、`role`=`bot`，带 `feishu-member-op/stream/seq`）。
  - agent 自己签名、另起一个 Thread 的入群申请：@ owner，`/approve JOIN-…` 单独一行，末行是 `buzz-join:v1 JOIN-…`。
  - 申请原样出现在飞书群里。
  - agent（或群助手代发）在飞书里发了一次自我介绍。
- 证据：`feishu_bots_before/after`、`feishu_added_at`、`add_event`、`request_event`、`request_feishu`、`intro_feishu`。

### L4-148-02 owner 在飞书点 ✅ → 开通
- When：PO 在飞书的申请消息上点 ✅（飞书记成 `CheckMark` 或 `DONE`）。
- Then：
  - 镜像签了 kind 7：`e`=申请、`feishu-author`=owner、`join`=编号。
  - agent 进程确实换了（MainPID 变了）。
  - 新进程日志出现 `subscribed to channel <本频道>` 之后，agent 才在申请 Thread 里回复「已开通…」（末行 `buzz-join:v1 JOIN-… active`）。
  - 这条回复在飞书里也在同一个话题下。
- 证据：`feishu_reaction`、`mirror_reaction`、`restart{old_main_pid,new_main_pid,restarted_at,subscribed_at}`、`active_event`、`active_feishu`。

### L4-148-03 owner 在话题里回复 `/approve` → 开通（不 @）
- When：换一个新申请，PO 点申请消息的「回复」，只发 `/approve JOIN-…`，不 @ 任何人。
- Then：镜像转进 Buzz 的回复在申请 Thread 里，带 `feishu-author`=owner 和 `join`；之后的重启、订阅、开通链路和 02 一样。
- 证据：`feishu_command`（`root_id`=申请消息、`mentions`=[]）、`mirror_command`、`restart`、`active_event`。

### L4-148-04 无效审批和提前 @ 都有解释，都不开通
- When：
  - 同事在申请上点 ✅；
  - PO 在群里另起一条消息发 `/approve JOIN-…`；
  - 同事在开通前 @ agent。
- Then：
  - 每一次都由 agent 在对应的 Thread 里回复：前两次以「审批未生效」开头，第三次说明「在等待 owner」。每条回复都带 `feedback-<触发事件 id>`。
  - 至少两轮 timer 之后，状态仍是 `REQUESTED`，没有「已开通」。
- 证据：`non_owner_reaction/feedback`、`stray_command/feedback`、`pending_mention/feedback`、`state_after`、`rounds_observed`、`active_events_before_owner`。

### L4-148-05 开通后真实 @ → 同一个话题里有回复
- When：同事在飞书群里 @ agent 发「回复 PONG」。
- Then：镜像转进 Buzz 的消息带 agent 的 `p`；agent 在同一个 Thread 回复；飞书里的回复也在这个话题下。这条同时补上 #161「真实 @ 得到回复」的证据。
- 证据：`feishu_mention`、`mirror_mention`、`agent_reply`、`reply_feishu`。

### L4-148-06 agent 正忙时到了审批 → 不打断，空闲后才重启
- Given：agent 在另一个频道处理一个约 2 分钟的任务。When：这时 PO 批准它进本群。
- Then：审批时间点观察到 agent 正忙（cgroup 里除了 harness 还有其他进程）；那个任务在原 Thread 回复 DONE 之后才发生重启。
- 证据：`task_event`、`task_done_event`、`approval_at`、`busy_at_approval`、`restart`。

### L4-148-07 agent 的成员变动双向同步
- When：在飞书移出 agent；在 Buzz 里把它加回来；再在 Buzz 里移出。
- Then：
  - 飞书移出后有同步签名身份签的 kind 9001（带 `feishu-member-*`）。
  - Buzz 的加入（kind 9000、`role`=`bot`）之后，飞书群的 bot 名单里有它。
  - Buzz 移出之后，飞书群的 bot 名单里没有它。
- 证据：`feishu_remove{bots_before,bots_after,at,event}`、`buzz_add{event,bots_after,observed_at}`、`buzz_remove{…}`。

### L4-148-08 人的成员变动从飞书同步到 Buzz（需要一位同事配合）
- When：PO 在飞书把同事拉进群，然后再移出。
- Then：同步签名身份先签 kind 9000（`role`=`member`，带 `feishu-member-*`），后签 kind 9001。
- 证据：`feishu_add_person{users_after,at,event}`、`feishu_remove_person{…}`。
- 同事的配合走 #162 下的子 task，事前说清楚会收到哪些消息。

### L4-148-09 人的表情双向同步
- When：同事在飞书里给一条镜像消息点 👍，然后撤回；PO 在 Buzz 里给同一条消息点 👍。
- Then：
  - 镜像签了 kind 7（`e`=目标、`feishu-author`=同事），撤回后签了 kind 5，指向那条 kind 7。
  - Buzz 的表情出现在对应的飞书消息上，操作者是应用。
- 证据：`target_event`、`target_feishu`、`feishu_reaction`、`mirror_reaction`、`feishu_withdrawn_at`、`mirror_withdraw`、`buzz_reaction`、`feishu_reaction_from_buzz`。

## 回执与判定

回执结构：

```json
{"meta": {"suite": "L4-148", "skills_revision": "<sha>", "channel": "<uuid>", "feishu_chat": "oc_…",
          "owner": {"pubkey", "feishu_open_id", "name"}, "agent": {"pubkey", "name", "app_id"},
          "desk_app_id": "cli_…", "signer_pubkeys": ["…"], "mirror_pubkeys": ["…"],
          "colleague": {"pubkey", "feishu_open_id", "name"}},
 "cases": {"L4-148-01": {"status": "passed", …evidence}, "L4-148-08": {"status": "not_run", "reason": "…"}}}
```

- Buzz 事件：用 `buzz` CLI 按 id 读回的**原始签名事件**，字段不能删减。
- 飞书记录：用 `lark-cli im +messages-mget` 或 `+threads-messages-list` 读回，统一成 `message_id / chat_id / create_time / sender_id / sender_type / root_id / text / mentions` 几个字段。成员名单用 `+chat-members-list` 读回。
- 本机观测：`systemctl --user show -p MainPID`，再加上 journal 里 `subscribed to channel` 那一行的时间。
- `not_run` 必须写原因；只要有 `not_run`，就不算验收完成。
- 回执脱敏后提交到 `tests/fixtures/`：不能含 token、env 值、私钥，也不能含同事的真实邮箱。

```bash
BUZZ_MEMBER_SYNC_L4_RECEIPT=skills/agent-harness/buzz-agent-setup/tests/fixtures/<receipt>.json \
  python3 -m unittest skills/agent-harness/buzz-agent-setup/tests/integration/test_feishu_member_sync_l4_receipt.py -v
```

oracle 在 `tests/integration/member_sync_l4_oracle.py`，每条用例对应一个 `assert_*`。不设这个环境变量时，只跑用一次性密钥签的合成回执：它证明 oracle 能认出正确的形状、也能抓出每一种已知缺陷，但它本身不是 L4 证据。
