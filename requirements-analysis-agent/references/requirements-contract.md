# Requirements Analysis Contract

## Ready Artifact profiles

Ready Artifacts use a source-kind discriminated union. Select exactly one profile from the canonical source; never copy transport-only fields between profiles:

| Source kind | Ready profile | Required source fields |
|---|---|---|
| `GITLAB_ISSUE_SNAPSHOT` | Buzz/GitLab | stable reference, digest, retrieval time, live project/Issue identity and revision |
| `INLINE_TEXT_SNAPSHOT` | Local/manual | stable synthetic reference, digest, retrieval time |
| `DOCUMENT_SNAPSHOT` | Local/manual | stable document reference, digest, retrieval time |

### Buzz/GitLab Ready Artifact

For the Buzz Issue workflow, the writer accepts exactly this envelope and content shape. Do not omit fields or add alternate status fields:

```yaml
schema_version: "1.0"
workflow_id: "<root Issue workflow id>"
node_id: requirements_analysis
artifact_id: "<immutable requirement artifact id>"
artifact_type: REQUIREMENTS
attempt_id: "<immutable requirements attempt id>"
produced_by_role: REQUIREMENTS_AGENT
session_identity: "buzz:<64 lowercase hex Agent public key>"
input_refs: [] # requirements_analysis is the first DAG node
input_fingerprint: "<SHA-256 of compact JSON []>"
created_at: "<RFC3339 timestamp>"
status_or_verdict: READY_FOR_PO_REVIEW
supersedes: null # or the prior immutable artifact_id
evidence_refs:
  - "<immutable source or Agent-proof reference>"
open_questions: []
next_responsible_role: PO
content:
  contract_version: "requirements-analysis/1.1"
  requirement_kind: PRODUCT # PRODUCT | TECHNICAL | BUG_FIX | RESEARCH | OPERATIONS
  source_baseline:
    source_kind: GITLAB_ISSUE_SNAPSHOT
    source_ref: "<exact GitLab Issue URL>"
    source_digest: "<64 lowercase hex canonical Issue snapshot SHA-256>"
    retrieved_at: "<RFC3339 timestamp>"
    project_id: "<numeric GitLab project id>"
    issue_iid: "<numeric project-local Issue iid>"
    issue_updated_at: "<exact live RFC3339 Issue revision>"
  problem_statement: "<谁在什么场景下受影响，以及为什么重要>"
  goal: "<可观测的期望结果>"
  value: "<该结果对用户或业务的价值>"
  stakeholders:
    - "<角色或用户群体>"
  scope:
    - "<包含的行为>"
  non_goals:
    - "<明确排除的行为>"
  acceptance_criteria:
    - id: AC-001
      given: "<前置场景>"
      when: "<执行动作>"
      then: "<可观测结果>"
  constraints:
    - "<业务、产品、法务、运营或技术约束，无则明确写无>"
  risks:
    - "<重要的需求层风险，无则明确写无>"
  success_metrics:
    - "<指标与目标值>"
  assumptions:
    - "<本版本接受的假设，无则明确写无>"
  dependencies:
    - "<外部依赖与负责人，无则明确写无>"
  skills_considered:
    - skill_id: "addx:story-craftsman"
      load_key: "story-craftsman"
      decision: SELECTED
      reason: "必选的 PRD 质量评审"
  company_skill_reviews:
    - skill_id: "requirements-analysis-agent"
      load_key: "requirements-analysis-agent"
      skill_source: "<pinned engineering/skills commit and Skill path>"
      skill_content_sha256: "<pinned primary SKILL.md SHA-256>"
      selection_reason: "必选的主需求契约"
      verdict: PASS
      findings:
        - "F-000 <主契约发现，或明确记录无重要发现>"
      evidence_refs:
        - "<immutable rollout capability-pin reference>"
    - skill_id: "addx:story-craftsman"
      load_key: "story-craftsman"
      skill_source: "<same pinned engineering/skills commit and Skill path>"
      skill_content_sha256: "<pinned story-craftsman SKILL.md SHA-256>"
      selection_reason: "必选的 PRD 质量评审"
      verdict: PASS
      findings:
        - "F-001 <评审发现，或明确记录无重要发现>"
      evidence_refs:
        - "<source section or immutable evidence reference>"
  prd_quality_review:
    verdict: PASS # PASS | CONDITIONAL
    findings:
      - id: PRD-001
        severity: NOTE # BLOCKER | MAJOR | MINOR | NOTE
        finding: "<可追溯的 PRD 质量发现>"
        disposition: OBSERVATION # RESOLVED_IN_ARTIFACT | ACCEPTED_CONSTRAINT | OBSERVATION
        resolution: "<该发现如何解决或保留>"
        evidence_refs:
          - "<source section or immutable review evidence>"
  interpretation_confirmations:
    - id: IC-001
      statement: "<请 PO 确认的、有来源依据的理解>"
      source_basis: "<该理解如何由来源推导>"
      impact_if_wrong: "<理解错误时哪些范围、AC、假设、Gate 或指标会变化>"
      responsible_role: 产品负责人（PO）
  prd_review_summary: "<本次修正内容，或来源已就绪的原因>"
  readiness: READY_FOR_PO_REVIEW
artifact_hash: "<SHA-256 of canonical JSON for every field above except artifact_hash>"
```

