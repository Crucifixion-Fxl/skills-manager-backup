# Withdrawn transient reactions before the first attempt

An agent can publish `👀` or `💬` and withdraw it before its outlet gets a turn.
Replaying both effects later makes a completed agent appear to start typing
again. The own outlet now settles that exact pair without a native mutation
when **both** source events have never been attempted.

This applies only to the default `👀 → GLANCE` and `💬 → Typing` mappings.
Ordinary reactions and custom mappings retain their existing behavior. There
is no age/TTL assumption: an intact, verified same-author kind5 must reference
the exact kind7 ID. Both bounded envelopes must pass the existing signature,
channel and tag checks. The signed original kind9 must prove the same channel
and target; an optional `p` tag must match the relevant author. Arrival order
and same-second ID order do not change the proof.

The selected outlet first verifies its current owner, app, signed policy,
local grant, binding and membership. It then rechecks the local grant and
never-attempted conditions inside the terminal transaction. Any existing
source delivery row (including pending, failed or UNKNOWN), legacy pending
record, or positive scheduler attempt count excludes cancellation. An
already-attempted orphan withdrawal also remains on the normal recovery path.
No authority or native absence is inferred from queue metadata.

The existing `delivery.skipped` state expresses **no effect**, not delivery:

- One record for each immutable source, atomically committed together with
  removal of the corresponding inbox and scheduling hints.
- `attempts=0`, `target_id=NULL`, and no `outlet_receipt`.
- For these versioned no-effect records only, `root_id` is the counterpart's
  source ID. It is not a native target or ordinary thread mapping.
- Both `content_hash` values commit to the version marker, binding, channel,
  author, own app, add ID and withdrawal ID.
- No ACK, removed status, native POST/DELETE, or replay cursor advancement.

Replay exclusion checks both reciprocal records and their complete commitment,
zero attempts, empty target and absent receipt. A skipped flag alone is never
sufficient. A later same-author signed withdrawal of this exact never-created
add is also a no-effect event; a distinct pre-existing delivery for that later
withdrawal is not discarded. Lost native POST/DELETE responses continue through
the original complete own-app readback rules; absence cannot ACK an uncertain
create, and no write is repeated to compensate for uncertainty.

Cancellation changes only the selected work unit; it does not increase
`max_events`, add a queue-wide network sweep, or change text-lane priority and
fairness. A successful pair commits the selected scheduling turn in the same
transaction as the pair and cleanup, so continuous new pairs cannot bypass
the eighth-turn aging opportunity for due UNKNOWN/readback work. This advances
only fairness accounting; both source attempt counts remain zero. It never deletes an existing emoji, so other active references,
other agents/apps and human reactions remain untouched. This is not a new
reference-count implementation or a fix for all scheduling latency.

The terminal local ledger commits to previously verified event IDs; it does
not retain the full signed envelopes after cleanup. A test observer requiring
cryptographic lifecycle evidence must separately retain bounded intact signed
kind7/kind5 envelopes while available, rather than infer signatures from
these local metadata. Point-in-time equality, a no-effect settlement, and an
observed native add/remove lifecycle are separate outcomes.
