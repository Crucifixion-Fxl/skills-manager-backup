# Production-only Argo CD overlay exception

This is a narrow `production-non-promotion` classification for an application
repository that also has a protected `staging` branch. It is not a substitute
for staging verification of business code and does not authorize a production
rollout. The evidence URL must point to the authoritative production
Application in `DEV/argocd-apps` on its protected `main` branch:

```text
https://gitlab.addx.ai/DEV/argocd-apps/-/blob/main/<cluster>/<application>.yaml
```

Pass that URL as `--staging-flow-evidence`, and the matching staging
Application URL on the same protected GitOps `main` branch as
`--staging-application-evidence`, together with
`--staging-flow-exists false` and a specific `--not-applicable-reason`.
This mode fixes the staging branch name to `staging`; an alternate name cannot
be supplied to bypass the branch check.
The `false` value describes the **change class**, not the existence of the
repository's staging branch. The initializer does not accept the URL or reason
at face value. It fetches the current GitLab MR, protected production and
staging branches, and both Applications at the same fixed GitOps commit. It
checks that the production Application points to this project, the MR's
production target branch and an existing `overlays/prod-*` path, while its
matching staging Application points to the same project, the `staging` branch
and a separate `overlays/staging-<region>` path, optionally with a numeric
cluster qualifier such as `staging-us-390`. The staging path must remain in the
same overlay parent as production and match the Application's region; an
arbitrary suffix or a production path is not accepted. The candidate HEAD and target-to-candidate
Git diff must match GitLab. Every deployment change must modify an existing
regular YAML file beneath that production overlay. The content is limited to
`NetworkPolicy.spec` or JSON patches of workload NodePool/egress selectors;
image, resource-source and arbitrary workload changes are not exempt. Only
`docs/requirements/<Work Item IID>/plan.md` and an existing
`docs/deployment/prod-deployment-plan.md` may accompany it. A changed
production file may be absent from staging, as in Issue #170. If staging has
that file but its content differs from the production target, classification
fails closed until that lineage is resolved.

Shared bases, staging overlays, CI, business code, image changes, added/deleted deployment
files, unrelated docs, an unprotected GitOps branch, a stale MR SHA or a fake
Application URL are rejected. Other change classes still require production
parity, a separately attested cleanup or an approved emergency hotfix.

The initializer records a SHA-256 attestation bound to the current MR, branch
heads, both Application blobs and exact changed paths. The Auditor re-runs the live
attestation and rejects changed evidence; its ordinary workflow observation
alone cannot complete this exception.
