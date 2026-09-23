# Mode procedures

Use this file only after `verify_standards.py` passes. Normative details remain in the standards repository:

- [HTML Owner onboarding SOP](https://gitlab.addx.ai/domain-workspaces/standards/-/blob/779ed7dd25627bda043774e0c63f210b7a284d36/docs/workspace-onboarding.html)
- [Proposal schema](https://gitlab.addx.ai/domain-workspaces/standards/-/blob/779ed7dd25627bda043774e0c63f210b7a284d36/schemas/workspace-proposal.schema.json)
- [Proposal template](https://gitlab.addx.ai/domain-workspaces/standards/-/blob/779ed7dd25627bda043774e0c63f210b7a284d36/templates/workspace-proposal.yaml)
- [Governance validator](https://gitlab.addx.ai/domain-workspaces/standards/-/blob/779ed7dd25627bda043774e0c63f210b7a284d36/scripts/governance-validate)
- [Workspace validator](https://gitlab.addx.ai/domain-workspaces/standards/-/blob/779ed7dd25627bda043774e0c63f210b7a284d36/scripts/workspace-validate)

## ASSESS

Remain read-only. Read `catalog/workspaces.yaml` and `catalog/repository-references.yaml` from the verified standards revision. Inventory the requested durable outcome, Owner evidence, roadmap/lifecycle, repository set, source-owned detail, reusable-standard potential, and excluded local material.

Run `$SKILL_ROOT/scripts/assess.py`. Review its catalog-wide boundary comparisons and per-repository overlaps. Select exactly one outcome:

- `extend-existing` when a related workspace is preferred or the boundary duplicates/converges;
- `source-repository-docs` when the durable material belongs to one source repository or only implementation/runbook detail remains after exclusions;
- `standards` when the rule is reusable across all or a class of workspaces without business-specific topology;
- `new-workspace` only for a durable, independently governed, cross-repository boundary that is distinguishable from every non-archived catalog entry.

`new-workspace` authorizes `PROPOSE`, not `INIT`. List unresolved Owner, name, boundary, permission, and reuse evidence explicitly.

## PROPOSE

Copy the verified standards template to the proposal destination. Preserve the schema exactly and replace example values using evidence; do not guess an Owner, final English slug, boundary key, approval, or GitLab permission.

Cover every non-archived catalog workspace in `boundary_assessment`. Cover every reused repository and all its current workspace references in `repository_reuse`. Keep the three alternatives explicit: extending related workspaces, source-repository docs, and standards.

Validate with the Skill gate, which re-verifies standards and runs the governance validator from the verified Git object:

```bash
uv run python "$SKILL_ROOT/scripts/gate.py" propose --standards-root /path/to/standards --proposal /path/to/proposal.yaml
```

Detailed reader material created by the proposal or target workspace must be HTML-first. Markdown remains appropriate for repository/Agent entry files and Skill internals.

## INIT

Run `$SKILL_ROOT/scripts/gate.py init` before any namespace or project mutation. It re-verifies the trusted, clean standards checkout and executes the official governance validator from the verified Git object. It rejects the unchanged standards template, `Example` placeholders, stale permission dates, and non-auditable evidence pointers. It adds fail-closed checks for a confirmed governance Owner, same-day project-creation permission bound to that Owner, and complete reuse evidence after SSH/HTTPS Git URL normalization.

After the gate passes, re-read group membership and namespace creation policy. Confirm that the evidence in the proposal still names the actual creator and current permission. Create only the approved private `domain-workspaces/<project_path>` target, starting at `incubating`; never create shells for speculative candidates.

Initialize from the verified standards `examples/generic-workspace-v2`, then replace example values without copying product source. Keep the target workspace as the only write scope. Register catalog and repository references through reviewed standards changes; do not modify standards from the target Owner Agent conversation.

## INGEST

Require one independent Owner Agent conversation per target workspace. The coordinating Agent may compare catalogs and candidate boundaries but must hand each target to its own Owner Agent before writing.

Inventory legacy material and run `$SKILL_ROOT/scripts/ingest_filter.py` with the real legacy `--source-root`. Distill allowed material into HTML domain context, accepted ADRs, repeatable workflows, manifest references, and source pointers. Do not bulk-copy. Allowed items must be regular UTF-8 files up to 1 MB; the helper binds their SHA-256 and rejects secret-like content. Sensitive, transient, SQL/log, worktree, screenshot-like, binary, missing, or symlink paths override a caller-supplied allowed category and remain excluded. If the request is only a temporary project, personal archive, Issue/MR checkout pool, or one-off investigation, return `workspace_eligible: false` and route any durable source-owned residue to source-repository docs.

## VALIDATE

Run the verified standards `scripts/workspace-validate` on the target. Run governance validation for catalog/registry/proposal. Check YAML syntax and relationships, local HTML links/fragments, ADR metadata/indexes, manifest v2 taxonomy and unique boundary, unmanaged siblings, full-SHA snapshots, freeze rejection of dirty/origin-mismatched repositories, and clean/equal local/remote Git heads.

The Git-state gate ignores executable target/global/system Git configuration, rejects linked worktrees, split indexes, special index flags, and gitlinks/submodules, reads the literal origin without URL rewrites, and contacts it only after it matches the central catalog. Remote probes carry no implicit HTTPS helper, SSH agent, user SSH config, or default identity. For a private remote, the operator may explicitly set trusted process environment `DOMAIN_WORKSPACE_GIT_CREDENTIAL_HELPER` (for example, a platform keychain helper) or `DOMAIN_WORKSPACE_GIT_SSH_COMMAND`; never derive either value from workspace files, proposal data, or ingested material.

Record all checks, an executable representative workflow at an exact source SHA, a dated review plan, and the six evidence layers in the validation-evidence contract. Save each activation readback as a recent raw artifact with SHA-256 and run `$SKILL_ROOT/scripts/gate.py validate` against the actual workspace checkout. Use `--require-activation` only for an incubating candidate. A valid package returns `activation_candidate_ready: true` but always keeps `activation_authorized: false`; the independent Owner Agent must reopen the evidence URLs and make the separate activation decision.

## ARCHIVE

Do not jump from `active` to `archived`. First mark `deprecated`, freeze new scope, select a successor or source-owned destination, and migrate all consumers, registry references, reader entry points, and Agent routes.

Use two phases. In `preflight`, keep the actual workspace and central catalog at `deprecated`; require migrated consumers, working redirect/source pointer, confirmed Owner, a reviewed catalog transition, and a reviewed repository read-only plan. The gate runs the pinned official workspace and governance validators and binds actual origin/HEAD to the catalog, but only returns `archive_candidate_ready`. The independent Owner must reopen the evidence, explicitly authorize the transition, then commit/push the workspace lifecycle and landing page, merge the catalog/registry transition, and apply repository read-only control outside this helper.

In `post-transition`, re-run the same command with `phase: post-transition`, actual workspace/catalog lifecycle `archived`, transition approval, completed catalog update, and completed repository read-only raw readbacks. The governance validator must accept the archived catalog and registry; the gate additionally binds catalog `archive.archive_commit` to actual HEAD and catalog `archive.redirect` to the verified redirect target. It returns only `archive_transition_verified`. Every assertion uses the contract's entity-bound `observed_value`, recent raw artifact, SHA-256, HTTPS evidence URL, and verifier. Both phases always keep `authorized: false` and `archive_authorized: false`. Retain the read-only HTML landing page, Owner record, accepted ADRs, and reproducible snapshots. Archived workspaces require a new proposal to return; never reactivate in place.
