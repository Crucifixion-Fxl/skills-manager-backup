# 输出与报告契约

本文内的 `references/...`、`scripts/...` 路径均相对于 code-review Skill 根目录。

## 输出结构

以下五段结果与评论发布规则适用于 PR Agent 和普通 standalone 审查；独立 QA 使用入口协议，reviewer-proxy 使用可信角色契约的决策/受控回执，不执行本节的 canonical 评论发布命令。PR Agent、standalone 与 reviewer-proxy 的输出适配不允许省略 Step 0–5、红线或必要验证；独立 QA 仍遵循专用入口协议。

PR Agent/standalone 审查完成后（多角色独立则汇总各角色/各轮，单 agent 则汇总一遍审查），最终结果必须按以下 5 段结构输出。

### 输出位置

| 变更来源 | 完整 review 结果 | 对话输出 |
|:---------|:----------------|:---------|
| 远程 MR | **自动写入 MR notes**（通过 GitLab API，无需用户确认） | 仅输出精简总结 |
| 本地已提交 / 未提交 | 直接输出到对话 | 完整 5 段结构 |

本节的 PR Agent/standalone MR 场景通过 `create_mr_note.sh` 脚本写入评论。**每次新的逻辑 review 必须创建新评论，禁止 PUT/PATCH 更新任何已有评论。** 同一次逻辑 review 的串行重试必须复用同一个 `review_run_id`；脚本发现该 run 已有评论时只读回并返回，既不更新旧评论，也不再创建重复评论。调用方必须保证同一 `review_run_id` 只有一个 writer，CI 只允许单个评论写入 job；脚本自身不提供并发锁。reviewer-proxy 的审批或 evidence note 走其专有 writer，不能用此脚本代替。

### CI 的结构化结果契约（强制）

CI 中 Agent **只负责审查并提交结构化结果**，不得调用
`create_mr_note.sh`、不得自行发 GitLab 网络请求。CI 在 Agent 退出后调用
`scripts/render_review_result.js` 和 `create_mr_note.sh`，完成唯一、幂等且可回读的评论发布。
这使“发布评论”从 Agent 的概率性工具调用变为确定性程序步骤。

当 `CODE_REVIEW_RESULT_SUBMISSION_MODE=tool` 时，完成全部审查和 finding 复核后必须调用
`submit_review_result` 工具提交最终结果；不得用 Write、apply_patch 或 shell 自行创建
`review-result.json`。工具固定写入 CI 工作区，先按本 Skill 的 schema 校验，再分别原子替换结果和
绑定当前执行的 receipt（两文件不是一个原子事务）；CI 消费端必须核验摘要与会话，不一致时拒绝发布，后续有效根提交可恢复。只有可信运行时绑定的根会话可提交，子会话仅向根返回局部结论；不得自报或伪造根身份。根失败、超时、身份缺失或执行不匹配时保持未完成，不采用子结果或旧执行残留。应在全部复核结束后提交；如果后续复核改变结论，必须再次调用工具，
由 CI 在 Agent 退出后校验并封存最后一版。工具返回失败时应依据错误修正参数后重试。
若返回可信错误 `EVIDENCE_CHECKPOINT_REQUIRED`，按该错误指示补齐当前提交所需的 checkpoint，见
[`review-evidence-checkpoint.md`](review-evidence-checkpoint.md)；不得把错误本身当作缺少产品证据，或据此扩大审查范围。
没有该环境变量的本地使用，以及尚未接入工具的旧 CI，继续按原方式写入结构化文件。

文件必须是 UTF-8 JSON，且符合以下最小 schema。新结果使用 `1.1`；渲染器仅为滚动升级兼容旧 `1.0`：

