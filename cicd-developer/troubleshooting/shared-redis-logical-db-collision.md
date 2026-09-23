---
name: shared-redis-logical-db-collision
description: Diagnose a non-empty logical DB or generic key-pattern collision on a shared-middleware Redis endpoint without reading Secret values or mutating Redis.
---

# Shared Redis logical DB or key-pattern collision

## Symptom and boundary

Use this playbook when an application is being attached or migrated to a
shared-middleware Redis endpoint and either:

- the intended target logical DB has `DBSIZE > 0`; or
- a declared application key pattern also matches keys already present on the
  shared endpoint.

This is a read-only Troubleshoot workflow. A non-empty DB is not evidence that
the keys belong to the application, and an empty DB is not evidence that the DB
is unallocated. Do not implement a repair, freeze the source, or start target
writers while collecting this evidence.

## Step 1. Bind the consumer to its Git and controller ownership

Resolve the target from the ArgoCD resource tree and the application repository,
then record only non-secret identity:

- the `platform.addx.io/v1alpha1 Database` claim name, namespace, UID,
  `platform.addx.io/app-slug`, `spec.app`, and `spec.engine: redis`;
- the claim's Git repository, path, and exact revision;
- the app-owned `ExternalSecret` name, namespace, UID, target Secret name,
  `secretStoreRef`, and `remoteRef.key`/property names;
- whether the generated Secret owner reference points to the expected
  ExternalSecret/controller, without reading `.data`.

The claim and ExternalSecret must belong to the same application overlay. The
canonical endpoint path is the per-app shared-middleware connection path
documented in `references/shared-middleware/README.md`. A hand-written endpoint,
cross-app remote path, missing owner, or ambiguous Git source is a STOP.

Never run `kubectl get secret ... -o yaml/json`, print Secret `.data`, print a
resolved Redis URL, or put credentials in argv. If endpoint equality across
consumers must be checked, compare normalized values in-process and emit only
`endpoint_match=true|false`. Use a mounted Secret or `REDISCLI_AUTH`; never
display the value.

## Step 2. Prove every target writer is stopped

Build the writer inventory from the exact ArgoCD tree and Git revision. For each
Deployment, Rollout, StatefulSet, Job, CronJob, worker, scheduler, and migration
consumer that can write Redis, record desired/current/ready replicas, active Job
count, CronJob suspension, Pod UID, and owner UID.

The gate is:

```text
target_writer_replicas=0 active_jobs=0 writer_pods=0
```

Any target writer replica, active Job, unsuspended CronJob, unknown consumer, or
unowned Pod is a STOP. Scaling or suspending a resource is a mutation and is not
authorized by this playbook. Source writers remain unchanged; source freeze is
explicitly forbidden during diagnosis.

## Step 3. Prove Redis mode before considering logical DBs

From an already approved diagnostic client, query only read-only commands and
sanitize output:

```bash
redis-cli --raw INFO cluster \
  | awk -F: '$1 == "cluster_enabled" {gsub(/\r/, "", $2); print "cluster_enabled=" $2}'
```

Require exactly one `cluster_enabled` value:

- `cluster_enabled=1`: Redis Cluster supports DB 0 only. Logical DB allocation
  is not an option; continue to key namespacing or a dedicated Redis proposal.
- `cluster_enabled=0`: logical DBs may be evaluated, but are not yet allocated.
- missing, malformed, or conflicting evidence: STOP.

Do not expose the endpoint in the report. Do not create a debug Pod as an
implicit part of Troubleshoot; if no approved client exists, request that
read-only evidence instead of mutating the cluster.

## Step 4. Inventory every logical DB with `DBSIZE`

For non-cluster mode, obtain the configured logical DB count with the read-only
`CONFIG GET databases` command. If managed-service policy blocks it, use an
authoritative platform configuration source; do not assume the Redis default.
Then run `DBSIZE` once for every index from `0` through `database_count - 1`:

```bash
for db in $(seq 0 "$((database_count - 1))"); do
  size=$(redis-cli --raw -n "$db" DBSIZE) || exit 1
  printf 'db=%s dbsize=%s\n' "$db" "$size"
done
```

