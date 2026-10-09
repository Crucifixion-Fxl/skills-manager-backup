"""Author scheduling hints cannot grant effects or starve the ordinary sweep."""
import asyncio
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import test_hostd_worker_outlets as wfixture
import test_hostd_outlet_fresh_priority as wiring

base=wfixture.base
setUpModule=wfixture.setUpModule
tearDownModule=wfixture.tearDownModule

class AuthorFairness(base.TmpCase):
    LOW='1'*64;MID='5'*64;HOT='f'*64
    def setup_worker(self):
        self.worker=wfixture.bw.Worker(self.tmp/'cfg',self.tmp/'state',binding_id='test')
        self.worker.outlet_specs={pub:SimpleNamespace(pubkey=pub,owner_pubkey=base.OWNER_PK,status='own_bot_verified',
            app_id='cli_'+str(i),lark_config_dir=self.tmp/str(i),lark_data_dir=self.tmp/('data'+str(i)),env_file=self.tmp/'env')
            for i,pub in enumerate((self.LOW,self.MID,self.HOT))}
        self.run=SimpleNamespace(cfg={'agents':{},'desk_pubkey':'a'*64,'mirror_pubkey':'b'*64},other_mirrors=set())
        # Predeclare future scheduling metadata to make baseline RED behavioral.
        self.worker._outlet_urgent_authors={self.HOT:None}
        self.worker._outlet_urgent_streak=0
    def select(self):
        with mock.patch.object(wfixture.bw,'read_owned',side_effect=OSError('fixed unavailable env')):
            result=self.worker._run_outlets(self.run,base.NOW)
        return next(pub for pub,row in result.items() if row['reason']!='slice_yield')
    def test_fresh_hot_author_is_selected_before_unhinted_low_author(self):
        self.setup_worker()
        self.assertEqual(self.select(),self.HOT)
        self.assertEqual(self.worker.outlet_cursor,'')
    def test_repeated_hot_hint_does_not_move_ordinary_cursor_or_reset_fairness(self):
        self.setup_worker();order=[]
        for _ in range(12):
            self.worker._outlet_urgent_authors.setdefault(self.HOT,None)
            order.append(self.select())
        self.assertEqual(order[:4],[self.HOT]*3+[self.LOW])
        self.assertEqual(order[7],self.MID)
        self.assertEqual(self.worker.outlet_cursor,self.HOT)
    def test_duplicate_hints_preserve_fifo_and_missing_spec_is_pruned(self):
        self.setup_worker();self.worker._outlet_urgent_authors[self.MID]=None
        self.worker._outlet_urgent_authors.setdefault(self.HOT,None)
        self.assertEqual(self.select(),self.HOT);self.assertEqual(self.select(),self.MID)
        self.worker._outlet_urgent_authors[self.HOT]=None
        del self.worker.outlet_specs[self.HOT]
        self.assertEqual(self.select(),self.LOW)
        self.assertNotIn(self.HOT,self.worker._outlet_urgent_authors)
    def test_worker_owns_transferred_hints_even_if_setup_fails(self):
        self.setup_worker();self.worker._outlet_urgent_authors={}
        with mock.patch.object(wfixture.bw.hc,'load_config',side_effect=ValueError('setup failed')):
            with self.assertRaises(ValueError):self.worker.run({'buzz'},outlet_authors=(self.HOT,self.HOT,'invalid',self.MID))
        self.assertEqual(list(self.worker._outlet_urgent_authors),[self.HOT,self.MID])
        self.assertEqual(self.worker._outlet_urgent_streak,0)
    def test_new_hint_reenters_author_already_completed_in_current_sweep(self):
        self.setup_worker();self.worker.outlet_rescan=False
        self.worker.outlet_sweep_pending={self.LOW,self.MID}
        self.assertEqual(self.select(),self.HOT)
    def test_large_or_invalid_snapshot_cannot_grow_worker_queue(self):
        self.setup_worker();self.worker._outlet_urgent_authors={}
        with mock.patch.object(wfixture.bw.hc,'load_config',side_effect=ValueError('setup failed')):
            with self.assertRaises(ValueError):self.worker.run({'buzz'},outlet_authors=tuple(format(i,'064x') for i in range(257)))
        self.assertEqual(self.worker._outlet_urgent_authors,{})

    def test_metadata_trace_failure_does_not_replace_business_result(self):
        self.setup_worker();self.worker.latency_trace=SimpleNamespace(record=mock.Mock(side_effect=OSError('trace failed')))
        self.assertEqual(self.select(),self.HOT)
        self.worker.latency_trace.record.assert_called_once_with('test','outlet_selected',source=self.HOT)

