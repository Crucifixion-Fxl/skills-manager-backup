---
name: manage-business-image-rollback
description: 为已纳管 Kustomize 业务准备精确镜像 pin 或 release MR；只改 argocd-apps，不执行线上回滚。
---

# Workflow：manage-business-image-rollback

Read `references/business-image-rollback.md`, `references/image-automation/README.md`,
`references/data/stop-conditions.yaml` and `recipes/argocd/application.yaml.tmpl`.
This workflow declares that recipe's **existing-Application incident-pin variant**, not a new
Application. Only the existing target file in `argocd-apps` is writable. No new manifests, source
code, CI, app overlay, root policy, RBAC, or automatic execution helper is created.

## Step 1. Resolve mode and evidence

[precondition]
  - The user explicitly asks to prepare a rollback pin MR or a release/unpin MR.
  - Resolve `mode_keywords` from the route to `pin` or `release`. A request naming both is a
    sequenced plan: prepare pin only, and defer release until runtime evidence exists.

[action]
  - Read `troubleshooting/business-image-rollback-history.md` and collect its read-only evidence.
    Existing current evidence may be reused; unknown fields stay unknown.
  - Confirm the exact catalog target, existing Git file, live UID/source/mappings, writer ownership,
    current pins/original policies, candidate image set, compatibility, and release-freeze evidence.
  - `pin`: select the reviewed healthy rollback set, or a specifically approved fixed release set.
    `release`: require the exact fixed image set already deployed and business-verified; if it has
    not been deployed, return a separate pin plan first. Never blindly restore broad filters.

[validate]
  - Single-source Kustomize, existing stateless Deployment/Rollout, annotation `argocd` write-back,
    precise root ignore contract, real immutable SHA images available in the target Harbor.
  - Run `python3 "$skill_root/validators/check_deployment_target.py" --cluster <exact-catalog-name>`.
    A retired/unknown target remains queryable, but this Build route stops on it.
  - For prod read `references/cost-tiering/_global.yaml` → `prod_self_check`; this route cannot
    change capacity. Record applicability: verify existing release/staging evidence; infrastructure
    provisioning, new capacity/cost and database-CR field checks are `not applicable — image-only,
    no resource change`, not a reason to invent a database or edit resource settings. Known
    infrastructure risks remain in the Ops Todo; any required capacity change is a separate plan.
  - Apply every shared contract admission gate. Missing data, hooks with unapproved effects,
    incompatible data/config, competing operations/writers, Helm or multi-source means STOP + Ops Todo.

[output]
  - Sanitized evidence record, mode, target file, exact allowed diff and execution handoff scope.

## Step 2. Prepare the bounded Git change

[precondition]
  - Step 1 passed. Record clean base commit/status and the original annotation presence/values and
    recovery seeds in the MR evidence; preserve pre-existing unrelated work.

[action]
  - Incident-pin variant write allowlist, only for the approved aliases:
    1. `metadata.annotations["argocd-image-updater.argoproj.io/<alias>.allow-tags"]`.
    2. The matching entry in `spec.source.kustomize.images`.
  - `pin`: exact `regexp:^<verified-sha>$` and matching literal Harbor path/SHA seed. Apply all
    coupled aliases together, preserving unrelated entries and list order.
  - `release`: restore the recorded original allow-tags policy, retaining the currently verified
    fixed image as recovery seed. Preserve ignore-tags and every other field. If a new exclusion
    or strategy change is needed, STOP for a separate reviewed policy change.
  - Record incident/reason, rejected candidates, owner/review time, original rules, proposed
    image set, validation and execution/acceptance plan in the MR body, not unrecognized annotations.

[validate]
  - Compare parsed before/after Application objects; every changed field must be in the allowlist,
    the same aliases must retain image paths and mapping, and no object is added/deleted.
  - Do not re-render the entire Application from the new-service template, replace arbitrary
    lists, alter `targetRevision`, add force-update, change syncPolicy, or rewrite another app's pin.
  - A release candidate must pass the shared contract's selection/concurrent-publication gate.

[output]
  - One existing Application file diff plus a reviewable evidence/operation plan.

## Step 3. Validate exact output and render

[precondition]
  - The allowed diff exists and its before/after identity matches the captured live target.

[action]
  - Copy only the changed Application file to an isolated validation directory, preserving its
    `<cluster>/<file>.yaml` relative path. Confirm it contains exactly the expected Application.
    Run `python3 "$skill_root/validators/check_argocd_application.py" "$candidate_dir"` and
    `bash "$skill_root/validators/validate.sh" --repo-context argocd-apps "$candidate_dir"`.
  - Resolve/render the exact current source commit with all live overrides, then the same commit
    with the candidate image set, without syncing. Compare parsed manifests under the shared
    contract: only intended workload image changes, no other changes or deletions. For release,
    simulate the image set the restored updater policy would select, not just the retained seed.
  - Run `bash "$skill_root/validators/validate.sh" --repo-context app "$rendered_candidate_dir"`
    on that exact render and the target repository's applicable existing checks.

[validate]
  - All commands exit 0 and the render/compatibility/hook checks pass. The existing validator's
    exact SHA/seed check is sufficient for syntax, not health, field-diff scope or release selection.
  - Invalid inputs, missing dependencies, unavailable render or violations stop the workflow;
    do not introduce exemptions or invoke `validate_delta.py` for this route.

[output]
  - Exact base/candidate commits, bounded diff, render identity and validator evidence, or raw errors.

## Step 4. Submit MR and hand off execution

[precondition]
  - Step 3 passed and fresh source/target state still matches the reviewed plan.

[action]
  - Commit only the allowed diff in a branch and create the normal `argocd-apps` MR. Include the
    shared evidence contract and explicit statement that merge may trigger a production rollout.
  - Return `MR prepared`. Hand off the plan to `argocd`: obtain concrete production authorization
    before merge/pin activation; verify pin effective; conditionally update only the live Application
    image overrides if needed; observe sync, actual digests and business checks. Preserve
    auto-sync/selfHeal and keep the pin after recovery.
  - Use `references/deployment-tracking.md` when execution is requested. Existing Task evidence
    does not grant merge/sync/rollback permission; no unsolicited issue/comment creation.

[validate]
  - No live mutation or MR merge by this workflow. CLI acceptance, a merged MR, and healthy Argo
    state alone cannot be reported as `runtime verified` or `automation released`.

[output]
  - MR URL, current lifecycle state, planned image set, execution owner/authorization boundary,
    acceptance criteria, pin review time, and any Ops Todo. No claim of completed deployment.
