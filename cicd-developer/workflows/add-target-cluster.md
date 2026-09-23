---
name: add-target-cluster
description: 将已有应用以 parallel-run 方式扩展到新集群。支持 GitOps 源或健康的 legacy-live 源，冻结源基线，新增 target overlay/CI/Application，先被动验收，再用独立 MR 切流。
---

# Workflow: add-target-cluster

## Purpose

Move an existing application to another cluster without changing the source
runtime during target preparation. This workflow covers application manifests,
target Harbor CI, Vault/ESO contracts, ArgoCD registration, passive validation,
and the handoff to a separate traffic-switch MR. 本文的主运行态 Application 不代表一仓只能有一个 Application。若权限职责或生命周期需要分离，先提出独立 runtime/infra 渲染与唯一资源管理者方案，再为各 Application登记已有且批准的 Project、source/path/destination 合同；不能隐式多生成一个 Application。平台 claim 可按现有合同留 runtime，owner 专属 Project 未获批准前不可使用。存量资源拆分需独立 ownership/prune/finalizer/回滚评审，不能套用只改 Project 的迁移。详见 [权限与部署划分合同](../references/data/permission-boundaries.yaml)。

This is a parallel-run workflow. It does not decommission the source workload,
change the source Service selector, or switch traffic in the same change set.
It has two source modes: `gitops` preserves the existing source-overlay flow;
`legacy-live` uses a separately frozen live source plus a same-application,
different-region GitOps overlay as a structural reference. It is not a shortcut
through same-cluster kubectl adoption.

For compatibility with existing migration records, these are also named
**GitOps mode** and **Legacy mode**. Legacy mode keeps the read-only live-source
capture separate from the different-region structural reference.

