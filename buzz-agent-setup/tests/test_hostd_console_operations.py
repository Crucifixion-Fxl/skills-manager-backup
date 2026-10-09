"""Durable console: actual SQL reopen and low-level process-only fixtures."""
import asyncio
import unittest

import test_hostd_agent_operations as domain
from hostd.console import ActionResult
try:
    from hostd import console_operations as operations
except ImportError:
    operations=None

PRINCIPAL='4'*64
KEY='5'*64
EPOCH='6'*64
NEXT_EPOCH='7'*64

class ConsoleOperations(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        domain.RestartTests.setUp(self)
        self.coordinators=[];self.calls=[]
    async def asyncTearDown(self):
        for coordinator in self.coordinators:await coordinator.close()
    def reopen_sql(self):domain.RestartTests.reopen_sql(self)
    def coordinator(self,action=None,authorize=None,readback=None,*,epoch=EPOCH,timeout=1):
        self.assertIsNotNone(operations,'missing durable console coordinator is the genuine RED')
        async def observe_only(kind,target,action):
            self.calls.append((kind,target,action,operations.current_intent()))
            return ActionResult(False,'waiting_verify')
        coordinator=operations.ConsoleCoordinator(self.db,action or observe_only,authorize or (lambda intent:True),
                     readback,execution_epoch=epoch,timeout=timeout,clock=lambda:100)
        self.coordinators.append(coordinator);return coordinator
    def enqueue(self,coordinator,*,key=KEY,principal=PRINCIPAL,kind='binding',target='binding0',action='pause'):
        return coordinator.enqueue(principal,key,kind,target,action)

    async def test_lost_response_duplicate_key_has_one_actual_dispatch(self):
        coordinator=self.coordinator()
        first=self.enqueue(coordinator);duplicate=self.enqueue(coordinator)
        self.assertTrue(first.created);self.assertFalse(duplicate.created)
        self.assertEqual(first.record.id,duplicate.record.id)
        self.assertEqual(self.db.console_operation(first.record.id,PRINCIPAL).status,'queued')
        result=await coordinator.wait_operation(first.record.id,PRINCIPAL)
        self.assertEqual(result.status,'unknown');self.assertEqual(len(self.calls),1)
        self.assertFalse(self.enqueue(coordinator).created)
        await asyncio.sleep(0);self.assertEqual(len(self.calls),1)

    async def test_queued_close_reopen_hold_has_zero_automatic_action(self):
        self.coordinator()
        reserved=self.db.reserve_console_operation(PRINCIPAL,KEY,'binding','binding0','pause',execution_epoch=EPOCH,now=100)
        self.reopen_sql();coordinator=self.coordinator(epoch=NEXT_EPOCH)
        await coordinator.restore(PRINCIPAL)
        record=self.db.console_operation(reserved.record.id,PRINCIPAL)
        self.assertEqual(record.status,'unknown');self.assertEqual(record.reason,'recovered')
        self.assertFalse(self.enqueue(coordinator).created);self.assertEqual(self.calls,[])

    async def test_dispatched_close_reopen_hold_has_zero_automatic_action(self):
        self.coordinator()
        reserved=self.db.reserve_console_operation(PRINCIPAL,KEY,'binding','binding0','pause',execution_epoch=EPOCH,now=100)
        self.assertTrue(self.db.claim_console_operation(reserved.record.id,PRINCIPAL,EPOCH,now=100))
        self.reopen_sql();coordinator=self.coordinator(epoch=NEXT_EPOCH)
        await coordinator.restore(PRINCIPAL)
        self.assertEqual(self.db.console_operation(reserved.record.id,PRINCIPAL).status,'unknown')
        self.assertEqual(self.calls,[])

    async def test_same_epoch_preexisting_queued_record_is_not_new_execution_capability(self):
        self.coordinator()
        prior=self.db.reserve_console_operation(PRINCIPAL,KEY,'binding','binding0','pause',execution_epoch=EPOCH,now=100)
        coordinator=self.coordinator();duplicate=self.enqueue(coordinator)
        self.assertFalse(duplicate.created);self.assertEqual(duplicate.record.id,prior.record.id)
        await asyncio.sleep(0);self.assertEqual(self.calls,[])
        await coordinator.restore(PRINCIPAL);self.assertEqual(self.calls,[])

    async def test_revoked_sql_scope_is_rejected_before_dispatch(self):
        def authorize(intent):
            row=self.db.conn.execute('SELECT status FROM binding WHERE binding_id=?',(intent.target,)).fetchone()
            return row is not None and row['status']!='retired'
        coordinator=self.coordinator(authorize=authorize);reserved=self.enqueue(coordinator)
        self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id='binding0'")
        record=await coordinator.wait_operation(reserved.record.id,PRINCIPAL)
        self.assertEqual(record.status,'rejected');self.assertEqual(record.reason,'scope');self.assertEqual(self.calls,[])
        self.assertFalse(self.enqueue(coordinator).created)

    async def test_authorizer_exception_is_safe_predispatch_rejection(self):
        def unavailable(intent):raise RuntimeError('PRIVATE BODY TOKEN')
        coordinator=self.coordinator(authorize=unavailable);reserved=self.enqueue(coordinator)
        record=await coordinator.wait_operation(reserved.record.id,PRINCIPAL)
        self.assertEqual(record.status,'rejected');self.assertEqual(record.reason,'scope');self.assertEqual(self.calls,[])
        self.assertNotIn('PRIVATE',str(record))

    async def test_action_cancellation_persists_unknown_and_same_key_never_retries(self):
        entered=asyncio.Event()
        async def blocked(kind,target,action):
            self.calls.append(operations.current_intent());entered.set();await asyncio.Event().wait()
        coordinator=self.coordinator(action=blocked);reserved=self.enqueue(coordinator)
        await entered.wait();await coordinator.close()
        record=self.db.console_operation(reserved.record.id,PRINCIPAL)
        self.assertEqual(record.status,'unknown');self.assertEqual(record.reason,'cancellation')
        self.assertFalse(self.enqueue(coordinator).created);self.assertEqual(len(self.calls),1)
        self.reopen_sql();recovered=self.coordinator(epoch=NEXT_EPOCH)
        await recovered.restore(PRINCIPAL);self.assertEqual(len(self.calls),1)

    async def test_action_timeout_persists_unknown_instead_of_retryable_failure(self):
        async def blocked(kind,target,action):
            self.calls.append(operations.current_intent());await asyncio.Event().wait()
        coordinator=self.coordinator(action=blocked,timeout=.02);reserved=self.enqueue(coordinator)
        record=await coordinator.wait_operation(reserved.record.id,PRINCIPAL)
        self.assertEqual(record.status,'unknown');self.assertEqual(record.reason,'timeout')
        self.assertFalse(self.enqueue(coordinator).created);self.assertEqual(len(self.calls),1)

    async def test_fixed_three_argument_action_has_exact_typed_context_and_no_leak(self):
        coordinator=self.coordinator();reserved=self.enqueue(coordinator)
        await coordinator.wait_operation(reserved.record.id,PRINCIPAL)
        kind,target,action,intent=self.calls[0]
        self.assertEqual((kind,target,action),('binding','binding0','pause'))
        self.assertIsInstance(intent,operations.ConsoleIntent)
        self.assertEqual((intent.operation_id,intent.principal,intent.execution_epoch),(reserved.record.id,PRINCIPAL,EPOCH))
        self.assertEqual((intent.kind,intent.target,intent.action),(kind,target,action))
        self.assertIsNone(operations.current_intent())

    async def test_bare_actual_driver_ack_without_console_link_is_still_unknown(self):
        driver=domain.RestartTests.driver(self)
        async def action(kind,target,name):
            self.assertEqual((kind,target,name),('agent',domain.AGENT,'restart'))
            return await driver(target)
        coordinator=self.coordinator(action=action)
        reserved=self.enqueue(coordinator,kind='agent',target=domain.AGENT,action='restart')
        record=await coordinator.wait_operation(reserved.record.id,PRINCIPAL)
        self.assertEqual(self.ops.restarts,1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM restart_operation WHERE state='acked'").fetchone()[0],1)
        self.assertEqual(record.status,'unknown');self.assertIsNone(record.restart_operation_id)
        self.assertFalse(self.enqueue(coordinator,kind='agent',target=domain.AGENT,action='restart').created)
        self.assertEqual(self.ops.restarts,1)

    async def test_unassociated_receipt_cannot_complete_bare_success(self):
        async def action(kind,target,name):return ActionResult(True,'paused')
        async def wrong_receipt(intent):return operations.ConsoleReceipt('0'*64,'paused','a'*64)
        coordinator=self.coordinator(action=action,readback=wrong_receipt);reserved=self.enqueue(coordinator)
        record=await coordinator.wait_operation(reserved.record.id,PRINCIPAL)
        self.assertEqual(record.status,'unknown');self.assertIsNone(record.receipt_hash)

    async def test_scope_revoked_during_receipt_await_cannot_complete(self):
        entered=asyncio.Event();release=asyncio.Event()
        def authorize(intent):
            return self.db.conn.execute("SELECT status FROM binding WHERE binding_id='binding0'").fetchone()[0]!='retired'
        async def action(kind,target,name):return ActionResult(True,'paused')
        async def readback(intent):
            entered.set();await release.wait()
            return operations.ConsoleReceipt(intent.operation_id,'paused','a'*64)
        coordinator=self.coordinator(action=action,authorize=authorize,readback=readback);reserved=self.enqueue(coordinator)
        await entered.wait();self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id='binding0'")
        release.set();record=await coordinator.wait_operation(reserved.record.id,PRINCIPAL)
        self.assertEqual(record.status,'unknown');self.assertIsNone(record.receipt_hash)

    async def test_different_principal_and_key_tuple_cannot_read_or_rebind_prior_intent(self):
        coordinator=self.coordinator();reserved=self.enqueue(coordinator)
        self.assertIsNone(self.db.console_operation(reserved.record.id,'8'*64))
        with self.assertRaises(Exception):self.enqueue(coordinator,principal='8'*64)
        with self.assertRaises(Exception):self.enqueue(coordinator,action='resume')
        record=await coordinator.wait_operation(reserved.record.id,PRINCIPAL)
        self.assertEqual(record.action,'pause');self.assertEqual(len(self.calls),1)
        self.assertIsNotNone(self.db.console_operation(reserved.record.id,PRINCIPAL))

    def newer_terminal_rows(self,count):
        for index in range(count):
            row=self.db.reserve_console_operation(PRINCIPAL,f'{1000+index:064x}','binding','binding1','pause',
                                                  execution_epoch=EPOCH,now=200+index).record
            self.db.reject_console_operation(row.id,PRINCIPAL,EPOCH,reason='missing',now=200+index)

    async def test_old_linked_actual_restart_ack_recovers_beyond_128_terminal_rows(self):
        driver=domain.RestartTests.driver(self);acked=asyncio.Event()
        async def action(kind,target,name):
            result=await driver.restart_for_console(target,operations.current_intent())
            self.assertTrue(result.ok)
            acked.set()
            await asyncio.Event().wait()  # Crash/cancel before the console receipt is committed.
        live=self.coordinator(action=action)
        first=self.enqueue(live,kind='agent',target=domain.AGENT,action='restart').record
        await asyncio.wait_for(acked.wait(),2)
        await live.close()
        self.assertEqual(self.db.console_operation(first.id,PRINCIPAL).status,'unknown')
        self.newer_terminal_rows(129)
        self.assertNotIn(first.id,{row.id for row in self.db.console_operations(PRINCIPAL,limit=128)})
        self.reopen_sql();recovered=self.coordinator(epoch=NEXT_EPOCH)
        await recovered.restore(PRINCIPAL)
        self.assertEqual(self.db.console_operation(first.id,PRINCIPAL).status,'completed')
        self.assertEqual(self.ops.restarts,1)
        self.assertEqual(self.calls,[])

    async def test_recovery_keyset_does_not_skip_when_settlement_removes_first_page(self):
        # More pending targets than a single bounded page. Every receipt is an
        # associated fixture pause commit; no effect or driver is called by restore.
        for index in range(130):
            name=f'extra{index}';chat=f'oc_extra{index}'
            self.db.reconcile_bindings([domain.BindingRecord(name,f'channel{index}',chat,'cli_reader',
                                                           f'/fixture/{name}','/reader','/data')],now=10)
            row=self.db.reserve_console_operation(PRINCIPAL,f'{2000+index:064x}','binding',name,'pause',
                                                   execution_epoch=EPOCH,now=100).record
            self.db.claim_console_operation(row.id,PRINCIPAL,EPOCH,now=100)
            self.db.conn.execute("UPDATE binding SET status='paused' WHERE binding_id=?",(name,))
        observed=[]
        async def readback(intent):
            status=self.db.conn.execute('SELECT status FROM binding WHERE binding_id=?',(intent.target,)).fetchone()[0]
            self.assertEqual(status,'paused')
            observed.append(intent.operation_id)
            return operations.ConsoleReceipt(intent.operation_id,'paused','a'*64)
        recovered=self.coordinator(readback=readback,epoch=NEXT_EPOCH)
        await recovered.restore(PRINCIPAL)
        self.assertEqual(len(observed),130)
        self.assertEqual(len(set(observed)),130)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM console_operation WHERE status='completed'").fetchone()[0],130)
        self.assertEqual(self.calls,[])

    async def test_exact_readback_recovery_never_dispatches_or_disturbs_live_queue(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def action(kind,target,name):
            self.calls.append((kind,target,name));entered.set();await release.wait()
        live=self.coordinator(action=action)
        first=self.enqueue(live).record
        # A GET before the scheduled action starts cannot mark it unknown and
        # prevent the original live claim from succeeding.
        current=await live.reconcile_operation(first.id,PRINCIPAL)
        self.assertEqual(current.status,'queued')
        await asyncio.wait_for(entered.wait(),1)
        current=await live.reconcile_operation(first.id,PRINCIPAL)
        self.assertEqual(current.status,'dispatched')
        release.set();await live.wait_operation(first.id,PRINCIPAL)
        self.assertIsNone(await live.reconcile_operation(first.id,'8'*64))
        self.assertEqual((await live.reconcile_operation(first.id,PRINCIPAL)).status,'unknown')
        self.assertEqual(len(self.calls),1)

if __name__=='__main__':unittest.main()
