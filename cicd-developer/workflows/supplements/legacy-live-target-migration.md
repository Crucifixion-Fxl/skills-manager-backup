# Legacy-live target migration procedure

This is the normative `legacy-live` supplement to
[`workflows/add-target-cluster.md`](../add-target-cluster.md). The routed workflow keeps the
shared and GitOps procedure; this document supplies the staging-only legacy source gates. Read the
matching section before each main-workflow step. Both documents apply, and the stricter requirement
wins. A missing proof, unavailable control, nonzero validator result, or conflict is a STOP.

## Contents

- [Entry conditions](#legacy-live-entry-conditions)
- [Step 1: migration contract](#legacy-live-step-1-resolve-the-migration-contract)
- [Step 2: source, reference, and target-empty proof](#legacy-live-step-2-freeze-source-reference-and-empty-target-evidence)
- [Step 3: target identity](#legacy-live-step-3-resolve-target-platform-facts-and-overlay-identity)
- [Step 4: dependency and delta gates](#legacy-live-step-4-close-dependency-network-and-vault-gates)
- [Step 5: dormant overlay](#legacy-live-step-5-create-the-dormant-target-overlay)
- [Step 6: image CI and central lock](#legacy-live-step-6-add-target-only-image-ci-and-the-central-lock)
  - [CE-guarded degraded enforcement](#legacy-live-step-6-ce-guarded-degraded-enforcement)
- [Step 7: artifact proof](#legacy-live-step-7-merge-the-app-mr-and-prove-the-target-artifact)
- [Step 8: dormant Application](#legacy-live-step-8-create-the-dormant-target-argocd-application)
- [Step 9: passive validation](#legacy-live-step-9-passive-target-validation)
- [Step 10: handoff and rollback](#legacy-live-step-10-perform-the-separately-authorized-handoff)

## Legacy-live entry conditions

The migration contract records `source_ownership_mode: gitops | legacy-live`; this document applies
only after selecting `legacy-live` exactly.

`legacy-live` is staging-only. `$env_keyword` resolves to `env: staging`, both exact application
branches are protected `staging`, and any prod environment, namespace, branch, writer target, image,
Vault path, traffic route, or resource is a STOP before every application, evidence, CI, or platform
write. Never use a prod self-check to bypass this STOP.

Before any application write, require all of the following:

- Source and target clusters are explicitly named and are different.
- One healthy stateless source workload exists in the exact source context and namespace, with
  read-only proof that it has no Argo CD or Helm owner.
- An empty target with no same-app Application, Helm release, workload, namespace ownership conflict,
  or legacy writer capable of writing the exact target `(environment, app)` tuple is proven.
- The exact legacy Jenkins/kubectl writer identity, trigger, write set, enabled/idle state, exact
  `(environment, app)` guard, fence method, queue-empty proof method, and rollback re-enable policy are
  recorded.
- The application/CI MR is mechanically excluded from the writer across MR source branch, target
  branch, pipeline source, and changed paths. The exact source-tuple guard is defense in depth only;
  it cannot replace trigger exclusion.
- One same-application GitOps overlay whose region/cluster differs from the target is selected only as
  `$reference_overlay`, from a clean checkout at an exact SHA with approved protected-branch or
  merged-MR provenance.
- The protected-branch dormant lock is one controller-owned annotation
  `migrations.addx.io/runtime-state: dormant` plus integer replicas `0`.
- A closed handoff artifact is rendered from
  `recipes/docs/legacy-target-handoff-evidence.yaml.tmpl`.

A same-cluster unowned workload remains an adoption request for
`workflows/adopt-kubectl-workload-into-argocd.md`; never chain these workflows. Resolve the declared
route conflict and ask its focused question if both interpretations match.

## Legacy-live Step 1. Resolve the migration contract

Require `$source_live_controller`, `$source_owner_absence_evidence`, `$target_empty_evidence`,
`$legacy_writer`, `$legacy_writer_trigger`, `$legacy_writer_write_set`, `$legacy_writer_state`,
`$legacy_writer_guard`, `$legacy_writer_fence_method`, `$legacy_writer_queue_empty_evidence`,
`$legacy_writer_reenable_policy`, `$writer_isolation_decision`, `$reference_cluster`,
`$reference_region`, `$reference_overlay`, `$reference_branch`, `$reference_revision`,
`$reference_provenance_evidence`, and `$enforcement_mode`.

Set GitOps-only fields, including `$gitops_source_status`, to literal `n/a`; do not set, require, or
fabricate `$source_application` or `$source_overlay`. Set `$legacy_live_source_status`,
`$legacy_writer_isolation_status`, `$dormant_target_status`, `$legacy_writer_fence_status`,
`$legacy_handoff_status`, `$legacy_rollback_status`, `$reference_render_digest`, `$reference_status`,
and `$target_controller_kind` to `TODO`. Step 2 replaces the source/reference statuses with `FROZEN`,
the exact digest, and the proven `Deployment` or `Rollout` kind. Do not change mode-inapplicable `n/a`
values.

Resolve `$enforcement_mode` to exact `sre-policy` or `ce-guarded` before any evidence or application
write. `sre-policy` is the default and requires the SRE-owned GitLab pipeline execution policy from
Step 6. `ce-guarded` is a degraded GitLab CE enforcement mode: it requires the migration owner's
explicit, closed authorization row `spec.ce_guarded_authorization` in the evidence artifact and in
the app MR description before any write, plus all Step 6 CE-guarded gates. Do not render
`ce-guarded` when the instance already hosts pipeline execution policies.

Set `$legacy_handoff_evidence_path` to one tracked application-repository YAML path and render
`recipes/docs/legacy-target-handoff-evidence.yaml.tmpl`. Initialize its closed event map as `PENDING`
with null evidence fields, `handoff_status: PREPARING`, and `rollback_status: NOT_INVOKED`; freeze the
exact source/target identities and rollback/approved replica counts. Set `environment: staging` and
`target_branch: staging` exactly. The Markdown migration document links this artifact but is never
authoritative.

Derive `$source_cluster_fingerprint` and `$target_cluster_fingerprint` read-only as
`sha256(<canonical API server> + "\n" + <kube-system Namespace UID>)` from each exact context. Record
only `sha256:` plus 64 lowercase hex and require different fingerprints; context names alone do not
prove identity.

Require source and target identities to differ, and require the reference region/cluster to differ
from the target. Prove `$reference_overlay` belongs to the same application repository and renders the
same application/controller contract; never call it the source overlay.

Resolve writer isolation before creating or editing any application file. The exact source branch,
target branch, pipeline source, and changed paths must mechanically exclude the app/CI MR. The tuple
guard is mandatory defense in depth, not an alternative trigger boundary: a US-only guard cannot
satisfy EU isolation. Unknown trigger dimensions or a reachable exact source writer are a STOP before
writes.

Require exact protected `$source_branch=staging` and `$target_branch=staging`, then render the
mode-specific fields in `recipes/docs/target-cluster-migration.md`. Render the evidence recipe with
`enforcement_mode: $enforcement_mode` filled; for `ce-guarded`, uncomment and fill the closed
`ce_guarded_authorization` row in the rendered artifact, then run:

```bash
python3 "$skill_root/validators/check_legacy_target_handoff_evidence.py" \
  --phase bootstrap "$legacy_handoff_evidence_path"
```

A nonzero result is a STOP; Markdown cannot override it.

Validate that every live-source, no-owner, target-empty, writer/isolation, reference, branch, and
fingerprint field is closed, and that no source Application or source overlay was fabricated.

## Legacy-live Step 2. Freeze source, reference, and empty-target evidence

Use exact context/namespace reads from the main workflow to prove health from observed generation,
desired/ready replicas, Ready endpoints, probes, Pod UIDs/restarts, and recent events. Prove
statelessness from the workload contract and live volume/storage inventory. A PVC, StatefulSet,
durable local state, or ambiguous state ownership is a STOP.

Create the read-only live-source capture from Deployment/Rollout, Service, ExternalSecret, and
ConfigMap metadata in a private task evidence directory. There is no source overlay to render; the capture is never committed or used as
the structural reference. Preserve accepted behavior; do not externalize or rotate credentials as part of this workflow. Prove no
Argo CD or Helm owner from tracking metadata, owner references, Argo CD ownership, and Helm records.
Record the exact writer contract and keep the writer enabled and idle.

Set `$reference_origin` to the approved credential-free HTTPS repository identity and
`$reference_branch` to the exact protected application `staging` branch. The validator rejects every
other branch, including `main` and `master`. It uses a fresh object database,
`GIT_CONFIG_NOSYSTEM=1`, isolated HOME/global config, `GIT_NO_REPLACE_OBJECTS=1`,
`protocol.file=never`, no submodules/hooks, a clean detached checkout, protected-branch GitLab API
evidence, and a bounded credential-free render. It may read `GITLAB_TOKEN` only from the environment
and never prints it:

```bash
python3 "$skill_root/validators/check_legacy_reference_provenance.py" \
  --expected-origin "$reference_origin" \
  --protected-branch "$reference_branch" \
  --revision "$reference_revision" \
  --overlay "$reference_overlay" \
  --expected-render-digest compute \
  --provenance ancestor
```

For reviewed alternate lineage, use `--provenance merged-mr` and `--merged-mr-ref` with the exact
stable internal MR URL. Record the first safe PASS digest as `$reference_render_digest`, then rerun
with that exact `sha256:<64hex>` instead of `compute`. Either nonzero result is a STOP. No local remote
alias, replacement ref, global Git config, file transport, dirty checkout, unprotected branch,
mismatched MR head, or prose assertion satisfies provenance.

Require one unambiguous application controller in the reference render and set
`$target_controller_kind` to exact `Deployment` or `Rollout`; any other, missing, or duplicate kind is
a STOP. Keep the source live inventory and reference render as separate evidence sets. Never derive
live facts from the reference render and never copy a historical Jenkins YAML tree into the app repo.

Persist a closed sanitized target writer inventory with `schema_version: 1`, no credentials, and no
raw job output, then run:

```bash
python3 "$skill_root/validators/check_legacy_target_live_empty.py" \
  --context "$target_context" \
  --namespace "$target_namespace" \
  --app "$app" \
  --writer-inventory "$target_writer_inventory"
```

The gate rejects Pods, every built-in or Rollout workload producer, Jobs/CronJobs, HPA/KEDA targets,
known producer CRDs, target-directed writers, and non-empty unclassified namespaced CRDs. A missing
namespace is empty; auth/discovery failure, malformed output, or incomplete producer/CRD inventory is
a STOP. Separately check Application, Helm, namespace ownership, same-app tracking identity, Service,
Ingress, Endpoints, and EndpointSlice. Zero Ready Pods alone is not target-empty proof.

Set `$legacy_live_source_status=FROZEN` and `$reference_status=FROZEN`. Validate the healthy stateless
source, no-owner proof, approved reference provenance/digest/kind, writer/fence record, and exact
target-empty proof. No legacy writer may mutate the target tuple.

## Legacy-live Step 3. Resolve target platform facts and overlay identity

Set `$target_overlay_id` to the exact approved target environment identity. It must differ from
`$reference_overlay`; the reference region/cluster differs from target; and the reference render
selects the same app and controller/resource-kind contract. Keep the reference overlay and live source
unchanged and represented as separate evidence sources.

## Legacy-live Step 4. Close dependency, network, and Vault gates

Keep the live runtime inventory distinct from `$reference_overlay`. In `legacy-live`, every
target-render difference from the frozen reference render has exactly one reviewed row with a structural class, exact target
value/evidence, and one allowed category: namespace/environment label, target Harbor image, Vault/ESO
contract, dependency endpoint, target placement, or classified removal of source-only exposure.

Preserve the reference controller and resource kinds in `legacy-live`; a kind change needs a separately
bundled recipe or is a STOP. Validate that no reference-to-target delta is unclassified before applying
the shared dependency, private-network/NAT, Vault/ESO, and no-public-exposure gates.

## Legacy-live Step 5. Create the dormant target overlay

For `legacy-live`, use the frozen `$reference_overlay` and mechanically copy only that GitOps overlay to
`k8s/overlays/{$target_overlay_id}`. Never use live-object export, Jenkins workspace content, or
historical Jenkins YAML as target source. `recipes/k8s/rollout.yaml.tmpl` is a structured authoring
recipe only when the proven reference kind is already `Rollout`; it never authorizes kind conversion.

In `legacy-live`, preserve the reference render except for the classified target deltas, including controller/resource
kinds; never edit the reference overlay to make the comparison pass. Set the target workload replica
contract to zero and add exact controller annotation `migrations.addx.io/runtime-state: dormant`.
Render both marker and integer replicas `0`. No target Pod may start while source is active. This
dormant digest-pinned target lint contract overrides generic Image Updater defaults until separately
authorized activation. A controller that cannot be deterministically held at that exact two-field
dormant state is a STOP.

Render once to a bounded temporary multi-document YAML file and run:

```bash
python3 "$skill_root/validators/check_dormant_target.py" \
  --app "$app" --namespace "$target_namespace" \
  --expected-kind "$target_controller_kind" \
  --phase dormant \
  "$target_render_file"
```

The validator binds the reference-proven controller and all namespaced support resources to the exact `$target_namespace`;
lexical review is not a substitute. On PASS, set `$dormant_target_status=PASS`.
Validate classified deltas, preserved kinds, zero replicas, and no object capable of starting an app
Pod. A Service must be core `v1`, absent/`ClusterIP`, no `ExternalName`, `externalIPs`,
`loadBalancerClass`, or `nodePort`, with selector exact `app: $app`. Selectorless/cross-app Services,
PushSecret, and unclassified runtime/traffic-capable support resources are a STOP.

## Legacy-live Step 6. Add target-only image CI and the central lock

Use a proven same-application GitLab image-build job or the bundled target CI recipe. Never copy or
invoke Jenkins deployment or a kubectl writer; keep the exact legacy writer unchanged and enabled-idle.

Do not render the state gate into candidate `.gitlab-ci.yml`. Require SRE/Platform to install
`recipes/ci/gitlab-ci-legacy-target-lock.yml.tmpl` as an SRE-owned GitLab pipeline execution policy,
injected into every candidate pipeline and bound to exact overlay, namespace, app, controller kind,
evidence path, protected `staging`, and an approved validation-image repository plus exact lowercase
64-hex SHA-256 digest. Validator root is fixed image-owned
`/opt/addx/cicd-developer/validators`; the image contains Git, kubectl Kustomize, Python, PyYAML, and
the reviewed validators. Never execute candidate/trusted-base Python.

The credential-free `legacy-target-state-gate` runs only for merged-result MRs targeting protected
`staging` and every protected `staging` branch pipeline; MRs to `main`, `master`, or another branch do
not select it. It rejects Harbor/Vault/AWS/kubeconfig credentials. MR execution binds temporary merge
parents to exact trusted target/source SHAs; head-only or stale-base pipelines STOP.

Before candidate or baseline render, the image-owned validator proves a local-only tracked regular-file
Kustomize closure in clean detached worktrees under isolated Git/HOME, never `$CI_PROJECT_DIR`.
URL/git dependencies, symlinks, exec/plugins, Helm, and unclassified dependency fields STOP. Keep the
root-only load restrictor and never enable Helm/plugins. The first target commit passes `--phase
dormant`; evidence separately passes `--phase bootstrap` with `PREPARING / NOT_INVOKED` and all rows
`PENDING`; independent CODEOWNER review bootstraps the lock.

Verify the policy is enabled and candidate maintainers cannot modify, shadow, remove, or skip it. If
unavailable, STOP and emit an Ops Todo naming project, policy owner, job, digest, and acceptance proof;
candidate CI is not a fallback. The job uses `.pipeline-policy-pre`,
`inherit: {default: false, variables: false}`, empty before/after scripts and image entrypoint, fixed
absolute binaries/PATH, `python -I`, and unsets Python/shell/Git credentials plus `CI_JOB_TOKEN`. It
executes no candidate scripts, hooks, filters, generators, or plugins.

Verify protected `staging` requires MRs and this gate, blocks direct/skip-pipeline merges, and requires
independent migration-owner CODEOWNER approval for CI, validation image, evidence, and target overlay.
The author cannot self-approve or override controls; unknown settings/ownership are a STOP with an Ops
Todo.

### Legacy-live Step 6 CE-guarded degraded enforcement

`$enforcement_mode=ce-guarded` applies only on a GitLab CE instance that cannot host the SRE-owned
pipeline execution policy. It is a documented degraded fallback, never a silent substitution. Before
any application, evidence, or CI write in `ce-guarded`, require all of the following:

- Fresh read-only GitLab instance evidence proves the policy control plane is unavailable: exact
  `/api/v4/version` output with `enterprise: false`, and no security-policy /
  pipeline-execution-policy surface in the GraphQL schema or REST API. Record the observation and
  timestamp in the migration record; stale or unrecorded evidence is a STOP.
- The migration owner authorized degraded enforcement in the closed
  `spec.ce_guarded_authorization` row of the rendered evidence artifact (stable reference, canonical
  UTC, bounded actor, sanitized state, `status: VERIFIED`) and states `enforcement_mode: ce-guarded`
  in the app MR description. The evidence validator rejects a `ce-guarded` document without that
  closed row and rejects `sre-policy` documents carrying one. The authorization row and
  `enforcement_mode` are immutable for the whole handoff.
- Every CE-enforced control below is proven before the first write; missing proof is the Step 6 STOP
  plus an Ops Todo, and `ce-guarded` cannot bypass it.

CE-enforced minimum controls (all must be proven before any write):

- Protected `staging` sets push access to `No one` (every change goes through an MR), force push
  disabled, merge access restricted to Maintainers, and
  `only_allow_merge_if_pipeline_succeeds: true`. Direct or skip-pipeline merges are impossible.
- Candidate `.gitlab-ci.yml` renders the mandatory `legacy-target-state-gate` job from
  `recipes/ci/gitlab-ci-legacy-target-state-gate.yml.tmpl`: `stage: .pre`, the same digest-pinned
  credential-free validation image, the same exact validators, the same isolated detached worktree
  closure, and the same environment hardening as the SRE-owned lock job. It runs for every
  protected-`staging` MR and branch pipeline. It never receives Harbor, Vault, AWS, kubeconfig, or
  repository-write credentials, and never executes candidate or trusted-base Python.
- Evidence-only and activation MRs keep the exact Step 6 split: at most one pending row progresses
  per MR, target render stays byte-identical, and the activation MR changes exactly marker to
  `active` plus replicas `0` to the approved count.

Accepted residual risks of `ce-guarded` (recorded in the migration record; the migration owner takes
explicit responsibility):

- The gate job lives in candidate CI and is shadowable by candidate Maintainers; there is no
  unshadowable SRE-owned injection.
- GitLab CE lacks merged-result pipelines: MR pipelines run on the MR head (event type
  `basic` on older releases, `detached` on GitLab 18), cannot bind merge
  parents, and the baseline target-branch SHA is a point-in-time snapshot that may advance before
  merge (TOCTOU). A stale-base or head-only pipeline is not evidence for a later merge.
- Independent CODEOWNER approval cannot be enforced; MR approvals are not available on CE.

Mandatory compensating controls (none may be skipped):

- The migration owner reviews and merges every `staging` MR personally. Before opening and again
  immediately before merging each CI, evidence, or overlay MR, rerun the dormant and evidence
  validators locally against the exact branch commit; a nonzero result is a STOP.
- The evidence artifact remains the authoritative state machine; local validator runs and the CI
  gate are complementary, never substitutes.
- The migration record carries a replacement Ops Todo naming the SRE policy project, owner, job,
  image digest, and acceptance proof to replace the candidate gate when the instance gains pipeline
  execution policies; it stays open until the SRE-owned policy is live. The candidate gate is
  removed only in the same MR that installs the SRE-owned policy.
- An MR that removes, weakens, skips, or bypasses the candidate gate without the SRE policy in
  place is a STOP; `allow_failure`, manual skip, path-only exclusion, and bypass rules stay
  forbidden.

Candidate evidence is checked against trusted protected-parent evidence. Identities, terminal status,
and completed rows are immutable; at most one pending row progresses per MR. Each authorization/result
uses a separate evidence-only MR. After an operational authorization, evidence progresses without
changing dormant render. A prior MR must reach `--phase activation-ready`; the activation MR then
changes only exact target-overlay files, excludes evidence, source, production, Jenkins, CI, and all
other paths, and changes exactly marker to `active` plus replicas `0` to audited approved count.
After activation, ordinary MRs preserve both the `active` marker and approved replica count and cannot
replay an early activation. Rollback similarly consumes prior authorization/isolation evidence and
changes only those fields back to dormant/zero. Do not add `allow_failure`, manual skip, path-only
exclusion, or a bypass rule.

Before CI edit and app MR open, re-evaluate MR source branch, target branch, pipeline source, and changed
paths. They must mechanically exclude the exact app/CI MR from the source writer. The tuple guard cannot
make an otherwise reachable trigger safe; a broad environment check, disabled fixture, or US-only guard
cannot satisfy EU isolation. On PASS, set `$legacy_writer_isolation_status=VERIFIED`.

Validate the central gate on every protected-`staging` MR/branch pipeline, exact branch binding, writer
isolation across all four dimensions, exact tuple guard, and unchanged enabled-idle writer/write set.
In `ce-guarded`, validate the candidate gate the same way, prove the SRE policy is still absent and
the CE-enforced controls still hold, and keep the replacement Ops Todo open.

## Legacy-live Step 7. Merge the app MR and prove the target artifact

The app MR retains the zero-replica target and writer-isolation evidence; merge cannot start target or
write, scale, or restart source. Immediately before merge, re-evaluate final source/target branches,
pipeline source, and changed paths; any writer reachability is a STOP, regardless of tuple guard.
Validate unchanged source health/identity/writer state and zero target Pods.

## Legacy-live Step 8. Create the dormant target ArgoCD Application

Keep target always dormant and digest-pinned during preparation: pin the exact multi-arch digest
recovery seed and omit Image Updater annotations. Automation starts only under a separately designed
post-activation contract and cannot mutate the dormant lock. Point Application only at the proven
zero-replica overlay. Registration authorizes natural dormant reconciliation only, not probe or
activation.

Immediately before opening the Application MR and again immediately before its merge, use a clean
checkout of exact target-branch commit, render bounded target YAML, and rerun:

```bash
python3 "$skill_root/validators/check_dormant_target.py" \
  --app "$app" --namespace "$target_namespace" \
  --expected-kind "$target_controller_kind" \
  --phase dormant \
  "$target_render_file"
```

At pre-merge, rerun Step 2 `check_legacy_target_live_empty.py --context "$target_context"
--namespace "$target_namespace"` with fresh writer inventory. Refresh Application, Helm, namespace,
same-app ownership, Service, Ingress, Endpoints, and EndpointSlice. Ownership and target-directed
writer sets must still be empty. Auth failure, unavailable Rollout/KEDA inventory, or drift is a STOP.

At pre-open and pre-merge, run the generic Application validator and then:

```bash
python3 "$skill_root/validators/check_legacy_target_application.py" \
  --app "$app" \
  --application-name "$application_name" \
  --expected-repo "$application_repo_url" \
  --expected-path "$target_overlay" \
  --expected-namespace "$target_namespace" \
  "$application_yaml"
```

It requires exact `spec.source.targetRevision: staging`, credential-free app repo, path, namespace, and
identity; generic PASS cannot replace it. Validate zero rendered replicas, no possible target Pod,
exact recovery digest, no Image Updater annotations, and immediately preceding dormant/empty gates.

## Legacy-live Step 9. Passive target validation

Verify only dormant target: Synced/Healthy Application, zero desired/Ready replicas and app Pods,
expected identities, Ready secret/credential controllers that cannot start app, immutable recovery
seed, and unchanged source/writer evidence. Do not run app, join consumer group, send callbacks, or run
a runtime smoke probe. Any private dependency probe needs fresh scope and cannot start the app. Zero
logs are expected. Validate all three target counts remain zero.

## Legacy-live Step 9.5 Parallel activation mode

`$activation_mode=parallel` applies to staging-only stateless request/response
services without consumer-group ownership (for example `auth`), where zeroing
the source would break consumers that have not yet repointed to the target.
It is an explicit contract field in the evidence artifact, immutable for the
whole handoff, and requires:

- `spec.activation_mode: parallel` rendered from bootstrap with
  `source_scale_authorization` and `source_zero` closed as `NOT_INVOKED`
  (the source is never scaled to zero in this mode); the bootstrap validator
  rejects any other status for those rows.
- Source identity keeps `rollback_replicas` as the running replica count; the
  source stays at that count through activation and the observation window.
- The activation MR still changes exactly dormant marker to `active` and
  replicas `0` to the approved count; activation-ready requires the other
  four pre-activation rows VERIFIED (preparation, writer fence authorization
  and result, activation authorization).
- Rollback in parallel mode closes `rollback_source_restore` and
  `rollback_source_ready` as `NOT_APPLICABLE` (nothing to restore); the
  target-zero and writer-policy rows behave as in stop-source mode.
- Source zeroing is deferred to a separately coordinated consumer repointing
  phase and must NOT be performed inside this mode.

A stop-source handoff never sets `activation_mode: parallel`; the validator
forbids NOT_INVOKED source rows in stop-source mode.

## Legacy-live Step 10. Perform the separately authorized handoff

Preparation ends before this action. Obtain fresh scoped authorization independently for each mutation
and use only `$legacy_handoff_evidence_path` as the state machine. Run the evidence validator with
`--phase auto` before and after every persisted row; nonzero is a STOP. The writer-fence authorization does not authorize source scale-down,
activation, or traffic change.

1. Re-freeze identities, source rollback replicas/health, writer state, target dormant marker/zero,
   consumer state, and traffic. Persist `preparation=VERIFIED` with stable evidence reference, canonical
   UTC timestamp, actor, sanitized exact state, and no raw/secret output.
2. Obtain dedicated fresh authorization to fence exactly `$legacy_writer`; persist
   `writer_fence_authorization=VERIFIED`, disable it, prove disabled/idle/no execution/queue-empty, then
   persist `writer_fence_result=VERIFIED`. Keep it fenced through activation, traffic, and rollback.
3. Obtain separate fresh authorization for the exact source scale command; persist
   `source_scale_authorization=VERIFIED`, scale only the exact legacy source controller to zero, prove
   zero source Ready Pods, persist `source_zero=VERIFIED`, and never delete source.
4. Persist separate `activation_authorization=VERIFIED` while target stays dormant, then validate the
   protected branch with `--phase activation-ready`. The later protected-`staging` activation MR
   changes exactly two rendered fields together: marker `dormant` to `active`, and replicas `0` to
   `spec.target.approved_replicas`. Its branch guard consumes trusted BASELINE evidence, excludes the
   evidence path, rejects evidence/CI/validator edits, and allows only exact target overlay. After merge
   and protected pipeline, persist `activation_transition=VERIFIED`, allow natural reconciliation, and
   persist `activation_result=VERIFIED` or one complete `FAILED` row.
5. Prove target Ready, stable Pod identity, dependencies, and single consumer ownership. If route exists,
   persist `traffic_authorization=VERIFIED`, perform only the separately authorized traffic MR, and
   persist `traffic_result` later. If none, close authorization then result as `NOT_APPLICABLE` in
   separate MRs. Run approved observation and persist `runtime_acceptance`; keep writer fenced.
6. On pre-traffic or post-traffic failure, persist `rollback_authorization=VERIFIED`, target-traffic
   isolation evidence, and `rollback_status: IN_PROGRESS` while branch remains active. Then validate and
   persist each step before the next mutation:
   - isolate/drain target traffic first, then persist it;
     `rollback_traffic_isolation=NOT_APPLICABLE` only if traffic was never authorized;
   - merge protected-`staging` rollback changing exactly active/approved to dormant/zero; persist
     `rollback_target_zero=VERIFIED` only after zero target desired/Ready/Pods/consumer ownership;
   - restore captured source replicas and persist `rollback_source_restore=VERIFIED`; separately prove
     Ready and persist `rollback_source_ready=VERIFIED`;
   - only after source Ready, restore `$rollback_upstream` and persist
     `rollback_route_restore=VERIFIED`; `NOT_APPLICABLE` only if traffic was never authorized;
   - apply `$legacy_writer_reenable_policy` last and persist `rollback_writer_policy=VERIFIED` whether
     re-enabling or deliberately leaving fenced. Never add a source dependency fallback to target.

Every non-PENDING row has one of the stable internal GitLab issue/MR/note URLs or an immutable safe ID, canonical
UTC timestamp, bounded actor, sanitized state, and allowed status. Duplicate/unknown fields, aliases,
control characters, reused refs, placeholders, secret-bearing values, impossible order, or multiple
operational `FAILED` rows fail closed without echoing row content. Raw secret/token material, signed
URLs, terminal/log/manifest output, and sensitive assignments are invalid.

Successful no-rollback closure runs `--phase terminal --expected-outcome success` and ends exactly
`VERIFIED/NOT_INVOKED`. Completed rollback runs `--phase terminal --expected-outcome rollback` and ends
`ABORTED_ROLLED_BACK/VERIFIED`. Later forward rows are complete `NOT_INVOKED`; pre-traffic rollback
uses both conditional route rows as `NOT_APPLICABLE`, while post-traffic rollback verifies both.
Markdown status cannot override validation.

Validate writer disabled/idle/queue-empty before source zero, source zero before activation, writer
fenced through activation/traffic, no simultaneous consumers, and rollback in exact isolation -> target
zero -> source restore -> source readiness -> route restore -> writer policy order. Each row is durable
and validated before the next mutation.

Target preparation never scales or deletes the source, switches traffic, retires the writer, or starts
a `legacy-live` target. Every such action belongs to this later handoff and needs fresh scope.