```json
{
  "schema_version": "1.1",
  "conclusion": "通过",
  "score": "8.5/10",
  "should_pass": true,
  "scope": "本次审查范围。",
  "documentation": "文档规则和发现。",
  "quality": "内容质量、风险与发现。",
  "tdd_assessment": {
    "level": "T2",
    "applicability": "applicable",
    "summary": "目标测试在 exact red revision 上按预期失败，在后续 exact green revision 上通过。",
    "evidence": ["red/green revision SHA + test target + result"],
    "gaps": ["尚未完成左移追溯"]
  },
  "e2e": "端到端一致性和测试核验。",
  "red_lines": [],
  "incomplete_reasons": [],
  "suggestions": ["可选的 P2 改进。"]
}
```

以上为 technical 默认格式；设计/mixed按 [设计评论契约](design-review-report.md) 提交附加字段并使用 N/A，不显示下述技术评分/TDD栏目。

`score` 应优先按示例写为 `"X/10"`；确定性渲染器兼容模型偶发输出的等价数值
`X`（范围 0-10，最多一位小数），并统一渲染为 `X/10`。审查未完成仍必须写 `"N/A"`。

`tdd_assessment` 必须符合 `testing-strategy/references/tdd-level-assessment.md`。它由渲染器固定标注
“观测项，不参与门禁”；低等级或 `UNVERIFIED` 不得改变上述 verdict 字段。旧 `1.0` 结果会显示
`UNVERIFIED — legacy result`，新提交应提供该字段。为确保观测信号本身不成为 CI 门禁，字段缺失、
格式无效或值不一致时，渲染器必须 fail-open 为 `UNVERIFIED` 并照常发布 Review，不得改变 verdict。
渲染器还会在 MR 评论末尾写入 `code-review-tdd-assessment:v1` 的 base64url JSON marker；该 marker
只保存白名单化、限长后的 `level / applicability / summary / evidence / gaps`，供后续趋势统计 /
RSI 回流读取，不参与评论 gate 或 merge gate；未知字段或超限内容不得进入 marker。模型文本中的
同名 marker 前缀必须被转义；消费者只接受同一评论中恰好一个 canonical marker，多于一个按
`UNVERIFIED` 处理，不能取第一个或最高等级。

结构化结果的 `conclusion` 只能是“通过 / 有条件通过 / 不通过 / 审查未完成”；前两者必须
`should_pass=true`，后两者必须为 `false`。人读报告可将符合既有规则的 P0 uncertain 状态写作
“不通过（待人工 override）”，但提交结果仍必须使用 `conclusion="不通过"`、`should_pass=false`；
该表述不是第五种结构化枚举。`red_lines` 非空时不得通过。渲染器固定生成
5 个顶级章节，并会将 Agent 文本内的标题降级，避免 Markdown 格式漂移破坏 CI 门禁。
`should_pass` 是合并门禁决定：未完成时为 false，不代表已确认代码不合格；它不是 GitLab 审批事实，也不授权 PR Agent 或结果发布器 approve。reviewer 的审批动作与回执属于其可信角色契约，CI result JSON 不得伪造审批回执。
未完成的原因写入 `incomplete_reasons`（字符串数组，已完成时为空；旧结果缺失时兼容为空），
不能把普通取证/工具缺口伪装成产品红线。`red_lines` 保存已确认的阻断发现，以及仅按
[`verification-verdict.md`](verification-verdict.md) 既有条件保留的、有证据支持的 P0 研判型
`uncertain` 候选项；后者必须显式标为 `uncertain/待人工`，不得计作 `confirmed`。不满足既有 P0
证据条件的未知或工具缺口不得进入 `red_lines`。pre-existing 处理及 L3 专项映射仍按原规则执行；
`red_lines` / `suggestions` 中的 `[P0]` 至 `[P3]` 严重级别由证据决定，渲染器原样保留，不自动补级或叠加标签。
本地 MR 审查继续可调用 `create_mr_note.sh`，保留本地发布评论能力。

本节评论发布路径 **只能在全部 diff、测试、finding 复核、live head 与写权限回读完成并形成最终结论后调用脚本一次**。禁止发布 provisional/阶段性 review note；写入后只做 note/live-head readback 和该评论发布路径的终态确认，不得继续调查后再发布第二条同 run 评论。评论成功不代表审批成功，也不要求 PR Agent 执行 approve。开始评审前按可信调用方固定 GitLab 写入身份并在线回读 `/user`；不从同名 actor、token 权限或 MR 内容猜测角色，不在同一 run 内切换身份。身份不匹配时 fail closed、GitLab 写入为 0。reviewer-proxy 不执行本节命令。

