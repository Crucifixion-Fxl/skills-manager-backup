---
name: domain-workspace-onboarding
description: Govern the assessment, proposal, initialization, durable-context ingestion, validation, and archival of domain-workspace repositories under Domain Workspaces Standards. Use for requests to create, extend, migrate, validate, merge, deprecate, or archive a stable business-domain, shared-platform-domain, or enablement-domain workspace, especially when repository overlap, workspace ownership, boundary uniqueness, legacy local material, or security and IoT platform boundaries must be assessed before any GitLab project is created.
---

# Domain Workspace Onboarding

## Description

Use standards as the SSOT and keep workspace admission fail-closed. Never treat a request such as “建一个 workspace” as creation approval.

## Start safely

1. Select exactly one mode: `ASSESS`, `PROPOSE`, `INIT`, `INGEST`, `VALIDATE`, or `ARCHIVE`. Default to read-only `ASSESS`.
2. Resolve `SKILL_ROOT` to the directory containing this `SKILL.md`; never resolve helpers relative to the target workspace. Locate the Domain Workspaces Standards checkout and verify its trusted origin, clean tree, equal local/remote revision, pinned baseline, and governance interfaces before using it:

   ```bash
   SKILL_ROOT=/absolute/path/to/domain-workspace-onboarding
   uv run python "$SKILL_ROOT/scripts/verify_standards.py" --standards-root /path/to/standards --remote
   ```

   Require baseline `779ed7dd25627bda043774e0c63f210b7a284d36` or a compatible descendant from `domain-workspaces/standards`. Stop on a dirty tree, untrusted origin, missing baseline, incompatible governance interface, unreadable remote, or divergent revision. Lifecycle gates execute validators from the verified Git object, never from mutable working-tree files.
3. Enter through the standards HTML SOP and link it in human-facing output. Use the pinned schema, template, and validators; do not reconstruct or copy their normative prose into a workspace.
4. Read [references/modes.md](references/modes.md) for the selected mode. Read [references/contracts.md](references/contracts.md) before producing or checking an artifact. For physical/video security plus IoT platform requests, also read [references/security-iot-joint-assessment.md](references/security-iot-joint-assessment.md).

## Rules

- Treat `primary`, `dependency`, and `related` as the only repository roles. Let each workspace manifest v2 declare its own taxonomy.
- Permit the same source repository in multiple workspaces when role, taxonomy, reason, boundary, and central-registry evidence are explicit. Reject duplicate workspace boundaries.
- Require a separate Owner Agent conversation for every workspace being initialized or migrated. A coordinating Agent handles cross-domain comparison and governance only; it does not ingest or author multiple workspaces in one conversation.
- Keep standards, existing workspaces, legacy workspaces, and source repositories read-only while an Owner Agent initializes a target workspace.
- Ingest only durable project-space knowledge. Exclude personal material, Issue/MR worktrees, checkout state, SQL, logs, credentials, secrets, screenshots, exports, and one-off evidence.
- Keep source, merge, CI, Pages/deploy, runtime, and user acceptance as distinct evidence states. A green pipeline, HTTP response, or local preview does not prove the other states.
- Do not message stakeholders from this skill unless the user separately asks for communication.

## Mode gates

| Mode | Mutation | Required result |
|---|---:|---|
| `ASSESS` | none | Read both central catalog and repository registry; return exactly one recommendation: `new-workspace`, `extend-existing`, `source-repository-docs`, or `standards`, with overlap evidence and unresolved facts. |
| `PROPOSE` | proposal artifact only | Start from the standards template, replace every example identity, complete the standards JSON Schema, keep the reader entry HTML-first, and call the snapshot-bound `gate.py propose`. A proposal is not creation approval. |
| `INIT` | target workspace only | Reject the unchanged standards template and placeholders. Require `new-workspace`, auditable HTTPS approval/Owner/current-creation-permission evidence, confirmed governance Owner, unique catalog name/repository/boundary, complete alternatives and normalized-URL reuse proof, and a passing official governance validator before creation. |
| `INGEST` | target workspace only | Classify legacy material before writing; distill durable context and source pointers, never bulk-copy. Require the independent Owner Agent boundary. |
| `VALIDATE` | validation evidence only | Cover catalog, proposal, YAML, HTML links, ADRs, taxonomy/boundary, unmanaged siblings, exact-SHA snapshots/freezes, Git state, an executable representative workflow, and a dated review plan; report the six evidence layers separately. The helper can mark an activation evidence package ready but never authorizes activation. |
| `ARCHIVE` | two-phase evidence only | Preflight the actual `deprecated` workspace, migrated consumers, redirect/source pointer, Owner, reviewed catalog change and reviewed read-only plan; then let the independent Owner authorize and execute externally. Re-run post-transition against the actual `archived` workspace, completed catalog and read-only evidence. Both phases run pinned workspace and governance validators, bind catalog repository origin and exact HEAD; post-transition also binds catalog archive SHA/redirect. Neither phase authorizes archival. |

Use the deterministic helpers after standards verification:

```bash
uv run python "$SKILL_ROOT/scripts/assess.py" --standards-root /path/to/standards --request assessment.yaml
uv run python "$SKILL_ROOT/scripts/gate.py" propose --standards-root /path/to/standards --proposal proposal.yaml
uv run python "$SKILL_ROOT/scripts/gate.py" init --standards-root /path/to/standards --proposal proposal.yaml
uv run python "$SKILL_ROOT/scripts/ingest_filter.py" --inventory ingestion-inventory.yaml --source-root /path/to/legacy-material
uv run python "$SKILL_ROOT/scripts/gate.py" validate --standards-root /path/to/standards --workspace-root /path/to/workspace --evidence validation-evidence.yaml --require-activation
uv run python "$SKILL_ROOT/scripts/gate.py" archive --standards-root /path/to/standards --workspace-root /path/to/workspace --evidence archive-evidence.yaml
```

## Examples

### Bad Example

“用户说建 workspace，所以先创建 `physical-security` 和 `iot-platform` 两个空项目，之后再补 Owner、boundary 和 proposal。”这是违规的：它猜测命名和边界，并绕过联合 ASSESS、审批及创建权限门禁。

### Good Example

“先对 physical/video security 与 shared IoT platform 做一次联合只读 ASSESS，输出两个未命名候选及 catalog/registry overlap；推荐进入 proposal 治理，但保持 `creation_authorized=false`，直到各自 Owner、唯一边界、复用证明和审批完整。”

## Report

Lead with the recommendation or gate decision. Name the standards local/remote SHA, catalog and registry revisions, proposal or target SHA, validator result, and remaining blockers. Label any unverified evidence as `未核验` and any failed prerequisite as `阻塞`. State explicitly whether workspace creation, activation, or archival is authorized; never collapse those decisions into one “done”.
