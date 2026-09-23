---
name: gitlab-runner-pvc-cache
description: Guide developers through configuring RWX PVC-backed persistent file caches for GitLab Kubernetes runner jobs. Use when CI repeatedly downloads or rebuilds large files across fresh Kubernetes executor pods and GitLab S3 cache is not enough because the cache needs POSIX filesystem semantics such as flock, symlinks, executable bits, or direct directory reuse. Forces cache sensitivity classification and runner isolation choice because runner tags are not security boundaries.
---

# gitlab-runner-pvc-cache

## Description

Use this skill when a GitLab CI job running on a Kubernetes executor needs a persistent shared filesystem across fresh job pods: toolchains, dependency repositories, model weights, Bazel externals, or other large files that are expensive to download or rebuild.

This skill does not replace `gitlab-ci` for general `.gitlab-ci.yml` design, `gitlab-instance-runners` for normal runner selection, `cicd-developer` for application StatefulSet PVCs, or `bazel-remote-cache` for BuildBuddy/Bazel remote cache.

Do not use this skill when:

- Cache content is small, roughly under 100 MB, and re-downloading fits the job time budget.
- Cache state is only needed within one pipeline; use GitLab `cache:` or artifacts instead.
- Multiple cache writers cannot be serialized safely.
- Cache content contains L3 sensitive material such as tokens, private keys, license key files, personal data, or customer data.

## Required Triage

Before recommending a workflow, answer these questions in order.

1. Is the data large and reused across jobs?
   - No: use normal artifacts or GitLab cache.
   - Yes: continue.
2. Does the data require POSIX filesystem semantics such as `flock`, symlinks, executable bits, or direct directory reuse?
   - No: prefer GitLab S3 cache or artifacts.
   - Yes: use this skill.
3. Will cached files be executed, such as compilers, interpreters, or scripts?
   - Yes: include permission repair and sample verification.
   - No: mount verification and locking may be enough.
4. Classify cache sensitivity:
   - L1 public: open-source toolchains or public dependency cache. Instance runner can be acceptable.
   - L2 internal low-sensitive: internal SDK binaries, closed-source compiler binaries, commercial compiler body without license keys. Use group/project locked runner.
   - L3 sensitive: license keys, tokens, API keys, private keys, personal data, customer data. Do not store on PVC; use CI/CD Variables, K8s Secret, Vault, or a license server.
5. Decide isolation:
   - Runner tag is not a security boundary. Any project that can use the runner and knows the tag may schedule a job onto it.
   - Hard isolation requires a group runner or project runner with a dedicated registration token and a dedicated PVC/namespace boundary.

## Workflow Routing

| Situation | Use |
|-----------|-----|
| L1 public cache, instance runner visibility accepted | `workflows/option-b-instance-runner.md` |
| L2 internal cache or hard isolation required | `workflows/option-b-project-locked-runner.md` |
| L1 public cache intentionally shared by changing an existing runner | `workflows/option-a-share-existing-runner.md` |
| Decommission / teardown | Not covered yet; do a manual review of PVC data retention, runner users, and reverse rollout order |

Read only the selected workflow. Load references or troubleshooting files only when the workflow asks for them or symptoms match.

## Three-Repository Shape

Most implementations touch three repositories in this order:

| Order | Repository | Change |
|-------|------------|--------|
| 1 | `k8s` | Runner values override and PVC manifest |
| 2 | `argocd-apps` | Application for PVC and Application for runner release |
| 3 | Business project | Job tags, mount verification, optional lock wrapper, project variable |

The `k8s` and `argocd-apps` changes must merge and sync first. The PVC should be `Bound`, the runner should be online, and only then should the business project change be merged. If the project change lands first, jobs should fail fast rather than silently writing cache data into a pod-local filesystem.

## Pre-Merge Validation

Run the bundled validator before asking for review:

```bash
bash skills/gitlab-runner-pvc-cache/validators/validate.sh \
  <k8s-repo-path>/clusters/.../cicd/gitlab-runner \
  <argocd-apps-repo-path>/<cluster-dir> \
  <business-project-repo-path>
```

MVP validation catches mechanical mistakes: unreplaced placeholders, PVCs that are not RWX, and ArgoCD sync-wave annotations that are not quoted strings. Additional checks report runner tag and mount-path alignment when enough files are available.

When changing the validator itself, run:

```bash
bash skills/gitlab-runner-pvc-cache/validators/test/run-tests.sh
```

## Common Symptoms

| Symptom | Read |
|---------|------|
| `execvp(...): Permission denied` from cached compiler/script | `troubleshooting/execvp-permission-denied.md` |
| New jobs still use the old mount path after values changed | `troubleshooting/manager-pod-not-using-new-mount.md` |
| PVC remains `Pending` | `troubleshooting/pvc-pending-no-storageclass.md` |
| Job is pending or not picked by the new runner | `troubleshooting/job-not-scheduled.md` |
| Cache state is corrupt after concurrent prepare | `troubleshooting/concurrent-prepare-corrupts-state.md` |
| PVC contains unexpected files or another project appears to write to it | `troubleshooting/unexpected-pvc-writers.md` |

## Rules

- Do not put L3 sensitive material on a shared PVC.
- Do not claim a runner is project-private just because it has a unique tag.
- Do not mount a PVC onto an existing broad shared runner unless the cache is L1 and the blast radius is explicitly accepted.
- Do not merge the business project change before ArgoCD shows the PVC and runner are healthy.
- Do not require local developer scripts to have the PVC environment variable set. Local paths must continue to work.

## Examples

### Bad Example

```yaml
# Wrong: a runner tag is treated as an isolation boundary, and a license file is
# stored on the shared cache filesystem.
variables:
  LICENSE_FILE: /mnt/toolchains/vendor/license.key

job:
  tags:
    - runner-sg-toolchain-cache
  script:
    - cp "$LICENSE_FILE" ~/.vendor/license.key
```

### Good Example

```yaml
# Correct: public toolchains use the PVC cache, mutable prepare work is
# serialized, and secrets stay outside the PVC.
variables:
  ADDX_T_ROOT: /mnt/addx/toolchains
  VENDOR_LICENSE_SERVER: "$VENDOR_LICENSE_SERVER"

prepare-toolchain:
  tags:
    - runner-sg-toolchain-cache
  script:
    - test -d "$ADDX_T_ROOT"
    - flock -x "$ADDX_T_ROOT/.prepare.lock" bash ci/ci_prepare_cache_root.sh
```