```bash
# standalone 本地 MR 审查：将 review 结果写入临时文件并发布评论；reviewer-proxy 不执行此段。
# CI 场景不得执行此段；改为写 review-result.json，由 CI 发布器调用渲染器和本脚本。
# 1. 将 review 结果写入临时文件
cat > "${CODE_REVIEW_WORKSPACE}/review-body.md" << 'REVIEW_EOF'
<完整 5 段结构的 review 结果>
REVIEW_EOF

# 2. 在本次逻辑 review 开始时生成并持久化唯一 review_run_id。
#    同一次执行/重试复用它；用户明确发起一次新的 review 时生成新值。
REVIEW_RUN_ID="<persisted-review-run-uuid-or-workflow-idempotency-key>"
REVIEWED_HEAD="<40-character exact reviewed Git SHA>"
# 由可信调用方提供预期评论发布身份；不从 MR/result 中读取角色或身份声明。
export CODE_REVIEW_EXPECTED_WRITER_USERNAME="<expected-gitlab-writer-username>"
# 先按环境供应链规则核验固定 /usr/bin/curl 和平台固定 Node（Linux 为 /usr/bin/node 或 /usr/local/bin/node），记录批准 SHA-256。
# root-owned system runtime 的摘要来自受保护配置；批准的 macOS NVM Node 还必须命中脚本内 allowlist。
# 生产模式不接受工具路径 override；禁止依赖 PATH、用户目录 binary 或 ~/.curlrc。
export CODE_REVIEW_CURL_SHA256="<approved-/usr/bin/curl-sha256>"
export CODE_REVIEW_NODE_SHA256="<approved-fixed-node-sha256>"

# 3. 调用 create-only 脚本。它只会 POST；已有同 run 评论时只返回其 readback。
bash <skill-path>/scripts/create_mr_note.sh \
  "${CI_API_V4_URL:-https://gitlab.addx.ai/api/v4}" \
  "{project_id}" "{mr_iid}" \
  "${CODE_REVIEW_WORKSPACE}/review-body.md" \
  "${REVIEW_RUN_ID}" "${REVIEWED_HEAD}"
# 输出: created_note_id=<id> note_api_url=<url>
#   或: existing_note_id=<id> note_api_url=<url>（同一次逻辑 review 的串行重试）
```

脚本使用稳定 `GITLAB_TOKEN` 对 `review_run_id + reviewed_head` 做 HMAC-SHA256，生成不可由普通 MR 评论者预伪造的本次 review 专属隐藏 marker，并另存 exact-head marker；若提供 `CODE_REVIEW_EXPECTED_WRITER_USERNAME`，脚本先在线回读 `/user` 并在身份不匹配时零写入退出；匹配既有 note 时还要求 author 等于当前 token 身份，并分页扫描全部 notes。生产模式只执行脚本内固定的 curl、Node 与系统 CA 路径，并验证 canonical path、owner/mode 和批准 SHA-256；root-owned system runtime 的摘要来自受保护配置，批准的用户态 macOS Node 还必须命中脚本内置 allowlist。任意路径 override 只允许使用非生产 token 的显式单元测试模式。Node/curl 从空环境启动，禁用 curlrc、代理、继承 CA、Node options 与动态 loader 注入；token 仅经 stdin 提供，不进入 child env、argv 或磁盘。新建后脚本再次通过 GitLab API 回读 note ID、两个 marker 与作者；调用方还必须在线确认 MR 当前 head 仍等于 `reviewed_head`。不同逻辑 review 使用不同 ID，因此总是新增评论；同一逻辑 review 的串行网络重试必须使用相同 ID、head、token 和在线写入身份，因此不会重复评论。禁止调用 `upsert_mr_note.sh`，也禁止搜索全局 `<!-- code-review-bot -->` marker 后更新旧评论。

