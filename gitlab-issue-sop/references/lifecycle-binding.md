# Issue Lifecycle Binding Contract

This contract keeps one auditable identity across requirements, design, testing,
implementation, review, merge, release, operations, and closure. Skills should
reference this file instead of copying the full policy.

## When the contract is required

Require lifecycle binding before project-specific work creates or changes a
durable artifact, source file, configuration, branch, MR, deployment, or final
investigation conclusion. Read-only explanation, generic education, and local
experiments that create no project artifact are exempt.

For an active incident, immediate reversible mitigation must not wait for ticket
creation. Bind or create the Incident Issue at the first safe checkpoint and
before any durable remediation, RCA final, release, or closure claim.

## Binding model

- `issue_binding` identifies the Issue (or Task) that the change delivers. One
  Issue is enough; there is no separate Root/Work Item role.
- The Issue may live in any project. A change that spans several repositories
  links every MR to the same Issue and records the per-repository MRs in that
  Issue (checklist or comment).
- Create a separate repository-local Issue or child Task only when that part has
  its own owner, release cadence, environment, approval boundary, or acceptance
  result (see below). It is never required just to satisfy the MR gate.

Each verified binding records:

```yaml
contract_version: issue-lifecycle/v2
gitlab_host: gitlab.addx.ai
project_id: 123
issue_iid: 45
issue_url: https://gitlab.addx.ai/group/project/-/issues/45
state: opened
assignee_ids: [400]
snapshot_digest: sha256:...
verified_at: 2026-09-28T00:00:00Z
```

Resolve from an explicit full URL or exact `project_id + issue_iid`, then read
GitLab live. Branch names, commit messages, free-text `#45`, chat messages, and
copied webhook payloads are hints only. The Issue must exist, be readable by the
actor doing the work, and have an assignee. Starting new work requires an `opened`
Issue; the CI review gate also accepts `closed`, because release-flowback MRs
legitimately reference an Issue that was already delivered and closed.

## MR binding and non-closing association

An MR description declares the Issue with one full URL:

```text
Issue: https://gitlab.addx.ai/group/program/-/issues/12
```

`Work Item:` and `Root Issue:` lines are still accepted as aliases for MRs created
under the earlier two-role format; they are not required.

The line is a candidate identifier, not trusted evidence. The CI adapter
live-reads the Issue with the review token and confirms that GitLab lists the
current MR in the Issue's related MRs (mentioning the URL in the MR description
creates that relation, also across projects). Pick an Issue the MR author and the
CI review token can read: a cross-project Issue in a project the triggering user
cannot access fails the gate. When several candidate lines are present, the first
one that verifies is bound.

Do not require `Closes`, `Fixes`, `Resolves`, or the `closes_issues` endpoint for
this gate. Those keywords can close an Issue at merge, while the Issue must
normally remain open through deployment, runtime verification, business acceptance,
and any observation window. Use a closing keyword only when the Issue's own scope
explicitly ends at merge.

## Issue versus child Task

Do not create one Issue per command or test run. Keep the main lifecycle on one
Issue. Create a child Task only when the work has an independently accountable
owner, repository, release cadence, environment, approval boundary, or acceptance
result. Typical child Tasks are repository implementation, deployment, migration,
security remediation, and incident follow-up.

## Phase receipts

At a lifecycle boundary, append an idempotent comment to the bound Issue
and read it back. Use a stable marker containing the phase, project ID, Issue IID,
and immutable revision (artifact digest, commit SHA, pipeline ID, deployment ID,
or incident revision). Do not edit the original Issue description for progress.

The receipt contains only the evidence needed to resume or audit the workflow:

- lifecycle phase and outcome (`PASS`, `FAIL`, `BLOCKED`, `DEFERRED`, or
  `NOT_APPLICABLE`);
- immutable artifact links and digests;
- exact commit/MR/pipeline/release/deployment identifiers where applicable;
- evidence boundary: what was and was not verified;
- next gate, owner, and unresolved blockers.

Routine logs remain in CI or the owning evidence store. Do not paste full logs,
secrets, personal data, or short-lived local paths into an Issue.

## Required transitions

| Boundary | Required Issue evidence |
|---|---|
| Requirements accepted | Exact Requirements artifact ID/hash and Gate decision |
| Design accepted | Fixed design/ADR links, digests, decision state, reviewer |
| Test plan accepted | Plan version/digest, AC traceability result, final plan gate |
| Development started | Verified Issue binding, Issue-created branch, assignee |
| Review submitted | MR URL, exact head SHA, verified Issue association, review result |
| Merged | MR URL, exact merge SHA, target branch, terminal pipeline result |
| Deployment or rollback | Deployment Task, artifact digest, environment, approval, result |
| Post-deploy acceptance | Runtime/business observations, observation window, limitations |
| Incident/RCA | Incident revision, impact, hypotheses/evidence, mitigation, recovery |
| Final closure | All required child Tasks terminal; release and acceptance evidence read back |

## Status and closure

- Move to `status::in-progress` only after the bound Issue is
  assigned and implementation has started from its Issue-created branch.
- Move to `status::in-review` only after the MR and review evidence are linked.
- Merge, a green pipeline, HTTP 200, or a successful sync is not closure.
- Keep the Issue open while external release, production verification,
  business acceptance, observation windows, rollback follow-up, or required child
  Tasks remain incomplete.
- Close only after a persisted, read-back final receipt proves the required
  production/runtime and business acceptance outcome. Later work starts a successor
  Issue or Task; never rewrite archived evidence.

Any write failure or inconsistent readback is a hard stop for the next lifecycle
transition. Preserve the true state and report the exact blocker.
