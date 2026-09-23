# TDD Platform — Dev References

Reference material for developers using the A4x TDD platform (L2.DevLink intercept, async coloring, side-effect sandbox). Foundation built in Phase 2a (2026-05); Phase 2b landed the first real-service pilot (personalization-engine on us-staging) and expanded the `tdd-demo` reference stack to **all three staging clusters (us / eu / cn)**. Full implementation history: [DEV/k8s docs/plans/2026-05-09-tdd-platform-impl-notes.md](https://gitlab.addx.ai/DEV/k8s/-/blob/master/docs/plans/2026-05-09-tdd-platform-impl-notes.md).

> **Current location (Phase 2b, 2026-05-28+)**: the `tdd-demo` namespace runs on **us-eks-staging / eu-eks-staging / cn-eks-staging**. The demo was migrated off the old `us-eks` tech-service cluster (account 002) — that namespace no longer exists there. The demo services (entitlement / notification / payment / redpanda / mailpit / traffic-manager) are identical across the three clusters; only the cluster ARN + Harbor registry differ.

## Files

- [intercept-onboarding.md](intercept-onboarding.md) — how a developer plugs into the staging `tdd-demo` and runs an interactive L2.DevLink telepresence intercept (telepresence + kubectl + `addx.dev-id` header), on any of the three staging clusters.
- [pitfalls.md](pitfalls.md) — non-obvious traps from the Phase 2a wireup (W1–W4) **plus** the Phase 2b multi-region rollout. Each is something that silently bites anyone copying the demo into a new service or a new cluster. Cross-link from `troubleshooting/`.

## Two modes of L2.DevLink — don't confuse them

| Mode | What | Who runs it | Header | Where |
|---|---|---|---|---|
| **Interactive intercept** | Reverse-tunnel cluster traffic to a handler on your laptop | a dev, manually | `X-Addx-Dev-Id` (routing) | [intercept-onboarding.md](intercept-onboarding.md) |
| **Automated test** | `testbase` baggage propagation through the real cluster chain, in CI | CI (`l2-devlink-staging-{us,eu,cn}` jobs) | `addx.run-id` baggage + `X-Addx-Run-Id` (tracing) | tdd-platform-demo `.gitlab-ci.yml`; PE `test-l2-devlink` |

Both ride the same telepresence + agent-injector + W3C-baggage substrate but answer different questions (debug-on-laptop vs regression-gate). The automated per-region CI path was added 2026-05-28 and is the durable proof that L2.DevLink works on each cluster. The cross-language (Go→Java payment), full-chain (order→Kafka→notification→mailpit), and gRPC baggage tests all pass on all three regions — what Phase 2a left as "planned cross-language validation" is now done in CI.

## Copy-the-demo-into-a-real-service companion

When a real business service adopts this (PE was the first), the migration playbook + its 7 hard-won pitfalls live in [DEV/k8s docs/plans/2026-05-24-staging-us-migration-playbook.md](https://gitlab.addx.ai/DEV/k8s/-/blob/master/docs/plans/2026-05-24-staging-us-migration-playbook.md). Read it alongside [pitfalls.md](pitfalls.md).

## Routing

These docs are not workflow templates and not troubleshooting playbooks — they are reference / onboarding material, intentionally outside `workflows/` and `troubleshooting/`. Consult them manually when a dev asks "how do I run intercept against tdd-demo" or "what was that Kafka coloring / cn-firewall gotcha".
