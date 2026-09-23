# Known Gotchas

## Runner Tag Is Not a Security Boundary

An instance runner with a unique tag can still be used by any project that has instance runners enabled and knows the tag. Use group or project runner registration for hard isolation.

## Classify Cache Contents First

Do not store license keys, tokens, private keys, personal data, or customer data on a PVC cache. Split commercial compiler binaries from their license material.

## Executable Bits May Need Repair

On PVC-backed filesystems, extracted file modes can differ from expectations due to archive headers, extraction library behavior, uid/gid mapping, or NFS/EFS semantics. If cached files are executed, repair only standard executable locations:

```bash
find "$cache_root" -type d ! -perm -555 -exec chmod a+rx {} +
find "$cache_root" \( -path "*/bin/*" -o -path "*/sbin/*" -o -path "*/libexec/*" \) \
  -type f ! -perm -555 -exec chmod a+rx {} +
```

Then sample-check one executable path. Do not assume the root cause is always EFS Access Point behavior.

## Verify Mount Options

EFS-CSI usually does not mount with `noexec`, but verify:

```bash
findmnt -T "$CACHE_ROOT" -no TARGET,FSTYPE,OPTIONS
```

## Runner Manager May Need Restart

Some Helm chart/config changes do not automatically roll the manager Deployment. If job pods still use old mounts after ArgoCD sync, inspect the manager pod and consider a controlled rollout restart.

## Instance Runner Token Reuse Is Platform-Specific

In the addx-things rollout, the instance-runner variant reused the existing `gitlab-runner-token` secret because SG DevOps already exposed that shared instance-runner registration secret. Do not assume this exists in a new cluster or namespace. If the platform does not expose a shared instance token secret, request one from SRE/platform.

Locked project/group runners must use a dedicated registration token secret and must not reuse the instance-runner token.

## Sync-Wave Must Be a String

Use:

```yaml
argocd.argoproj.io/sync-wave: "-10"
```

not an unquoted number.

## Directory-Mode Application Paths Must Not Collide

The PVC Application should point to a dedicated child directory that contains only PVC manifests. Do not point it at the parent runner values directory.

## `find | head` Under `pipefail`

With `set -o pipefail`, `find ... | head -n1` can return a non-zero status due to SIGPIPE. Use `find ... -print -quit`.

## Lock Multiple Commands With an FD

Do not write this when both the prepare step and permission repair need the same lock:

```bash
flock -x "$lock_file" prepare.sh
chmod_repair.sh
```

The repair step runs outside the lock. Use an FD lock instead:

```bash
exec 9>"$lock_file"
flock -x 9
cmd1
cmd2
```

## Local Development Must Not Require PVC

Project scripts should fall back when the cache environment variable is unset. The PVC-specific verifier belongs in CI `before_script`, not in the local default path.
