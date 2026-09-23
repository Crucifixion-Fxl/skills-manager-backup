# Issue Analysis Routing

本路由用于 `root-cause-analysis` 处理问题类 GitLab Issue。它只决定分析模式与只读证据能力，不授权设计、编码、测试写入、部署、Issue 修改或消息发送。

## 模式选择

默认从 `ISSUE_TRIAGE` 开始：

- 固定 canonical Issue source 与 revision；
- 解析可信 repository context；
- 区分事实、未知、边界和假设；
- 只声明实际完成的 evidence depth；
- 不输出任何因果或 root-cause 结论。

满足以下任一条件时切换为 `ROOT_CAUSE_INVESTIGATION`：

- 用户明确要求原因、RCA 或故障调查；
- 当前输出准备声称 cause、root cause、因果链或排除替代原因；
- 已获得足以执行 hypothesis discrimination 的 repository/runtime evidence。

这是同一 Skill 内的模式升级，不得重新调用 `addx:root-cause-analysis`，也不得生成 self-review record。执行身份和 mode 只记录在 `methodology_proof`。

## Context gate

只有经审核 mapping 得到 `context_resolution.status=UNIQUE`，并完成 cwd、origin 和 exact-head readback，才允许读取仓库。`AMBIGUOUS` 和 `UNMAPPED` 保持 source-only；若一个可由人回答的问题能唯一解锁 mapping，则输出 `NEEDS_INPUT`，否则生成诚实的 source-only Artifact。

不得把当前目录、Issue label、产品名或历史猜测当作 repository identity。canonical source、package identity、privacy 或可信读取证明失败时必须 `BLOCKED`，不能降级为普通 triage。

## Supporting Skill routing

`root-cause-analysis` 提供唯一的问题分析与 RCA 方法论。domain Skill 只用于获取或审查特定领域证据，例如日志、指标、Kubernetes、Sentry、GitLab 或项目约束。

选择 domain Skill 时必须：

1. 由当前 evidence gap 和 Skill trigger 决定，不按关键词堆叠；
2. 保持 `review_only` 或 read-only，不扩大用户授权；
3. 记录 package identity、实际 result、脱敏 findings 和 evidence refs；
4. 将结果写入 `supporting_skill_reviews`，并与 `evidence_ledger` 双向绑定；
5. 缺失或不可用时记录 limitation，不模拟执行结果。

`supporting_skill_reviews` 禁止出现 `addx:root-cause-analysis`；方法论自身的执行证明只能出现在 `methodology_proof`。

## 结果选择

| Condition | Result |
|---|---|
| 缺少一个可由人直接回答且能解锁 admission/context 的事实 | `NEEDS_INPUT` + `ZERO_WRITE` |
| source 有效，但尚未形成足够的竞争假设或 discrimination | `TRIAGED` |
| 已有至少两个竞争假设，但 root-cause gate 未全部通过 | `ANALYZED_NO_ROOT_CAUSE` |
| `ROOT_CAUSE_INVESTIGATION` 完成且全部 gate 通过 | `ROOT_CAUSE_CONFIRMED` |
| source、identity、package proof、privacy 或必要工具证明失败 | `BLOCKED` |

“最可能”“经验上”“与现象一致”不能提升状态。所有终态按 `references/problem-analysis-contract.md` 生成并验证。
