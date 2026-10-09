# 建频道默认项：飞书妙记 → 已有 Issue

这是新建业务 Channel 的默认配置项：生成候选 Workflow、给默认 `-desk`（或用户指定且已注册的其它 agent）加载 `meeting-issue-notes`，在接入清单中逐项验收。**默认准备不等于默认放行**；缺权限/来源验证/幂等 writer 时保留 disabled 或 draft 并记录 owner/恢复条件，不能把模板说成现网已配置。本次只分发 Skill/模板与 how-to，不创建线上 Workflow、不写生产 Issue、不向同事发消息。

业务入口是**会后人把妙记链接发到已绑定飞书群**。不邀请 bot 参会、不订阅日程。也接受当前授权范围内、正文或 notes 含妙记链接的 GitLab Issue URL。已有自然语言任务可直接 @ agent 调用专用 Skill；纯链接自动唤醒需完成下方接入。裸 `#110` 不在原生自动 filter 范围，需完整链接或明确 @ agent 并提供唯一项目上下文。

## 职责

- Setup：频道/群绑定、注册 agent、Skills、独立权限、可信项目配置、native Workflow、来源回读与幂等执行能力、正负向验收。
- `meeting-issue-notes`：资料识别/读取、说话人与时间证据、议题到已有 Issue 匹配、最小计划/决策增量、幂等/未知写入处理。
- 后续 Workflow：由 owner 独立配置的状态驱动动作。`ready-for-implementation` 只是可能的示例，本流程不设置该状态、不硬编码 @`-dev`、不自动实现或合并。

## 已核对能力与样例

