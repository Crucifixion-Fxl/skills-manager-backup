"""Durable local pause fence and associated drain evidence. Never external IO."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets

from .console_operations import ConsoleIntent, ConsoleReceipt, ConsoleCoordinator
from .store import StoreError, ERROR, hexid, ident, stamp


def process_start(pid):
    stamp(pid)
    if not pid:
        raise StoreError(ERROR)
    try:
        with open(Path('/proc') / str(pid) / 'stat', 'rb') as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError
        value = int(raw.rsplit(b') ', 1)[1].split()[19])
        return stamp(value) if value else None
    except FileNotFoundError:
        return None
    except Exception:
        raise StoreError(ERROR) from None


def root_identity():
    pid = os.getpid()
    start = process_start(pid)
    if start is None:
        raise StoreError(ERROR)
    return (pid, start, secrets.token_hex(32))


def scope_hash(values):
    return hashlib.sha256(json.dumps(tuple(values), separators=(',', ':')).encode()).hexdigest()


def fence(store, binding):
    ident(binding)
    row = store.conn.execute('SELECT * FROM console_pause WHERE binding_id=?', (binding,)).fetchone()
    return dict(row) if row is not None else None


def matches(store, intent):
    if type(intent) is not ConsoleIntent or intent.kind != 'binding' or intent.action != 'pause':
        return False
    row = fence(store, intent.target)
    record = store.console_operation(intent.operation_id, intent.principal)
    return bool(row and record and row['operation_id'] == intent.operation_id
                and ConsoleIntent.from_record(record) == intent
                and record.status in ('dispatched', 'unknown', 'completed'))


def begin(store, intent, scope, root, *, now):
    if type(intent) is not ConsoleIntent or intent.kind != 'binding' or intent.action != 'pause':
        raise StoreError(ERROR)
    hexid(scope); stamp(now)
    pid, start, nonce = root
    stamp(pid); stamp(start); hexid(nonce)
    with store.transaction():
        record = store.console_operation(intent.operation_id, intent.principal)
        if record is None or record.status != 'dispatched' or ConsoleIntent.from_record(record) != intent:
            raise StoreError(ERROR)
        existing = fence(store, intent.target)
        if existing is not None:
            if matches(store, intent) and existing['scope_hash'] == scope:
                return
            previous = store.console_operation(existing['operation_id'], intent.principal)
            if (existing['state'] != 'drained' or existing['scope_hash'] != scope or previous is None
                    or previous.status != 'completed' or previous.receipt_hash != existing['receipt_hash']):
                raise StoreError(ERROR)
            # A new explicit pause after a completed pause gets its own drain
            # evidence; old receipt/key history remains in console_operation.
            store.conn.execute('DELETE FROM console_pause WHERE binding_id=?', (intent.target,))
        store.conn.execute('INSERT INTO console_pause VALUES(?,?,?,?,?,?,?,\'stopping\',NULL)',
            (intent.target, intent.operation_id, scope, pid, start, nonce, now))


def may_observe_drain(store, intent, scope, root):
    """A different live root cannot certify another root's worker drain."""
    if not matches(store, intent):
        return False
    row = fence(store, intent.target)
    if row['scope_hash'] != scope:
        return False
    if row['state'] == 'drained':
        return True
    pid, start, nonce = root
    if (row['root_pid'], row['root_start'], row['root_nonce']) == (pid, start, nonce):
        return True  # Caller still must own the actual binding lock.
    observed = process_start(row['root_pid'])
    return observed is None or observed != row['root_start']


def complete(store, intent, scope, root, *, now):
    """Caller owns the binding lock and rechecked principal/registry scope.

    Only local observed state is recorded. This does not invoke a worker or
    repeat any external action, including on old UNKNOWN operation readback.
    """
    stamp(now)
    with store.transaction():
        if not may_observe_drain(store, intent, scope, root):
            return None
        binding = store.conn.execute('SELECT status FROM binding WHERE binding_id=?', (intent.target,)).fetchone()
        if binding is None or binding['status'] in ('retired', 'conflict'):
            return None
        row = fence(store, intent.target)
        if row['state'] == 'stopping':
            digest = hashlib.sha256(json.dumps(('hostd-console-pause-drained:v1',
                intent.operation_id, intent.principal, intent.execution_epoch, scope,
                row['root_pid'], row['root_start'], row['root_nonce'], *root), separators=(',', ':')).encode()).hexdigest()
            store.conn.execute("UPDATE binding SET status='paused',heartbeat_at=? WHERE binding_id=?", (now, intent.target))
            store.conn.execute("UPDATE console_pause SET state='drained',receipt_hash=? WHERE binding_id=? AND state='stopping'",
                (digest, intent.target))
        elif binding['status'] != 'paused':
            return None  # Never restore a later independently changed state.
        digest = fence(store, intent.target)['receipt_hash']
        record = store.console_operation(intent.operation_id, intent.principal)
        if record.status == 'completed':
            if record.receipt_hash != digest:
                raise StoreError(ERROR)
        elif not store.finish_console_operation(intent.operation_id, expected=record.status,
                observed='paused', receipt_hash=digest, now=now):
            raise StoreError(ERROR)
        return ConsoleReceipt(intent.operation_id, 'paused', digest)


def release(store, intent, scope, *, driver=None):
    """Only an original explicit resume can release a drained fence.

    UNKNOWN requires the actual still-owned original driver, never a boolean
    recovery flag or a new execution reconstructed from the operation ledger.
    """
    if type(intent) is not ConsoleIntent or intent.kind != 'binding' or intent.action != 'resume':
        return False
    with store.transaction():
        record = store.console_operation(intent.operation_id, intent.principal)
        if (record is None or ConsoleIntent.from_record(record) != intent
                or record.status not in ('dispatched', 'unknown')):
            return False
        if record.status == 'unknown' and (type(driver) is not ConsoleCoordinator
                or not ConsoleCoordinator.live_driver(driver, intent)):
            return False
        row = fence(store, intent.target)
        if row is None:
            return True
        original = store.console_operation(row['operation_id'], intent.principal)
        if (row['scope_hash'] != scope or row['state'] != 'drained' or original is None
                or original.status != 'completed' or original.receipt_hash != row['receipt_hash']):
            return False
        store.conn.execute('DELETE FROM console_pause WHERE binding_id=? AND operation_id=?',
            (intent.target, row['operation_id']))
        return True
