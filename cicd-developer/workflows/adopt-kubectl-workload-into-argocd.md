---
name: adopt-kubectl-workload-into-argocd
description: Adopt an existing kubectl-managed stateless workload into a new Argo CD Application through an exact, passive, Git-first ownership transition.
---

# Workflow: adopt-kubectl-workload-into-argocd

## Purpose

Bring an already-live, namespaced stateless workload under durable Argo CD
ownership without treating it as a new service and without hiding a runtime
change inside the first sync. The first sync is an ownership-only gate. Image
normalization, automatic promotion, additive hardening, and retirement of the
legacy writer are separate stages.

This workflow is deliberately narrow. It does not adopt a StatefulSet, PVC,
database, cache, raw Secret, Namespace, historical Job, or cluster-scoped
resource. It does not convert a Deployment to a Rollout. A live Deployment is
allowed only as an exact grandfathered object; this does not authorize creating
a new Deployment in another application.

## Entry conditions

- One exact cluster context, namespace, application repository/path, future
  Application identity, AppProject, and legacy writer are known.
- The selected workload is healthy and has no Argo CD tracking identity, Helm
  release identity, or another controller that would compete for the same
  objects.
- The application owner and operations owner agree on a closed stateless
  included cohort and a closed excluded cohort. Every live object in the
  discovery scope is classified exactly once.
- Every included object already exists. State, credentials, migration Jobs,
  and additive hardening resources remain excluded from the passive cohort.
- A deterministic build/render command is available. Production work also
  completes `references/cost-tiering/_global.yaml -> prod_self_check`.

An unknown owner, incomplete inventory, unhealthy baseline, stateful handoff,
raw Secret capture, selector/immutable-field change, or proposed new object is
a STOP. Produce an Ops Todo instead of using `new-service.md` or broadening
prune.

## Step 1. Freeze non-secret live identity and the ownership boundary

[precondition]
  - Entry conditions pass and all discovery commands are read-only.

[action]
  - Capture live YAML for the exact discovery scope into a private,
    task-specific evidence directory. Record resource apiVersion/kind,
    namespace/name, UID, owner references, Argo/Helm tracking, and the fields
    needed for semantic comparison. Never capture Secret `data` or
    `stringData`; for excluded Secrets record only name, UID, owner, and key
    names through a separate redacted inventory.
  - For each included Deployment, record its UID, controller kind,
    `spec.selector`, replicas, strategy, Pod-template labels/annotations/spec,
    container image strings, live Pod image IDs, placement, and restart/Ready
    baseline. Record Service cluster identity and ports, Ingress identity and
    exposure annotations/hosts, and ConfigMap content.
  - Classify every scoped object into `$included_resources` or
    `$excluded_resources`. Excluded stateful middleware, PVCs, Secrets,
    historical Jobs, retired workloads, and independently owned dependencies
    remain outside both the render and Argo prune.
  - Record the legacy writer identity, whether it is enabled/idle, its exact
    write set, and the supported disable/verify procedure. Do not disable it.
  - Record all stage approvals as fresh, independent requirements in this
    order: `source-publish`, `project-permission`, `artifact-copy`,
    `image-normalization`, `passive-sync`, `source-branch-activation`,
    `automatic-promotion`, and `writer-retirement`. Planning approval is not
    any of these approvals.

[validate]
  - Every included/excluded entry has a non-empty live UID and appears exactly
    once. The captured live resource set equals their union.
  - Every rendered Deployment candidate will point to one included live
    `apps/v1 Deployment` with `grandfatheredExistingDeployment: true`; no flag
    can authorize a missing or differently named Deployment.
  - No secret value, token, password, or non-identity Secret field is present
    in evidence.

[output]
  - A reviewed non-secret adoption contract, live snapshot, Pod baseline, and
    closed included/excluded inventory.

Use this exact contract shape. Repeat inventory/provenance/writer entries as
needed; `excluded: []` is the only valid way to declare a proven-empty excluded
set. For a Service, `immutableFields` includes every server-allocated field
present in live state, not only `spec.clusterIP`. A Secret may appear only as an
excluded `apiVersion`/`kind`/`metadata.name`/`metadata.namespace`/`metadata.uid`
identity with UID/reason; omit every other top-level or metadata field. Use
`pinnedImageNormalizations: []` when no non-Image-Updater image needs a
tag-to-digest transition.

