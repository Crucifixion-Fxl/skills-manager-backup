"""Regressions for authentication starvation observed with 17 live bindings."""
import asyncio
import concurrent.futures
import tempfile
from pathlib import Path
from unittest import TestCase, mock

import test_hostd_relay_malformed as fixture
from hostd.store import Store


class ReplayBeforeConnect(fixture.MalformedRelay):
    async def test_cursor_is_ready_before_opening_authentication_socket(self):
        # ID: L2-HOSTD-READINESS-001
        # AC: Ledger contention must not consume the relay authentication window.
        cursor_ready = False
        observations = []
        socket = fixture.Socket([])

        async def cursor():
            nonlocal cursor_ready
            await asyncio.sleep(0)
            cursor_ready = True
            return 123

        def connect(url):
            observations.append(cursor_ready)
            return socket

        async def event(*args):
            raise asyncio.CancelledError

        with mock.patch.object(fixture.relay, '_connect', side_effect=connect):
            with self.assertRaises(asyncio.CancelledError):
                await fixture.relay.follow('binding', str(self.env), 'channel-a', event,
                    lambda *args: None, trusted_relays=('wss://relay.test',), replay_since=cursor)
        self.assertEqual(observations, [True])
        self.assertEqual(next(row[2]['since'] for row in socket.sent if row[0] == 'REQ'), 123)


class ConcurrentMetadataRead(TestCase):
    def test_current_schema_opens_and_reads_while_another_writer_holds_wal(self):
        # ID: L2-HOSTD-READINESS-002
        # AC: Reading replay metadata must not acquire the global writer lock.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state' / 'hostd.db'
            with Store(path) as writer, concurrent.futures.ThreadPoolExecutor(1) as pool:
                def read():
                    with Store(path) as reader:
                        return reader.conn.execute('PRAGMA user_version').fetchone()[0]
                with writer.transaction():
                    pending = pool.submit(read)
                    try:
                        version = pending.result(timeout=1)
                        completed_with_writer = True
                    except concurrent.futures.TimeoutError:
                        completed_with_writer = False
                pending.result(timeout=12)
                self.assertTrue(completed_with_writer, 'read-only schema admission waited for a writer')
                self.assertGreater(version, 0)


class ResolverIsolation(__import__('unittest').IsolatedAsyncioTestCase):
    async def test_blocked_round_does_not_queue_dns_behind_binding_io(self):
        # ID: L2-HOSTD-READINESS-003
        # AC: A real DNS lookup completes while a binding round is blocked.
        import threading
        import test_hostd_wiring_lifecycle as wiring
        wiring.Wiring.setUp(self)
        loop = asyncio.get_running_loop()
        loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=1))
        entered, release = threading.Event(), threading.Event()
        def run(*args, **kwargs):
            entered.set()
            release.wait(3)
            return {'hostd': {'verdict': 'off'}}
        self.h.workers['alpha'].run = run
        task = asyncio.create_task(self.h._round('alpha', {'feishu'}, None))
        try:
            for _ in range(100):
                if entered.is_set(): break
                await asyncio.sleep(.005)
            self.assertTrue(entered.is_set())
            answer = await asyncio.wait_for(loop.getaddrinfo('localhost', 443), .4)
            self.assertTrue(answer)
            self.assertFalse(release.is_set())
        finally:
            release.set()
            await task
            await self.h._shutdown()

    async def test_waiting_notice_yields_to_live_phase_without_losing_notice(self):
        from hostd.round_slots import RoundSlots
        import test_hostd_wiring_lifecycle as wiring
        wiring.Wiring.setUp(self)
        self.h._round_slots=RoundSlots(1)
        hold=self.h._round_slots.slot('occupied');await hold.__aenter__()
        task=asyncio.create_task(self.h._round('beta',{'notice'},set()))
        try:
            await asyncio.sleep(.01)
            self.h.mark('beta','feishu',thread='new-thread')
            self.h._round_slots.promote('beta')
            await hold.__aexit__(None,None,None)
            await task
            self.assertEqual(self.h.workers['beta'].calls,[])
            self.assertEqual(self.h.dirty['beta'],{'notice','feishu'})
            await self.h._round('beta',{'feishu'},set())
            self.assertEqual(self.h.workers['beta'].calls,[({'feishu'},{'new-thread'})])
            self.assertEqual(self.h.dirty['beta'],{'notice'})
        finally:
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
            await self.h._shutdown()

    async def test_waiting_round_is_cancelled_without_starting_worker(self):
        import threading
        import test_hostd_wiring_lifecycle as wiring
        wiring.Wiring.setUp(self)
        self.h._round_slots = __import__('hostd.round_slots',fromlist=['RoundSlots']).RoundSlots(1)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def first(*args, **kwargs):
            entered.set()
            release.wait(3)
            return {'hostd': {'verdict': 'off'}}
        def second(*args, **kwargs):
            calls.append('unexpected dispatch')
            return {'hostd': {'verdict': 'off'}}
        self.h.workers['alpha'].run = first
        self.h.workers['beta'].run = second
        self.h.notice_hints['beta'] = {'a' * 64}
        first_task = asyncio.create_task(self.h._round('alpha', {'feishu'}, None))
        second_task = None
        try:
            for _ in range(100):
                if entered.is_set(): break
                await asyncio.sleep(.005)
            self.assertTrue(entered.is_set())
            second_task = asyncio.create_task(self.h._round('beta', {'feishu'}, None))
            await asyncio.sleep(.03)
            self.assertFalse(second_task.done())
            second_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await second_task
            self.assertEqual(calls, [])
        finally:
            release.set()
            await first_task
            if second_task:await asyncio.gather(second_task, return_exceptions=True)
            await self.h._shutdown()
        self.assertEqual(calls, [])
        self.assertIsNone(self.h._round_executor)
        self.assertEqual(self.h.notice_hints['beta'], {'a' * 64})
