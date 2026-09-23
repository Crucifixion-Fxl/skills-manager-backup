# Workflow: option-b-instance-runner

Default path for L1 public cache content. This creates a separate GitLab Runner release with its own tag and mounts an RWX PVC into job pods. It is still an instance-runner shape: the tag is not a security boundary.

## Preconditions

- Cache sensitivity is L1: public toolchain or public dependency cache.
- The team accepts that any project allowed to use instance runners and the tag may schedule onto this runner.
- Platform/SRE confirmed the target cluster has an RWX StorageClass.
- PVC capacity is estimated.
- Business CI scripts can still run locally without the PVC variable.

If any of these is false, stop and use `option-b-project-locked-runner.md`.

## Step 1: Name Things

Choose:

- `runner_tag`: for example `runner-sg-toolchain-cache`
- `pvc_name`: for example `myproject-toolchain-cache`
- `mount_path`: for example `/mnt/myproject`
- `values_file`: for example `values-override-toolchain-cache.yaml`
- `extras_dir`: for example `toolchain-cache-extras`

Validate:

- `mount_path` is not exactly `/mnt`, `/var`, `/opt`, `/tmp`, or another broad system directory.
- `mount_path` is short enough to use in CI variables.
- `pvc_name` is unique in the runner namespace.

## Step 1.5: Confirm Isolation

Confirm all are true:

- Cache content is L1.
- Hard isolation is not required.
- Instance-runner visibility is accepted.

If not, switch to `option-b-project-locked-runner.md`.

## Step 2: Update the k8s Repository

Use:

- `recipes/k8s-values-override.yaml.tmpl`
- `recipes/k8s-pvc.yaml.tmpl`

Actions:

1. Create a runner values override in `clusters/<cluster>/cicd/gitlab-runner/<values_file>`.
2. Fill the runner tag, PVC name, mount path, image registry, cache bucket, namespace, and role ARN slots.
3. Put the PVC manifest under a dedicated child directory such as `clusters/<cluster>/cicd/gitlab-runner/<extras_dir>/pvc-<pvc_name>.yaml`.
4. Use `ReadWriteMany`.
5. Use a real RWX StorageClass, never a placeholder.
6. For instance runner mode, reuse a shared instance-runner registration secret only if the platform already exposes one for this runner namespace. Otherwise ask SRE/platform for a new token secret.

Validate:

```bash
bash skills/gitlab-runner-pvc-cache/validators/validate.sh <gitlab-runner-dir> /dev/null /dev/null
```

## Step 3: Update argocd-apps

Use:

- `recipes/argocd-application-pvc.yaml.tmpl`
- `recipes/argocd-application-runner.yaml.tmpl`

Actions:

1. Add a PVC Application pointing to the k8s repository child directory that contains only PVC manifests.
2. Add a runner Application using the base runner chart plus the new values override.
3. Set PVC sync-wave to `"-10"`.
4. Set runner sync-wave to `"10"`.
5. Keep PVC lifecycle decoupled from the Helm release so chart upgrades or runner rebuilds do not delete the cache.

Validate:

```bash
bash skills/gitlab-runner-pvc-cache/validators/validate.sh /dev/null <cluster-apps-dir> /dev/null
```

## Step 4: Update the Business Project

Use:

- `recipes/ci-prepare-cache-root.sh.tmpl`
- `recipes/ci-prepare-locked.sh.tmpl` if a prepare/download step writes shared state
- `recipes/ci-permission-repair.sh.tmpl` if cached files are executed

Actions:

1. Add the runner tag to only jobs that need the PVC.
2. Add a CI/CD variable pointing into the mounted directory, for example `ADDX_T_ROOT=/mnt/addx/toolchains`.
3. Fail fast when the variable is missing in CI.
4. Run the cache-root verifier before any prepare or download step.
5. Lock prepare/download operations when concurrent jobs may write the same cache state.
6. Keep local developer scripts working when the cache variable is unset.

Validate:

```bash
bash skills/gitlab-runner-pvc-cache/validators/validate.sh <gitlab-runner-dir> <cluster-apps-dir> <project-dir>
```

## Step 5: Merge Order

1. Merge k8s.
2. Merge argocd-apps.
3. Wait for ArgoCD sync, PVC `Bound`, and runner online.
4. Merge the business project.

## Step 6: Acceptance

Run two pipelines.

First pipeline:

- Log contains a stable line such as `Cache root: <path>` or `Toolchain cache root: <path>`.
- The prepare/download step populates the PVC.

Second pipeline:

- Prepare/download step is significantly faster or logs a cache hit.
- Job does not re-download the same large content.

If executable cache content is involved, also verify at least one executable sample under `bin`, `sbin`, or `libexec` has the executable bit.