```yaml
apiVersion: cicd.addx.io/v1alpha1
kind: ArgoCDAdoptionContract
metadata:
  name: <application-name>
spec:
  target:
    clusterContext: <exact-kube-context>
    namespace: <existing-namespace>
    application: <future-application-name>
    project: <approved-appproject>
    repository: <application-repository-url>
    revision: <branch>
    path: <render-path>
  sourceRevision: <lowercase-40-character-git-sha-containing-reviewed-source>
  passiveRevision: <lowercase-40-character-git-sha>
  inventory:
    included:
      - apiVersion: apps/v1
        kind: Deployment
        namespace: <existing-namespace>
        name: <existing-deployment>
        uid: <live-uid>
        immutableFields:
          spec.selector: <exact-live-selector-mapping>
        legacyWriter: <exact-writer-name>
        grandfatheredExistingDeployment: true
    excluded:
      - apiVersion: v1
        kind: Secret
        namespace: <existing-namespace>
        name: <excluded-secret-name>
        uid: <live-uid>
        reason: <why-it-remains-outside-argo>
  imageProvenance:
    - alias: <unique-image-updater-alias>
      resource:
        apiVersion: apps/v1
        kind: Deployment
        namespace: <existing-namespace>
        name: <existing-deployment>
      container: <exact-container-name>
      sourceImage: <literal-current-image-with-immutable-tag>
      normalizedImage: <literal-target-image-with-passiveRevision-tag>
      digest: sha256:<64-lowercase-hex>
      commit: <same-passiveRevision>
      evidence: <reviewed-build-and-registry-provenance-reference>
      kustomizeImageName: <literal-kustomize-image-name>
      platforms: [linux/amd64, linux/arm64]
  pinnedImageNormalizations:
    - resource:
        apiVersion: apps/v1
        kind: Deployment
        namespace: <existing-namespace>
        name: <existing-deployment>
      container: <exact-init-or-sidecar-container-name>
      sourceImage: <literal-current-image-with-non-floating-tag>
      normalizedImage: <fully-qualified-image@sha256:digest>
      digest: <same-sha256-digest-as-normalizedImage>
      evidence: <reviewed-live-imageID-and-registry-evidence-reference>
      platforms: [linux/arm64]
  approvalStages:
    - source-publish
    - project-permission
    - artifact-copy
    - image-normalization
    - passive-sync
    - source-branch-activation
    - automatic-promotion
    - writer-retirement
  legacyWriters:
    - name: <exact-writer-identity>
      type: <writer-type>
      state: enabled-idle
      retirementAfter: automatic-promotion-accepted
      disableMethod: <supported-api-or-ui-procedure>
      verification: <disabled-and-idle-proof>
      writeSet:
        - apiVersion: apps/v1
          kind: Deployment
          namespace: <existing-namespace>
          name: <existing-deployment>
```

## Step 2. Prove image provenance and define the passive recovery seed

[precondition]
  - Step 1 passes.

[action]
  - For every application container whose image reference must be normalized,
    record the source image, immutable digest, exact full 40-character Git
    commit, provenance evidence, target image, required platforms, and target
    full-SHA tag. Do not infer provenance from a timestamp or mutable tag.
  - Prove the source commit produced the live digest. If the target registry
    does not yet contain the exact manifest, keep artifact copy as a separately
    approved future stage; never rebuild an accepted artifact merely to invent
    provenance.
  - For an initContainer or sidecar that is not managed by Image Updater but
    whose current version tag must become immutable, record it separately in
    `pinnedImageNormalizations`. The source must be a credential-free literal
    non-floating tag (not `latest`, `main`, `master`, `dev`, `staging`, or
    `prod`); the target must be a fully qualified digest reference;
    `digest` must exactly equal that reference's suffix. Record the live Pod
    image ID, registry evidence, and required platform. Do not invent an Image
    Updater alias, Git commit, or full-SHA tag for a third-party artifact.
  - Define the passive Image Updater contract: all aliases use
    `newest-build`, `write-back-method=argocd`, exact kustomize image names,
    required platforms, recovery seeds at the proven full SHA, and
    `allow-tags: regexp:^<accepted-full-sha>$`.