For `legacy-live`, this routed workflow remains the entry point, but the
staging-only procedure is maintained in
[`workflows/supplements/legacy-live-target-migration.md`](supplements/legacy-live-target-migration.md#legacy-live-entry-conditions).
Read that document before Step 1 and apply its matching step supplement before
the shared action in every step below. Missing or conflicting evidence is a
STOP; the stricter requirement wins.

At the routing boundary, `legacy-live` means a healthy stateless source and a
target-empty destination. Historical Jenkins YAML is never target source, a
US-only guard cannot prove writer isolation, target preparation stays at zero replicas,
and handoff follows `stop-source-before-start-target` ordering.

## Entry conditions

- The working directory is the application repository.
- The migration contract records `source_ownership_mode: gitops | legacy-live`;
  select exactly one value as `$source_ownership_mode`.
- `legacy-live` is staging-only: `$env_keyword` must resolve to
  `env: staging`, `$source_branch=staging`, `$target_branch=staging`, and both
  application branch proofs must show protected `staging`. If a legacy-live
  request or live writer targets prod, STOP before every application, evidence,
  CI, or platform write. Production remains available only to `gitops` mode,
  where the existing `prod_self_check` applies.
- Source and target clusters are explicitly named and are different.
- The target cluster exists in `references/data/clusters.yaml`.
- The migration owner has selected one exact source Git revision and image
  digest as the target baseline.
- The target namespace is explicit. New business Applications use the
  `{phase}-{app}` namespace pattern unless a committed exception exists.
- The source cluster remains read-only until the dedicated cutover step.
- A staging target uses the application repository's approved protected
  `staging` branch. Its Argo CD Application keeps `targetRevision: staging`;
  an immutable image digest/SHA does not replace the branch with an immutable
  source revision.
- `gitops` mode requires a live source ArgoCD Application and source overlay.
- `legacy-live` mode must satisfy every
  [legacy-live entry condition](supplements/legacy-live-target-migration.md#legacy-live-entry-conditions)
  before any application write. The companion contract is normative, not
  optional background material.
- A same-cluster unowned workload remains an adoption request and must use
  `workflows/adopt-kubectl-workload-into-argocd.md`. Never chain the workflows.
- If both legacy-live cross-cluster migration and same-cluster adoption match,
  resolve the exact legacy `mode_keywords`, apply the explicit
  `route_conflicts` record in `routes-build.yaml`, STOP, and ask its one focused
  question. Do not treat the two routes as additive; unrelated selected pairs
  remain additive.

Missing any condition means STOP and add the missing evidence to Ops Todo.

## Step 1. Resolve the migration contract

[precondition]
  - Entry conditions are satisfied.

[action]
  - For `legacy-live`, first apply
    [Legacy-live Step 1](supplements/legacy-live-target-migration.md#legacy-live-step-1-resolve-the-migration-contract).
  - Read `docs/deployment/cd-requirements.md`. If it is missing, run
    `workflows/interview-cd-requirements.md` first and add a migration target
    section; do not infer legacy runtime details from manifests alone.
  - Resolve `$runtime_profile` from that document. For a legacy document with
    no profile, set `service` only when the source build/runtime evidence is an
    unambiguous long-running service; a possible static-web build with no
    production start command is a hard STOP until the profile is explicit.
  - Resolve and record:
    - `$source_ownership_mode`
    - `$app`
    - `$source_cluster`, `$source_context`, `$source_namespace`
    - `$source_branch`
    - `$env_keyword`
    - `$target_cluster`, `$target_context`, `$target_namespace`
    - `$target_branch`
    - `$traffic_entry` and `$rollback_upstream`
  - Resolve the mode-specific fields without substituting one evidence source
    for another:
    - `gitops`: require `$source_application` and `$source_overlay`; preserve
      those historical recipe slot names, set `$source_live_controller`,
      `$source_owner_absence_evidence`, and every legacy-writer/reference-overlay
      field (including reference provenance, status, and
      `$target_controller_kind`) to literal `n/a`. Set every legacy-only gate
      status (`$legacy_live_source_status`, `$legacy_writer_isolation_status`,
      `$dormant_target_status`, `$legacy_writer_fence_status`,
      `$legacy_handoff_status`, and `$legacy_rollback_status`) to literal `n/a`;
      set `$legacy_handoff_evidence_path` to literal `n/a` and do not create a
      legacy evidence artifact;
      set `$gitops_source_status` to `TODO` until Step 2 freezes it.
      `$target_empty_evidence` may remain `TODO` until Step 2 if it is not
      already frozen.
  - Verify `$env_keyword` in `references/data/env-keywords.yaml` and both
    clusters in `references/data/clusters.yaml`.
  - For a staging target, require `$target_branch=staging` and fresh read-only
    proof that the application branch is protected. Record that evidence and
    keep the target Application on the branch; do not substitute a commit SHA
    for `targetRevision`.
  - For a prod target in `gitops` mode only, run every item in
    `references/cost-tiering/_global.yaml -> prod_self_check` and require
    explicit confirmation before continuing.
  - For a staging migration, reject any prod namespace, prod branch, prod
    Vault path, prod image path, or prod traffic route in the target contract.
  - Render `recipes/docs/target-cluster-migration.md` to
    `docs/deployment/target-cluster-{$target_cluster}.md` with the resolved
    source, reference, writer, and target fields. Gate and dependency rows may
    remain `TODO`, but no mode-required field may be empty. Use literal `n/a`
    only for fields declared inapplicable to the selected mode.
  - Fill the runtime/build contract from `cd-requirements.md`. A service
    profile writes `n/a` for every static-web-only field. A static-web profile
    first adds or updates the target row in `cd-requirements.md` as the source
    of truth for build script, output directory, and reviewed public bundle
    config source. Copy that contract into the migration document together
    with SPA decision, exact Node/Nginx versions, port, health path,
    target-specific Nginx config path, and target Dockerfile path. Mark the
    migration snapshot's static target fields `TODO` until Step 6 reruns every
    fail-closed gate.

[validate]
  - Source and target clusters differ.
  - Every cluster/env/branch/namespace field is non-empty.
  - `gitops` has both `$source_application` and `$source_overlay`.
  - `legacy-live` passes every Step 1 validation in the linked companion and
    has not fabricated a source Application or source overlay.
  - New target namespace passes `check_namespace_pattern.py` semantics.
  - A staging-only contract contains no action against a prod target.
  - A staging target branch is exactly the protected application `staging`
    branch and the evidence YAML passes its closed-schema validator.
  - The migration document has a non-empty runtime/build contract; static-web
    target evidence is either `TODO` before Step 6 or `VERIFIED` afterward.

[output]
  - `docs/deployment/target-cluster-{$target_cluster}.md`
  - `$legacy_handoff_evidence_path` for `legacy-live`.
  - In-memory source/target variables.

## Step 2. Freeze source and reference evidence read-only

[precondition]
  - Step 1 is complete.

[action]
  - For `legacy-live`, first apply
    [Legacy-live Step 2](supplements/legacy-live-target-migration.md#legacy-live-step-2-freeze-source-reference-and-empty-target-evidence).
  - In both modes, use commands with both exact context and namespace to read
    the source Rollout/Deployment, Pods, Service, EndpointSlice,
    ExternalSecret, ConfigMap, Ingress, and current image IDs. Record every
    serving controller selected by the Service, image tag and immutable digest
    for every Ready backend, probes and ports, placement, source exposure,
    current traffic entry, desired/ready replicas, Pod UIDs/restarts, and
    secret key names without values.
  - If one Service selects multiple controllers or image digests, STOP until
    the migration owner selects one exact baseline. Record the drift; do not
    patch, scale, or delete the source controller in this workflow.
  - In `gitops` mode:
    - read the source Application and record its source repo, branch, path, and
      revision;
    - confirm `$source_branch` points at the selected Git revision;
    - before editing, render `$source_overlay` with `kubectl kustomize` into a
      temporary file and keep its SHA-256. The temporary file is validation
      evidence and must not be committed.
  - Set `$gitops_source_status=FROZEN` after the GitOps source proof, or set
    `$legacy_live_source_status=FROZEN` and `$reference_status=FROZEN` after the
    legacy-live source/reference proof. Preserve the other mode's status slots
    as literal `n/a`.

[validate]
  - One target baseline Git SHA and one expected source digest are frozen.
  - Source commands contain exact context and namespace.
  - No source write command was run.
  - `gitops` has a frozen source Application and source render digest.
  - `legacy-live` passes every Step 2 validation in the linked companion.
  - No legacy writer can mutate the target tuple. Target resource ownership is
    empty or, for `gitops` only, explicitly approved.

[output]
  - Frozen live source baseline and mode-specific source/reference render
    digest in the migration document.
  - Exact target-empty or approved-ownership evidence plus target-writer
    absence evidence.

## Step 3. Resolve target platform facts and overlay identity

[precondition]
  - Step 2 is complete.

[action]
  - For `legacy-live`, first apply
    [Legacy-live Step 3](supplements/legacy-live-target-migration.md#legacy-live-step-3-resolve-target-platform-facts-and-overlay-identity).
  - From `references/data/clusters.yaml`, resolve target account, region,
    Harbor URL, runner tags, build mode, Vault ClusterSecretStore, and
    argocd-apps directory.
  - Set `$target_image` to
    `{$target.harbor_url}/cicd/{$target.env}-{$target.region}/{$app}`.
  - If the application has multiple business component images, set the
    authoritative kebab-case owner to `$app` in `metadata.labels.app` on both
    the target overlay Kustomization and target Application. Every component
    image path must end in either `/$app` or `/$app-<component>`; do not infer
    ownership from image order or a common prefix.
  - Keep `$env_keyword` as the runtime `env` label and image path segment.
  - In `gitops` mode, if the source Application already consumes
    `k8s/overlays/{$env_keyword}`, set `$target_overlay_id` to
    `{$env_keyword}-new` for the parallel run. Never repoint or overwrite the
    live source overlay during target preparation.
  - If `k8s/overlays/{$target_overlay_id}` already exists, STOP until its
    cluster ownership is proven. Do not merge two target meanings into one
    directory.
  - Confirm `$target_namespace` uses `{phase}-{app}`. Shared legacy namespaces
    need a committed exception and an ownership collision audit.

[validate]
  - `$target_image` matches `/cicd/(dev|staging|pre|prod)-[a-z]+/{$app}$`.
  - `$target_overlay_id` differs from the mode's structural overlay ID.
  - In `gitops`, the source Application path remains unchanged.
  - In `legacy-live`, the linked Step 3 validation passes.

[output]
  - Resolved target facts and `$target_overlay_id`.

## Step 4. Close dependency, network, and Vault gates

[precondition]
  - Step 3 is complete.

[action]
  - For `legacy-live`, first apply
    [Legacy-live Step 4](supplements/legacy-live-target-migration.md#legacy-live-step-4-close-dependency-network-and-vault-gates).
  - Inventory the mode's structural overlay (`$source_overlay` for `gitops`,
    `$reference_overlay` for `legacy-live`) and the live source runtime
    configuration. Keep those inventories distinct. Classify every structural
    item as `portable`, `target-specific`, `source-only`, or `unresolved`. For
    every proposed reference-to-target delta, record both that structural class
    and exactly one allowed delta category:
    - ConfigMap and environment endpoints
    - ExternalSecret stores, remoteRef paths, and expected key names
    - database/cache/queue endpoints
    - third-party HTTPS dependencies
    - account-specific ARN, security group, IAM, IRSA, certificate, and S3 refs
    - Ingress, LoadBalancer Service, and traffic-controller resources
  - Put the structural class, allowed delta category, exact target value, and
    evidence into the migration document.
  - Resolve all Vault paths through `references/vault-paths/resolver.md`. A
    missing resolver match is a hard STOP.
  - Register the app in the correct `gitlab-vault-sync` domain/region. After
    merge, wait for a successful reconcile that names the app.
  - Verify target path existence and expected key names without printing
    values. Do not copy a Kubernetes Secret object between clusters.
  - For platform-owned credentials, record the owning controller or platform
    operator. Registry membership alone does not provision those credentials.
  - For workloads calling external services, require private-subnet scheduling
    and stable NAT EIPs. Test DNS, dependency TCP ports, and external egress
    from every eligible target subnet/AZ. Cross-check observed IPs against the
    recorded NAT gateways; never allowlist dynamic node public IPs.
  - Any `unresolved` item is a hard STOP before target manifests are committed.

[validate]
  - No dependency row remains `unresolved`.
  - No unclassified mode-specific structural-to-target delta remains.
  - Vault registry reconcile succeeded and path/key ownership is recorded.
  - Every eligible target subnet/AZ passed the required network checks.
  - No public data store or `0.0.0.0/0` internal ingress is introduced.

[output]
  - Completed dependency and gate tables in the migration document.

## Step 5. Create the parallel target overlay

[precondition]
  - Step 4 is complete.
  - The mode-specific source/reference render captured in Step 2 is available.

[action]
  - For `legacy-live`, first apply
    [Legacy-live Step 5](supplements/legacy-live-target-migration.md#legacy-live-step-5-create-the-dormant-target-overlay).
  - Select the structural recipe by mode:
    - `gitops`: use the existing `$source_overlay` and mechanically copy it to
      `k8s/overlays/{$target_overlay_id}`.
    - `legacy-live`: use the exact structural recipe selected by the linked
      Step 5 supplement.
  - Do not invent a new workload resource. New resource kinds still require a
    bundled recipe or an Ops Todo.
  - Apply only approved target deltas using structured YAML edits:
    - target namespace
    - target Harbor `images[].newName`
    - runtime `env` label remains `$env_keyword`
    - target ClusterSecretStore and resolved Vault remoteRef keys
    - approved target dependency endpoints
    - target scheduling labels/tolerations required for NAT egress
  - Remove source-only public Ingress, LoadBalancer, source account ARN, and
    source traffic resources from the target overlay.
  - In `gitops`, if a source-only resource is included by
    `k8s/base/kustomization.yaml`, refactor it into the source overlay while
    preserving the rendered source output exactly. Do not silently carry a
    public Ingress into the target.
  - Render the mode's structural overlay again and compare it byte-for-byte
    with the Step 2 render. A difference means STOP unless, in `gitops`, it is
    an independently approved source-staging change.
  - Render the target overlay once to a bounded temporary multi-document YAML
    file and inspect all resources and image hosts.

[validate]
  - `kubectl kustomize k8s/overlays/{$target_overlay_id}` exits 0.
  - `bash "$skill_root/validators/validate.sh" k8s/overlays/{$target_overlay_id}` passes.
  - The selected source/reference render before/after is identical.
  - Target render has the target namespace, `$env_keyword` pod label, target
    Harbor path, target Vault store, exact dormant annotation, and no Ingress,
    Endpoints, or EndpointSlice.
  - In `legacy-live`, every linked Step 5 validation passes on the exact
    rendered file.
  - A Service, when present, is core `v1`, has absent/`ClusterIP` type, has no
    `ExternalName`, `externalIPs`, `loadBalancerClass`, or `nodePort`, and its
    selector has exact `app: $app`. Selectorless and cross-app Services are a
    STOP. PushSecret and every unclassified support resource with possible
    traffic/runtime activation semantics are a STOP.
  - Target files contain no source account image host, prod action, or prod
    Vault path for a staging migration.

[output]
  - `k8s/overlays/{$target_overlay_id}/**`
  - In `gitops` only, any semantics-preserving source/base refactor required to
    isolate exposure.

## Step 6. Add target-only image CI

[precondition]
  - Step 5 passes.

[action]
  - For `legacy-live`, first apply
    [Legacy-live Step 6](supplements/legacy-live-target-migration.md#legacy-live-step-6-add-target-only-image-ci-and-the-central-lock).
  - Preserve every source build job. Source and target builds must coexist
    until rollback closes.
  - In `gitops`, treat the existing source build job as the CI structural
    recipe. Copy it to a unique target job and change only the target key,
    runner tags, target Harbor image path, and approved branch rule.
  - If the repository uses the bundled shell CI recipes, append the matching
    `gitlab-ci-target-dual-arch.yml.tmpl` or
    `gitlab-ci-target-single-arch.yml.tmpl` instead.
  - Resolve `$target_dockerfile_path` before rendering either bundled snippet:
    - `service` profile: preserve the source build job's effective Dockerfile
      path. If the source bundled job omits `DOCKERFILE`, preserve its legacy
      default as `Dockerfile`.
    - `static-web` profile: do not reuse the source target's packaged bundle or
      environment-specific Dockerfile. Re-run the fail-closed static-web
      lockfile, exact script/output, production-semantics, public config, Docker-context,
      pinned-image, port, health, SPA, and no-secret gates from
      `new-service.md`. Set
      `$target_nginx_conf_path=nginx.{$target_overlay_id}.conf`, render that
      file from `recipes/ci/nginx-static.txt` with the exact target port,
      health path, and SPA/non-SPA fallback, then render a dedicated
      `Dockerfile.{$target_overlay_id}` with the verified exact Node/Nginx
      versions, build script, output directory, port, and
      `{{nginx_conf_path}}=$target_nginx_conf_path`; use that Dockerfile.
      Do not overwrite a source Nginx config or make the source build consume
      the target config.
      If the repository uses bundled CI recipes, also append
      `gitlab-ci-static-web-verify.yml.tmpl` for this target before the image
      snippet. Otherwise merge an equivalent target-specific MR/branch-push
      gate that runs exact `npm ci` plus the frozen target build script on the
      audited credential-free verification runner, then asserts the frozen
      output directory exists and is non-empty. Never run MR-controlled npm
      scripts on a target image-build runner that mounts Harbor push
      credentials. Preserve all existing tests.
  - For static-web, update the migration document's runtime/build table with
    the exact verified target values and mark every target static field
    `VERIFIED`. The authoritative free-form bundle config evidence remains in
    the target row of `cd-requirements.md`; the migration document is its
    frozen verification snapshot. Neither value is interpolated into Nginx or
    Dockerfile templates.
  - Fill `{{dockerfile_path}}=$target_dockerfile_path` together with the
    existing target key, image path, runner tag, and branch slots. Do not leave
    any `{{...}}` slot in the appended CI.
  - CI may build/push images and run credential-free local Kustomize validation.
    It must not contact a Kubernetes API, deploy, run Helm, invoke ArgoCD sync,
    or make traffic changes.
  - Tag target images with the pure Git SHA expected by Image Updater.

[validate]
  - CI YAML parses.
  - Source build job remains present and unchanged in behavior.
  - Target job uses target runner tags and `$target_image`.
  - Every target build job's effective `DOCKERFILE` equals
    `$target_dockerfile_path`; a legacy service resolves to `Dockerfile`, while
    static-web uses only its dedicated target file.
  - For static-web, `$target_nginx_conf_path` exists, contains the exact listen
    port and health location, has the confirmed SPA/non-SPA fallback, and the
    target Dockerfile copies that exact path. The migration document records
    the same values as `VERIFIED`, and its script/output/bundle-config snapshot
    exactly matches the target row in `cd-requirements.md`.
  - For static-web, production semantics are proven from the exact build script
    and tracked config; Vite effective `NODE_ENV=production` and no
    `--mode=development` / `NODE_ENV=development` override exists. The target
    CI runs exact `npm ci` plus that build script for MR and target-branch push
    on the audited credential-free verification runner, fails closed if common
    credential mounts/env are present, and proves the output directory is
    non-empty.
  - Appended CI contains no unresolved `{{...}}` slot.
  - CI contains no deployment command.
  - In `legacy-live`, every linked Step 6 validation passes, including the
    protected-`staging` central state/evidence gate and writer isolation.
  - App repository validators and tests pass.

[output]
  - Updated CI configuration in the app repository.
  - For static-web, `nginx.{$target_overlay_id}.conf` and
    `Dockerfile.{$target_overlay_id}`, plus the target static-web verify job.

## Step 7. Merge the app MR and prove the target artifact

[precondition]
  - Steps 1-6 are committed in an app repository MR.

[action]
  - For `legacy-live`, first apply
    [Legacy-live Step 7](supplements/legacy-live-target-migration.md#legacy-live-step-7-merge-the-app-mr-and-prove-the-target-artifact).
  - Complete the reviewable app MR and validation first. Merge it before creating a
    deployable target Application only when the current user authorization covers
    this exact merge and repository approval gates pass. Reuse existing in-scope
    authorization; do not ask again. If only configuration preparation was authorized,
    return the concrete MR and pending artifact/Application handoff at this gate.
  - Run the target branch pipeline and require the target build job to succeed.
  - Verify the target Harbor contains an immutable SHA tag. Record its digest
    and provenance to the frozen source revision.
  - If rebuilding does not preserve the source digest, record the new target
    digest and CI provenance. Never accept a mutable environment tag as proof.

[validate]
  - Target image tag matches `^[a-f0-9]{7,40}$`.
  - Target image is pullable for every required architecture.
  - Migration document contains target tag, digest, and pipeline URL.
  - In `legacy-live`, every linked Step 7 validation passes.

[output]
  - Immutable target image evidence.

## Step 8. Create the target ArgoCD Application

[precondition]
  - Step 7 is complete.
  - Target Vault paths and target artifact gates are closed.
  - The exact target cluster has an approved existing AppProject whose current
    repo/path/revision/destination/resource contract covers the final render, including
    hooks, and whose namespace prerequisite is ready. Read the target AppProject and
    argocd-apps boundary lint; never infer permission from the same name in another
    cluster. Missing permission is an Ops Todo for a separate platform permission MR
    that must merge and sync before consumer registration.

[action]
  - For `legacy-live`, first apply
    [Legacy-live Step 8](supplements/legacy-live-target-migration.md#legacy-live-step-8-create-the-dormant-target-argocd-application).
  - Use a dedicated argocd-apps worktree based on current main.
  - Render `recipes/argocd/application.yaml.tmpl` into the target cluster's
    `argocd_apps_dir` with:
    - Application name `{$app}-{$env_keyword}`
    - the approved existing target project; do not default to `default`, create an
      AppProject, or regress an existing `app-runtime` identity to `default`
    - source path `k8s/overlays/{$target_overlay_id}`
    - protected application `staging` branch as `targetRevision` for staging,
      and the dedicated target namespace; never replace this branch with an
      immutable source SHA
    - the real SHA recovery seed from Step 7
    - target build platforms
    - `metadata.labels.app: {$app}` as the authoritative owner; for a
      multi-image Application, include every component alias and require every
      image path to end in `/$app` or `/$app-<component>`
  - In `gitops`, include the target Harbor image-list and
    `write-back-method=argocd` unless the argocd-apps repository boundary lint
    classifies this Application as a dormant digest-pinned target. Under that
    reviewed lint contract, pin the exact multi-arch digest, omit Image Updater
    annotations, and record the lint authority in the migration document.
  - Do not create an ImageUpdater CR, ApplicationSet, merger policy, Git write
    credential, or `.argocd-source-*` file for the target.
  - Do not add `force-update` unless read-only evidence proves the target
    Application cannot report its workload image through status.summary.images.
  - Application names may match across different ArgoCD control planes. The
    destination cluster directory and source path must still be target-specific.
  - Keep this Application MR separate from the app MR and traffic-switch MR.

[validate]
  - `check_argocd_application.py` passes on the new file.
  - `check_argocd_namespace_creation.py <cluster-dir> --application <changed-app.yaml>`
    and the current argocd-apps boundary/application contract CI checks pass. The skill
    validators do not replace that repository's exact permission contracts.
  - In `legacy-live`, every linked Step 8 validation passes on the exact new
    file and exact target render.
  - Image seed is a real target SHA, not `0000000` or `latest`.
  - Destination namespace and overlay namespace match.
  - `check_namespace_pattern.py` binds both namespaces to the same declared
    multi-image owner and rejects any unrelated component image.
  - Repo URL uses the verified HTTPS credential path.
  - In `gitops`, the Application contains the standard Image Updater annotations
    unless the reviewed dormant-target lint exception applies. In that exception
    and in `legacy-live`, Image Updater annotations are absent and the exact
    digest is the recovery seed. Every mode forbids retired
    `image-writeback.addx.io/*` labels and `git-branch` annotations.
  - No source destination is present. A staging invocation contains no prod
    destination; a prod `gitops` invocation contains only the exact target
    approved by `prod_self_check`. `legacy-live` can never reach this step for
    prod.
  - A staging Application has exact `targetRevision: staging`; the immutable
    target image/recovery seed does not alter the source branch contract.

[output]
  - Target argocd-apps MR.

## Step 9. Passive target validation

[precondition]
  - Target Application MR is merged and ArgoCD reports Synced/Healthy.
  - Existing traffic still uses `$rollback_upstream`.

[action]
  - For `legacy-live`, first apply
    [Legacy-live Step 9](supplements/legacy-live-target-migration.md#legacy-live-step-9-passive-target-validation).
  - In `gitops`, preserve the existing parallel-run gate: verify Rollout/Pod
    readiness, Service endpoints, ExternalSecret Ready, image digest, logs, and
    resource usage; test health, dependency operations, third-party calls, and
    application-specific smoke flows directly against the target Service; and
    observe for the agreed passive window without changing public traffic.
  - Confirm logs use the expected app/env labels when a mode-authorized runtime
    exists; zero logs are expected from a dormant `legacy-live` target.
  - A build-time or runtime failure enters the matching troubleshooting
    playbook in a separate session; do not mix troubleshooting into this build
    workflow.

[validate]
  - All mode-applicable passive checks pass and are recorded.
  - Source endpoints and source workload remain unchanged.
  - In `legacy-live`, every linked Step 9 validation passes.

[output]
  - Passive validation evidence and go/no-go decision.

## Step 10. Perform the separately authorized handoff

[precondition]
  - Step 9 is a documented GO.

[action]
  - For `legacy-live`, apply
    [Legacy-live Step 10](supplements/legacy-live-target-migration.md#legacy-live-step-10-perform-the-separately-authorized-handoff)
    instead of the GitOps traffic handoff below.
  - In `gitops`, preserve the existing traffic handoff:
    - create one traffic-only MR in the owning gateway/router repository;
    - change only the staging route/upstream to the target Service FQDN;
    - record the exact source upstream as the rollback value;
    - validate route syntax and target reachability before merge;
    - after merge, monitor errors, latency, authentication flows, and logs for
      the agreed soak window;
    - roll back by reverting the traffic MR. Do not scale down or delete the
      source workload during the rollback window.

[validate]
  - In `gitops`, the traffic MR contains no workload, Vault, or prod changes;
    rollback is one revert to `$rollback_upstream`; and soak criteria pass
    before source retirement is proposed.
  - In `legacy-live`, every linked Step 10 validation passes and each runtime
    mutation has its own fresh scoped authorization.

[output]
  - Mode-appropriate separate activation/traffic MR and validated
    `$legacy_handoff_evidence_path`, whose rows may link to audited GitLab
    issue/MR notes.

## Exit

The user receives:

- App MR with target overlay, migration contract, and parallel target CI
- Immutable target image evidence
- Separate target ArgoCD Application MR
- Passive validation evidence (`legacy-live` remains zero-replica)
- Separate mode-appropriate activation/traffic MR and rollback record
- Ops Todo for source retirement after the rollback window

Source decommission is deliberately outside this workflow.
Target preparation never scales or deletes the source, switches traffic,
retires the writer, or starts a `legacy-live` target. Each such action requires
fresh scoped authorization in the later handoff stage.