Because `requirements_analysis` is the first Issue Agent MVP DAG node, `input_refs` must be exactly `[]`; the GitLab source snapshot is provenance in `content.source_baseline` and `evidence_refs`, not a fake upstream Artifact. Compute `input_fingerprint` from `json.dumps([], separators=(",", ":"))`, UTF-8 encoded. Compute `artifact_hash` from the envelope with `artifact_hash` omitted using `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, UTF-8 encoded. No other whitespace, key-order, or Unicode-escaping convention is accepted.

Arrays must be present. Use explicit Chinese values such as `当前未识别到` rather than omitting a reviewed dimension. Every requirement handled by this Agent must include both pinned `requirements-analysis-agent` provenance and a successfully executed `addx:story-craftsman` review in `company_skill_reviews`; both mandatory entries require `PASS`. There is no implicit pure-bug or pure-refactor exemption inside this Agent. Inputs classified as problem or maintenance are routed before this contract is invoked. `skill_id` is the audited catalog ID; `load_key` must equal the selected Skill's current `SKILL.md name:` value. Any other executed review may be `PASS` or `CONDITIONAL`; `BLOCKED` prevents readiness. The Buzz writer pins `session_identity` and both mandatory Skill source/hash pairs to the active capability readback.

所有面向人的业务字段必须包含中文，例如问题、目标、价值、范围、非目标、验收标准、
约束、风险、指标、假设、依赖、Skill 选择原因与 findings、PRD 评审文本、待确认
理解和负责角色。中文句子可保留英文产品名和技术缩写。ID、枚举、哈希、URL 和 evidence reference
是精确机器字段，不做语言转换。Buzz writer 对每个面向人的字段
执行确定性中文门禁；只有英文的 ready Artifact 必须 fail closed。

`prd_quality_review.findings` must be non-empty, even for a clean PRD; use a
traceable `NOTE` rather than a generic success claim. IDs are unique and stable
within the Artifact. `BLOCKER` and `MAJOR` findings may appear in a ready
Artifact only after they have been resolved into the Artifact; an unresolved `BLOCKER`
or unresolved `MAJOR` must produce `ATTEMPT_NEEDS_INPUT`, not an
Artifact. `MINOR` and `NOTE` findings may be resolved, accepted as an explicit
constraint, or retained as observations. The structured review verdict is
`PASS` or `CONDITIONAL`; a blocked review is never ready.

`interpretation_confirmations` contains only non-blocking, source-backed
normalizations whose rejection would change the requirement baseline. Every
entry records a unique `IC-*` ID, the interpreted statement, its source basis,
the impact if it is wrong, and the responsible role. It may be empty, but it
must be present. Missing business intent, an unknown owner, or an unresolved
material ambiguity is not an interpretation confirmation and must use
`ATTEMPT_NEEDS_INPUT`.

The formal envelope's `open_questions` must remain exactly empty for a ready
Artifact. The next responsible role is `PO`.

### Local/manual Ready Artifact

Use this exact profile for pasted prose and non-GitLab documents. It is not accepted by the Buzz writer and grants no Buzz route, claim, comment, or remote-write authority. `source_ref` must identify the exact immutable input: use `inline:<source_digest>` for pasted text, or a stable document URL/path plus immutable revision when one exists.

```yaml
schema_version: "1.0"
workflow_id: "<stable local/manual workflow id>"
node_id: requirements_analysis
artifact_id: "<immutable requirement artifact id>"
artifact_type: REQUIREMENTS
attempt_id: "<immutable requirements attempt id>"
produced_by_role: REQUIREMENTS_AGENT
session_identity: "manual:<stable session identifier>"
input_refs: []
input_fingerprint: "<SHA-256 of compact JSON []>"
created_at: "<RFC3339 timestamp>"
status_or_verdict: READY_FOR_PO_REVIEW
supersedes: null
evidence_refs:
  - "<immutable source or review evidence reference>"
