# Binding round concurrency

`hostd run` accepts two process-local scheduling options:

| Option | Default | Accepted values |
| --- | --- | --- |
| `--round-workers` | 4 | Integer1..16 |
| `--round-live-reserve` | 1; automatically0 when workers=1 | Integer0..workers-1 |

An explicit reserve equal to or above workers is rejected, including
`--round-workers 1 --round-live-reserve 1`. `--round-workers 1` and an explicit
reserve0 both retain the existing single-worker behavior. Invalid combinations
fail during argument validation before registry loading or runtime-state writes.

The reviewed local8/2 setting is supplied by appending these arguments to the
existing `hostd run` invocation, retaining all existing binding, state, console
and owner configuration arguments:

```text
--round-workers 8 --round-live-reserve 2
```

This admits at most8 simultaneous synchronous binding rounds, with at most6
background rounds. A background backlog cannot consume the two live-reserved
slots. Live rounds may use all8 slots; reservation is a background cap rather
than a separate executor. The actual dedicated `hostd-round` executor gets its
capacity from the same validated `RoundSlots` instance. DNS/default-executor
isolation is preserved.

The existing trusted live-event promotion rules, per-binding locks, queue
cancellation, final pause/authorization fence and joined shutdown remain in
force. After eight consecutive live admissions, the existing fairness rule
admits an eligible waiting background round when the background cap allows it.
An in-flight round is not preempted; one busy binding remains serial. When all
slots contain live work, later live arrivals still queue.

Within a round that includes Feishu work, member checks run first, then Feishu
ingestion precedes Buzz outlet work. Setup, binding claims, pending-target
recovery and each phase's independent checkpoint/rollback/persistence still
apply. Without Feishu work, the existing member-before-Buzz order remains.
This avoids making newly queued inbound work wait behind that round's outlet
history; it neither preempts an in-flight round nor proves the end-to-end
latency target. Object-root causality and UNKNOWN no-resend rules are unchanged.

These options do **not** change HTTP concurrency/rate policy. The same shared
Scheduler/HttpPool retain chat5/second, app50/second, app1000/minute and burst1,
including429 handling/backoff. More round threads do not authorize more API
requests, bypass owner/root/receipt checks or retry UNKNOWN writes. SQLite
durable writes remain serialized by the existing ledger.

Configure deliberately per host: CPU pressure, memory, full swap, API limits or
database contention may limit the benefit. Compare existing `round_queued`,
`round_started` and `round_finished` timing evidence after a reviewed rollout.
Defaults remain4/1 for deployments that do not opt in. Reverting the invocation
to the default flags is a scheduling rollback, not a ledger/cursor reset.

Offline validation: `test_hostd_round_concurrency` exercises CLI→real Hostd→real
ThreadPoolExecutor wiring, eight simultaneous blocked IO workers,6+2 reservation,
binding serialization, queued pause and shutdown drain. `test_hostd_round_slots`
pins admission fairness/cancellation; existing console pause, shutdown, scheduler
and HTTP suites cover the unchanged surrounding gates. These tests make no live
Feishu or relay calls. Changing flags does not by itself constitute deployment
or post-rollout latency acceptance.

## Registration while orphan withdrawals wait

Registration readback distinguishes connected own-bot identity from completed
business delivery. When fresh signed verification finds only retained kind-5
withdrawals whose original reaction is unavailable, registration may be ready
while the outlet still reports pending. This observation lasts at most 300
seconds and is invalidated by authority, app, retained-work or relevant-receipt
changes. Ordinary pending/UNKNOWN deliveries and incomplete verification still
block readiness. The withdrawal stays in its durable workset; no ACK, approval,
retry permission or completed business result is inferred from registration.

Whole-binding business degradation does not block registration when this agent's
current scoped Worker observation passes the same readiness checks. The Worker
must have run and remain alive, and the required readers, relay and own outlet
must still be connected. Paused, conflicting, retired and pending bindings remain
blocked; aggregate degradation never makes ordinary pending or UNKNOWN work
ready. A normal verified cache also requires no current own outlet work or
legacy pending rows for this exception; work arriving after verification blocks
it. The orphan-only observer retains its exact workset check. An old report
without the scoped observation cannot grant this exception.

The [own-home admission reference](hostd-install.md#own-home-admission-and-original-pending-records)
retains the owner-approval and original RESERVED/UNKNOWN readback rules.

For an already approved channel JOIN, the full effect readback uses that same
exact fresh registration proof when the existing binding is degraded. It still
verifies current approval/agent/owner/app/target, protected files, native
subscription and both membership surfaces. After awaited checks, the final grant
transaction invokes the Registrar's same synchronous current observation again,
including the real Worker's authority and complete retained-work/receipt scope.
No await occurs inside that transaction, and concurrent ledger writers cannot
change its SQL snapshot. This avoids a separate, potentially incomplete copy of
the Worker's scope algorithm. A transition back to active cannot discard an
already required scoped check, including the normal proof’s no-current-work
condition; the synchronous observer receives that explicit obligation. A readback
that starts and stays active keeps its
previous path. This exception neither marks the whole binding healthy nor
settles retained business work or retries UNKNOWN effects.
