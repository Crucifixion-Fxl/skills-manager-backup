# Frozen fixture: example/app (project 9999)

Authoritative for offline evals. Do not call live GitLab. Do not copy these numbers into SKILL.md.

Window: 2026-09-14 00:00 UTC → 2026-09-19 00:00 UTC. Filter: pipeline `created_at`.

## Pipelines

| status | n |
|---|---:|
| success | 12 |
| failed | 10 |
| canceled | 2 |

Successful MR wall-clock median 22 min, p90 31 min. Queue p50 8s, p90 15s.

## Jobs (analysis unit)

| job | n | blocking fail | allow_failure fail | success p50 | p90 | notes |
|---|---:|---:|---:|---:|---:|---|
| test-web | 22 | 8 | 0 | 14.0 min | 16.5 min | 6 `runner_system_failure` eviction; 2 `script_failure` |
| test-api | 22 | 4 | 0 | 13.0 min | 14.5 min | 3 eviction; 1 `script_failure` same SHA later success |
| gate-review | 20 | 9 | 0 | 3.5 min | 6.0 min | 5 tooling crash exit 80; 4 incomplete review exit 94 |
| lint-colors | 20 | 0 | 16 | 1.0 min | 1.2 min | `allow_failure=true`; hardcoded color |

Critical path last job on successful MR pipelines: `gate-review`.

## Representative links

- eviction: https://gitlab.addx.ai/example/app/-/jobs/101
- script fail then retry pass: https://gitlab.addx.ai/example/app/-/jobs/201
- gate exit 80: https://gitlab.addx.ai/example/app/-/jobs/301
- allow_failure lint: https://gitlab.addx.ai/example/app/-/jobs/401

Live yaml at `sha=aaa111` has `retry.when: runner_system_failure` only. No path filters. `test-web` serial, no cache.

## Trace snippet (must redact)

```
ERROR: Job failed: exit 80
Authorization: Bearer <redacted-jwt>
X-Token-Header: <redacted>
```
