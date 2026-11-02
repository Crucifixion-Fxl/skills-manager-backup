# CN staging PostgreSQL Manual revision identity checks

Work Item: https://gitlab.addx.ai/engineering/skills/-/issues/334
Revision 3/4 registration: https://gitlab.addx.ai/engineering/skills/-/issues/378
Root Issue: https://gitlab.addx.ai/infra/mlflow/-/issues/1
Deployment Task: https://gitlab.addx.ai/infra/mlflow/-/work_items/2

The existing CN factory intentionally uses Manual Composition adoption. Do not
change that policy, remove an explicit selection or use the AWS identity
projection to make a candidate pass. This profile adds bounded offline evidence
checking; it does not prove cluster admission, SQL, TLS, credentials or deployment.

## Accepted producer contracts

The only supported composition is database.cn-staging.platform.addx.io, explicit
engine=postgres/env=staging, primary app in its exact staging namespace. A claim
must name a concrete compositionRevisionRef and Manual update policy. Selectors,
additionalUsers, instanceRef, unknown revisions or arbitrary spec changes fail
closed. The app must match the factory canonical snake_case rule, 2..31 characters;
doubled or trailing underscores fail even when no app-slug annotation is present.

Four reviewed DEV/k8s Composition specs are recognized. Hash the complete spec
with canonical Python JSON (sort_keys=True, separators=(',', ':')), excluding
only the native CompositionRevision revision counter. No template is executed.

