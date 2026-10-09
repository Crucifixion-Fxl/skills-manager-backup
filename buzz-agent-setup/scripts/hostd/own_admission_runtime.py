"""Root-owned, bounded scheduling for actual local own-home admission.

This scheduler supplies only pubkeys/original ids to the physical coordinator.
Its SQL/catalog selection is not source approval, process readiness or a grant.
Unresolved durable rows are always read back, never admitted again. Borrowed
Store, service, Scheduler and HTTP pool live until all owned calls have joined.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import re
import threading

from . import agent_catalog
from .async_io import thread_call
from .join_effects import AgentSpec
from .onboarding_runtime import OnboardingRuntime
from .own_admission import OwnHomeAdmissionCoordinator
from .store import Store

NOTICE = ('本机接入仍在等待当前来源、配置和实际进程核验。怎么解决：检查本机受保护目录、旧任务排除、自己的应用身份和原始接入记录；未知结果只读回，不重复重启。'
          '\n复制给 AI：帮我检查 hostd 本机接入调度、原始 RESERVED/UNKNOWN、来源与进程读回；不要输出密钥、凭据、提示词正文或人员信息。')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
MAX_PUBKEYS = 32
PERIOD_SECONDS = 30


class _AgentOrder:
    """Internal owning-task ordering, never evidence or an authority verdict.

    Admission and normal remote IO acquire without waiting. Nested operations
    in the SAME owning loop task are reentrant; another pubkey is independent.
    No external caller can supply a token, proof or verified lock capability.
    """
    def __init__(self, loop):
        self._loop, self._owners = loop, {}

    def _task(self):
        if (threading.current_thread() is not threading.main_thread()
                or asyncio.get_running_loop() is not self._loop):
            raise ValueError(NOTICE)
        task = asyncio.current_task()
        if task is None:
            raise ValueError(NOTICE)
        return task

    def _busy(self, pubkey):
        task = self._task()
        owned = self._owners.get(pubkey)
        return owned is not None and owned[0] is not task

    def _try_enter(self, pubkey):
        task = self._task()
        if type(pubkey) is not str or not _HEX.fullmatch(pubkey):
            raise ValueError(NOTICE)
        owned = self._owners.get(pubkey)
        if owned is None:
            self._owners[pubkey] = (task, 1)
            return True
        if owned[0] is task:
            self._owners[pubkey] = (task, owned[1]+1)
            return True
        return False

    def _leave(self, pubkey):
        task = self._task()
        owned = self._owners.get(pubkey)
        if owned is None or owned[0] is not task:
            raise ValueError(NOTICE)
        if owned[1] == 1:
            del self._owners[pubkey]
        else:
            self._owners[pubkey] = (task, owned[1]-1)


class OwnHomeAdmissionRuntime:
    """Owning-loop metadata selection; real coordinator owns every effect."""

    def __init__(self, service, coordinator, *, scheduler, http_pool):
        if (type(service) is not OnboardingRuntime
                or type(coordinator) is not OwnHomeAdmissionCoordinator
                or type(service.store) is not Store or coordinator.store is not service.store
                or threading.current_thread() is not threading.main_thread()
                or service.loop is not asyncio.get_running_loop()
                or service._scheduler is not scheduler or service._http_pool is not http_pool
                or not callable(getattr(scheduler, 'run', None))
                or not callable(getattr(http_pool, 'request', None))
                or coordinator.catalog_path != Path(service.config.catalog_path)
                or coordinator.legacy_join_path != Path(service.config.legacy_join_path)):
            raise ValueError(NOTICE)
        self.store, self.service = service.store, service
        self.scheduler, self.http_pool = scheduler, http_pool
        self.coordinator = coordinator
        self._loop = service.loop
        self._closed = False
        self._wake = asyncio.Event()
        self._scan_lock = asyncio.Lock()
        self._calls = set()
        self._scans = set()
        self._close_task = None
        self._run_task = None
        self._recovery_cursor = ''
        self._new_cursor = ''
        self.last_notice = ''
        if getattr(self.store, '_own_home_order', None) is not None:
            raise ValueError(NOTICE)
        self._order = _AgentOrder(self._loop)
        self.store._own_home_order = self._order  # Root-owned internal ordering only.

    def _own_loop(self):
        if (threading.current_thread() is not threading.main_thread()
                or asyncio.get_running_loop() is not self._loop):
            raise ValueError(NOTICE)

    def spec_for(self, pubkey):
        """Current runtime map only; still available for historical cleanup views."""
        self._own_loop()
        return self.service.effects.specs.get(pubkey)

    def _sql_spec(self, pubkey):
        """Pure owning-loop identity guard, never a process/source verdict."""
        if type(pubkey) is not str or not _HEX.fullmatch(pubkey):
            return None
        spec = self.spec_for(pubkey)
        row = self.store.conn.execute('SELECT * FROM agent WHERE pubkey=?', (pubkey,)).fetchone()
        config = self.service.config
        if (type(spec) is not AgentSpec or row is None or row['status'] != 'active'
                or (spec.pubkey, spec.owner_pubkey, spec.app_id, spec.env_file) !=
                   (pubkey, row['owner_pubkey'], row['app_id'], row['config_path'])
                or spec.owner_pubkey != self.service.relay.owner
                or Path(spec.timer_config) != Path(config.legacy_join_path)
                or Path(spec.binding_dir) != Path(config.binding_dir)
                or Path(spec.template_config) != Path(config.template_config)
                or spec.reader_app_id != spec.app_id):
            return None
        return spec

    def _current_zero(self, record):
        if (type(record) is not agent_catalog.AgentRecord
                or record.status != 'own_bot_verified' or record.local_bot_verified is not True
                or record.owner_pubkey != self.service.relay.owner or record.channels):
            return False
        spec = self._sql_spec(record.pubkey)
        return bool(spec is not None and
            (spec.app_id, spec.env_file, spec.unit, spec.prompt_file, spec.responsible_file,
             spec.reader_config_dir, spec.reader_data_dir) ==
            (record.app_id, str(record.env_file), record.unit, str(record.prompt_file),
             str(record.responsible_config), str(record.lark_config_dir), str(record.lark_data_dir)))

    def _recovery_batch(self):
        """At most16 rotating metadata rows leave room for new pubkeys."""
        rows = self.store.conn.execute(
            "SELECT agent_id,admission_id FROM own_home_admission "
            "WHERE state IN ('reserved','unknown') AND agent_id>? ORDER BY agent_id LIMIT 16",
            (self._recovery_cursor,)).fetchall()
        if len(rows) < 16 and self._recovery_cursor:
            rows += self.store.conn.execute(
                "SELECT agent_id,admission_id FROM own_home_admission "
                "WHERE state IN ('reserved','unknown') AND agent_id<=? ORDER BY agent_id LIMIT ?",
                (self._recovery_cursor, 16-len(rows))).fetchall()
        if rows:
            self._recovery_cursor = rows[-1]['agent_id']
        return rows

    @staticmethod
    def _rotate(pubkeys, cursor, limit):
        ordered = sorted(set(pubkeys))
        return ([pub for pub in ordered if pub > cursor] +
                [pub for pub in ordered if pub <= cursor])[:limit]

    async def _call(self, method, identity, pubkey):
        if self._closed:
            return None
        async def invoke():
            if not self._order._try_enter(pubkey):
                self.last_notice = NOTICE
                return None  # Busy only this pubkey, no global wait or IO.
            try:
                return await method(identity)
            finally:
                self._order._leave(pubkey)  # Actual physical finally has reaped.
        pending = asyncio.create_task(invoke())
        self._calls.add(pending)
        cancelled = False
        try:
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    cancelled = True
                    if not pending.done() and not pending.cancelling():
                        pending.cancel()
                except BaseException:
                    break
            try:
                result = pending.result()
            except BaseException:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
            if cancelled:
                raise asyncio.CancelledError
            return result
        finally:
            self._calls.discard(pending)  # Physical finally/thread_call have reaped.

    async def scan_once(self):
        self._own_loop()
        if self._closed:
            return 0
        completed = self._loop.create_future()
        self._scans.add(completed)
        processed = 0
        try:
            async with self._scan_lock:
                if self._closed:
                    return 0
                # Original rows are examined before applying any channel filter:
                # UNKNOWN can already have changed the protected allowlist.
                recovered = set()
                for row in self._recovery_batch():
                    if self._closed:
                        return processed
                    pub = row['agent_id']
                    recovered.add(pub)
                    processed += 1
                    current = self.store.active_own_home_admission(pub)
                    if (current is None or current.admission_id != row['admission_id']
                            or self._sql_spec(pub) is None):
                        self.last_notice = NOTICE
                        continue
                    await self._observe(self.coordinator.readback, current.admission_id, pub)
                if self._closed:
                    return processed
                catalog = await thread_call(agent_catalog.load, self.service.config.catalog_path,
                    legacy_join_path=self.service.config.legacy_join_path)
                if self._closed:
                    return processed
                candidates = {row.pubkey: row for row in catalog.records if self._current_zero(row)}
                keys = self._rotate(candidates, self._new_cursor, MAX_PUBKEYS-processed)
                for pub in keys:
                    if self._closed:
                        return processed
                    self._new_cursor = pub
                    if pub in recovered:
                        continue
                    processed += 1
                    # Recheck SQL and current runtime spec after earlier awaits.
                    # The physical coordinator independently reloads every file.
                    active = self.store.active_own_home_admission(pub)
                    if active is not None:
                        await self._observe(self.coordinator.readback, active.admission_id, pub)
                    elif self._current_zero(candidates[pub]):
                        await self._observe(self.coordinator.admit, pub, pub)
                    else:
                        self.last_notice = NOTICE
                return processed
        except asyncio.CancelledError:
            raise
        except Exception:
            self.last_notice = NOTICE
            return processed
        finally:
            completed.set_result(None)
            self._scans.discard(completed)

    async def _observe(self, method, identity, pubkey):
        try:
            result = await self._call(method, identity, pubkey)
            if result is not None and result.status != 'admitted':
                self.last_notice = NOTICE
        except asyncio.CancelledError:
            raise
        except Exception:
            self.last_notice = NOTICE

    async def run(self):
        self._own_loop()
        if self._run_task is not None:
            raise ValueError(NOTICE)
        self._run_task = asyncio.current_task()
        try:
            while not self._closed:
                self._wake.clear()
                await self.scan_once()
                if self._closed:
                    return
                try:
                    await asyncio.wait_for(self._wake.wait(), PERIOD_SECONDS)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._run_task = None

    def stop(self):
        self._own_loop()
        self._closed = True
        self._wake.set()
        for task in tuple(self._calls):
            if not task.done() and not task.cancelling():
                task.cancel()

    async def _close(self):
        await asyncio.gather(*tuple(self._scans), *tuple(self._calls), return_exceptions=True)

    async def close(self):
        """Join original IO; repeated cancellation cannot close borrowed objects."""
        self.stop()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError
