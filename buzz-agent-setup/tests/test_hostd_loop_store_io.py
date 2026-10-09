"""Real temporary SQLite writer contention must not stop the SDK/relay event loop."""
import asyncio
import concurrent.futures
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
import test_hostd_relay_malformed as relay_fixture
SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from hostd.store import Store, BindingRecord
spec=importlib.util.spec_from_file_location('loop_store_hostd',SCRIPTS/'hostd/__main__.py')
hd=importlib.util.module_from_spec(spec)
# Existing source-shaped transport import boundary also works without websockets.
with mock.patch.dict(sys.modules,{'relay_feed':relay_fixture.relay}):
    spec.loader.exec_module(hd)

class LoopStoreIO(unittest.IsolatedAsyncioTestCase):
    async def test_original_sdk_status_keeps_heartbeat_alive_during_real_writer_contention(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'state.db'
            with Store(path):pass
            ready=threading.Event();release=threading.Event();settled=threading.Event();errors=[]
            def writer():
                try:
                    with Store(path) as db:
                        with db.transaction():
                            ready.set();release.wait(1)
                except BaseException as error:errors.append(error)
                finally:settled.set()
            thread=threading.Thread(target=writer);thread.start()
            host=object.__new__(hd.Hostd)
            host.status={'apps':{},'bindings':{}};host.onboarding=None
            host.store_path=path;host.reg=types.SimpleNamespace(bindings={})
            host.save_status=lambda:None;host.notify=lambda:None
            beats=[]
            def heartbeat():
                beats.append(not settled.is_set());release.set()
            timer=None
            try:
                self.assertTrue(ready.wait(3),'fixture writer never acquired original SQLite transaction')
                timer=asyncio.get_running_loop().call_later(.03,heartbeat)
                await host.on_feishu('cli_test',{'type':'_connected'}, {})
                await asyncio.sleep(0)
                self.assertEqual(beats,[True],'main loop could not run heartbeat before original writer settled')
                with Store(path) as db:
                    row=db.conn.execute("SELECT status FROM connection WHERE identity='cli_test'").fetchone()
                    self.assertEqual(row[0],'connected')
            finally:
                if timer is not None:timer.cancel()
                release.set();thread.join(3)
                self.assertFalse(thread.is_alive(),'original fixture writer not joined')
                executor=getattr(host,'_ledger_executor',None)
                if executor is not None:executor.shutdown(wait=True)
                if errors:raise errors[0]

    async def test_cancellation_joins_dispatched_store_io_before_propagating(self):
        with tempfile.TemporaryDirectory() as temporary:
            host=object.__new__(hd.Hostd);path=Path(temporary)/'state.db'
            entered=threading.Event();release=threading.Event();closed=threading.Event()
            def write():
                try:
                    with Store(path) as db:
                        with db.transaction():
                            entered.set();release.wait(3)
                            db.record_connection('relay','fixture',status='connected',now=1)
                finally:closed.set()
            task=asyncio.create_task(host._ledger_call(write))
            try:
                for _ in range(300):
                    if entered.is_set():break
                    await asyncio.sleep(.001)
                self.assertTrue(entered.is_set())
                task.cancel();await asyncio.sleep(.01);task.cancel();await asyncio.sleep(.01)
                self.assertFalse(task.done(),'cancel escaped with original Store still owned by IO thread')
                self.assertFalse(closed.is_set())
                release.set()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertTrue(closed.is_set())
                with Store(path) as db:
                    self.assertEqual(db.conn.execute('SELECT status FROM connection').fetchone()[0],'connected')
            finally:
                release.set()
                await asyncio.gather(task,return_exceptions=True)
                host._ledger_executor.shutdown(wait=True)

    async def test_ledger_lane_does_not_queue_behind_default_round_dns_executor(self):
        host=object.__new__(hd.Hostd)
        default=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        asyncio.get_running_loop().set_default_executor(default)
        entered=threading.Event();release=threading.Event()
        def busy():entered.set();release.wait(3)
        pending=asyncio.get_running_loop().run_in_executor(None,busy)
        try:
            for _ in range(300):
                if entered.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(entered.is_set())
            observed=await host._ledger_call(lambda:(threading.get_ident(),threading.current_thread().name))
            self.assertNotEqual(observed[0],threading.get_ident())
            self.assertTrue(observed[1].startswith('hostd-loop-ledger'))
            self.assertFalse(pending.done(),'control default lane was not occupied')
        finally:
            release.set();await pending
            executor=getattr(host,'_ledger_executor',None)
            if executor is not None:executor.shutdown(wait=True)

    async def test_closed_lane_refuses_new_io_and_original_io_exception_preserved(self):
        host=object.__new__(hd.Hostd)
        original=ValueError('fixture failure')
        def failure():raise original
        try:
            with self.assertRaises(ValueError) as caught:await host._ledger_call(failure)
            self.assertIs(caught.exception,original)
            host._ledger_closed=True
            with self.assertRaises(RuntimeError):await host._ledger_call(lambda:None)
        finally:host._ledger_executor.shutdown(wait=True)

    async def test_round_failure_uses_ledger_lane_and_preserves_original_exception(self):
        with tempfile.TemporaryDirectory() as temporary:
            host=object.__new__(hd.Hostd);host.store_path=Path(temporary)/'state.db'
            with Store(host.store_path) as db:
                db.reconcile_bindings([BindingRecord('binding','channel_test','oc_test','cli_test',
                    '/fixture/config','/fixture/profile','/fixture/data')],now=1)
            original=ValueError('fixture round error')
            def failure(*args,**kwargs):raise original
            host.workers={'binding':types.SimpleNamespace(run=failure,notice_sources=())}
            host.onboarding=None;host.notice_clock=lambda:1
            host.dirty={'binding':set()};host.threads={'binding':set()}
            host._round_slots=__import__('hostd.round_slots',fromlist=['RoundSlots']).RoundSlots(4);host._round_executor=None
            self.addCleanup(lambda:host._round_executor.shutdown(wait=True))
            host.status={'bindings':{'binding':{'runs':0}},'apps':{}}
            host.notice_hints={'binding':set()};host.notice_overflow=set()
            host.save_status=lambda:None;host.notify=lambda:None
            retries=[];host.schedule_retry=lambda *args:retries.append(args)
            calls=[];original_call=host._ledger_call
            async def observed(call):calls.append(True);return await original_call(call)
            host._ledger_call=observed
            try:
                with self.assertRaises(ValueError) as caught:await host._round('binding',{'buzz'},set())
                self.assertIs(caught.exception,original)
                self.assertEqual(calls,[True])
                self.assertEqual(retries[0][0:2],('binding',{'buzz'}))
                with Store(host.store_path) as db:
                    self.assertEqual(db.bindings()[0]['status'],'degraded')
            finally:host._ledger_executor.shutdown(wait=True)

class AwaitableRelay(relay_fixture.MalformedRelay):
    async def test_async_replay_and_status_preserve_exact_auth_subscription(self):
        socket=relay_fixture.Socket([]);statuses=[];requested=[]
        async def provider():requested.append(True);return 123
        async def status(name,kind,value):statuses.append((name,kind,value))
        async def event(*args):pass
        async def stop(*args):raise asyncio.CancelledError()
        with mock.patch.object(relay_fixture.relay,'_connect',return_value=socket),mock.patch.object(relay_fixture.relay.asyncio,'sleep',side_effect=stop):
            with self.assertRaises(asyncio.CancelledError):
                await relay_fixture.relay.follow('binding',str(self.env),'channel-a',event,status,
                    trusted_relays=('wss://relay.test',),replay_since=provider)
        self.assertEqual(requested,[True])
        self.assertIn(('binding','relay','connected'),statuses)
        filters=[row[2] for row in socket.sent if row[0]=='REQ']
        self.assertEqual(len(filters),1)
        self.assertEqual(filters[0]['since'],123)
        self.assertEqual(filters[0]['#h'],['channel-a'])

    async def test_bad_async_replay_never_authenticates_or_subscribes(self):
        for value in (True,-1,'123'):
            socket=relay_fixture.Socket([])
            async def provider():return value
            async def status(*args):pass
            async def event(*args):pass
            async def stop(*args):raise asyncio.CancelledError()
            with mock.patch.object(relay_fixture.relay,'_connect',return_value=socket),mock.patch.object(relay_fixture.relay.asyncio,'sleep',side_effect=stop):
                with self.assertRaises(asyncio.CancelledError):
                    await relay_fixture.relay.follow('binding',str(self.env),'channel-a',event,status,
                        trusted_relays=('wss://relay.test',),replay_since=provider)
            self.assertFalse(any(row[0] in ('AUTH','REQ') for row in socket.sent))

if __name__=='__main__':unittest.main()
