# Problem Analysis Artifact Contract 2.0

本契约定义 `root-cause-analysis` 在问题类 GitLab Issue 上的机器可验证输出。它扩展现有 RCA 方法论，而不是定义第二套问题分析方法。字段名、枚举、ID、哈希和 locator 使用机器值；面向人的判断与说明使用中文。

## 唯一结果

一次 Attempt 只能产生以下结果之一：

1. 一个完整且不可变的 `problem-analysis/2.0` Artifact；
2. 一个 Artifact 外部的 `NEEDS_INPUT` control outcome。

Artifact 的 `status` 只允许：

- `TRIAGED`
- `ANALYZED_NO_ROOT_CAUSE`
- `ROOT_CAUSE_CONFIRMED`
- `BLOCKED`

`READY`、`SUCCESS`、`FAILED`、`LIKELY_ROOT_CAUSE` 和 `NEEDS_INPUT` 都不是 Artifact status。

## Modes

`methodology_proof.mode` 只允许以下值：

| Mode | 用途 | 合法终态 |
|---|---|---|
| `ISSUE_TRIAGE` | 固定 source、界定问题、整理证据并形成可证伪假设；不声称根因 | `TRIAGED`、`ANALYZED_NO_ROOT_CAUSE`、`BLOCKED` |
| `ROOT_CAUSE_INVESTIGATION` | 执行完整 RCA 方法、区分竞争假设并通过 root-cause gate | `ANALYZED_NO_ROOT_CAUSE`、`ROOT_CAUSE_CONFIRMED`、`BLOCKED` |

`ISSUE_TRIAGE` 可以独立结束。只有实际开始因果判定时才切换为 `ROOT_CAUSE_INVESTIGATION`；切换是同一个 `root-cause-analysis` Skill 的模式升级，不得伪造成再次加载另一个问题分析 Skill。

## Artifact fields

Artifact 顶层只允许且必须包含以下字段；额外字段同样拒绝：

| Field | Contract |
|---|---|
| `schema_version` | 精确等于 `problem-analysis/2.0` |
| `artifact_id` | `problem-analysis:<canonical content digest>` |
| `content_sha256` | 移除 `artifact_id` 和 `content_sha256` 后的 canonical Artifact 内容摘要，格式为 `sha256:<64 lowercase hex>` |
| `identity` | workflow/node/attempt、producer、produced time 与 source revision |
| `status` | 本契约允许的四个状态之一 |
| `evidence_depth` | `SOURCE_ONLY`、`REPOSITORY_READ`、`RUNTIME_READ` 之一 |
| `source_baseline` | GitLab Issue 的 canonical source 与 revision |
| `context_resolution` | 项目和仓库映射结论 |
| `methodology_proof` | 当前 `root-cause-analysis` 执行身份、模式与证据绑定 |
| `problem_frame` | 现象、期望、实际、未知项和明确非结论 |
| `impact` | 用户、业务、系统与时间范围；未知值显式标记 |
| `boundary_matrix` | 范围内、范围外、未知及下一步 probe |
| `evidence_ledger` | 可回读的事实、反证和局限 |
| `causal_neighbors` | 相关上下游与替代解释的检查状态 |
| `hypotheses` | 竞争假设、falsifier 和证据状态 |
| `code_findings` | 仅记录真实完成的代码读取 |
| `recommended_actions` | 候选证据探针或明确 handoff，不授权执行 |
| `verification_matrix` | 已运行、未运行和被阻塞的验证项 |
| `open_questions` | 问题、所需原因、责任角色与 blocking 标记 |
| `quality_gate` | 所有终态判定的机器输入 |

Artifact 一旦发布不得原地修改；任何修订必须创建新 `artifact_id` 和新
`content_sha256`，版本 lineage 由执行器的 append-only archive 单独绑定。

## Source 与 context

`source_baseline.kind` 只允许 `GITLAB_ISSUE`。`USER_TEXT`、`DOCUMENT` 和 `OTHER` 明确禁止进入该自动化 Artifact；它们只能作为人机对话中的辅助上下文。

`source_baseline` 必须包含：

