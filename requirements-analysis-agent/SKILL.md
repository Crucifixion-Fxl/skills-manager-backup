---
name: requirements-analysis-agent
description: Mandatory first-pass requirements analysis for any incoming product or software idea, PRD, feature request, change request, bug-derived requirement, or delivery Issue before architecture, test design, or coding. Use this skill to review source requirements with relevant approved company Skills and return either one typed REQUIREMENTS Artifact ready for the PO Gate or an ATTEMPT_NEEDS_INPUT control outcome. Also use it when a user asks to review, refine, clarify, or assess the readiness of a PRD.
---

# Requirements Analysis Agent

## Description

Turn an arbitrary requirement into an auditable, immutable input for downstream design. Do not design or code while the requirement is still ambiguous.

## Rules

- Every requirement starts with a canonical source and digest.
- Every requirement handled by this Agent runs the approved company Skill `addx:story-craftsman` using its declared load key `story-craftsman`; other approved company Skills are selected by declared triggers.
- Every executed Skill leaves its current content hash, findings, and evidence.
- Unresolved intent returns `ATTEMPT_NEEDS_INPUT`; only a complete immutable Artifact reaches the PO Gate.
- PO acceptance binds the exact Artifact hash. No Agent may accept its own Requirements output.

## Read the contract

Before analysis, read both references completely:

- [references/requirements-contract.md](references/requirements-contract.md)
- [references/skill-routing.md](references/skill-routing.md)

If this skill is running as the first node of Issue Agent MVP, also follow `issue-agent-mvp` and preserve its envelope, Attempt, Gate, and evidence rules. This skill owns the Requirements node's content quality; the workflow skill owns orchestration.

## Treat every requirement as intake

Trigger on raw ideas, pasted prose, PRD files or URLs, GitLab Issue snapshots, enhancement requests, and bug reports that imply a behavior change. The current working directory is not requirement identity or project identity.

Establish one canonical source before analysis:

- record source kind, stable reference, retrieval timestamp, and SHA-256 digest;
- distinguish user-supplied text from retrieved documents and from Agent inference;
- keep source text read-only throughout the Requirements Attempt; an explicitly requested rewrite is a separate post-acceptance action and grants no write authority inside this Attempt;
- if a URL or Issue cannot be read, request an immutable snapshot instead of guessing.

## Run one bounded Requirements Attempt

1. Classify the request as `PRODUCT`, `TECHNICAL`, `BUG_FIX`, `RESEARCH`, or `OPERATIONS`.
2. Extract the affected users, current pain, desired outcome, scope, non-goals, constraints, dependencies, risks, and measurable success conditions.
3. Load and apply `story-craftsman` (canonical company ID `addx:story-craftsman`) for every requirement handled by this Agent. Every selected Skill runs with `review_only=true`; an explicit document-write request does not change this Attempt boundary or substitute for PO acceptance.
4. Evaluate every route in `references/skill-routing.md`. Load only the applicable approved company Skills. Run each as a separate review pass inside the same Requirements Session; do not hide one Skill's findings from the others.
5. Record the selected and skipped Skills with reasons. For every executed Skill, record its source, content SHA-256, verdict, findings, and evidence references.
6. Reconcile the review passes into one structured PRD quality review. Classify each finding as `BLOCKER`, `MAJOR`, `MINOR`, or `NOTE`, and record how it was resolved or retained as a constraint or observation.
7. Record every non-blocking, source-backed Agent interpretation whose rejection would change scope, ACs, assumptions, rollout gates, or success criteria as an `interpretation confirmation`. Include the source basis, impact if wrong, and responsible human role. Do not use this list to hide missing intent.
8. Reconcile conflicting findings. A blocking company rule or unresolved required fact wins over a more permissive review.
9. Produce exactly one outcome: `ATTEMPT_NEEDS_INPUT` or `REQUIREMENTS_READY_FOR_PO_REVIEW`.

Do not spawn coding, design, test, deployment, or observation work from this skill. Those start only after the PO accepts the exact Requirements Artifact hash.

## Fail closed on missing intent

Return `ATTEMPT_NEEDS_INPUT` when any of these cannot be established without invention:

- who has the problem and in what context;
- desired outcome or measurable success;
- in-scope versus explicitly out-of-scope behavior;
- at least one testable acceptance criterion;
- a mandatory company-Skill review;
- a blocking security/compliance classification or external dependency owner.

An unresolved `BLOCKER` or unresolved `MAJOR` PRD finding always returns
`ATTEMPT_NEEDS_INPUT`. It must not be downgraded to an assumption,
`CONDITIONAL` review, or interpretation confirmation merely to produce a ready
Artifact.

Ask one consolidated set of questions, grouped by decision. Include why each answer is needed and the responsible human role. Do not submit a partial Requirements Artifact and do not advance the workflow.

## Produce the typed outcome