MR 场景的对话精简总结：

```markdown
- **审查状态**: complete / incomplete（含未核验项及单 agent 降级说明）
- **结论**: 通过 / 有条件通过 / 不通过 / 审查未完成
- **评分**: X/10；审查未完成时 N/A
- **红线问题**: 无（若无红线则直接写"无"，不要用编号格式；若有则用编号列出）
  1. [P0] 问题描述
- **关键发现**: 列出 Top 3-5 问题
- **详细 review 已写入 MR notes**: <MR 链接>
```

### 5 段输出结构

MR 评论必须严格遵循以下模板输出（**标题必须用 `### N.` 三级格式**，CI gate 通过正则解析，`##`/`####`/无标题符号均会导致 gate 失败）：

```markdown
### 1. 范围（Scope）
- **变更来源**: MR !XX / 本地 N 个 commit / 本地未提交
- **变更文件数**: N 个
- **涉及模块**: 列出产品模块/服务

### 2. 文档规范
<!-- 审查的最终结论，按规则逐项列出 -->
| 规则 | 状态 | 说明 |
|:-----|:-----|:-----|
| Markdown ≤ 600 行（docs/plans/ 豁免；HTML 不限） | ✅/⚠️/豁免/待核验 | 固定 head、文件名、可信计数；缺失不写通过 |
| domain-model 同步 | ✅/🔴 | 具体缺失项 |
| US 无技术细节 | ✅/🔴 | 具体文件和行号 |
| ADR 推理链完整 | ✅/🔴 | 缺失段落 |
| 跨文档引用正确 | ✅/🔴 | 断链路径 |
| ... | ... | ... |

<details>
<summary>审查过程（按实际执行如实记录）</summary>

如实记录本步**实际怎么审的**：
- 真多角色独立执行 → 写各角色发现 + 影响范围评估（用**加粗**标记角色，**禁止 `###` 标题**）；
- 单 agent 多视角一遍 → 写「单 agent 多视角一遍审查」+ 关键发现，**不编造多角色独立执行或虚构角色名**。

</details>

### 3. 内容质量
<!-- 按变更涉及的文件类型，逐项评价。给出具体建议，引用文件路径和行号 -->
- **架构文档**：设计目标、ADR 质量、数据模型、API 设计、结构性/健壮性/可演进性
- **测试文档**：AC 覆盖、L3 E2E 真实依赖、测试层级划分、边界场景
- **TDD Level（观测项，不参与门禁）**：按 `testing-strategy/references/tdd-level-assessment.md` 输出 level、适用性、摘要、证据与缺口；远程 MR 必须有值
- **L3 gate context**: `non-release` / `release` / `unknown`；**L3 gate status**: `passed` / `L3_GATE_DEFERRED_TO_RELEASE` / `L3_REPORT_VERIFIED` / `L3_EXECUTION_EXPLAINED` / `RELEASE_L3_EVIDENCE_MISSING`；附 dependency 与 evidence
- **L3 affected scope** / **L3 selection reason** / **L3 evidence reuse**: 按 `l3-release-gate.md` 列受影响行为与端、最小充分验证依据及旧报告复用证明；无行为变化一行说明，不要求全端材料
- **可观测性**：埋点覆盖、漏斗完整性、指标可量化、告警阈值
- **Issue ↔ MR 对照**（`[ISSUE-ALIGN]`）：矩阵 `| Issue 要求（来源） | 状态 | 证据 file:line | 结论 |`，规则见 `references/issue-mr-alignment-review.md`。审查日截至 2026-11-22（含）的缺口写入第 5 段建议改进，不得改「是否应通过」；自 2026-11-23 起经 Step 4 `confirmed` 的项写入红线问题。跳过只写一行 `[ISSUE-ALIGN] 跳过：<原因>`。
- **PRD → observability → 埋点**（`[PRD-OBS-TRACK]`）：矩阵 `| PRD 功能 | observability 决策问题/事件 | 代码实现 file:line | 结论 |`，规则见 `references/prd-observability-tracking-review.md`。审查日 2026-10-04 至 2026-11-04（含）的缺口写入第 5 段建议改进，不得改「是否应通过」；自 2026-11-05 起写入红线问题。跳过只写一行 `[PRD-OBS-TRACK] 跳过：<原因>`。
- **功能代码 / 部署配置**：安全边界、输入/注入面、敏感数据、数据一致性、并发、性能容量、资源释放、工程规范、类型安全、模块边界、错误处理、事务边界、降级方案、线上事故风险、与架构文档一致性；`k8s/` 新增文件附 cicd-developer Review findings（带文件路径 + 规则号）
- **代码质量与新增逻辑必要性**（功能代码）：按 `code-structure-design-review.md` 仅在此处汇总一次。无 finding 最多三行；机械改动一行；有 finding 给出比较证据、反例与归因；pending 单列且不计分。
- **app UI 无障碍 + 可测试性**（diff 涉及 app UI 源文件时）：T 轴（硬卡）+ A 轴（试跑）两张子表，按 `references/app-ui-a11y-testability-review.md` 输出格式；T、A 均无问题时仅一行「N 个 UI 文件，T 轴通过 ✅、A 轴无 ⚠️」
- **代码中文字面量拦截**（diff 涉及源码文件时）：先输出 canonical project + `policy=block|warning`，再按 `references/code-chinese-literal-review.md` 输出 C1 子表（阻断范围命中且非豁免高置信 → 🔴；范围外高置信、注释、外部绑定、生成文件、测试意图不明 → ⚠️；多语言文案测试 oracle → 豁免）；无违规命中时仅一行「N 个源码文件，无违规中文字符串字面量 ✅（多语言文案测试豁免 K 处）」