- `kind=GITLAB_ISSUE`
- `stable_ref`（canonical GitLab Issue URL）
- `revision`
- `content_sha256`
- `retrieved_at`
- `retrieval_status=VERIFIED|FAILED`

`revision` 必须是 live-read GitLab Issue `updated_at` 原值。`context_resolution.status` 只允许 `UNIQUE`、`AMBIGUOUS` 或 `UNMAPPED`，并包含中文 `explanation`、`candidate_contexts` 及可空的 `repository`。

只有 `UNIQUE` 且完成 cwd、origin 和 exact-head readback 后才允许 `REPOSITORY_READ`。Issue label、产品名、当前目录或调用方传入的猜测都不能单独证明 repository identity。

`repository` 保留为主仓对象；可选 `related_repositories` 最多包含一个关联仓，
省略时保留旧单仓行为。每仓字段相同：`mapping_id`、`cwd_realpath`、
`origin_url`、`exact_head_sha`、`observed_at`。两仓须属于同一个 `mapping_id`；
`UNIQUE` 表示唯一解析的有界仓库组，并不表示任意相关仓都获准读取。
非 `UNIQUE` context 不得携带关联仓。

可信 manifest 最多指定两仓；执行器必须逐仓验证 control plane 指定的分支
（未指定时使用该仓 default branch）、origin、干净工作区和 exact SHA。
任一仓失败不得默默退化为“已验证的两仓组”。未映射保持 `SOURCE_ONLY`，
映射歧义拒绝仓库读取；此能力不授予 runtime 访问。Issue 文本、链接和模型推测
不得自行扩展 manifest 的仓库组。

## GitLab Issue canonical source

source digest 的输入必须来自同一次 live-read，至少包含：`project_id`、`project_path`、`issue_iid`、`title`、`description`、`labels`、`revision` 和 `attachment_refs`。

计算规则：UTF-8 canonical JSON、`sort_keys=true`、`separators=(",", ":")`、labels sort and dedupe。`revision` 必须逐字节等于 live-read GitLab Issue `updated_at`；缺失或与 canonical payload 不匹配时，Artifact status 必须为 `BLOCKED`。

附件只记录稳定引用和实际读取状态。未读取附件时必须明确写入 limitations，不能从文件名或 Issue 摘要推断附件内容。

## Durable evidence privacy

Artifact 只保存最小必要证据：稳定 locator、时间、中文 claim/result
摘要与内容 digest。不得复制 raw sensitive attachments、secret、token、
cookie、完整日志或 unnecessary PII。不能安全脱敏的必要证据使本次
Attempt `BLOCKED`。

## Methodology provenance

每个 Artifact 必须包含一个 `methodology_proof`：

- `skill_id`：精确等于 `addx:root-cause-analysis`；
- `mode`：`ISSUE_TRIAGE` 或 `ROOT_CAUSE_INVESTIGATION`；
- `package_sha256`：已验证时为 `sha256:<64 lowercase hex>`；
- `proof_verified`：是否由可信执行器核验 package；
- `evidence_refs`：引用 `evidence_ledger.evidence_id`；
- `failure_code`、`failure_reason`：仅 proof 失败时出现。

该对象证明当前 Skill 的执行身份和模式，不是二次调用或自我 review。Artifact 不得再为 `addx:root-cause-analysis` 生成 `skill_reviews` 记录，也不得把 Skill 名称或模型自报当作 package proof。

package digest 基于整个 `skills/root-cause-analysis` package-tree manifest：每个 regular file 记录 normalized relative path、size 和 per-file SHA-256，按 path 排序后编码为 canonical JSON，再计算 SHA-256。symlink、超出 package root 的路径、读取期间发生变化的文件或不可解释的文件类型都使 proof 失败。

`proof_verified=false` 时 status 必须为 `BLOCKED`，`package_sha256` 可以为 `null`，且 failure 字段和至少一个 failure evidence ref 必填。`ROOT_CAUSE_CONFIRMED` 要求 `proof_verified=true`、有效 package digest、`mode=ROOT_CAUSE_INVESTIGATION`，并且全部 `methodology_proof.evidence_refs` 都属于 `quality_gate.gate_evidence_ids`。

