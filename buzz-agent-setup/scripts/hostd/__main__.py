"""buzz-hostd: event-driven Feishu <-> Buzz sync for every binding on this machine (ADR-0025).

  python3 -m hostd run [--only a,b] [--status-file PATH]

Feishu: one child process per sync app (feishu_feed.py) holds that app's long connection. Buzz: one relay WebSocket
per binding as its mirror agent (relay_feed.py). Events only mark a binding dirty; a single worker task per binding
runs the dirty phases with the existing round rules (binding_worker.py), debounced so a burst is one run. No polling:
the only clock is the claim lease renewal (ADR-0022) and a reconnect catch-up.
"""
from __future__ import annotations

import argparse
import contextlib
import concurrent.futures
import fcntl
import hashlib
import stat
import tempfile
import asyncio
import json
import math
import os
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
from hostd import binding_worker as bw  # noqa: E402
import registry  # noqa: E402
import relay_feed  # noqa: E402
from hostd.round_slots import RoundSlots, AdmissionWithdrawn
from hostd.scheduler import Scheduler  # noqa: E402
from hostd.http_pool import HttpPool  # noqa: E402
from safety import read_owned  # noqa: E402
from hostd.store import Store, BindingRecord  # noqa: E402
from hostd.console import ConsoleServer, ActionResult  # noqa: E402
from hostd.console_operations import ConsoleIntent, current_intent  # noqa: E402

DEBOUNCE = 0.6        # seconds: events arriving together become one run
TASK_RETRY = 1.0
CHILD_STOP_TIMEOUT = 2.0
CLAIM_TICK = 300      # check whether any claim needs renewal (renewal itself happens every 25 min)
STATE_DIR = Path.home() / ".local" / "state" / "buzz-hostd"


def default_console_dir():
    notice = ('控制台运行目录无法确认。怎么解决：使用本人持有、权限为 0700 的 XDG_RUNTIME_DIR，'
              '或为隔离运行明确指定 --console-dir。复制给 AI：检查 hostd 本机 Unix 控制台运行目录与权限。')
    value = os.environ.get('XDG_RUNTIME_DIR', '')
    path = Path(value)
    if not value or not path.is_absolute() or '..' in path.parts:
        raise ValueError(notice)
    fd = None
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        meta = os.fstat(fd)
        if meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise ValueError(notice)
    except (OSError, ValueError):
        raise ValueError(notice) from None
    finally:
        if fd is not None:
            os.close(fd)
    return path / 'buzz-hostd'


