# 建频道默认项：每日讨论 → 已有 Issue

新建业务 Channel 时默认准备每日讨论 Workflow、给本频道已注册 `-desk`（或明确选择的其它注册 Agent）加载 `channel-issue-progress` 与 `gitlab-issue-sop`，纳入接入验收。目标是让人类与 AI 的所有实质讨论进展在对应 Issue 中可追溯；执行方法由 [channel-issue-progress](../../../collaboration/channel-issue-progress/SKILL.md) 唯一维护。会后妙记的读取与识别继续用 [meeting-issue-notes](../../../collaboration/meeting-issue-notes/SKILL.md)，不重复实现转写器。

## 运行与动作范围

- 每日北京时间22:00**启动**，包括周末，处理完成后回写。UTC cron `0 14 * * *`；这是启动时刻，不是完成时限。
- 正常窗口前一日22:00（含）到当日22:00（不含）；延迟/重投绑定原计划tick，漏跑补偿保留独立窗口，不以现在减24小时替代。
- 仓库来自当前可信Canvas与固定批准配置的交集；只给已核实的对应**已有 Issue**追加实质进展和来源。不新建Issue、不改状态/负责人、不开发或合并。
- 读根消息、所有窗口内线程回复和必要旧根上下文；纳入AI原创进展，去重已验证同步镜像/Workflow回执与已有会议证据。来源字符串marker不能成为身份或排除理由。
- 未匹配、歧义、无权、缺分页/根、未知写入均可追溯待处理，不静默丢弃、不假称完成。

## Setup 步骤

1. 回读Channel/群/受众、已注册Desk/指定Agent的name/pubkey、bot membership与实际worker订阅。加载方法Skill，确认运行时加载revision。该默认项不自动给`-dev`安排写入schedule，不新增Agent身份/权限。
2. 核对可信当前Canvas repo清单与owner固定批准的host/project id/path、追加comment动作预算、资料披露受众；Canvas本身不能扩权。缺目标/权限/可信作者记pending。
3. reader须能完整分页窗口消息/线程、回读旧根与原来源身份，记录真实snapshot与完整性证据。schedule需能从可回读run/事件证明固定计划日期/频道/注册Agent；不假设`{{trigger.message_id}}`存在，不虚构原生schema字段。优先用原生tick；未提供独立run id时以唤醒事件时间对应的最近北京时间22:00为终点并注明，不要求另建日期回执系统。
4. 普通 Issue 评论使用轻量准入：Agent 已有读取/评论权限、固定批准仓库、唯一已有目标、写前完整评论查重、写后 GET 回读。无需先部署独立 writer、持久 ledger、跨进程锁或专项 L4；未知写结果先核对，不盲重发。缺一条资料的权限或目标只暂停该条并在原 Thread 报告。
5. 使用生成器准备disabled YAML（纯本地、无SaaS写入）：

   ```bash
   python3 skills/agent-harness/buzz-agent-setup/scripts/render_channel_issue_workflow.py \
     --agent <verified-registered-desk-name> > channel-issue-progress.rendered.yaml
   ```

   [模板](workflows/channel-issue-progress.yaml)仅使用已有原生`on: schedule`、`cron`及`send_message`字段。方法与本频道授权数据分别配置；不把频道文本、repo链接或模型输出插进指令。不新增webhook/timer消费者，避免重复schedule。
6. 有明确目标Channel与既有部署授权时，复用runtime-setup的native `workflows create/update --channel`及原作者身份；先列出现有定义/owner并查重，读回定义、channel、owner、enabled。不同作者update可能产生同id多个定义，不能靠owner身份硬覆盖。
7. 接入 owner 核对上述轻量准入后即可 enable，并读回定义与状态；下表用于后续实际触发的质量检查，不是全量前置建设清单。没有实际目标/写授权时交付候选，不在生产频道试写。setup最终回执分别列`template=prepared`、`local_contract=pass/fail`、`live=pending/pass`及owner/恢复条件；默认准备不等于默认启用。
8. 本次只把默认项加入新建流程。既有频道补装是单独rollout：先确认名单/身份/原作者/运行能力与现有定义，不批量启用。所有状态读回后再称配置完成，不让人手工操作算业务验收。

