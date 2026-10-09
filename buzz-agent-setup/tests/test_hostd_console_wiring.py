"""Actual Unix API, SQLite and worker threads; low-level round effects are faked."""
import asyncio
import json
import secrets
import threading
import time
from unittest import mock
import unittest
import test_hostd_wiring_lifecycle as fixture
from store import Store

class ConsoleWiring(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture.Wiring.setUp(self)
        self.h.console_dir=self.tmp/'console'
        await self.h.start_console()
        self.token=self.h.console.token_path.read_text().strip()
        self.tasks=[]
    async def asyncTearDown(self):
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        await self.h.close_console()
    async def request(self,operation=None,*,kind='binding',target='alpha'):
        path='/api/graph' if operation is None else '/api/'+kind+'s/'+target+'/'+operation
        reader,writer=await asyncio.open_unix_connection(self.h.console.socket_path)
        head=('GET' if operation is None else 'POST')+' '+path+' HTTP/1.1\r\nHost: hostd.local\r\nAuthorization: Bearer '+self.token+'\r\n'
        if operation:head+='Origin: http://hostd.local\r\nX-Hostd-Request: 1\r\nIdempotency-Key: '+secrets.token_hex(32)+'\r\nContent-Type: application/json\r\nContent-Length: 2\r\n'
        writer.write((head+'\r\n').encode()+(b'{}' if operation else b''));await writer.drain()
        raw=await reader.read();writer.close();await writer.wait_closed()
        status=int(raw.split(b' ',2)[1]);payload=json.loads(raw.split(b'\r\n\r\n',1)[1]);return status,payload
    def persisted(self):
        with Store(self.h.store_path) as db:return next(row['status'] for row in db.bindings() if row['binding_id']=='alpha')
    async def wait_for(self,predicate):
        for _ in range(300):
            if predicate():return
            await asyncio.sleep(.005)
        self.fail('bounded wait did not observe expected result')
    async def start_worker(self):
        task=asyncio.create_task(self.h.worker('alpha'));self.tasks.append(task);return task
    async def test_console_graph_is_actual_runtime_metadata(self):
        status,payload=await self.request();self.assertEqual(status,200)
        self.assertEqual(next(n['status'] for n in payload['nodes'] if n['id']=='binding:alpha'),'pending')
        self.assertEqual(self.h.console.socket_path.stat().st_mode & 0o777,0o600)
    async def test_pause_waits_current_thread_and_preserves_dirty_events(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def run(dirty,**kwargs):calls.append(set(dirty));entered.set();release.wait(2);return {'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        with mock.patch.object(fixture.hd,'DEBOUNCE',.001):
            await self.start_worker();self.h.mark('alpha','buzz');await self.wait_for(entered.is_set)
            code,op=await self.request('pause');self.assertEqual(code,202)
            await asyncio.sleep(.02);self.assertNotEqual(self.persisted(),'paused')
            self.h.mark('alpha','feishu',catch_up=True);release.set()
            result=await self.h.console.wait_operation(op['id']);self.assertEqual(result['status'],'completed')
            self.assertEqual(self.persisted(),'paused');await asyncio.sleep(.03)
            self.assertEqual(len(calls),1);self.assertIn('feishu',self.h.dirty['alpha'])
    async def test_resume_and_backfill_wait_observed_round(self):
        _,paused=await self.request('pause');await self.h.console.wait_operation(paused['id'])
        entered=threading.Event();release=threading.Event()
        def run(dirty,**kw):self.assertEqual(dirty,{'members','buzz','feishu'});entered.set();release.wait(2);return {'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        _,op=await self.request('resume');await self.wait_for(entered.is_set)
        self.assertNotEqual(self.persisted(),'active');release.set()
        self.assertEqual((await self.h.console.wait_operation(op['id']))['status'],'completed')
        self.assertEqual(self.persisted(),'active')
        _,op=await self.request('backfill');self.assertEqual((await self.h.console.wait_operation(op['id']))['status'],'completed')
    async def test_round_error_is_failed_operation_not_fake_success(self):
        self.h.workers['alpha'].run=lambda *a,**kw:{'errors':1,'hostd':{'verdict':'off'}}
        _,op=await self.request('backfill');result=await self.h.console.wait_operation(op['id'])
        self.assertEqual(result['status'],'unknown');self.assertEqual(self.persisted(),'degraded')
    async def test_structured_retry_runs_without_new_event(self):
        count=0
        def run(*a,**kw):
            nonlocal count
            count+=1
            return {'hostd':{'verdict':'off','retry_phases':['feishu'] if count==1 else [],'next_retry_at':time.time()+.01}}
        self.h.workers['alpha'].run=run
        with mock.patch.object(fixture.hd,'DEBOUNCE',.001):
            await self.start_worker();self.h.mark('alpha','feishu');await self.wait_for(lambda:count==2)
            await asyncio.sleep(.04);self.assertEqual(count,2)
    async def test_reconnect_invalidates_setup_and_full_catchup(self):
        self.h.workers['alpha'].cached={'roles':'stale'}
        await self.h.on_relay('alpha',{'type':'_reconnected'})
        self.assertFalse(self.h.workers['alpha'].cached)
        self.assertEqual(self.h.dirty['alpha'],{'members','buzz','feishu','notice'});self.assertIsNone(self.h.threads['alpha'])
    async def test_cancellation_retains_lock_until_executor_finishes(self):
        entered=threading.Event();release=threading.Event()
        def run(*a,**kw):entered.set();release.wait(2);return {'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        with mock.patch.object(fixture.hd,'DEBOUNCE',.001):
            task=await self.start_worker();self.h.mark('alpha','buzz');await self.wait_for(entered.is_set)
            task.cancel();pause=asyncio.create_task(self.h.console_action('binding','alpha','pause'))
            await asyncio.sleep(.02);self.assertFalse(pause.done());release.set()
            await asyncio.gather(task,return_exceptions=True);self.assertTrue((await pause).ok)
    async def test_pending_targets_alone_schedule_bounded_retry(self):
        count=0
        def run(*a,**kw):
            nonlocal count
            count+=1
            return {'hostd':{'verdict':'off','pending_targets':1 if count==1 else 0,'next_retry_at':None}}
        self.h.workers['alpha'].run=run
        with mock.patch.object(fixture.hd,'DEBOUNCE',.001),mock.patch.object(fixture.hd,'TASK_RETRY',.01):
            await self.start_worker();self.h.mark('alpha','feishu');await self.wait_for(lambda:count==2)
    async def test_actual_replay_provider_uses_store_cursor_once(self):
        with Store(self.h.store_path) as db:
            with db.transaction():db.conn.execute("INSERT INTO cursor VALUES(?,?,?,?,?)",('alpha','','relay',2000,2000))
        self.assertEqual(self.h.replay_since('alpha'),1100)
    async def test_agent_restart_requires_awaited_verified_driver(self):
        pk='a'*64
        with Store(self.h.store_path) as db:
            db.register_agent(pk,owner_pubkey='b'*64,app_id='cli_alpha',config_path='/private/agent',now=100)
            db.record_agent_chat(pk,'oc_alpha','c'*64,binding_id='alpha',now=100)
        code,op=await self.request('restart',kind='agent',target=pk)
        self.assertEqual(code,202);self.assertEqual((await self.h.console.wait_operation(op['id']))['status'],'unknown')
        calls=[]
        async def driver(identity):
            calls.append(identity);return fixture.hd.ActionResult(True,'restarted')
        self.h.restart_agent=driver
        code,_=await self.request('restart',kind='agent',target=pk)
        self.assertEqual(code,409);self.assertEqual(calls,[])
    async def test_paused_backfill_and_unknown_binding_fail(self):
        _,op=await self.request('pause');await self.h.console.wait_operation(op['id'])
        _,op=await self.request('backfill');self.assertEqual((await self.h.console.wait_operation(op['id']))['status'],'unknown')
        self.assertFalse((await self.h.console_action('binding','unknown','resume')).ok)
    async def test_main_shutdown_closes_console_and_retry_tasks(self):
        server=self.h.console;socket=server.socket_path
        blocked=asyncio.Event()
        async def source(*a,**kw):await blocked.wait()
        with mock.patch.object(self.h,'feishu_child',source),mock.patch.object(fixture.hd.relay_feed,'follow',source),mock.patch.object(fixture.hd,'DEBOUNCE',.001):
            task=asyncio.create_task(self.h.main())
            await self.wait_for(lambda:self.h.status['bindings']['alpha']['runs']>0)
            self.h.schedule_retry('alpha',{'feishu'},time.time()+60)
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertFalse(socket.exists());self.assertIsNone(self.h.console_store);self.assertFalse(self.h.retry_tasks)
    async def test_metadata_writes_notify_console(self):
        with mock.patch.object(self.h.console,'notify') as notify:
            self.h.binding_status('alpha','active')
            self.h.set_status('alpha','relay','connected')
            self.h.retain_target('alpha','cli_alpha',{'type':'im.message.receive_v1','message_id':'om_a'})
            self.assertGreaterEqual(notify.call_count,3)
    async def test_unverified_round_report_never_completes_operation(self):
        self.h.workers['alpha'].run=lambda *a,**kw:{}
        _,op=await self.request('backfill')
        self.assertEqual((await self.h.console.wait_operation(op['id']))['status'],'unknown')
        self.assertEqual(self.persisted(),'degraded')
    async def test_restart_gates_existing_durable_pause_before_consuming(self):
        self.h._set_binding_state('alpha','paused')
        with mock.patch.object(fixture.hd.bw,'Worker',fixture.FakeWorker):
            restarted=fixture.hd.Hostd(self.h.reg,self.h.status_file,state_db=self.h.store_path)
        with mock.patch.object(fixture.hd,'DEBOUNCE',.001):
            task=asyncio.create_task(restarted.worker('alpha'))
            try:
                restarted.mark('alpha','buzz');await asyncio.sleep(.03)
                self.assertFalse(restarted.workers['alpha'].calls)
                self.assertEqual(restarted.dirty['alpha'],{'buzz'})
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True);await restarted.close_console()
    async def test_shutdown_cancels_retries_created_by_inflight_operation(self):
        entered=threading.Event();release=threading.Event()
        def run(*a,**kw):entered.set();release.wait(2);return {'hostd':{'verdict':'off','retry_phases':['feishu'],'next_retry_at':time.time()+60}}
        self.h.workers['alpha'].run=run
        await self.request('backfill');await self.wait_for(entered.is_set)
        close=asyncio.create_task(self.h.close_console());await asyncio.sleep(.02)
        self.assertFalse(close.done());release.set();await close
        self.assertFalse(self.h.retry_tasks)
    async def test_store_connection_closed_with_console(self):
        db=self.h.console_store;await self.h.close_console()
        with self.assertRaises(Exception):db.bindings()
