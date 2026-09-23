# Troubleshooting: Concurrent Prepare Corrupts Cache State

## Symptom

Prepare/download intermittently fails, state JSON is malformed, files are partially extracted, or a second job observes a half-populated cache.

## Likely Cause

Multiple jobs write the same cache tree or state file concurrently. Many prepare scripts are not atomic; common patterns such as `open("state", "w")` plus JSON dump can truncate or corrupt files when two writers race.

## Fix

Serialize the shared write phase with `flock` on a lock file under the PVC:

```bash
lock_file="$CACHE_ROOT/.locks/prepare.lock"
mkdir -p "$CACHE_ROOT/.locks"
exec 9>"$lock_file"
flock -x 9
prepare_or_download
repair_permissions_if_needed
```

If `flock` is unavailable, a `mkdir` lock can be used, but do not auto-delete a supposedly stale lock by age while jobs may legitimately run for a long time.

## Prevention

Use `recipes/ci-prepare-locked.sh.tmpl` for any cache writer. Readers can be unlocked only if they do not observe partially written state.
