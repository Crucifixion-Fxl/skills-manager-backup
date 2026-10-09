"""Bounded signed reaction evidence, never a delivery or authorization grant.

The central Store owns path checks, durable transactions and schema admission.
Only the fixed native-supported emoji vocabulary and identifier-only tags are
retained. Unsupported extensions continue through the normal relay reader;
we never strip signed fields and claim the resulting event was verified.
"""
from __future__ import annotations

import json
import sqlite3

import buzz_feishu_group_sync as gs
try:
    from .store import StoreError, ERROR, ident, hexid, stamp
except ImportError:
    from store import StoreError, ERROR, ident, hexid, stamp

FIELDS = frozenset(('id', 'pubkey', 'created_at', 'kind', 'tags', 'content', 'sig'))
CONTENTS = frozenset(gs.DEFAULT_REACTION_MAP) | frozenset(x + '\ufe0f' for x in gs.DEFAULT_REACTION_MAP if x != '+')


def envelope(event, channel, author):
    """Return only an intact verified, small, non-secret reaction envelope."""
    try:
        ident(channel); hexid(author)
        if (not isinstance(event, dict) or type(event.get('kind')) is not int
                or event['kind'] not in (5, 7) or event.get('pubkey') != author
                or type(event.get('created_at')) is not int or not 0 <= event['created_at'] < 2 ** 63
                or not isinstance(event.get('content'), str)
                or event['content'] not in (CONTENTS if event['kind'] == 7 else {''})):
            return None
        tags = event.get('tags')
        if not isinstance(tags, list) or not 1 <= len(tags) <= 4:
            return None
        seen = set()
        for tag in tags:
            if not isinstance(tag, list) or len(tag) != 2 or not all(isinstance(v, str) for v in tag):
                return None
            key, value = tag
            if key in seen or key not in ('e', 'p', 'h', 'k'):
                return None
            seen.add(key)
            if key in ('e', 'p'): hexid(value)
            elif key == 'h' and value != channel: return None
            elif key == 'k' and value != ('9' if event['kind'] == 7 else '7'): return None
        if 'e' not in seen or not gs._nip01_event_verified(event):
            return None
        # Extra transport fields are unsigned and must never enter SQLite.
        clean = {key: event[key] for key in FIELDS}
        encoded = json.dumps(clean, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        return encoded if len(encoded.encode()) <= 2048 else None
    except (ValueError, TypeError, KeyError, RecursionError):
        return None


def retain(store, binding, channel, author, event):
    """Durably retain before returning from a verified owned feed callback.

    Capacity/storage failure is explicit: caller must not report this frame as
    retained. No eviction, retries of native writes, or feed replacement here.
    A stock no-h event is only a target hint until outlet proves its channel.
    """
    encoded = envelope(event, channel, author)
    if encoded is None:
        return False
    ident(binding)
    try:
        with store.transaction():
            row = store.conn.execute('SELECT channel_id FROM binding WHERE binding_id=?', (binding,)).fetchone()
            if row is None or row['channel_id'] != channel:
                raise StoreError(ERROR)
            prior = store.delivery_by_source(binding, event['id'], 'r2f', agent_id=author)
            if prior is not None and prior['status'] in ('acked', 'removed'):
                return True
            # Match Worker._run_outlets' canonical initial_since, atomically
            # with capture. A later cursor/floor cannot manufacture eligibility.
            existing = store.conn.execute('SELECT channel_id,event_json FROM reaction_inbox WHERE binding_id=? AND agent_id=? AND source_id=?',
                (binding, author, event['id'])).fetchone()
            if existing is not None:
                if existing['channel_id'] != channel or existing['event_json'] != encoded:
                    raise StoreError(ERROR)
                return True
            floors = list(store.conn.execute(
                "SELECT value_int FROM state_scalar WHERE binding_id=? AND field IN ('floor','buzz_floor')", (binding,)))
            if len(floors) != 2:
                return False  # No initialized canonical baseline, no exemption.
            floor = max(stamp(r[0]) for r in floors)
            if event['created_at'] < floor and prior is None:
                return False
            if foreign_reference(store, binding, channel, author, event):
                return True  # Proven negative scope only; never a native ACK.
            store.conn.execute('INSERT OR IGNORE INTO reaction_inbox VALUES(?,?,?,?,?,?)',
                               (binding, author, event['id'], channel, encoded, floor))
        return True
    except sqlite3.Error:
        raise StoreError(ERROR) from None


def pending(store, binding, channel, author):
    ident(binding); ident(channel); hexid(author)
    rows = store.conn.execute('SELECT * FROM reaction_inbox WHERE binding_id=? AND agent_id=?',
                              (binding, author)).fetchall()
    if len(rows) > 256:
        raise StoreError(ERROR)
    events = []
    for row in rows:
        try:
            event = json.loads(row['event_json'])
            if (row['channel_id'] != channel or row['source_id'] != event.get('id')
                    or envelope(event, channel, author) != row['event_json']
                    or (event['created_at'] < stamp(row['captured_floor']) and
                        store.delivery_by_source(binding, event['id'], 'r2f', agent_id=author) is None)):
                raise ValueError
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise StoreError(ERROR) from None
        events.append(event)
    return events


def _tag(event, name):
    tags = [t for t in event.get('tags', []) if isinstance(t, list) and t[:1] == [name]]
    return tags[0][1] if len(tags) == 1 and len(tags[0]) == 2 else None


def foreign_reference(store, binding, channel, author, event):
    """A bounded negative cache can only exclude an exact own reaction/ref.

    The source target hash commits to the signed original previously read.
    This is never a delivery receipt, grant, or proof of native absence.
    """
    if envelope(event, channel, author) is None:
        return False
    source = event['id'] if event['kind'] == 7 else _tag(event, 'e')
    row = store.conn.execute('SELECT * FROM reaction_foreign WHERE binding_id=? AND agent_id=? AND source_id=? AND channel_id=?',
        (binding, author, source, channel)).fetchone()
    if row is None:
        return False
    if (row['foreign_channel'] == channel or (event['kind'] == 7 and row['target_id'] != _tag(event, 'e'))
            or store.delivery_by_source(binding, event['id'], 'r2f', agent_id=author) is not None
            or store.delivery_by_source(binding, source, 'r2f', agent_id=author) is not None):
        return False  # An existing uncertain/real operation is never discarded.
    ident(row['foreign_channel']); hexid(row['target_id']); stamp(row['observed_at'])
    return True


def reject_foreign(store, binding, channel, author, event, original, *, now):
    """Save only negative metadata after an exact signed foreign original read.

    Old negative metadata may be evicted at the hard bound. Losing it only
    leaves a future withdrawal unresolved; it never permits a native effect.
    Pending/UNKNOWN evidence is not evicted.
    """
    if envelope(event, channel, author) is None or event['kind'] != 7:
        return False
    try:
        foreign = _tag(original, 'h')
        ident(foreign); stamp(now)
        if (original.get('kind') != 9 or original.get('id') != _tag(event, 'e')
                or foreign == channel or not gs._nip01_event_verified(original)):
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    with store.transaction():
        binding_row = store.conn.execute('SELECT channel_id FROM binding WHERE binding_id=?', (binding,)).fetchone()
        if binding_row is None or binding_row['channel_id'] != channel:
            raise StoreError(ERROR)
        if store.delivery_by_source(binding, event['id'], 'r2f', agent_id=author) is not None:
            return False
        existing = store.conn.execute('SELECT 1 FROM reaction_foreign WHERE binding_id=? AND agent_id=? AND source_id=?',
            (binding, author, event['id'])).fetchone()
        if existing is None:
            for clause, args, limit in [('binding_id=? AND agent_id=?', (binding, author), 256), ('1', (), 8192)]:
                count = store.conn.execute('SELECT count(*) FROM reaction_foreign WHERE '+clause, args).fetchone()[0]
                if count >= limit:
                    store.conn.execute('DELETE FROM reaction_foreign WHERE rowid IN (SELECT rowid FROM reaction_foreign WHERE '+clause+
                        ' ORDER BY observed_at,source_id LIMIT ?)', (*args, count-limit+1))
            store.conn.execute('INSERT INTO reaction_foreign VALUES(?,?,?,?,?,?,?)',
                (binding, author, event['id'], channel, original['id'], foreign, now))
        if not foreign_reference(store, binding, channel, author, event):
            raise StoreError(ERROR)
        forget(store, binding, author, event['id'])
    return True


def forget(store, binding, author, source):
    """Caller prunes after a settled ledger receipt or proven foreign target."""
    ident(binding); hexid(author); hexid(source)
    with store.transaction():
        store.conn.execute('DELETE FROM reaction_inbox WHERE binding_id=? AND agent_id=? AND source_id=?',
                           (binding, author, source))


def causal_order(events):
    """Signed withdrawals follow their own author's retained create, even if
    arrival or same-second ID sorting reversed them. No cross-author edge.
    """
    by_id = {event['id']: event for event in events}
    result, seen = [], set()
    for event in sorted(by_id.values(), key=lambda ev: (ev['created_at'], ev['id'])):
        if event['kind'] == 5:
            refs = [t[1] for t in event.get('tags', []) if len(t) >= 2 and t[0] == 'e']
            parent = by_id.get(refs[0]) if len(refs) == 1 else None
            if parent and parent['kind'] == 7 and parent['pubkey'] == event['pubkey'] and parent['id'] not in seen:
                result.append(parent); seen.add(parent['id'])
        if event['id'] not in seen:
            result.append(event); seen.add(event['id'])
    return result
