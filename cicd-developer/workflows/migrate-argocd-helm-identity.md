---
name: migrate-argocd-helm-identity
description: Replace an existing Argo CD Application and Helm release identity through an isolated, Git-first parallel migration. Use only when the source workload already exists and the old/new releases can be safely overlapped in different namespaces.
---

# Workflow: migrate-argocd-helm-identity

## Purpose

Replace a live Argo CD Application / Helm release identity without treating a
rename as an in-place mutation. The target release is a separately owned,
parallel GitOps release; the legacy release is retired only after a bounded
health and continuity gate.

This workflow is intentionally narrow. It does not move PVC data, rename a
Kafka consumer group, change Kafka offsets, switch traffic, or repair a
runtime incident. Any of those needs a separate approved design and an Ops
Todo.

## Entry conditions

- Exactly one bounded source unit is selected for the first wave (for example,
  one zone-local consumer), with a known source Application and Helm release.
- Physical namespace isolation is mandatory: old and new releases can overlap
  only in different namespaces on the same destination cluster. A
  same-namespace replacement is out of scope.
- A target AppProject and one explicit Namespace ownership mode are known from
  the exact target cluster directory: the default standalone bootstrap owner,
  or the user-approved target-project Namespace-owner mode. `default` is not a
  bypass.
- The migration owner has defined continuity evidence and an observation
  window. For a Kafka consumer, the existing consumer-group and committed
  offsets remain unchanged in this workflow.
- For a production wave, every item in
  `references/cost-tiering/_global.yaml -> prod_self_check` has explicit
  confirmation, and the temporary parallel capacity and cost for exactly one
  old/new pair are approved before target manifests are written.

Missing evidence, an existing target owner, a required PVC/data handoff, or a
requested Kafka group/offset change is a STOP. Create an Ops Todo with the
resource, reason, required input, and acceptance criteria instead of writing
target manifests.

## Step 1. Freeze the old/new identity and continuity contract

[precondition]
  - Entry conditions are satisfied.
  - The source Application is `Synced` and not already degraded or in a
    rebalance / lag incident.

[action]
  - Record one migration contract before editing Git. It must include:
    - `$source_application`, `$source_project`, `$source_server`,
      `$source_namespace`, `$source_repo`, `$source_revision`, `$source_path`,
      plus the legacy `$source_chart`, `$source_chart_version`, and
      `$source_release` when the existing workload is Helm-owned.
    - `$target_application`, `$target_project`, `$target_server`,
      `$target_namespace`, `$target_repo`, full-SHA `$target_revision`,
      `$target_path`, the required omission of `spec.source.directory`, and
      `$target_workload_identity`.
    - Old and new StatefulSet / Deployment, ServiceAccount, Service, KEDA/HPA,
      ConfigMap, PVC, selector, and Argo tracking identities. Record every
      immutable or stateful continuity dependency, not only the display name.
    - The continuity identity and evidence: for Kafka, topic subscription,
      consumer-group, committed-offset baseline, lag query, partition
      assignment evidence, and error/drop baseline; for another stateful
      system, its equivalent owner-approved signal.
    - For Kafka, the exact checked-in machine-readable Kafka extractor/diff command,
      source and target config paths, source/target render revisions and
      SHA-256 values, and its expected zero-difference result for
      `group_id`, topic/subscription patterns, `auto_offset_reset`, broker/
      authentication selection, and every other consumer-startup field that
      could create a new stream or reset behavior. A prose assertion is not
      continuity evidence.
  - Use Argo CD UI/API or Operations-run read-only commands to capture the
    source Application revision, operation history, workload readiness, Pods,
    autoscaler state, and baseline metrics. Do not patch, restart, scale, sync,
    or delete the source.
  - If either side of the wave is production, run every item in
    `references/cost-tiering/_global.yaml -> prod_self_check` before writing.
    Record explicit confirmation plus the temporary old/new replica requests,
    available capacity, and approved cost for this one bounded overlap.
  - Confirm the target namespace is different from `$source_namespace` and has
    no live workload or Argo tracking owner that the migration does not own.
    Do not reuse a shared namespace merely because resource names differ.

