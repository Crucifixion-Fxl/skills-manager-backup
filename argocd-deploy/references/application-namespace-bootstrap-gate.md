# Application Namespace Bootstrap Gate

Read this before merging a new or modified child Application that uses
`CreateNamespace=true`, or whose destination Namespace is created by an earlier child.

## Pre-merge checks

`CreateNamespace=true` does not bypass AppProject. The controller creates a
cluster-scoped core `Namespace`, so inspect both the exact desired AppProject in the
target cluster directory and the live AppProject:

```bash
rg -n 'name: <PROJECT>|clusterResourceWhitelist|kind: Namespace' \
  <ARGOCD_APPS_REPO>/<CLUSTER_DIR>/appproject-*.yaml

kubectl --context <KUBE_CONTEXT> -n argo-cd get appproject <PROJECT> -o yaml
```

Only continue when one of these contracts holds:

1. The Application's AppProject allows `{group: '', kind: Namespace}` (or an
   equivalent wildcard) because Namespace creation is part of that project's
   long-term ownership.
2. A lower-wave bootstrap Application in the same cluster directory uses an
   AppProject that already allows `/Namespace` and targets the same destination
   server. It must either:
   - use `CreateNamespace=true` with the exact destination Namespace required by the
     later child; or
   - render an explicit Namespace whose `metadata.name` exactly matches the required
     Namespace and whose annotations include
     `argocd.argoproj.io/sync-options: Prune=false,Delete=false`.

The explicit Namespace form may share a bootstrap whose destination Namespace is
different. For example, a bootstrap that creates `staging-flink` via
`CreateNamespace=true` can also render a protected `Namespace/flink-operator`.
Merely creating `staging-flink` is not a prerequisite for `flink-operator`.

For either bootstrap form, verify the live Application health customization includes
the exact bootstrap and dependent child names. A lower sync wave alone does not prove
that the root waits for the bootstrap to become Healthy.

## Live gate

After the bootstrap reconciles, verify the exact Namespace and bootstrap health before
allowing the dependent child to sync:

```bash
kubectl --context <KUBE_CONTEXT> get namespace <EXACT_DESTINATION_NAMESPACE>
kubectl --context <KUBE_CONTEXT> -n argo-cd get application <BOOTSTRAP_APP> \
  -o custom-columns='SYNC:.status.sync.status,HEALTH:.status.health.status'
```

Do not accept a hand-created Namespace, an unprotected explicit Namespace, a bootstrap
whose AppProject also lacks `/Namespace`, or a bootstrap that creates only a different
Namespace.

When Argo CD reports `resource /Namespace is not permitted in project ...`, first fix
project classification or add the exact authorized bootstrap. Do not add `/Namespace`
to a dedicated project or move the control-plane workload Application to wildcard
`default` merely to clear one SyncFailed. Expand a project only in a separate review
when Namespace creation is its durable responsibility, with owner, resource scope, and
cleanup plan recorded.

## Recovering an already failed wave

Merging the Git fix does not guarantee automatic recovery when the root Application
already has a `Running` operation pinned to an older revision and the dependent child
has exhausted its retries. The active root operation does not switch revisions in
place. Treat recovery as a separate, explicitly authorized operation.

First record the old root revision/phase, the child failure, and the exact merged fix
SHA. Wait for the existing bootstrap child to reconcile its source at that new SHA,
then verify the Namespace is Active and retains the tracking and delete-safety
annotations:

```bash
kubectl --context <KUBE_CONTEXT> -n argo-cd get application <ROOT_APP> -o yaml
kubectl --context <KUBE_CONTEXT> -n argo-cd get application <FAILED_CHILD> -o yaml
kubectl --context <KUBE_CONTEXT> -n argo-cd get application <BOOTSTRAP_APP> \
  -o custom-columns='REVISION:.status.sync.revision,SYNC:.status.sync.status,HEALTH:.status.health.status'
kubectl --context <KUBE_CONTEXT> get namespace <EXACT_DESTINATION_NAMESPACE> -o yaml
```

Hard gate before touching the root operation:

- bootstrap revision equals `<EXACT_NEW_SHA>` and is Synced/Healthy;
- Namespace phase is `Active`;
- an explicit Namespace still has `Prune=false,Delete=false` and the expected Argo CD
  tracking annotation;
- the requested root revision is the exact merged commit, not a moving branch name.

Stopping an operation and starting a sync are live mutations. Show the exact commands
and obtain explicit user approval before running them. After approval, terminate only
the stale root operation and sync only the exact merged SHA:

```bash
argocd app terminate-op <ROOT_APP>
argocd app sync <ROOT_APP> --revision <EXACT_NEW_SHA>
```

Do not add `--force` or `--prune`, do not patch the failed child/Application/workload
by hand, and do not directly apply the Namespace. Verify that the new root operation is
pinned to `<EXACT_NEW_SHA>`, then require bootstrap Healthy before the operator child,
and operator Healthy before the workload wave. If any gate fails, stop the sync and
report the exact status instead of bypassing the dependency chain.
