"""Bounded Feishu discovery. IDs/tokens retain work, never authorize effects."""
import hashlib
import json
import sqlite3
import time

import buzz_feishu_group_sync as gs
try:
    from .store import StoreError
except ImportError:
    from store import StoreError

CAPACITY = 10000
DRAIN_LIMIT = 8
DRAIN_SECONDS = 5


def scope(run):
    owner = run.clients.owner
    return hashlib.sha256(json.dumps([run.binding_id, run.cfg, owner.app_id,
        str(owner.config_dir), str(owner.data_dir)], sort_keys=True).encode()).hexdigest()


def pending(run):
    rows = run.mapping_store.conn.execute('SELECT * FROM feishu_ingest WHERE binding_id=? AND done=0 ORDER BY retry_at,source_ms,capture_seq,message_id',
                                          (run.binding_id,)).fetchall()
    if any(r['chat_id'] != run.cfg['chat_id'] for r in rows):
        raise gs.GroupSyncError('Feishu ingest scope changed')
    return [dict(r) for r in rows]


def begin(run, start, end):
    store = run.mapping_store
    current_scope = scope(run)
    with store.transaction():
        old = store.conn.execute('SELECT * FROM feishu_scan WHERE binding_id=?', (run.binding_id,)).fetchone()
        if old and old['scope_hash'] == current_scope and not old['complete']:
            return dict(old)
        if old and old['scope_hash'] == current_scope and old['complete']:
            # Discovery completeness is independent of thread/phase cursors. A
            # broken descending thread read cannot pin new root discovery.
            start = max(run.state.floor, run.state.feishu_floor, old['end_at'] - run._feishu_overlap())
        if old:
            # Reconfiguration never skips an unfinished interval. Work IDs stay.
            if not old['complete']: start = min(start, old['start_at'])
            store.conn.execute('DELETE FROM feishu_scan WHERE binding_id=?', (run.binding_id,))
        store.conn.execute('DELETE FROM feishu_ingest WHERE binding_id=? AND done=1 AND source_ms<?', (run.binding_id, start * 1000))
        store.conn.execute('INSERT INTO feishu_scan VALUES(?,?,?,?,?,0)',
                           (run.binding_id, current_scope, start, end, ''))
        return dict(store.conn.execute('SELECT * FROM feishu_scan WHERE binding_id=?', (run.binding_id,)).fetchone())


def capture(run, scan, page):
    """All IDs and next token commit together, or none do."""
    store = run.mapping_store
    with store.transaction():
        current = store.conn.execute('SELECT * FROM feishu_scan WHERE binding_id=?', (run.binding_id,)).fetchone()
        if current is None or dict(current) != scan or scope(run) != scan['scope_hash'] or scan['complete']:
            raise gs.GroupSyncError('Feishu scan changed')
        if page.has_more:
            token_hash = hashlib.sha256(page.next_token.encode()).hexdigest()
            if (not page.next_token or len(page.next_token) > 4096 or not page.rows
                    or page.next_token == scan['next_token']
                    or store.conn.execute('SELECT 1 FROM feishu_scan_token WHERE binding_id=? AND token_hash=?',
                                          (run.binding_id, token_hash)).fetchone()
                    or store.conn.execute('SELECT count(*) FROM feishu_scan_token WHERE binding_id=?',
                                          (run.binding_id,)).fetchone()[0] >= CAPACITY):
                raise gs.GroupSyncError('Feishu continuation did not progress')
            store.conn.execute('INSERT INTO feishu_scan_token VALUES(?,?)', (run.binding_id, token_hash))
        if type(page.has_more) is not bool or len(page.rows) > 50:
            raise gs.GroupSyncError('Invalid retained Feishu page')
        for row in page.rows:
            if not isinstance(row.get('message_id'), str) or not gs.MESSAGE_ID_RE.fullmatch(row['message_id']):
                raise gs.GroupSyncError('Invalid Feishu message ID')
            mid, source_ms = row['message_id'], int(row['create_time'])
            if row['chat_id'] != run.cfg['chat_id'] or not scan['start_at'] * 1000 <= source_ms < (scan['end_at'] + 1) * 1000:
                raise gs.GroupSyncError('Feishu page outside retained scope')
            old = store.conn.execute('SELECT * FROM feishu_ingest WHERE binding_id=? AND message_id=?',
                                     (run.binding_id, mid)).fetchone()
            if old and (old['chat_id'] != row['chat_id'] or old['source_ms'] != source_ms):
                raise gs.GroupSyncError('Feishu source metadata conflict')
            # Preserve the provider's order at timestamp ties, including across
            # pages/restart. IDs are not a chronological tie breaker.
            sequence = store.conn.execute('SELECT coalesce(max(capture_seq),0)+1 FROM feishu_ingest WHERE binding_id=?',
                                          (run.binding_id,)).fetchone()[0]
            store.conn.execute('INSERT OR IGNORE INTO feishu_ingest(binding_id,chat_id,message_id,source_ms,captured_floor,capture_seq) VALUES(?,?,?,?,?,?)',
                               (run.binding_id, row['chat_id'], mid, source_ms, scan['start_at'], sequence))
        store.conn.execute('UPDATE feishu_scan SET next_token=?,complete=? WHERE binding_id=?',
                           (page.next_token if page.has_more else '', int(not page.has_more), run.binding_id))


