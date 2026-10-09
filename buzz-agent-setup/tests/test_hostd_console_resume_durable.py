"""One original Console resume driver, never a replay from active status."""
import asyncio
import unittest
import threading
import secrets
from unittest import mock
import test_hostd_console_wiring as fixture
from hostd.round_slots import RoundSlots
from hostd.store import Store

class ResumeDurable(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ConsoleWiring.asyncSetUp
    asyncTearDown=fixture.ConsoleWiring.asyncTearDown
    request=fixture.ConsoleWiring.request
    persisted=fixture.ConsoleWiring.persisted
    wait_for=fixture.ConsoleWiring.wait_for
    start_worker=fixture.ConsoleWiring.start_worker

    async def pause(self):
        _,op=await self.request('pause');result=await self.h.console.wait_operation(op['id'])
        self.assertEqual(result['status'],'completed');self.assertEqual(self.persisted(),'paused')

    async def test_resume_deadline_does_not_cancel_its_only_queued_driver(self):
        await self.pause();calls=[]
        self.h.workers['alpha'].run=lambda *a,**kw:calls.append(True) or {'hostd':{'verdict':'off'}}
        self.h._round_slots=RoundSlots(1);self.h.console._coordinator.timeout=.02
        async with self.h._round_slots.slot('unrelated'):
            _,op=await self.request('resume')
            await self.wait_for(lambda:any(t.name=='alpha' for t in self.h._round_slots.waiting))
            await asyncio.sleep(.08)
            row=self.h.console_store.console_operation(op['id'],self.h.console._principal())
            self.assertEqual(row.status,'dispatched','deadline must not abandon a durable resume intent')
            self.assertEqual(calls,[])
        result=await self.h.console.wait_operation(op['id'])
        self.assertEqual(result['status'],'completed');self.assertEqual(self.persisted(),'active')
        self.assertEqual(calls,[True])

    async def test_observer_cancel_keeps_same_driver_and_blocks_second_binding_operation(self):
        await self.pause();calls=[]
        self.h.workers['alpha'].run=lambda *a,**kw:calls.append(True) or {'hostd':{'verdict':'off'}}
        self.h._round_slots=RoundSlots(1)
        async with self.h._round_slots.slot('unrelated'):
            _,op=await self.request('resume')
            await self.wait_for(lambda:any(t.name=='alpha' for t in self.h._round_slots.waiting))
            coordinator=self.h.console._coordinator;task=coordinator._tasks[op['id']]
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            row=self.h.console_store.console_operation(op['id'],self.h.console._principal())
            self.assertEqual(row.status,'unknown');self.assertIsNone(row.receipt_hash)
            self.assertIn(op['id'],coordinator._drivers)
            code,_=await self.request('pause');self.assertNotEqual(code,202)
            row=await coordinator.reconcile_operation(op['id'],self.h.console._principal())
            self.assertEqual(row.status,'unknown');self.assertEqual(calls,[])
        await self.wait_for(lambda:self.h.console_store.console_operation(op['id'],self.h.console._principal()).status=='completed')
        row=await coordinator.reconcile_operation(op['id'],self.h.console._principal())
        self.assertEqual(row.status,'completed');self.assertIsNotNone(row.receipt_hash)
        self.assertEqual(calls,[True]);self.assertEqual(self.persisted(),'active')

    async def test_observer_cancel_before_binding_lock_keeps_original_release_authority(self):
        await self.pause();calls=[]
        self.h.workers['alpha'].run=lambda *a,**kw:calls.append(True) or {'hostd':{'verdict':'off'}}
        await self.h.binding_locks['alpha'].acquire()
        _,op=await self.request('resume');coordinator=self.h.console._coordinator
        await self.wait_for(lambda:op['id'] in coordinator._drivers)
        task=coordinator._tasks[op['id']];task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(self.persisted(),'paused')
        self.h.binding_locks['alpha'].release()
        await self.wait_for(lambda:self.h.console_store.console_operation(op['id'],self.h.console._principal()).status=='completed')
        self.assertEqual(self.persisted(),'active');self.assertEqual(calls,[True])

    async def test_cancel_observer_of_submitted_thread_still_commits_its_actual_result(self):
        await self.pause();entered=threading.Event();release=threading.Event();calls=[]
        def run(*a,**kw):calls.append(True);entered.set();release.wait(2);return {'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        _,op=await self.request('resume');await self.wait_for(entered.is_set)
        try:
            task=self.h.console._coordinator._tasks[op['id']];task.cancel();await asyncio.gather(task,return_exceptions=True)
            self.assertTrue(self.h.binding_locks['alpha'].locked())
            self.assertEqual(self.h.console_store.console_operation(op['id'],self.h.console._principal()).status,'unknown')
        finally:release.set()
        await self.wait_for(lambda:self.h.console_store.console_operation(op['id'],self.h.console._principal()).status=='completed')
        self.assertEqual(calls,[True]);self.assertEqual(self.persisted(),'active')

    async def test_shutdown_of_unsubmitted_resume_preserves_wake_without_replaying_old_unknown(self):
        await self.pause();calls=[]
        self.h.workers['alpha'].run=lambda *a,**kw:calls.append(True) or {'hostd':{'verdict':'off'}}
        self.h._round_slots=RoundSlots(1);self.h.notice_hints['alpha']={'f'*64}
        async with self.h._round_slots.slot('unrelated'):
            _,op=await self.request('resume');principal=self.h.console._principal()
            await self.wait_for(lambda:any(t.name=='alpha' for t in self.h._round_slots.waiting))
            await self.h.close_console()
            self.assertTrue(self.h.wake['alpha'].is_set())
            self.assertTrue({'members','buzz','feishu'} <= self.h.dirty['alpha'])
            self.assertIsNone(self.h.threads['alpha']);self.assertIn('f'*64,self.h.notice_hints['alpha'])
            self.assertEqual(calls,[])
        with Store(self.h.store_path) as db:
            self.assertEqual(db.console_operation(op['id'],principal).status,'unknown')
            db.conn.execute("UPDATE binding SET status='active' WHERE binding_id='alpha'")
        with mock.patch.object(self.h,'console_action',wraps=self.h.console_action) as dispatch:
            await self.h.start_console()
            row=await self.h.console._coordinator.reconcile_operation(op['id'],principal)
            self.assertEqual(row.status,'unknown');self.assertIsNone(row.receipt_hash)
            self.assertEqual(dispatch.call_count,0)

    async def test_shutdown_joins_submitted_thread_before_binding_lock_release(self):
        await self.pause();entered=threading.Event();release=threading.Event()
        def run(*a,**kw):entered.set();release.wait(2);return {'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        _,op=await self.request('resume');await self.wait_for(entered.is_set)
        closing=asyncio.create_task(self.h.close_console())
        try:
            await asyncio.sleep(.04);self.assertFalse(closing.done())
            self.assertTrue(self.h.binding_locks['alpha'].locked())
        finally:release.set()
        await closing;self.assertFalse(self.h.binding_locks['alpha'].locked())

    async def test_scope_change_while_queued_prevents_dispatch_and_receipt(self):
        await self.pause();calls=[]
        self.h.workers['alpha'].run=lambda *a,**kw:calls.append(True) or {'hostd':{'verdict':'off'}}
        self.h._round_slots=RoundSlots(1)
        async with self.h._round_slots.slot('unrelated'):
            _,op=await self.request('resume')
            await self.wait_for(lambda:any(t.name=='alpha' for t in self.h._round_slots.waiting))
            with Store(self.h.store_path) as db:db.conn.execute("UPDATE binding SET channel_id='changed' WHERE binding_id='alpha'")
        result=await self.h.console.wait_operation(op['id'])
        self.assertEqual(result['status'],'unknown');self.assertEqual(calls,[])
        self.assertNotEqual(self.persisted(),'active')

    async def test_retired_during_actual_round_cannot_be_overwritten_or_mint_receipt(self):
        await self.pause();entered=threading.Event();release=threading.Event()
        def run(*a,**kw):entered.set();release.wait(2);return {'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        _,op=await self.request('resume');await self.wait_for(entered.is_set)
        with Store(self.h.store_path) as db:db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id='alpha'")
        release.set();result=await self.h.console.wait_operation(op['id'])
        self.assertEqual(result['status'],'unknown');self.assertEqual(self.persisted(),'retired')

    async def test_detached_driver_exception_is_consumed_and_cannot_mint_receipt(self):
        await self.pause();entered=threading.Event();release=threading.Event();errors=[]
        def run(*a,**kw):entered.set();release.wait(2);raise ValueError('private exception must not be emitted')
        self.h.workers['alpha'].run=run
        loop=asyncio.get_running_loop();previous=loop.get_exception_handler();loop.set_exception_handler(lambda *args:errors.append(True))
        try:
            _,op=await self.request('resume');await self.wait_for(entered.is_set)
            coordinator=self.h.console._coordinator;task=coordinator._tasks[op['id']]
            task.cancel();await asyncio.gather(task,return_exceptions=True);release.set()
            await self.wait_for(lambda:op['id'] not in coordinator._drivers)
            row=await coordinator.reconcile_operation(op['id'],self.h.console._principal())
            self.assertEqual(row.status,'unknown');self.assertIsNone(row.receipt_hash);self.assertEqual(errors,[])
        finally:release.set();loop.set_exception_handler(previous)

    async def test_current_principal_change_while_queued_cannot_dispatch_or_complete(self):
        await self.pause();calls=[];server=self.h.console;principal=server._principal();token=server._token
        self.h.workers['alpha'].run=lambda *a,**kw:calls.append(True) or {'hostd':{'verdict':'off'}}
        self.h._round_slots=RoundSlots(1)
        try:
            async with self.h._round_slots.slot('unrelated'):
                _,op=await self.request('resume')
                await self.wait_for(lambda:any(t.name=='alpha' for t in self.h._round_slots.waiting))
                server._token=secrets.token_urlsafe(32)
            row=await server._coordinator.wait_operation(op['id'],principal)
            self.assertEqual(row.status,'unknown');self.assertIsNone(row.receipt_hash);self.assertEqual(calls,[])
        finally:server._token=token

    async def test_resume_receipt_failure_cannot_leave_unassociated_active_success(self):
        await self.pause()
        self.h.workers['alpha'].run=lambda *a,**kw:{'hostd':{'verdict':'off'}}
        with mock.patch.object(Store,'finish_console_operation',side_effect=ValueError('fixed receipt failure')):
            _,op=await self.request('resume');result=await self.h.console.wait_operation(op['id'])
        self.assertEqual(result['status'],'unknown');self.assertNotEqual(self.persisted(),'active')
        self.assertIsNone(self.h.console_store.console_operation(op['id'],self.h.console._principal()).receipt_hash)

if __name__=='__main__':unittest.main()