[validate]
  - Every source/target identity field is non-empty and
    `$source_application != $target_application`,
    `$source_release != $target_workload_identity` when a legacy Helm release
    exists, and
    `$source_namespace != $target_namespace`.
  - `$source_server == $target_server`; both are exact matches for the intended
    cluster, and no cross-cluster assumption is used as a namespace workaround.
  - The contract shows that target selectors, StatefulSet/PVC identities, and
    tracking IDs cannot adopt or prune legacy resources.
  - A production contract contains every `prod_self_check` confirmation and
    proves capacity and cost approval for the temporary parallel pair.
  - The baseline contains a timestamped health and continuity signal. A firing
    lag alert, elevated error/drop rate, or unresolved source degradation is a
    STOP.
  - The Kafka extractor/diff is reproducible from the recorded source and
    target inputs and exits 0. Missing a deterministic extractor, a changed
    field, or an unverifiable input revision is a STOP; create an Ops Todo for
    a separately approved Kafka offset/group migration instead.

[output]
  - One reviewed migration contract and baseline for a single wave.
  - An Ops Todo instead of manifests when a stateful handoff, group mutation,
    or ownership conflict is required.

## Step 2. Select and prove exactly one Namespace ownership mode

[precondition]
  - Step 1 is complete.
  - `$target_namespace` is empty of unrelated live owners.

