# Deployment tracking contract

Use this contract when a request includes a concrete deployment execution or explicitly asks to
track deployment progress. It separates durable product requirements from the operational record
of one rollout.

## Work-item model

- The Requirement Issue is the source of the requirement, scope, and final acceptance criteria.
  Do not mix a continuous deployment log into it.
- Track one concrete environment plus version or release stage in a separate Deployment Task when
  `gitlab-issue-sop` selects that work-item type and hierarchy. Invoke `gitlab-issue-sop` before
  creating, linking, labeling, transitioning, or commenting on any deployment work item.
- Reuse the same Deployment Task throughout that rollout. Do not create a new Task merely because
  the rollout moves to its next phase. A different environment, version, or release stage gets its
  own Task so evidence remains unambiguous.

`gitlab-issue-sop` is the sole source of truth
for GitLab work-item types, labels, status transitions, parent-child or related-item hierarchy, and
progress-comment format. This contract does not define or copy that taxonomy. If the SOP selects a
different canonical work-item shape, follow it while preserving the evidence requirements below.

## Lifecycle and update cadence

Keep the same Deployment Task as the execution record across preflight, readiness and approval,
execution, GitOps synchronization, runtime verification, failure, and rollback. Promptly append
each key decision, gate, revision, operation, and result using the progress-comment rules from
`gitlab-issue-sop`; use that skill's canonical status transitions instead of inventing deployment
statuses here. A waiting approval is evidence of a gate, not authorization to perform the next
action.

## Minimum evidence

The Deployment Task must contain or link all applicable items below. Use exact identifiers and
timestamps; replace provisional values when the authoritative result becomes available.

- `parent requirement`: Requirement Issue URL and the deployment-specific acceptance criteria.
- `environment / cluster / Argo Application`: the exact target, not an inferred shorthand.
- `exact revision`: commit SHA or immutable artifact digest used by the operation. Record both the
  intended and observed revision when they differ.
- `MR / pipeline`: canonical MR and pipeline URLs, result, and the SHA each result verifies.
- `authorization evidence`: the specific approval for the current gated action, including source,
  time, and scope. A previous action's approval cannot be reused for a later gate.
- `preflight / stop gates`: checks performed, their results, blockers, and explicit stop decisions.
- `sync operation / history`: GitOps operation ID or Argo history entry, timestamps, and outcome.
- `runtime acceptance / L4`: health checks and user-visible acceptance evidence tied to the deployed
  revision and environment.
- `rollback conditions and result`: trigger, approved target revision, operation evidence, runtime
  result, or a clear statement that rollback was not required.
- Never record secret values. Record only secret names or paths when needed for diagnosis, and
  redact tokens, credentials, private keys, and sensitive payloads from commands and evidence.

## Authorization boundary

An existing Requirement Issue plus the user's explicit request to deploy or continue the rollout
and to track it authorizes creating or reusing the in-scope Deployment Task as a normal workflow
step through `gitlab-issue-sop`. If either condition is absent, do not create an external work item:
return an Ops Todo or ask for authorization.

- **MR merge, production sync, and rollback remain separately authorized.**
- **Task updates do not authorize a sync.**

Creating, editing, or closing the Deployment Task is evidence bookkeeping only. It does not satisfy
or imply any required approval for an MR merge, a production synchronization, a live mutation, or a
rollback. Follow the selected operational skill's approval and stop gates for each action.

## Closeout and learning triage

After success, failure, or rollback, complete the Deployment Task with the full evidence trail and
write only this back to the Requirement Issue:

- **brief outcome and Deployment Task link**

Do not copy the chronological execution log into the Requirement Issue. At closeout, perform a
reusable-learning triage:

- A cross-project, durable rule belongs in an engineering/skills MR.
- A project-specific operating fact belongs in that project's workspace skill or runbook.
- One-off status, IDs, timestamps, and execution evidence remain only in the Deployment Task.
- If there is no new reusable rule, do not manufacture a Skill change for that deployment.

## Operational routing

`cicd-developer` owns this tracking and evidence-continuity contract. Routine Argo CD sync,
rollback, and Kubernetes live operations still route to `argocd` / `k8s-ops`; follow those skills'
separate operational checks and authorization gates.
