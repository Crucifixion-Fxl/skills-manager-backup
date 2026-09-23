# Issue Agent：研发质量节点契约

## 适用范围与职责

仅用于已明确委派的 `FINAL_TEST_PLAN` 或 `DEV_TEST_REPORT` 节点；不启动完整研发流程，
不重新做已接受的需求/技术设计。研发负责测试设计和执行，QA 使用独立 Session 审查，
人工 QA Owner 才能决定 Gate。节点包不是新的 Skill，也不定义第二套测试分层方法。

测试设计复用 `testing-strategy` 的分层、AC 追溯、场景和防假绿规则；项目可读文档仍以
`docs/testing/*.html` / `scenarios/*.html` 为设计正本。下面的机器记录是绑定文档 revision、
用例 ID 和代码输入的不可变交接快照，不是另一份可独立修改的方案正本；在 `evidence_refs`
中记录对应文档 revision/hash。两者冲突时停止、修订正本并生成新快照，不手改已接受的快照。

## 单节点执行

1. 校验 dispatch packet 的职责、权限、输入 Artifact/hash、Attempt/Session、仓库集合与精确 SHA。
   缺少无法安全推断的意图返回 `NEEDS_INPUT`；缺失证据或不满足前提返回 `BLOCKED`。
2. `FINAL_TEST_PLAN`：读取已接受 REQUIREMENTS、TECHNICAL_DESIGN 和当前 CODE_CHANGE，
   复用已有测试方案，根据实际 diff 补齐 AC、风险、影响范围与用例追溯，只提交最终方案快照。
3. 方案交给 [独立 QA review 协议](../../code-review/references/issue-agent-qa-review.md)，
   再由 Coordinator 校验人工方案 Gate。普通 test-first、单元测试和探索性自测不等待该 Gate；
   它约束的是受控 RC 验证及后续流转，不能倒置 TDD。
4. `DEV_TEST_REPORT`：要求可信控制端提供当前有效的方案 Gate、最终方案和 exact RC；
   仅在授权的环境、测试数据和 scratch workspace 内执行，记录实际命令、环境及逐用例结果。
   然后提交报告，交独立 QA review 和人工报告 Gate，不由研发自验收。

- P0/P1 用例不得静默跳过；失败、跳过、阻塞、未执行和残余风险逐项披露，不能填成 PASS。
- 代码、RC、测试方案或影响结果的环境变化会使相关证据失效；由 Coordinator 创建新 Attempt，
  重新取得受影响的 Review/Gate。Worker 不改旧 Artifact、不开新流程、不自行推进 Issue。
- 研发自测 PASS 不是 QA ACCEPT，更不是发布许可；这里没有部署、MR 创建/写回或生产授权。

例：SHA A 的 PASS 不可用于 SHA B；先按 B 修订影响范围，重新取得有效方案 Gate 并在 B 的 RC 重测。

## FINAL_TEST_PLAN

以下是 Artifact 的 `artifact_type` 和 `content` 片段，提交前必须补全 dispatch packet 指定的 envelope。
当前执行器要求 `cases` 为非空用例 ID 字符串数组；详细用例放在 `case_details` 中，以 `case_id` 一一关联。

```yaml
artifact_type: FINAL_TEST_PLAN
content:
  covers_code_artifact: "<CODE_CHANGE artifact id>"
  requirement_traceability: []
  cases: [TC-001]
  case_details:
    - case_id: TC-001
      acceptance_criteria: [AC-001]
      priority: P0
      type: functional
      preconditions: []
      test_data: []
      steps: []
      expected_result: ""
      automation: planned
      evidence_required: []
  impact_coverage: ["<实际代码影响区域及对应用例>"]
  environment_matrix: []
  entry_criteria: []
  exit_criteria: []
  rollback_validation: []
  unexecuted_cases: []
  residual_risks: []
```

## DEV_TEST_REPORT（独立不可变报告记录）

```yaml
record_type: DEV_TEST_REPORT
report_id: "<deterministic report id>"
schema_version: "1.0"
workflow_id: "<workflow id>"
report_hash: "<sha256>"
rc_id: "<exact RC id>"
repositories:
  - project_id: "group/project"
    tested_sha: "<40-char SHA>"
content:
  result: BLOCKED # PASS | FAIL | BLOCKED，依据实际执行证据
  executed_cases: []
  failed_cases: []
  executed_commands: []
  environment: {}
  case_results: []
  summary:
    passed: 0
    failed: 0
    skipped: 0
    blocked: 0
    unexecuted: 0
  unexecuted_cases: []
  known_failures: []
evidence_refs: []
created_at: "<RFC3339>"
```

`content.result` 只表示研发自验证结论；QA 必须单独产生 Review 和 Gate Decision。
PASS 要求 executed_cases 非空、failed_cases 为空且声明的阻塞项已解决；详细 case_results 和计数须与之对账。

## 版本与 envelope

本模板对应 Issue Agent MVP `schema_version: "1.0"`。执行器契约源为配套
`zlin/issue-agent-orchestrator` 仓库的 `src/contracts/model.ts`、`src/contracts/validate.ts`
及 `src/quality/qa-review.ts`。使用时须记录实际执行器 revision 并核对版本；配套仓库的发布和 live 接入独立验收。

Artifact envelope 必需字段：schema_version、workflow_id、node_id、artifact_id、artifact_type、attempt_id、
produced_by_role、session_identity、input_refs（每项 artifact_id/artifact_type/artifact_hash）、input_fingerprint、
created_at、status_or_verdict、supersedes、evidence_refs、open_questions、next_responsible_role、content、artifact_hash。
哈希必须按运行时契约计算，不能用占位值提交。

DEV_TEST_REPORT 是独立记录，不是 ArtifactEnvelope；Attempt/生产者 Session 和上游输入来源须由可信 dispatch packet
及其引用的 Artifact 链核验，不给报告臆造顶层字段。缺少绑定证据时返回 BLOCKED。