<details>
<summary>审查过程（按实际执行如实记录）</summary>

如实记录实际执行：多角色独立则写各角色发现；单 agent 一遍则写「单 agent 多视角一遍审查」，**不编造多角色独立执行**。禁止 `###` 标题。

</details>

### 4. 端到端一致性
<!-- 追溯矩阵（审查的最终结论） -->
| 变更 | US | 架构设计 | 功能代码 | 测试 | 可观测 | 状态 |
|:-----|:---|:--------|:--------|:-----|:------|:-----|
| 具体变更项 | US-XX-NN | doc §N.N | file.go | test.go | obs.md | ✅/⚠️/🔴 |

`[PRD-OBS-TRACK]` 的可观测列与 `### 3` 使用同一条审查日规则：2026-11-04（含）之前写 ⚠️，2026-11-05 起写 🔴。跳过时在说明里写跳过原因。

**行为覆盖与漏报复核**：只摘要核验深度、实际执行方式、补审/未核验项及必要证据。
详细模式附检查器结果，不在正文重复完整覆盖表/JSON；简要模式不要求 JSON；无行为变化一行依据。
格式与完成条件见 `behavior-coverage-review.md`，不能以“无 finding”代替核验。

**🟣 既有债（不阻断本 MR）**：发现但判定为既有历史债的问题（不计入红线/评分，供后续排期）；无则写“无”。
- 🟣 file:line — 问题描述（既有债，非本次引入）

<details>
<summary>审查过程（按实际执行如实记录）</summary>

如实记录实际执行：多角色独立则写各角色发现；单 agent 一遍则写「单 agent 多视角一遍审查」，**不编造多角色独立执行**。禁止 `###` 标题。

</details>