[action]
  - Read the exact target cluster directory's AppProjects and
    `references/data/permission-boundaries.yaml -> argocd_app_projects`.
    Never infer permission from another cluster or a similarly named
    AppProject.
  - Select exactly one mode in the reviewed migration contract. Standalone
    bootstrap is the default. Target-project Namespace ownership is allowed
    only when the user explicitly approved that exact AppProject as the
    long-term owner and accepted the project-wide authority implied by its
    destination rules. Missing bootstrap inputs never imply the alternative
    mode.
  - **Default standalone-bootstrap mode:** keep the consumer project's
    Namespace boundary unchanged. Define a
    separate lower-sync-wave bootstrap Application under the correct long-term
    Namespace-owning project even if the consumer project happens to have broad
    permissions. It must target the exact `$target_server` and
    `$target_namespace`, set `CreateNamespace=true`, and render an explicit
    `Namespace/$target_namespace` with
    `argocd.argoproj.io/sync-options: Prune=false,Delete=false`.
    The bootstrap must declare exactly one AppProject-authorized, secure Git
    `spec.source` (never `spec.sources`) with an explicit `repoURL`, `path`, full 40- or 64-hex
    `targetRevision`, and must omit `spec.source.directory`. Argo CD
    auto-detects plain YAML non-recursively by default; zero-valued
    `directory: {}` and `directory: {recurse: false}` objects are normalized
    away and cause persistent parent drift. Any explicit directory value,
    including `recurse: true`, `include`, `exclude`, Jsonnet, null, or a
    non-mapping value, is unsupported. It is a deliberately narrow
    direct-manifest owner: its declared non-recursive path contains exactly one
    tracked `.yaml`/`.yml` file at the path root, with exactly one protected
    `v1 Namespace/$target_namespace` document. Helm/Kustomize marker files,
    nested files, `+argocd:skip-file-rendering`, templates, Helm, Kustomize,
    Jsonnet, config-management plugins, `ref`, multi-source, generated
    inputs, `spec.sourceHydrator`, top-level `operation`, and extra tracked
    files are unsupported here; do not disguise any of them as a Namespace
    bootstrap.
    The consumer Application must set `CreateNamespace=false`, target the
    pre-created exact namespace, and never use `default` or a broadened
    whitelist to clear `SyncFailed`.
  - **Explicit target-project Namespace-owner mode:** do not create or retain a
    bootstrap Application for this migration. Record a nonempty, finite
    `$permission_projects` set of literal
    `($cluster_dir, $control_namespace, $project_name)` identities whose every
    AppProject and project-wide authority impact the user explicitly approved.
    Wildcards, selectors, and directory/fleet discovery are prohibited; the
    reviewed set itself is the complete bound. A single-project set remains
    valid. For example, a generic approved batch may contain
    `(cluster-alpha, argo-cd, platform-consumer)` and
    `(cluster-beta, argo-cd, platform-consumer)`; these identities are
    illustrative, not a fleet default. First prepare one separate,
    permission-only AppProject MR that adds only the same exact unconstrained
    `{group: '', kind: Namespace}` entry to each member. Record the reviewed
    permission MR and per-project approval evidence. A pre-existing `*/*`
    cluster-resource grant does not replace this explicit permission-only
    entry. The selected wave's singular
    `($cluster_dir, $application_control_namespace, $target_project)` identity
    must be a member of `$permission_projects` and must permit the exact destination,
    source repository, and every rendered namespaced GVK. The target
    Application must target the isolated `$target_namespace`, set
    `CreateNamespace=true`, and remain outside `default`. Its raw render must
    contain no Namespace or other cluster-scoped/unknown GVK; Argo CD's
    destination-namespace sync option is the only Namespace creation
    mechanism.
  - In standalone mode, record the exact candidate paths `$source_application_yaml`,
    `$bootstrap_application_yaml`, and `$target_application_yaml`. The source,
    bootstrap, and later target Applications must share the same Argo CD
    control-plane `metadata.namespace`; their names must remain distinct.
    Check out the bootstrap source at its declared full commit SHA into the Git
    root `$bootstrap_source_root`; its local `origin` must be the same secure
    repository (standard-port SSH/HTTPS equivalence only) and `HEAD` must
    equal that SHA and contain its required tree/blob objects; a partial-clone
    promised object is a STOP rather than a lazy fetch. The gate reads only
    committed Git blobs, not worktree bytes, and performs a disposable fresh
    remote reachability proof. It preserves only a globally configured,
    bare exact standard noninteractive credential-helper name (`store`, `cache`,
    `manager`/`manager-core`, `libsecret`, `osxkeychain`, or `wincred`), never
    URL rewrites or shell helpers. For another private HTTPS auth scheme, use a
    secure SSH source or create an Ops Todo; do not weaken the proof transport.
    Explicitly approve each remote host that proof may contact; an omitted or
    mismatched `--verify-remote-host` is a STOP. Render the committed direct path into
    `$bootstrap_render_dir` outside the checkout; it must contain only that
    protected Namespace output, never a raw source scan. Run the strict
    bootstrap gate before its MR:

    ```bash
    python3 "$skill_root/validators/check_argocd_helm_identity_migration.py" \
      "$cluster_dir" \
      --source-application "$source_application_yaml" \
      --bootstrap-application "$bootstrap_application_yaml" \
      --bootstrap-source-root "$bootstrap_source_root" \
      --bootstrap-render "$bootstrap_render_dir" \
      --verify-remote-host "$bootstrap_git_host"
    ```

  - In target-project Namespace-owner mode, record
    `$source_application_yaml`, `$target_application_yaml`,
    `$target_source_root`, and `$target_render_dir`; the target Application and
    immutable target evidence are mandatory for a **static candidate
    preflight**. Run the command below against the proposed permission-only
    AppProject diff:

    ```bash
    python3 "$skill_root/validators/check_argocd_helm_identity_migration.py" \
      "$cluster_dir" \
      --target-project-namespace-owner \
      --source-application "$source_application_yaml" \
      --target-application "$target_application_yaml" \
      --target-render "$target_render_dir" \
      --target-source-root "$target_source_root" \
      --verify-remote-host "$target_git_host"
    ```

    The flag is mutually exclusive with every `--bootstrap-*` input. Without
    the flag, all three bootstrap inputs remain required. This pre-merge result
    checks candidate syntax and provenance only: it does not prove live
    authority, accept the consumer candidate, or authorize opening its Add MR.
    The complete owner consumer gate must be rerun only after Step 4's
    permission-only merge and live read-only verification.
    Separately inspect the permission MR candidate for every
    `$permission_projects` member and prove its diff adds only that member's
    one exact entry. The consumer validator intentionally checks only the
    selected wave's singular `$target_project`; it is not a batch validator.
  - Run the repository's Application/static checks for the exact permission,
    bootstrap (when selected), and consumer candidate files. Treat a missing
    AppProject, source permission, or validator dependency as a STOP.

