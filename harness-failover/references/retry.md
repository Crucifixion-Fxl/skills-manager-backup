# 切换后的 retry

## 做什么

`switch --apply --retry`（或单独 `retry`）：

1. 读每个 agent 日志，找失败窗口（`requeueing … attempt=1` 起、`dead-lettering` 止；attempt=1 重新开始一个窗口）。
2. 对每个窗口，**以 owner 身份**读该 channel 窗口内的消息（`buzz messages get --channel <id> --since <ts>`），
   筛出 @ 了该 agent、且其后没有该 agent 回复（直接回复或同一 thread 内的回复）的消息。
3. 在该消息的 thread 里回帖并 @agent：`buzz messages send --reply-to <event> --mention <agent_pubkey>`，正文是固定模板：
   `[harness-failover retry] @<agent> 上面这条消息因 <from> 额度耗尽而处理失败，已自动切换到 <to>。请重新处理这条消息。`

## 哪些消息才算「丢失」

日志里每个失败窗口只有 channel、时间和数量。丢失的消息按下面四条从 relay 重建，缺一不可：
1. 窗口本身**确定是丢失**：被 dead-letter，或紧挨着该 agent 自己日志里的额度错误（一次已恢复的抖动不算）；
2. 作者会被该 agent 的**作者门**放行（`BUZZ_ACP_RESPOND_TO`：`anyone` 不过滤；`owner-only`/`allowlist` 只认 owner 加白名单；`nobody` 或判断不了则全部拒绝）——
   没通过作者门的消息根本不会进 batch，永远「无回复」是设计如此，提醒它等于让 owner 的身份为陌生人的文字背书；
3. 在窗口内提到了该 agent（`--mode any` 放宽此条，但作者门仍生效）；
4. 窗口之后没有该 agent 自己对它（或它所在 thread）的回复。

## 安全边界

- 正文**不含原消息**；提醒本身带 `[harness-failover retry]` 前缀，扫描时被排除，不会「提醒提醒」。
- 默认 dry-run；同一事件 ID 记在 `~/.config/buzz/harness-failover/nudged.json`（0600），只提醒一次；单次最多 10 条；超过 24h 不提醒。
- 切换不干净（有 agent 没加载新配置）时**不发** retry；`--from` / `--to` 只接受 `[A-Za-z0-9._-]{1,32}`，state 里读到的同样校验，识别不出就**拒绝发送**。
- 每成功发一条就立刻写入 `nudged.json`；发送失败或超过上限的进 `pending_retry` 队列，后续每一轮继续，24 小时后放弃。有 `manual` 项或发送失败时退出码为 `4`。
- 已知并接受的风险：agent 被提示注入后，可以自己运行 `retry --apply`，让 owner 身份在**它自己日志里出现过的频道**发一条固定模板的消息（正文不含任何可控文本）。
  要消除它只能让 agent 拿不到 owner 的身份，那就违背了「被 agent 调用时用本地 user 身份」的设计；因此靠模板固定、作者门过滤、上限与去重把影响面收窄。

## 身份

`respond_to=allowlist` 只认 owner；agent 自己 @ 自己会被忽略。所以读 relay 与发提醒**只能以本地 user 身份**。实现：`as_owner=True` 时剥离所有 `BUZZ_*` 变量，
标准 `buzz` wrapper（`BUZZ_PRIVATE_KEY` 为空时才 `source ~/.config/buzz/env`）就会加载 owner 身份。

这是对 `buzz-agent-setup` 红线 1（agent 子进程不得以 owner 身份发消息）的**受限例外**，仅限这两个动作。`BUZZ_CLI` 环境变量可指向别的可执行文件——**测试必须用它注入假 CLI**。

## 找不回的情况（不静默）

- 日志没有事件 ID，只能用「窗口 + @ + 无回复」重建；重建条数 < 日志记的丢失数 → 标 incomplete，进 `manual` 列表（agent / channel / 丢失条数 / 原因）。
- owner 不是成员的私有频道：CLI 返回 auth 错误或空结果，同样进 `manual`。（真机数据：agent 订阅的 10 个频道里 owner 只看得到 9 个。）
- **后备来源（未实现）**：claude 会话 jsonl 里 Buzz turn 带事件 ID（近 170 条里 112 条），grok 有 `prompt_history.jsonl`；可以覆盖 owner 不在的频道，但「失败那次的 prompt 是否被记录」还没验证。
