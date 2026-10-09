"""Bounded unresolved outlet source IDs, never payloads or delivery authority.

A failed event pins the replay boundary before a later independent ACK can
advance it. Missing relay events stay unresolved; no absence implies success.
"""
from __future__ import annotations
import sqlite3
import buzz_feishu_group_sync as gs
try:
    from .store import StoreError, ERROR, ident, hexid, stamp
except ImportError:
    from store import StoreError, ERROR, ident, hexid, stamp


def pending(store, binding, channel, author):
    ident(binding); ident(channel); hexid(author)
    actual = store.conn.execute('SELECT channel_id FROM binding WHERE binding_id=?', (binding,)).fetchone()
    if actual is None or actual['channel_id'] != channel:
        raise StoreError(ERROR)
    rows = store.conn.execute('SELECT * FROM outlet_pending WHERE binding_id=? AND agent_id=? ORDER BY source_at,source_id',
                              (binding, author)).fetchall()
    if len(rows) > 256 or any(row['channel_id'] != channel for row in rows):
        raise StoreError(ERROR)
    return [dict(row) for row in rows]


def remember(store, binding, channel, author, event, *, floor):
    """Caller has attempted normal validated delivery; this grants no effect."""
    ident(binding); ident(channel); hexid(author); stamp(floor)
    try:
        hexid(event['id']); stamp(event['created_at'])
        hs = [t for t in event['tags'] if t[:1] == ['h']]
        if (event['pubkey'] != author or type(event['kind']) is not int or event['kind'] not in (9, 40003, 7, 5)
                or not gs._nip01_event_verified(event)
                or not (len(hs) == 1 and len(hs[0]) >= 2 and hs[0][1] == channel
                        or not hs and event['kind'] in (7, 5))):
            raise ValueError
        direction = 'b2f' if event['kind'] == 9 else 'e2f' if event['kind'] == 40003 else 'r2f'
        with store.transaction():
            rows = pending(store, binding, channel, author)
            previous = next((row for row in rows if row['source_id'] == event['id']), None)
            if previous is not None:
                if previous['kind'] != event['kind'] or previous['source_at'] != event['created_at']:
                    raise ValueError
                return
            # Preserve the exact baseline decision, not a later moving floor.
            # Existing ledger/inbox evidence may predate this adapter's floor.
            if (event['created_at'] < floor
                    and store.delivery_by_source(binding, event['id'], direction, agent_id=author) is None):
                inbox = store.conn.execute('SELECT captured_floor FROM reaction_inbox WHERE binding_id=? AND agent_id=? AND source_id=? AND channel_id=?',
                    (binding, author, event['id'], channel)).fetchone()
                if inbox is None or event['created_at'] < inbox['captured_floor']:
                    raise ValueError
                floor = inbox['captured_floor']
            store.conn.execute('INSERT INTO outlet_pending VALUES(?,?,?,?,?,?,?)',
                (binding, author, event['id'], channel, event['kind'], event['created_at'], floor))
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        raise StoreError(ERROR) from None


def forget(store, binding, author, source):
    """Only explicit settled delivery or verified negative scope may prune."""
    ident(binding); hexid(author); hexid(source)
    with store.transaction():
        store.conn.execute('DELETE FROM outlet_pending WHERE binding_id=? AND agent_id=? AND source_id=?',
                           (binding, author, source))