[validate]
  - Exactly one CLI/workflow mode is selected. The standalone bootstrap gate
    exits 0 for the exact bootstrap Application, its
    checkout rooted at the declared immutable commit, explicitly approved
    remote host, and exact rendered output.
  - In standalone mode, the bootstrap project permits `/Namespace`, has the exact destination
    server, and has a lower numeric sync-wave than the consumer Application.
  - In standalone mode, the bootstrap source renders the exact protected Namespace; the consumer
    uses `CreateNamespace=false`; no AppProject whitelist expansion or
    `default`-project substitution is present. A normal Kustomize/Helm
    bootstrap is an expected strict-gate failure, not a reason to weaken the
    rule.
  - In standalone mode, the bootstrap source is one Git path permitted by its AppProject's
    `sourceRepos`; both projects permit their exact destinations; and the
    supplied output is rendered rather than raw Helm template input or an
    unrelated resource bundle.
  - In target-project Namespace-owner mode, the proposed exact non-default
    `$permission_projects` set is finite, nonempty, composed only of literal
    cluster/control-namespace/project identities, and explicitly approved;
    every member's candidate statically adds exactly one unconstrained
    `{group: '', kind: Namespace}` entry and no other delta. The selected
    wave's single `$target_project` belongs to that set, the target uses
    `CreateNamespace=true`, source/target destination servers match, namespaces
    differ, and the required target raw-source/render provenance and resource
    policy checks pass. Each owner whitelist/blacklist field is limited to 128
    rules and 4096 cumulative group/kind/name pattern characters; every
    individual pattern must be valid Go `filepath.Match` syntax within the
    256-character limit. These budgets are checked before matching, and any
    malformed or over-limit input fails closed. A missing flag, any bootstrap input, absent target
    evidence, `CreateNamespace=false`, or target render containing Namespace is
    a STOP.

[output]
  - One explicit Namespace-owner contract, static candidate-preflight result,
    and source locations.
  - An Ops Todo when no authorized project can own the exact Namespace.

## Step 3. Prepare and render the new release source without runtime mutation

[precondition]
  - Step 2 passes.
  - The legacy chart/release facts and the proposed target direct-manifest Git
    source are exact and available locally.

[action]
  - Open a source-preparation MR that only adds the target direct raw-YAML
    source and, only in standalone-bootstrap mode, the protected Namespace
    manifest. Target-project Namespace-owner mode must not add or reference a
    Namespace manifest. Do not delete the legacy source, modify the legacy
    Application, or change Kafka/runtime continuity identities.
  - Copy only the required source configuration into the target identity.
    Change release-local component IDs and comments as approved, but retain
    topics, Kafka consumer-group, offset behavior, scaling thresholds,
    resources, node placement, sinks, and secret/service-account references
    unless an independently approved migration says otherwise.
  - Render the exact source form into `$target_render_dir` outside every input
    checkout. The strict gate accepts only one direct raw-YAML Git source:
    exactly `repoURL`, `path`, and full 40- or 64-hex `targetRevision` in one
    `spec.source`; omit `spec.source.directory`. Argo CD auto-detects plain
    YAML non-recursively, while explicit zero-valued directory objects drift
    and non-zero directory options change rendering. `$target_source_root`
    must have `HEAD` at that commit;
    its direct non-recursive path may contain only root-level YAML manifests,
    no Helm/Kustomize marker, no template, and no
    `+argocd:skip-file-rendering`. The committed YAML resource multiset must
    exactly equal `$target_render_dir`; `spec.sourceHydrator` and top-level
    `operation` are forbidden.
    The gate makes a disposable fresh remote proof only to explicitly approved
    `--verify-remote-host` values and reads committed blobs rather than
    worktree bytes; missing partial-clone objects fail rather than triggering
    a promisor fetch. It may use only the standard noninteractive credential
    helpers listed in Step 2; arbitrary helper commands remain disabled.
    The disposable remote proof fetches commit ancestry with
    `--filter=tree:0` so repositories with many large trees remain bounded;
    exact tree/blob provenance is still read from the caller's complete
    checkout with lazy promisor fetches disabled, so this optimization does
    not weaken the immutable-source proof.
  - A normal HTTPS Helm repository source is an expected STOP even when its
    chart `version` is exact: SemVer does not bind immutable chart bytes and
    cannot prove the artifact Argo CD will later render. Do not use
    `--target-values-root`, a Git `ref`, a local Helm render, or a values-file
    checksum to bypass that rule. Separately verified OCI-digest source support
    is future work; create an Ops Todo with the chart digest, renderer contract,
    and acceptance criteria instead of weakening this gate. If the raw target
    YAML was prepared from a Helm chart, review and commit the generated output
    as the target artifact; do not call an arbitrary YAML directory an “exact
    render”.
  - Run the normal manifest validator against the exact raw target render:

    ```bash
    bash "$skill_root/validators/validate.sh" --repo-context k8s "$target_render_dir"
    ```

  - Review rendered group/version/kind/namespace/name, selectors, service
    accounts, PVC templates, KEDA/HPA targets, and Argo ownership. Compare the
    target render with the frozen source render; the target must not reference
    `$source_namespace`, adopt source PVCs, select source Pods, or collide
    with an existing target object.
  - Run the Step 1 Kafka extractor/diff against the frozen source and exact
    target render/config. Commit its machine-readable zero-difference evidence
    with the source-preparation MR. It must compare semantic values rather
    than names/comments and fail on any consumer-group, topic/subscription,
    `auto_offset_reset`, broker/authentication, or equivalent startup-field
    change. A missing parser for an embedded config format is a STOP, not a
    manual review exception.

