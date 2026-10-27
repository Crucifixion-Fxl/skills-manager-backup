"""An orphan feed hint waits durably without claiming an outlet is ready."""
import json
import unittest
from unittest import mock
import test_hostd_worker_outlets as fixture
base=fixture.base
setUpModule=fixture.setUpModule
tearDownModule=fixture.tearDownModule

class OrphanWait(base.TmpCase):
    assembly=fixture.WorkerOutlets.assembly
    event=fixture.WorkerOutlets.event
    put=fixture.WorkerOutlets.put
    sends=fixture.WorkerOutlets.sends

    def orphan(self,run):
        event=base.FGS.sign_event(base.AGENT2_KEY,5,[['e','a'*64]],'',run.now_ts-10)
        self.assertTrue(fixture.bw.ao.reaction_inbox.retain(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,event))
        return event

    def registration(self,w,run):
        observe=getattr(w,'registration_readiness',None)
        self.assertTrue(callable(observe))
        return observe(base.AGENT2_PK,run.mapping_store)

    def test_orphan_registration_is_ready_without_settling_business_work(self):
        world,run,w,spec=self.assembly();self.orphan(run)
        rep=w.run({'buzz'});db=run.mapping_store
        business=rep['hostd']['outlet_results'][base.AGENT2_PK]
        self.assertEqual((business['status'],business['pending']),('pending',1))
        self.assertNotIn('registration_only',business)
        before=list(db.conn.iterdump())
        proof=self.registration(w,run)
        self.assertTrue(proof['registration_only'])
        self.assertEqual(proof['reason'],'orphan_withdrawal_wait')
        self.assertEqual(proof['app_id'],spec.app_id)
        self.assertEqual(w.outlet_readiness(base.AGENT2_PK,db),{})
        self.assertEqual(list(db.conn.iterdump()),before)
        from datetime import timedelta
        w.clock=lambda:base.NOW+timedelta(seconds=10)
        w.run({'notice'})
        self.assertEqual(self.registration(w,run),proof)
        self.assertFalse(self.sends(world))

    def test_orphan_registration_invalidates_when_current_work_changes(self):
        world,run,w,spec=self.assembly();self.orphan(run);w.run({'buzz'})
        db=run.mapping_store;pub=base.AGENT2_PK;saved=w._outlet_readiness[pub]
        self.assertTrue(self.registration(w,run))
        mutations=[
            "DELETE FROM outlet_work",
            "INSERT INTO outlet_pending SELECT binding_id,agent_id,source_id,channel_id,kind,source_at,captured_floor FROM outlet_work",
            "DELETE FROM reaction_inbox",
            "INSERT INTO outlet_work SELECT binding_id,agent_id,replace(source_id,substr(source_id,1,1),'b'),channel_id,9,source_at,captured_floor,parent_id,attempts,retry_at FROM outlet_work",
        ]
        for sql in mutations:
            with self.subTest(sql=sql):
                db.conn.execute('SAVEPOINT changed_work')
                try:
                    db.conn.execute(sql)
                    self.assertEqual(self.registration(w,run),{})
                finally:
                    db.conn.execute('ROLLBACK TO changed_work');db.conn.execute('RELEASE changed_work')
                    w._outlet_readiness[pub]=saved
        for status in ('pending','unknown'):
            delivery=db.reserve_delivery('test',('b' if status=='pending' else 'c')*64,'r2f',agent_id=pub,source_at=run.now_ts,now=run.now_ts)
            if status=='unknown':db.fail_delivery(delivery.id,unknown=True,now=run.now_ts)
            self.assertEqual(self.registration(w,run),{})
            db.conn.execute('DELETE FROM delivery WHERE id=?',(delivery.id,))
            w._outlet_readiness[pub]=saved
        original=db.reserve_delivery('test','a'*64,'r2f',agent_id=pub,source_at=run.now_ts,now=run.now_ts)
        db.ack_delivery(original.id,'om_original',now=run.now_ts)
        self.assertEqual(self.registration(w,run),{})
        self.assertFalse(self.sends(world))

    def test_orphan_registration_invalidates_on_authority_or_app_change(self):
        world,run,w,spec=self.assembly();self.orphan(run);w.run({'buzz'})
        pub=base.AGENT2_PK;saved=w._outlet_readiness[pub]
        for path in (w.config,spec.env_file):
            before=path.read_bytes()
            try:
                path.write_bytes(before+b'\n')
                self.assertEqual(self.registration(w,run),{})
            finally:
                path.write_bytes(before);w._outlet_readiness[pub]=saved
        old=spec.app_id;spec.app_id='cli_changed'
        self.assertEqual(self.registration(w,run),{})
        spec.app_id=old;w._outlet_readiness[pub]=saved
        run.mapping_store.conn.execute("UPDATE agent_chat SET status='paused'")
        self.assertEqual(self.registration(w,run),{})
        self.assertFalse(self.sends(world))

    def test_later_agent_cannot_change_work_between_verification_and_cache(self):
        from types import SimpleNamespace
        world,run,w,spec=self.assembly();self.orphan(run)
        # The following candidate is not selected for dispatch, but even its
        # local validation must not make the earlier proof cover changed work.
        later='f'*64
        original=fixture.bw.ao.AgentOutlet.catch_up
        def catch_up(adapter,**kwargs):
            try:return original(adapter,**kwargs)
            finally:
                w.outlet_specs[later]=SimpleNamespace(pubkey=later,owner_pubkey=base.OWNER_PK,app_id='cli_later',status='blocked')
        class Specs(dict):
            def get(mapping,key,*args):
                if key==later:
                    run.mapping_store.conn.execute('DELETE FROM outlet_work')
                return super().get(key,*args)
        w.outlet_specs=Specs(w.outlet_specs)
        w.outlet_specs[later]=None
        with mock.patch.object(fixture.bw.ao.AgentOutlet,'catch_up',catch_up),mock.patch.object(w,'_orphan_wait_scope',wraps=w._orphan_wait_scope) as scope:
            w.run({'buzz'})
        self.assertEqual(scope.call_count,2)  # Snapshot at wait, then reject changed work at cache insertion.
        self.assertEqual(self.registration(w,run),{})
        self.assertFalse(self.sends(world))

    def test_authority_change_during_verification_cannot_borrow_new_scope(self):
        world,run,w,spec=self.assembly();self.orphan(run)
        original=fixture.bw.ao.AgentOutlet.verify
        def verify(adapter):
            result=original(adapter)
            spec.env_file.write_bytes(spec.env_file.read_bytes()+b'\n')
            return result
        with mock.patch.object(fixture.bw.ao.AgentOutlet,'verify',verify):w.run({'buzz'})
        self.assertEqual(self.registration(w,run),{})
        self.assertFalse(self.sends(world))

    def test_wait_exception_without_actual_fresh_verification_is_not_registration_proof(self):
        world,run,w,spec=self.assembly();self.orphan(run)
        for error in (fixture.bw.ao.OutletWaiting(123),fixture.bw.ao.OutletDeferred(),base.FGS.GroupSyncError('controlled')):
            with self.subTest(error=type(error).__name__),mock.patch.object(fixture.bw.ao.AgentOutlet,'catch_up',side_effect=error):
                w.run({'buzz'})
                self.assertEqual(self.registration(w,run),{})
                self.assertEqual(w.outlet_readiness(base.AGENT2_PK,run.mapping_store),{})
        self.assertFalse(self.sends(world))

    def test_real_worker_orphan_wait_keeps_hint_and_new_topic_progress_without_business_error(self):
        from store import Store
        world,run,w,spec=self.assembly();orphan=self.orphan(run)
        rep=w.run({'buzz'})
        self.assertEqual(rep['errors'],0)
        self.assertEqual(rep['hostd']['cooperative_phases'],['buzz'])
        self.assertEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['reason'],'orphan_withdrawal_wait')
        lease=fixture.bw.ao.outlet_work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)[0]['retry_at']
        self.assertEqual(rep['hostd']['next_retry_at'],lease)
        self.assertGreater(lease,run.now_ts+1)
        self.assertEqual(w.outlet_readiness(base.AGENT2_PK,run.mapping_store),{})
        self.assertFalse(self.sends(world))
        with Store(run.mapping_store.path) as reopened:
            self.assertEqual(fixture.bw.ao.reaction_inbox.pending(reopened,'test',base.CHANNEL,base.AGENT2_PK)[0]['id'],orphan['id'])
        fresh=self.event(content='independent new source');self.put(world,fresh);w.outlet_rescan=True
        rep=w.run({'buzz'})
        self.assertEqual(rep['errors'],0);self.assertEqual(len(self.sends(world)),1)
        self.assertEqual(run.mapping_store.delivery_by_source('test',fresh['id'],'b2f',agent_id=base.AGENT2_PK)['status'],'acked')
        self.assertTrue(fixture.bw.ao.outlet_work.pending(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK))
        self.assertEqual(w.outlet_readiness(base.AGENT2_PK,run.mapping_store),{})

    def test_new_backlog_uses_immediate_slice_instead_of_old_orphan_lease(self):
        world,run,w,spec=self.assembly();self.orphan(run);rep=w.run({'buzz'})
        self.assertGreater(rep['hostd']['next_retry_at'],run.now_ts+1)
        for i in range(2):self.put(world,self.event(content='fresh backlog '+str(i)))
        w.outlet_rescan=True;rep=w.run({'buzz'})
        self.assertEqual(rep['errors'],0)
        self.assertEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['reason'],'slice_yield')
        self.assertEqual(rep['hostd']['next_retry_at'],run.now_ts+1)
        self.assertEqual(len(self.sends(world)),1)
        rep=w.run({'buzz'})
        self.assertEqual(rep['errors'],0);self.assertEqual(len(self.sends(world)),2)
        self.assertEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['reason'],'orphan_withdrawal_wait')

    def test_revoked_public_authority_does_not_become_cooperative_wait(self):
        world,run,w,spec=self.assembly();self.orphan(run)
        world.relay_events.append(base.FGS.sign_event(base.OWNER_KEY,30177,[['d',base.AGENT2_PK]],json.dumps({'feishu':{'app_id':'cli_revoked'}}),run.now_ts))
        rep=w.run({'buzz'})
        self.assertGreater(rep['errors'],0);self.assertEqual(rep['hostd']['cooperative_phases'],[])
        self.assertFalse(self.sends(world));self.assertEqual(w.outlet_readiness(base.AGENT2_PK,run.mapping_store),{})

    def test_actual_verify_failure_keeps_original_type_and_is_not_waiting(self):
        world,run,w,spec=self.assembly();self.orphan(run)
        failure=base.FGS.GroupSyncError('controlled proof failure')
        with mock.patch.object(fixture.bw.ao.AgentOutlet,'verify',side_effect=failure):rep=w.run({'buzz'})
        self.assertGreater(rep['errors'],0);self.assertEqual(rep['hostd']['cooperative_phases'],[])
        self.assertNotEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['reason'],'orphan_withdrawal_wait')
        self.assertFalse(self.sends(world))

    def test_existing_unknown_business_delivery_never_becomes_metadata_wait(self):
        world,run,w,spec=self.assembly();self.orphan(run);w.run({'buzz'})
        db=run.mapping_store
        saved=db.reserve_delivery('test','b'*64,'r2f',agent_id=base.AGENT2_PK,source_at=run.now_ts,now=run.now_ts)
        db.fail_delivery(saved.id,unknown=True,now=run.now_ts)
        rep=w.run({'buzz'})
        self.assertGreater(rep['errors'],0);self.assertEqual(rep['hostd']['cooperative_phases'],[])
        self.assertEqual(db.delivery_by_source('test','b'*64,'r2f',agent_id=base.AGENT2_PK)['status'],'unknown')
        self.assertFalse(self.sends(world))

    def test_partial_history_read_is_never_a_cooperative_wait(self):
        world,run,w,spec=self.assembly();self.orphan(run)
        with mock.patch.object(fixture.bw.ao,'signed_history',side_effect=base.FGS.GroupSyncError('controlled history failure')):
            rep=w.run({'buzz'})
        self.assertGreater(rep['errors'],0);self.assertEqual(rep['hostd']['cooperative_phases'],[])
        self.assertFalse(self.sends(world))

    def test_current_effect_failure_keeps_exact_exception_instead_of_deferred(self):
        world,run,w,spec=self.assembly()
        adapter=fixture.bw.ao.AgentOutlet(run,base.AGENT2_PK,spec.env_file,
            bot_client=run.clients.agents[spec.app_id],trusted_relays={'https://relay.test'},
            http=world.http_get,clock=lambda:base.NOW,initial_since=run.state.buzz_floor)
        self.put(world,self.event())
        failure=base.FGS.GroupSyncError('controlled actual effect proof failure')
        with mock.patch.object(adapter,'deliver',side_effect=failure):
            with self.assertRaises(base.FGS.GroupSyncError) as caught:adapter.catch_up(max_events=1)
        self.assertIs(caught.exception,failure)
        self.assertFalse(self.sends(world))

    def test_exact_target_network_failure_is_not_converted_to_unknown_scope(self):
        world,run,w,spec=self.assembly()
        adapter=fixture.bw.ao.AgentOutlet(run,base.AGENT2_PK,spec.env_file,
            bot_client=run.clients.agents[spec.app_id],trusted_relays={'https://relay.test'},
            http=world.http_get,clock=lambda:base.NOW,initial_since=run.state.buzz_floor)
        reaction=base.FGS.sign_event(base.AGENT2_KEY,7,[['e','a'*64]],'👍',run.now_ts)
        failure=base.FGS.GroupSyncError('controlled exact target read failure')
        with mock.patch.object(adapter,'_original',return_value=None),mock.patch.object(adapter,'_query',side_effect=failure):
            with self.assertRaises(base.FGS.GroupSyncError) as caught:adapter._retained_reaction_scope(reaction)
        self.assertIs(caught.exception,failure)

    def test_actual_worker_failed_root_does_not_dispatch_second_independent_candidate(self):
        self._failed_attempt_budget(network=False)

    def test_actual_worker_network_failure_does_not_dispatch_second_independent_candidate(self):
        self._failed_attempt_budget(network=True)

    def _failed_attempt_budget(self,*,network):
        world,run,w,spec=self.assembly()
        bad=self.event(tags=[['e','a'*64,'','root']])
        fresh=self.event(content='independent candidate',created=run.now_ts-1)
        self.put(world,bad);self.put(world,fresh)
        calls=[];original=fixture.bw.ao.AgentOutlet._deliver
        def attempt(adapter,event,ancestors,**kw):
            if not ancestors:calls.append(event['id'])
            return original(adapter,event,ancestors,**kw)
        from contextlib import nullcontext
        guard=(mock.patch.object(fixture.bw.ao.AgentOutlet,'_original',side_effect=base.FGS.GroupSyncError('controlled network failure')) if network else nullcontext())
        with mock.patch.object(fixture.bw.ao.AgentOutlet,'_deliver',attempt),guard:rep=w.run({'buzz'})
        self.assertEqual(calls,[bad['id']])
        self.assertFalse(self.sends(world))
        self.assertGreater(rep['errors'],0)
        self.assertEqual(rep['hostd']['cooperative_phases'],[])
        self.assertEqual(rep['hostd']['outlet_results'][base.AGENT2_PK]['reason'],'own_outlet_attempt_failed')