| Source | Canonical spec SHA256 | Capability |
|---|---|---|
| [merged911798](https://gitlab.addx.ai/DEV/k8s/-/blob/911798bd2f5f1e0bf40c87e82a74ae05c312e2ad/prepared/cn-staging-shared-postgres-factory/composition-database.yaml) | f478388ab73a7b7fc909186b2fd0e21470feab397945b62763e039a0c70d1c15 | Five SQL resources, publication unavailable |
| [merged71e85e0](https://gitlab.addx.ai/DEV/k8s/-/blob/71e85e0c9eee11026ff9724e335fcf224c77b286/prepared/cn-staging-shared-postgres-factory/composition-database.yaml) | 022802afb1a8dc44f36f368b403eae8f9b67db12f9d51a41bfce596918723ffe | Same SQL identities, default-off scoped publication for alphanumeric apps only |
| [merged067a008](https://gitlab.addx.ai/DEV/k8s/-/blob/067a0086aa365752674a8dd3b4182e849536d7f0/prepared/cn-staging-shared-postgres-factory/composition-database.yaml) | 363a2e6b6efd755d3eb3585337eda47d83c793afccee678832c3d712dcbe019e | Adds optional purpose identities; publication still alphanumeric app only and never with purpose |
| [mergedbb5ec5d](https://gitlab.addx.ai/DEV/k8s/-/blob/bb5ec5d0bcf8a0899434c4dd63922a433ba88802/prepared/cn-staging-shared-postgres-factory/composition-database.yaml) | 2ff826c1ee99fd3e9787b0e357473ce0c2040793be82a30cb9aae47042cc3008 | Purpose identities; publication also for purpose and underscore app identities |

The validator applies each selected revision's own template guards; it never
borrows a newer revision's capability. A merged source is not evidence of
activation. The selected revision must actually exist on the exact target and
match its full spec hash. SQL-only revisions cannot accept publishCredentials=true.
Publication-capable revisions additionally require the actual native TLS/SQL/identity
acceptance in the platform plan; this offline profile does not authorize that claim
update. New factory semantics require a reviewed profile update, not a new name prefix.

### Purpose identities (merged067a008 and later)

`spec.purpose` is optional. It must be canonical snake_case (same rule as app,
no doubled or trailing underscore), 2..20 characters, and `app + "__" + purpose`
must not exceed 63 characters. The claim keeps the owner app and the owner
`staging-<app-slug>` namespace. This CN factory derives identities differently
from the AWS `database.platform.addx.io` contract in the shared-middleware README;
never project a CN claim through the AWS `<app>_<purpose>` /
`postgres-<purpose>` rules.

| Identity | Without purpose | With purpose |
|---|---|---|
| SQL Role / Database / Schema | `app` | `app__purpose` |
| Connection Secret (crossplane-system) | `cn-staging-<app-dns>-postgres-db-cred` | `cn-staging-<identity-dns>-postgres-db-cred` |
| Schema ProviderConfig | `cn-staging-<app-dns>-postgres-schema` | `cn-staging-<identity-dns>-postgres-schema` |
| PushSecret / ClusterSecretStore reader+writer | `cn-staging-<app-dns>-postgres[-reader/-writer]` | `cn-staging-<identity-dns>-postgres[-reader/-writer]` |
| Publisher / reader ServiceAccount | `<app-dns>` | `<identity-dns>` |
| Vault key (cn-staging-vault/shared-db) | `staging/rds/application/<app-dns>/postgres` | `staging/rds/application/<identity-dns>/postgres` |

`<identity-dns>` replaces every `_` of `app__purpose` with `-` (for example
`micro_app_platform__spicedb` becomes `micro-app-platform--spicedb`). Because app
and purpose contain no hyphens and purpose cannot hold a doubled underscore, the
mapping stays injective. The merged71e85e0 and merged067a008 templates publish
under the raw app name, which equals `<app-dns>` for the only names they can
publish (alphanumeric app, no purpose); one projection therefore covers revision
adoption across all four sources.

## Collect actual target evidence

After the routed target/live identity checks, use its explicit context. Obtain
fresh complete DatabaseList and target-selected CompositionRevisionList JSON:

```bash
kubectl --context cn-staging get --raw /apis/platform.addx.io/v1alpha1/databases > "$database_inventory"
kubectl --context cn-staging get --raw '/apis/apiextensions.crossplane.io/v1/compositionrevisions?labelSelector=crossplane.io%2Fcomposition-name%3Ddatabase.cn-staging.platform.addx.io' > "$composition_revisions"
bash "$skill_root/validators/validate.sh" --repo-context app \
  --database-inventory "$database_inventory" \
  --database-composition-revisions "$composition_revisions" "$target_render_dir"
```

The example context is the verified staging target, never a default context or
production substitute. Do not synthesize an empty list or populate its resource
version after a failed inventory read. Some kubectl aggregate output loses list
metadata; the native raw API preserves the resourceVersion required by the gate.
Pagination, duplicate identities/JSON keys, missing UID/resourceVersion, wrong
kind/labels and unrecognized full specs are invalid evidence. Both inventories
are required; a revision name or hash supplied in a claim is not sufficient.

The validator reads bounded inert files only. The trusted caller must verify the
context/cluster, source/namespace ownership, freshness and completeness of these
native responses. Offline JSON is not cryptographic proof of its API origin.
Retain the actual files and exact refs with the deployment receipt. New claims
may pin the existing verified SQL-only revision for initialization; later adoption
of the publication revision and publication enablement require reviewed changes.

## Collision and recovery boundaries

CN projection uses the actual cn-staging- prefixed connection Secret, schema
ProviderConfig, PushSecret, two scoped stores and credential namespace SAs, plus
the isolated shared-db mount's application key. SQL Role/Database identities
remain shared-postgres/app, so mixed legacy/CN claims cannot hide a physical SQL
collision behind different Kubernetes Secret names. Potential publication names
are reserved even while publication is off. Same-claim nonidentity revision
adoption preserves the producer set; identity-changing replacement retains the
existing migration gate. Existing AWS Automatic behavior remains unchanged.

Fixtures contain only the four public source Compositions; test Revision UID and
resourceVersion are explicitly synthetic parser inputs. Tests, fixture hashes
or validator PASS are not native reconciliation evidence. Do not delete a claim,
rotate a password, seed Vault or retry a bootstrap to fix a validation failure.
