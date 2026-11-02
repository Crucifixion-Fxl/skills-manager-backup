# Associated Issue update and closure

When a bound Issue/MR session finishes acceptance or receives an explicit owner result/waiver,
write it back in that session and verify readback without waiting for another request to comment.
Route to [the shared acceptance contract](../../../collaboration/gitlab-issue-sop/references/deployment-acceptance.md):
`acceptance.py report --issue ...` and `pending --issue ...` work independently of release-agent,
a publisher or a deployment batch. Record known facts and missing context separately; the release
consumer later matches records to actual deployments and applies its gates. MR notes only reference
the Issue record. Keep acceptance ownership and remaining work after merge; no closure on CI alone.

This is a mandatory parent-session responsibility at MR submission/update,
actual merge, and session wrap-up. It complements the shared
[Issue lifecycle contract](../../../collaboration/gitlab-issue-sop/references/lifecycle-binding.md);
it does not replace acceptance with a merge result.

## Scope and triggers

Use the verified Issue from Step 1.25. Include other Issues only when their
native MR/Issue association and delivered scope have been verified. A URL in
prose, related example, dependency or historical reference is not a closure
instruction. Deduplicate by GitLab host/project/IID and write one receipt per Issue.

- After creating/updating the MR, append and read back a submitted/revised
  receipt before starting or resuming Driver. Include MR URL, exact HEAD,
  target branch, scope, available review evidence and the next gate. Update
  when HEAD, target, binding or delivered scope changes, not on every poll.
- After live GitLab confirms `state=merged`, append the exact merge receipt
  and decide update/close for each verified Issue. Also do this when the user
  reports a merge or an already-merged MR is encountered during cleanup.
- On session wrap-up, reconcile against the current MR, Issue and receipt
  state. Complete any missing synchronization before local deletion; preserve
  unmerged/failed/cancelled/unknown outcomes without inventing a merge receipt.

## Per-Issue decision

Read the current Issue and its completion conditions before deciding:

| Evidence | Required disposition |
|:--|:--|
| Scope partly delivered, blockers or required acceptance remain | Keep open; append progress, verified boundary, remaining conditions, dependency, next action and responsible owner |
| All conditions for this Issue are proved by persisted, read-back evidence | Append final receipt, read it back, close and read back `state=closed` |
| Issue still has required release/runtime/business/observation/child work or MRs in other repositories | Keep open, even if this MR is complete |
| Issue already closed | Read the closure evidence; do not reopen or repeat a close automatically. If current evidence contradicts closure, report the discrepancy and stop the transition |

A green pipeline, HTTP 200, Collector acknowledgement, package publication or
MR merge proves only its own stage. Close the Issue at merge only when its
documented scope actually ends there and no other repository MR is pending.
Do not introduce closing
keywords to bypass this decision or close every Issue mentioned by the MR.

## Receipt and readback

Use an append-only comment, not edits to the original requirements description.
At merge it includes MR URL, source HEAD, exact merge SHA, target branch,
terminal pipeline/job results (including allowed failures), delivered scope,
acceptance/release links where applicable, limitations and disposition rationale.
For an open Issue also state the remaining conditions and next owner/action.
Do not paste credentials, full logs or transient local paths.

Use a stable marker, for example:

```text
<!-- gitlab-mr-issue-sync:v1:<phase>:<project-id>:<issue-iid>:<mr-iid>:<revision> -->
```

`phase` distinguishes submitted, revised, merged and final; `revision` is the
immutable HEAD/merge SHA or acceptance artifact digest. Before POST, paginate
Notes to reconcile an existing marker; this cross-note duplicate check is the
caller's job (the verifier only counts the marker within the selected note and
does not promise cross-client exactly-once delivery). Verify its author and evidence content;
a matching marker alone is insufficient. After writing, GET the exact note
and verify Issue identity, author, marker and body. After a close request,
GET the Issue and verify its final state. On a timeout or unknown response,
read back first; retry only a missing operation, without duplicate comments.

Drive-state `history` keeps each phase through its own locked writer; never hand-edit
the file. Submitted/revised receipts are `kind=issue_sync`, produced only by
`verify_issue_sync_receipt.py --state` (below). Merged/final receipts are the separate
non-review kind `issue_sync_postmerge`, written after the parent's own native readback
(the exact note, then the Issue state after any close) with:

```bash
uv run <skill-path>/scripts/drive_state.py postmerge --state "$STATE_FILE" \
  --mr-iid "$MR_IID" --project-path "$PROJECT_PATH" --json '{"phase": "merged",
  "issue_url": "<canonical Issue URL>", "revision": "<exact merge SHA>", "note_id": <id>,
  "note_url": "<Issue URL>#note_<id>", "disposition": "kept-open", "observed_state": "opened",
  "next_action": "<remaining condition and owner>"}'
```