class RegistrationScope(base.TmpCase, unittest.IsolatedAsyncioTestCase):
    """Actual signed orphan work and Worker cache, not a fabricated ready dict."""
    assembly=OrphanWait.assembly
    sends=OrphanWait.sends

    async def registered_worker(self, *, orphans=True):
        import asyncio
        from types import SimpleNamespace
        from hostd.runtime_registration import RuntimeRegistrar
        world,run,worker,spec=self.assembly();db=run.mapping_store
        request='JOIN-1234abcd'
        db.register_agent(base.AGENT2_PK,owner_pubkey=base.OWNER_PK,app_id=spec.app_id,now=run.now_ts)
        db.create_join(request,base.AGENT2_PK,base.OWNER_PK,spec.app_id,base.CHAT,
                       kind='channel',binding_id='test',now=run.now_ts)
        db.conn.execute("UPDATE join_request SET status='approved' WHERE request_id=?",(request,))
        plan=db.ensure_effect_plan(request,base.CHANNEL,'test',str(spec.env_file),str(worker.config),now=run.now_ts)
        row=db.join_request(request)
        if orphans:
            for target in ('a'*64,'b'*64):
                event=base.FGS.sign_event(base.AGENT2_KEY,5,[['e',target]],'',run.now_ts-10)
                self.assertTrue(fixture.bw.ao.reaction_inbox.retain(db,'test',base.CHANNEL,base.AGENT2_PK,event))
        worker.run({'buzz'})
        proof=worker.registration_readiness(base.AGENT2_PK,db)
        self.assertTrue(proof)
        self.assertEqual(proof.get('registration_only',False),orphans)
        # Aggregate health can reflect another agent or phase. It is not in
        # this target's authority fingerprint or its retained-work proof.
        db.conn.execute("UPDATE binding SET status='degraded' WHERE binding_id='test'")
        task=asyncio.create_task(asyncio.Event().wait())
        async def close():
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.addAsyncCleanup(close)
        binding=SimpleNamespace(channel_id=base.CHANNEL,chat_id=base.CHAT,config=worker.config,sync_app_id=spec.app_id)
        host=SimpleNamespace(runtime_store=db,onboarding=SimpleNamespace(records={spec.app_id:spec}),
            workers={'test':worker},status={'apps':{spec.app_id:{'feishu':'connected'}},
                'bindings':{'test':{'relay':'connected','runs':1}}},
            app_tasks={spec.app_id:task},binding_tasks={'test':{'worker':task,'relay':task}},
            outlet_tasks={('test',base.AGENT2_PK):task},outlet_status={('test',base.AGENT2_PK):'connected'},
            reg=SimpleNamespace(bindings={'test':binding}),
            _binding_state=lambda name:db.conn.execute('SELECT status FROM binding WHERE binding_id=?',(name,)).fetchone()[0])
        return RuntimeRegistrar(host,clock=lambda:base.NOW.timestamp()),host,row,plan,world,run,worker,spec

    async def test_two_real_orphans_ready_in_degraded_binding_without_settlement(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker()
        db=run.mapping_store;before=list(db.conn.iterdump())
        proof=await registrar.readback(row,plan)
        self.assertTrue(proof.worker_active)
        self.assertTrue(proof.reader_connected and proof.relay_connected and proof.outlet_active)
        self.assertEqual(len(fixture.bw.ao.outlet_work.pending(db,'test',base.CHANNEL,base.AGENT2_PK)),2)
        self.assertEqual(worker.outlet_readiness(base.AGENT2_PK,db),{})
        self.assertEqual(list(db.conn.iterdump()),before)
        self.assertFalse(self.sends(world))

    async def test_degraded_verified_target_does_not_wait_for_other_business(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker(orphans=False)
        proof=await registrar.readback(row,plan)
        self.assertTrue(proof.worker_active and proof.outlet_active)
        self.assertFalse(self.sends(world))

    async def test_nonoperational_binding_states_still_reject_real_orphan_proof(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker()
        for state in ('paused','conflict','retired','pending'):
            with self.subTest(state=state):
                run.mapping_store.conn.execute('UPDATE binding SET status=? WHERE binding_id=?',(state,'test'))
                self.assertFalse((await registrar.readback(row,plan)).worker_active)
        self.assertFalse(self.sends(world))

    async def test_degraded_rejects_expired_or_changed_own_scope(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker()
        registrar.clock=lambda:base.NOW.timestamp()+301
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active or proof.outlet_active)
        registrar.clock=lambda:base.NOW.timestamp()
        spec.env_file.write_bytes(spec.env_file.read_bytes()+b'\n')
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active or proof.outlet_active)
        self.assertFalse(self.sends(world))

    async def test_degraded_own_unknown_invalidates_real_orphan_readiness(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker()
        db=run.mapping_store;delivery=db.reserve_delivery('test','c'*64,'r2f',agent_id=base.AGENT2_PK,source_at=run.now_ts,now=run.now_ts)
        db.fail_delivery(delivery.id,unknown=True,now=run.now_ts)
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active or proof.outlet_active)
        self.assertEqual(db.delivery_by_source('test','c'*64,'r2f',agent_id=base.AGENT2_PK)['status'],'unknown')
        self.assertEqual(len(fixture.bw.ao.outlet_work.pending(db,'test',base.CHANNEL,base.AGENT2_PK)),2)
        self.assertFalse(self.sends(world))

    async def test_degraded_ordinary_work_cannot_reuse_orphan_cache(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker()
        event=base.FGS.sign_event(base.AGENT2_KEY,7,[['e','c'*64]],'+',run.now_ts-10)
        self.assertTrue(fixture.bw.ao.reaction_inbox.retain(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,event))
        fixture.bw.ao.outlet_work.capture(run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,[event],
            floor=run.now_ts-1200,now=run.now_ts,retained={event['id']})
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active or proof.outlet_active)
        self.assertFalse(self.sends(world))

    async def test_new_ordinary_work_after_verified_cache_blocks_degraded_only(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker(orphans=False)
        self.assertTrue((await registrar.readback(row,plan)).worker_active)
        event=base.FGS.sign_event(base.AGENT2_KEY,7,[['e','c'*64]],'+',run.now_ts-10)
        db=run.mapping_store
        self.assertTrue(fixture.bw.ao.reaction_inbox.retain(db,'test',base.CHANNEL,base.AGENT2_PK,event))
        fixture.bw.ao.outlet_work.capture(db,'test',base.CHANNEL,base.AGENT2_PK,[event],
            floor=run.now_ts-1200,now=run.now_ts,retained={event['id']})
        self.assertEqual(db.conn.execute('SELECT count(*) FROM delivery').fetchone()[0],0)
        before=list(db.conn.iterdump())
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active or proof.outlet_active)
        self.assertEqual(list(db.conn.iterdump()),before)
        # No change to active bindings' pre-existing normal-cache contract.
        db.conn.execute("UPDATE binding SET status='active' WHERE binding_id='test'")
        proof=await registrar.readback(row,plan)
        self.assertTrue(proof.worker_active and proof.outlet_active)
        self.assertFalse(self.sends(world))

    async def test_legacy_pending_after_verified_cache_blocks_own_degraded_scope(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker(orphans=False)
        db=run.mapping_store
        db.register_agent('f'*64,owner_pubkey=base.OWNER_PK,app_id='cli_other',now=run.now_ts)
        db.conn.execute('INSERT INTO outlet_pending VALUES(?,?,?,?,?,?,?)',
            ('test','f'*64,'c'*64,base.CHANNEL,7,run.now_ts-10,run.now_ts-1200))
        # Another agent's durable pending work must not become this agent's gate.
        self.assertTrue((await registrar.readback(row,plan)).worker_active)
        db.conn.execute('INSERT INTO outlet_pending VALUES(?,?,?,?,?,?,?)',
            ('test',base.AGENT2_PK,'c'*64,base.CHANNEL,7,run.now_ts-10,run.now_ts-1200))
        before=list(db.conn.iterdump())
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active or proof.outlet_active)
        self.assertEqual(list(db.conn.iterdump()),before)
        self.assertFalse(self.sends(world))

    async def test_degraded_lost_claim_invalidates_actual_proof(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker()
        for verdict in ('lost','unreadable',''):
            worker.verdict=verdict
            proof=await registrar.readback(row,plan)
            self.assertFalse(proof.worker_active or proof.outlet_active)
        self.assertFalse(self.sends(world))

    async def test_degraded_requires_running_worker_and_all_actual_connections(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker()
        host.status['apps'][spec.app_id]['feishu']='disconnected'
        self.assertFalse((await registrar.readback(row,plan)).reader_connected)
        host.status['apps'][spec.app_id]['feishu']='connected'
        host.status['bindings']['test']['relay']='disconnected'
        self.assertFalse((await registrar.readback(row,plan)).relay_connected)
        host.status['bindings']['test']['relay']='connected'
        host.outlet_status[('test',base.AGENT2_PK)]='connecting'
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active or proof.outlet_active)
        host.outlet_status[('test',base.AGENT2_PK)]='connected'
        task=host.binding_tasks['test']['worker'];task.cancel()
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active or proof.reader_connected or proof.relay_connected or proof.outlet_active)
        self.assertFalse(self.sends(world))

    async def test_degraded_needs_scoped_observer_not_old_report_fallback(self):
        registrar,host,row,plan,world,run,worker,spec=await self.registered_worker(orphans=False)
        worker.registration_readiness=None
        proof=await registrar.readback(row,plan)
        self.assertFalse(proof.worker_active)
        self.assertFalse(self.sends(world))

if __name__=='__main__':unittest.main()