[validate]
  - Each normalized image tag exactly equals the same lowercase 40-hex commit
    recorded in its provenance, and each digest is `sha256:<64 hex>`.
  - Source and target image paths are literal and credential-free. Required
    platform members are explicit and match the target cluster contract.
  - Missing provenance or a digest/commit/platform mismatch is a STOP.
  - A pinned normalization with a mutable source tag, non-digest target,
    mismatched digest, duplicate container identity, or missing evidence is a
    STOP.

[output]
  - Exact image provenance and a fixed-SHA passive recovery seed; no registry
    or workload write.

## Step 3. Build the application-owned source and compare it with live

[precondition]
  - Steps 1-2 pass.

[action]
  - Create the application-owned overlay from the included cohort only. Do not
    copy a historical directory wholesale. Preserve identities, selectors,
    replicas, placement, Services, exposure, configuration, and controller
    kinds. The render contains no Namespace, Secret, PVC, StatefulSet, Job,
    database/cache, retired workload, or new PDB.
  - Put the proven normalized full-SHA image strings in the candidate render.
    This declared image-only transition is not part of passive ownership and
    must occur in Step 6 before the Application is activated.
  - Put every declared pinned third-party normalization at its proven digest
    in the candidate render. Pin every other rendered init/container image by
    full 40-character Git SHA or digest. A version-looking tag is still mutable
    and is not an adoption recovery identity.
  - Record the clean source checkout HEAD as `sourceRevision`. Every included
    resource explicitly declares `legacyWriter: <name>` or `legacyWriter: null`;
    writer write sets must match those declarations bidirectionally and may not
    overlap. HPA is excluded because automated self-heal and a rendered
    Deployment replica count would create a competing writer for replicas.
  - Render the exact candidate outside the source checkout. The directed gate
    verifies the credential-free checkout origin, clean full-SHA HEAD, in-tree
    canonical repository-relative target path, local-only committed Kustomize
    dependency closure, and an independent resource/output-bounded
    `kubectl kustomize` render with Git, proxy, SSH-agent, and kubeconfig state
    removed. Run the normal strict manifest suite, then the directed adoption
    gate. Name every Git host that is explicitly allowed for the later fresh
    target-branch proof; this argument does not authorize Kustomize egress:

    ```bash
    bash "$skill_root/validators/validate.sh" --repo-context app \
      "$clean_source_checkout/$target_path"
    python3 "$skill_root/validators/check_argocd_adoption_contract.py" \
      --contract "$adoption_contract" \
      --live "$live_snapshot_dir" \
      --render "$render_dir" \
      --source-root "$clean_source_checkout" \
      --verify-remote-host gitlab.addx.ai \
      --phase pre-normalization
    ```

  - The directed gate normalizes only documented Kubernetes server defaults
    (including probe threshold defaults and Pod-template creation timestamps),
    tracking metadata, and the AWS Load Balancer Controller group finalizer on
    a live Ingress. It permits only image-string transitions declared by
    `imageProvenance` or `pinnedImageNormalizations`; all other live/render
    semantics must be equal. Unknown finalizers remain a mismatch.

[validate]
  - Both commands exit 0. Inventory equality proves the render is exactly the
    included cohort and contains no excluded or unclassified object.
  - Every included UID and immutable-field assertion matches live. Every
    Deployment is the exact existing grandfathered Deployment.
  - Any non-image drift, mutable candidate image, new object, raw Secret, or
    excluded-resource appearance is a STOP; do not add an ignore.

[output]
  - One application-source candidate and deterministic normalized comparison
    evidence. Additive hardening is deferred until passive acceptance.

## Step 4. Prepare build-only delivery and the exact AppProject bridge

[precondition]
  - Step 3 passes and the frozen live baseline is still current.

