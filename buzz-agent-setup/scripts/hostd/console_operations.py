"""Durable console intent; recovered operations never replay business actions.

A live creation capability and its execution epoch authorize one dispatch. SQL
keeps keys and terminal outcomes; ContextVar carries only typed internal metadata
to the fixed three-argument dispatcher. Bare handler success is not a receipt.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import inspect
import math
import secrets
import time

from .store import hexid, ident

NOTICE = ('操作尚未通过持久回执核验。怎么解决：检查当前授权和操作账本；恢复的未决操作只核验回执，不要重新派发。'
          '\n复制给 AI：帮我检查 hostd 控制台操作的请求标识、执行批次、授权和关联回执；不要输出凭据、消息正文或个人信息。')
_EXPECTED = {'pause': 'paused', 'resume': 'active', 'backfill': 'backfilled', 'restart': 'restarted'}
_TERMINAL = frozenset(('completed', 'rejected'))
_INTENT = ContextVar('hostd_console_intent', default=None)


class ConsoleOperationError(ValueError):
    def __init__(self): super().__init__(NOTICE)


@dataclass(frozen=True)
class ConsoleIntent:
    operation_id: str
    principal: str
    execution_epoch: str
    kind: str
    target: str
    action: str

    def __post_init__(self):
        hexid(self.operation_id); hexid(self.principal); hexid(self.execution_epoch)
        if ((self.kind == 'agent' and self.action == 'restart')):
            hexid(self.target)
        elif self.kind == 'binding' and self.action in ('pause', 'resume', 'backfill'):
            ident(self.target)
        else:
            raise ConsoleOperationError()

    @classmethod
    def from_record(cls, record):
        return cls(record.id, record.principal, record.execution_epoch, record.kind, record.target, record.action)


@dataclass(frozen=True)
class ConsoleReceipt:
    operation_id: str
    observed: str
    receipt_hash: str

    def __post_init__(self):
        hexid(self.operation_id); hexid(self.receipt_hash)
        if self.observed not in _EXPECTED.values(): raise ConsoleOperationError()


def current_intent():
    """Internal execution context; HTTP never supplies a unit or process spec."""
    return _INTENT.get()


class ConsoleCoordinator:
    def __init__(self, store, action, authorize, readback=None, *, execution_epoch=None, timeout=90, clock=time.time):
        if (not callable(action) or not callable(authorize) or not callable(clock)
            or (readback is not None and not callable(readback)) or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300):
            raise ConsoleOperationError()
        self.store, self.action, self.authorize, self.readback = store, action, authorize, readback
        self.execution_epoch = secrets.token_hex(32) if execution_epoch is None else hexid(execution_epoch)
        self.timeout, self.clock = timeout, clock
        self._created, self._tasks = set(), {}
        self._drivers = {}  # Strong ownership of the one original resume action.
        self._closing = False
        self._loop = None

    def _now(self):
        value = self.clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ConsoleOperationError()
        return int(value)

    def _on_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None: self._loop = loop
        if loop is not self._loop: raise ConsoleOperationError()

    def _authorized(self, intent):
        try:
            answer = self.authorize(intent)
            if inspect.iscoroutine(answer): answer.close()
            return answer is True
        except Exception:
            return False

    def _hold(self, operation_id, reason):
        try: return self.store.hold_console_operation(operation_id, reason=reason, now=self._now())
        except Exception: return False  # Durable queued/dispatched uncertainty still forbids replay.

    def enqueue(self, principal, key_hash, kind, target, action):
        self._on_loop()
        reservation = self.store.reserve_console_operation(principal, key_hash, kind, target, action,
                                        execution_epoch=self.execution_epoch, now=self._now())
        record = reservation.record
        if reservation.created:
            if self._closing:
                self._hold(record.id, 'driver')
                return type(reservation)(self.store.console_operation(record.id, principal), True)
            self._created.add(record.id)
            task = asyncio.create_task(self._execute(record))
            self._tasks[record.id] = task
            def finished(done):
                self._created.discard(record.id)
                if self._tasks.get(record.id) is done: self._tasks.pop(record.id, None)
                if not done.cancelled(): done.exception()  # Consume exceptions without logging their bodies.
            task.add_done_callback(finished)
        return reservation

    async def _settle(self, intent):
        record = self.store.console_operation(intent.operation_id, intent.principal)
        if record is None or record.status in _TERMINAL: return record
        if self._closing:
            return record
        if not self._authorized(intent):
            self._hold(record.id, 'scope')
            return self.store.console_operation(record.id, intent.principal)
        if intent.action == 'restart':
            # Exact linked domain ACK is authoritative. Never call a fresh
            # pub-only restart driver to try to "recover" this operation.
            if not self.store.reconcile_console_restart(record.id, now=self._now()):
                if record.status != 'unknown': self._hold(record.id, 'proof')
            return self.store.console_operation(record.id, intent.principal)
        if self.readback is None:
            if record.status != 'unknown': self._hold(record.id, 'proof')
            return self.store.console_operation(record.id, intent.principal)
        try:
            receipt = self.readback(intent)
            if inspect.isawaitable(receipt): receipt = await asyncio.wait_for(receipt, self.timeout)
            # Every receipt await is a scope boundary. A matching target status
            # or a bare ActionResult cannot substitute for associated metadata.
            if self._closing:
                self._hold(record.id, 'driver')
            elif not self._authorized(intent):
                self._hold(record.id, 'scope')
            else:
                current = self.store.console_operation(record.id, intent.principal)
                if current is not None and current.status not in _TERMINAL:
                    if (type(receipt) is ConsoleReceipt and receipt.operation_id == record.id
                        and receipt.observed == _EXPECTED[intent.action]
                        and self.store.finish_console_operation(record.id, expected=current.status,
                                        observed=receipt.observed, receipt_hash=receipt.receipt_hash, now=self._now())):
                        pass
                    elif current.status != 'unknown': self._hold(record.id, 'proof')
        except asyncio.CancelledError:
            self._hold(record.id, 'cancellation')
            raise
        except asyncio.TimeoutError:
            self._hold(record.id, 'timeout')
        except Exception:
            if record.status != 'unknown': self._hold(record.id, 'proof')
        return self.store.console_operation(record.id, intent.principal)

    def live_driver(self, intent):
        """Only the actual original task can mint its operation's round proof."""
        entry = self._drivers.get(intent.operation_id) if type(intent) is ConsoleIntent else None
        return bool(entry and entry[0] == intent and entry[1] is asyncio.current_task()
                    and not entry[1].done())

    def _durable_resume(self, intent):
        if intent.kind != 'binding' or intent.action != 'resume':
            return False
        entry = self._drivers.get(intent.operation_id)
        record = self.store.console_operation(intent.operation_id, intent.principal)
        return bool(intent.kind == 'binding' and intent.action == 'resume' and entry and entry[0] == intent
                    and record and ConsoleIntent.from_record(record) == intent
                    and record.status in ('dispatched', 'unknown'))

    async def _dispatch_once(self, record, intent):
        task = asyncio.create_task(self.action(record.kind, record.target, record.action))
        if intent.kind == 'binding' and intent.action == 'resume':
            self._drivers[intent.operation_id] = (intent, task)
            def finished(done):
                if self._drivers.get(intent.operation_id) == (intent, done):
                    self._drivers.pop(intent.operation_id, None)
                if not done.cancelled():done.exception()  # Never log exception bodies.
            task.add_done_callback(finished)
        try:
            try:
                return await asyncio.wait_for(asyncio.shield(task), self.timeout)
            except asyncio.TimeoutError:
                from .console_pause import matches
                if matches(self.store, intent) or self._durable_resume(intent):
                    # The original durable pause/resume remains the only action.
                    # An observer deadline cannot cancel it or invent success.
                    return await asyncio.shield(task)
                raise
        finally:
            if not task.done():
                if self._closing or not self._durable_resume(intent):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def _execute(self, record):
        self._on_loop()
        intent = ConsoleIntent.from_record(record)
        token = None
        try:
            if record.id not in self._created or record.execution_epoch != self.execution_epoch:
                self._hold(record.id, 'recovered')
                return
            if not self._authorized(intent):
                self.store.reject_console_operation(record.id, record.principal, self.execution_epoch,
                                                    reason='scope', now=self._now())
                return
            if not self.store.claim_console_operation(record.id, record.principal, self.execution_epoch, now=self._now()):
                self._hold(record.id, 'busy')
                return
            token = _INTENT.set(intent)
            try:
                await self._dispatch_once(record, intent)
            except asyncio.CancelledError:
                self._hold(record.id, 'cancellation')
                raise
            except asyncio.TimeoutError:
                self._hold(record.id, 'timeout')
            except Exception:
                self._hold(record.id, 'driver')
            await self._settle(intent)
        except asyncio.CancelledError:
            self._hold(record.id, 'cancellation')
            raise
        except Exception:
            self._hold(record.id, 'driver')
        finally:
            if token is not None: _INTENT.reset(token)

    async def wait_operation(self, operation_id, principal):
        self._on_loop(); hexid(operation_id); hexid(principal)
        if self.store.console_operation(operation_id, principal) is None: return None
        task = self._tasks.get(operation_id)
        if task is not None:
            try: await asyncio.shield(task)  # Lost HTTP response never cancels dispatched work.
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling(): raise
        return self.store.console_operation(operation_id, principal)

    async def reconcile_operation(self, operation_id, principal):
        """Exact authorized readback only. GET cannot interrupt or dispatch a live intent."""
        self._on_loop(); hexid(operation_id); hexid(principal)
        record = self.store.console_operation(operation_id, principal)
        if record is None or record.status in _TERMINAL or self._closing:
            return record
        task = self._tasks.get(operation_id)
        if task is not None and not task.done():
            return record
        if record.status in ('queued', 'dispatched'):
            self._hold(record.id, 'recovered')
        return await self._settle(ConsoleIntent.from_record(record))

    async def restore(self, principal):
        self._on_loop(); hexid(principal)
        after = None
        while not self._closing:
            records = self.store.console_pending_operations(principal, after=after, limit=128)
            if not records: break
            # Settling a page may remove every row from the pending set. An
            # immutable keyset advances past it without OFFSET skips.
            last = records[-1]
            after = (last.created_at, last.id)
            for record in records:
                if self._closing: break
                await self.reconcile_operation(record.id, principal)
        return tuple(self.store.console_operations(principal, limit=128))

    async def close(self):
        self._on_loop(); self._closing = True
        tasks = tuple(set(self._tasks.values()) | {task for _, task in self._drivers.values()})
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
