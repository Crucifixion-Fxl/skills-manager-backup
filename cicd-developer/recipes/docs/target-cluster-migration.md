# Target cluster migration: {{app}} -> {{target_cluster}}

## Scope

| Field | Value |
|---|---|
| Source ownership mode | `{{source_ownership_mode}}` |
| Enforcement mode | `{{enforcement_mode}}` |
| CE-guarded authorization row | `{{ce_guarded_authorization}}` |
| Activation mode | `{{activation_mode}}` |
| Source cluster | `{{source_cluster}}` |
| Source context / namespace | `{{source_context}}` / `{{source_namespace}}` |
| Source cluster fingerprint | `{{source_cluster_fingerprint}}` |
| Source branch | `{{source_branch}}` |
| GitOps source Application / overlay | `{{source_application}}` / `{{source_overlay}}` |
| Legacy live controller / image digest | `{{source_live_controller}}` / `{{source_digest}}` |
| Source Argo CD / Helm owner absence | `{{source_owner_absence_evidence}}` |
| Reference cluster / region | `{{reference_cluster}}` / `{{reference_region}}` |
| Reference approved HTTPS origin | `{{reference_origin}}` |
| Reference overlay / branch / revision | `{{reference_overlay}}` / `{{reference_branch}}` / `{{reference_revision}}` |
| Protected branch evidence | `{{reference_protected_branch_evidence_ref}}` |
| Reference provenance evidence | `{{reference_provenance_evidence}}` |
| Reference render digest | `{{reference_render_digest}}` |
| Target cluster | `{{target_cluster}}` |
| Target context / namespace | `{{target_context}}` / `{{target_namespace}}` |
| Target cluster fingerprint | `{{target_cluster_fingerprint}}` |
| Target branch / overlay | `{{target_branch}}` / `{{target_overlay}}` |
| Expected target controller kind | `{{target_controller_kind}}` |
| Target runtime-state annotation | `{{dormant_marker_key}}={{dormant_marker_value}}` |
| Target ownership / empty evidence | `{{target_empty_evidence}}` |
| Runtime environment | `{{env_keyword}}` |
| Traffic entry | `{{traffic_entry}}` |
| Rollback upstream | `{{rollback_upstream}}` |
| Legacy writer / state | `{{legacy_writer}}` / `{{legacy_writer_state}}` |
| Legacy writer trigger | `{{legacy_writer_trigger}}` |
| Legacy writer write set | `{{legacy_writer_write_set}}` |
| Legacy writer exact-tuple guard | `{{legacy_writer_guard}}` |
| Legacy writer fence method | `{{legacy_writer_fence_method}}` |
| Legacy writer queue-empty evidence | `{{legacy_writer_queue_empty_evidence}}` |
| Legacy writer rollback re-enable policy | `{{legacy_writer_reenable_policy}}` |
| Writer isolation decision | `{{writer_isolation_decision}}` |

Use literal `n/a` only for fields that do not apply to the selected mode.
`gitops` requires its source Application and overlay. `legacy-live` never
fabricates them: it requires separate live-source, writer, target-empty, and
different-region reference-overlay evidence.

Source preparation is read-only. In `legacy-live`, target preparation also
keeps target replicas at zero and the exact controller annotation
`migrations.addx.io/runtime-state: dormant`. Workload activation and traffic
switching use separate, freshly authorized changes. Source retirement is
outside this migration.

## Artifact baseline

| Evidence | Value | Status |
|---|---|---|
| Source Git revision | `{{source_revision}}` | FROZEN |
| Source image digest | `{{source_digest}}` | FROZEN |
| Reference revision | `{{reference_revision}}` | {{reference_status}} |
| Reference render digest | `{{reference_render_digest}}` | {{reference_status}} |
| Target image tag | `{{target_image_tag}}` | TODO |
| Target image digest | `{{target_image_digest}}` | TODO |
| Target pipeline | `{{target_pipeline}}` | TODO |

## Runtime and build contract

Use `n/a` for the static-web fields when `Runtime profile` is `service`.
For static-web, this table is a frozen verification snapshot of the target row
in `docs/deployment/cd-requirements.md`, which remains the source of truth.

| Field | Value | Status |
|---|---|---|
| Runtime profile | `{{runtime_profile}}` | FROZEN |
| Target build script | `{{target_build_script}}` | {{target_static_contract_status}} |
| Target static output | `{{target_static_output_dir}}` | {{target_static_contract_status}} |
| Target public bundle config | `{{target_bundle_config_source}}` | {{target_static_contract_status}} |
| SPA routing | `{{target_spa_routing}}` | {{target_static_contract_status}} |
| Node / Nginx exact versions | `{{target_node_version}}` / `{{target_nginx_version}}` | {{target_static_contract_status}} |
| Container port / health path | `{{target_port}}` / `{{health_path}}` | FROZEN |
| Target Nginx config | `{{target_nginx_conf_path}}` | {{target_static_contract_status}} |
| Target Dockerfile | `{{target_dockerfile_path}}` | {{target_static_contract_status}} |

