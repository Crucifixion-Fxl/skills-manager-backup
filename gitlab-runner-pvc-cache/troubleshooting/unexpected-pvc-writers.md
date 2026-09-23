# Troubleshooting: Unexpected Files on the PVC

## Symptom

The PVC contains files from another project, unknown directories, or cache content changes outside the expected jobs.

## Likely Cause

An instance runner with the same tag was used by another project. Runner tags are not security boundaries.

## Checks

1. Search GitLab projects for the runner tag if API access is available.
2. Inspect runner pod labels:

```bash
kubectl -n gitlab-runner get pods --show-labels | grep '<runner-or-cache-tag>'
```

3. Check job pod annotations/labels such as `ci.gitlab.com/project-name`, `ci.gitlab.com/job-id`, and `ci.gitlab.com/pipeline-id` if configured.

## Fix

- For L1 public cache, decide whether shared writes are acceptable and clean the PVC if needed.
- For L2 content, stop using instance runner mode. Move to group/project locked runner and a new PVC.
- Rotate or archive the PVC if contamination may affect correctness.

## Prevention

Use `option-b-project-locked-runner.md` whenever cache contents are not safe for broad sharing.