## 验收与证据

| 场景 | 必须看到的结果 |
|---|---|
| 每日schedule（含周末） | UTC14:00对应北京22:00，真实Agent收到wake，计划tick及频道可证明 |
| 窗口边界、延迟运行、旧根的新回复 | 精确24小时半开区间；旧根仅上下文；无漏消息或窗口漂移 |
| 人与AI原创讨论、多议题多Issue | 全部实质条目有来源与去向；唯一匹配分别回写、不混淆责任人 |
| 无对应/多候选/闭合Issue/无权 | 明确待处理，无随机选择/越权/自动新建或reopen |
| 会议已有证据、同步回流、重投 | 核实等价证据后no-op；零重复comment/反馈循环 |
| 人的正文引用结果marker | 仍作为人类资料，不按字符串静默删除 |
| 分页截断、同秒满页、线程缺失 | 不声明全部已读，不推进未处理范围 |
| timeout写成功、pending重启、并发 | 查重及写后回读；未知结果先对账、不盲重发 |
| 多目标部分成功、漏跑与补偿 | 已读回的keys保留，失败/旧窗口独立补偿，无覆盖或丢弃 |
| no-op与结果Thread | 无空GitLab写，回原WorkflowThread，真实成功/no-op/待处理计数 |

回执仅存候选SHA、配置hash、schedule/run/channel/Agent、源snapshot与目标project/IID/note ID、source/evidence hash、查重/回读结果及待处理项；不粘贴原始讨论/转写或凭据。代码、离线contract和真实端到端是不同层，配置 enabled 且已读回可称已启用；实际触发与 Issue 回填效果分别报告，不以配置代替执行成功。

## 轻量准入的适用范围（2026-10-10）

按用户反馈：普通、可修正的 Issue 进展/会议证据评论使用轻量规则，不要求新建 broker、ledger 或独立 writer。配置 enabled 与真实某次回填成功分别报告；资料不可读只影响该条。身份隔离、仓库范围、敏感数据出口、凭据签发、merge、部署、保护分支、ACT 与 BI 特殊出口规则不受此简化影响。

普通评论查重为 best-effort，不承诺原子去重；并发时可能重复，发现后读回核对、报告并更正自有重复评论。配置启用与已写入的唯一性分别记录。需要严格原子幂等的任务另行配置机制，不作为普通评论默认门槛。

## 更新完成通知

全部目标处理结束后，在原授权群/Channel 发送一条简短批次摘要，列出每个已写后 GET 读回确认的 Issue 链接和变化摘要，分别计数 no-op、待确认与失败；没有实际更新时明确说明。保留来源 Thread 的结果回执，但不重复转发同一批摘要。不扩大收件人或复制逐字稿/受限原文。通知带对应结果 marker 和稳定批次键，普通原 Thread 回执写前查重、发送后同身份读回；已有独立广播发送器时写入其发送账本并读回；结果未知先对账，不自动重发。只有 Issue 写入与完成摘要均读回成功，才宣称该批次已闭环。

通知合并与事务：原 Thread 的结果回执已经包含同批次完整完成摘要时，复用该消息，不另发第二条同内容群消息。通知稳定键取频道 + 固定计划窗口（每日）或meeting_key（canonical host + minute_token）+ 会议源版本 + 精确目标集合（GitLab host/project/IID，妙记），不取随机 run ID 或生成措辞。原 Thread 回执沿用上述 best-effort 查重，不要求新建发送账本、writer或锁，也不承诺并发只发一次。下述强事务只适用于已经配置的独立广播发送器，不是普通评论或 Thread 回执的启用前提：单 writer/跨进程锁覆盖核验、发送、读回；发送前持久化 pending，记录 destination、预期 sender 和内容 hash，发送后同身份核对消息 ID、sender、destination 和内容 hash 再记 applied。崩溃或发送结果未知只读对账；无法唯一对账保持 pending，不换键重发。