## Dependencies

| Dependency | Source contract | Target contract | Network evidence | Owner | Status |
|---|---|---|---|---|---|
{{dependency_rows}}

## Structural classification

| Structural reference item | Class | Target action | Evidence | Status |
|---|---|---|---|---|
{{file_rows}}

Allowed classes: `portable`, `target-specific`, `source-only`, `unresolved`.
No `unresolved` row may remain when the target overlay is committed.

## Reference-to-target deltas

| Reference identity | Target identity | Structural class | Allowed delta category | Exact change | Evidence | Status |
|---|---|---|---|---|---|---|
{{delta_rows}}

Every target-render difference requires exactly one reviewed row containing
both its structural class and one allowed delta category. Structural classes
are `portable`, `target-specific`, `source-only`, or `unresolved`; allowed delta
categories are namespace/environment label, target Harbor image, Vault/ESO
contract, dependency endpoint, target placement, and classified source-only
removal. Controller and resource kinds remain equal to the reference unless a
separately bundled recipe authorizes the change.

## Legacy-live executable handoff evidence

This section applies only when `Source ownership mode` is `legacy-live`. In
`gitops`, `{{legacy_handoff_evidence_path}}` is literal `n/a`, no legacy
evidence artifact or central legacy state gate is created, and none of the
commands below is invoked.

For `legacy-live`, the only authoritative handoff/rollback record is the tracked
path `{{legacy_handoff_evidence_path}}`, rendered from
`recipes/docs/legacy-target-handoff-evidence.yaml.tmpl`. This Markdown document
may summarize that artifact, but Markdown text or gate status can
never override the validator result.

After every persisted event row, run:

```bash
python3 "$skill_root/validators/check_legacy_target_handoff_evidence.py" \
  --phase auto "{{legacy_handoff_evidence_path}}"
```

Persist each authorization and each result through a separate evidence-only MR
while runtime state remains unchanged. Each such MR changes exactly this YAML
path, progresses at most one pending event row, and keeps the target render
byte-identical. After the activation-authorization MR merges, require the
trusted protected branch to
pass:

```bash
python3 "$skill_root/validators/check_legacy_target_handoff_evidence.py" \
  --phase activation-ready "{{legacy_handoff_evidence_path}}"
```

The later activation MR consumes that trusted BASELINE evidence through a
centrally injected SRE-owned GitLab pipeline execution policy. In
`enforcement_mode: ce-guarded`, the baseline binding instead runs through the
candidate-rendered `legacy-target-state-gate` job and the migration-owner
authorization row; CE residual risks (shadowable gate, non-merged-result MR
pipelines, no independent approval) are recorded and accepted in the migration
record, and the SRE policy must replace the candidate gate when the instance
gains pipeline execution policies. Candidate and
trusted-base Python are never executed. Its changed paths exclude this evidence
path and are closed to the exact target overlay; business source, production,
Jenkins, CI, and every other path fail. Candidate evidence must preserve the
trusted source/target identity and every completed row while progressing only
pending state monotonically. Rollback uses the same split and allowlist: persist
authorization and traffic isolation in separate evidence-only active-state MRs,
then make the separately judged marker/replica transition. Every applicable MR check
is a merged-result pipeline targeting the protected `staging` branch and bound
to the exact protected target and source parents; `main`/`master` MRs do not run
this target gate, and a stale/head-only pipeline is not evidence.

At successful terminal closure, run `--phase terminal --expected-outcome
success`; at completed rollback closure, run `--phase terminal
--expected-outcome rollback`. The YAML schema is closed and every event row has
exactly `evidence_ref`, `timestamp_utc`, `actor`, `observed_state`, and `status`.
Stable internal GitLab issue/MR/note URLs or immutable safe record IDs are the
only accepted references. Duplicate keys, aliases, control characters,
unknown fields, non-canonical UTC, reused/stale references, placeholders, raw
secret/token material, punctuation-wrapped raw-output markers, manifest-shaped
`metadata:`/`spec:`/`data:`/`stringData:` fragments, YAML document markers or
braces, long opaque base64-like blobs, impossible
ordering, and incomplete terminal matrices fail closed without echoing row
content.

Rollback ordering is executable in the event map: isolate target traffic,
prove target zero, restore exact source replicas, prove source Ready, only then
restore/reopen the source route, and apply the writer re-enable policy last.
The traffic-isolation and route-restore rows may both be `NOT_APPLICABLE` only
when `traffic_authorization` was never `VERIFIED`; once traffic was authorized,
both rollback rows must be `VERIFIED`, including when `traffic_result: FAILED`.

## Gates

