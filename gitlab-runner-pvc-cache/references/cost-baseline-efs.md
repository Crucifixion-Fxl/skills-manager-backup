# Cost Baseline: EFS-backed Runner Cache

Use this as a prompt for estimation, not as a price sheet.

Order-of-magnitude examples, based on EFS Standard-style capacity billing. Verify current cloud pricing before approval.

| Backend | Capacity planned | Typical actual usage | Monthly cost order |
|---------|------------------|----------------------|--------------------|
| EFS Standard | 50 GiB | 5-10 GiB | under 5 USD for addx-things-like usage |
| EFS Standard | 200 GiB | around 50 GiB | roughly 10-30 USD |

Estimate:

- Initial cache size: compressed download size plus expanded directory size.
- Growth rate: new versions retained per week.
- Concurrency: number of jobs reading/writing during peak.
- IO pattern: many small metadata operations are common for toolchains and dependency repositories.

For first rollout, prefer a conservative PVC size such as `50Gi` for toolchains, then review actual usage after a few pipelines:

```bash
kubectl -n gitlab-runner exec <debug-pod> -- du -sh /mnt/<project>
```

If the organization exposes PVC usage metrics in Prometheus/Grafana, use those instead of manual pod access.

Operational notes:

- RWX is required when jobs may run on different nodes.
- EFS/NFS metadata operations can make recursive `chmod` expensive. Keep permission repair scoped to executable directories such as `bin`, `sbin`, and `libexec`.
- Avoid storing unbounded build outputs on the cache PVC. This pattern is for reusable inputs, not artifact retention.
