"""Actual Worker readiness must outlive unrelated rounds without renewing proof."""
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
import unittest
import test_hostd_worker_outlets as fixture

base=fixture.base
setUpModule=fixture.setUpModule
tearDownModule=fixture.tearDownModule

class Readiness(base.TmpCase):
    assembly=fixture.WorkerOutlets.assembly

    def proof(self, worker, run):
        method=getattr(worker,'outlet_readiness',None)
        return method(base.AGENT2_PK,run.mapping_store) if method else worker.last.get('hostd',{}).get('outlet_results',{}).get(base.AGENT2_PK,{})

    def test_actual_notice_round_preserves_original_success_timestamp(self):
        world,run,w,spec=self.assembly();w.run({'buzz'})
        first=copy.deepcopy(self.proof(w,run));self.assertEqual(first['pending'],0)
        w.clock=lambda:base.NOW+timedelta(seconds=10)
        w.run({'notice'})
        self.assertEqual(self.proof(w,run),first)

    def test_peer_slice_does_not_erase_but_selected_failure_does(self):
        world,run,w,spec=self.assembly();w.run({'buzz'})
        first=copy.deepcopy(self.proof(w,run))
        bad='0'*64
        w.outlet_specs[bad]=SimpleNamespace(pubkey=bad,owner_pubkey=base.OWNER_PK,app_id='cli_bad',status='blocked')
        w.outlet_rescan=True;w.run({'buzz'})
        self.assertEqual(self.proof(w,run),first)
        spec.status='blocked';w.outlet_rescan=True;w.run({'buzz'})
        self.assertFalse(self.proof(w,run))

    def test_selected_slice_with_more_work_and_unknown_ledger_cannot_reuse_success(self):
        world,run,w,spec=self.assembly();w.run({'buzz'});self.assertTrue(self.proof(w,run))
        events=[fixture.WorkerOutlets.event(self,content='later'+str(n)) for n in range(2)]
        world.events.extend(events);world.relay_events.extend(events)
        w.outlet_rescan=True;w.run({'buzz'})
        self.assertFalse(self.proof(w,run))
        w.run({'buzz'});self.assertTrue(self.proof(w,run))
        row=run.mapping_store.reserve_delivery('test','a'*64,'b2f',agent_id=base.AGENT2_PK,now=run.now_ts)
        run.mapping_store.fail_delivery(row.id,unknown=True,now=run.now_ts)
        self.assertFalse(self.proof(w,run))

    def test_config_change_and_new_worker_cannot_reuse_success(self):
        world,run,w,spec=self.assembly();w.run({'buzz'});self.assertTrue(self.proof(w,run))
        base.write_owner_only(w.config,w.config.read_text()+'\n')
        self.assertFalse(self.proof(w,run))
        self.assertEqual(fixture.bw.Worker(w.config,w.state_dir)._outlet_readiness,{})

    def test_local_env_and_negative_grant_invalidate_before_next_round(self):
        world,run,w,spec=self.assembly();w.run({'buzz'})
        self.assertTrue(self.proof(w,run))
        run.mapping_store.conn.execute("UPDATE agent_chat SET status='paused' WHERE agent_id=?",(base.AGENT2_PK,))
        self.assertFalse(self.proof(w,run))
        run.mapping_store.conn.execute("UPDATE agent_chat SET status='active' WHERE agent_id=?",(base.AGENT2_PK,))
        self.assertFalse(self.proof(w,run))
        w.outlet_rescan=True;w.run({'buzz'});self.assertTrue(self.proof(w,run))
        base.write_owner_only(spec.env_file,spec.env_file.read_text().replace(base.CHANNEL,'00000000-0000-0000-0000-000000000099'))
        self.assertFalse(self.proof(w,run))

    def test_fresh_setup_failure_invalidates_old_success(self):
        world,run,w,spec=self.assembly();w.run({'buzz'});self.assertTrue(self.proof(w,run))
        with mock.patch.object(fixture.bw.dm.MappedHostdRound,'verify_identities',side_effect=RuntimeError('offline fixture')):
            with self.assertRaises(RuntimeError):w.run({'notice'})
        self.assertFalse(self.proof(w,run))

    def test_fresh_signed_policy_revocation_invalidates_selected_agent(self):
        world,run,w,spec=self.assembly();w.run({'buzz'});self.assertTrue(self.proof(w,run))
        world.relay_events.append(base.FGS.sign_event(base.OWNER_KEY,30177,[['d',base.AGENT2_PK]],
            json.dumps({'feishu':{'app_id':'cli_revoked'}}),int(base.NOW.timestamp())))
        w.outlet_rescan=True;w.run({'buzz'})
        self.assertFalse(self.proof(w,run))

    def test_fresh_lost_claim_invalidates_before_another_outlet_can_run(self):
        world,run,w,spec=self.assembly();w.run({'buzz'});self.assertTrue(self.proof(w,run))
        with mock.patch.object(fixture.bw.dm.MappedHostdRound,'check_claims',return_value='lost'):
            w.run({'notice'})
        self.assertFalse(self.proof(w,run))

if __name__=='__main__':unittest.main()
