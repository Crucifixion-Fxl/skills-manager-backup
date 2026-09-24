---
name: harness-failover
description: 检测本机 Buzz agent 当前使用的 harness（grok / claude / codex / glm，可多账号多 profile）是否用完额度（402 / 429 / 周限额 / 5 小时限额），用完则把全部 agent 一起自动切到下一个可用的 profile，切完后用 buzz CLI 在原 thread @agent 让它重试失败的消息。当 Buzz agent 突然不回话、日志里出现 usage balance exhausted / Request rejected (429) / hit your weekly limit / dead-lettering batch、需要「切换 harness」「换账号」「额度用完自动切」「切完 retry」、新增 harness 或 profile、或检测发现新的错误文案要沉淀进本 skill 时使用。
---

# harness-failover

## Description

本机（systemd --user）上的 Buzz agent 共用几个 harness 账号。额度用完时，buzz-acp **不会退出**：turn 反复失败、重试 10 次（约 50 分钟）后**整条消息被丢弃**。本 skill 让这件事变成「检测 → 决策 → 一起切换 → 找回并重试」。

可运行脚本在 [scripts/](scripts/)，入口 `scripts/harness-failover`。测试：`uv run --extra dev pytest skills/harness-failover/tests -q`。

## Rules

1. **只在「当前 harness 已判定耗尽」时才切换。** `unknown` 不切、`unavailable` 不选；不自动回切（回切会白白重启全部 agent）。恢复时刻已知的 profile，到点后才会重新被当成可用。
2. **要切就全部 agent 一起切。** 账号是共享的，只切一部分没有意义。切换是原子的：先改全部 env（每个先在临时文件里用 bash 验证），任何一个失败就整体回滚、不重启任何 unit。**唯一例外是被钉住的 agent**（env 里 `HARNESS_FAILOVER_PINNED=1`）：它代表一个手动配的、不在任何 profile 里的一次性组合（例如某个 agent 单独用某个 profile 没有的模型），`switch --apply` 会跳过它——不改它的 env、不重启它，也不计入它判断「当前 harness 是谁」的多数票。drift 修复（进程配置和自己 env 不一致时的自愈重启）仍然覆盖它，因为那是它跟自己的 env 对齐，不是跟机队对齐。
3. **env 文件只动 harness runtime 变量**（`BUZZ_ACP_AGENT_COMMAND/ARGS/MODEL/EFFORT_LEVEL`、三个 `BUZZ_ACP_MEDIA_*`、`HARNESS_CLAUDE_WRAPPER`、`CLAUDE_CODE_EXECUTABLE`、`CLAUDE_CONFIG_DIR`、`CODEX_HOME`），其余行（含私钥）逐字保留；文件保持 `0600`，备份 `<env>.bak.<ts>-failover` 也是 `0600`。启用 media proxy 的 profile 会把 proxy、真实 adapter 与固定 Buzz CLI 作为一个不可拆分的 runtime tuple 一起切换和回读；脚本从不打印、不复制任何密钥。
4. **切完必须证明「进程真的加载了新配置」**：读 `MainPID` 的 `/proc/<pid>/environ`（只取非密钥的 harness 变量）核对，不是只看 env 文件。有任何一个不对，返回码 3，且**不发 retry**。
5. **一个 profile = adapter + wrapper + 账号 home + model + effort**（见下表）。同一 harness 可以有多个账号；候选里**跳过与已耗尽 profile 同账号**的项。
6. **retry 只发固定模板，永远不引用原消息内容**（频道文本是不可信输入）；默认 dry-run，`--apply` 才发送；同一事件只提醒一次；每次最多 10 条；超过 24h 的不提醒。
7. **找不回的要明说。** 日志只记 channel + 时间窗口 + 数量、**没有事件 ID**，丢失的消息要靠 relay 重建。找回条数少于日志记的丢失数（例如你不在的私有频道）→ 标 `manual` 列给人，**不静默跳过**。
8. **身份（有意的例外）**：被 Buzz agent 调用时，读 relay 和发 retry 提醒**必须用本地 user（owner）身份**——白名单 `respond_to` 只认 owner，agent 自己 @ 自己会被忽略。脚本在这两处以 `as_owner` 调 CLI：剥离全部 `BUZZ_*` 变量，让标准 `buzz` wrapper 去加载 owner env。这是对 `buzz-agent-setup` 红线 1 的**受限例外**，只限这两个动作，不得扩展；agent 不得自己 `source ~/.config/buzz/env`，也不得把 owner 身份用于别的操作。**测试与 CI 一律用 `BUZZ_CLI` 注入假 CLI，绝不调真 wrapper。**
9. **新的错误文案/新 harness → 更新本 skill**，走 [references/self-update.md](references/self-update.md)：先红后绿、样本必须脱敏、提 MR 由人 review，**不无人值守自动合并**。