Report only the DB index, integer `DBSIZE`, observation time, endpoint identity
hash, and client identity. Do not report key names or values. A failed or
partial loop is not an empty-state proof.

## Step 5. Count declared application patterns without emitting raw keys

Derive the expected key patterns from version-controlled application code,
configuration, and migration documentation before inspecting Redis. Do not
invent patterns by looking at live keys. For each declared pattern, use cursor
`SCAN`, consume its results locally, and emit only an aggregate count:

```bash
count=$(redis-cli --raw -n "$db" --scan --pattern "$declared_pattern" \
  | awk 'END {print NR}') || exit 1
printf 'db=%s pattern_sha256=%s count=%s\n' \
  "$db" "$declared_pattern_sha256" "$count"
```

The collector must not log, persist, or return the `SCAN` stream. Record the
pattern's Git source and a SHA-256 identifier, not raw keys. Repeat the scan if
the dataset is changing; unequal counts mean the evidence is unstable and the
decision must STOP.

`KEYS`, `DUMP`, value reads, or sampling raw key names are forbidden. A pattern
count cannot prove ownership because another application may use the same
generic prefix.

## Step 6. Reconcile durable reservations with live consumers

Read the platform-designated, version-controlled logical DB reservation source
for this exact Redis instance. Cross-check it against live consumers of the same
resolved endpoint:

- compare endpoints in-process and emit only equality booleans;
- record each consumer owner, Git revision, configured logical DB index, and
  live workload UID;
- if a DB index is stored inside a Secret or URL, extract and validate only the
  integer in-process; never print the surrounding value;
- distinguish `reserved`, `used but unregistered`, `reserved but not observed`,
  and `unassigned`.

An empty DB that is reserved remains unavailable. A non-empty unregistered DB
is an ownership conflict, not cleanup permission. If no durable reservation
source exists, logical DB allocation is not implementation-ready; create an Ops
Todo for a per-instance Git-owned allocation inventory and STOP.

## Diagnosis output

Return a compact evidence table containing:

- claim/ExternalSecret ownership and Git revision;
- target writer-zero proof;
- `cluster_enabled`;
- `DBSIZE` for every configured logical DB;
- declared-pattern SHA-256/count pairs, without raw keys;
- durable reservation versus live-consumer reconciliation;
- one of: `collision confirmed`, `ownership unknown`, `logical DB candidate`,
  or `logical DB unsupported`.

Do not claim that existing keys are stale, disposable, or owned by the migrating
application without an independent ownership contract.

## Git-first repair options

Propose exactly the smallest eligible option; do not implement it in this
Troubleshoot turn:

1. **Reserved logical DB**: eligible only when `cluster_enabled=0`, the selected
   DB has `DBSIZE=0`, no live consumer or existing reservation uses it, and the
   allocation is added atomically to the durable per-instance reservation
   inventory. The application Git change must set the same DB index. Keep target
   writers at zero until both MRs are merged, reconciled, and re-proven. Logical
   DB separation is not an authentication or ACL boundary.
2. **Key namespacing**: use a versioned, application-unique prefix declared in
   Git when logical DB allocation is unsuitable. The proposal must cover all
   readers/writers, TTL behavior, rollback, and any full-retention data rewrite
   or dual-read/dual-write phase; do not rename or delete live keys ad hoc.
3. **Dedicated Redis**: use when Redis Cluster mode, security isolation, client
   limitations, or reservation exhaustion makes shared placement unsafe. Follow
   the platform Build workflow and shared-middleware exception process; keep the
   service private and do not create an app-owned staging instance as a
   Troubleshoot shortcut.

## Hard stops and implementation approval

Never run `FLUSHDB`, `FLUSHALL`, `DEL`, `UNLINK`, `KEYS`, a write-capable Lua
script, source freeze, target scale-up, or traffic cutover under this playbook.
Do not delete target keys to manufacture an empty-state proof.

Before any Git or runtime implementation, present the selected option, exact
repositories/files, affected Redis instance and DB/prefix boundary, blast
radius, rollback, acceptance checks, and whether source freeze will later be
required. Obtain explicit user approval for that bounded implementation.
Approval of diagnosis or evidence collection is not approval to implement, and
approval of a Git MR is not approval for a later destructive runtime action.