For a ready requirement, select the exact source-kind profile in `references/requirements-contract.md`: Buzz-managed GitLab Issues use the Buzz/GitLab profile, while pasted text and documents use the local/manual profile. Never mix profiles and never fabricate GitLab or Buzz identity fields to make a non-GitLab source fit the Buzz writer schema. Every acceptance criterion must have a stable ID and observable Given/When/Then behavior. Keep implementation choices out of acceptance criteria unless the requirement is explicitly technical.

The ready Artifact must use `requirements-analysis/1.1`. Its structured PRD
quality review and interpretation confirmations are part of the canonical
Artifact hash. The PO's exact-hash decision therefore accepts or rejects those
interpretations together with scope, ACs, assumptions, and conditional review
findings. If there are no non-blocking interpretations to confirm, preserve an
empty `interpretation_confirmations` array and render it explicitly as none;
never omit the reviewed dimension.

所有面向人的业务字段必须包含中文，可在中文句子中保留产品名、技术缩写和
英文专有名词。机器绑定的 ID、枚举、哈希、URL 和 evidence reference
不做语言转换，以保持 Artifact 与 Gate 的精确字节绑定。任一面向人的字段
只有英文时，不得产出 ready Artifact。

历史 `requirements-analysis/1.0` Artifact 及其评论仅允许读回与恢复，
不得新增 PO ACCEPT 或 REJECT。它未绑定结构化 PRD 评审和待确认理解，
因此必须创建 superseding `requirements-analysis/1.1` Attempt 后才能进入 PO Gate。

Emit this marker immediately before the final Artifact block when running in Buzz:

```text
REQUIREMENTS_READY_FOR_PO_REVIEW
```

For missing input, emit this marker instead:

```text
REQUIREMENTS_NEEDS_INPUT
```

The Buzz marker is a transport hint only. It must never be parsed as the workflow outcome or persisted as a second status. The sole authoritative fields are the envelope `status_or_verdict` for a ready Artifact and `outcome_kind` for a needs-input control result, as mapped in `references/requirements-contract.md`; ready also requires the contract-specific `content.readiness` mirror to match. Only the PO Gate on the exact Artifact hash can unlock downstream nodes.

## Review an existing PRD

When the user provides a PRD, preserve the source document and report findings before proposing edits:

- `BLOCKER`: intent, scope, compliance, ownership, or AC gap that prevents a safe Artifact;
- `MAJOR`: material ambiguity or missing scenario that should be fixed before PO approval;
- `MINOR`: clarity or structure improvement that does not change intent;
- `NOTE`: traceable observation with no required change.

Before declaring the PRD ready, separate two kinds of uncertainty:

- blocking clarification: the answer is required to establish intent and must
  produce `ATTEMPT_NEEDS_INPUT`;
- interpretation confirmation: the source supports a reasonable normalization,
  but rejecting it would change the baseline, so the PO must see and accept it
  through the exact Artifact hash.

If edits are requested during the Requirements Attempt, return only proposed edits linked to the prior source digest. Writing a new version is a separate action that may run only after an independent PO accepts the exact Artifact ID/hash; never silently overwrite the accepted source.

## Boundaries

- Company Skill availability is runtime-dependent. Missing mandatory Skills are `BLOCKED_SKILL_UNAVAILABLE`, not a reason to simulate their review.
- Skill frontmatter or path alone is not execution evidence; record the content hash and concrete findings.
- Do not treat Buzz message delivery, Agent output, or a workflow approval notification as a GitLab Issue write or PO acceptance.
- Do not expose credentials, private keys, tokens, customer PII, or signed URLs in the Artifact.
- Do not turn assumptions into facts. Label assumptions and require PO acceptance or clarification.

## Examples

### ❌ Bad Example

```text
需求：做一个 VIP 功能
Agent：目标是提升收入，范围是 App 和云服务，AC 是“功能可用”，可以开始编码。
```

This invents the affected user, business goal, repository scope, and acceptance criteria. It also records no company-Skill review.

### ✅ Good Example

```text
REQUIREMENTS_NEEDS_INPUT
schema_version: issue-agent-mvp-control/1.0
outcome_kind: ATTEMPT_NEEDS_INPUT
compatibility: MANUAL_COORDINATOR_EXTENSION_NOT_PROJECTABLE_BY_SYNTHETIC_CORE
workflow_id: CLOUD/example#52
node_id: requirements_analysis
attempt_id: CLOUD/example#52-requirements_analysis-a1
session_identity: buzz:<verified-agent-public-key>
questions:
  - 哪一类 VIP 用户在什么场景下遇到了什么问题？
responsible_role: PO
reason: 缺少受影响用户和场景，无法在不猜测的情况下完成必选需求质量审查。
```

This preserves uncertainty in the exact manual control schema, does not pretend that the mandatory review passed, and keeps downstream design closed.
