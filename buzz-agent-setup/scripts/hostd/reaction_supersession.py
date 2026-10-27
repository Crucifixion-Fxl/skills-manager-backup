"""Exact signed transient add/withdraw pairs settled before any attempt.

Uses paired delivery.skipped records, never native ACKs/receipts. For this
no-effect record only, root_id is the reciprocal source ID, content_hash a
versioned commitment to both immutable sources and the complete local scope,
and attempts=0. Neither record can authorize an effect or advance a cursor.
"""
from __future__ import annotations

import hashlib
import json

import buzz_feishu_group_sync as gs
try:
    from .reaction_inbox import envelope, _tag
    from .store import StoreError, ERROR
except ImportError:
    from reaction_inbox import envelope, _tag
    from store import StoreError, ERROR

TRANSIENT = {'👀': 'GLANCE', '💬': 'Typing'}


def commitment(binding, channel, author, app, add_id, withdrawal_id):
    return hashlib.sha256(json.dumps(['reaction-no-effect/v1', binding, channel,
        author, app, add_id, withdrawal_id], separators=(',', ':')).encode()).hexdigest()


def superseded(store, binding, channel, author, app, event):
    """Replay exclusion only, including later exact-author withdrawals.

    A skipped flag alone proves nothing. Both scoped zero-attempt records
    must exist, refer reciprocally, and have no target or native receipt.
    """
    if envelope(event, channel, author) is None:
        return False
    add_id = event['id'] if event['kind'] == 7 else _tag(event, 'e')
    add = store.conn.execute("SELECT * FROM delivery WHERE binding_id=? AND agent_id=? AND source_id=? AND direction='r2f'",
        (binding, author, add_id)).fetchone()
    if add is None or not gs.HEX64_RE.fullmatch(add['root_id']):
        return False
    withdrawal = store.conn.execute("SELECT * FROM delivery WHERE binding_id=? AND agent_id=? AND source_id=? AND direction='r2f'",
        (binding, author, add['root_id'])).fetchone()
    if withdrawal is None or withdrawal['root_id'] != add_id:
        return False
    if (event['kind'] == 5 and event['id'] != withdrawal['source_id']
            and store.delivery_by_source(binding, event['id'], 'r2f', agent_id=author) is not None):
        return False  # Never hide a distinct pre-existing uncertain operation.
    digest = commitment(binding, channel, author, app, add_id, withdrawal['source_id'])
    return all(row['status'] == 'skipped' and row['attempts'] == 0
        and row['stream'] == 'relay' and row['target_id'] is None
        and row['content_hash'] == digest and store.outlet_receipt(row['id']) is None
        for row in (add, withdrawal))


def eligible(store, binding, channel, author, add, withdrawal, reaction_map):
    """Strict pair and never-attempted guard; not an authority grant."""
    if (envelope(add, channel, author) is None or add['kind'] != 7
            or envelope(withdrawal, channel, author) is None or withdrawal['kind'] != 5
            or _tag(withdrawal, 'e') != add['id']
            or _tag(withdrawal, 'p') not in (None, author)):
        return False
    emoji = gs._reaction_emoji(add['content'])
    if emoji not in TRANSIENT or gs.merged_reaction_map(reaction_map or {}).get(emoji) != TRANSIENT[emoji]:
        return False  # Custom/ordinary reactions keep their existing contract.
    for event in (add, withdrawal):
        if store.delivery_by_source(binding, event['id'], 'r2f', agent_id=author) is not None:
            return False  # Even pending/failed reservations may have attempted.
        if store.conn.execute('SELECT 1 FROM outlet_pending WHERE binding_id=? AND agent_id=? AND source_id=?',
                (binding, author, event['id'])).fetchone():
            return False
        work = store.conn.execute('SELECT attempts FROM outlet_work WHERE binding_id=? AND agent_id=? AND source_id=?',
                (binding, author, event['id'])).fetchone()
        if work is not None and work['attempts'] != 0:
            return False
    return True


def settle(store, binding, channel, author, app, add, withdrawal, original, *, reaction_map, now):
    """Caller verifies live authority first; atomically recheck before recording.

    The original signed target proves scope without materializing a root or
    touching any native reaction (including another active reference).
    """
    if (not isinstance(original, dict) or original.get('kind') != 9
            or original.get('id') != _tag(add, 'e') or _tag(original, 'h') != channel
            or _tag(add, 'p') not in (None, original.get('pubkey'))
            or not gs._nip01_event_verified(original)):
        return False
    # A degraded binding still runs recovery with freshly verified authority.
    # Requiring active here deadlocks recovery of never-dispatched pairs: the
    # failed settlement itself keeps the binding degraded. This only records
    # no-effect evidence; it neither sends a reaction nor promotes health.
    with store.transaction():
        authority = store.conn.execute("SELECT 1 FROM binding b JOIN agent_chat c ON c.binding_id=b.binding_id "
            "JOIN agent a ON a.pubkey=c.agent_id WHERE b.binding_id=? AND b.channel_id=? AND b.status IN ('active','degraded') "
            "AND c.agent_id=? AND c.chat_id=b.chat_id AND c.status='active' AND a.status='active' AND a.app_id=?",
            (binding, channel, author, app)).fetchone()
        if authority is None:
            raise StoreError(ERROR)
        if not eligible(store, binding, channel, author, add, withdrawal, reaction_map):
            return False
        digest = commitment(binding, channel, author, app, add['id'], withdrawal['id'])
        for event, peer in ((add, withdrawal), (withdrawal, add)):
            record = store.reserve_delivery(binding, event['id'], 'r2f', agent_id=author,
                source_at=event['created_at'], content_hash=digest, root_id=peer['id'], stream='relay', now=now)
            store.settle_delivery(record.id, outcome='skipped', now=now)
            store.conn.execute('UPDATE delivery SET attempts=0 WHERE id=?', (record.id,))
        if not superseded(store, binding, channel, author, app, add):
            raise StoreError(ERROR)
    return True
