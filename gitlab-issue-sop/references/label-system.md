# GitLab Label Governance — executable SSOT

This file is the sole executable source of truth for GitLab work-item label names, meanings,
cardinality, placement, mutation, and migration in `gitlab-issue-sop`. Other Skills must route label
decisions here instead of copying the taxonomy. The historical
`docs/standards/gitlab-label-governance.md` path is a compatibility pointer only.

## Principles and field boundaries

1. **Minimum sufficient**: create a label only for a stable query, board, notification, or
   automation consumer.
2. **One semantic owner**: do not duplicate a native GitLab field with a label.
3. **Lowest common ancestor**: public labels belong to the **最低共同祖先 Group** shared by all
   consumers. Project labels are exceptional and require the gate below.
4. **Governed lifecycle**: every label has a definition, owner, use case, and retirement condition.

Use native GitLab fields for completion (`Open` / `Closed` / `Merged`), assignee/reviewer, Milestone,
due date, Weight, checklist, and Task hierarchy. Do not create labels that restate those fields.

### Native mode versus Label compatibility mode

- **Native mode**: when the namespace has an organization-configured GitLab Work Item Type and
  GitLab 原生 Work Item Status, use those fields and omit the equivalent `type::*` and `status::*`
  labels.
- **Label 兼容模式**: for GitLab Free / CE, or when native types/statuses are not configured for the
  namespace, use the canonical `type::*` and `status::*` families below.
- Do not mix modes inside one namespace. A migration between modes is a separately reviewed,
  one-time operation.

## Naming and cardinality

- Names use lowercase English `kebab-case` with a semantic prefix.
- `<scope>::<value>` is mutually exclusive: at most one label in that scope.
- `<family>/<value>` is multi-select.
- No bare names such as `bug`, `p1`, or `backend`.
- Do not put people, dates, versions, project codes, or temporary activity names in labels.

## Canonical families

### Work type: `type::*` (Label compatibility mode only)

One value after triage:

| Label | Use | Excludes |
|---|---|---|
| `type::feature` | New or significantly expanded user/business capability | Fixes and routine maintenance |
| `type::bug` | Confirmed behavior differs from expectation | Uncommitted new requirements |
| `type::maintenance` | Refactor, dependency, technical debt, docs, engineering governance | User-facing capability |
| `type::operation` | An operational, data, platform, or deployment execution that needs an audit trail | Long-lived capability development |

### Priority: `priority::*`

One value after triage. `priority::p0` is active major production/business impact;
`priority::p1` is high value or risk for the nearest planning window; `priority::p2` is normal
planned work; `priority::p3` is opportunistic. Priority is not incident severity. Only this family
receives GitLab label priority, ordered P0 through P3.

### Workflow: `status::*` (Label compatibility mode only)

Each open work item has exactly one of:

| Label | Meaning |
|---|---|
| `status::triage` | Information, classification, or ownership is unresolved |
| `status::backlog` | Accepted but not ready to start |
| `status::ready` | Scope, acceptance, and dependencies are ready |
| `status::in-progress` | Assignee is executing the work |
| `status::in-review` | Deliverable is under review or verification |

The canonical flow is `triage -> backlog -> ready -> in-progress -> in-review -> Closed`. A factual
rollback in workflow may move backward, but its reason must be appended as a progress comment.
There is no `status::done`; completion is the native Closed state.

### Technical area: `area/*`

This optional multi-select family contains the approved public values `area/backend`, `area/data`,
`area/infra`, `area/observability`, and `area/testing`. A one-off topic belongs in Scope, not in a
new label.

### Product: `app/*`

`app/vicohome`, `app/viconature`, `app/kiwibit`, and `app/safemo` are multi-select product facts.
They are filters, not board columns. Do not create `app/common`. When product release schedules
differ, split independently scheduled Tasks and give each its own Milestone.

### Optional business domain: `domain::*`

This mutually exclusive family is not currently enabled. A stable business domain may be added only
at the Business Group shared by at least two consumers and only through the new-label gate. Never use
it for product names or short-lived features.

### Blocking: `flag/blocked`

This optional flag is orthogonal to workflow status. Preserve the actual status while blocked,
append the dependency, responsible party, exit condition, and review time, and remove only the flag
when the dependency clears.

## Deployment Task 分类契约

Use this mapping for a deployment execution Task derived from a Requirement Issue. The Task is the
audit record for one environment plus version or release stage; the Requirement Issue remains the
source of requirement scope and final acceptance criteria.

