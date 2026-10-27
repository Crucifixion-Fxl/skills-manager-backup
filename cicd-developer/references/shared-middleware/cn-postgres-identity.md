# CN staging PostgreSQL Manual revision identity checks

Work Item: https://gitlab.addx.ai/engineering/skills/-/issues/334
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
purpose, additionalUsers, unknown revisions or arbitrary spec changes fail closed.
The app must match the factory canonical snake_case rule, 2..31 characters;
doubled or trailing underscores fail even when no app-slug annotation is present.

Two reviewed DEV/k8s Composition specs are recognized. Hash the complete spec
with canonical Python JSON (sort_keys=True, separators=(',', ':')), excluding
only the native CompositionRevision revision counter. No template is executed.

| Source | Canonical spec SHA256 | Capability |
|---|---|---|
| [merged911798](https://gitlab.addx.ai/DEV/k8s/-/blob/911798bd2f5f1e0bf40c87e82a74ae05c312e2ad/prepared/cn-staging-shared-postgres-factory/composition-database.yaml) | f478388ab73a7b7fc909186b2fd0e21470feab397945b62763e039a0c70d1c15 | Five SQL resources, publication unavailable |
| [merged71e85e0](https://gitlab.addx.ai/DEV/k8s/-/blob/71e85e0c9eee11026ff9724e335fcf224c77b286/prepared/cn-staging-shared-postgres-factory/composition-database.yaml) | 022802afb1a8dc44f36f368b403eae8f9b67db12f9d51a41bfce596918723ffe | Same SQL identities, default-off scoped publication |

The second source being merged is not evidence of activation. The selected
revision must actually exist on the exact target and match its full spec hash.
SQL-only revisions cannot accept publishCredentials=true. Publication-capable
revisions additionally require the actual native TLS/SQL/identity acceptance in
the platform plan; this offline profile does not authorize that claim update.
New factory semantics require a reviewed profile update, not a new name prefix.

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

Fixtures contain only the two public source Compositions; test Revision UID and
resourceVersion are explicitly synthetic parser inputs. Tests, fixture hashes
or validator PASS are not native reconciliation evidence. Do not delete a claim,
rotate a password, seed Vault or retry a bootstrap to fix a validation failure.
