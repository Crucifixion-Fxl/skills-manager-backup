# Workflow: option-a-share-existing-runner

This path modifies an existing shared runner so every job scheduled onto that runner can see the PVC. Use only for L1 public cache data when the blast radius is explicitly accepted.

Prefer `option-b-instance-runner.md` for almost every new cache. A dedicated release keeps opt-in explicit and avoids mounting the PVC onto unrelated jobs.

## Preconditions

- Cache sensitivity is L1.
- All projects that can use the runner are allowed to read and write the cache.
- Platform/SRE accepts the runner-wide blast radius.
- The runner is already dedicated enough that mounting a shared filesystem is not surprising.

## Steps

1. Add the PVC manifest in the k8s repository.
2. Add the PVC volume mount to the existing runner values override.
3. Add or update ArgoCD Application ordering so PVC sync happens before runner sync.
4. Restart or otherwise roll the runner manager if the chart does not checksum runner config changes.
5. Update only the business jobs that should actually use the cache path.

## Extra Review

Call out explicitly in the MR description:

- Which runner is affected.
- Which projects can use that runner.
- What data will be placed on the PVC.
- Why a dedicated runner release was not used.

If these cannot be answered, stop and use option B.
