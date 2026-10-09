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

if __name__=='__main__':unittest.main()