### 5. 总结
- **审查状态**: complete / incomplete；行为覆盖深度与执行结果引用第 4 段，不重复清单；未核验影响结论时不得报通过，评分 N/A
- **部署验收**（存在部署后验收项时）：按 `l3-release-gate.md` §4 单列 pending/passed/failed、执行前条件及证据；代码审查通过不等于可立即合并或上线已完成
- **概览（一行 tally）**: `结论 X | 红线 N（确定性 a / 研判型 b confirmed / c uncertain待人工）| P1 d | P2·nit e` —— 将待人工项单列，不计入 confirmed（移植自 ultrareview 的 summary tally）
- **L3 gate context**: `non-release` / `release` / `unknown`；**L3 gate status**: `passed` / `L3_GATE_DEFERRED_TO_RELEASE` / `L3_REPORT_VERIFIED` / `L3_EXECUTION_EXPLAINED` / `RELEASE_L3_EVIDENCE_MISSING`；附 dependency 与 evidence
- **L3 affected scope** / **L3 selection reason** / **L3 evidence reuse**: 按 `l3-release-gate.md` 列受影响行为与端、最小充分验证依据及旧报告复用证明；无行为变化一行说明，不要求全端材料
- **TDD Level（观测项，不参与门禁）**: `T0-T4 / N/A / UNVERIFIED`；摘要证据，不得据此改变结论、评分或是否应通过
- **结论（人读）**: 通过 / 有条件通过 / 不通过 / 不通过（待人工 override）/ 审查未完成。待人工 override 仅是人读状态；结构化结果仍提交 `conclusion="不通过"`、`should_pass=false`
- **评分**: X/10（通过: 8.0-10.0；有条件通过: 6.0-7.9；不通过: 0.0-5.9）；审查未完成时 N/A
- **是否应通过**: 是/否，附理由；审查未完成且无已确认阻断时暂不作通过判断
- **红线问题**: 无（若无红线则直接写"无"，不要用编号格式；若有则用编号 + 方括号优先级列出，禁止用 `**P0**` 加粗格式）。仅列已确认阻断发现与符合既有 P0 条件的 uncertain 研判型候选；后者须明确标记、不得计为 confirmed。普通取证/工具缺口不列为红线。每条标类型 + 验证状态：`[确定性]` 命中即阻断 / `[研判型·✅confirmed]` 已复现验证 / `[研判型·⚠️uncertain/待人工]` 保持阻断并等待人工 override
  1. [P0][确定性] 问题描述
  2. [P0][研判型·✅confirmed] 问题描述
  3. [P0][研判型·⚠️uncertain/待人工] 按既有 P0 条件需人工复核的证据型候选
- **复现验证摘要（Step 4）**: confirmed N 条 / refuted 排除 N 条 / uncertain 待人工 N 条；待人工候选不得计入 confirmed tally；refuted 的发现附推翻证据（供人工抽查验证 agent 是否误判）；本地全量 / CI 仅红线（P1·P2 由本地 push-gate 覆盖）
- **建议改进**: 无（若无建议则直接写"无"，不要用编号格式；若有则用编号 + 方括号优先级列出，禁止用 `**P1**` 加粗格式）
  1. [P1] 改进描述
  2. [P2] 改进描述
- **执行方式**: 如实填本次实际怎么审的（如「单 agent 多视角一遍」或「N 个角色独立并行」）+ Step 4 复现验证 N 条。**单 agent 一遍审时禁止编造多角色独立执行或虚构角色名。**
```

### ❌ Bad — 代码先落地，文档承诺后补

handleDeleteEvent() 已合入当前 MR，作者说明“下一 MR 再补文档” → 🔴 documentation-code drift；当前 MR 不得通过。

### ❌ Bad — 代码已实现但文档仍标为目标态

文档仍写 `planned`，但代码已经提供该 API → 🔴 文档过时；必须在当前 MR 把文档更新为当前实现并补齐证据。

---

## 示例

### ❌ Bad — 只查格式不查内容

```
overview.md ≤ 600 行 ✅
domain-model 有更新 ✅
→ "通过"
```

问题：格式合规但没读内容。overview.md 里写了 3 个新表，代码一个没实现也没标注"待实现"，读者以为都已落地。

### ❌ Bad — 编造未发生的执行过程

```
（实际单 agent 一遍审，却在评论里写）
架构审查员（独立 sub-agent）✅ / 安全审查员（独立 sub-agent）✅ / 交叉验证 ✅
```

问题：实际是单 agent 一遍审，却编造了「多个独立 sub-agent 并行 + 交叉验证」。应如实写「单 agent 多视角一遍审查」+ 真实发现。只有引擎真 spawn 了独立 sub-agent，才写多角色独立记录。

### ✅ Good — 三维审查，读懂内容再评判（最终输出）

```
1. 文档规范：overview.md 636 行 → ⚠️ 超 600 行，建议拆分（不计入红线）
2. 内容质量：species_content 用 300KB JSON blob 存储，ADR 未记录 trade-off → ⚠️ 补 ADR
3. 端到端一致性：
   - species_content 文档明确标注 `target state`，代码尚未实现 → ✅ 目标态，不计入当前实现范围
   - handleSpeciesDetail() 仍 SELECT description 但文档说已删除 → 🔴 已实现部分与文档不一致