[validate]
  - The direct raw-YAML source exactly matches the rendered target; raw chart
    templates are never passed to a manifest validator. A normal Helm chart
    repository source fails closed, with an OCI-digest Ops Todo instead.
  - `validate.sh` exits 0 for the rendered target output.
  - The rendered identity review has no source-namespace object, duplicate
    target object, unsafe selector, or unreviewed PVC/state handoff.
  - The source-preparation diff is additive only; the recorded
    machine-readable Kafka diff exits 0 against pinned source/target inputs.

[output]
  - A source-preparation MR with rendered-manifest and ownership evidence.
  - An Ops Todo when rendering, resource ownership, or state continuity cannot
    be proven.

## Step 4. Land and verify the selected Namespace authority

[precondition]
  - The source-preparation MR is merged and its exact revision is available to
    Argo CD.
  - Step 2's static candidate preflight is current for the selected ownership mode.

[action]
  - In standalone-bootstrap mode, open a separate `argocd-apps` bootstrap MR.
    It adds only the lower-wave Namespace Application and no consumer release.
    Merge through normal review and allow App-of-Apps auto-sync to reconcile.
    Operations verifies read-only evidence for its source revision,
    operation/history, `Synced`/`Healthy` status, and
    `Namespace/$target_namespace` phase `Active`, including both
    `Prune=false` and `Delete=false` protections.
  - In target-project Namespace-owner mode, open and merge the independent
    permission-only MR before any consumer MR. For every and only
    `$permission_projects` member, it may add that AppProject's one
    unconstrained `{group: '', kind: Namespace}` whitelist entry; it must not add a
    bootstrap/consumer Application, change destinations/source repositories,
    or change another resource permission. After automatic root
    reconciliation, verify each member independently and read-only: the exact
    live AppProject contains that exact entry, its before/after delta contains
    no other change, and its cluster whitelist/blacklist rules are
    structurally valid with no deny for its declared target namespaces. For
    each member's destination cluster, also prove the permission change alone
    created no declared target Namespace and no workload/consumer resource.
    Record the merged MR/revision and one evidence record per member as input
    to later consumer gates.
  - Do not run `argocd app sync`, `kubectl apply`, or any manual create
    command in either mode.

[validate]
  - In standalone mode, the bootstrap Application is `Synced` and `Healthy` at
    the merged source revision, the exact Namespace is `Active`, and no
    consumer resource was introduced.
  - In target-project Namespace-owner mode, the permission-only MR is live,
    exactly the approved `$permission_projects` members each gained only the
    unconstrained `{group: '', kind: Namespace}` entry, every member passed
    its own live exact-delta and no-runtime-side-effect verification, and no
    declared target Namespace or consumer resource exists yet.
  - A failed reconciliation, unexpected AppProject delta/runtime resource,
    missing protection in standalone mode, or manual-sync attempt is a STOP.

[output]
  - Bootstrap health evidence or permission-only live evidence, sufficient to
    run Step 5's complete gate before beginning one consumer wave.
  - A Git revert or Ops Todo when the selected authority contract is not met.

## Step 5. Add one isolated target release through GitOps

[precondition]
  - Step 4 passes.
  - The source baseline is still healthy and no other identity-migration wave
    is overlapping.