@contextlib.contextmanager
def app_lock(app: str, directory: Path):
    """One app connection across hostd processes; hold the inode across restarts."""
    if ".." in directory.parts:
        raise ValueError("应用连接锁路径不安全；怎么解决：使用绝对真实目录。复制给 AI：检查 hostd 应用锁路径。")
    directory = directory.absolute()
    # Walk directory FDs without following any symlink, including all ancestors.
    directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in directory.parts[1:]:
            try:
                child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=directory_fd)
                child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        meta = os.fstat(directory_fd)
        if meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise ValueError("应用连接锁目录权限不正确；怎么解决：由本人持有并改为 0700。复制给 AI：检查 hostd 应用锁目录权限。")
        filename = hashlib.sha256(app.encode()).hexdigest() + ".lock"
        fd = os.open(filename, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    except OSError:
        raise ValueError("应用连接锁路径不安全；怎么解决：使用不经过软链接的真实目录。复制给 AI：检查 hostd 应用锁路径。") from None
    finally:
        os.close(directory_fd)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o600:
            raise ValueError("应用连接锁权限不正确；怎么解决：由本人持有并改为 0600。复制给 AI：检查 hostd 应用锁文件权限。")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("这个应用已有本机连接；怎么解决：停掉重复的 hostd。复制给 AI：查找占用 hostd 应用连接锁的进程。") from None
        yield
    finally:
        os.close(fd)  # Do not unlink: waiters and new entrants must use the same inode.


async def stop_child(proc) -> None:
    """Terminate and reap a child; an uncooperative SDK is killed after a bounded grace period."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), CHILD_STOP_TIMEOUT)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    else:
        await proc.wait()


class Hostd:
    def __init__(self, reg: registry.Registry, status_file: Path, *, state_db: Path | None = None,
                 migrate_bot_readers: bool = False, console_dir: Path | None = None, restart_agent=None,
                 onboarding_config=None, sdk_evidence_config=None, latency_trace=None,
                 round_workers=4, round_live_reserve=None):
        self._round_slots = RoundSlots(round_workers, round_live_reserve)
        self.console_dir = console_dir
        self.latency_trace = latency_trace
        self.sdk_evidence=None
        if sdk_evidence_config is not None:
            from hostd.sdk_evidence import EvidenceTap
            self.sdk_evidence=EvidenceTap(sdk_evidence_config)
        self.restart_agent = restart_agent
        self._restart_driver = None
        self.console = None
        self.console_store = None
        self.reg = reg
        self.scheduler = Scheduler()
        self.http_pool = HttpPool(scheduler=self.scheduler)
        self.migrate_bot_readers = migrate_bot_readers
        self.onboarding_config = onboarding_config
        self.onboarding = None
        self.remote_dispatch = None
        self.own_home_admission = None
        self.own_home_admission_runtime = None
        self.runtime_store = None
        self._round_executor = None
        self._notice_lane = asyncio.Lock()
        self._ledger_executor = None
        self._ledger_closed = False
        self._running = False
        self._tasks = set()
        self.app_tasks = {}
        self.binding_tasks = {}
        self.outlet_tasks = {}
        self.outlet_status = {}
        self.outlet_author_hints = {}  # Loop-owned FIFO metadata, never send authority.
        self.app_profiles = {app: (bs[0].lark_config_dir, bs[0].lark_data_dir)
                             for app, bs in reg.by_app().items()}
        self.store_path = state_db or status_file.parent / 'hostd.sqlite3'
        self.workers = {b.name: bw.Worker(b.config, b.state_dir, base_env=self._env(),
            store_path=self.store_path, binding_id=b.name, migrate_bot_readers=migrate_bot_readers,
            scheduler=self.scheduler, claim_relay_pubkey=self._claim_relay_pubkey())
            for b in reg.bindings.values()}
        for worker in self.workers.values():
            worker.http_pool = self.http_pool
            worker.latency_trace = self.latency_trace
        self.dirty: dict[str, set[str]] = {n: set() for n in reg.bindings}
        self.threads: dict[str, set[str] | None] = {n: set() for n in reg.bindings}
        self.wake: dict[str, asyncio.Event] = {n: asyncio.Event() for n in reg.bindings}
        self.binding_locks = {n: asyncio.Lock() for n in reg.bindings}
        self.retry_tasks = {}
        self.retry_phases = {}
        self.retry_at = {}
        self.notice_clock = lambda: int(time.time())
        self.notice_hints = {n: set() for n in reg.bindings}
        self.notice_overflow = set()
        self.notice_retry_at = {}
        self.notice_binding_cursor = ""
        self.status: dict = {"started": time.time(), "bindings": {}, "apps": {}}
        self.status_file = status_file
        self.app_lock_dir = STATE_DIR / "app-locks"
        self.mirrors = {}
        for b in reg.bindings.values():
            cfg = json.loads(read_owned(b.config))
            self.mirrors[b.name] = cfg["mirror_pubkey"]
            self.status["bindings"][b.name] = {"relay": "connecting", "last_run": None, "last_error": None, "runs": 0}
        # One machine ledger, not one independent database per worker. Connections
        # are opened in their owning thread; SQLite serializes durable writes.
        with Store(self.store_path) as store:
            records = [BindingRecord(b.name, b.channel_id, b.chat_id, b.sync_app_id,
                str(b.config.absolute()), b.lark_config_dir, b.lark_data_dir, self.mirrors[b.name], status='pending')
                for b in reg.bindings.values()]
            store.reconcile_bindings(records, now=int(time.time()))

    def _claim_relay_pubkey(self):
        from hostd.onboarding_runtime import RuntimeConfig
        # A binding or a returned relay event is not a reviewed trust anchor.
        config = self.onboarding_config
        return config.relay_pubkey if isinstance(config, RuntimeConfig) else None

    async def start_console(self):
        if self.console_dir is None or self.console is not None:
            return
        db = Store(self.store_path)  # Created and used only on the active main loop.
        server = ConsoleServer(db, self.console_dir, action=self.console_action,
            operation_readback=lambda intent: self.console_readback(intent, principal=server._principal))
        try:
            await server.start()
        except BaseException:
            db.close()
            raise
        self.console_store, self.console = db, server

    async def close_console(self):
        try:
            if self.console is not None:
                await self.console.close()  # Reap operation threads before final retry cleanup.
        finally:
            self.console = None
            for task in self.retry_tasks.values():
                task.cancel()
            await asyncio.gather(*self.retry_tasks.values(), return_exceptions=True)
            self.retry_tasks.clear()
            self.retry_phases.clear()
            self.retry_at.clear()
            if self.console_store is not None:
                self.console_store.close()
                self.console_store = None

    def trace(self, name, stage, **fields):
        if getattr(self, 'latency_trace', None) is not None:
            self.latency_trace.record(name, stage, **fields)

    def notify(self):
        if self.console is not None:
            self.console.notify()

    def _binding_state(self, name):
        with Store(self.store_path) as db:
            row = db.conn.execute("SELECT status FROM binding WHERE binding_id=?", (name,)).fetchone()
            from hostd.console_pause import fence
            if row is not None and fence(db, name) is not None:
                return 'paused'  # Internal dispatch fence; public state waits for real drain.
            return row['status'] if row else None

    def _console_binding_current(self, db, name, intent):
        binding = self.reg.bindings.get(name)
        row = db.conn.execute("SELECT b.*,p.config_dir,p.data_dir FROM binding b JOIN app_profile p ON p.app_id=b.sync_app_id WHERE binding_id=?", (name,)).fetchone()
        if binding is None or row is None or row['status'] in ('retired', 'conflict'):
            return False
        actual = tuple(row[k] for k in ('channel_id', 'chat_id', 'sync_app_id', 'config_path', 'config_dir', 'data_dir', 'mirror_pubkey'))
        expected = (binding.channel_id, binding.chat_id, binding.sync_app_id, str(binding.config.absolute()),
                    str(binding.lark_config_dir), str(binding.lark_data_dir), self.mirrors[name])
        if actual != expected:
            return False
        if intent is None:
            return True
        if (type(intent) is not ConsoleIntent or self.console is None or self.console._coordinator is None
                or intent.kind != 'binding' or intent.target != name
                or intent.principal != self.console._principal()
                or intent.execution_epoch != self.console._coordinator.execution_epoch):
            return False
        record = db.console_operation(intent.operation_id, intent.principal)
        if not record or ConsoleIntent.from_record(record) != intent:
            return False
        if intent.action == 'resume':
            return record.status in ('dispatched', 'unknown') and self.console._coordinator.live_driver(intent)
        return record.status == 'dispatched'

    def _pause_root(self):
        from hostd.console_pause import root_identity
        if not hasattr(self, '_console_pause_root'):
            self._console_pause_root = root_identity()
        return self._console_pause_root

    def _pause_scope(self, db, target):
        from hostd.console_pause import scope_hash
        if not self._console_binding_current(db, target, None):
            return None
        row = db.conn.execute("SELECT b.channel_id,b.chat_id,b.sync_app_id,b.config_path,p.config_dir,p.data_dir,b.mirror_pubkey FROM binding b JOIN app_profile p ON p.app_id=b.sync_app_id WHERE binding_id=?", (target,)).fetchone()
        return scope_hash(tuple(row)) if row is not None else None

    async def console_readback(self, intent, *, principal):
        from hostd import console_pause
        if (type(intent) is not ConsoleIntent or intent.kind != 'binding' or intent.action != 'pause'
                or not callable(principal) or intent.principal != principal() or intent.target not in self.binding_locks):
            return None
        with Store(self.store_path) as db:
            scope = self._pause_scope(db, intent.target)
            if scope is None or not console_pause.may_observe_drain(db, intent, scope, self._pause_root()):
                return None
        async with self.binding_locks[intent.target]:
            with Store(self.store_path) as db:
                with db.transaction():
                    scope = self._pause_scope(db, intent.target)
                    if scope is None or intent.principal != principal():
                        return None
                    receipt = console_pause.complete(db, intent, scope, self._pause_root(), now=int(time.time()))
        self.notify()
        return receipt

    def _binding_receipt(self, db, name, intent, observed, *, scope=None):
        """Loop-local SQL proof, associated with this live intent; never infer a recovered round."""
        if intent is None:
            return True
        if not self._console_binding_current(db, name, intent):
            return False
        if intent.action == 'resume' and (scope is None or self._pause_scope(db, name) != scope):
            return False
        row = db.conn.execute("SELECT status,channel_id,sync_app_id,mirror_pubkey FROM binding WHERE binding_id=?", (name,)).fetchone()
        if row['status'] != ('paused' if observed == 'paused' else 'active'):
            return False
        values = ('hostd-console-binding-receipt:v1', intent.operation_id, intent.execution_epoch,
                  intent.principal, intent.kind, intent.target, intent.action, observed, *tuple(row))
        receipt = hashlib.sha256('\0'.join(values).encode()).hexdigest()
        record = db.console_operation(intent.operation_id, intent.principal)
        return db.finish_console_operation(intent.operation_id, expected=record.status, observed=observed,
                                           receipt_hash=receipt, now=int(time.time()))

    def _set_binding_state(self, name, value, *, intent=None, observed=None, scope=None):
        with Store(self.store_path) as db:
            with db.transaction():
                if not self._console_binding_current(db, name, intent):
                    return False
                if scope is not None and self._pause_scope(db, name) != scope:
                    return False
                if value == 'pending' and intent is not None and intent.action == 'resume':
                    from hostd.console_pause import release
                    if not release(db, intent, self._pause_scope(db, name), driver=self.console._coordinator):
                        return False
                db.conn.execute("UPDATE binding SET status=?,heartbeat_at=? WHERE binding_id=? AND status!='retired'",
                    (value, int(time.time()), name))
                if observed is not None and not self._binding_receipt(db, name, intent, observed):
                    raise ValueError('控制台操作授权已改变。怎么解决：核对原操作和绑定。复制给 AI：检查 hostd 持久操作回执。')
        self.notify()
        return True

    async def console_action(self, kind, target, operation):
        intent = current_intent()
        if kind == 'agent' and operation == 'restart':
            if self.restart_agent is None:
                return ActionResult(False, 'failed')
            if intent is not None:
                scoped = getattr(self.restart_agent, 'restart_for_console', None)
                if not callable(scoped):
                    return ActionResult(False, 'failed')
                result = await scoped(target, intent)
            else:
                result = await self.restart_agent(target)
            self.notify()
            return result if isinstance(result, ActionResult) else ActionResult(False, 'failed')
        if kind != 'binding' or target not in self.workers or operation not in {'pause', 'resume', 'backfill'}:
            return ActionResult(False, 'failed')
        resume_scope = None
        if operation == 'resume' and intent is not None:
            with Store(self.store_path) as db:
                if not self._console_binding_current(db, target, intent):
                    return ActionResult(False, 'failed')
                resume_scope = self._pause_scope(db, target)
                if resume_scope is None:
                    return ActionResult(False, 'failed')
        if operation == 'pause' and intent is not None:
            from hostd.console_pause import begin
            with Store(self.store_path) as db:
                with db.transaction():
                    if not self._console_binding_current(db, target, intent):
                        return ActionResult(False, 'failed')
                    scope = self._pause_scope(db, target)
                    begin(db, intent, scope, self._pause_root(), now=int(time.time()))
            self._round_slots.withdraw_waiting(target)
            self.notify()
        async with self.binding_locks[target]:
            with Store(self.store_path) as db:
                if not self._console_binding_current(db, target, intent):
                    return ActionResult(False, 'failed')
            if operation == 'pause' and intent is not None:
                from hostd.console_pause import complete
                with Store(self.store_path) as db:
                    with db.transaction():
                        if not self._console_binding_current(db, target, intent):
                            return ActionResult(False, 'failed')
                        receipt = complete(db, intent, self._pause_scope(db, target), self._pause_root(), now=int(time.time()))
                self.notify()
                return ActionResult(receipt is not None, 'paused')
            if operation == 'pause':
                ok = self._set_binding_state(target, 'paused', intent=intent, observed='paused')
                return ActionResult(ok, 'paused')
            if operation == 'backfill' and self._binding_state(target) == 'paused':
                return ActionResult(False, 'failed')
            if operation == 'resume':
                if not self._set_binding_state(target, 'pending', intent=intent, scope=resume_scope):
                    return ActionResult(False, 'failed')
            self.workers[target].cached = {}
            self.workers[target].setup_at = 0
            worker = self.workers[target]
            previous = getattr(worker, 'request_priority', 'normal')
            worker.request_priority = 'backfill'
            try:
                if resume_scope is not None:
                    rep = await self._round(target, {'members', 'buzz', 'feishu'}, None, console_scope=resume_scope)
                else:
                    rep = await self._round(target, {'members', 'buzz', 'feishu'}, None)
            finally:
                worker.request_priority = previous
            ok = isinstance(rep, dict) and self._binding_state(target) == 'active' and not rep.get('hostd', {}).get('retry_phases') and not rep.get('hostd', {}).get('pending_targets')
            observed = 'active' if operation == 'resume' else 'backfilled'
            if resume_scope is not None:
                with Store(self.store_path) as db:
                    record = db.console_operation(intent.operation_id, intent.principal)
                    ok = bool(record and record.status == 'completed' and record.receipt_hash)
            elif ok:
                with Store(self.store_path) as db:
                    with db.transaction():
                        ok = self._binding_receipt(db, target, intent, observed)
            return ActionResult(ok, observed)

    def replay_since(self, name):
        with Store(self.store_path) as db:
            if db.has_replay_state(name, 'relay'):
                return db.replay_since(name, 'relay')
        return max(0, int(time.time()) - bw.gs.BUZZ_OVERLAP_SECONDS)

    def schedule_retry(self, name, phases, when):
        phases = set(phases) & set(bw.PHASES)
        if not phases or type(when) not in (int, float) or not math.isfinite(when):
            return
        self.retry_phases.setdefault(name, set()).update(phases)
        deadline = time.time() + max(.01, min(60, when - time.time()))
        old = self.retry_tasks.get(name)
        if old is not None and not old.done() and self.retry_at[name] <= deadline:
            return
        if old is not None:
            old.cancel()
        self.retry_at[name] = deadline
        async def wake_later():
            await asyncio.sleep(max(.01, deadline - time.time()))
            selected = self.retry_phases.pop(name, set())
            for phase in selected:
                self.mark(name, phase, catch_up=phase == 'feishu')
        task = asyncio.create_task(wake_later())
        self.retry_tasks[name] = task
        self._tasks.add(task)  # Replaced retries also remain owned until reaped.
        task.add_done_callback(self._tasks.discard)

    def retain_notice_hint(self, name, event):
        # Only an ID crosses the feed/worker boundary. Actual source/app/scope
        # authorization comes from the coordinator's fresh signed read.
        source = event.get('id')
        pin = getattr(self.workers.get(name), 'claim_relay_pubkey', None)
        if not isinstance(pin, str) or not bw.gs.HEX64_RE.fullmatch(pin):
            return True  # No notice producer without an explicit reviewed trust pin.
        if (name not in self.workers or event.get('kind') != 9
                or not isinstance(source, str) or not bw.gs.HEX64_RE.fullmatch(source)):
            return True
        hints = self.notice_hints.setdefault(name, set())
        if source in hints or len(hints) < 256:
            hints.add(source)
            return True
        self.notice_overflow.add(name)
        return False  # The async feed retains its current ID until bounded room opens.

    async def notice_wake_once(self):
        """Bounded metadata-only pass; no relay/native source discovery."""
        if not self._running:
            return  # Shutdown refuses new scheduled work while original IO joins.
        now = self.notice_clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError('通知时钟无法确认；怎么解决：检查本机 UTC。复制给 AI：检查 hostd 通知调度。')
        now = int(now)
        names = sorted(self.reg.bindings)
        selected = [name for name in names if name > self.notice_binding_cursor][:32]
        if not selected:
            self.notice_binding_cursor = ""
            selected = names[:32]
        if selected:
            self.notice_binding_cursor = selected[-1]
        def snapshot():
            rows=[]
            with Store(self.store_path) as db:
                for name in selected:
                    current = db.conn.execute("SELECT status FROM binding WHERE binding_id=?", (name,)).fetchone()
                    if current is None or current['status'] != 'active':
                        continue
                    # Binding scope precedes the aggregate; global first256 cannot
                    # hide another binding's earliest deadline after a restart.
                    row = db.conn.execute("SELECT min(CASE WHEN state='waiting' THEN deadline_at END) AS deadline, "
                        "sum(CASE WHEN state IN ('reserved','unknown','recovery_reserved','recovery_unknown') THEN 1 ELSE 0 END) AS retry, "
                        "sum(CASE WHEN state='noticed' THEN 1 ELSE 0 END) AS noticed "
                        "FROM delivery_notice WHERE binding_id=?", (name,)).fetchone()
                    item = dict(row)
                    item['hints'] = bool(db.pending_notice_hints(name))
                    rows.append((name,item))
            return rows
        for name,row in await self._ledger_call(snapshot):
            due = row['deadline'] is not None and row['deadline'] <= now
            recovery = bool(row['retry'] or row['noticed'] or row['hints'])
            if ((due or recovery) and self.notice_retry_at.get(name, 0) <= now
                    and 'notice' not in self.dirty[name]):
                self.mark(name, 'notice')
                # Minimum retry floor prevents due-past or unknown busy loops.
                self.notice_retry_at[name] = now + (5 if due or row['retry'] else 60)

    async def notices(self):
        while True:
            await self.notice_wake_once()
            await asyncio.sleep(1)

    def _retain_round_work(self, name, dirty, threads, selected_hints):
        self.dirty[name].update(dirty)
        self.notice_hints[name].update(selected_hints)
        prior_threads = self.threads[name]
        self.threads[name] = None if prior_threads is None or threads is None else set(prior_threads) | set(threads)
        self.wake[name].set()

    def _resume_scope_current(self, name, intent, scope):
        with Store(self.store_path) as db:
            return bool(type(intent) is ConsoleIntent and intent.action == 'resume'
                and self._console_binding_current(db, name, intent) and self._pause_scope(db, name) == scope)

    async def _round(self, name, dirty, threads, *, console_scope=None):
        st = self.status['bindings'][name]
        began = time.time()
        cancelled = False
        capacity = max(0, 256 - len(getattr(self.workers[name], 'notice_sources', ())))
        stage = 'round_outlets'
        self.trace(name, 'round_queued')
        selected_hints = tuple(sorted(self.notice_hints.setdefault(name, set())))[:capacity]
        resume_intent = current_intent() if console_scope is not None else None
        try:
            if self.onboarding is not None and dirty != {'notice'}:
                await self.refresh_outlets(name)
            self.trace(name, 'round_refreshed')
            stage = 'round_dispatch'
            worker = self.workers[name]
            worker.notice_clock = self.notice_clock
            worker.failure_formatter = relay_feed.failure_diagnostic
            # Pause may have arrived during refresh, before any ticket existed.
            # There is no yield between this read and creating the slot ticket.
            if self._binding_state(name) == 'paused':
                self._retain_round_work(name, dirty, threads, selected_hints)
                return
            if console_scope is not None and not self._resume_scope_current(name, resume_intent, console_scope):
                self._retain_round_work(name, dirty, threads, selected_hints)
                return
            # Keep binding IO out of the loop's DNS/default executor. Waiting
            # rounds are cancellable before dispatch; only in-flight IO is joined.
            async with self._round_slots.slot(name):
                # Events received while this round waited must not wait for
                # another full queue cycle. They still use the normal Worker.
                incoming = self.dirty[name] - {'notice'}
                if dirty == {'notice'} and incoming:
                    # Yield the notice lane without turning an unprepared
                    # notice-only round into ordinary outbound work.
                    self.mark(name, 'notice')
                    self._round_slots.promote(name)
                    return
                dirty.update(incoming)
                self.dirty[name].difference_update(incoming)
                if 'feishu' in incoming:
                    more = self.threads[name]
                    threads = None if threads is None or more is None else set(threads) | set(more)
                    self.threads[name] = set()
                self.notice_hints[name].difference_update(selected_hints)
                # This last loop-local read and executor submit must not yield:
                # an asynchronously returned snapshot could predate a new fence.
                if self._binding_state(name) == 'paused':
                    self._retain_round_work(name, dirty, threads, selected_hints)
                    return  # No dispatch after a durable pause fence; retain pending work.
                if console_scope is not None and not self._resume_scope_current(name, resume_intent, console_scope):
                    self._retain_round_work(name, dirty, threads, selected_hints)
                    return
                if self._round_executor is None:
                    self._round_executor = concurrent.futures.ThreadPoolExecutor(
                        max_workers=self._round_slots.capacity, thread_name_prefix='hostd-round')
                # Snapshot only after final pause/admission checks; arrivals
                # during the worker thread stay loop-owned for the next round.
                author_hints = tuple(getattr(self, 'outlet_author_hints', {}).pop(name, {})) if 'buzz' in dirty else ()
                def execute():
                    self.trace(name, 'round_started')
                    try:
                        options = {'threads': threads}
                        if selected_hints: options['notice_sources'] = selected_hints
                        if author_hints: options['outlet_authors'] = author_hints
                        return worker.run(dirty, **options)
                    finally:
                        self.trace(name, 'round_finished')
                try:
                    future = asyncio.get_running_loop().run_in_executor(self._round_executor, execute)
                except BaseException:
                    # No thread accepted the snapshot. Once submit succeeds,
                    # only the worker owns it, even if its setup later raises.
                    if author_hints:
                        later = self.outlet_author_hints.get(name, {})
                        self.outlet_author_hints[name] = dict.fromkeys((*author_hints, *later))
                    raise
                # Task cancellation cannot release the binding lock while its thread
                # still writes. Reap the executor result, then propagate cancellation.
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        cancelled = True
            stage = 'round_result'
            rep = future.result()
            if not isinstance(rep, dict):
                raise ValueError('unverified round result')
            meta = rep.get('hostd', {})
            if not isinstance(meta, dict) or meta.get('verdict') not in {'won', 'off', 'lost', 'unreadable'}:
                raise ValueError('unverified round observation')
            st.get('failure_diagnostics', {}).pop('round', None)
            durations = meta.get('timings', {})
            allowed = {'setup_people', 'claims', 'directory_desk', 'own_outlets', 'delivery_notices'}
            allowed.update(step for steps in bw.PHASES.values() for step in steps)
            st['timings'] = {key: round(value, 2) for key, value in durations.items()
                if key in allowed and type(value) in (int, float) and math.isfinite(value) and value >= 0} if isinstance(durations, dict) else {}
            diagnostics = meta.get('failure_diagnostics', ())
            if isinstance(diagnostics,(list,tuple)):
                st['worker_failure_diagnostics'] = [
                    relay_feed.copy_diagnostic(row)
                    for row in diagnostics[:8] if relay_feed.valid_diagnostic(row)]
            errors = rep.get('errors', 0)
            errors = errors if type(errors) is int and errors >= 0 else 1
            stage = 'round_status'
            st.update(last_run=began, last_ms=int((time.time() - began) * 1000), runs=st['runs'] + 1,
                last_error=None, verdict=meta.get('verdict'), last_dirty=sorted(dirty),
                to_feishu=rep.get('to_feishu'), to_buzz=rep.get('to_buzz'), errors=errors)
            retries = meta.get('retry_phases', ())
            retries_valid = isinstance(retries, (list, tuple, set, frozenset)) and all(p in bw.PHASES for p in retries)
            if not retries_valid:
                retries = ()
            pending = meta.get('pending_targets', 0)
            if dirty != {'notice'} and type(pending) is int and pending > 0:
                retries = set(retries) | {'feishu'}
            cooperative = meta.get('cooperative_phases', ())
            cooperative_valid = isinstance(cooperative,(list,tuple)) and all(p in {'buzz','feishu'} for p in cooperative)
            if not cooperative_valid:
                cooperative = ()
            if dirty != {'notice'} or errors or st['verdict'] in {'lost', 'unreadable'}:
                binding_state = ('conflict' if st['verdict'] == 'lost' else
                    'degraded' if errors or set(retries)-set(cooperative) or st['verdict'] == 'unreadable' else 'active')
                if console_scope is not None:
                    # The same original task supplies the actual round result.
                    # Status and exact receipt share one loop-local transaction;
                    # cancellation after this point cannot erase the evidence.
                    with Store(self.store_path) as db:
                        with db.transaction():
                            if (not self._console_binding_current(db, name, resume_intent)
                                    or self._pause_scope(db, name) != console_scope):
                                self._retain_round_work(name, dirty, threads, selected_hints)
                                return
                            db.conn.execute('UPDATE binding SET status=?,heartbeat_at=? WHERE binding_id=?',
                                (binding_state, int(time.time()), name))
                            # A healthy bounded slice proves work resumed even with more
                            # cooperative work queued; it does not claim history drained.
                            if (binding_state == 'active' and retries_valid and cooperative_valid
                                    and not (set(retries) - set(cooperative))
                                    and type(pending) is int and pending == 0):
                                if not self._binding_receipt(db, name, resume_intent, 'active', scope=console_scope):
                                    raise ValueError('resume receipt could not be committed')
                else:
                    await self._ledger_call(lambda: self._binding_status_store(name, binding_state))
                self.notify()
            notice = meta.get('notice', {})
            if isinstance(notice, dict):
                state = notice.get('status')
                st['delivery_notice'] = state if state in {'unconfigured', 'binding_pending', 'scanned', 'pending'} else 'pending'
                seconds = notice.get('retry_seconds', 5)
                seconds = seconds if type(seconds) is int and 1 <= seconds <= 60 else 5
                self.notice_retry_at[name] = int(self.notice_clock()) + seconds
                if dirty == {'notice'} and notice.get('backlog'):
                    # A bounded notice slice may have freed capacity. Replay
                    # the held ordinary cursor without treating pressure as a
                    # failed authorization or disabling the draining lane.
                    retries = set(retries) | {'buzz'}
                    meta = dict(meta, next_retry_at=time.time() + seconds)
                if (dirty != {'notice'} and (notice.get('pending_hints') or notice.get('backlog') or self.notice_hints[name])
                        and await self._ledger_call(lambda: self._binding_state(name)) == 'active'):
                    # Candidate discovery belongs to its own bounded lane.
                    retries = set(retries) | {'notice'}
                    meta = dict(meta, next_retry_at=time.time() + seconds)
            if name in self.notice_overflow:
                self.notice_overflow.discard(name)
                retries = set(retries) | {'buzz'}
            when = meta.get('next_retry_at')
            if type(when) not in (int, float) or not math.isfinite(when):
                when = time.time() + TASK_RETRY
            if retries:
                self.schedule_retry(name, retries, when)
            elif dirty != {'notice'}:
                old = self.retry_tasks.pop(name, None)
                if old is not None:
                    old.cancel()
                self.retry_phases.pop(name, None)
                self.retry_at.pop(name, None)
            return rep
        except asyncio.CancelledError:
            # A real shutdown may cancel an unsubmitted admission wait. Keep
            # pending work awake; never strand a resumed binding in pending.
            self._retain_round_work(name, dirty, threads, selected_hints)
            raise
        except AdmissionWithdrawn:
            # No executor was submitted. Preserve this round and release only
            # its normal binding-lock scope; unrelated/admitted work continues.
            self._retain_round_work(name, dirty, threads, selected_hints)
            return
        except Exception as error:
            try:
                self.set_failure_diagnostic(name,'round',relay_feed.failure_diagnostic(stage,error))
            except Exception:
                pass  # formatter failure cannot replace the original round exception
            remaining = self.notice_hints[name] | set(selected_hints)
            self.notice_hints[name] = set(sorted(remaining)[:256])
            if len(remaining) > 256:
                self.notice_overflow.add(name)
            st.update(last_run=began, last_error='这个绑定同步失败，已排队重试；怎么解决：检查配置和应用权限。复制给 AI：检查 hostd 绑定同步失败。')
            await self._ledger_call(lambda: self._binding_status_store(name, 'degraded'))
            self.notify()
            retry_dirty = {'members', 'notice'} if dirty == {'notice'} else dirty
            self.schedule_retry(name, retry_dirty, time.time() + TASK_RETRY)
            raise
        finally:
            self.save_status()
            self.notify()
            if cancelled:
                raise asyncio.CancelledError

    @staticmethod
    def _env() -> dict[str, str]:
        return dict(os.environ)

    def mark(self, name: str, phase: str, thread: str = "", catch_up: bool = False) -> None:
        self.dirty[name].add(phase)
        if phase == "feishu":
            if catch_up or self.threads[name] is None:
                self.threads[name] = None  # read every thread once (reconnect / unknown thread)
            elif thread:
                self.threads[name].add(thread)
        self.wake[name].set()

    def save_status(self) -> None:
        diagnostics = getattr(self.onboarding, 'diagnostics', None)
        if callable(diagnostics):
            self.status['onboarding'] = diagnostics()
        fd, path = tempfile.mkstemp(dir=self.status_file.parent, prefix=".hostd-status-")
        tmp = Path(path)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(json.dumps(self.status, ensure_ascii=False, indent=1))
            tmp.replace(self.status_file)
        finally:
            tmp.unlink(missing_ok=True)

    def set_failure_diagnostic(self, name: str, kind: str, diagnostic: dict) -> None:
        # Three latest records per declared binding; no history/payload retention.
        # Best-effort persistence must not alter an original failure or retry.
        try:
            if name not in self.status['bindings'] or kind not in {'relay','outlet','round'}:
                return
            if not relay_feed.valid_diagnostic(diagnostic):
                return
            self.status['bindings'][name].setdefault('failure_diagnostics',{})[kind] = (
                relay_feed.copy_diagnostic(diagnostic))
            self.save_status()
        except Exception:
            pass

    async def _ledger_call(self, call):
        # One owned lane reserves the default executor for round IO and DNS.
        # Every call constructs/validates/closes its Store in this owning thread;
        # the original loop-owned runtime_store is never transferred.
        if getattr(self, '_ledger_closed', False):
            raise RuntimeError('ledger lane is closed')
        if getattr(self, '_ledger_executor', None) is None:
            self._ledger_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix='hostd-loop-ledger')
        pending = asyncio.get_running_loop().run_in_executor(self._ledger_executor, call)
        cancelled = False
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = True
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

    def _status_memory(self, name, what, value):
        if name in self.status["bindings"]:
            self.status["bindings"][name][what] = value
        else:
            self.status["apps"].setdefault(name, {})[what] = value

    def _status_store(self, name, what, value):
        if what in ('relay', 'feishu'):
            observed = value if value in {'connecting', 'connected', 'disconnected', 'backoff', 'failed', 'stopped'} else 'failed'
            with Store(self.store_path) as store:
                store.record_connection(what, name, binding_id=name if name in self.reg.bindings else None,
                    status=observed, error_code='protocol' if observed == 'failed' else '', now=int(time.time()))

    def set_status(self, name: str, what: str, value: str) -> None:
        self._status_memory(name, what, value)
        self._status_store(name, what, value)
        self.save_status()
        self.notify()

    async def set_status_async(self, name: str, what: str, value: str) -> None:
        self._status_memory(name, what, value)
        if what in ('relay', 'feishu'):
            await self._ledger_call(lambda: self._status_store(name, what, value))
        self.save_status()
        self.notify()

    def _binding_status_store(self, name: str, status: str) -> None:
        with Store(self.store_path) as store:
            with store.transaction():
                store.conn.execute("UPDATE binding SET status=?,heartbeat_at=? WHERE binding_id=? AND status NOT IN ('paused','retired')",
                    (status, int(time.time()), name))

    def binding_status(self, name: str, status: str) -> None:
        self._binding_status_store(name, status)
        self.notify()

    def _retain_target_store(self, name: str, app: str, event: dict) -> None:
        """Persist trusted target identifiers; the reader verifies the actual chat later."""
        message, root = event.get('message_id'), event.get('root_id') or ''
        if (name not in self.reg.bindings or self.reg.bindings[name].sync_app_id != app
                or not isinstance(message, str) or not bw.gs.MESSAGE_ID_RE.fullmatch(message)
                or not isinstance(root, str) or (root and not bw.gs.MESSAGE_ID_RE.fullmatch(root))):
            return
        with Store(self.store_path) as store:
            store.enqueue_target(name, app, message, root, event['type'], now=int(time.time()))
    def retain_target(self, name: str, app: str, event: dict) -> None:
        self._retain_target_store(name, app, event)
        self.notify()

    async def retain_target_async(self, name: str, app: str, event: dict) -> None:
        await self._ledger_call(lambda: self._retain_target_store(name, app, event))
        self.notify()

    async def worker(self, name: str) -> None:
        while True:
            await self.wake[name].wait()
            await asyncio.sleep(DEBOUNCE)
            # Never wait for the global notice lane while holding a binding
            # lock. A waiting notice also must not hide a newly arrived message.
            notice_slot = False
            if self.dirty[name] == {'notice'}:
                if self._notice_lane.locked():
                    continue
                await self._notice_lane.acquire()
                notice_slot = True
            try:
                async with self.binding_locks[name]:
                    self.wake[name].clear()
                    if await self._ledger_call(lambda: self._binding_state(name)) == 'paused':
                        continue  # Keep dirty targets for resume.
                    dirty = set(self.dirty[name])
                    if not dirty:
                        continue
                    if dirty - {'notice'}:
                        dirty = dirty - {'notice'}
                    elif not notice_slot:
                        self.wake[name].set()
                        continue
                    self.dirty[name] -= dirty
                    threads, self.threads[name] = self.threads[name], set()
                    if not dirty:
                        continue
                    try:
                        await self._round(name, dirty, threads)
                    except Exception:
                        self.dirty[name] |= dirty
                        if 'feishu' in dirty:
                            self.threads[name] = None
                    if self.dirty[name]:
                        self.wake[name].set()
            finally:
                if notice_slot:
                    self._notice_lane.release()
            # Retry delays run separately and never hold the operation lock.

    def _retain_reaction_store(self, name, event):
        from hostd.reaction_inbox import retain
        binding = self.reg.bindings.get(name)
        worker = self.workers.get(name)
        author = event.get('pubkey')
        if binding is None or worker is None or author not in (worker.outlet_specs or {}):
            return
        with Store(self.store_path) as store:
            retain(store, name, binding.channel_id, author, event)

    def retain_outlet_author(self, name, author):
        worker = self.workers.get(name)
        if (worker is None or type(author) is not str or not bw.gs.HEX64_RE.fullmatch(author)
                or author not in (worker.outlet_specs or {})):
            return
        if not hasattr(self, 'outlet_author_hints'):
            self.outlet_author_hints = {}
        pending = self.outlet_author_hints.setdefault(name, {})
        if author not in pending and len(pending) < 256:
            pending[author] = None  # Duplicates never move the FIFO position.

    async def retain_reaction_async(self, name, event):
        if event.get('kind') in (7, 5):
            await self._ledger_call(lambda: self._retain_reaction_store(name, event))

    async def on_relay(self, name: str, ev: dict) -> None:
        self.trace(name, 'relay_received', source=ev.get('id') or '', kind=ev.get('kind'))
        if ev.get("type") == "_reconnected":
            self.workers[name].outlet_rescan = True
            self.mark(name, 'notice')
            self.workers[name].cached = {}
            self.workers[name].setup_at = 0
            self.mark(name, "members")
            self.mark(name, "buzz")
            self.mark(name, "feishu", catch_up=True)
            return
        if ev.get("pubkey") == self.mirrors[name]:
            return  # our own Feishu -> Buzz post
        await self.retain_reaction_async(name, ev)
        self.workers[name].outlet_rescan = True
        while not self.retain_notice_hint(name, ev):
            if not self._running:
                return
            self.mark(name, 'buzz')
            await asyncio.sleep(.1)  # Per-binding feed backpressure; no global lane lock.
        self.mark(name, "members" if ev.get("kind") in (9000, 9001) else "buzz")
        if ev.get('kind') in (9, 9000, 9001):
            self.mark(name, 'notice')
        created = ev.get('created_at')
        if ev.get('kind') in (9, 7, 5, 40003) and type(created) is int and 0 <= time.time() - created <= 30:
            self.retain_outlet_author(name, ev.get('pubkey'))
            self._round_slots.promote(name)

    async def on_feishu(self, app: str, ev: dict, by_chat: dict[str, str]) -> None:
        """Route trusted IPC without persisting event bodies or callback tokens."""
        if ev.get("app", app) != app:
            return
        t = ev.get("type")
        chat = ev.get("chat_id", "")
        if not isinstance(t, str) or not isinstance(chat, str):
            return
        if self.onboarding is not None and t in {'_connected', 'card.action.trigger', 'im.chat.member.bot.added_v1'}:
            # enqueue_feed performs no network I/O; actual callbacks/unbound
            # invitations must reach the durable coordinator before routing.
            answer = self.onboarding.enqueue_feed(app, ev)
            key = {'_connected':'connected', 'card.action.trigger':'card',
                   'im.chat.member.bot.added_v1':'invite'}[t]
            counts = self.status['apps'].setdefault(app, {}).setdefault('onboarding_events', {})
            outcome = 'queued' if isinstance(answer, dict) and answer.get('toast', {}).get('type') != 'error' else 'rejected'
            key += '_' + outcome
            counts[key] = min(10**9, counts.get(key, 0) + 1)
            # Optional diagnostics must not interrupt the original event route.
            with contextlib.suppress(OSError, ValueError, TypeError):
                self.save_status()
        if t in ("_connecting", "_connected", "_disconnected"):
            await self.set_status_async(app, "feishu", {"_connecting": "connecting", "_connected": "connected",
                                          "_disconnected": "disconnected"}[t])
            if t == "_connected":
                for name in by_chat.values():
                    self.mark(name, 'notice')
                    self.mark(name, "members")
                    self.mark(name, "feishu", catch_up=True)
            return
        name = by_chat.get(chat)
        target_type = t in {'im.message.receive_v1', 'im.message.reaction.created_v1',
                            'im.message.reaction.deleted_v1'}
        if not name and target_type and t != 'im.message.receive_v1' and not ev.get('chat_id'):
            # The actual reaction SDK supplies a message ID, without chat_id.
            # Each selected-reader binding verifies the message's real chat;
            # no chat association is invented from an app identity.
            for candidate in set(by_chat.values()):
                await self.retain_target_async(candidate, app, ev)
                self.mark(candidate, 'notice')
                self.mark(candidate, 'feishu', catch_up=True)
                self._round_slots.promote(candidate)
            return
        if not name or not isinstance(t, str):
            return
        if target_type:
            self.trace(name, 'feishu_received', source=ev.get('message_id') or '', sdk_at=ev.get('t'))
            await self.retain_target_async(name, app, ev)
        if t in {'im.chat.member.bot.added_v1', 'im.chat.member.bot.deleted_v1',
                 'im.chat.member.user.added_v1', 'im.chat.member.user.deleted_v1',
                 'im.chat.member.user.withdrawn_v1'}:
            self.mark(name, "members")
        self.mark(name, 'notice')
        if t == "im.message.receive_v1":
            root = ev.get("root_id") or ""
            known = getattr(self.workers[name], "known_threads", set())
            self.mark(name, "feishu", thread=root, catch_up=bool(root and root not in known))
        elif t.startswith("im.message.reaction"):
            # A reaction may identify only a reply whose missing root was never observed.
            self.mark(name, "feishu", catch_up=True)
        if target_type:self._round_slots.promote(name)
        self.status["apps"].setdefault(app, {})["last_event"] = time.time()
        self.save_status()

    async def feishu_child(self, app: str, bindings: list[registry.Binding]) -> None:
        profile = self.app_profiles.get(app)
        if profile is None:
            raise ValueError('应用凭据目录尚未核验；怎么解决：检查明确指定的本机应用。复制给 AI：检查 hostd 应用连接装配。')
        backoff = TASK_RETRY
        with app_lock(app, self.app_lock_dir):
            while True:
                argv=[sys.executable,str(HERE / "feishu_feed.py"),app,str(profile[0]),str(profile[1])]
                tap=self.sdk_evidence
                nonce=None
                if tap is not None and app in dict(tap.config.apps).values():
                    import secrets
                    nonce=secrets.token_hex(32)
                    argv.extend(['--sdk-evidence-run-id',tap.config.run_id,'--sdk-evidence-nonce',nonce])
                proc = await asyncio.create_subprocess_exec(*argv,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                if nonce is not None:tap.register_child(app,proc,argv,nonce)
                async def drain_stderr():
                    # SDK errors may contain URLs, payloads and tokens. Drain, never store raw text.
                    while await proc.stderr.read(8192):
                        self.status["apps"].setdefault(app, {})["diagnostic"] = (
                            "应用连接产生诊断信息；怎么解决：如持续断线，检查应用权限和网络。复制给 AI：检查 hostd 飞书应用连接。")

                stderr_task = asyncio.create_task(drain_stderr())
                try:
                    await self.set_status_async(app, "feishu", "connecting")
                    async for line in proc.stdout:
                        try:
                            ev = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        if not isinstance(ev, dict):
                            continue
                        # A newly approved binding joins an existing app feed;
                        # never keep the startup registry snapshot for dispatch.
                        by_chat = {b.chat_id: b.name for b in self.reg.bindings.values() if b.sync_app_id == app}
                        envelope=ev.pop('_sdk_evidence',None) if tap is not None else None
                        await self.on_feishu(app, ev, by_chat)
                        if tap is not None:
                            tap.enqueue(app,proc,envelope,ev)
                            self.status['sdk_evidence']=tap.readback()
                        if ev.get("type") == "_connected":
                            backoff = TASK_RETRY
                    await proc.wait()
                finally:
                    # Keep stderr draining until wait() reaps the process, including on cancellation.
                    if tap is not None:tap.child_ended(proc)
                    async def drain_stdout():
                        while await proc.stdout.read(8192):
                            pass
                    stdout_task = asyncio.create_task(drain_stdout())
                    try:
                        await stop_child(proc)
                    finally:
                        stdout_task.cancel()
                        await asyncio.gather(stdout_task, return_exceptions=True)
                    stderr_task.cancel()
                    await asyncio.gather(stderr_task, return_exceptions=True)
                await self.set_status_async(app, "feishu", "disconnected")
                self.status["apps"][app]["last_error"] = (
                    "应用连接已断开，正在重连；怎么解决：如持续断线，检查应用权限和网络。复制给 AI：检查 hostd 飞书应用断线。")
                self.save_status()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def supervise(self, component: str, name: str, start) -> None:
        """Restart one failed component while every sibling remains running."""
        while self._running:
            try:
                await start()
                if not self._running:
                    return
                raise RuntimeError("component returned unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._running:
                    return
                await self.set_status_async(name, component, "已排队重试；怎么解决：如持续失败，检查配置与权限。复制给 AI：检查 hostd 组件重试。")
                if component == "worker":
                    self.mark(name, "members")
                    self.mark(name, "buzz")
                    self.mark(name, "feishu", catch_up=True)
                await asyncio.sleep(TASK_RETRY)

    async def claims(self) -> None:
        while True:
            await asyncio.sleep(CLAIM_TICK)
            for name, w in self.workers.items():
                if time.time() - w.claim_at > bw.CLAIM_RENEW:
                    self.mark(name, "buzz")  # any run renews the claim when it is due

    def _spawn(self, component, identity, start):
        if not self._running:
            raise ValueError('主控尚未启动；怎么解决：先启动 hostd 再注册。复制给 AI：检查 hostd 动态注册时序。')
        task = asyncio.create_task(self.supervise(component, identity, start))
        self._tasks.add(task)
        return task

    def spawn_app(self, app):
        old = self.app_tasks.get(app)
        if old is not None and not old.done():
            return old
        if app not in self.app_profiles:
            raise ValueError('应用尚未核验；怎么解决：检查本机 bot 配置。复制给 AI：检查 hostd 应用注册。')
        bindings = [b for b in self.reg.bindings.values() if b.sync_app_id == app]
        self.set_status(app, 'feishu', 'connecting')
        self.app_tasks[app] = self._spawn('feishu', app, lambda: self.feishu_child(app, bindings))
        return self.app_tasks[app]

    def spawn_binding(self, name, *, restore_done=False):
        binding = self.reg.bindings[name]
        cfg = json.loads(read_owned(binding.config))
        tasks = self.binding_tasks.setdefault(name, {})
        if tasks.get('worker') is None or tasks['worker'].done():
            self.workers[name].last = {}
            self.status['bindings'][name]['runs'] = 0
            # Keep completed grants recoverable if this process exits before
            # the first round. Even a previously active binding is degraded
            # until the new worker proves readiness; runs still starts at 0.
            state = 'degraded' if restore_done and self._binding_state(name) in {'active','degraded'} else 'pending'
            self.binding_status(name, state)
            tasks['worker'] = self._spawn('worker', name, lambda: self.worker(name))
        if tasks.get('relay') is None or tasks['relay'].done():
            self.set_status(name, 'relay', 'connecting')
            trusted = self.onboarding_config.trusted_relays if self.onboarding_config is not None else ()
            tasks['relay'] = self._spawn('relay', name, lambda: relay_feed.follow(name, cfg['mirror_env_file'],
                binding.channel_id, self.on_relay, self.set_status_async, trusted_relays=trusted,
                replay_since=lambda: self._ledger_call(lambda: self.replay_since(name)), on_failure=self.set_failure_diagnostic))
        self.spawn_app(binding.sync_app_id)

    def check_own_profile(self, record):
        """Check a freshly reverified catalog candidate before admitting adapters."""
        if not self._running or self.onboarding is None or self.onboarding._closed:
            raise ValueError('own app recovery is stopped')
        profile=(str(record.lark_config_dir),str(record.lark_data_dir))
        old=self.app_profiles.get(record.app_id)
        if old is not None and tuple(map(str,old))!=profile:
            raise ValueError('own app recovery profile conflict')

    def register_own_app(self, record):
        """Start one recovered own feed; workers retain their current proof gates."""
        self.check_own_profile(record)
        if self.onboarding.records.get(record.app_id)!=record:
            raise ValueError('own app recovery record mismatch')
        client=self.onboarding.clients.get(record.app_id)
        spec=self.onboarding.effects.specs.get(record.pubkey)
        profile=(str(record.lark_config_dir),str(record.lark_data_dir))
        if (client is None or (str(client.config_dir),str(client.data_dir))!=profile
                or spec is None or (spec.pubkey,spec.owner_pubkey,spec.app_id)!=(record.pubkey,record.owner_pubkey,record.app_id)):
            raise ValueError('own app recovery adapter mismatch')
        from hostd.bot_admission import channel_candidate
        self.app_profiles[record.app_id]=profile
        self.spawn_app(record.app_id)
        for name,binding in self.reg.bindings.items():
            if channel_candidate(binding, record, self.store_path, self.onboarding.relay.owner, self.onboarding_config.trusted_relays):
                self.workers[name].outlet_rescan=True
                # The normal binding-locked round refreshes outlet specs and
                # starts their feeds; no slow all-binding work in admission.
                self.mark(name,'members');self.mark(name,'buzz')
        self.notify()

    async def start_onboarding(self):
        if self.onboarding_config is None or self.onboarding is not None:
            return
        from hostd.onboarding_runtime import OnboardingRuntime
        from hostd.runtime_registration import RuntimeRegistrar
        db = Store(self.store_path)
        service = None
        try:
            service = await OnboardingRuntime.create(self.onboarding_config, db,
                registrar=RuntimeRegistrar(self), scheduler=self.scheduler, http_pool=self.http_pool, base_env=self._env(), defer_issuers=True)
            profiles = dict(self.app_profiles)
            reviewed_profiles = getattr(service, 'app_profiles', None)
            if reviewed_profiles is None:
                reviewed_profiles = {app: (str(record.lark_config_dir), str(record.lark_data_dir))
                                     for app, record in service.records.items()}
            for app, candidate in reviewed_profiles.items():
                candidate = tuple(map(str, candidate))
                if app in profiles and tuple(map(str, profiles[app])) != candidate:
                    raise ValueError('同一个应用配置了不同凭据目录；怎么解决：明确唯一的本机 profile。复制给 AI：检查 hostd 重复应用。')
                profiles[app] = candidate
            from hostd.own_admission import OwnHomeAdmissionCoordinator
            from hostd.own_admission_runtime import OwnHomeAdmissionRuntime, NOTICE as ADMISSION_NOTICE
            if type(service) is not OnboardingRuntime:
                raise ValueError(ADMISSION_NOTICE)
            from hostd.agent_operations import ScopedProcessOps
            from hostd.agent_signed_reads import OwnAgentReader
            from hostd.bot_clients import BotLarkCli
            # Factories receive protected catalog records only. Physical/Source
            # reload those records; no public path/spec/proof is adopted here.
            def reader_for(record):
                return OwnAgentReader(record, origin=service.relay.origin,
                    relay_pubkey=service.config.relay_pubkey,
                    trusted_relays=service.config.trusted_relays,
                    clock=lambda: int(service.clock()), http=service.http)
            def bot_for(record):
                return BotLarkCli(record.app_id, record.lark_config_dir, record.lark_data_dir,
                    base_env=service._base_env, runner=service._runner,
                    scheduler=self.scheduler, http_pool=self.http_pool, chat_id=None)
            own_home_admission = OwnHomeAdmissionCoordinator(service.config.catalog_path,
                service.config.legacy_join_path, db, reader_factory=reader_for, bot_factory=bot_for,
                spec_getter=lambda pub: service.effects.specs.get(pub),
                operations=ScopedProcessOps(base_env=self._env()),
                trusted_relays=service.config.trusted_relays, clock=lambda: int(service.clock()))
            own_home_admission_runtime = OwnHomeAdmissionRuntime(service, own_home_admission,
                scheduler=self.scheduler, http_pool=self.http_pool)
            remote_dispatch = None
            if self.onboarding_config.remote_link_base:
                from hostd.remote_dispatch import RemoteDispatch
                remote_dispatch = RemoteDispatch(service, scheduler=self.scheduler,
                    http_pool=self.http_pool, base_env=self._env(), clock=lambda: int(service.clock()))
        except BaseException:
            if service is not None:
                service.close()
            db.close()
            raise
        self.runtime_store, self.onboarding, self.app_profiles = db, service, profiles
        self.remote_dispatch = remote_dispatch
        self.own_home_admission = own_home_admission
        self.own_home_admission_runtime = own_home_admission_runtime
        if self.restart_agent is None:
            from hostd.agent_operations import AgentRestartDriver
            self._restart_driver = AgentRestartDriver(db, lambda pub: service.effects.specs.get(pub))
            self.restart_agent = self._restart_driver

    async def close_onboarding(self):
        # Direct reopen/close callers share root's lifetime ordering: original
        # operations join while the actual specs, Store, pool and scheduler live.
        if self.own_home_admission_runtime is not None:
            await self.own_home_admission_runtime.close()
            self.own_home_admission_runtime = None
            self.own_home_admission = None
        if self.remote_dispatch is not None:
            await self.remote_dispatch.close()
            self.remote_dispatch = None
        if self.restart_agent is self._restart_driver:
            self.restart_agent = None
        self._restart_driver = None
        if self.onboarding is not None:
            self.onboarding.close()
            self.onboarding = None
        if self.runtime_store is not None:
            self.runtime_store.close()
            self.runtime_store = None

    def outlet_since(self, name, agent):
        with Store(self.store_path) as db:
            if db.has_replay_state(name, 'relay', agent_id=agent):
                return db.replay_since(name, 'relay', agent_id=agent)
            floors = [r[0] for r in db.conn.execute("SELECT value_int FROM state_scalar WHERE binding_id=? AND field IN ('floor','buzz_floor')", (name,))]
        return max(floors) if floors else max(0, int(self.status['started']) - bw.gs.BUZZ_OVERLAP_SECONDS)

    async def refresh_outlets(self, name):
        from hostd.bot_admission import channel_candidate
        binding = self.reg.bindings[name]
        specs = {}
        worker = self.workers[name]
        responsibilities = set(worker.outlet_responsibilities)
        # Protected local catalog records retain a declared own responsibility
        # even if the currently usable spec has disappeared. Public relay bots
        # are deliberately absent from this catalog/authority boundary.
        for record in self.onboarding.catalog.records:
            agent = record.pubkey
            if not agent or record.owner_pubkey != self.onboarding.relay.owner:
                continue
            if channel_candidate(binding, record, self.store_path, self.onboarding.relay.owner, self.onboarding_config.trusted_relays):
                responsibilities.add(agent)
            else:
                responsibilities.discard(agent)
        for agent, spec in self.onboarding.effects.specs.items():
            if channel_candidate(binding, spec, self.store_path, self.onboarding.relay.owner, self.onboarding_config.trusted_relays):
                specs[agent] = spec
        if (worker.outlet_specs != specs
                or worker.outlet_responsibilities != responsibilities | set(specs)):
            worker.outlet_rescan = True
        worker.outlet_responsibilities = responsibilities | set(specs)
        worker.outlet_specs = specs
        if name in getattr(self, 'outlet_author_hints', {}):
            self.outlet_author_hints[name] = {pub: None for pub in self.outlet_author_hints[name] if pub in specs}
        worker.trusted_relays = self.onboarding_config.trusted_relays
        # Bind task lifetime to current local channel authority. Removing an
        # outlet locally does not delete any Feishu/Buzz object.
        for key, task in list(self.outlet_tasks.items()):
            if key[0] == name and key[1] not in specs:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                del self.outlet_tasks[key]
                self.outlet_status[key] = 'stopped'
        for agent, spec in specs.items():
            key = (name, agent)
            old = self.outlet_tasks.get(key)
            if old is not None and not old.done():
                continue
            identity = 'outlet-' + hashlib.sha256((name + ':' + agent).encode()).hexdigest()[:48]
            async def event(_identity, value, name=name, agent=agent):
                await self.retain_reaction_async(name, value)
                self.workers[name].outlet_rescan = True
                self.mark(name, 'buzz')
                created = value.get('created_at')
                if value.get('kind') in (9, 7, 5, 40003) and type(created) is int and 0 <= time.time() - created <= 30:
                    self.retain_outlet_author(name, agent)
                    self._round_slots.promote(name)
            async def status(_identity, _kind, value, key=key, identity=identity):
                observed = value if value in {'connecting','connected','disconnected','stopped'} else 'failed'
                self.outlet_status[key] = observed
                def persist():
                    with Store(self.store_path) as db:
                        db.record_connection('outlet', identity, binding_id=key[0], status=observed,
                                             error_code='protocol' if observed == 'failed' else '', now=int(time.time()))
                await self._ledger_call(persist)
                self.notify()
            self.outlet_status[key] = 'connecting'
            self.outlet_tasks[key] = self._spawn('outlet', identity,
                lambda identity=identity, spec=spec, agent=agent, event=event, status=status: relay_feed.follow(
                    identity, spec.env_file, binding.channel_id, event, status,
                    trusted_relays=self.onboarding_config.trusted_relays,
                    replay_since=lambda: self._ledger_call(lambda: self.outlet_since(name, agent)), author=agent, status_kind='outlet',
                    on_failure=lambda _identity,_kind,diagnostic,name=name: self.set_failure_diagnostic(name,'outlet',diagnostic)))

    async def register_bound_outlet(self, row, plan, *, restore_done=False):
        """Adopt an approved own outlet without changing the existing sync role."""
        from hostd.config import load_config
        import buzz_agent_join_requests as legacy
        name = plan['binding_id']
        binding = self.reg.bindings.get(name)
        record = self.onboarding.records.get(row['callback_app_id']) if self.onboarding is not None else None
        spec = self.onboarding.effects.specs.get(row['agent_id']) if self.onboarding is not None else None
        if (not self._running or row['kind'] != 'channel' or binding is None
                or row['binding_id'] != name or binding.channel_id != plan['channel_id']
                or binding.chat_id != row['chat_id'] or str(binding.config) != plan['config_path']
                or plan['mirror_pubkey'] or record is None or spec is None
                or record.pubkey != row['agent_id'] or record.owner_pubkey != row['owner_pubkey']
                or spec.app_id != row['callback_app_id'] or spec.env_file != plan['secret_ref']):
            raise ValueError('已绑定群的发送器身份无法确认；怎么解决：核对原绑定、批准申请和独立 bot。复制给 AI：检查 hostd 已绑定群发送器接入。')
        cfg = load_config(binding.config)
        sync_app = cfg.get('sync_app_id') or cfg['agents'][cfg['desk_pubkey']]['app_id']
        from hostd.bot_admission import channel_candidate
        if (cfg['channel_id'] != binding.channel_id or cfg['chat_id'] != binding.chat_id
                or cfg['mirror_pubkey'] != self.mirrors[name] or sync_app != binding.sync_app_id
                or not channel_candidate(binding, spec, self.store_path, self.onboarding.relay.owner, self.onboarding_config.trusted_relays)):
            raise ValueError('原绑定或 agent 授权发生变化；怎么解决：重新核对配置和频道授权。复制给 AI：检查 hostd 已绑定发送器授权读回。')
        self.spawn_binding(name, restore_done=restore_done)
        self.spawn_app(row['callback_app_id'])
        await self.refresh_outlets(name)
        for phase in ('members', 'buzz', 'feishu'):
            self.mark(name, phase, catch_up=phase == 'feishu')
        self.notify()

    async def register_runtime_binding(self, row, plan, *, restore_done=False):
        """Accept only the exact approved config from the protected binding root."""
        from hostd.config import load_config
        path = Path(plan['config_path'])
        expected = Path(self.onboarding_config.binding_dir) / row['request_id'] / 'config.json'
        if (not self._running or path != expected or plan['binding_id'] != row['request_id']):
            raise ValueError('绑定注册范围无法确认；怎么解决：检查已批准申请的固定路径。复制给 AI：检查 hostd 动态绑定配置。')
        cfg = load_config(path)
        record = self.onboarding.records.get(row['callback_app_id'])
        agent = cfg['agents'].get(row['agent_id'], {})
        if (record is None or cfg['channel_id'] != plan['channel_id'] or cfg['chat_id'] != row['chat_id']
                or cfg['mirror_pubkey'] != plan['mirror_pubkey'] or cfg['desk_pubkey'] != row['agent_id']
                or agent.get('app_id') != row['callback_app_id']
                or agent.get('lark_config_dir') != str(record.lark_config_dir)
                or agent.get('lark_data_dir') != str(record.lark_data_dir)):
            raise ValueError('绑定身份与申请不一致；怎么解决：核对配置与独立应用。复制给 AI：检查 hostd 注册身份。')
        name = plan['binding_id']
        binding = registry.Binding(name, path, path.parent / 'state', cfg['channel_id'], cfg['chat_id'],
            row['callback_app_id'], agent['lark_config_dir'], agent['lark_data_dir'], registry._relay_url(cfg['mirror_env_file']))
        old = self.reg.bindings.get(name)
        if old is not None and old != binding:
            raise ValueError('绑定标识发生冲突；怎么解决：核对原注册记录。复制给 AI：检查 hostd 重复绑定。')
        for other in self.reg.bindings.values():
            if other.name != name and (other.chat_id == binding.chat_id or other.channel_id == binding.channel_id):
                raise ValueError('群或频道已被占用；怎么解决：核对现有认领。复制给 AI：检查 hostd 绑定冲突。')
        if old is None:
            with Store(self.store_path) as db:
                db.reconcile_bindings([BindingRecord(name, binding.channel_id, binding.chat_id, binding.sync_app_id,
                    str(path), binding.lark_config_dir, binding.lark_data_dir, cfg['mirror_pubkey'], status='pending')], now=int(time.time()))
            self.reg.bindings[name] = binding
            self.workers[name] = bw.Worker(path, binding.state_dir, base_env=self._env(), store_path=self.store_path,
                binding_id=name, scheduler=self.scheduler, claim_relay_pubkey=self._claim_relay_pubkey())
            self.workers[name].http_pool = self.http_pool
            self.workers[name].latency_trace = self.latency_trace
            self.notice_hints[name] = set()
            self.dirty[name], self.threads[name] = set(), set()
            self.wake[name], self.binding_locks[name] = asyncio.Event(), asyncio.Lock()
            self.mirrors[name] = cfg['mirror_pubkey']
            self.status['bindings'][name] = {'relay':'connecting','last_run':None,'last_error':None,'runs':0}
        self.spawn_binding(name, restore_done=restore_done)
        for phase in ('members','buzz','feishu'):
            self.mark(name, phase, catch_up=phase == 'feishu')
        self.notify()

    async def _shutdown(self):
        self._running = False
        if self.own_home_admission_runtime is not None:
            self.own_home_admission_runtime.stop()
        if self.remote_dispatch is not None:
            self.remote_dispatch.stop()
        if self.onboarding is not None:
            self.onboarding.close()  # Refuse new work, retaining its Store for dispatched IO.
        # Console operations and Worker/onboarding IO must finish while the
        # existing SDK feeds still hold the cross-process application locks.
        await self.close_console()
        feeds = set(self.app_tasks.values())
        jobs = self._tasks - feeds
        for task in jobs:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        for task in feeds:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*feeds, return_exceptions=True)
        if self.remote_dispatch is not None:
            await self.remote_dispatch.close()
            self.remote_dispatch = None
        await self.close_onboarding()
        if self.sdk_evidence is not None:await self.sdk_evidence.close()
        self.http_pool.close()
        self.scheduler.close()
        if self._round_executor is not None:
            # Every dispatched round has been joined by the job gather above.
            self._round_executor.shutdown(wait=True)
            self._round_executor = None
        self._ledger_closed = True
        executor = getattr(self, '_ledger_executor', None)
        if executor is not None:
            # Feed/job gathers above have joined every dispatched ledger call.
            executor.shutdown(wait=True)
            self._ledger_executor = None

    async def main(self) -> None:
        self._running = True
        sources = None
        try:
            if self.sdk_evidence is not None:await self.sdk_evidence.start()
            await self.start_console()
            self.trace('hostd', 'startup_onboarding_started')
            with contextlib.suppress(OSError, ValueError, TypeError):
                self.save_status()
            await self.start_onboarding()
            self.trace('hostd', 'startup_onboarding_finished')
            if self.onboarding is not None:
                from hostd.runtime_registration import RuntimeRegistrar
                self.trace('hostd', 'startup_restore_started')
                restored = await RuntimeRegistrar(self).restore()
                self.trace('hostd', 'startup_restore_finished')
                self.status['onboarding_restore'] = restored
                if restored['notice']:
                    self.onboarding.last_notice = restored['notice']
            for name in list(self.reg.bindings):
                self.spawn_binding(name)
            if self.onboarding is not None:
                for app in self.onboarding.eligible_apps:
                    self.spawn_app(app)
                self._spawn('onboarding', 'onboarding', self.onboarding.run)
                if self.own_home_admission_runtime is not None:
                    self._spawn('admission', 'own-home-admission', self.own_home_admission_runtime.run)
                if self.remote_dispatch is not None:
                    self._spawn('remote', 'remote', self.remote_dispatch.run)
            self.trace('hostd', 'startup_feeds_started')
            self._spawn('claims', 'claims', self.claims)
            self._spawn('notice', 'delivery-notices', self.notices)
            for n in self.reg.bindings:
                self.mark(n, "members"); self.mark(n, "buzz"); self.mark(n, "feishu", catch_up=True)
            sources = asyncio.gather(*self._tasks)
            # Cancelling the root must not implicitly cancel feeds and release
            # their app locks before the outbound executor tasks are reaped.
            await asyncio.shield(sources)
        finally:
            self._running = False
            cleanup = asyncio.create_task(self._shutdown())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue  # A second signal still cannot abandon dispatched IO.
            cleanup.result()
            if sources is not None:
                with contextlib.suppress(BaseException):
                    sources.result()  # Retrieve the gather's final cancellation/error.


async def run_until_stopped(h: Hostd, stop: asyncio.Event | None = None) -> None:
    stop = stop if stop is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(h.main())
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait((task, stopping), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task  # propagate fatal root failure to systemd immediately
            raise RuntimeError("hostd stopped unexpectedly")
    finally:
        for pending in (task, stopping):
            if not pending.done() and not pending.cancelling():
                pending.cancel()
        await asyncio.gather(task, stopping, return_exceptions=True)
        for sig in signals:
            loop.remove_signal_handler(sig)


def main() -> int:
    ap = argparse.ArgumentParser(prog="hostd")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--only", default="", help="comma-separated binding names (default: all)")
    r.add_argument("--status-file", default=str(STATE_DIR / "status.json"))
    r.add_argument("--console-dir", help='private Unix console directory (default: $XDG_RUNTIME_DIR/buzz-hostd)')
    r.add_argument("--state-db", default=str(STATE_DIR / "hostd.sqlite3"))
    r.add_argument("--migrate-bot-readers", action="store_true",
                   help="after preserving rollback backups, verify and migrate imported identity caches to the sync bot")
    r.add_argument('--onboarding-config', type=Path, help='protected explicit owner/relay/catalog configuration for card-only onboarding')
    r.add_argument('--latency-trace-file', type=Path, help='optional private bounded metadata-only timing capture')
    r.add_argument('--round-workers', type=int, default=4,
                   help='synchronous binding round workers, 1..16 (default: 4; HTTP limits unchanged)')
    r.add_argument('--round-live-reserve', type=int,
                   help='slots reserved from background rounds, 0..workers-1 (default: 1, or 0 with one worker)')
    r.add_argument('--sdk-evidence-config',type=Path)
    r.add_argument('--sdk-evidence-config-sha256')
    a = ap.parse_args()
    try:
        a.round_live_reserve = RoundSlots(a.round_workers, a.round_live_reserve).live_reserve
    except ValueError as error:
        ap.error(str(error))
    latency_trace=None
    if a.latency_trace_file is not None:
        from hostd.latency_trace import LatencyTrace
        latency_trace=LatencyTrace(a.latency_trace_file)
    sdk_evidence=None
    if (a.sdk_evidence_config is None)!=(a.sdk_evidence_config_sha256 is None):ap.error('SDK evidence requires both explicit config and independent SHA-256')
    if a.sdk_evidence_config is not None:
        from hostd.sdk_evidence import EvidenceConfig
        sdk_evidence=EvidenceConfig.check(a.sdk_evidence_config,a.sdk_evidence_config_sha256)
    console_dir = Path(a.console_dir) if a.console_dir is not None else default_console_dir()
    only = {x for x in a.only.split(",") if x} or None
    reg = registry.load(only=only)
    STATE_DIR.mkdir(parents=True, exist_ok=True); os.chmod(STATE_DIR, 0o700)
    onboarding = None
    if a.onboarding_config is not None:
        from hostd.onboarding_runtime import RuntimeConfig
        onboarding = RuntimeConfig.load(a.onboarding_config)
    h = Hostd(reg, Path(a.status_file), state_db=Path(a.state_db), migrate_bot_readers=a.migrate_bot_readers,
              console_dir=console_dir, onboarding_config=onboarding,sdk_evidence_config=sdk_evidence,latency_trace=latency_trace,
              round_workers=a.round_workers, round_live_reserve=a.round_live_reserve)
    asyncio.run(run_until_stopped(h))
    return 0


if __name__ == "__main__":
    sys.exit(main())