## Profiles（默认，PO 2026-09-19 定）

| profile | harness | model | effort | 账号 / home | 优先级 |
|:--|:--|:--|:--|:--|:--|
| `grok` | grok build | `grok-4.6` | high | `~/.grok/auth.json` | 10 |
| `claude-buzz` | claude-agent-acp | `sonnet` | medium | `~/.claude-buzz`（wrapper `claude-buzz`） | 20 |
| `codex-buzz` | codex-acp | `gpt-5.6-sol` | medium | `~/.codex-buzz`（`CODEX_HOME`） | 30 |
| `glm` | claude-agent-acp + `claude-glm` | `opus[1m]`（wrapper 映射到 glm-5.2） | high | `~/.claude-glm` | 40 |

多账号：在 `~/.config/buzz/harness-profiles.json` 里按 id 增加/覆盖/`"enabled": false`，见 [references/profiles.md](references/profiles.md)。`effort` 走 `BUZZ_ACP_EFFORT_LEVEL`：buzz-acp 按适配器声明的 `thought_level` 类别找配置项，claude 是 `effort`、codex 是 `reasoning_effort`，两者都已用 ACP 握手核实。

## 流程

```bash
H=skills/harness-failover/scripts/harness-failover   # 安装后见 references/install.md
$H profiles                       # 列出 profile，标出当前
$H detect [--probe] [--json]      # 不改任何 agent：每个 profile 的健康度 + 建议 + 配置漂移（会更新本机学习记录，失败不影响判断）
$H switch --to auto               # dry-run：会切到哪
$H switch --to auto --apply --probe --retry   # 检测→切换全部 agent→证明已加载→找回并提醒重试
$H retry --since-hours 6 [--apply]            # 单独做找回 + 提醒
$H learn [--propose|--prune]      # 日志里没有签名解释的可疑错误
$H notify [--status|--setup|--test]  # 飞书通知：查看状态 / 从 lark-cli 读你的 open_id 并启用 / 发一条测试消息
```

返回码：`0` 正常/无需切换/另一个实例在跑（busy）；`1` 输入错误或已回滚；`2` **stuck**（当前耗尽且无可用备选，需要人）；
`3` 已切换但有 agent 未加载新配置（下一轮自动修复）；`4` retry 有需要人处理的项（`manual` 或发送失败）；`6` 认不出当前 harness（env 与任何 profile 都对不上，拒绝乱切）。

## 飞书通知

耗尽、切换成功、失败都会用 `lark-cli`（bot 身份）私聊你，详见 [references/notify.md](references/notify.md)。

| 事件 | 何时发 | 重复 |
|:--|:--|:--|
| 额度耗尽 | 判定当前 harness 耗尽、**开始切换前**（不用等 90 秒切完） | 恢复时刻已知：每个周期一次；未知：最多每 6 小时一次 |
| 耗尽且没有可用备选（stuck） | 没有可切的目标 | 每 6 小时提醒一次 |
| 切换成功 | 全部 agent 已确认加载新配置（含 retry 摘要） | 每次切换一条 |
| 失败 | 回滚 / 半切 / 认不出当前 harness / retry 要人处理 / 漂移修复未完成 / 意外异常（含启动阶段崩溃、被 SIGTERM 终止） | 同一问题每 6 小时最多一次 |
| 漂移已修复 | 下一轮修好了半切的 agent | 一次 |

首次使用：`$H notify --setup`（只读你的 open_id 并写本机配置，不发消息），再 `$H notify --test`（**只发一条**测试消息）。
通知是尽力而为：`lark-cli` 缺失、超时或报错**不会**改变切换的结果和退出码，且没发出去的不会被记为已通知，下一轮重试。
消息只由固定模板和通过严格字符过滤的字段组成（profile id、agent 名、时间、数量），**不含日志正文、错误原文、密钥或频道内容**。