[action]
  - Prepare the `$target_application` candidate locally. It must use the
    one `$target_project` for this wave, exact target server and namespace, and
    one direct raw-YAML Git source at a full immutable commit SHA with exactly
    `repoURL`, `path`, and `targetRevision`; omit `spec.source.directory`.
    In target-project Namespace-owner mode only,
    `($cluster_dir, $application_control_namespace, $target_project)` must
    belong to `$permission_projects`. Standalone mode
    uses a sync-wave after the bootstrap and `CreateNamespace=false`; owner
    mode uses the reviewed wave and `CreateNamespace=true`. It must not use
    `spec.sourceHydrator` or top-level `operation`.
  - Keep `$source_application` unchanged.
  - After Step 4 has proved the selected Namespace authority live—bootstrap
    health in standalone mode, or the merged permission-only MR with no runtime
    side effects in target-project owner mode—run the changed Application's
    static validator and the complete strict exact gate for that mode. Supply
    the direct target checkout rooted at its declared SHA.
    The gate does not accept a target render that it cannot reproduce:

    ```bash
    python3 "$skill_root/validators/check_argocd_helm_identity_migration.py" \
      "$cluster_dir" \
      --source-application "$source_application_yaml" \
      --bootstrap-application "$bootstrap_application_yaml" \
      --target-application "$target_application_yaml" \
      --target-render "$target_render_dir" \
      --target-source-root "$target_source_root" \
      --bootstrap-source-root "$bootstrap_source_root" \
      --bootstrap-render "$bootstrap_render_dir" \
      --verify-remote-host "$bootstrap_git_host" \
      --verify-remote-host "$target_git_host"
    ```

    In target-project Namespace-owner mode, instead run:

    ```bash
    python3 "$skill_root/validators/check_argocd_helm_identity_migration.py" \
      "$cluster_dir" \
      --target-project-namespace-owner \
      --source-application "$source_application_yaml" \
      --target-application "$target_application_yaml" \
      --target-render "$target_render_dir" \
      --target-source-root "$target_source_root" \
      --verify-remote-host "$target_git_host"
    ```

    This post-merge gate must exit 0 against the merged AppProject state and
    retained live verification record before the consumer Add MR is opened.
    It remains bound to this one target Application, AppProject, cluster, and
    namespace; the permission batch never authorizes multiple simultaneous
    consumer waves.
  - Only after that complete gate passes, open one Add MR that adds only
    `$target_application`. Do not put add and legacy retirement in the same MR.

  - A normal Helm repository source, Helm values-ref, or
    `--target-values-root` is rejected even with a pinned version; create an
    OCI-digest Ops Todo instead. Review the rendered target object names
    against the live target namespace before merge. The strict gate rejects
    `Namespace` and all cluster-scoped/unknown GVKs in the target render,
    verifies AppProject resource allow/deny rules, and treats an omitted
    `metadata.namespace` as the target only for a known namespaced resource.
  - Merge through normal Git review. Allow Argo CD auto-sync; never use manual
    sync, `kubectl apply`, `kubectl scale`, restart, or a Helm CLI deployment.

[validate]
  - The Add MR contains one new target Application and no legacy Application
    deletion or consumer-group/offset mutation; its machine-readable Kafka
    continuity evidence still exits 0 against the exact source/target inputs.
  - The new Application reaches `Synced` and `Healthy` at the reviewed source
    revision; `Namespace/$target_namespace` is `Active`; its StatefulSet/Pods
    reach the configured ready replica count.
  - The new ScaledObject/HPA is Ready, points only at the target release, and
    old/new releases remain in different namespaces.

[output]
  - One active, isolated target release plus Add-MR evidence.
  - A Git revert and Ops Todo if the target cannot become healthy.

## Step 6. Run the continuity gate before retiring the legacy release

[precondition]
  - Step 5 passes and both releases are intentionally present for one bounded
    wave.

[action]
  - Collect read-only evidence for both Applications: merged revision,
    operation/history, health, StatefulSet ready/updated replicas, Pod
    readiness/restarts, and KEDA/HPA status.
  - Collect the continuity evidence defined in Step 1 for the full observation
    window. For Kafka, confirm the unchanged consumer-group remains present,
    compare lag against the baseline/trend, inspect bounded rebalance behavior,
    compare Vector/application error and drop metrics with baseline, and retain
    the Step 3 machine-readable source/target continuity diff plus its pinned
    input hashes in the wave record.
  - Keep at most one old/new pair overlapping. Do not advance to another zone
    or region until this wave has a documented pass.
  - Do not delete/reset a Kafka consumer-group, alter offsets, change
    `auto_offset_reset`, or use a new group as a naming shortcut. Do not use
    manual sync or scaling to force a rebalance.

[validate]
  - Both releases are healthy; the target release is consuming; and the
    continuity signal has no sustained regression during the agreed window.
  - Kafka lag is not firing or persistently increasing; partition/consumer
    evidence shows the unchanged group rather than an independent stream, and
    the recorded semantic config diff remains zero for the reviewed inputs.
  - Any degraded Application, missing Ready replica, sustained lag increase,
    unexpected error/drop increase, rebalance churn, or missing evidence is a
    STOP and blocks legacy retirement.