`phase` is `merged` (revision = exact merge SHA) or `final` (merge SHA or
`sha256:<acceptance artifact digest>`); `disposition` is `kept-open` (observed
`opened`), `closed` or `already-closed` (observed `closed`); `note_url` and
`next_action` are optional. Any other field (`result`, `binding_role`, `schema`, ...),
a `submitted`/`revised` phase or kind `issue_sync`/`issue_binding` is exit 2 and
nothing is written; `drive_state.py event` rejects all three Issue kinds. The writer
adds MR IID/project, `source=parent-readback`, `native_verified_by_writer=false` and
the recorded UTC. It does not read GitLab: the record is the parent's readback, not
proof of the merge, the closure or business acceptance, and it never satisfies the
submitted/revised audit. Receipts bind the current revision; an old HEAD receipt
does not cover a later push. For a standalone cleanup with no drive state, preserve
equivalent receipts in the session evidence record.

## Binding role, active set and retirement

Each bound Issue gets one `kind=issue_binding` admission record in drive-state
history, produced only by `record_issue_binding.py` from a live GET set (Issue,
MR, related MRs). It reuses the lifecycle-binding identity and SOP preflight
predicates and adds the declared role:

- `new-work`: this MR delivers the Issue; live `opened`, assigned, MR related.
- `closed-reference`: a release-flowback reference to already delivered work;
  live `opened` or `closed`, assigned, MR related. Only this role can reach
  `NOT_APPLICABLE_CLOSED`. The role is never inferred from `state=closed`.

The first role recorded for an Issue in a state is fixed. Admitting
`closed-reference` after any new-work evidence (a `new-work` admission, or a
receipt that observed the Issue `opened` without an admission) is
`binding_role_conflict`; a real role change needs a new MR/state, not a re-tag.
Evidence is read in history order, never from a receipt's own role fields: a
receipt may carry an admission role only when that same admission is in force at
its position. An admission claim without one, or a default/other-role receipt
after a `closed-reference` admission, is `binding_role_conflict`; so is a terminal
receipt that contradicts itself (`VERIFIED` without `observed_state=opened`,
`NOT_APPLICABLE_CLOSED` without `closed`, or a verdict that differs from the
result), a consistency check that does not authenticate anything. Admission
records must carry the full `issue-lifecycle/v2` identity of the named Issue and
`native_relation_listed=true`, otherwise the history is malformed. These shape
checks reject hand-made records; they do not authenticate caller intent, and the
role stays trusted caller intent. A fully fabricated local history remains the
documented instruction-contract bypass.

The audited active set is every admitted or receipted Issue whose latest binding
action is not `retire`. Leaving an Issue out of a later sync never retires it,
and an admitted Issue without a current-HEAD receipt fails the audit. To stop
delivering an Issue: remove every reference to it from the MR description (its
`Issue:`/`Issues:`/`Work Item:`/`Root Issue:` line or table row, a closing keyword
such as `Closes`, the URL in any form, `#<iid>` in the MR project and
`group/project#<iid>`), then run `retire` with a one-line reason. Retirement requires
a fresh read showing the same Issue identity, the current open MR HEAD and a
description that no longer references the Issue. Detection normalizes scheme, host
case, trailing `/`, sub-paths after the IID (`/issues/24/designs/mock.png`),
queries, fragments and short references only to find references; this is a
conservative detection policy, not a claim about GitLab's native linking, and it
grants no trust or access. A declaration/closing line is `binding_still_declared`,
any other mention `mr_binding_context_ambiguous`, and a `null`/non-text description
`mr_binding_context_unreadable` (not treated as empty). A short `#<iid>` in another
project's MR is that project's Issue, not this one. GitLab keeps the historical cross-reference, so the
record stores `native_relation_listed` and always `native_unlink=false`; never
delete notes to "unlink". History stays append-only; the audit re-reads every
retired Issue and fails if it is referenced again (re-admit and refresh it).

```bash
BINDING_ROLE=new-work  # or closed-reference
uv run <skill-path>/scripts/record_issue_binding.py admit --state "$STATE_FILE" \
  --issue "$ISSUE_URL" --mr-project "$PROJECT_PATH" --mr-iid "$MR_IID" \
  --revision "$HEAD_SHA" --binding-role "$BINDING_ROLE"
uv run <skill-path>/scripts/record_issue_binding.py retire --state "$STATE_FILE" \
  --issue "$ISSUE_URL" --mr-project "$PROJECT_PATH" --mr-iid "$MR_IID" \
  --revision "$HEAD_SHA" --reason "<scope moved to ...>"
```