## 运行保障（每 5 分钟的定时器是无人值守的，这些是它不出事的前提）

- **同一时间只有一个修改类命令**（`switch --apply` / `retry --apply` / `learn --prune`）：`flock` 锁，抢不到就打印 `busy` 并退出 0。
- **`last_switch` 先于重启写入**：中途崩溃或被杀，下一轮不会再全员重启一次；纯回滚（什么都没重启）会还原它。
- **每一轮都对账**：把每个在跑的 agent 的 `/proc/<pid>/environ` 与它的 env 文件比对，不一致的（例如重启超时留下的半切状态）只重启这几个。
- **停掉的 unit 不会被拉起**：`inactive` 的 agent 只更新 env，不重启、不算失败。
- **提醒失败会排队**：发送失败或超过每次 10 条上限的，记入 `pending_retry`，后续每一轮继续做，24 小时后放弃；每成功一条就立刻落盘去重，崩溃不会重复提醒。
- **只对「确定丢了」的窗口提醒**：被 dead-letter 的，或紧挨着 agent 自己日志里额度错误的；一次已经恢复的抖动不会被重做（重做可能有副作用）。
- **学习记录是尽力而为**：磁盘满或文件损坏只会跳过它，绝不阻止切换决策。

定时器（每 5 分钟，只读历史）：`switch --to auto --apply --probe --retry`——健康的一轮**不探测、不花额度**；只有判定当前 harness 耗尽、需要切换时，才对候选做一次真实请求核实，再决定切到哪个；显式 `--to <profile> --probe` 只核实那一个 profile；显式目标只要**已耗尽或不可用**（历史或探测判定，例如没登录）就拒绝切换，`--force` 才能覆盖，见 [references/install.md](references/install.md)。

## 检测：怎么判断「耗尽」

| harness | 证据来源 | 判定 |
|:--|:--|:--|
| grok | `~/.grok/logs/unified.jsonl` 的 `billing: fetched credits config` + `inference_failed 402` | 周期内 credits ≥ 100% 且按需/预充余额为 0 → 耗尽，恢复时刻 = `billingPeriodEnd` |
| claude / glm | `<home>/projects/*/*.jsonl` 近 48h 的 `api_error` 与 `isApiErrorMessage` | **最新事件**是额度错误且恢复时刻在未来 → 耗尽；最新是成功回复 → 可用；无恢复时刻的错误只在 30 分钟内算数 |
| codex | `codex login status`（两个输出流 + 返回码）+ 探测 | 未登录 → `unavailable`；已登录但无历史 → `unknown`（需 `--probe` 验证才会被选中） |

错误签名是**数据**：[assets/signatures.json](assets/signatures.json)，样本在 `tests/fixtures/samples.json`，两者必须成对出现（测试强制）。当前已知签名与证据见 [references/detection.md](references/detection.md)。

## 已知限制（如实）

- **找回不是全覆盖**：检测能覆盖 agent 所在的**所有**频道（含私有、DM，因为看的是本机日志），但重建丢失消息要靠 relay，只有 owner 是成员的频道才读得到。读不到的会列为 `manual`。本地会话记录（claude jsonl / grok `prompt_history.jsonl`）里有 Buzz turn 的事件 ID，可作为后备来源，**尚未实现**。
- **只提醒 agent 的作者门会放行的人**：按每个 agent 的 `BUZZ_ACP_RESPOND_TO` / `..._ALLOWLIST` / `..._AGENT_OWNER` 过滤（未通过作者门的消息根本不会进 batch，不可能是「丢失」；否则 owner 的身份会为陌生人的文字背书）。判断不了时按拒绝处理。
- **「已回复」只认 e-tag 引用**：agent 若用不带 `--reply-to` 的新消息回复，会被当成没回复；一次读取只看 200 条，满页会标 incomplete。重做可能有副作用，所以提醒默认 dry-run、单次最多 10 条。
- **切换过程被 SIGKILL/断电打断不做日志式回滚**：靠备份 + 下一轮的漂移对账兜底；env 文件的写入本身是原子替换。
- **「已加载新配置」证明的是环境，不是存活**：切完 8 秒后的 `active` + `/proc` 环境一致；随后才崩溃循环的 agent 要靠 systemd/监控发现。
- **profiles 文件与 wrapper 由同一 uid 控制**：只要求文件属于你且不被所有人可写；能写它的进程本来就能读 owner 密钥，这里不构成提权，但请当作可信配置对待。
- **restart 之后的备份会累积**：每次切换每个 env 留一份完整副本（0600），只保留最近 5 份。
- codex 路径：`codex-buzz` 必须先**人工登录**（`CODEX_HOME=~/.codex-buzz codex login`）；登录后请先 `detect --probe` 确认能出 token，再依赖它做备选。codex 的额度错误文案未采样，签名 `codex-usage-limit` 为 `verified:false`。
- **`notify.json` / `state.json` 由同一 uid 控制**，没有属主/权限校验（不同于 profiles 文件）：能写它们的进程能改收件人、关掉通知或指向别的可执行文件；同 uid 本来就能改 unit 和工具本身，这里不构成提权，请当作可信配置。SIGKILL/断电无法通知（没有进程可发）。
- **通知依赖 lark-cli 与 bot 权限**：bot 需要 `im:message:send_as_bot`，且与你已有单聊关系；CLI 返回成功不等于你已读。飞书本身不可用时，这条通道无法告警（只有 systemd 日志）。
- 探测消耗一次极小的真实请求；grok 不探测（billing 历史是权威来源）。
- 5 分钟粒度：最坏耗尽后 5 分钟发现；buzz-acp 约 50 分钟后才丢弃消息，多数情况来得及，但切换前已在途的 turn 仍可能丢——所以才有 retry。

