# Associated Issue update and closure

This is a mandatory parent-session responsibility at MR submission/update,
actual merge, and session wrap-up. It complements the shared
[Issue lifecycle contract](../../../collaboration/gitlab-issue-sop/references/lifecycle-binding.md);
it does not replace acceptance with a merge result.

## Scope and triggers

Use the verified Root Issue and repository Work Item from Step 1.25. Include
other Issues only when their native MR/Issue association and delivered scope
have been verified. A URL in prose, related example, dependency or historical
reference is not a closure instruction. Deduplicate by GitLab host/project/IID;
when Root and Work Item are the same Issue, write one receipt.

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
| Root Issue still has required release/runtime/business/observation/child work | Keep open, even if this MR and a repository Work Item are complete |
| Issue already closed | Read the closure evidence; do not reopen or repeat a close automatically. If current evidence contradicts closure, report the discrepancy and stop the transition |

A green pipeline, HTTP 200, Collector acknowledgement, package publication or
MR merge proves only its own stage. Close a Work Item at merge only when its
documented scope actually ends there. Root and Work Item sharing an identity
must satisfy the Root's full completion conditions. Do not introduce closing
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
Notes to reconcile an existing marker. Verify its author and evidence content;
a matching marker alone is insufficient. After writing, GET the exact note
and verify Issue identity, author, marker and body. After a close request,
GET the Issue and verify its final state. On a timeout or unknown response,
read back first; retry only a missing operation, without duplicate comments.

The parent saves `kind=issue_sync` entries in drive-state `history`, recording
phase, Issue URL/project/IID, MR IID, revision, note ID/URL, intended disposition,
observed state, verified-at UTC and result. Receipts bind the current revision;
an old HEAD receipt does not cover a later push. For a standalone cleanup with
no drive state, preserve equivalent receipts in the session evidence record.

Any write failure, unreadable evidence or inconsistent readback yields
`ISSUE_SYNC_UNVERIFIED`. Preserve the actual MR/Issue state and the verified
receipts for successful Issues, report the failing Issue and exact missing
operation, and stop the next lifecycle transition/local deletion. Do not claim
an Issue was updated/closed or wrap-up completed until its readback succeeds.
Independent reversible validation can continue while synchronization is pending.

## Acceptance scenarios

1. Merge delivers implementation but deployment/observation is pending: Work Item
   and Root receive evidence; any Issue with outstanding conditions stays open.
2. A merge-only Work Item is complete and Root awaits business acceptance:
   final receipt and close readback for Work Item; progress receipt for Root.
3. Root equals Work Item: one receipt; close only when the full Root scope is done.
4. Notes POST or close PUT times out: reconcile before retry; no false completion
   and no local deletion while the result is unknown.
5. A superseding push changes HEAD: submission receipt must be refreshed before
   Driver resumes; readback for the previous revision cannot pass the new audit.