[action]
  - Add immutable target image production in the application repository as a
    build-only pipeline. Use the target cluster's architecture contract and
    full `CI_COMMIT_SHA` manifest-list tag. CI must not call Kubernetes or
    Argo CD and must not create another deployment writer.
  - Validate the application-repository delivery candidate against every
    active target render path before requesting `source-publish` approval:
    - When the target path has no historical strict-validator debt, run the
      full strict suite directly:

      ```bash
      bash "$skill_root/validators/validate.sh" --repo-context app \
        "$target_path"
      ```

    - A delivery-contract-only candidate may use the delta validator when the
      strict failure is unrelated pre-existing debt. This exception is limited
      to `.gitlab-ci.yml`, `.dockerignore`, `Dockerfile*`, entrypoint scripts,
      `.mvn/**`, and dedicated CI/contract tests or verifier scripts. The
      target-to-candidate changed-path inventory must contain no application
      source, runtime configuration, `k8s/**`, Helm/chart, or other manifest
      path. Commit the candidate, require a clean worktree, set
      `$mr_target_branch` to the exact GitLab MR target branch, and run:

      ```bash
      python3 "$skill_root/validators/validate_delta.py" \
        --expected-origin "$application_repository_url" \
        --base-ref "origin/$mr_target_branch" \
        --repo-context app \
        "$target_path"
      ```

      The remote target must be a fresh verified ancestor of the candidate.
      The validator still runs the full strict suite on both fixed snapshots;
      every candidate-only finding, carried finding on a changed path, changed
      MR head/target SHA, dirty worktree, invalid remote proof, or nonzero exit
      is a STOP. This path does not waive the pipeline's credential-free
      contract tests, image build, CI lint, or architecture/manifest checks,
      and it cannot be used for a source, config, workload, or manifest change.
  - Before merging a source MR that publishes images, present its registry
    write set, impact, verification, and rollback; obtain fresh
    `source-publish` approval.
  - In a separate `argocd-apps` permission MR, add only the exact repository,
    destination, and namespaced resource permissions required by the included
    cohort. Do not use `default`, wildcard permissions, or a Namespace
    exception. A shared namespace such as `prod-us` satisfies the Fluent Bit
    phase prefix but not the `{phase}-{app}` ownership contract. Retaining it
    requires a separately reviewed exact, time-bounded identity in
    `namespace-legacy-exceptions.yaml`; never infer compliance from the prefix
    or create a namespace-wide bypass.
  - Before merging the permission MR, present its exact AppProject delta and
    rollback; obtain fresh `project-permission` approval. Observe natural
    reconciliation and prove the exact permission is live before proceeding.

[validate]
  - The strict validator or the bounded delivery-contract delta validator exits
    0 for every active target path. Delta evidence records the exact remote
    target and candidate SHAs and proves the candidate write set is confined to
    the declared delivery-contract files.
  - Pipeline output is immutable, architecture-complete, and build-only.
  - The permission MR adds no resource kind absent from the included render
    and no broader repository/destination authority.
  - Source merge, pipeline success, AppProject merge, and live permission are
    recorded as distinct facts.

[output]
  - Reviewed application source/build evidence and a live minimal permission
    bridge; still no Application and no runtime ownership change.

## Step 5. Copy accepted artifacts only after a fresh approval

[precondition]
  - The source build and permission stages are accepted, and provenance/live
    digests have been refreshed without drift.

[action]
  - Present exact source/destination image references, tags, expected digests,
    required platforms, credential mechanism, impact, verification, and
    rollback. Wait for fresh `artifact-copy` approval.
  - Copy manifests with all architectures using a credential file/stdin
    mechanism. Never print credentials or put them on a command line.
  - Compare raw source/destination manifests and configs. Record digest and
    platform-member equality.

[validate]
  - Both target full-SHA tags resolve to the accepted digests and required
    platform members. A rebuild, changed config digest, or missing member is a
    STOP.

[output]
  - Immutable target artifacts proven equal to the accepted live artifacts.

## Step 6. Normalize live image references as a separate rollout

[precondition]
  - Step 5 passes; the legacy writer is idle and no competing write is queued.

[action]
  - Present the exact image-reference-only commands, rolling-update impact,
    health gates, and old-image rollback. Wait for fresh
    `image-normalization` approval.
  - Change only the approved Deployment image strings. Wait for each rollout
    and verify desired/updated/ready replicas, replacement Pod image IDs,
    readiness/restarts, routing, authentication/API smoke checks, and declared
    dependencies.
  - Refresh live YAML, included/excluded UIDs, Deployment selectors and Pod
    cohort. Fetch the declared target branch from the explicitly approved Git
    host, check out its current remote tip in a clean source checkout, refresh
    `sourceRevision` to that exact commit, then re-run the directed gate with
    that checkout passed as `--source-root`, the same
    `--verify-remote-host gitlab.addx.ai`, and `--phase post-normalization` only
    after the accepted digest is running under the normalized full-SHA string.

