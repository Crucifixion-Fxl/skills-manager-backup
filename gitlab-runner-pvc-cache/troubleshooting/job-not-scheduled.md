# Troubleshooting: Job Not Scheduled on Cache Runner

## Symptom

Pipeline job is pending or is picked by a runner that does not have the PVC mount.

## Checks

In GitLab:

- Project -> Settings -> CI/CD -> Runners
- Confirm instance/group/project runners are enabled as intended.
- Confirm the runner is online and has the tag used by the job.

In `.gitlab-ci.yml`:

```yaml
tags:
  - <runner_tag>
```

Remember GitLab tag matching is AND semantics: every tag listed by the job must exist on the same runner.

## Likely Causes

1. Typo between runner values `runners.tags` and job `tags`.
2. Project has instance runners disabled.
3. Locked runner is registered to a different project/group.
4. Runner is offline or at concurrency limit.
5. Job specifies multiple tags that no single runner has.

## Fix

Align the job tag with the runner values. For locked runners, verify the runner appears in the intended project/group settings.

## Prevention

Keep the cache runner job tag as a single capability tag unless there is a specific reason to combine tags.
