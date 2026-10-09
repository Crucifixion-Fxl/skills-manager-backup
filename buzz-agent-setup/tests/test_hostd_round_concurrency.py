"""Real round executor/slots; only the synchronous external IO is offline."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import test_hostd_wiring_lifecycle as fixture
from hostd.round_slots import RoundSlots
from hostd.scheduler import RateLimits
from hostd.store import Store

hd = fixture.hd


def registry_at(directory, names):
    reg = fixture.registry.Registry()
    for name in names:
        config = directory / (name + '.json')
        config.write_text(json.dumps({'mirror_pubkey': fixture.old.base.MIRROR_PK,
                                      'mirror_env_file': 'unused'}))
        config.chmod(0o600)
        reg.bindings[name] = fixture.registry.Binding(name, config, directory / name,
            'ch_' + name, 'oc_' + name, 'cli_' + name,
            str(directory / ('cfg_' + name)), str(directory / ('data_' + name)), 'ws://localhost')
    return reg


class Configuration(unittest.TestCase):
    def test_bounds_and_single_worker_compatibility(self):
        self.assertEqual((RoundSlots().capacity, RoundSlots().live_reserve), (4, 1))
        self.assertEqual((RoundSlots(1).live_reserve, RoundSlots(1).background_limit), (0, 1))
        self.assertEqual(RoundSlots(8, 2).background_limit, 6)
        self.assertEqual(RoundSlots(16, 0).background_limit, 16)
        for workers, reserve in [(0, None), (17, None), (True, None), (4.0, None),
                                 (8, -1), (8, 8), (8, True), (8, 2.0), (1, 1)]:
            with self.subTest(workers=workers, reserve=reserve), self.assertRaises(ValueError):
                RoundSlots(workers, reserve)

    def test_invalid_cli_refuses_before_registry_or_runtime_writes(self):
        for args in [('--round-workers', '0'), ('--round-workers', '17'),
                     ('--round-workers', 'bad'), ('--round-live-reserve', '-1'),
                     ('--round-live-reserve', '4'), ('--round-live-reserve', '1.5'),
                     ('--round-workers', '1', '--round-live-reserve', '1')]:
            with self.subTest(args=args), mock.patch('sys.argv', ['hostd', 'run', *args]), \
                 mock.patch.object(hd.registry, 'load') as load, \
                 mock.patch.object(hd, 'Hostd') as host, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    hd.main()
                self.assertEqual(caught.exception.code, 2)
                load.assert_not_called()
                host.assert_not_called()

    def test_actual_cli_configures_slots_and_actual_executor(self):
        for args, expected in [([], (4, 1)), (['--round-workers', '1'], (1, 0)),
                               (['--round-workers', '8', '--round-live-reserve', '2'], (8, 2))]:
            with self.subTest(args=args), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                reg = registry_at(directory, ['alpha'])
                observed = []

                async def exercise(host):
                    try:
                        await host._round('alpha', {'buzz'}, set())
                        observed.append((host._round_slots.capacity, host._round_slots.live_reserve))
                        self.assertEqual(host._round_executor._max_workers, expected[0])
                        self.assertEqual(host.scheduler.limits, RateLimits())
                        self.assertIs(host.http_pool.scheduler, host.scheduler)
                    finally:
                        await host._shutdown()

                argv = ['hostd', 'run', '--console-dir', str(directory / 'console'),
                        '--state-db', str(directory / 'state.db'), '--status-file', str(directory / 'status.json'), *args]
                with mock.patch('sys.argv', argv), mock.patch.object(hd, 'STATE_DIR', directory / 'state'), \
                     mock.patch.object(hd.registry, 'load', return_value=reg), \
                     mock.patch.object(hd.bw, 'Worker', fixture.FakeWorker), \
                     mock.patch.object(hd, 'run_until_stopped', exercise):
                    self.assertEqual(hd.main(), 0)
                self.assertEqual(observed, [expected])


class ExecutorConcurrency(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.names = [f'background{i}' for i in range(8)] + ['live0', 'live1']
        root = Path(self.temp.name)
        with mock.patch.object(hd.bw, 'Worker', fixture.FakeWorker):
            self.host = hd.Hostd(registry_at(root, self.names), root / 'status.json',
                                 round_workers=8, round_live_reserve=2)
        self.host.app_lock_dir = root / 'locks'
        self.release = {name: threading.Event() for name in self.names}
        self.started = []
        self.active = set()
        self.maximum = 0
        self.thread_ids = set()
        self.guard = threading.Lock()

        def run_for(name):
            def run(*args, **kwargs):
                with self.guard:
                    self.started.append(name)
                    self.active.add(name)
                    self.maximum = max(self.maximum, len(self.active))
                    self.thread_ids.add(threading.get_ident())
                try:
                    if not self.release[name].wait(10):
                        raise AssertionError('offline round was not released')
                    return {'hostd': {'verdict': 'off'}}
                finally:
                    with self.guard:
                        self.active.remove(name)
            return run

        for name, worker in self.host.workers.items():
            worker.run = run_for(name)
        self.debounce = mock.patch.object(hd, 'DEBOUNCE', .001)
        self.debounce.start()
        self.addCleanup(self.debounce.stop)

    async def asyncTearDown(self):
        for gate in self.release.values():
            gate.set()
        await asyncio.wait_for(self.host._shutdown(), 5)

    async def until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(.005)
        await asyncio.wait_for(wait(), 3)

    def start_binding(self, name):
        task = asyncio.create_task(self.host.worker(name))
        self.host._tasks.add(task)
        self.host.mark(name, 'buzz')
        return task

    async def fill_background(self):
        for name in self.names[:8]:
            self.start_binding(name)
        await self.until(lambda: len(self.started) == 6 and len(self.host._round_slots.waiting) == 2)
        self.assertEqual(len(self.active), 6)
        self.assertTrue(all(name.startswith('background') for name in self.active))

    async def test_six_background_rounds_leave_two_actual_executor_threads_for_live_work(self):
        await self.fill_background()
        background_started = set(self.started)
        for name in ['live0', 'live1']:
            self.host._round_slots.promote(name)
            self.start_binding(name)
        await self.until(lambda: len(self.active) == 8)
        self.assertEqual(set(self.started), background_started | {'live0', 'live1'})
        self.assertEqual(len(self.thread_ids), 8, 'the executor must actually run all eight admitted rounds')
        self.assertEqual(self.host._round_executor._max_workers, 8)
        self.assertEqual(sum(not ticket.live for ticket in self.host._round_slots.active), 6)
        self.assertEqual(len(self.host._round_slots.waiting), 2)
        for gate in self.release.values():
            gate.set()
        await self.until(lambda: len(self.started) == 10 and not self.active)
        self.assertLessEqual(self.maximum, 8)

    async def test_same_binding_remains_serial_even_with_eight_workers(self):
        first = self.start_binding('background0')
        await self.until(lambda: self.started == ['background0'])
        # Even a second caller and more events cannot bypass the binding lock.
        second = asyncio.create_task(self.host.worker('background0'))
        self.host._tasks.add(second)
        self.host.mark('background0', 'feishu')
        self.host._round_slots.promote('background0')
        await asyncio.sleep(.03)
        self.assertEqual(self.started, ['background0'])
        self.assertTrue(self.host.binding_locks['background0'].locked())
        self.release['background0'].set()
        await self.until(lambda: len(self.started) == 2 and not self.active)
        self.assertEqual(self.maximum, 1)
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)

    async def test_paused_queued_binding_cannot_use_a_reserved_live_slot(self):
        await self.fill_background()
        paused = self.host._round_slots.waiting[0].name
        with Store(self.host.store_path) as store:
            store.conn.execute("UPDATE binding SET status='paused' WHERE binding_id=?", (paused,))
        self.host._round_slots.promote(paused)
        await self.until(lambda: all(ticket.name != paused for ticket in
                                    self.host._round_slots.waiting + self.host._round_slots.active))
        self.assertNotIn(paused, self.started)
        self.assertIn('buzz', self.host.dirty[paused])
        self.assertEqual(len(self.active), 6)

    async def test_shutdown_cancels_waiters_but_joins_all_eight_dispatched_threads(self):
        await self.fill_background()
        for name in ['live0', 'live1']:
            self.host._round_slots.promote(name)
            self.start_binding(name)
        await self.until(lambda: len(self.active) == 8)
        admitted = set(self.started)
        shutdown = asyncio.create_task(self.host._shutdown())
        try:
            await self.until(lambda: not self.host._round_slots.waiting)
            self.assertFalse(shutdown.done())
            self.assertEqual(set(self.started), admitted)
            self.assertTrue(all(self.host.binding_locks[name].locked() for name in admitted))
        finally:
            for gate in self.release.values():
                gate.set()
            await asyncio.wait_for(shutdown, 5)
        self.assertEqual(set(self.started), admitted, 'shutdown must not dispatch queued background work')
        self.assertFalse(self.active or self.host._round_slots.active or self.host._round_slots.waiting)
        self.assertIsNone(self.host._round_executor)


if __name__ == '__main__':
    unittest.main()