[validate]
  - The accepted digest and resource UIDs are retained. No excluded object or
    non-image field changed.
  - The post-normalization directed gate exits 0 against the refreshed live
    snapshot and exact render. The `passive` phase remains reserved for Step 7,
    where `--application` is mandatory.

[output]
  - A post-normalization passive baseline. This rollout is recorded separately
    from the later Argo ownership transition.

## Step 7. Activate one fixed-SHA Application and accept passive ownership

[precondition]
  - Step 6 passes and the exact AppProject permission is live.

[action]
  - Create one Application for the exact repository/path/project/destination.
    Pin `spec.source.targetRevision` to the proven `sourceRevision` for the
    first ownership-only sync. Use automated prune/self-heal, required
    finalizer/notifications, exactly `PruneLast=true` for sync options,
    `write-back-method=argocd`, and complete aliases frozen to the provenance
    SHA. Do not create an ImageUpdater CR, ApplicationSet, Git credential,
    merger, or `.argocd-source-*` file.
  - Choose exactly one normal validator acceptance path before the directed
    passive gate:
    - If the trusted target directory has no historical strict-validator debt,
      run the full strict validator directly:

      ```bash
      bash "$skill_root/validators/validate.sh" --repo-context argocd-apps \
        "$argocd_apps_candidate_cluster_dir"
      ```

    - If the target has unrelated pre-existing strict-validator debt, use the
      validator delta only after the candidate is committed and the worktree
      is clean. Set `$mr_target_branch` to the exact GitLab MR target branch,
      then run:

      ```bash
      python3 "$skill_root/validators/validate_delta.py" \
        --expected-origin https://gitlab.addx.ai/DEV/argocd-apps.git \
        --base-ref "origin/$mr_target_branch" \
        --repo-context argocd-apps \
        "$argocd_apps_candidate_cluster_dir"
      ```

      This path runs the full strict suite against both the trusted merge-base
      and committed candidate snapshots, keeps inherited debt visible, and
      blocks every candidate-only finding. It also mechanically computes the
      candidate write set and rejects every carried finding on a changed path
      with exit 1. Select it only after inventorying
      every baseline finding and proving both that it exists on the trusted
      base and that its exact path/object is outside the candidate's intended
      write set; a failing candidate is not itself evidence of baseline debt.
      A baseline finding on any path/object the candidate changes must be fixed
      before proceeding, not carried as debt. `--base-ref` names only the
      concrete `origin/<mr-target-branch>`; the validator does not trust that
      local ref. It verifies the caller origin against `--expected-origin`,
      uses a verified same-identity credential-free transport for a fresh
      config-isolated fetch of the exact remote branch, fixes the fetched object
      ID, and uses that fixed ID for merge-base computation. The fresh target
      must be an ancestor of the candidate; otherwise exit 2 requires the
      candidate to be rebased or rebuilt and both gates rerun.
      Never pass a local branch, commit SHA, or arbitrary ref. The output must
      show the exact remote target SHA and candidate SHA reviewed for the MR.
      If the MR HEAD or target branch SHA changes, rerun both this validator and
      the directed passive gate. Do not add an application- or rule-specific
      ignore. A dirty or uncommitted candidate, missing or unfetched trusted
      base, unavailable validator, unrecognized output, or nonzero result is a
      STOP.

  - After the selected normal validator path exits 0, run the directed passive
    gate:

    ```bash
    python3 "$skill_root/validators/check_argocd_adoption_contract.py" \
      --contract "$adoption_contract" \
      --live "$post_normalization_live_dir" \
      --render "$render_dir" \
      --source-root "$clean_source_checkout" \
      --verify-remote-host gitlab.addx.ai \
      --application "$application_yaml" \
      --phase passive
    ```

  - Refresh the live/render comparison immediately before merge. Present the
    exact Application effect, orphan-safe rollback decision tree, and stop
    conditions; wait for fresh `passive-sync` approval.
  - Merge through Git review and observe natural root/Application
    reconciliation. Do not manually refresh or sync.