## Examples

### ❌ Bad — 靠进程退出码或「出现过错误」判断，且只切一部分

2026-09-18 事故：额度耗尽时 buzz-acp 与 adapter 都不退出，`Restart=on-failure` 感知不到；07:36 首次 429，重试 10 次后 08:30 整条消息被丢弃；
人工切换在 23:14 才完成（约 15.5 小时）。用「窗口内出现过错误」也会误判：`.claude-buzz` 23:06 撞了周限额，00:00 重置后早已恢复。

```bash
systemctl --user restart buzz-local-jchen-ubuntu-192-168-20-24   # 只切一个 agent，账号是共享的，其余 21 个继续失败
```

### ✅ Good — 看各 harness 自己的历史，耗尽才切，全部一起切，切完证明并找回

```bash
H=skills/harness-failover/scripts/harness-failover
$H detect --probe
#   grok         EXHAUSTED  until 2026-09-25T01:03:18Z — credits 100% 且无按需/预充余额（402 usage balance exhausted）
#   claude-buzz  OK         — 最近一次事件是成功回复
#   decision: switch → claude-buzz
$H switch --to auto --apply --probe --retry
#   switched 22 agents to claude-buzz: every process confirmed to load the new config
#   retry: windows=1 planned=2 sent=2 ...   manual: agent=… channel=… lost_events=1 — auth: not a member
```

### ✅ Good — 某个 agent 要单独试一个 profile 里没有的模型，先钉住再改

```bash
echo 'HARNESS_FAILOVER_PINNED=1' >> ~/.config/buzz/agents/neopace-investigator.env  # 连同 MODEL/CODEX_HOME 一起手动改
systemctl --user restart buzz-local-neopace-investigator.service
$H switch --to auto --apply   # 之后任何一次机队切换都会跳过它
#   switched 29 agents to claude-buzz: every process confirmed to load the new config; 1 pinned agent(s) skipped: neopace-investigator
```

### ❌ Bad — 想单独锁定一个 agent 的 harness，却没有钉住它

```bash
# 手动把某个 agent 改成一个 profile 之外的模型，以为“不碰它就不会被切”
sed -i 's/BUZZ_ACP_MODEL=sonnet/BUZZ_ACP_MODEL=gpt-6-astra/' ~/.config/buzz/agents/neopace-investigator.env
# 下一次机队切换（无论是自动耗尽触发，还是人工 switch --apply）会把它和其余 29 个一起覆盖回目标 profile：
# apply_switch 对拿到的每一个 env 文件都会重写，不会因为内容特殊就跳过——不加 HARNESS_FAILOVER_PINNED=1，这个手改随时被冲掉。
```

### ✅ Good — 遇到没见过的错误，走更新流程而不是猜

```bash
$H detect            # …末尾: 1 unclassified error sample(s) — run `harness-failover learn --propose`
$H learn --propose   # 脱敏样本 + 签名/fixture 草稿 → 先红后绿 → MR（references/self-update.md）
```
