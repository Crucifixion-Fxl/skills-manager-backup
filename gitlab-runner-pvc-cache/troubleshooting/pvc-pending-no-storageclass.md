# Troubleshooting: PVC Pending

## Symptom

ArgoCD PVC Application does not become Healthy, or the PVC remains `Pending`.

## Checks

```bash
kubectl -n gitlab-runner get pvc
kubectl -n gitlab-runner describe pvc <pvc-name>
kubectl get storageclass
```

## Likely Causes

1. `storageClassName` is still a placeholder.
2. The StorageClass does not exist in the target cluster.
3. The StorageClass does not support RWX.
4. Quota or provisioning permissions block volume creation.

## Fix

Use a real RWX StorageClass confirmed by SRE/platform. Keep `accessModes` as:

```yaml
accessModes:
  - ReadWriteMany
```

Do not switch to `ReadWriteOnce` for GitLab runner caches unless the runner is guaranteed to run on one node, which is usually not true.

## Prevention

Run:

```bash
bash skills/gitlab-runner-pvc-cache/validators/validate.sh <k8s-runner-dir> <argocd-dir> /dev/null
```