→ "有条件通过，7/10"
```

### ✅ Good — 审查过程如实记录（单 agent 一遍 / 多角色独立通用）

```
单 agent 多视角一遍审查（本引擎不支持 spawn 独立 sub-agent，未跑多轮）：
- 架构视角: overview.md:136 — species_content 用 300KB JSON blob，ADR 未记录 trade-off
- 测试视角: test-plan.md — species_content CRUD 缺少 L3 E2E 用例
- 可观测视角: observability.md — species_content 写入成功率无监控指标
→ "有条件通过，7/10"
```

### ❌ Bad — 未标注目标态却把文档领先代码当成已实现

```
species_content 表：文档写成“已支持”，但代码没有实现 → 🔴 documentation-code drift
```

问题：如果确实是方案先行，必须明确标注 `planned` / `target state`；未标注时读者会把它误认为当前能力。

### ✅ Good — 区分目标态和 undocumented code

```
species_content 表：文档明确标注 `planned`，代码没有 → ✅ 目标态，不宣称已实现
handleDeleteEvent()：代码有软删除逻辑，文档未提及 → 🔴 undocumented implementation
```

---

## 与其他 review skill 的关系

| Skill | 职责 | 何时用 |
|:------|:-----|:-------|
| **code-review**（本 skill） | 文档规范 + 实现风险（安全/性能/并发/数据正确性/线上事故风险）+ 代码质量/工程规范 + 内容质量 + 端到端一致性（多角色并行审查） | 日常 MR/commit review |
| **security-compliance-review** | 深入安全合规：密钥、PII、访问控制、红线、暴露面分类 | MR 涉及安全敏感变更、用户要求重点安全、或 code-review 发现安全 P0/P1 需要专项展开时 |
| **codebase-vulnerability-analysis** | 源码漏洞机理、攻击类、真实信任边界与全仓覆盖协议；支持 MR 内的 `focused_review` | 安全敏感 diff 由 code-review 条件触发 focused 模式；只有用户明确要求完整代码库漏洞审计时使用 `full_audit` |
| **cicd-developer**（Review 模式） | A4x K8s/ArgoCD/Crossplane 部署规约专项：镜像路径三方一致、Image Updater 自助契约、ExternalSecret/Vault 路径、`kind: Rollout`、Crossplane providerConfig/identifier/external-name、双向仓库边界、Kyverno baseline + validator | 业务仓 `k8s/` **新增/重命名移入**，或 `DEV/k8s`·`argocd-apps`·`crossplane-infra` **新增/修改/重命名**部署文件时，由 code-review 自动 `/cicd-developer` 触发（见 Step 2「部署配置」节）|
| **firmware-security-compliance** | 产品固件安全实现及缺失项审查，技术与流程基线分列 | 已确认固件产品的代码/依赖/构建/版本/发布提交即触发，包括只改业务逻辑；固件文件、设备安全线索或用户明确要求也触发（见 Step 2「设备/固件安全专项升级触发条件」节）|
| **gitlab-issue-sop** | 把 review 发现的问题批量建 issue + 修复后自动验证 | review 输出多个问题、需要 tracking 闭环时 |
| **/architect** | 定义标准（文档体系 + 架构质量原则） | 设计阶段，非 review |

