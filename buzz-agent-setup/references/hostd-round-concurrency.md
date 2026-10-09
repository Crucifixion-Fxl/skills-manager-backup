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