open_questions: []
next_responsible_role: PO
content:
  contract_version: "requirements-analysis/1.0"
  requirement_kind: PRODUCT # PRODUCT | TECHNICAL | BUG_FIX | RESEARCH | OPERATIONS
  source_baseline:
    source_kind: INLINE_TEXT_SNAPSHOT # or DOCUMENT_SNAPSHOT
    source_ref: "<stable source reference>"
    source_digest: "<64 lowercase hex canonical source SHA-256>"
    retrieved_at: "<RFC3339 timestamp>"
  problem_statement: "<who is affected, in what context, and why it matters>"
  goal: "<observable desired outcome>"
  value: "<why this outcome matters>"
  stakeholders: ["<role or user group>"]
  scope: ["<included behavior>"]
  non_goals: ["<explicitly excluded behavior>"]
  acceptance_criteria:
    - id: AC-001
      given: "<context>"
      when: "<action>"
      then: "<observable result>"
  constraints: ["<constraint or none identified>"]
  risks: ["<risk or none identified>"]
  success_metrics: ["<metric and target>"]
  assumptions: ["<accepted assumption or none identified>"]
  dependencies: ["<dependency and owner or none identified>"]
  skills_considered:
    - skill_id: "addx:story-craftsman"
      load_key: story-craftsman
      decision: SELECTED
      reason: mandatory PRD quality review
  company_skill_reviews:
    - skill_id: requirements-analysis-agent
      load_key: requirements-analysis-agent
      skill_source: "<pinned engineering/skills commit and Skill path>"
      skill_content_sha256: "<pinned primary SKILL.md SHA-256>"
      selection_reason: mandatory primary requirements contract
      verdict: PASS
      findings: ["F-000 <finding or no material finding>"]
      evidence_refs: ["<immutable evidence reference>"]
    - skill_id: "addx:story-craftsman"
      load_key: story-craftsman
      skill_source: "<same pinned engineering/skills commit and Skill path>"
      skill_content_sha256: "<pinned story-craftsman SKILL.md SHA-256>"
      selection_reason: mandatory PRD quality review
      verdict: PASS
      findings: ["F-001 <finding or no material finding>"]
      evidence_refs: ["<source section or immutable evidence reference>"]
  prd_review_summary: "<why the source is ready>"
  readiness: READY_FOR_PO_REVIEW
artifact_hash: "<SHA-256 of canonical JSON for every field above except artifact_hash>"
```

The local/manual profile uses the same canonical JSON and full-envelope hash algorithm as the Buzz/GitLab profile. Its `session_identity` records provenance only; it is not a remote identity proof. PO acceptance still binds the exact Artifact ID/hash, but the additional signed Buzz message requirements apply only to the Buzz/GitLab profile.

## Needs-input control outcome

When a valid ready Artifact cannot be formed, return no Artifact. Use:

```yaml
schema_version: "issue-agent-mvp-control/1.0"
outcome_kind: ATTEMPT_NEEDS_INPUT
compatibility: MANUAL_COORDINATOR_EXTENSION_NOT_PROJECTABLE_BY_SYNTHETIC_CORE
workflow_id: "<workflow id>"
node_id: requirements_analysis
attempt_id: "<attempt id>"
session_identity: "<fresh Agent session identity>"
questions:
  - "<one answerable question>"
responsible_role: PO
reason: "<why the missing business fact cannot be inferred>"
```

This is a control outcome, not an accepted Artifact. It never unlocks technical design or test-plan drafting.

## Transport marker mapping

Buzz may use one line before the structured payload to select the parser. The marker is not workflow state and must not be stored or evaluated as an alternative outcome:

| Transport marker | Sole authoritative structured field |
|---|---|
| `REQUIREMENTS_READY_FOR_PO_REVIEW` | Artifact `status_or_verdict: READY_FOR_PO_REVIEW` |
| `REQUIREMENTS_NEEDS_INPUT` | Control result `outcome_kind: ATTEMPT_NEEDS_INPUT` |

After parser selection, consumers must validate only the structured field and reject a marker/payload mismatch. For a ready Artifact, `content.readiness` is a required contract-specific mirror of the authoritative envelope `status_or_verdict`; both must equal `READY_FOR_PO_REVIEW`, and any mismatch is rejected. A marker alone creates neither an Artifact nor a control outcome.

## PO Gate

历史 `requirements-analysis/1.0` Artifact 与英文受控评论仅允许读回与恢复，
不得新增 PO ACCEPT 或 REJECT。由于 v1.0 没有绑定结构化 PRD 评审与
待确认理解，必须创建 superseding `requirements-analysis/1.1` Attempt，并对新
Artifact 的精确 ID/hash 执行 Gate。

PO acceptance must bind:

- Artifact ID and the canonical full-envelope `artifact_hash`;
- source digest;
- accepted assumptions;
- explicit scope and non-goals;
- all acceptance criteria;
- any `CONDITIONAL` Skill finding and its owner.
- the structured PRD quality review and every interpretation confirmation.

In the Buzz Issue workflow, acceptance evidence must also be one exact-readback PO message signed by a separately provisioned PO approver public key whose private key is unavailable to the Requirements Agent and the local desktop OS identity. The local desktop-owner and Requirements Agent keys are explicitly rejected. The Gate records its channel ID, event ID, approver public key, and content hash together with the Artifact ID/hash. A Requirements Agent output, transport acknowledgement, GitLab writer identity, or self-authored message is not PO approval.

A source, AC, accepted-assumption, PRD-finding disposition, or interpretation
confirmation change creates a new Requirements Attempt and invalidates
downstream work derived from the prior hash. Because these fields are inside the
canonical envelope, the exact Artifact hash is the sole approval binding; do
not create a second confirmation state.
