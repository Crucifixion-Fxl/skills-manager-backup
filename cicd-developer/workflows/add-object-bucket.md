---
name: add-object-bucket
description: 给现有 GKE 服务加 GCS ObjectBucket。开发者只声明版本化平台 API，平台负责项目、位置、GCS 名称、ProviderConfig、安全默认值和 WIF 授权。
---

# Workflow: add-object-bucket

## Purpose

Add one private GCS bucket to an existing Argo CD managed GKE application by
declaring `platform.addx.io/v1alpha1 ObjectBucket`. This workflow is fail-closed:
the target and exact namespace must be enabled in the versioned readiness file
before it writes any manifest. The current developer product is scoped GA only
for `us-tech-service-gke/staging-us-gcp`; it is not fleet-wide or production GA.

## Step 1. Resolve the existing Application and exact target

[precondition]
  - The user identifies an existing application or application repository.
  - The owning `argocd-apps` and application repositories are readable.

[action]
  - Find the existing Argo CD `Application` that owns the requested overlay.
  - Record `$app`, `$source_repo`, `$source_path`, `$target_cluster`,
    `$target_namespace`, and `$argocd_application` from that Application.
  - Resolve `$target_cluster` against `references/data/clusters.yaml` by exact
    `argocd_apps_dir` and destination, not by guessing from an env keyword.
  - Keep the existing source revision, overlay, destination namespace and
    AppProject unchanged.
  - Reuse that Application for the workload KSA and ObjectBucket. Do not create
    an ObjectBucket-specific Application or a per-bucket platform Application.

[validate]
  - Exactly one existing Application owns the overlay.
  - Its source repo/path and destination namespace are explicit.
  - The exact cluster entry exists and has `cloud: gcp`.
  - A shared namespace such as `staging-us-gcp` is never treated as proof of
    application ownership by itself.

[output]
  - In-memory target tuple: application, repo, overlay, cluster and namespace.

## Step 2. Enforce the platform readiness gate before writing

[precondition]
  - Step 1 produced one exact target tuple.

[action]
  - Open `references/object-storage/objectbucket-readiness.yaml`.
  - Look up `targets[$target_cluster]` by exact key.
  - Require all of:
    - target exists;
    - `cloud: gcp`;
    - `developer_enabled: true`;
    - `$target_namespace` occurs exactly in `approved_namespaces`.
  - Require `phase: scoped_ga` and every listed prerequisite to be
    `ready`/`passed`. `developer_enabled: true` does not override another phase
    or an incomplete prerequisite.
  - If any requirement is missing or false, STOP before writing files. Return an
    Ops Todo containing target, namespace, current phase/prerequisites and these
    acceptance criteria: namespaced `ProviderConfig/platform-storage`, explicit
    identity-mode admission, isolated positive/denied runtime IO, raw-resource
    and direct-kubectl denial, Argo ObjectBucket health and retained
    decommission readiness.
  - Do not fall back to `add-s3-bucket.md`, raw GCP managed resources or a pilot
    namespace.
  - Do not use direct `kubectl create/apply` as a fallback. Developer
    self-service is the application GitOps path only.

[validate]
  - A successful path proves the exact target is enabled and namespace approved.
  - A stopped path has made no application, `crossplane-infra` or `argocd-apps`
    file change.

[output]
  - Scoped-GA readiness decision and, only when stopped, a fail-closed Ops Todo.

## Step 3. Verify namespace onboarding and workload identity

[precondition]
  - Step 2 passed the readiness gate.

[action]
  - Verify the platform repository declares namespaced
    `ProviderConfig/platform-storage` in `$target_namespace`; do not copy or
    modify that ProviderConfig in the application repository.
  - Prefer `DirectKSA`. Find an app-owned Kubernetes ServiceAccount used by the
    workload, or declare a dedicated one in the same existing overlay, and
    record `$service_account_name`.
  - For `DirectKSA`, require
    `iam.gke.io/return-principal-id-as-email: "true"`, reject
    `iam.gke.io/gcp-service-account`, and set `$identity_mode=DirectKSA`.
    When declaring a dedicated KSA, render
    `recipes/crossplane/object-bucket-direct-ksa.yaml.tmpl` in this overlay and
    preserve its sync wave `-3` so the KSA exists before ObjectBucket wave `-2`.
  - Verify the workload pod template explicitly sets the same
    `serviceAccountName`, enables the GKE metadata server node selector and uses
    Application Default Credentials. The ServiceAccount must render in
    `$target_namespace` and be owned by the same Argo CD Application as the
    ObjectBucket; never hand-write a tracking annotation.
  - `GSAImpersonation` is an advanced path. Continue only when the platform has
    already approved the same-project GSA and installed its WIF binding, and the
    app-owned KSA carries the exact `iam.gke.io/gcp-service-account` annotation;
    then set `$identity_mode=GSAImpersonation`. Otherwise STOP with an Ops Todo.
  - If the ProviderConfig, ServiceAccount or workload binding is absent and
    cannot be added to this same application change, STOP with an onboarding
    Ops Todo; do not make a bucket declaration that cannot pass Composition's
    ExtraResources and same-Application ownership checks.

[validate]
  - The exact namespace has one platform-owned `ProviderConfig/platform-storage`.
  - The rendered application has one same-namespace ServiceAccount with the
    recorded name and the workload uses it.
  - No GCP service-account JSON key, Kubernetes Secret,
    `GOOGLE_APPLICATION_CREDENTIALS` or static credential is introduced.

