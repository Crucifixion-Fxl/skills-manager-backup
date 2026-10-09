# GitLab Label Governance — executable SSOT

This file is the sole executable source of truth for GitLab work-item label names, meanings,
cardinality, placement, mutation, and migration in `gitlab-issue-sop`. Other Skills must route label
decisions here instead of copying the taxonomy. The historical
`docs/collaboration/standards/gitlab-label-governance.md` path is a compatibility pointer only.

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
| `type::research` | Bounded feasibility, option comparison, risk validation, or pre-requirement investigation whose deliverable is evidence and a decision | Accepted capability delivery, confirmed bug fixes, routine technical upkeep |
| `type::bug` | Confirmed behavior differs from expectation | Uncommitted new requirements |
| `type::maintenance` | Refactor, dependency, technical debt, docs, engineering governance | User-facing capability |
| `type::operation` | An operational, data, platform, or deployment execution that needs an audit trail | Long-lived capability development |

`type::research` remains open while evidence and a decision are pending. Its exit criteria name the
decision owner and evidence threshold, not code merge or deployment. Record the conclusion in a
comment and link a separate `type::feature` Issue if a capability is accepted. A research label never
implies an accepted Requirement or activates a Requirements Agent route.

### Project domain module: `module::*` and `submodule::*` (project-scoped)

Use these stable domain labels for both research and development Issues in the project. Each
family is mutually exclusive: exactly one first-level module and one second-level module per
classified Issue. Both are project-local only after the Project-label exception gate below
is met. Do not use general `area/*` or `app/*` multi-select
labels as mutually exclusive board columns; cross-module dependencies belong in linked Issues or
comments. Issue type and status describe the work phase; they do not change the module.

Approved `applications/sq` values for research and development:

| Board label (first level) | Second-level labels | Scope |
|---|---|---|
| `module::ai` | `submodule::compute-ai` | AI algorithms and their SoC/DDR compute budget |
| `module::vision` | `submodule::camera-imaging` | Camera modules, optics, imaging, panorama and calibration |
| `module::localization` | `submodule::indoor-aoa`, `submodule::outdoor-gnss`, `submodule::wearable` | Indoor/outdoor location and positioning accessories |
| `module::device-system` | `submodule::gimbal`, `submodule::power`, `submodule::connectivity` | Motion system, endurance, and links/accessories |

Only first-level labels become Board lists. Second-level labels are filters and must map to the
first-level value in the same row; never give one Issue a mismatched pair. The status and priority
families remain independent. A module change updates both levels together and preserves an
explanation in the Issue history.

These values are not global module names. Other projects must define their own stable set here under
their owning namespace before creating labels; do not automatically copy the SQ001 labels.

### Capability SIG and Harness topic: `sig::*` and `topic/*`