| Gate | Acceptance evidence | Status |
|---|---|---|
| Source baseline | Exact Git SHA and image digest | FROZEN |
| GitOps source | Application and source overlay are exact, or mode is `legacy-live` | {{gitops_source_status}} |
| Legacy live source | Healthy stateless controller, no Argo CD/Helm owner, exact live inventory, or mode is `gitops` | {{legacy_live_source_status}} |
| Structural render | Source/reference render before and after target preparation is identical | TODO |
| Target ownership | No target-directed legacy writer; `legacy-live` has no Service, Ingress, Endpoints, EndpointSlice, workload producer, or autoscaler, while `gitops` is empty or explicitly approved | TODO |
| Legacy writer isolation | Exact writer, trigger, write set, state, tuple guard, and mechanical source-branch/target-branch/pipeline-source/changed-path exclusion prove the app/CI MR cannot invoke it, or mode is `gitops` | {{legacy_writer_isolation_status}} |
| Reference provenance | Config-isolated fresh proof validates approved HTTPS origin, exact protected `staging` branch, exact detached SHA, ancestor/merged-MR lineage, no replacement refs, and the exact render digest, or mode is `gitops` | {{reference_status}} |
| Static bundle semantics | Exact target build is production-semantic, credential-isolated, produces non-empty output, and runs in MR/target-branch CI | TODO |
| Target namespace | `{phase}-{app}` or committed exception | TODO |
| Vault registration | Correct domain/region reconcile names the app | TODO |
| Vault contents | Expected key names exist; values are not recorded here | TODO |
| Network | Every eligible subnet/AZ reaches dependencies and stable NAT | TODO |
| Target image | Immutable SHA tag and digest in target Harbor | TODO |
| Target Application | Generic validator for both modes; dedicated legacy-live validator additionally requires exact app/repo/path/namespace and `targetRevision: staging`, with real image SHA | TODO |
| Dormant target | `legacy-live` exact render has `{{dormant_marker_key}}=dormant`, passes bounded parsing, local tracked symlink-free Kustomize closure, traffic-resource/unsafe-Service rejection, exact target-namespace binding, raw-Secret rejection, and zero-controller checks in image-owned `check_dormant_target.py`, and is Synced/Healthy with zero desired/Ready replicas and zero application Pods, or mode is `gitops` | {{dormant_target_status}} |
| Passive validation | Mode-applicable health and business checks pass before traffic | TODO |
| Traffic rollback | Separate MR restores exact source upstream | TODO |
| Legacy writer fence | With fresh scoped authorization, exact writer is disabled, idle, queue-empty, and remains fenced through activation/traffic switch, or mode is `gitops` | {{legacy_writer_fence_status}} |
| Legacy handoff | Writer fence and queue-empty proof precede source zero; source zero precedes target activation; no simultaneous runtime, or mode is `gitops` | {{legacy_handoff_status}} |
| Legacy rollback | Isolate target traffic; prove target zero; restore/prove exact source replicas Ready; only then restore/reopen the source route; apply writer policy last, or mode is `gitops` | {{legacy_rollback_status}} |

## Safety boundary

- Do not write to the source cluster during target preparation.
- Do not copy live exports, Jenkins workspace content, or historical Jenkins
  YAML into the application repository.
- Do not conflate the live source inventory with the reference render.
- Allow only classified reference-to-target deltas.
- Do not continue while a legacy writer can mutate the exact target tuple.
- Do not copy Kubernetes Secret objects between clusters.
- Do not render or commit a core Kubernetes Secret; use ESO or a platform
  credential resource in the exact target namespace.
- Do not introduce a target public Ingress unless separately approved.
- Do not combine workload, Application, and traffic changes in one MR.
- For staging, keep the target Application on the protected application
  `staging` branch with exact `targetRevision: staging`. The controller annotation
  `{{dormant_marker_key}}=dormant` plus replicas `0` is the branch lock; do not
  replace `targetRevision` with a commit SHA to simulate dormancy.
- Only the authorized activation transition changes the annotation to `active`
  and replicas to the audited approved count together. Its exact changed-path
  allowlist is only the target overlay and excludes the executable evidence
  path. Ordinary dormant MRs
  preserve both fields; ordinary post-activation MRs preserve the active marker
  and approved count. Rollback changes both fields back together only after
  target traffic isolation evidence passes.
- In `legacy-live`, preparation must not scale/delete the source, switch
  traffic, fence/retire the writer, or start the target. With fresh scoped
  authorization, fence and drain the writer before source scale-down, keep it
  fenced through target activation and traffic switch, and use the recorded
  route/target/source/writer rollback order.
- A staging migration must not modify prod branches, namespaces, images,
  Vault paths, Applications, or routes.
- `legacy-live` itself is staging-only: source and target application branches
  are exact protected `staging`. A prod legacy-live request is a STOP before
  writes; the generic prod self-check remains applicable only to `gitops` mode.
