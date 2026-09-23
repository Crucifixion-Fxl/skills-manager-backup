# Object storage self-service

GCP object storage uses a versioned platform API. Application repositories may
declare only `platform.addx.io/v1alpha1 ObjectBucket`; the platform owns its XRD,
Composition, Configuration package revisions, GCP project/location, bucket-name
generation, provider identity and security defaults.

The current product is scoped GA only for cluster `us-tech-service-gke` and
namespace `staging-us-gcp`. This is not fleet-wide or GKE Prod availability.
Developer self-service means committing through the existing application
repository and Argo CD Application; direct `kubectl create/apply` is denied.

## Readiness gate

`objectbucket-readiness.yaml` is the fail-closed source of truth. A workflow may
write an ObjectBucket manifest only when all of the following are true:

1. the existing Argo CD Application resolves to a target listed under `targets`;
2. that target has `cloud: gcp` and `developer_enabled: true`;
3. that target has `phase: scoped_ga` and every prerequisite is
   `ready`/`passed`;
4. the Application's exact destination namespace occurs in
   `approved_namespaces`;
5. the workload KSA is rendered by the same existing Application and the
   platform-owned namespaced `ProviderConfig/platform-storage` exists.

Missing targets, missing fields, `false`, pilot-only states and empty namespace
lists all mean STOP before writing YAML. Do not fall back to AWS S3 or invent a
raw GCP managed resource.

`grandfathered_objects` records only the exact immutable revision-2
`staging-us-gcp/dvc-artifacts` UPDATE compatibility. It is not a CREATE
template, readiness proof or test target. Never copy its omitted
`identityMode` contract to a new ObjectBucket.

## Developer contract

- The logical ObjectBucket name is a 1-18 character DNS label. The Composition
  creates `ConfigMap/objectbucket-<logical-name>` in the same namespace.
- The workload uses its existing KSA through GKE Workload Identity Federation;
  no service-account key, JSON credential or Kubernetes Secret is generated.
- New declarations must explicitly set `identityMode`. Prefer `DirectKSA`; its
  KSA has `iam.gke.io/return-principal-id-as-email: "true"` and must not have
  `iam.gke.io/gcp-service-account`. `GSAImpersonation` requires prior platform
  approval and WIF bootstrap for the annotated same-project GSA.
- `spec.access` contains exactly `serviceAccountName`, `identityMode` and
  `mode`. Applications do not choose or pin a Composition revision.
- `ReadOnly` maps to object-viewer behavior; `ReadWrite` maps to object-user
  behavior. Public access is not an option.
- `profile: standard`, `residency: local` and `protection: Retained` are fixed.
- The platform creates a private GCS bucket with uniform bucket-level access,
  public-access prevention, versioning, a seven-day soft-delete policy and no
  Crossplane Delete management policy.
- The application consumes non-secret connection facts from the deterministic
  ConfigMap keys listed in the readiness file.

## Ownership and lifecycle

The ObjectBucket CR belongs in the owning application's existing overlay. The
same Application must also own the selected KSA and workload. Do not create a
separate ObjectBucket Application and do not write Argo CD tracking annotations.
The namespaced ProviderConfig, provider identity and admission policy are
platform onboarding and remain in platform repositories.
XRD/Composition/package sources remain in `DEV/k8s`.

Removing an ObjectBucket from Git is not a normal rollback. The Argo annotations
require prune/delete confirmation, the cloud bucket is retained, and any
decommission must be a separate reviewed workflow that inventories data,
revokes runtime access and records the retained bucket before removing the CR.