def reset_token(run, scan):
    """A failed/expired continuation rereads the same interval, never skips it."""
    with run.mapping_store.transaction():
        changed = run.mapping_store.conn.execute('UPDATE feishu_scan SET next_token=? WHERE binding_id=? AND scope_hash=? AND next_token=? AND complete=0',
                                       ('', run.binding_id, scan['scope_hash'], scan['next_token']))
        if changed.rowcount == 1:
            run.mapping_store.conn.execute('DELETE FROM feishu_scan_token WHERE binding_id=?', (run.binding_id,))


def discover(run, start):
    run.feishu_ingest_cooperative = False
    run.feishu_ingest_retry_at = None
    run._feishu_scan_failed = False
    scan = begin(run, start, run.now_ts)
    run._feishu_scan_blocked = len(pending(run)) > CAPACITY - 50
    if not scan['complete'] and not run._feishu_scan_blocked:
        try:
            page = run.clients.owner.message_page(run.cfg['chat_id'], scan['start_at'], scan['end_at'], token=scan['next_token'])
            capture(run, scan, page)
        except (gs.GroupSyncError, gs.CliError, sqlite3.Error, StoreError):
            if scan['next_token']: reset_token(run, scan)
            # No new page progress, but previously retained sources can drain.
            run.report['errors'] += 1
            run._feishu_scan_failed = True
    run._feishu_scan = dict(run.mapping_store.conn.execute('SELECT * FROM feishu_scan WHERE binding_id=?', (run.binding_id,)).fetchone())
    run._feishu_ingest_deadline = None
    # Return metadata placeholders only. The effect wrapper obtains a fresh exact read.
    rows = [r for r in pending(run) if r['retry_at'] <= run.now_ts][:DRAIN_LIMIT]
    run._feishu_ingest_selected = {r['message_id']: r for r in rows}
    return [{'message_id': r['message_id']} for r in rows]


def process(run, mirror, placeholder, window_start):
    mid = placeholder['message_id']
    row = run._feishu_ingest_selected[mid]
    if run._feishu_ingest_deadline is None:
        run._feishu_ingest_deadline = time.monotonic() + DRAIN_SECONDS
    if time.monotonic() >= run._feishu_ingest_deadline:
        return
    store = run.mapping_store
    now = int((run.auth_clock() if run.auth_clock else run.now).timestamp())
    with store.transaction():
        changed = store.conn.execute('UPDATE feishu_ingest SET attempts=attempts+1,retry_at=? WHERE binding_id=? AND message_id=? AND attempts=? AND retry_at<=?',
            (now + min(300, 30 * 2 ** min(row['attempts'], 4)), run.binding_id, mid, row['attempts'], now))
        if changed.rowcount != 1: raise gs.GroupSyncError('Feishu ingest lease changed')
    try:
        msg = run.clients.owner.message_view(mid, 'open_id')
        if (msg is None or msg.get('chat_id') != row['chat_id'] or msg.get('message_id') != mid
                or str(msg.get('create_time')) != str(row['source_ms'])):
            raise gs.GroupSyncError('Feishu retained source cannot be freshly resolved')
        msg = run.clients.owner._normalize_message(msg)
        if msg.get('thread_id'):
            root = run._feishu_source_root(msg) or mid
            run.state.threads.setdefault(root, run.now_ts)
            # A retained old root may be drained after the scan watermark has
            # advanced. Its undiscovered replies still start at capture time,
            # not that newer aggregate cursor; actual reply effects deduplicate.
            run.state.polled[root] = min(run.state.polled.get(root, row['captured_floor']), row['captured_floor'])
        # Durable discovery preserves the source beyond overlap, but never crosses
        # a deliberately changed protected floor. Exact source seconds are
        # independent of the display-normalized timestamp and scan watermark.
        since = max(max(run.state.floor, run.state.feishu_floor), min(window_start, row['source_ms'] // 1000))
        result = mirror(msg, None, since, source_ts=row['source_ms'] // 1000)
        run.persist()  # ACK/UNKNOWN or explicit no-effect decision precedes forgetting.
        value = run.state.f2b.get(mid)
        if result is not gs._HELD and not (gs._is_pending(value) or gs._is_retry(value)):
            with store.transaction():
                store.conn.execute('UPDATE feishu_ingest SET done=1 WHERE binding_id=? AND message_id=?', (run.binding_id, mid))
    except (gs.GroupSyncError, gs.CliError, OSError, ValueError, TypeError, KeyError, OverflowError):
        run.report['errors'] += 1  # One dependency/read failure cannot monopolize the batch.


def finish(run):
    rows = pending(run)
    scan = run._feishu_scan
    run.feishu_ingest_cooperative = (not scan['complete'] and not run._feishu_scan_failed and not run._feishu_scan_blocked) or any(r['retry_at'] <= run.now_ts for r in rows)
    run.feishu_ingest_retry_at = min((r['retry_at'] for r in rows), default=None)
    if run._feishu_scan_failed and run.feishu_ingest_retry_at is None:
        run.feishu_ingest_retry_at = run.now_ts + 30
    run.feishu_ingest_status = dict(pending=len(rows), scan_complete=bool(scan['complete']),
                                    scan_failed=run._feishu_scan_failed, capacity_wait=run._feishu_scan_blocked)
    if scan['complete']:
        run.state.feishu_since = max(run.state.feishu_since, scan['end_at'])