class Transfer(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=wiring.OwnedFeedPriority.asyncSetUp
    asyncTearDown=wiring.OwnedFeedPriority.asyncTearDown
    emit=wiring.OwnedFeedPriority.emit
    async def test_hint_arriving_while_waiting_is_in_admitted_snapshot(self):
        slots=self.h._round_slots
        hold=[]
        for name in ('one','two','three','four'):
            slots.promote(name);ctx=slots.slot(name);await ctx.__aenter__();hold.append(ctx)
        seen=[]
        self.h.workers['alpha'].run=lambda *a,**kw:seen.append(kw.get('outlet_authors')) or {'hostd':{'verdict':'off'}}
        task=asyncio.create_task(self.h._round('alpha',{'buzz'},set()));self.tasks.append(task)
        try:
            await asyncio.sleep(.01);await self.emit()
            self.assertFalse(seen)
            await hold.pop().__aexit__(None,None,None);await task
            self.assertEqual(seen,[(wiring.fixture.old.base.AGENT_PK,)])
            self.assertFalse(self.h.outlet_author_hints.get('alpha'))
        finally:
            for ctx in hold:await ctx.__aexit__(None,None,None)
    async def test_arrival_during_thread_stays_in_loop_for_next_round_without_replay(self):
        await self.emit();entered=threading.Event();release=threading.Event();seen=[]
        def run(*a,**kw):
            seen.append(kw.get('outlet_authors'));entered.set();release.wait(2)
            raise ValueError('actual worker failed after accepting hints')
        self.h.workers['alpha'].run=run
        task=asyncio.create_task(self.h._round('alpha',{'buzz'},set()));self.tasks.append(task)
        try:
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.005)
            self.assertTrue(entered.is_set());self.assertFalse(self.h.outlet_author_hints.get('alpha'))
            await self.emit();release.set()
            with self.assertRaises(ValueError):await task
            self.assertEqual(seen,[(wiring.fixture.old.base.AGENT_PK,)])
            self.assertEqual(list(self.h.outlet_author_hints['alpha']),[wiring.fixture.old.base.AGENT_PK])
        finally:release.set()
    async def test_pause_preserves_loop_hint_without_dispatch(self):
        with wiring.Store(self.h.store_path) as db:db.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='alpha'")
        await self.emit();await self.h._round('alpha',{'buzz'},set())
        self.assertFalse(self.h.workers['alpha'].calls)
        self.assertEqual(list(self.h.outlet_author_hints['alpha']),[wiring.fixture.old.base.AGENT_PK])
    async def test_loop_hint_capacity_and_duplicate_order_are_bounded(self):
        authors=[format(i,'064x') for i in range(257)]
        self.h.workers['alpha'].outlet_specs=dict.fromkeys(authors)
        for author in authors:self.h.retain_outlet_author('alpha',author)
        self.h.retain_outlet_author('alpha',authors[0])
        self.h.retain_outlet_author('alpha','invalid')
        self.assertEqual(list(self.h.outlet_author_hints['alpha']),authors[:256])

    async def test_submit_failure_restores_hint_without_delivering_snapshot(self):
        await self.emit();executor=mock.Mock();executor.submit.side_effect=RuntimeError('submit rejected')
        self.h._round_executor=executor
        with self.assertRaises(RuntimeError):await self.h._round('alpha',{'buzz'},set())
        self.assertEqual(list(self.h.outlet_author_hints['alpha']),[wiring.fixture.old.base.AGENT_PK])
        self.assertFalse(self.h.workers['alpha'].calls)

class CurrentAuthority(base.TmpCase):
    assembly=wfixture.WorkerOutlets.assembly
    event=wfixture.WorkerOutlets.event
    def test_hint_never_bypasses_revoked_actual_own_grant(self):
        world,run,worker,spec=self.assembly();event=self.event()
        world.events.append(event);world.relay_events.append(event)
        adapter=wfixture.bw.ao.AgentOutlet(run,base.AGENT2_PK,spec.env_file,
            bot_client=run.clients.agents[spec.app_id],trusted_relays={'https://relay.test'},
            http=world.http_get,clock=lambda:base.NOW)
        adapter.verify()
        changed=run.mapping_store.conn.execute("UPDATE agent_chat SET status='retired' WHERE agent_id=?",(base.AGENT2_PK,))
        self.assertEqual(changed.rowcount,1)
        worker.run({'buzz'},outlet_authors=(base.AGENT2_PK,))
        self.assertFalse(any('--content' in a for a,_ in world.bot_calls))
        self.assertIsNone(run.mapping_store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK))
    def test_restart_loss_of_priority_keeps_durable_source_catchup(self):
        world,run,worker,spec=self.assembly()
        events=[self.event(content='source'+str(i),created=run.now_ts-i) for i in range(2)]
        world.events.extend(events);world.relay_events.extend(events)
        worker.run({'buzz'},outlet_authors=(base.AGENT2_PK,))
        self.assertEqual(len(wfixture.bw.ao.outlet_work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)),1)
        restarted=wfixture.bw.Worker(worker.config,worker.state_dir,base_env=worker.base_env,
            store_path=worker.store_path,binding_id='test',client_factory=worker.client_factory,clock=worker.clock)
        restarted.outlet_specs=worker.outlet_specs;restarted.trusted_relays=worker.trusted_relays
        restarted.run({'buzz'})
        self.assertTrue(all(run.mapping_store.delivery_by_source('test',e['id'],'b2f',agent_id=base.AGENT2_PK)['status']=='acked' for e in events))
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),2)

if __name__=='__main__':unittest.main()