| Classification | Canonical representation | Forbidden substitute |
|---|---|---|
| Deployment | Native Work Item Type `Task`; in Label compatibility mode use `type::operation`; use `area/infra` when infrastructure/deployment filtering is needed | `deployment::*`, `lifecycle::*`, or a project-local synonym |
| Environment | Exact environment, cluster, and Argo Application fields in the Task description and progress comments | 不得创建 `environment::*`; also do not invent `env::*`, region labels, or one label per cluster |
| Status | GitLab 原生 Work Item Status in Native mode; canonical `status::*` in Label 兼容模式 | Deployment-specific status labels or copied rollout phases |
| Version / release stage | Milestone plus exact revision/digest in deployment evidence | Version, phase, SHA, or date labels |
| Blocked | Current status plus optional `flag/blocked` | `status::blocked` or closing the Task |

The deployment Task may be a child of a feature Requirement Issue when it is an independently
verifiable delivery slice. A standalone operation with no parent product requirement remains an
independent operation work item. Parent-child and related-item API mechanics, assignee, Milestone,
and progress-comment format follow the corresponding sections of `gitlab-issue-sop`.

Do not copy the parent Requirement Issue's `type::feature` onto the deployment Task in Label
compatibility mode: the parent describes the capability, while the Task describes the operational
execution. Product labels may be narrowed to the product actually shipped by that Task.

## Missing-label behavior

Before any label mutation, list project labels including inherited ancestor Group labels and
determine the namespace's Native or Label compatibility mode.

If a required canonical label is missing:

1. Treat it as a **缺失 canonical label**. Do not select a similar legacy label and do not create a
   temporary synonym.
2. Continue an already authorized Task/evidence write only without fabricated labels, and mark label
   classification as incomplete. Do not claim the work item is fully governed.
3. **停止 label 写入** and emit an Ops Todo containing the missing canonical name, target namespace,
   intended query/automation consumer, lowest common ancestor Group, required Group owner, and
   acceptance proof (created once, inherited by the project, and read back).
4. A Group owner may create the canonical label only after that external write is explicitly
   authorized. A Project label may be proposed only if all Project-label exception gates below pass.

禁止临时自造 label to keep a deployment moving. Missing governance metadata never expands
authorization for MR merge, production synchronization, live mutation, or rollback.

## Placement and new-label gate

| Level | Default ownership |
|---|---|
| Organization / Engineering Group | Approved priority, area, app, and blocked families; compatibility mode also holds type/status |
| Business Group | Approved stable `domain::*` values shared by its projects |
| Project | None by default |

A new Project label requires all of the following:

1. The meaning is project-only and would pollute the parent Group.
2. Multiple existing or foreseeable work items reuse it.
3. A concrete query, board, notification, or automation consumes it.
4. Native fields, hierarchy, checklist, Milestone, and canonical labels cannot express it.
5. It has a maintenance owner and retirement condition.

Any canonical family change must update this file first and receive the owning Group's review before
the label is created. Consumers link here; they do not copy definitions.

## Safe mutation contract

In GitLab Free / CE, `::` naming does not enforce server-side exclusivity. For a scoped update:

1. GET the current work-item labels and identify only old values in the same scope.
2. Use `remove_labels` for those old values and `add_labels` for the one canonical new value in the
   same request. Never send the overwrite-style `labels` field with a partial or reconstructed list.
3. Preserve every unrelated scope and every `/` multi-select family.
4. 回读 work item and prove exactly one value remains in the scoped family.

Create operations first query the target level and ancestors. A duplicate-name `409`, ambiguous
same-name label from different levels, write failure, or failed readback is not success.

## Migration and retirement

- Bare `p0/p1/p2` values require re-triage; do not mechanically rename them.
- `status::blocked` becomes the factual workflow status plus `flag/blocked`.
- `feature::*` is not automatically a `domain::*`; decide per item.
- `area::*` migrates to the multi-select `area/*` family.
- `lifecycle::*` steps move to checklists or Tasks, not replacement labels.
- Migrate open work items; preserve closed history unless a separately approved immutable audit
  mapping exists.
- Deprecate before retirement, stop new writers, migrate active consumers, and remove only after
  proving zero live references or preserving an approved historical mapping.

## Related execution documents

- [Status workflow](status-workflow.md)
- [Progress comments](progress-comments.md)
- [Linked items and Task hierarchy](linked-items.md)
- [Batch operations](batch-operations.md)
- [Board setup](board-setup.md)
