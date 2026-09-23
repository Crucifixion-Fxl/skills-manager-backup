# Post-merge local cleanup

This flow removes one local linked worktree and its local feature branch after
GitLab proves that the corresponding MR is merged. It never deletes the remote
branch.

## Trigger boundary

After the Step 7 audit passes, first inspect the original request, current
thread, and drive-state history. Ask only when the request and thread contain
no explicit choice and history contains no complete matching
`decision=pending|approved|declined` event for the same MR, worktree, and
branch. The completion message must then proactively ask once whether to clean
this session's local worktree and branch after merge. A matching `pending`
event suppresses another prompt. If GitLab already reports `state=merged`, ask
whether to clean them now. A green pipeline, `AUDIT PASS`, becoming mergeable,
or asking the question does not authorize cleanup.

Run this flow only after the user explicitly agrees. The user may agree while
the MR is still awaiting merge, but execution must wait until a fresh GitLab
read proves `state=merged`. When prompting and when receiving a decision, append
a `kind=post_merge_local_cleanup` event to the existing drive state's `history`.
Each event must contain `mr_iid`, the current session's absolute `worktree`,
`branch`, `decision=pending|approved|declined`, and `ts`. On resume, only the
latest event whose MR, worktree, and branch all match is valid. Missing or
incomplete records do not prove consent. `declined` stops cleanup; `pending`
does not repeat the prompt for that MR and session.

The caller must already know the exact worktree created or reused for the
current session. Do not discover candidates by scanning all local branches or
worktrees, and do not clean sibling sessions.

## Deterministic gate

Resolve the absolute directory that contains the loaded `gitlab-mr/SKILL.md`,
then preview the exact cleanup from the current-session worktree:

```bash
GITLAB_MR_SKILL_DIR="/absolute/path/to/the-loaded/gitlab-mr"
test -f "$GITLAB_MR_SKILL_DIR/SKILL.md"
BRANCH=$(git branch --show-current)
uv run "$GITLAB_MR_SKILL_DIR/scripts/cleanup_merged_worktree.py" \
  --worktree "$PWD" \
  --branch "$BRANCH" \
  --mr-iid "$MR_IID"
```

`status=ready` proves all of the following at the instant of the check:

- the target is a registered linked worktree, not the primary checkout;
- the worktree contains no tracked changes, untracked files, or ignored files;
- it is checked out on exactly the requested local branch;
- GitLab reports `state=merged` and a non-empty `merged_at`;
- the MR source branch equals the local branch;
- the local branch HEAD equals the MR source SHA;
- the MR target branch equals the repository's GitLab default branch;
- the default branch is not the branch being removed; and
- another worktree survives to administer the shared repository.

Matching the MR source SHA is the no-local-new-commit proof. It also makes the
flow safe for squash merges, where `git branch --merged <target>` may reject a
feature branch even though GitLab has merged its MR.

## Execute

Only after the user explicitly agreed to cleanup, rerun the same command with
`--execute`:

```bash
uv run "$GITLAB_MR_SKILL_DIR/scripts/cleanup_merged_worktree.py" \
  --worktree "$PWD" \
  --branch "$BRANCH" \
  --mr-iid "$MR_IID" \
  --execute
```

Each external Git/GitLab command has a 60-second timeout. The helper rechecks
local state, removes the linked worktree without force,
deletes the proven local branch with an atomic `update-ref` bound to the
verified source SHA, and prunes worktree metadata. It returns structured JSON.
After `status=cleaned`, run subsequent commands from the
surviving checkout because the former working directory no longer exists.

Any failed check returns `status=blocked` with a reason and performs no cleanup.
If a destructive Git command itself fails, the helper returns `status=partial`
with `worktree_removed`, `local_branch_deleted`, and
`worktree_metadata_pruned`. Report those exact fields; do not retry with a
broader target, `--force`, or a different branch.
