"""Durable scheduling hints, not receipts, grants, or absence evidence."""
from __future__ import annotations
import sqlite3
import buzz_feishu_group_sync as gs
try:
    from .store import StoreError, ERROR, ident, hexid, stamp
    from . import outlet_pending
except ImportError:
    from store import StoreError, ERROR, ident, hexid, stamp
    import outlet_pending


def parent(event):
    if event['kind'] == 9:
        value = gs.buzz_thread_root(event) or gs._buzz_parent(event)
    elif event['kind'] == 40003:
        value = gs.edit_target(event)
    else:
        refs = [t[1] for t in event['tags'] if t[:1] == ['e'] and len(t) >= 2]
        value = refs[0] if len(refs) == 1 else None
    return value if isinstance(value,str) and gs.HEX64_RE.fullmatch(value) else ''


def pending(store, binding, channel, author):
    ident(binding); ident(channel); hexid(author)
    actual=store.conn.execute('SELECT channel_id FROM binding WHERE binding_id=?',(binding,)).fetchone()
    if actual is None or actual['channel_id'] != channel:raise StoreError(ERROR)
    rows=store.conn.execute('SELECT * FROM outlet_work WHERE binding_id=? AND agent_id=? ORDER BY source_at,source_id',(binding,author)).fetchall()
    if len(rows)>10000 or any(r['channel_id']!=channel for r in rows):raise StoreError(ERROR)
    return [dict(r) for r in rows]