2026-10-09 只读读取 [infra/buzz-deploy#110](https://gitlab.addx.ai/infra/buzz-deploy/-/issues/110) 正文和全部 5 条 notes。正文包含一个妙记直链和一个 docx 链接；notes 包含同一妙记、独立测试驱动写入的证据评论与状态记录。因此可验证的容器路径是“精确 Issue → 正文 + 全部分页 notes → 去重后的妙记链接”，不是把 Issue 当妙记 API。

该记录证明个人身份读转写、私有测试 Workflow 定义回读及个人测试驱动追加评论；它明确**没有**证明 agent bot 身份可读转写、native trigger/mention 成功或完整自动回写。这里不复刻会议原文，不把样例中的个人身份作为 agent 配置。

Buzz 源码检查点：`bfc201b15736eb5a005999d3ff3e7d4ea244812b`（本机 `/home/jchen/buzz-paragraph-display-20261008`，源码快照，非部署声明）：

| 能力 | 源码事实 | 使用边界 |
|---|---|---|
| `trigger.on: message_posted`、`filter` | `crates/buzz-workflow/src/schema.rs` TriggerDef | 无 regex、无虚构 minutes/calendar trigger |
| `trigger_author`、`trigger_text`、`str_contains`、`&&`/`||`/`!` | `executor.rs` build_eval_context / evaluate_condition | author 来自签名者；不提供飞书 `sender_type` |
| `{{trigger.message_id}}` / `{{trigger.channel_id}}` | `executor.rs` template resolver / TriggerContext | 必须在目标 relay 回读触发输出；未渲染/空值停止，不搜索“最新消息”代替 |
| `send_message` 的 `reply_in_thread` | 当前源码有；旧 relay 0.2.1 reference 记为不支持 | 本模板省略该字段，由 agent 精确回复原 source Thread；升级不能推断线上已支持 |
| Workflow mention 唤醒 agent | 已有 schedule/个人待办契约 | 目标频道必须实测输出真实注册 agent mention 并被 worker 接收；不能只看文本含 @ |

这不是一个新的 SaaS connector：原生 Workflow 只筛选候选并唤醒。它不读取链接、不验证消息原人、不判断权限、不写 Issue。镜像 signer 可能与 Desk 相同；因此正文里的署名和 author allowlist 都不能授予写入权。

## 新建频道接入步骤

1. 沿用已确认的 Channel/群/受众与 Canvas repo 清单；默认目标取本频道已注册 `-desk`，如用户选择其它 agent，回读 bot membership、kind:0/30177 和当前 worker 订阅。配置中记录精确 name/pubkey；不存在则 pending，不能捏造名字。加入 `meeting-issue-notes` 与已有 `gitlab-issue-sop`，刷新真实加载 revision 后验收。
2. owner 固定批准的 GitLab host/project id/path、动作预算（默认自有证据 note；正文受管区另有授权才写）、会议资料披露出口、唯一 writer/ledger 路径；以当前可信 Canvas repo 清单取交集，缺/变更/多义 fail closed。Canvas 不存 token、不自行授予新增 repo 权限。
3. 读能力预检：以**该 agent 已批准的身份**读取一条获准样例 metadata+原始 transcript，并确认原消息回读身份、群/Channel 绑定与 sender_type。用户身份实测不替代 agent 验收；缺 scope、资源共享或原消息安全回读时保留 draft，owner 负责恢复。不借个人 profile/其它 bot，不自动申请/扩权。
4. 回读实际入站事件，收集允许的 human signer 和本绑定镜像 signer；记录 signer provenance。Workflow filter 的 publisher allowlist 仅缩小候选面。使用镜像时必须另外能从精确原消息确认 user sender，并拒绝 bot/同步回流。原群的人发同链接可触发；Agent/Workflow/GitLab sync 消息不能写回。
5. 使用 [模板](workflows/minutes-issue-wake.yaml) 和 renderer 生成 **disabled** YAML。`issue-project` 来自受保护配置与可信 Canvas 交集；不能从待处理正文填入。模板将 source event id 传给 Skill，不把不可信正文插入指令；wake 不回显输入链接，所有结果带结果 marker，sync marker 排除再加原消息身份检查，阻断循环。字符串 prefix 筛选不是 URL 校验，Skill 仍做完整 URL parser 校验。示例只生成本地文件：

   ```bash
   python3 skills/agent-harness/buzz-agent-setup/scripts/render_minutes_workflow.py \
     --agent <verified-desk-or-agent-name> \
     --publisher <verified-human-or-binding-mirror-hex-pubkey> \
     --minutes-host <tenant>.feishu.cn \
     --issue-project gitlab.addx.ai/<group>/<project> > minutes-issue-wake.rendered.yaml
   ```

6. 持有现有授权时才在隔离 Channel 或批准的目标 Channel 下发，复用 runtime-setup 的 `workflows create/update --channel` 路线及原作者身份；读回完整定义、owner、channel、enabled。启用前按下表验收；缺前提就保留 disabled，并将默认项记为 pending。不要为了验收增加权限/创建业务 Issue。无测试写授权时只跑 dry-run 并标明写回未验收。
7. 启用需持久 ledger、跨进程锁和单 writer 的实际实现可用，并验证未知写结果只读对账。当前离线 helper 不提供这些机制。启用后使用说明由 setup 正常既有步骤发送一次：发直链或容器 Issue、更新范围与来源格式、如何补齐权限/唯一目标；无发群授权时只交付文字草稿。分享材料：[单文件 HTML how-to](../../../../docs/agent-harness/how-to/meeting-to-issue.html)。

## 验收矩阵与回执

| 案例 | 必须看到的结果 |
|---|---|
| 人发允许的直链 / 含直链的 Issue | 原消息→native trigger→固定注册 agent→身份/范围核验；两个入口分别验收 |
| 同链接重发 / 不同消息同会议同版本同 Issue | 无新增评论、无改写；返回 no-op |
| 会议新版本 | 仅新增/修改/撤回条目；未变化条目保持不动；来源版本有序 |
| Issue 里多个妙记 / 一条议题多个候选 | 待确认；没有“自动选第一个”或批量写全部 |
| repo 越界 / Canvas 不可信 / 恶意指令 / 同名 agent | 不写、不扩权；说明可恢复条件 |
| bot/Workflow 输出 / GitLab 同步包含同链接 | 不回写、不形成二次唤醒链；原 sender 核验是硬前提 |
| 缺转写权限 / 仅摘要 / 姓名或时间缺失 | 缺口或降级纪要；未知明确未知，不造发言归属 |
| 请求超时但服务器写成功 / ledger pending 重启 | 只读对账找到同 operation；不二次 POST/PUT |
| 并发 / 分页不完整 / 多个自有 marker | 单 writer/锁生效或停止；不能把部分扫描当不存在 |
| 结果回读 | project/IID、note id/author、source hash、operation id 和内容 hash 匹配 |

回执不含逐字稿/密钥：candidate SHA、配置 hash、channel/agent 绑定、read identity、原消息/event/workflow id、源版本 hash、目标 project/IID、note id、no-op 与负向结果、writer/锁/ledger 能力和恢复条件。分别写 `template=prepared`、`local_contract=pass/fail`、`live=pending/pass`；不能从离线测试推断真实平台成功。