[validate]
  - The selected normal validator path and the directed passive gate both exit
    0. Delta validation does not waive baseline findings or permit any
    candidate-only finding; it only proves the committed candidate introduced
    none relative to the trusted merge-base.
  - The Application reaches `Synced/Healthy` with no conditions at the reviewed
    source. Included resource UIDs, Deployment Pod-template hashes, Pod UIDs,
    image IDs, readiness, and restart counts remain unchanged.
  - No excluded object acquires tracking or prune ownership. If replacement,
    deletion, Pod-template drift, or excluded tracking is proposed, STOP; do
    not delete the Application as a rollback shortcut.

[output]
  - Accepted passive Argo ownership at one exact recovery SHA.

## Step 8. Activate automatic delivery, then retire the legacy writer

[precondition]
  - Passive ownership is accepted and the legacy writer remains enabled but
    idle.

[action]
  - Prepare a first single-purpose Application MR changing only
    `spec.source.targetRevision` from `sourceRevision` to the declared target
    branch. Present the future-commit eligibility boundary, expected no-op at
    the equal branch tip, exact re-pin rollback, and health gates; wait for
    fresh `source-branch-activation` approval.
  - After approval and immediately before merge, freshly read the declared
    remote branch through the approved Git host and require its tip to remain
    exactly the accepted `sourceRevision`. Refresh live/render evidence and
    re-run the passive directed gate while the Application is still pinned. If
    the branch has advanced, abort the merge, keep the pin, and repeat source
    review plus passive equivalence against the new candidate; never unpin onto
    accumulated, unreviewed manifests. Merge only after this merge-time gate,
    then prove normal reconciliation changed no resource, Pod, or image before
    continuing.
  - Prove the newest eligible full-SHA release exists for every alias and all
    aliases resolve to the same commit. Measure the installed Image Updater
    reconciliation/write-back behavior against the build pipeline's
    non-atomic publication window across image repositories. Prove a partial
    publication cannot become an accepted mixed-alias release. One final CI
    job reduces and detects that window but is not an atomic registry
    transaction. If commit coherence cannot be proven, keep the fixed-SHA
    freeze and create an Ops Todo for a release coordinator or ready-marker
    design instead of activating promotion.
  - Only after source-branch activation and alias-coherence proof, prepare a
    second single-purpose Application MR changing only each fixed allow-list to
    `regexp:^[a-f0-9]{40}$`.
  - Present candidate SHA, rollout impact, freeze path, health gates, and
    rollback; wait for fresh `automatic-promotion` approval. Merge and observe
    one natural Image Updater plus Argo rollout cycle. A mixed alias commit is
    a STOP and must be refrozen.
  - Only after automatic delivery is accepted and the legacy writer has no
    queued/running work, present its exact identity, disable mechanism,
    verification, and recovery implications. Wait for fresh
    `writer-retirement` approval, disable only that writer through its
    supported API/UI, and prove it is disabled and idle.
  - Re-run runtime acceptance and prove no CI job, Jenkins job, operator, or
    person-owned process besides Argo CD/Image Updater retains a write path to
    the adopted Deployments.

[validate]
  - Source-branch activation is an observed no-op at the accepted commit before
    image promotion is changed. Automatic promotion is accepted before writer
    retirement. Both aliases deploy one commit coherently and remain healthy.
  - The former writer is disabled/idle and cannot mutate the adopted cohort.
    It is never re-enabled to bypass GitOps.
  - Merge, natural reconciliation, short health, and any longer soak are
    reported separately.

[output]
  - One GitOps/Image Updater writer, a retired legacy writer, and a complete
    evidence/rollback record.

## Recovery rules

- Before passive ownership, revert only the separately approved stage: source
  MR, permission MR, artifact alias, or image reference. A runtime rollback is
  another production rollout and receives the same care.
- During passive ownership, keep the fixed accepted SHA and repair desired
  state through Git. Never delete the Application while its finalizer/prune can
  delete adopted objects; abandoning ownership requires a reviewed orphaning
  sequence.
- After source-branch activation or automatic promotion, an urgent rollback
  re-pins `spec.source.targetRevision` to the last healthy source SHA and
  freezes both aliases to the last healthy coherent image SHA by reverting the
  exact activation MR(s). Do not leave source on the moving branch while only
  freezing images, and do not restore an untracked legacy writer as a shortcut.
- Excluded state, Secrets, DNS, and independently owned dependencies remain
  outside every rollback write set.
