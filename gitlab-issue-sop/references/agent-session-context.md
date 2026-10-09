# Agent 环境与中断恢复契约

Issue 是恢复入口。所有 AI 发起的 create/update/close 都携带**执行这次操作的实际会话**信息，让人能找到会话，也能在会话不可恢复时继续工作。创建者快照不代表当前执行者；后续操作不得沿用旧 session ID。人工普通操作不要求伪造 AI 字段。

## 统一字段

| 字段 | 要求 |
|------|------|
| agent / harness | 当前 Agent 名或角色；harness 取 grok/claude/codex/glm/other，未知写 other，实际工具名可另补；可得时记版本与模型，未知不猜 |
| session id / source | 实际 session/thread ID 及来源；Codex 可读取真实存在的 `CODEX_THREAD_ID`，Grok 可读取 `GROK_SESSION_ID`；其他工具只读其实际暴露的信息。缺失写 `unknown` + 原因，不用 PID/时间戳冒充 |
| environment | 本机 CLI / IDE / 远程 runner 等运行方式、主机或稳定实例标识、OS、cwd、repo/worktree 路径；容器/远程环境补父主机或平台入口（可得时） |
| branch / head / dirty | 当前分支、完整候选 SHA、未提交改动摘要或持久补丁位置；尚无仓库写 `n/a` + 原因 |
| resume | 有效的会话恢复入口/经核验的命令，或平台 channel UUID + thread root；本机仅有 ID 时写 ID 与主机/目录，不能编恢复命令。另给持久 handoff/证据入口 |
| operation / event id / time | create/update/close/reopen、具体变更、操作前生成并保存的唯一 event ID、含时区的时间；event ID 是操作标识，不是 session ID |
| status / next / owner | 会话认领状态（active/paused/released）、下一步、负责的人/角色与阻塞解除条件；与 Issue 的 open/closed 或 status label 分开 |

只采集这些必要字段。不得粘贴全量环境变量、token、Cookie、带凭据的 remote URL 或未经脱敏的完整 transcript。路径指针不等于内容备份：未提交工作需注明是否已有持久副本；`/tmp`、PID、进程内 handle 和容器路径只能辅助定位，不能是唯一恢复入口。

## 写入与回读

| 操作 | 记录位置与顺序 |
|------|----------------|
| create | 生成 event ID，description 独立附录写入创建快照；创建后 GET Issue 核对字段并保存 Issue URL/IID。创建不自动代表 active 认领 |
| update / reopen | 执行业务变更，复用同次进展/需求修订/MR comment 写入具体 delta、当前环境和 event ID；GET Issue 与 note 核对字段、note ID 和结果。单次 comment 可同时充当变更与回执 |
| close | 先写关闭理由、验收/取消/重复证据、当前环境和 event ID，并回读 note；再关闭并 GET 确认 closed。回执区分“拟关闭”与“已确认关闭”，成功后补确认结果 |

每个受影响 Issue 都要有记录；批量操作复用同一环境快照，各 Issue 分别留 event ID 和 URL。已有有效同次回执就复用；不能因为本会话已经认领而省略当前写操作的来源。API 状态变更与 note 写入不具备事务性：任一步失败就保存 `待写回` / `待回读` 和实际已完成步骤，不能声称全部同步。

重试前查目标字段与 event ID。创建响应丢失时先查重并寻找创建快照，找到就复用；更新响应丢失先核对状态，避免再次改派或重复通知。event ID 便于查证，但不是服务端唯一性约束；不能把一次查询无结果当成可以盲目重放。只读查询不要求新增评论。

## 会话认领与检查点

对已绑定 Issue 做第一次会改变仓库或推进实施状态的动作前，先追加 `AI 接手` comment，带统一字段和 `status: active`。仅管理 Issue 时可以写 `released`，不能把一个 create/update 记录当成认领锁。

看到其他会话仍 active 时先核实，不凭更新时间久远或断连推断其已退出；没有明确接手授权时停止冲突操作。新会话接手追加 `supersedes: <旧认领 note URL/ID>`，保留历史。认领 comment 是协作信号，不是原子互斥锁。

push、MR 变化、可独立验收功能完成、阻塞变化与 session 清理时更新检查点；本地每次 commit 不刷评论。合并到已有回执，写明：

- 当前候选 SHA、branch/worktree 和未提交工作是否已持久保存；
- 已完成/未完成、下一步与 owner，明确当前授权范围和未取得的审批；
- 验证的候选 SHA、环境/依赖/配置/产物/数据条件与证据链接；变更或未知结果使相关证据失效；
- 长任务的一次运行 handle/PID、日志位置、超时与退出码；尚无退出码写 `unknown/running`，重启后核验进程，不复用旧 PID 判断完成。

session 清理先写 `paused` 或 `released` 及剩余项，再删除 worktree。仅在范围完成且验收条件满足时关闭；取消/重复关闭要写理由和承接 Issue。被中断的 active 记录只是最后已知状态，不能当成任务已完成。

## 回执模板

创建时标题改成“创建者环境快照”；其他操作在已有 comment 中嵌入此块。

```markdown
### Agent 操作与恢复信息
- operation / event id / time: <update: labels / UUID / ISO-8601 with timezone>
- agent / harness: <角色 / 实际工具、可得版本与模型>
- session id / source: <实际 ID / 实际来源，或 unknown + 原因>
- environment: <运行方式 / 主机或实例 / OS / cwd / worktree>
- branch / head / dirty: <分支 / 完整 SHA / 未提交摘要或持久补丁>
- resume / handoff: <真实恢复入口 / 持久交接与证据链接>
- status: <active | paused | released>
- result: <具体 delta、已成功步骤、待写回/待回读步骤>
- evidence: <MR/merge SHA、CI/验收链接及对应版本；没有写未验证>
- next / owner / blocker: <下一步 / 负责人 / 恢复条件，或无>
```

回读后的 note URL/ID、Issue 状态和 event ID 保存到本地持久 handoff 或最终交付回执，无需为记录 note 自身 ID 再发一条空评论。

## 断电后恢复顺序

1. 从 Issue 最新操作回执定位原主机、session、持久 handoff 和候选版本；核对当前认领，按已有授权决定恢复原会话或追加接手。
2. 回读 Issue、notes、MR/pipeline，查 event ID；核对中断操作哪些已成功，补缺失回执，不盲目重复 create/update/close。
3. 核对仓库 HEAD、dirty、补丁和测试运行状态；环境未变且完整成功证据有效时复用，未知状态如实标记后做必要验证。
4. 按 next/owner/解除条件继续，保持原授权边界；记录新 session 的来源与 supersedes。恢复记录本身不扩大写入、部署或联系他人的权限。