若调用 domain Skill 收集或审查证据，可写入独立的 `supporting_skill_reviews`。每条记录包含 `skill_id`、`mode=READ_ONLY|REVIEW_ONLY`、`result=EXECUTED|BLOCKED|SKIPPED`、`package_sha256`、`findings` 和 `evidence_refs`；`EXECUTED` 必须有有效 package digest 与非空 evidence refs。这些记录不得复制 `addx:root-cause-analysis`，也不得替代 `methodology_proof`。

## Problem frame 与 boundary

`problem_frame` 包含 `affected_actor`、`scenario`、`observed_behavior`、
`expected_behavior`、`deviation`、`reproducibility` 和 `known_unknowns`。
非根因终态不得在这些或其他人类可读字段中写入肯定根因结论。

`boundary_matrix` 的每一项必须包含 `dimension`、`inside`、`outside`、
`unknown`、`evidence_ids` 和 `next_probe`。`impact` 包含
`affected_surface`、`scope`、`frequency`、`severity_basis` 与
`user_or_business_effect`，不得把潜在影响写成已发生影响。

## Evidence ledger

每条 `evidence_ledger` 记录包含：

- `evidence_id`
- `evidence_class`：`SOURCE`、`REPOSITORY` 或 `RUNTIME`
- `claim`
- `result`
- `locator`
- `observed_at`
- `content_sha256`
- `relation`：`SUPPORTS`、`REFUTES` 或 `CONTEXT`
- `limitations`

`SOURCE_ONLY` 只能包含 source evidence；`REPOSITORY_READ` 必须包含
source 和至少一条 repository evidence；`RUNTIME_READ` 必须同时包含
source、repository 和至少一条 runtime evidence。深度只描述真实读取范围，不代表结论质量。

单仓保留 `repo@<SHA>:<path>:<line>` locator。使用关联仓时，组内所有
`REPOSITORY` evidence 和 `code_findings.file_locator` 必须使用
`repo[<project/path>]@<SHA>:<path>:<line>`，同时按项目路径和 exact SHA 绑定到
已验证仓库；相同 SHA 不能替代项目身份。不得混入未限定仓库的 locator。

每仓的首条 `REPOSITORY` evidence 必须记录该 exact tree 的根 `AGENTS.md:1`。
仅当根文件确实不存在时，允许 `AGENTS.md:absent`；其 `content_sha256` 是对
ASCII 字节 `AGENTS.md absent in git tree <sha>\n` 的 SHA-256（替换完整 SHA，
末尾为一个真实 LF，不是两个转义字符），使用既有 `sha256:` 前缀。
writer 须独立执行 `git ls-tree --full-tree <SHA> -- AGENTS.md` 并确认成功且
输出为空；命令失败、拒绝读取或文件存在都不能接受 absence 声明。
此例外只适用于根 `AGENTS.md`，不得作为 code finding，也不得推广到其他路径。
若 `AGENTS.md` 是 symlink，目标必须在后续 ledger 中按序提供同仓、同 SHA 的读取及
digest 证明，不能只记录链接文本；循环、越界、缺失目标均拒绝；规则链最多 8 个节点，超限拒绝。
仍须应用 workspace 规则，读取现有 `CLAUDE.md` 及适用的嵌套规则；根 `AGENTS.md`
缺失但 `CLAUDE.md` 存在时，必须先读取后者，再记录代码证据。

## Causal neighbors 与 hypotheses

`causal_neighbors` 每项包含 `neighbor_id`、
`relationship=UPSTREAM|DOWNSTREAM|ADJACENT`、`mechanism`、
`check_status=CHECKED|NOT_CHECKED|NOT_APPLICABLE`、`evidence_ids` 和
`remaining_uncertainty`。`CHECKED` 必须有非空、可解析的 evidence refs；
Root-cause gate 要求所有物质性邻接解释均已核验或明确不适用。

每个 hypothesis 至少包含：

