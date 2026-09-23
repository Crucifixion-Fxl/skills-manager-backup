---
name: new-cronjob
description: Register a periodic Kubernetes CronJob through GitOps. New source stays suspended until a separately reviewed activation change.
---

# Workflow: new-cronjob

## Purpose

Create a bounded periodic workload without making source publication schedule live execution.
This workflow ends with a suspended registration MR:

- `spec.suspend: true` and `ops.addx.io/activation-review: pending`;
- a standard **five-field** cron expression, explicit run limits, and immutable runtime inputs;
- offline render plus both the CronJob-specific and full manifest validator;
- no `kubectl apply`, live patch, manual Argo CD sync, Service, or Ingress.

Activation is outside the registration workflow. It requires a **separate activation MR** that
changes only the review annotation and `spec.suspend` after the suspended registration and its
prerequisites have been reviewed.

## Step 1. Resolve target ownership and repository boundaries

[precondition]
  - `docs/deployment/cd-requirements.md` exists and is complete; otherwise run
    `workflows/interview-cd-requirements.md` first
  - the request is periodic work; a one-time side effect routes to
    `workflows/new-one-shot-job.md`, while an orchestrator-owned schedule stays in that
    orchestrator's independently routed workflow

[action]
  - Resolve every target with `references/data/env-keywords.yaml`; an unknown environment is a
    STOP.
  - For each target, record the exact environment, cluster, namespace, owning team, application
    repository, existing Argo CD Application, source path, target revision, and overlay.
  - Put the CronJob only in the existing owning application repository and overlay. Do not create
    a second Application for the same namespace.
  - Keep Application/AppProject source in `argocd-apps`, centralized cloud identity in
    `crossplane-infra`, and app-owned workload source in the application repository. Missing
    ownership or a required new Application is a STOP and an independently routed handoff.
  - Perform repository and desired-state inspection only. Do not run live `kubectl`, `kubectl
    apply`, `helm install`, Argo CD sync, patch, or delete operations.

[validate]
  - target, environment, namespace, owner, repository, Application, revision, and overlay all have
    explicit evidence
  - the overlay is already owned by the recorded Application and no cross-repository file is
    planned in the registration MR

[output]
  - `$targets[]` and an exact repository-routing table

## Step 2. Freeze schedule and execution semantics

[precondition]
  - Step 1 completed

[action]
  - Record one standard **five-field** schedule (`minute hour day-of-month month day-of-week`).
    Reject macros such as `@hourly`, a sixth seconds field, an empty field, or free-form prose.
  - Decide the timezone explicitly. If the target Kubernetes API and repository convention support
    `spec.timeZone`, record an IANA `Area/Location` (or `Etc/UTC`) and emit the `timeZone` field. If
    they do not, omit the field only with version/repository evidence and document the controller
    timezone used to interpret the schedule. Never silently assume local time.
  - Record `concurrencyPolicy` (`Forbid` is the safe default; `Allow` or `Replace` requires an
    explicit idempotency/replacement justification), positive `startingDeadlineSeconds` for a
    missed run, positive per-run `activeDeadlineSeconds`, non-negative `backoffLimit`, and
    non-negative `successfulJobsHistoryLimit` / `failedJobsHistoryLimit`.
  - Record the owner, expected duration, idempotency key or duplicate-run behavior, success signal,
    failure signal, alert/observation path, and rollback or disable action.
  - Freeze two reviews: registration MR keeps the CronJob suspended; the separate activation MR
    changes only `ops.addx.io/activation-review` from `pending` to its full GitLab MR URL and
    `spec.suspend` from `true` to `false`. Schedule, image, command, credentials, resources, and
    placement changes return to a new registration review.

[validate]
  - schedule has exactly five whitespace-delimited fields
  - timezone inclusion/omission has target evidence
  - concurrency, missed-run deadline, job deadline, backoff, and both history limits are explicit
  - registration and activation reviews are separate and activation cannot bundle runtime changes

[output]
  - `$schedule_contract` and `$activation_contract`

## Step 3. Freeze immutable runtime and dependency inputs

[precondition]
  - Step 2 completed

