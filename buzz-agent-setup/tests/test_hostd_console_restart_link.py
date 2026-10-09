"""Actual console SQL and restart driver; only process transport is synthetic."""
import asyncio
from dataclasses import replace
import unittest

import test_hostd_agent_operations as domain
import test_hostd_console_operations as foundation
from hostd import console_operations as operations
from hostd import store as ledger


class ConsoleRestartLink(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        foundation.ConsoleOperations.setUp(self)
        self.domain_driver=domain.RestartTests.driver(self)
        self.dispatched=[]
    async def asyncTearDown(self):await foundation.ConsoleOperations.asyncTearDown(self)
    def reopen_sql(self):domain.RestartTests.reopen_sql(self)
    def coordinator(self,action=None,*,epoch=foundation.EPOCH):
        self.assertTrue(callable(getattr(self.domain_driver,'restart_for_console',None)),
                        'missing typed console restart adapter is the genuine RED')
        async def dispatch(kind,target,action):
            self.assertEqual((kind,target,action),('agent',domain.AGENT,'restart'))
            intent=operations.current_intent();self.dispatched.append(intent)
            return await self.domain_driver.restart_for_console(target,intent)
        return foundation.ConsoleOperations.coordinator(self,action or dispatch,epoch=epoch)
    def enqueue(self,coordinator,*,key=foundation.KEY):
        return coordinator.enqueue(foundation.PRINCIPAL,key,'agent',domain.AGENT,'restart')

    async def test_same_original_transaction_links_real_ack_and_completes_once(self):
        coordinator=self.coordinator();reserved=self.enqueue(coordinator)
        record=await coordinator.wait_operation(reserved.record.id,foundation.PRINCIPAL)
        self.assertEqual(record.status,'completed');self.assertIsNotNone(record.restart_operation_id)
        self.assertIsNotNone(record.receipt_hash);self.assertEqual(self.ops.restarts,1)
        restart=self.db.restart_record(record.restart_operation_id)
        self.assertEqual(restart.state,'acked');self.assertEqual(restart.new_process.invocation,'b'*32)
        self.assertFalse(self.enqueue(coordinator).created);self.assertEqual(len(self.dispatched),1)
        self.assertIsNone(operations.current_intent())

    async def test_domain_ack_before_console_cancel_reopen_recovers_without_fresh_driver(self):
        reached=asyncio.Event()
        async def dispatch(kind,target,action):
            self.dispatched.append(operations.current_intent())
            answer=await self.domain_driver.restart_for_console(target,operations.current_intent())
            self.assertTrue(answer.ok);reached.set();await asyncio.Event().wait()
        coordinator=self.coordinator(action=dispatch);reserved=self.enqueue(coordinator)
        await asyncio.wait_for(reached.wait(),2)
        live=self.db.console_operation(reserved.record.id,foundation.PRINCIPAL)
        self.assertEqual(live.status,'dispatched')
        self.assertEqual(self.db.restart_record(live.restart_operation_id).state,'acked')
        await coordinator.close()
        self.assertEqual(self.db.console_operation(live.id,foundation.PRINCIPAL).status,'unknown')
        self.reopen_sql();fresh_calls=[]
        async def forbidden(kind,target,action):
            fresh_calls.append((kind,target,action))
            return await domain.RestartTests.driver(self).restart_for_console(target,operations.current_intent())
        recovered=self.coordinator(action=forbidden,epoch=foundation.NEXT_EPOCH)
        await recovered.restore(foundation.PRINCIPAL)
        record=self.db.console_operation(live.id,foundation.PRINCIPAL)
        self.assertEqual(record.status,'completed');self.assertEqual(fresh_calls,[])
        self.assertEqual(self.ops.restarts,1);self.assertFalse(self.enqueue(recovered).created)

    async def test_linked_unknown_close_reopen_never_replays_or_fakes_ack(self):
        self.ops.logs={domain.CHANNELS[0]}
        coordinator=self.coordinator();reserved=self.enqueue(coordinator)
        record=await coordinator.wait_operation(reserved.record.id,foundation.PRINCIPAL)
        self.assertEqual(record.status,'unknown');self.assertIsNotNone(record.restart_operation_id)
        self.assertEqual(self.db.restart_record(record.restart_operation_id).state,'unknown')
        self.reopen_sql();self.ops.logs=set(domain.CHANNELS);fresh_calls=[]
        async def forbidden(*args):fresh_calls.append(args)
        recovered=self.coordinator(action=forbidden,epoch=foundation.NEXT_EPOCH)
        await recovered.restore(foundation.PRINCIPAL)
        self.assertEqual(self.db.console_operation(record.id,foundation.PRINCIPAL).status,'unknown')
        self.assertEqual(fresh_calls,[]);self.assertEqual(self.ops.restarts,1)

    async def test_new_key_cannot_borrow_previous_unknown_domain_pin(self):
        self.ops.logs={domain.CHANNELS[0]}
        coordinator=self.coordinator();first=self.enqueue(coordinator)
        old=await coordinator.wait_operation(first.record.id,foundation.PRINCIPAL)
        self.assertEqual(old.status,'unknown');self.ops.logs=set(domain.CHANNELS)
        with self.assertRaises(ledger.StoreError):self.enqueue(coordinator,key='9'*64)
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual(self.db.restart_record(old.restart_operation_id).state,'unknown')
        self.assertEqual(self.ops.restarts,1)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM restart_operation').fetchone()[0],1)

    async def test_link_failure_reaches_actual_stage_and_rolls_back_whole_restart_reservation(self):
        coordinator=self.coordinator();calls=[]
        def failed(*args,**kwargs):
            calls.append((args,kwargs));raise ledger.StoreError(ledger.ERROR)
        self.db.link_console_restart=failed
        reserved=self.enqueue(coordinator)
        record=await coordinator.wait_operation(reserved.record.id,foundation.PRINCIPAL)
        self.assertEqual(len(calls),1,'negative must actually reach the linkage stage')
        self.assertEqual(record.status,'unknown');self.assertIsNone(record.restart_operation_id)
        self.assertEqual(self.ops.restarts,0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM restart_operation').fetchone()[0],0)

    async def test_queued_terminal_wrong_epoch_principal_and_raw_context_never_dispatch(self):
        self.coordinator()
        reserved=self.db.reserve_console_operation(foundation.PRINCIPAL,'a'*64,'agent',domain.AGENT,'restart',
                                                   execution_epoch=foundation.EPOCH,now=100)
        intent=operations.ConsoleIntent.from_record(reserved.record)
        bad=(intent,replace(intent,execution_epoch='0'*64),replace(intent,principal='0'*64),
             replace(intent,kind='binding',target='binding0',action='pause'),{'unit':'arbitrary.service'},None)
        for forged in bad:
            with self.subTest(kind=type(forged).__name__):
                self.assertFalse((await self.domain_driver.restart_for_console(domain.AGENT,forged)).ok)
        self.assertTrue(self.db.claim_console_operation(intent.operation_id,intent.principal,intent.execution_epoch,now=100))
        for forged in bad:
            with self.subTest(dispatched_kind=type(forged).__name__):
                self.assertFalse((await self.domain_driver.restart_for_console(domain.AGENT,forged)).ok)
        self.assertEqual(self.ops.restarts,0);self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM restart_operation').fetchone()[0],0)

    async def test_terminal_request_has_independent_fixture_and_never_dispatches(self):
        self.coordinator()
        terminal=self.db.reserve_console_operation(foundation.PRINCIPAL,'b'*64,'agent',domain.AGENT,'restart',
                                                   execution_epoch=foundation.EPOCH,now=100)
        done=operations.ConsoleIntent.from_record(terminal.record)
        self.assertTrue(self.db.reject_console_operation(done.operation_id,done.principal,done.execution_epoch,
                                                         reason='scope',now=100))
        self.assertFalse((await self.domain_driver.restart_for_console(domain.AGENT,done)).ok)
        self.assertEqual(self.ops.restarts,0);self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM restart_operation').fetchone()[0],0)

    async def test_actual_dispatched_context_cannot_choose_another_agent_pubkey(self):
        reached=[]
        async def dispatch(kind,target,action):
            intent=operations.current_intent()
            self.assertEqual(self.db.console_operation(intent.operation_id,intent.principal).status,'dispatched')
            reached.append(intent)
            return await self.domain_driver.restart_for_console('f'*64,intent)
        coordinator=self.coordinator(action=dispatch);reserved=self.enqueue(coordinator)
        record=await coordinator.wait_operation(reserved.record.id,foundation.PRINCIPAL)
        self.assertEqual(len(reached),1);self.assertEqual(record.status,'unknown')
        self.assertEqual(self.ops.restarts,0);self.assertIsNone(self.db.active_restart(domain.AGENT))

if __name__=='__main__':unittest.main()