[output]
  - A pass/fail gate record for exactly one migration wave.
  - An Ops Todo when the continuity problem needs a separate runtime or Kafka
    migration design.

## Step 7. Retire the legacy Application in a separate Git change

[precondition]
  - Step 6 has a documented pass.

[action]
  - Open a separate Retire MR deleting only `$source_application` from
    `argocd-apps`. Retain legacy values/source until every wave completes, and
    retain the selected Namespace authority: the protected bootstrap in
    standalone mode or the target-project permission in owner mode.
  - Merge through normal review and let App-of-Apps pruning/finalization remove
    only the legacy release from `$source_namespace`. Do not run a manual
    Argo rollback, sync, `kubectl delete`, or `kubectl scale`.
  - Operations verifies the old Application, StatefulSet, ScaledObject, HPA,
    and Pods are absent; the new release remains the sole active target; and
    continuity/health remains stable for the agreed post-retire window.

[validate]
  - The Retire MR does not remove the target release, its selected Namespace
    authority, or any unrelated Application.
  - Legacy managed resources are absent only after the target was healthy and
    consuming; target health and continuity remain within the Step 6 baseline.
  - An unexpected prune, namespace deletion, target regression, or continued
    legacy ownership is a STOP.

[output]
  - Retire-MR and post-retire evidence for one completed wave.

## Step 8. Stop safely and roll back only through Git

[precondition]
  - A stop condition occurs, or a rollback decision is required.

[action]
  - Before legacy retirement, revert the Add MR; the unchanged source release
    remains available while the target release leaves through GitOps.
  - After legacy retirement, restore the legacy Application in a Git revert or
    restore MR and wait for it to be `Synced`/`Healthy`; keep the target
    Application unchanged while the restored legacy release rejoins the group.
    Before opening a target-retire MR, rerun the full Step 6 continuity gate
    for the restored legacy/target pair, including the full observation window.
    Only a documented pass permits the later target-retire MR.
  - After the target-retire MR merges, run the role-reversed Step 7 post-retire
    observation: the restored legacy release must be the sole active consumer
    and its health, lag, partition/rebalance, error/drop, and ready-replica
    signals must remain stable for the agreed window. Any regression is a STOP
    and leaves the target Application in place until a new Git-reviewed plan
    exists.
  - Never roll back by `kubectl apply`, `kubectl scale`, Pod deletion, manual
    Argo CD sync/rollback, `helm uninstall`, Kafka consumer-group deletion, or
    Kafka offset reset. Do not remove the selected Namespace authority during
    an application rollback.
  - For an unsatisfied precondition, publish an Ops Todo containing: affected
    Application/release/namespace, observed evidence, responsible owner,
    required input or platform capability, acceptance criteria, and the Git
    state that must remain unchanged.

[validate]
  - The rollback/stop record identifies the exact Git MR to revert or restore,
    preserves a healthy serving release, records the full Step 6 pass before
    target retirement, records the role-reversed post-retire observation, and
    leaves the selected Namespace authority intact.
  - No direct production mutation or consumer-group/offset mutation appears in
    the command history or proposed plan.

[output]
  - A Git-only rollback MR or a complete Ops Todo; no unsafe workaround.

## Step 9. Final Cleanup MR after all waves

[precondition]
  - Every migration wave has passed its post-retire observation window and any
    agreed rollback window has ended.

[action]
  - Open one final Cleanup MR that removes only legacy values/source paths.
    Before editing, prove that no live or Git-defined legacy Application still
    references those paths, record the exact recoverable Git refs for the last
    known-good legacy and target states, and retain the selected Namespace authority.
    Removing target-project `/Namespace` authority requires a
    separate post-migration security/ownership review.
  - Merge through normal review and verify read-only that no legacy Application
    or workload identity remains while the final target releases and selected
    Namespace owners remain healthy. Do not use manual deletion to clean up
    source or runtime objects.

[validate]
  - The Cleanup MR contains no target Application, selected Namespace
    authority, Kafka identity, or unrelated resource deletion.
  - The recorded Git refs can restore the last known-good state, and every
    active target release remains healthy after cleanup.

[output]
  - One final Cleanup MR and retained recovery evidence, or an Ops Todo if any
    legacy reference or rollback obligation remains.