Exit 0 records `ADMITTED`/`RETIRED`; exit 1 prints a `REJECTED` record with
`gap` and writes nothing; exit 2 is an input, lock or state-binding error.

Migration of a state written before roles existed: do not edit history. Opened
legacy receipts stay valid as default `new-work`. A legacy closed reference
fails with `binding_role_unresolved` until `admit --binding-role
closed-reference` (allowed only without earlier opened receipts) and a verifier
re-run. A legacy Issue that is no longer delivered is retired as above.

## Submitted/revised receipt: review-state boundary

A comment/body readback alone is not a submitted or revised receipt
([#61](https://gitlab.addx.ai/engineering/skills/-/issues/61)). After the note is
written or reconciled, the authorized parent sets the review state, then runs the
read-only verifier. It is the only producer of the `kind=issue_sync`
submitted/revised history entry.

The state write stays with the authorized parent and is a transition, not an add:

0. Fresh GET the Issue. If live `state=closed` and the Issue is admitted as
   `closed-reference`, skip steps 1-3: no label PUT, no reopen, no relabel. Run the
   verifier with `--state`; it returns the terminal `NOT_APPLICABLE_CLOSED`. A closed
   `new-work` Issue returns `ISSUE_SYNC_UNVERIFIED issue_closed_during_review`, and a
   closed Issue without an admission `binding_role_unresolved`; neither is reopened.
1. Fresh GET the Issue labels and the project label catalog including ancestor
   groups (`labels?search=status::in-review&include_ancestor_groups=true`).
   Confirm a label named exactly `status::in-review` exists **before any PUT**.
   If it is absent, or only native Status exists, stop with the structured
   `BLOCKED` project-mode gap; never let `add_labels` create the label and never
   claim native Status support.
2. Build the transition from the observed labels: `remove_labels` = every
   existing `status::` label (case-insensitive scope match) other than exact
   `status::in-review`; `add_labels=status::in-review`. Every unrelated label
   stays. Never PUT `labels` (overwrite semantics).
3. If a status label name cannot be passed safely (for example it contains a
   comma) or the readback is unclear, stop with `ISSUE_SYNC_UNVERIFIED` instead
   of guessing. Do not backfill historical Issues/MRs (MR205).
4. Names are not identities. Before a name-based remove/add, check that each
   name in the transition maps to exactly one catalog label. If the catalog has
   the same title more than once (for example a project label and an ancestor
   group label), or a write ACK disagrees with the readback (a 200 that still
   leaves the old status), the transition is not complete. Do not repeat the
   same name-based PUT. Instead, fresh-read the Issue's attached label global
   IDs and the native schema the project has approved. An authorized caller may
   then send an exact `removeLabelIds`/`addLabelIds` delta for only the status
   labels. Unrelated label IDs stay. Never overwrite `labels`/`labelIds`. If the
   exact identities, the native capability or the permission cannot be proved,
   keep the structured `ISSUE_SYNC_UNVERIFIED`/`BLOCKED` with an owner and next
   action. Retry only the operation that the readback shows is still missing.
   When a write outcome is unknown, read first. Never create labels automatically.

```bash
# Step 1-2 above; OLD_STATUS is the comma-joined observed status:: labels except status::in-review.
glab api --method PUT "projects/<issue-project>/issues/<iid>" \
  -f add_labels=status::in-review ${OLD_STATUS:+-f remove_labels="$OLD_STATUS"}
SYNC_PHASE=submitted  # or revised
uv run <skill-path>/scripts/verify_issue_sync_receipt.py \
  --issue "$ISSUE_URL" --mr-project "$PROJECT_PATH" --mr-iid "$MR_IID" \
  --revision "$HEAD_SHA" --phase "$SYNC_PHASE" --note-id "$NOTE_ID" \
  [--expected-body-file <posted body>] [--state "$STATE_FILE"]
```

Step 4 delta shape (upstream `UpdateIssueInput`). Every ID comes from the fresh
readback of this Issue; never copy IDs from another Issue or a previous run.
`clientMutationId` is only echoed back. It does not make the write idempotent, so
read back before any retry:

```graphql
mutation { updateIssue(input: {projectPath: "<issue-project>", iid: "<iid>",
  removeLabelIds: ["<attached old status:: label gid>"],
  addLabelIds: ["<catalog status::in-review gid>"], clientMutationId: "<trace id>"})
  { issue { labels { nodes { id title } } } errors } }
```

With one live GET set (`user`, Issue, the note, MR, related MRs, project labels; the
project-label catalog is not requested for a live `closed` Issue), it
checks that the exact marker occurs once within the selected note, note
author/Issue/LF-only body, MR HEAD/open state/related MR, and that the Issue's
only `status::` label is exactly `status::in-review`. The role comes only from
the `--state` admission record (no `--state`: default `new-work`).
Exit 0 means `VERIFIED`, or for a live `closed` Issue admitted as
`closed-reference` `NOT_APPLICABLE_CLOSED`; read `result`. Exit 1 prints and records a structured
`ISSUE_SYNC_UNVERIFIED` or `BLOCKED` receipt with `gap.reason`, `owner` and
`next_action`. Exit 2 is an input or state-binding error. Native `opened`/`closed` is
stored as `observed_state` and never counts as a review state.

- A live `closed` Issue admitted as `closed-reference` skips only the label check. Every note, actor, MR, HEAD,
  marker, related-MR and body check still applies. `review_state.verdict` is
  `NOT_APPLICABLE_CLOSED` with provenance `live GET issue state=closed`. It is never
  in-review and never `verified`. `native-status` stays `BLOCKED`, offline snapshots
  never pass, and any other native state stays `ISSUE_SYNC_UNVERIFIED issue_not_open`.

- Supported mode: `canonical-label` only. `--project-mode native-status` is
  recognized but returns `BLOCKED unsupported_project_mode`. A project without the
  canonical label returns `BLOCKED canonical_review_label_absent`. Neither gap
  lets submission sync complete.
- An offline `--check-snapshot` replay never verifies and cannot be written to state.
  Caller booleans, body text such as `review_state: ...`, and receipts for another
  Issue, MR, phase or HEAD are rejected.
- Before Step 5 init, keep the printed receipt. After `init_drive_state.py`, admit
  each binding, then re-run the verifier with `--state` so history holds the
  verifier's own output. All writes take the shared state lock (background-drive §1).
- `validate_drive_audit.py` requires a live terminal receipt for the current HEAD,
  recorded after the Issue's latest binding action, for every active Issue:
  `VERIFIED` with `observed_state=opened`, or `NOT_APPLICABLE_CLOSED` with
  `observed_state=closed` under a `closed-reference` admission. It then repeats the live
  check, and the outcome and state must match the stored ones. A stale, edited,
  self-asserted or now-failing receipt yields `AUDIT FAIL: ISSUE_SYNC_UNVERIFIED`.
  So does an Issue that was closed or reopened after the receipt; re-run the
  verifier instead of converting the receipt.
- Merged/final phases do not require `in-review`. Merge, preflight or green CI do not
  close the Issue; business acceptance stays with its owner.

Any write failure, unreadable evidence or inconsistent readback yields
`ISSUE_SYNC_UNVERIFIED`. Preserve the actual MR/Issue state and the verified
receipts for successful Issues, report the failing Issue and exact missing
operation, and stop the next lifecycle transition/local deletion. Do not claim
an Issue was updated/closed or wrap-up completed until its readback succeeds.
Independent reversible validation can continue while synchronization is pending.

## Acceptance scenarios

1. Merge delivers implementation but deployment/observation is pending: the
   Issue receives evidence and stays open.
2. One of several repository MRs for the same Issue merges: progress receipt
   naming the merged MR and the remaining ones; the Issue stays open.
3. The last MR merges and the Issue scope ends at merge: final receipt, close,
   and read back `state=closed`.
4. Notes POST or close PUT times out: reconcile before retry; no false completion
   and no local deletion while the result is unknown.
5. A superseding push changes HEAD: submission receipt must be refreshed before
   Driver resumes; readback for the previous revision cannot pass the new audit.
6. Comment readback succeeds but the Issue has no `status::in-review` (MR205):
   `ISSUE_SYNC_UNVERIFIED review_state_missing`; Driver/Auditor cannot claim
   submission sync complete until the label is set and the verifier re-runs.
7. Project lacks the canonical label or only native Status is available:
   `BLOCKED` with owner and next action; no verified state is inferred.
8. A release-flowback MR admits an Issue as `closed-reference` (open or already
   closed): no label PUT or reopen once closed. The live receipt is
   `NOT_APPLICABLE_CLOSED` and the audit can pass. If the Issue is reopened later,
   the audit fails until the verifier re-runs.
9. A `new-work` Issue is closed during review (manually, by another MR or too early):
   the refreshed receipt stays `ISSUE_SYNC_UNVERIFIED issue_closed_during_review`,
   the audit fails, and re-admitting it as `closed-reference` is rejected.
10. An Issue is dropped from the MR: its old receipt keeps the audit failing until
    the description no longer references it in any form and `retire --reason`
    records the live proof; the old receipt and cross-reference remain in place.
11. The MR merges or the Issue reaches its final state: after the native readback,
    record each Issue with `drive_state.py postmerge` (`merged`, then `final` when
    applicable); a merged MR with an unsynchronized Issue stays a parent to-do.