These project-local labels are enabled for the two company Harness repositories after the
Maintainer-approved SIG migration (Issue #227). A SIG owns a capability domain whose objective is
to complete and maintain Agent Harness for Business Loops. It is not a workflow state, business
line, implementation repository, or a duplicate of a native assignee.

- `sig::*` is mutually exclusive: exactly one responsible SIG per classified Harness Issue.
- `topic/*` is multi-select: stable capability themes within a SIG; cross-theme Issues remain one
  Issue. Direction boards may display that Issue in multiple topic columns; this does not
  change workflow status or create an organization subgroup. Counts deduplicate by project ID and Issue IID.
- Owner: the Harness repository Maintainer. Consumer: one SIG Board per capability domain, plus
  direction columns, topic filtering and migration reconciliation. Retire only when no open Issue or Board/filter
  references the label and the replacement mapping is preserved.
- Scope: project labels are justified because these are Harness-specific capability domains and
  themes; they do not classify every implementation repository in the parent Group. Existing
  type/status/priority governance and Group placement continue independently.

Approved SIG set:

| Harness repository | Label | Display name / source alias |
|---|---|---|
| `engineering/skills` (`addx`) | `sig::business-capability` | Business Capability / 业务能力 |
| `engineering/skills` (`addx`) | `sig::devops` | DevOps / 研发流程与平台 |
| `engineering/skills` (`addx`) | `sig::tdd-quality` | TDD & Quality / 测试驱动与质量 |
| `engineering/skills` (`addx`) | `sig::hardware-ai` | 硬件研发 AI 化 / 硬件开发 AI 化 |
| `marketing/marketing_automation` (`addx-marketing`) | `sig::marketing-ai` | 市场运营 AI 化 / 市场营销 AI 化 |

Approved multi-select topics for these Harness projects:
`topic/development-workflow` (研发流程), `topic/hardware-assets` (硬件研发资产), `topic/hardware-in-loop` (硬件在环),
`topic/test-automation` (测试自动化), `topic/quality-gates` (质量门禁), `topic/capability-planning` (能力规划),
`topic/agent-harness` (Agent Harness), `topic/saas-access-security` (SaaS 接入与身份安全),
`topic/gitops` (GitOps),
`topic/data-observability` (数据与观测), `topic/observability-tdd` (可观测性的 TDD 开发),
`topic/content-production` (内容生产), `topic/performance-insights` (效果与洞察),
`topic/page-voc` (页面与 VOC),
`topic/experience-quality` (体验质量).

Topic refinement approved in Issue #251:

- `topic/saas-access-security` combines SaaS access and identity/security: user and
  Agent identity, login, native API/CLI access, least privilege, credentials and access audit.
  Replace `topic/saas-access` and `topic/identity-security` with this single topic, preserving
  other labels and decision history; the old names are retired for new classification.
- `topic/gitops` covers declarative desired state, versioned configuration, automatic pull
  and continuous reconciliation, including Agent-operated review, rollout, drift and recovery
  evidence. A Git repository, CI job or Terraform reference alone does not justify this topic.
  The DevOps Board has this column even when no current Issue is qualified; an empty column
  does not claim an implemented capability. [OpenGitOps principles](https://opengitops.dev/).
- `topic/observability-tdd` replaces `topic/instrumentation-metrics`: observability is
  designed, tested and accepted with the feature, from signals/instrumentation and data
  contracts to metrics, dashboards/alerts and positive/negative evidence. Preserve current
  Issue-specific milestones and scope; renaming does not silently expand individual AC.
- `topic/development-workflow` and `topic/hardware-assets` split the former
  `topic/engineering-assets`: DevOps delivery/development workflow and hardware design
  asset/version/traceability governance are separate consumers. #207/#245 use development
  workflow; #208 uses hardware assets. Preserve unrelated fields and retire the mixed name
  only after live consumers and Board columns are reconciled. New ambiguous Issues require
  content triage, not automatic assignment based on repository or title keywords.
- Maintainer owns these project-local topics; consumers are the DevOps/TDD direction Boards
  and filters. Retire only after replacement mappings and all live consumers are reconciled.
  Existing label definitions above keep the same cardinality, placement and authorization.

Not every project Issue belongs to a SIG. Only classified shared Harness construction,
adoption or ongoing capability maintenance receives `sig::*`; ordinary business/project
execution remains in its owning project and may link a SIG Root. A documentation coordination
Issue is not automatically a SIG construction task. Removing a SIG label preserves the Issue,
its progress and implementation links; no replacement SIG is guessed.

New SIGs or topics go through the new-label gate before creation; arbitrary source-table options
are not automatically approved labels. The concrete Board and migration protocol is in
[sig-workflow.md](sig-workflow.md).

### Priority: `priority::*`

One value after priority triage; before that, leave this family unset and record the priority owner
and review date in a comment. Do not silently default every new research Issue to P2.
`priority::p0` is active major production/business impact;
`priority::p1` is high value or risk for the nearest planning window; `priority::p2` is normal
planned work; `priority::p3` is opportunistic. Priority is not incident severity. Only this family
receives GitLab label priority, ordered P0 through P3.

### SIG planning priority: `planning-priority::*` (Harness project exception)

For the two owning Harness projects, each SIG Issue has one of
`planning-priority::p0`, `planning-priority::p1`, `planning-priority::p2`,
`planning-priority::p3`, or `planning-priority::unconfirmed`. These labels express
SIG backlog ordering: P0 highest, P1 next, P2 normal, P3 opportunistic. They do not
assert active major business impact and never replace confirmed `priority::*`.
The migrated source table's P0/P1/P2 is explicitly shown as planning priority,
not promoted into formal impact priority. Seven missing source values stay unconfirmed;
suggestions in prose do not count as confirmed values. Later owner judgment may refine
ordering with an Issue comment and readback.

Consumer: SIG Board cards and filters; governance owner: owning Harness maintainers;
Issue owner judges ordering, AI executes. Project placement keeps this Harness planning
exception out of unrelated Group projects. Creation and P0-as-highest-planning-priority meaning are explicitly confirmed
by the user in coordination Issue #227. Only canonical `priority::*` gets GitLab label priority;
planning labels do not reorder unrelated labels. Retire only after all consumers move
and original source ordering/decision history remains traceable.

### SIG Size: `size::*` (Harness project exception)

For the two owning Harness projects only, use exactly one of `size::s`, `size::m`,
`size::l`, `size::xl`, `size::xxl`, or `size::unconfirmed` on each SIG Issue. These
project labels expose the source table's overall scope/complexity estimate on Board
cards and filters: S = single point, M = one-team loop, L = multiple modules,
XL = cross-team platform, XXL = multiple regions or sustained long-term construction.
Size is not remaining duration, people count, priority, or a numeric Weight conversion.

Consumer: the five direction-based SIG Boards. Governance owner: owning Harness
maintainers; decision/authorization remains with the Issue owner. The user explicitly
requested visible Size in coordination Issue #227. GitLab 18.0 CE lacks native Weight;
these labels preserve the distinct T-shirt estimate rather than duplicating a native
field. Existing estimates come from the migration snapshot; missing/conflicting values
are unconfirmed until owner judgment. AI records the source, scope, changed estimate,
reason and owner in an Issue comment, replaces only this family, and reads it back.
Retire only after a replacement preserves this meaning and all Board/Issue consumers
are migrated. Future native Weight availability does not authorize automatic conversion.
[GitLab Weight tiers](https://docs.gitlab.com/user/work_items/weight/).

### Workflow: `status::*` (Label compatibility mode only)

Each open work item has exactly one of:

| Label | Meaning |
|---|---|
| `status::triage` | Information, classification, or ownership is unresolved |
| `status::backlog` | Accepted but not ready to start |
| `status::ready` | Scope, acceptance, and dependencies are ready |
| `status::in-progress` | Assignee is executing the work |
| `status::in-review` | Deliverable is under review or verification |
| `status::on-hold` | Work is deliberately paused; record why, the decision owner, and a review date |

The canonical flow is `triage -> backlog -> ready -> in-progress -> in-review -> Closed`. A factual
rollback in workflow may move backward, but its reason must be appended as a progress comment.
`status::on-hold` is a side branch from any open state; when resuming, restore the factual active
status and comment on the reason. There is no `status::done`; completion is the native Closed state.

### Closed research disposition: `resolution::abandoned`

Use this optional label only when the **whole research Issue** is explicitly abandoned. Append who
made the decision, why, the evidence or changed premise, and any replacement Issue; add
`resolution::abandoned`, remove its open `status::*`, then close it with GitLab's native Closed state.
Dropping one hypothesis while research continues is a comment, not this label. An accepted research
conclusion also closes natively but does not get an “accepted/done” label. This label expresses the
reason for ending the work, not a duplicate completion state. In a namespace with a configured native
resolution reason, use that native field instead of the label.

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

For `applications/sq`, the owner is the project Maintainer; consumers include the module Board and
research/development Issues. Review the set when domain boundaries change, and retire a value
only after no open Issue or board list uses it. Create `type::research` at the lowest common
ancestor Group for projects consuming that work type; create SQ `module::*` and
`submodule::*` values only in `applications/sq`. Check inherited and project labels before creation; the GitLab Issue API can
silently create a missing name as a Project label when used via `add_labels`.

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