def capture(store,binding,channel,author,events,*,floor,now,retained):
    """Capture every skipped positive candidate before any newer cursor ACK.

    Signatures and original baseline eligibility authorize metadata only. No
    native/root/owner proof is inferred, including for stock h-less reactions.
    """
    stamp(floor);stamp(now)
    try:
        with store.transaction():
            previous={r['source_id']:r for r in pending(store,binding,channel,author)}
            legacy={r['source_id']:r for r in outlet_pending.pending(store,binding,channel,author)}
            for event in events:
                source=event['id'];hexid(source);stamp(event['created_at'])
                hs=[t for t in event['tags'] if t[:1]==['h']]
                if (event['pubkey']!=author or event['kind'] not in (9,40003,7,5)
                        or not gs._nip01_event_verified(event)
                        or not (hs==[['h',channel]] or not hs and event['kind'] in (7,5))):raise ValueError
                old=previous.get(source)
                if old:
                    if (old['kind'],old['source_at'],old['parent_id'])!=(event['kind'],event['created_at'],parent(event)):raise ValueError
                    continue
                direction='b2f' if event['kind']==9 else 'e2f' if event['kind']==40003 else 'r2f'
                eligible_floor=floor
                if source in legacy:
                    if (legacy[source]['kind'],legacy[source]['source_at'])!=(event['kind'],event['created_at']):raise ValueError
                    eligible_floor=min(eligible_floor,legacy[source]['captured_floor'],event['created_at'])
                if event['created_at']<eligible_floor:
                    if store.delivery_by_source(binding,source,direction,agent_id=author) is not None:
                        eligible_floor=event['created_at']
                    elif source in retained:
                        inbox=store.conn.execute('SELECT captured_floor FROM reaction_inbox WHERE binding_id=? AND agent_id=? AND source_id=? AND channel_id=?',(binding,author,source,channel)).fetchone()
                        if inbox is None or event['created_at']<inbox['captured_floor']:raise ValueError
                        eligible_floor=inbox['captured_floor']
                    else:raise ValueError
                # An old16 unresolved source was already attempted. Delay its
                # first retry, while newly arrived independent topics can run.
                attempts,retry=(1,now+30) if source in legacy else (0,0)
                store.conn.execute('INSERT INTO outlet_work VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (binding,author,source,channel,event['kind'],event['created_at'],eligible_floor,parent(event),attempts,retry))
    except (ValueError,KeyError,TypeError,sqlite3.Error):raise StoreError(ERROR) from None


def choose(store,binding,channel,author,events,*,now):
    """Keep text/edit and reaction/withdrawal dependencies in separate lanes.

    A reaction on a root is not a prerequisite for a reply to that root.
    Both lanes still use their causal heads and the shared eighth-turn aging.
    """
    rows=pending(store,binding,channel,author)
    if not rows:return None
    by_id={e['id']:e for e in events};rank={e['id']:i for i,e in enumerate(events)}
    links={}
    def root(value):
        links.setdefault(value,value)
        while links[value]!=value:
            links[value]=links[links[value]];value=links[value]
        return value
    def lane(kind):
        return "text" if kind in (9,40003) else "reaction"
    def connect(a,b,kind):
        if b:
            a,b=root((lane(kind),a)),root((lane(kind),b))
            if a!=b:links[max(a,b)]=min(a,b)
    # Settled sources still connect replies/reactions to their actual topic.
    for event in events:connect(event['id'],parent(event),event['kind'])
    for row in rows:connect(row['source_id'],row['parent_id'],row['kind'])
    for row in outlet_pending.pending(store,binding,channel,author):
        if row['source_id'] not in {r['source_id'] for r in rows}:
            rows.append(dict(row,parent_id='',attempts=1,retry_at=0))
    groups={}
    for row in rows:groups.setdefault(root((lane(row['kind']),row['source_id'])),[]).append(row)
    heads=[]
    for group in groups.values():
        # A captured source absent from this complete interval never becomes
        # settled by absence. Its dependent topic stays blocked.
        if any(r['source_id'] not in by_id for r in group):continue
        head=min(group,key=lambda r:rank[r['source_id']])
        if head['retry_at']<=now:heads.append((head,max(r['source_at'] for r in group)))
    if not heads:return None
    saved=store.conn.execute('SELECT fresh_turns FROM outlet_work_turn WHERE binding_id=? AND agent_id=?',(binding,author)).fetchone()
    turns=saved[0] if saved else 0
    fresh=[item for item in heads if item[0]['attempts']==0]
    text=[item for item in heads if item[0]['kind'] in (9,40003)]
    if turns>=7:
        # Compare real eligibility times: zero is only the unattempted sentinel.
        # A failed lease moves forward, letting both due retries and old fresh
        # topics age ahead of a continuous stream of newer independent topics.
        chosen=min(heads,key=lambda item:(item[0]['retry_at'] if item[0]['attempts'] else item[0]['source_at'],item[0]['source_at'],item[0]['source_id']));next_turn=0
    elif text:
        # A prior failed read does not remove a body from the preferred lane.
        # Count retry preference too, so reaction work still receives the
        # shared eighth-turn opportunity after restart.
        fresh_text=[item for item in text if item[0]['attempts']==0]
        chosen=(max(fresh_text,key=lambda item:(item[1],item[0]['source_id'])) if fresh_text else
                min(text,key=lambda item:(item[0]['retry_at'],item[0]['source_at'],item[0]['source_id'])))
        next_turn=turns+1
    elif fresh:
        chosen=max(fresh,key=lambda item:(item[1],item[0]['source_id']));next_turn=turns+1
    else:
        chosen=min(heads,key=lambda item:(item[0]['retry_at'],item[0]['source_at'],item[0]['source_id']));next_turn=0
    return chosen[0],next_turn


def start(store,binding,author,row,turn,*,now):
    """Commit a retry lease before any selected read/effect; crash cannot spin."""
    stamp(now)
    try:
        with store.transaction():
            delay=min(300,30*(2**min(row['attempts'],4)))
            changed=store.conn.execute('UPDATE outlet_work SET attempts=attempts+1,retry_at=? WHERE binding_id=? AND agent_id=? AND source_id=? AND attempts=? AND retry_at<=?',
                (now+delay,binding,author,row['source_id'],row['attempts'],now))
            if changed.rowcount!=1:raise StoreError(ERROR)
            store.conn.execute('INSERT INTO outlet_work_turn VALUES(?,?,?) ON CONFLICT(binding_id,agent_id) DO UPDATE SET fresh_turns=excluded.fresh_turns',(binding,author,turn))
    except sqlite3.Error:raise StoreError(ERROR) from None


def forget(store,binding,author,source):
    ident(binding);hexid(author);hexid(source)
    with store.transaction():
        store.conn.execute('DELETE FROM outlet_work WHERE binding_id=? AND agent_id=? AND source_id=?',(binding,author,source))