[action]
  - Resolve an internal image by immutable tag plus digest
    (`...:<immutable-tag>@sha256:<64 lowercase hex>`). A digest-only reference, mutable tag,
    tag-only reference, `latest`, unresolved build output, or fallback image is a STOP.
  - Record explicit non-empty YAML string lists for `command` and `args`; do not rely on an unknown
    image entrypoint or shell interpolation.
  - Record `envFrom` and `env` decisions. Credential-free execution is valid and must render both
    as `[]` when unused.
  - If credentials are required, use only an existing approved ExternalSecret and Vault resolver
    contract, or first complete the independently routed approved resource workflow. Confirm only
    object names, key names, ownership, and readiness; never invent a Vault path, Secret, account,
    or credential value.
  - Record CPU, memory, and ephemeral-storage requests/limits; a bounded `/tmp`; non-root UID;
    read-only root filesystem; dropped capabilities; seccomp; imagePullSecret; explicit
    ServiceAccount; `automountServiceAccountToken: false`; and explicit nodeSelector, affinity,
    and tolerations decisions. Cloud identity or special placement requires existing reviewed
    repository evidence.
  - Do not create public exposure. A Service or Ingress is not part of this workflow and may be
    added only through its own route and review.

[validate]
  - image is a pullable immutable digest with no mutable fallback
  - command and args are non-empty lists
  - every env, resource, security, placement, and ServiceAccount decision is explicit
  - every credential reference already has an approved owner/resolver and no secret material is
    read or emitted

[output]
  - `$runtime_contract`

## Step 4. Write suspended source

[precondition]
  - Steps 1-3 completed without unresolved inputs

[action]
  - Fill `recipes/k8s/suspended-cronjob.yaml.tmpl` and write
    `$overlay/cronjob-<purpose>.yaml` in the owning application repository.
  - Use `{{timezone_block}}` only for the exact Step 2 decision. Fill every other slot from the
    structured contracts; do not redesign the template or add an unbundled resource kind.
  - Add the file to the existing overlay Kustomization.
  - Preserve the registration defaults: `spec.suspend: true`, activation review `pending`, digest
    image, explicit command/args/env, bounded execution controls, and safe pod security.
  - Update `docs/deployment/cd-requirements.md` and `docs/deployment/cicd.md` with the repository,
    target, schedule/timezone, runtime, credential, observation, and activation contracts.
  - Do not generate a Service, Ingress, Application, AppProject, credential, or live operation.

[validate]
  - the CronJob, Kustomization entry, and both documentation contracts exist
  - source contains no unresolved `{{...}}`, mutable image, or live-execution fallback

[output]
  - `$overlay/cronjob-<purpose>.yaml`
  - updated Kustomization and deployment docs

## Step 5. Render and run deterministic validators

[precondition]
  - Step 4 completed

[action]
  - Create a temporary local render directory and run the repository-supported exact Kustomize or
    Helm render for `$overlay` into it. Raw Helm templates are never validator input.
  - Assert the render command exits zero, output is non-empty, and the rendered CronJob has the
    expected name, namespace, digest, schedule, and suspended registration state.
  - Run `python3 "$skill_root/validators/check_cronjob.py" --require-registration "$render_dir"`.
  - Run `bash "$skill_root/validators/validate.sh" --repo-context app "$render_dir"`.
  - Run any additional manifest validator required by the owning repository.

[validate]
  - missing dependency, invalid input, render failure, **empty manifest** output, missing CronJob,
    or any non-zero validator result is a STOP; report the raw error and do not patch around it
  - rendered source still has `spec.suspend: true` and activation review `pending`

[output]
  - exact render command, immutable input revision, and validator logs

## Step 6. Submit registration for review

[precondition]
  - Step 5 passed

[action]
  - Stage only the files declared by this workflow and open a Draft registration MR in the owning
    application repository.
  - State that merge registers a suspended CronJob and does not schedule execution. Include the
    schedule/timezone, immutable image digest, command/args, limits, credentials decision,
    placement, owner, observation, rollback, and validation evidence.
  - Do not merge, activate, force sync, or mutate live state in this workflow. If GitOps
    registration or a new Application is needed, emit an exact independently routed Ops Todo.

[validate]
  - MR diff contains no `spec.suspend: false`, mutable image, Service/Ingress, credential value,
    cross-repository file, or unrelated change
  - repository pipeline reruns the exact render and applicable validators

[output]
  - registration MR URL and any bounded Ops Todo

## Exit

Stop after suspended registration review. Activation is a separately authorized Git change with a
fresh pipeline and reviewer. It changes only the full activation review URL and `spec.suspend:
false`; any other change requires a new registration review. Rollback is a binding-first Git change
back to `spec.suspend: true`, followed by normal GitOps reconciliation. This workflow never performs
live `kubectl apply` or silently enables a schedule.
