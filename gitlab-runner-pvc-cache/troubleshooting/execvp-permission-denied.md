# Troubleshooting: `execvp(...): Permission denied`

## Symptom

Bazel, Make, or another build tool tries to execute a compiler/script from the PVC cache and fails:

```text
execvp(/mnt/.../bin/...gcc, ...): Permission denied
```

## Fast Checks

In the failing job log or a temporary diagnostic job, print:

```bash
findmnt -T "$CACHE_ROOT" -no TARGET,FSTYPE,OPTIONS
ls -la "$CACHE_ROOT"
find "$CACHE_ROOT" \( -path "*/bin/*" -o -path "*/sbin/*" -o -path "*/libexec/*" \) -type f -print -quit
```

For the sample executable:

```bash
ls -la "$sample"
test -x "$sample" && echo executable || echo not-executable
```

## Likely Causes

1. File landed without executable bits, commonly mode `0644`.
2. Archive headers, extraction library behavior, uid/gid mapping, or NFS/EFS semantics changed the final mode.
3. Mount has `noexec` (less common, but verify with `findmnt`).

Do not assume a single root cause. In addx-things the strict facts were: files landed as `0644`; copying and chmoding made execution work; shell chmod repair fixed the pipeline.

## Fix

Run permission repair after populating the cache and while holding the prepare/download lock:

```bash
find "$cache_root" -type d ! -perm -555 -exec chmod a+rx {} +
find "$cache_root" \( -path "*/bin/*" -o -path "*/sbin/*" -o -path "*/libexec/*" \) \
  -type f ! -perm -555 -exec chmod a+rx {} +
```

Then sample-check:

```bash
sample="$(find "$cache_root" \( -path "*/bin/*" -o -path "*/sbin/*" -o -path "*/libexec/*" \) -type f -print -quit)"
[ -z "$sample" ] || [ -x "$sample" ] || exit 1
```

## Prevention

Use `recipes/ci-permission-repair.sh.tmpl` or set `{{repair_permissions}}` to `true` in `recipes/ci-prepare-locked.sh.tmpl`.
