# Troubleshooting: Manager Pod Still Uses Old Mount Config

## Symptom

ArgoCD shows sync completed, but new CI jobs still mount the old path or do not mount the PVC.

## Checks

Inspect the live runner manager Deployment and the generated runner config:

```bash
kubectl -n gitlab-runner get deploy
kubectl -n gitlab-runner describe deploy <runner-manager-deployment>
kubectl -n gitlab-runner get configmap | grep runner
```

Check a new job pod:

```bash
kubectl -n gitlab-runner describe pod <job-pod> | grep -A5 -B5 '<mount-path>'
```

## Likely Cause

The Helm values changed, but the manager Deployment did not roll. Existing manager pods may keep old runner configuration.

## Fix

Ask SRE/platform owner to perform a controlled rollout restart for the affected runner manager Deployment, or add a checksum annotation pattern to the chart if this recurs.

```bash
kubectl -n gitlab-runner rollout restart deployment/<runner-manager-deployment>
kubectl -n gitlab-runner rollout status deployment/<runner-manager-deployment>
```

## Prevention

After ArgoCD sync, trigger a small diagnostic job and check the mounted path before merging the business project MR.
