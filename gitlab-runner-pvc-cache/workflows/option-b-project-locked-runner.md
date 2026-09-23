# Workflow: option-b-project-locked-runner

Strong-isolation path for L2 internal low-sensitive cache content, such as internal SDK binaries or closed-source compiler binaries. L3 secrets still must not be stored on PVC.

This path was not the path used by addx-things, so treat first use as an untested variant and require full GitLab and ArgoCD verification.

## Preconditions

- Cache sensitivity is L2.
- L3 content such as license keys, tokens, private keys, personal data, and customer data is excluded.
- Hard isolation is required.
- Platform/SRE can provide a project or group runner registration token.
- Platform/SRE agrees to use a dedicated token secret and separate PVC.
- Business CI scripts can still run locally without the PVC variable.

## Step 1: Name Things

Choose names as in `option-b-instance-runner.md`, but make the tag clearly project/group scoped, for example `runner-sg-myproject-cache-locked`.

## Step 1.5: Confirm Isolation

Confirm:

- Target GitLab project or group is known.
- Runner will be registered for only that project/group.
- Runner configuration will not reuse the instance runner token.
- Cache does not contain L3 material.

If cache is L1 and hard isolation is not required, use `option-b-instance-runner.md`.

## Step 2: Update the k8s Repository

Use:

- `recipes/k8s-values-override-locked.yaml.tmpl`
- `recipes/k8s-pvc.yaml.tmpl`

### Step 2a: Obtain Locked Runner Registration Token

1. Go to the target GitLab project or group: Settings -> CI/CD -> Runners.
2. Create a project or group runner.
3. Configure the intended tag and protected-ref behavior.
4. Copy the one-time registration token.
5. Hand it to SRE/platform and ask for a K8s Secret named `{{locked_runner_token_secret}}` in the runner namespace, preferably sourced from Vault or ExternalSecret.
6. Verify the Secret exists before deploying the Application.

Do not commit the token value to any repository.

Required differences from instance mode:

- Use a dedicated token secret.
- Set runner locked behavior according to the platform chart's supported fields.
- Prefer protected-ref access when the cache should only be writable from protected refs.
- Avoid reusing the instance runner token secret.

Validate the values file explicitly contains the dedicated secret reference and does not contain the standard instance-token secret name.

## Step 3: Update argocd-apps

Use the same Application templates as instance mode, but choose names that distinguish the locked release.

The PVC Application still uses sync-wave `"-10"` and the runner Application still uses `"10"`.

## Step 4: Update the Business Project

Use the dedicated locked runner tag only on jobs that need this cache. Keep the same cache-root verification and locking patterns as instance mode.

## Step 5: Merge Order

Same as instance mode:

1. k8s
2. argocd-apps
3. wait for ArgoCD sync, PVC `Bound`, and runner online
4. business project

## Step 6: Acceptance

In addition to the normal two-pipeline acceptance:

- Confirm the runner appears in the target project/group settings and is online.
- Confirm the runner is not available to an unrelated project.
- Trigger a job from the target project with the tag and confirm it schedules to this runner.
- Trigger or inspect an unrelated project with the same tag and confirm it cannot use this runner.
