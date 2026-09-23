# Isolated Git workspace

Never clone the Dashboard repository below the current caller repository.
Use `scripts/grafana_workspace.py prepare` to create a fresh temporary checkout
outside it. Every later Git command must target that checkout explicitly.

The helper rejects a worktree unless all conditions hold:

- its Git root equals the selected worktree;
- its `origin` normalizes to the fixed approved Dashboard repository URL;
- it is neither inside the caller repository nor an ancestor of it;
- its branch matches `grafana/<lowercase-slug>`.

For every successful source-changing request (`create`, `update-source`, or an
explicitly confirmed `remove-source`), validate first, then call
`scripts/grafana_workspace.py submit`. It may commit only named
`dashboards/**/*.json` paths, push that feature branch, and call `glab mr
create --repo DEV/grafana-dashboards-as-code`. The helper returns a parsed,
exact MR URL; return that URL to the user and tell them to send it to Ops for
review. Immediately after that reminder, return the output-only expected
Dashboard URI resolved from the stable UID. State that it becomes usable only
after the MR merges and the protected deploy/verify Pipeline succeeds.

`validate` and `diff/plan` do not create a commit, push, or MR. Never use
`git add .`, a submodule, subtree, force push, or the caller repository's
remote. The source-changing submission affects only the fixed Dashboard
repository; it never commits or changes the caller repository.
