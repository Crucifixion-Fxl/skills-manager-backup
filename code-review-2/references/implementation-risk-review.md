# Functional Implementation Risk Review

Read this reference only when the diff changes functional code or production behavior. It is the default code-review check; a security, firmware, or deployment specialist does not replace it.

## Implementation Risks

Check the affected paths for:

1. **Authentication and authorization**: trusted identity at handler/RPC/job boundaries; user or tenant identity supplied in request data; missing authorization, IDOR, cross-tenant access, admin/user confusion; webhook source, signature, replay window, and idempotency.
2. **Input and injection**: structured or parameterized SQL/NoSQL/shell/path/URL/template use; SSRF, path traversal, XSS, unsafe deserialization, ReDoS; type/range constraints on uploads, rich text, URLs, redirects, filters, and sorting.
3. **Sensitive data and logs**: secrets, credentials, PII, payment/order fields in logs, errors, events, caches, or responses; server-side redaction and safe failure behavior.
4. **Consistency and concurrency**: transaction boundaries, unique constraints, idempotency, state guards, compensation; retries and concurrent callbacks that can duplicate side effects, overwrite newer state, or lose events; ordering, deduplication, replay, and recovery for caches, outbox, queues, and jobs.
5. **Performance and capacity**: N+1 queries, full scans, missing indexes or pagination, unbounded time ranges or payloads, remote calls in hot loops, unsuitable complexity or memory use, and whether batching, caching, throttling, or circuit breaking is needed.
6. **Resources, failure handling, and observability**: timeouts, cancellation, bounded retries, connection-pool limits, error classes; cleanup of files, streams, bodies, rows, timers, goroutines, and subscriptions on failure; useful logs/metrics for security rejection, throttling, degradation, and repair without leaking sensitive data.

## P0 Evidence Gate

A confirmed bug is not automatically P0. Except for separately defined deterministic gates such as plaintext secrets or a centrally blocked Chinese-literal policy, a judgment-based finding is P0 only when the current path is directly triggerable and evidence proves at least one of: cross-tenant/unauthorized access or exploitable injection; irreversible loss of funds or core data; broad unavailability of a core path; or incident-level capacity impact at the known workload scale. Without that impact evidence, classify a current functional error as P1 or a maintenance risk with correct current behavior as P2. Request missing proof such as load tests, EXPLAIN output, call paths, permission matrices, or concurrency tests.

Severity must not exceed the highest impact established by the evidence. When context is complete and shows only local or recoverable impact, lower the finding to P1/P2; do not preserve a P0 by calling the impact uncertain. Payment-related terms do not prove irreversible loss without evidence that refunds, reversal, or ledger recovery are impossible. Locks, timeouts, slow queries, and long transactions do not prove broad outage without evidence of a core path and sufficient scale or duration. A single account, tenant, or batch hitting a quota does not prove incident-level capacity impact without shared-resource exhaustion and broad impact. Unauthorized access, tenant escape, or exploitable injection directly proves a security-boundary breach. If the evidence needed to judge impact is inaccessible, conflicting, or incomplete, retain `uncertain` and use Step 4's fail-closed handling.

## Production and Compatibility

Apply this checklist when a diff affects live request paths, releases, database schema/data, scheduled or queued work, caches, third-party dependencies, permissions/routes, CI/CD, or infrastructure:

1. **Release, rollout, and rollback**: feature flags, staged rollout, tenant/region controls, kill switches, deployment approval/order/isolation, and whether rollback remains compatible with the data and messages written by the new version.
2. **Compatibility**: old clients, workers, consumers, cached data, replayed messages, defaults, nullable/required fields, enums, error codes, and state transitions.
3. **Migration and repair**: online safety, large-table locks, concurrent index creation, idempotency, bounded/resumable/throttled backfills, dry runs, progress, and failure audit; do not hide large repairs in request paths.
4. **Async work**: idempotency, duplicate/late/out-of-order delivery, poison messages, partial failure, retry storms, concurrency, batch size, acknowledgement timing, dead-letter handling, compensation, and throttling.
5. **Configuration, cache, and dependencies**: fail-fast or safe degradation for missing settings/secrets; production-suitable defaults; cache key/version/TTL/invalidation risks; external timeouts, circuit breaking, throttling, fallbacks, and degradation signals.
6. **SLO and recovery**: logs, metrics, traces, dashboards, alerts, who responds, where to find the signal, how to stop impact, how to roll back, and how to verify recovery.

## API Contracts

For API route/type changes, read section 5 of [`service-catalog-onboarding/SKILL.md`](../../../development/service-catalog-onboarding/SKILL.md). Compare actual route method/path sets with the OpenAPI/proto contract, and compare request/response field names, requiredness, and types. Route-only changes still require checking for a missing contract update. A mismatch is a blocking contract drift. Trust implementation behavior when correcting drift; do not change code to satisfy an inaccurate contract unless the behavior itself is a separately documented bug. If the repository has a `kind: API` entity, verify independent route-set and schema-set CI drift jobs exist; OpenAPI lint alone does not establish code-contract parity.

## Structure and Engineering Quality

For functional code, also read [`code-structure-design-review.md`](code-structure-design-review.md). Prefer project-local `AGENTS.md`, `CLAUDE.md`, README, architecture docs, lint and formatter configuration over generic preferences. Check naming, complex branch/state modeling, type escape hatches, error context, boundary direction, new dependency cost, generated-file synchronization, and removal of dead config/tests/docs/jobs/alerts. Do not call a style difference a confirmed defect: cite the candidate HEAD location, a concrete baseline or project rule, the consequence, and MR attribution. Keep behavior necessity separate from reuse choice; insufficient requirement evidence remains pending.