- `hypothesis_id`
- `statement`
- `state`：`OPEN`、`KILLED` 或 `CONFIRMED`
- `falsifier`
- `falsifier_result`：`NOT_RUN`、`SURVIVED` 或 `FALSIFIED`
- `evidence_for`
- `evidence_against`
- `next_probe`
- `confidence_basis`

`KILLED` 要求 falsifier 已命中且至少一条 `evidence_against`；`CONFIRMED` 要求 falsifier 存活、至少一条 `evidence_for` 并通过 Root-cause gate。`OPEN` 不得被渲染为“基本确认”“大概率根因”或其他因果结论。

## Code findings 与 verification

`code_findings` 是 `{inspection_status, reason, findings}` 对象；每条 finding
包含 exact-head-bound `file_locator`、`exact_head_sha`、中文 finding 与
`evidence_ids`。`SOURCE_ONLY` 必须是 `inspection_status=NOT_INSPECTED` 且
`findings=[]`。context 不是 `UNIQUE` 时禁止写代码路径、函数名或实现推断。

`verification_matrix` 每项包含 `scenario_id`、`scenario`、`layer`、
`procedure`、`expected_observation`、`status=PASS|FAIL|NOT_RUN|BLOCKED` 和
`evidence_ids`。计划执行不等于已执行。

## Root-cause gate

只有 `ROOT_CAUSE_INVESTIGATION` 可以输出 `ROOT_CAUSE_CONFIRMED`，且必须同时满足：

- at least two competing hypotheses；
- at least one KILLED hypothesis；
- at least one CONFIRMED hypothesis；
- every hypothesis has a falsifier and recorded falsifier result；
- no material competing hypothesis remains OPEN；
- no gate-relevant falsifier remains NOT_RUN；
- relevant causal neighbors are checked and reconciled；
- magnitude and direction checks agree with observed evidence；
- no unexplained symptoms remain hidden；
- source、context、methodology proof 和全部 gate evidence 可回读；
- `methodology_proof.mode=ROOT_CAUSE_INVESTIGATION` 且 proof 已验证。

任一条件不满足都不能输出 `ROOT_CAUSE_CONFIRMED`。已形成至少两个假设但 gate 未通过时使用 `ANALYZED_NO_ROOT_CAUSE`；尚未开始有效区分时使用 `TRIAGED`；source、identity、privacy 或可信执行证明失败时使用 `BLOCKED`。

## Quality gate

`quality_gate` 至少包含以下 boolean：

- `source_verified`
- `context_claims_bounded`
- `evidence_traceable`
- `competing_hypotheses_present`
- `falsifiers_present`
- `causal_neighbors_reconciled`
- `magnitude_direction_reconciled`
- `no_unexplained_symptoms`
- `methodology_proof_verified`
- `root_cause_gate_passed`

还必须包含 `gate_evidence_ids` 与 `missing_requirements`。所有 boolean 和 status 必须由结构化字段决定，不能由摘要文案覆盖。

## `NEEDS_INPUT` control outcome

`NEEDS_INPUT` 仅用于缺少一个可由人回答、且答案会直接解锁 source、context 或 admission 的事实。它不生成 Artifact，不写 GitLab，也不能绕过 identity、package、privacy 或 evidence proof 失败。

最小结构：

```yaml
schema_version: problem-analysis-control/1.0
outcome_kind: NEEDS_INPUT
write_policy: ZERO_WRITE
question: <一个可直接回答的问题>
reason: <缺失事实如何阻塞本次分析>
```

禁止出现 `artifact_id`、`content_sha256` 或任何 Artifact `status`。

若 control outcome 携带 repository context，必须复用上述同一有界仓库组校验；
`NEEDS_INPUT` 不得借由不同的 context 校验路径绕过仓库身份或读取范围约束。

## Idempotency 与发布边界

幂等键至少绑定 `project_id`、`issue_iid`、source revision、source content digest、Skill package digest 和 mode。相同键只能绑定一个不可变结果；不同 digest 不得复用旧结果。

本契约不授权修改 Issue、创建 MR、修改代码、运行有副作用测试、部署、生产操作或发送外部消息。执行器必须在独立授权与写入策略下处理这些动作。
