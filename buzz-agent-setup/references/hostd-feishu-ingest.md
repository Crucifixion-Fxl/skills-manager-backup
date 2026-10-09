# Durable Feishu root-history ingestion (schema 18)

A hostd Feishu round reads one ascending history page (at most 50 messages), atomically retains every source ID and the continuation, then admits at most eight retained sources within a five-second drain-admission budget. An already admitted round drains under the existing transport timeouts and pause fence; queued IDs do not start background dispatch and no new round bypasses pause; five seconds is not a cancellation deadline for an in-flight effect. Legacy CLI rounds retain their existing backlog refusal and explicit skip behavior.

The [official Feishu message-list contract](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/list) supports second-based `start_time` and `end_time` for chat containers, page sizes up to 50, and opaque `page_token` continuations with the same `sort_type`. The query fixes chat, ascending order, start/end, page size and root-only/card options for the whole traversal. A continuation is not an atomic snapshot or authorization proof. Exact millisecond timestamps and provider capture order at ties are retained; no cursor uses `last_timestamp + 1` or the CLI's minute-resolution display time. Subsequent completed traversals keep the existing 900-second hostd overlap.

## Durable facts and migration

Opening a valid version-17 database in `Store` validates its schema and upgrades it transactionally to 18. Existing binding, delivery, source state, outlet and console tables/rows are unchanged. New tables are:

| Table | Meaning |
| --- | --- |
| `feishu_scan` | One query scope hash, frozen start/end, next opaque token and completion bit per binding. |
| `feishu_scan_token` | Hashes of continuations already seen in that traversal, for cycle detection. |
| `feishu_ingest` | Message/chat ID, exact creation milliseconds, capture floor, durable capture sequence, attempts, retry lease, processing completion and deferred-context bit. No body, credential, card or signed payload. |

Discovery validates the complete page metadata (IDs, chat, exact timestamps, token and shape). Unsupported or malformed message bodies do not prevent retaining those positive IDs; body parsing occurs only after each fresh exact read and failure retains that source without an ACK. Page IDs, their capture sequence and continuation commit in one transaction. A failed capture, malformed page, repeated token or capacity failure advances neither. A failed continuation resets only its token to the same frozen query; retained IDs and delivery records remain. Existing retained work may drain while page discovery fails. Scope changes invalidate old tokens and preserve unfinished discovery's lower bound and pending IDs. A different chat for retained work fails closed. Protected floors and the existing reader-namespace migration checks still apply.

The queue admits at most 10,000 unfinished sources per binding and 65,536 total rows. Capacity never authorizes skipping a page. It applies backpressure and retains the last checkpoint. Completed source rows remain as deduplication tombstones until a new traversal's lower bound has passed them. A backlog that exceeds total storage capacity must be investigated; neither tokens nor floors are advanced to discard it. No live database is modified by the development tests.

Deployment must retain a consistent pre-upgrade database backup through the existing stop/drain deployment procedure. An older binary cannot open schema 18. Restoring a consistent pre-upgrade snapshot is allowed only if no external effect occurred after upgrade. After any post-upgrade effect, retain schema 18 and every new delivery/ACK record: use a forward fix or a rollback binary that supports schema 18. Never restore an older database after effects, edit `user_version`, or drop these tables to force a downgrade; losing new ACKs can duplicate effects. This document does not authorize deployment or data rollback.

## Effect and retry safety

Retained IDs are work hints. Before each effect, an exact native GET must freshly confirm the same message, chat and creation timestamp. Current normalization, identity, sender/mention policy, actual root proof and signed publish logic still run. Exact source time is passed separately for cutoff comparisons; normalized display time remains unchanged in canonical signed content, including old stale-message retries. A retained thread first processed after scan completion preserves its original capture floor for reply discovery. A missing root holds that reply; independent topics continue. A context-only source deferred by the existing reread window persists its waiting relation, so the next batch cannot send a dependent agent mention early. It must resolve or finish first.

An attempt receives a durable retry lease before its exact read (30 seconds, exponentially increasing to 300). Processing completion is recorded only after persisted existing ACK/UNKNOWN or a valid no-effect routing decision. Read, normalization, image and root failures keep the ID pending. UNKNOWN and sealed signed-delivery conflicts are never reset; retries retain the original signed timestamp and payload constraints. Crash boundaries retain either an undispatched ID or existing recoverable signed delivery state.

`feishu_since` can advance only to the fixed end of a completed traversal, after all discovered IDs are durable. Unprocessed IDs continue independently of this cursor. Existing phase checkpoint rollback on genuinely incomplete reads remains intact; the next root traversal uses the separately completed scan watermark so a partial thread read cannot permanently pin root discovery. Valid page/drain continuations use existing cooperative scheduling and shared-slot fairness. Individual deferred sources use their retry leases.

The report's `hostd.feishu_ingest` exposes only pending count, scan completion, scan failure and capacity wait. Tokens and message bodies are not reported. This wave covers ascending chat/root discovery, including inline replies; descending thread-history pagination retains its prior limits and is not claimed complete.

## Offline acceptance

`tests/test_hostd_feishu_ingest.py` exercises actual temporary SQL, Worker restarts and the low-level fake Feishu/relay transport. It covers dense timestamp pages, atomic capacity failure, query scope changes, token failure/cycles, source deletion or malformed fresh reads, ACK/UNKNOWN handling, signed-write crash recovery, budget retention, old unattempted sources, root dependency restoration, context dependencies across batches, and v17 migration preserving existing business rows. Existing hostd mapping/recovery/pause and legacy common-round suites supply the surrounding regression gates.