[output]
  - Verified `$service_account_name`, `$identity_mode` and platform onboarding
    evidence.

## Step 4. Resolve the developer-facing ObjectBucket inputs

[precondition]
  - Step 3 verified onboarding and identity.

[action]
  - Collect `$logical_name` for one bucket purpose and `$access_mode`.
  - Require `$logical_name` to satisfy the ObjectBucket DNS-label rule enforced
    by `check_object_bucket.py` and contain at most 18 characters.
  - Require `$access_mode` to be exactly `ReadOnly` or `ReadWrite`; use
    `ReadOnly` unless the workload must create, overwrite or delete objects.
  - Keep the fixed platform values `profile=standard`, `residency=local` and
    `protection=Retained`.

[validate]
  - The name is a 1-18 character DNS label and is not already declared in the
    target namespace/overlay.
  - The access mode is one of the two supported values.
  - The request does not contain public access, project, project number,
    location, bucket name, ProviderConfig, Composition revision or raw GCP
    provider fields.

[output]
  - `$logical_name`, `$access_mode` and deterministic connection ConfigMap name
    `objectbucket-$logical_name`.

## Step 5. Render the ObjectBucket declaration

[precondition]
  - Step 4 produced validated inputs.

[action]
  - Open `recipes/crossplane/object-bucket.yaml.tmpl` and fill:
    `{{logical_name}}=$logical_name`, `{{namespace}}=$target_namespace`,
    `{{app}}=$app`, `{{service_account_name}}=$service_account_name`, and
    `{{identity_mode}}=$identity_mode`, `{{access_mode}}=$access_mode`.
  - Write the result to
    `$source_path/objectbucket-$logical_name.yaml` in the application repository.
  - When Step 3 selected a new DirectKSA, write the rendered ServiceAccount to
    `$source_path/objectbucket-$logical_name-ksa.yaml` and add it to the same
    kustomization. Reuse an existing same-Application KSA instead of declaring a
    duplicate.
  - Add that exact file to the existing overlay `kustomization.yaml` resources.
  - Preserve the recipe's sync wave, prune/delete confirmations and retained
    platform contract exactly.

[validate]
  - Run
    `python3 "$skill_root/validators/check_object_bucket.py" "$source_path" --objectbucket-target "$target_cluster"`
    and require exit 0.
  - Render the exact overlay with `kustomize build` and verify exactly one
    ObjectBucket with the expected name, namespace, ServiceAccount and explicit
    identity mode. Verify the same render contains the selected KSA.

[output]
  - Application overlay ObjectBucket manifest and updated kustomization.

## Step 6. Consume the deterministic non-secret ConfigMap

[precondition]
  - Step 5 rendered a valid ObjectBucket.

[action]
  - Update the existing workload pod template to consume
    `ConfigMap/objectbucket-$logical_name` through `envFrom.configMapRef`, or map
    only the required keys explicitly.
  - Use only the platform keys `BUCKET_NAME`, `BUCKET_URI`, `BUCKET_CLOUD`,
    `BUCKET_LOCATION`, `BUCKET_ACCESS_MODE` and `BUCKET_SERVICE_ACCOUNT`.
  - Do not create a second ConfigMap, hard-code the generated GCS bucket name or
    add credentials. The ObjectBucket sync wave `-2` lets the platform-provided
    connection ConfigMap become ready before the normal workload wave.

[validate]
  - `kustomize build $source_path` shows the workload referencing exactly
    `objectbucket-$logical_name` in `$target_namespace`.
  - The pod template uses `$service_account_name`, selects
    `iam.gke.io/gke-metadata-server-enabled: "true"`, and does not set
    `GOOGLE_APPLICATION_CREDENTIALS`.
  - No Secret reference, GCP JSON credential, fixed `gs://a4x-ob-*` value or raw
    GCP managed resource appears in the application change.

[output]
  - Existing workload updated to consume platform connection facts.

## Step 7. Run full validation and document lifecycle

[precondition]
  - Steps 5 and 6 produced the complete same-overlay change.

[action]
  - Run
    `bash "$skill_root/validators/validate.sh" --objectbucket-target "$target_cluster" "$source_path"`
    so the shared-namespace exception is bound to the exact readiness target and
    unrelated sibling overlays do not become part of this Application's gate.
  - Run the repository's normal lint/test command and the exact overlay
    `kustomize build`.
  - Update `docs/deployment/cd-requirements.md` and `docs/deployment/cicd.md` with
    logical name, access mode, KSA and ConfigMap reference; do not record or
    predict the generated bucket name.
  - State that removal is a separate platform decommission workflow: inventory
    data, revoke runtime access, record the retained external bucket and obtain
    explicit prune/delete approval. Ordinary rollback must not delete the CR.
  - After merge, use the existing Application resource tree and ObjectBucket
    conditions to require current-generation `Healthy`, `Ready=True` and
    `Synced=True`. Run a minimal runtime IO check from the actual workload KSA
    that matches `ReadOnly`/`ReadWrite`; reading the ConfigMap alone is not an IO
    acceptance test.
  - Do not pin or edit a Composition revision in the application repository.
    Platform promotion owns implementation revisions.

[validate]
  - All validators and repository tests exit 0.
  - Rendered output contains no template slots and no forbidden raw GCP fields.
  - The final summary lists the readiness target/namespace evidence and the
    no-delete lifecycle boundary.

[output]
  - One reviewable application MR in the existing Application containing the
    app-owned KSA (when newly declared), ObjectBucket, workload/ConfigMap
    consumption and documentation, plus any separate platform onboarding Todo.
