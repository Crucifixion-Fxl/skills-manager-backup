"""Actual Console, SQL, root scheduling and thread drain; offline round effects."""
import asyncio
import threading
import secrets
import sqlite3
import unittest
from unittest import mock

import test_hostd_console_wiring as fixture
from hostd import console_pause
from hostd.store import Store, StoreError, ERROR
from hostd.console_operations import ConsoleIntent
from hostd.round_slots import RoundSlots

class PauseDrain(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ConsoleWiring.asyncSetUp
    asyncTearDown = fixture.ConsoleWiring.asyncTearDown
    request = fixture.ConsoleWiring.request
    persisted = fixture.ConsoleWiring.persisted
    wait_for = fixture.ConsoleWiring.wait_for
    start_worker = fixture.ConsoleWiring.start_worker

    async def test_pause_intent_survives_action_timeout_until_actual_thread_drains(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def run(dirty, **kwargs):
            calls.append(set(dirty)); entered.set(); release.wait(2)
            return {'hostd': {'verdict': 'off'}}
        self.h.workers['alpha'].run = run
        self.h.console._coordinator.timeout = .02
        with mock.patch.object(fixture.fixture.hd, 'DEBOUNCE', .001):
            await self.start_worker(); self.h.mark('alpha', 'buzz')
            await self.wait_for(entered.is_set)
            _, op = await self.request('pause')
            try:
                await asyncio.sleep(.08)
                row = self.h.console_store.console_operation(op['id'], self.h.console._principal())
                self.assertEqual(row.status, 'dispatched', 'pause must keep its one live drain operation after the request deadline')
                self.assertTrue(self.h.binding_locks['alpha'].locked())
                self.assertEqual(self.h.console.details('binding','alpha')['status'],'stopping')
                self.assertEqual(self.h.console._operation_view(row)['phase'],'stopping')
                self.assertNotEqual(self.persisted(), 'paused', 'in-flight effects have not drained')
                self.h.mark('alpha', 'feishu', catch_up=True)
            finally:
                release.set()
            result = await self.h.console.wait_operation(op['id'])
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(self.persisted(), 'paused')
            await asyncio.sleep(.02)
            self.assertEqual(len(calls), 1)
            self.assertIn('feishu', self.h.dirty['alpha'])

    async def pending_pause(self):
        await self.h.binding_locks['alpha'].acquire()
        _,op=await self.request('pause')
        await self.wait_for(lambda: console_pause.fence(self.h.console_store,'alpha') is not None)
        return op

    async def test_client_disconnect_does_not_cancel_persisted_pause(self):
        await self.h.binding_locks['alpha'].acquire()
        self.h.console._coordinator.timeout=.02
        reader,writer=await asyncio.open_unix_connection(self.h.console.socket_path)
        request=('POST /api/bindings/alpha/pause HTTP/1.1\r\nHost: hostd.local\r\n'
            'Authorization: Bearer '+self.token+'\r\nOrigin: http://hostd.local\r\n'
            'X-Hostd-Request: 1\r\nIdempotency-Key: '+secrets.token_hex(32)+'\r\n'
            'Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{}')
        writer.write(request.encode()); await writer.drain()
        await self.wait_for(lambda: console_pause.fence(self.h.console_store,'alpha') is not None)
        writer.close(); await writer.wait_closed()
        await asyncio.sleep(.05)
        fence=console_pause.fence(self.h.console_store,'alpha')
        record=self.h.console_store.console_operation(fence['operation_id'],self.h.console._principal())
        self.assertEqual(record.status,'dispatched'); self.assertNotEqual(self.persisted(),'paused')
        self.h.binding_locks['alpha'].release()
        result=await self.h.console.wait_operation(record.id)
        self.assertEqual(result['status'],'completed'); self.assertEqual(self.persisted(),'paused')

    async def test_queued_round_cannot_dispatch_after_fence_and_preserves_work(self):
        calls=[]; self.h.workers['alpha'].run=lambda *a,**kw: calls.append(a) or {'hostd':{'verdict':'off'}}
        self.h._round_slots=RoundSlots(1)
        self.h.notice_hints['alpha']={'f'*64}
        with mock.patch.object(fixture.fixture.hd,'DEBOUNCE',.001):
            async with self.h._round_slots.slot('blocked'):
                await self.start_worker()
                self.h.mark('alpha','buzz'); self.h.mark('alpha','feishu',thread='om_pending')
                await self.wait_for(lambda: any(t.name=='alpha' for t in self.h._round_slots.waiting))
                _,op=await self.request('pause')
                await self.wait_for(lambda: console_pause.fence(self.h.console_store,'alpha') is not None)
            result=await self.h.console.wait_operation(op['id'])
            self.assertEqual(result['status'],'completed'); self.assertEqual(calls,[])
            self.assertTrue({'buzz','feishu'} <= self.h.dirty['alpha'])
            self.assertIn('om_pending',self.h.threads['alpha'])
            self.assertIn('f'*64,self.h.notice_hints['alpha'])

    async def test_cancelled_pause_keeps_fence_and_actual_thread_lock_until_readback(self):
        entered=threading.Event(); release=threading.Event(); calls=[]
        def run(*a,**kw): calls.append(True); entered.set(); release.wait(2); return {'hostd':{'verdict':'off'}}
        self.h.workers['alpha'].run=run
        with mock.patch.object(fixture.fixture.hd,'DEBOUNCE',.001):
            await self.start_worker(); self.h.mark('alpha','buzz'); await self.wait_for(entered.is_set)
            _,op=await self.request('pause')
            await self.wait_for(lambda: console_pause.fence(self.h.console_store,'alpha') is not None)
            task=self.h.console._coordinator._tasks[op['id']]; task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            self.assertEqual(self.h.console_store.console_operation(op['id'],self.h.console._principal()).status,'unknown')
            self.assertTrue(self.h.binding_locks['alpha'].locked())
            self.h.mark('alpha','feishu',thread='om_after_pause')
            release.set(); await self.wait_for(lambda:not self.h.binding_locks['alpha'].locked())
            record=await self.h.console._coordinator.reconcile_operation(op['id'],self.h.console._principal())
            self.assertEqual(record.status,'completed'); self.assertEqual(self.persisted(),'paused')
            self.assertEqual(calls,[True]); self.assertIn('feishu',self.h.dirty['alpha'])

    async def test_old_epoch_readback_drains_without_action_replay_then_resume(self):
        op=await self.pending_pause(); principal=self.h.console._principal()
        old_epoch=self.h.console._coordinator.execution_epoch
        await self.h.close_console(); self.h.binding_locks['alpha'].release()
        with Store(self.h.store_path) as db:
            self.assertEqual(db.console_operation(op['id'],principal).status,'unknown')
            self.assertEqual(console_pause.fence(db,'alpha')['state'],'stopping')
        self.assertEqual(self.h._binding_state('alpha'),'paused')
        with mock.patch.object(self.h,'console_action',wraps=self.h.console_action) as dispatch:
            await self.h.start_console()
            self.assertNotEqual(self.h.console._coordinator.execution_epoch,old_epoch)
            self.assertEqual(dispatch.call_count,0)
            record=self.h.console_store.console_operation(op['id'],principal)
            self.assertEqual(record.status,'completed'); self.assertEqual(self.persisted(),'paused')
            self.h.workers['alpha'].run=lambda *a,**kw:{'hostd':{'verdict':'off'}}
            _,resume=await self.request('resume'); result=await self.h.console.wait_operation(resume['id'])
            self.assertEqual(result['status'],'completed'); self.assertEqual(dispatch.call_count,1)
            self.assertIsNone(console_pause.fence(self.h.console_store,'alpha'))
            self.assertEqual(self.persisted(),'active')

    async def test_another_live_root_cannot_claim_old_drain(self):
        op=await self.pending_pause(); principal=self.h.console._principal()
        await self.h.console._coordinator.close(); self.h.binding_locks['alpha'].release()
        record=self.h.console_store.console_operation(op['id'],principal); intent=ConsoleIntent.from_record(record)
        original=self.h._console_pause_root
        self.h._console_pause_root=(*original[:2],secrets.token_hex(32))
        self.assertIsNone(await self.h.console_readback(intent,principal=lambda:principal))
        self.assertNotEqual(self.persisted(),'paused')
        self.assertEqual(self.h.console_store.console_operation(op['id'],principal).status,'unknown')
        # Model the recorded prior root being absent, without ever killing a service.
        with mock.patch.object(console_pause,'process_start',return_value=None):
            receipt=await self.h.console_readback(intent,principal=lambda:principal)
        self.assertIsNotNone(receipt); self.assertEqual(self.persisted(),'paused')

    async def test_target_change_or_negative_state_cannot_be_overwritten(self):
        op=await self.pending_pause(); principal=self.h.console._principal()
        with Store(self.h.store_path) as db:
            db.conn.execute("UPDATE binding SET status='conflict' WHERE binding_id='alpha'")
        self.h.binding_locks['alpha'].release()
        result=await self.h.console.wait_operation(op['id'])
        self.assertEqual(result['status'],'unknown'); self.assertEqual(self.persisted(),'conflict')
        self.assertEqual(console_pause.fence(self.h.console_store,'alpha')['state'],'stopping')
        self.assertIsNone(self.h.console_store.console_operation(op['id'],principal).receipt_hash)

    async def test_registry_scope_change_while_waiting_blocks_completion(self):
        op=await self.pending_pause()
        with Store(self.h.store_path) as db:
            db.conn.execute("UPDATE binding SET channel_id='changed' WHERE binding_id='alpha'")
        self.h.binding_locks['alpha'].release()
        result=await self.h.console.wait_operation(op['id'])
        self.assertEqual(result['status'],'unknown'); self.assertNotEqual(self.persisted(),'paused')
        self.assertEqual(console_pause.fence(self.h.console_store,'alpha')['state'],'stopping')

    async def test_principal_revocation_while_waiting_blocks_receipt(self):
        op=await self.pending_pause(); server=self.h.console; old_token=server._token; principal=server._principal()
        server._token=secrets.token_urlsafe(32); self.h.binding_locks['alpha'].release()
        try:
            await server._coordinator.wait_operation(op['id'],principal)
            row=server.store.console_operation(op['id'],principal)
            self.assertEqual(row.status,'unknown'); self.assertIsNone(row.receipt_hash)
            self.assertNotEqual(self.persisted(),'paused')
        finally: server._token=old_token

    async def test_receipt_commit_failure_rolls_back_pause_observation(self):
        with mock.patch.object(Store,'finish_console_operation',side_effect=StoreError(ERROR)):
            _,op=await self.request('pause'); result=await self.h.console.wait_operation(op['id'])
            self.assertEqual(result['status'],'unknown'); self.assertNotEqual(self.persisted(),'paused')
            self.assertEqual(console_pause.fence(self.h.console_store,'alpha')['state'],'stopping')
        record=await self.h.console._coordinator.reconcile_operation(op['id'],self.h.console._principal())
        self.assertEqual(record.status,'completed'); self.assertEqual(self.persisted(),'paused')

    async def test_resume_cannot_release_fence_after_binding_becomes_conflicted(self):
        _,op=await self.request('pause'); await self.h.console.wait_operation(op['id'])
        with Store(self.h.store_path) as db:
            db.conn.execute("UPDATE binding SET status='conflict' WHERE binding_id='alpha'")
        self.h.workers['alpha'].run=mock.Mock(side_effect=AssertionError('must not dispatch'))
        _,resume=await self.request('resume'); result=await self.h.console.wait_operation(resume['id'])
        self.assertEqual(result['status'],'unknown'); self.assertEqual(self.persisted(),'conflict')
        self.assertIsNotNone(console_pause.fence(self.h.console_store,'alpha'))
        self.h.workers['alpha'].run.assert_not_called()

    async def test_readback_rechecks_current_principal_after_waiting_for_lock(self):
        op=await self.pending_pause(); principal=self.h.console._principal()
        await self.h.console._coordinator.close()
        intent=ConsoleIntent.from_record(self.h.console_store.console_operation(op['id'],principal))
        current=[principal]
        readback=asyncio.create_task(self.h.console_readback(intent,principal=lambda:current[0]))
        await asyncio.sleep(.02)
        self.assertFalse(readback.done())
        current[0]='a'*64; self.h.binding_locks['alpha'].release()
        self.assertIsNone(await readback)
        self.assertNotEqual(self.persisted(),'paused')
        self.assertEqual(self.h.console_store.console_operation(op['id'],principal).status,'unknown')

    async def test_schema14_upgrade_preserves_existing_binding_and_operations(self):
        _,op=await self.request('backfill'); await self.h.console.wait_operation(op['id'])
        principal=self.h.console._principal(); await self.h.close_console()
        raw=sqlite3.connect(self.h.store_path)
        before=list(raw.execute('SELECT * FROM console_operation'))
        raw.execute('DROP TABLE join_adoption');raw.execute('DROP TABLE feishu_scan_token');raw.execute('DROP TABLE feishu_scan');raw.execute('DROP TABLE feishu_ingest');raw.execute('DROP TABLE outlet_work_turn');raw.execute('DROP TABLE outlet_work');raw.execute('DROP TABLE outlet_pending'); raw.execute('DROP TABLE console_pause'); raw.execute('PRAGMA user_version=14'); raw.commit(); raw.close()
        with Store(self.h.store_path) as db:
            self.assertEqual(db.conn.execute('PRAGMA user_version').fetchone()[0],19)
            self.assertEqual([tuple(r) for r in db.conn.execute('SELECT * FROM console_operation')],before)
            self.assertIsNone(console_pause.fence(db,'alpha'))
            self.assertIsNotNone(db.console_operation(op['id'],principal))

if __name__ == '__main__': unittest.main()
