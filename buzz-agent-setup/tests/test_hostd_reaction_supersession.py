"""Withdrawn transient reactions never flash late; real signed offline flow."""
import copy
import json
import hashlib
import unittest
from datetime import datetime, timezone
import test_hostd_reaction_inbox as fixture
import outlet
from store import StoreError
from outlet import AgentOutlet

base=fixture.base
setUpModule=fixture.setUpModule
tearDownModule=fixture.tearDownModule

class Supersession(base.TmpCase):
    assembly=fixture.Durability.assembly
    row=fixture.Durability.row
    capture=fixture.Durability.capture
    host=fixture.Durability.host
    disappear=fixture.Durability.disappear
    restart=fixture.Durability.restart
    count=fixture.Durability.count
    catch_up_due=fixture.Durability.catch_up_due

    def pair(self, emoji='💬', *, stock=True):
        self.assembly()
        self.disappear(self.reaction)
        tags=[['e',self.root_event['id']]]+([] if stock else [['h',base.CHANNEL]])
        self.reaction=base.FGS.sign_event(base.AGENT2_KEY,7,tags,emoji,self.run.now_ts-1)
        deletion=base.FGS.sign_event(base.AGENT2_KEY,5,[['e',self.reaction['id']]],'',self.run.now_ts)
        self.capture(self.reaction,deletion)
        return deletion

    def test_captured_typing_and_tombstone_settle_without_native_flash(self):
        deletion=self.pair(); self.adapter.catch_up(max_events=1)
        self.assertEqual(self.mutations,[])
        for event in (self.reaction,deletion):
            row=self.row(event)
            self.assertEqual(row['status'],'skipped')
            self.assertEqual(row['attempts'],0)
            self.assertIsNone(row['target_id'])
            self.assertIsNone(self.run.mapping_store.outlet_receipt(row['id']))
        self.assertEqual(self.count(),0)
        self.assertFalse(self.adapter.slice_pending)

    def test_restart_and_replayed_exact_pair_never_dispatch(self):
        deletion=self.pair('👀'); self.adapter.catch_up(max_events=1)
        self.restart(); self.capture(deletion,self.reaction)
        self.assertEqual(self.count(),0)
        self.world.events.extend([self.reaction,deletion]);self.world.relay_events.extend([self.reaction,deletion])
        self.adapter.catch_up(max_events=1)
        self.assertEqual(self.mutations,[])

        self.adapter.deliver(self.reaction); self.adapter.deliver(deletion)
        self.assertEqual(self.mutations,[])

    def test_degraded_binding_recovers_no_effect_pair_without_native_dispatch(self):
        deletion = self.pair()
        db = self.run.mapping_store
        db.conn.execute("UPDATE binding SET status='degraded' WHERE binding_id='test'")
        self.adapter.catch_up(max_events=1)
        self.assertEqual(self.mutations, [])
        self.assertEqual(self.row(self.reaction)['status'], 'skipped')
        self.assertEqual(self.row(deletion)['status'], 'skipped')
        self.assertEqual(self.count(), 0)
        self.assertEqual(db.conn.execute("SELECT status FROM binding WHERE binding_id='test'").fetchone()[0], 'degraded')

    def test_non_running_binding_cannot_settle_no_effect_pair(self):
        deletion = self.pair()
        db = self.run.mapping_store
        for status in ('paused', 'retired', 'pending'):
            with self.subTest(status=status):
                db.conn.execute("UPDATE binding SET status=? WHERE binding_id='test'", (status,))
                with self.assertRaises(StoreError):
                    outlet.reaction_supersession.settle(
                        db, 'test', base.CHANNEL, base.AGENT2_PK, self.adapter.app,
                        self.reaction, deletion, self.root_event,
                        reaction_map={}, now=self.run.now_ts)
                self.assertIsNone(self.row(self.reaction))
                self.assertIsNone(self.row(deletion))
        self.assertEqual(self.mutations, [])


    def test_delete_arrival_first_and_same_second_id_order_survive_restart(self):
        deletion=self.pair()
        self.run.mapping_store.conn.execute('DELETE FROM reaction_inbox')
        deletion=base.FGS.sign_event(base.AGENT2_KEY,5,[['e',self.reaction['id']]],'',self.reaction['created_at']-1)
        self.capture(deletion,self.reaction)
        self.restart(); self.adapter.catch_up(max_events=1)
        self.assertEqual(self.mutations,[])
        self.assertEqual(self.row(self.reaction)['status'],'skipped')
        self.assertEqual(self.row(deletion)['status'],'skipped')

    def test_preexisting_pending_unknown_failed_or_waiting_delivery_never_coalesces(self):
        deletion=self.pair(); db=self.run.mapping_store
        digest=hashlib.sha256(json.dumps([self.target,'Typing',False],separators=(',',':')).encode()).hexdigest()
        record=db.reserve_delivery('test',self.reaction['id'],'r2f',agent_id=base.AGENT2_PK,
            source_at=self.reaction['created_at'],content_hash=digest,stream='relay',root_id=self.root_event['id'],now=self.run.now_ts)
        for status in ('pending','unknown','waiting_receipt','failed'):
            db.conn.execute('UPDATE delivery SET status=? WHERE id=?',(status,record.id))
            self.assertFalse(outlet.reaction_supersession.eligible(db,'test',base.CHANNEL,base.AGENT2_PK,self.reaction,deletion,{}))
        db.conn.execute("UPDATE delivery SET status='unknown' WHERE id=?",(record.id,))
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.catch_up(max_events=1)
        self.assertEqual(self.row(self.reaction)['status'],'unknown')
        self.assertIsNone(self.row(deletion));self.assertEqual(self.mutations,[])
        self.assertTrue(self.reads);self.assertEqual(self.count(),2)

    def test_actual_lost_post_stays_readback_recovery_not_cancellation(self):
        deletion=self.pair();self.lose='create'
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(self.reaction)
        self.restart();self.adapter.catch_up(max_events=1)
        self.assertEqual(self.row(self.reaction)['status'],'acked')
        self.assertEqual(self.mutations,['create']);self.assertTrue(self.reads)
        self.catch_up_due(max_events=1)
        self.assertEqual(self.mutations,['create','delete'])
        self.assertEqual(self.row(deletion)['status'],'acked')

    def test_prior_scheduler_attempt_even_without_native_reservation_is_not_coalesced(self):
        deletion=self.pair();db=self.run.mapping_store
        outlet.outlet_work.capture(db,'test',base.CHANNEL,base.AGENT2_PK,[self.reaction,deletion],floor=0,now=self.run.now_ts,retained={self.reaction['id'],deletion['id']})
        row,turn=outlet.outlet_work.choose(db,'test',base.CHANNEL,base.AGENT2_PK,[self.reaction,deletion],now=self.run.now_ts)
        outlet.outlet_work.start(db,'test',base.AGENT2_PK,row,turn,now=self.run.now_ts)
        self.catch_up_due(max_events=1)
        self.assertEqual(self.mutations,['create']);self.assertEqual(self.row(self.reaction)['status'],'acked')

    def test_local_revocations_leave_pair_pending_and_zero_effects(self):
        deletion=self.pair();db=self.run.mapping_store
        for table,column in (('agent_chat','agent_id'),('agent','pubkey'),('binding','binding_id')):
            key='test' if table=='binding' else base.AGENT2_PK
            db.conn.execute(f"UPDATE {table} SET status='paused' WHERE {column}=?",(key,))
            with self.subTest(table=table):
                with self.assertRaises(base.FGS.GroupSyncError):self.adapter.catch_up(max_events=1)
                self.assertIsNone(self.row(self.reaction));self.assertIsNone(self.row(deletion));self.assertEqual(self.count(),2)
            db.conn.execute(f"UPDATE {table} SET status='active' WHERE {column}=?",(key,))
        self.assertEqual(self.mutations,[])

    def test_signed_policy_revocation_prevents_pair_settlement(self):
        deletion=self.pair()
        revoke=base.FGS.sign_event(base.OWNER_KEY,30177,[['d',base.AGENT2_PK]],'{}',self.run.now_ts+1)
        self.world.relay_events.append(revoke)
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.catch_up(max_events=1)
        self.assertIsNone(self.row(deletion));self.assertEqual(self.count(),2);self.assertEqual(self.mutations,[])

    def test_wrong_author_channel_or_invalid_signature_is_not_cancellation_evidence(self):
        deletion=self.pair();db=self.run.mapping_store
        bad_events=[base.FGS.sign_event(base.OWNER_KEY,5,[['e',self.reaction['id']]],'',self.run.now_ts),
            base.FGS.sign_event(base.AGENT2_KEY,5,[['e',self.reaction['id']],['h','foreign-channel']],'',self.run.now_ts),
            base.FGS.sign_event(base.AGENT2_KEY,5,[['e',self.reaction['id']],['p',base.OWNER_PK]],'',self.run.now_ts)]
        bad=copy.deepcopy(deletion);bad['sig']='0'*128;bad_events.append(bad)
        for bad in bad_events:
            with self.subTest(event_id=bad['id']):
                self.assertFalse(outlet.reaction_supersession.eligible(db,'test',base.CHANNEL,base.AGENT2_PK,self.reaction,bad,{}))
        # Actual retained-envelope reader rejects tampering, not just helper tests.
        saved=dict(db.conn.execute('SELECT * FROM reaction_inbox WHERE source_id=?',(deletion['id'],)).fetchone())
        with self.assertRaises(__import__('sqlite3').IntegrityError):
            db.conn.execute('UPDATE reaction_inbox SET event_json=? WHERE source_id=?',('{}',deletion['id']))
        db.conn.execute('DELETE FROM reaction_inbox WHERE source_id=?',(deletion['id'],))
        saved['event_json']=json.dumps(bad_events[-1],sort_keys=True,separators=(',',':'),ensure_ascii=False)
        db.conn.execute('INSERT INTO reaction_inbox VALUES(?,?,?,?,?,?)',tuple(saved.values()))
        with self.assertRaises(StoreError):self.adapter.catch_up(max_events=1)
        self.assertIsNone(self.row(self.reaction));self.assertEqual(self.mutations,[])

    def test_unknown_or_foreign_original_cannot_settle_pair(self):
        deletion=self.pair();db=self.run.mapping_store
        for original in (None,base.FGS.sign_event(base.AGENT2_KEY,9,[['h','foreign-channel']],'fixture',self.run.now_ts)):
            self.assertFalse(outlet.reaction_supersession.settle(db,'test',base.CHANNEL,base.AGENT2_PK,self.adapter.app,
                self.reaction,deletion,original,reaction_map={},now=self.run.now_ts))
        self.disappear(self.root_event)
        with self.assertRaises(outlet.OutletDeferred):self.adapter.catch_up(max_events=1)
        self.assertIsNone(self.row(self.reaction));self.assertEqual(self.mutations,[])

    def test_ordinary_reaction_and_custom_transient_mapping_keep_existing_delivery(self):
        deletion=self.pair('✅');self.adapter.catch_up(max_events=1)
        self.assertEqual(self.mutations,['create']);self.assertEqual(self.row(self.reaction)['status'],'acked')
        self.catch_up_due(max_events=1)
        self.assertEqual(self.mutations,['create','delete']);self.assertEqual(self.row(deletion)['status'],'acked')

    def test_custom_transient_mapping_is_not_implicitly_ephemeral(self):
        deletion=self.pair();self.run.cfg['reaction_map']={'💬':'DONE'}
        self.adapter.catch_up(max_events=1)
        self.assertEqual(self.mutations,['create']);self.assertEqual(self.row(self.reaction)['status'],'acked')

    def test_other_live_reference_and_foreign_native_reactions_unchanged(self):
        deletion=self.pair()
        live=base.FGS.sign_event(base.AGENT2_KEY,7,[['e',self.root_event['id']]],'💬',self.run.now_ts-3)
        self.adapter.deliver(live)
        self.world.reactions.extend([dict(self.world.reactions[0],id='foreign-app',app='cli_other'),
            dict(self.world.reactions[0],id='human',app='ou_human')])
        before=copy.deepcopy(self.world.reactions)
        self.adapter.catch_up(max_events=1)
        self.assertEqual(self.mutations,['create']);self.assertEqual(self.world.reactions,before)
        self.assertEqual(self.row(live)['status'],'acked');self.assertEqual(self.row(deletion)['status'],'skipped')

    def test_pair_commit_failure_rolls_back_both_terminal_rows_and_cleanup(self):
        deletion=self.pair();db=self.run.mapping_store
        db.conn.execute("CREATE TRIGGER fail_pair BEFORE UPDATE OF status ON delivery WHEN NEW.status='skipped' AND NEW.source_id='"+deletion['id']+"' BEGIN SELECT RAISE(ABORT,'offline fault'); END")
        with self.assertRaises(StoreError):self.adapter.catch_up(max_events=1)
        self.assertIsNone(self.row(self.reaction));self.assertIsNone(self.row(deletion));self.assertEqual(self.count(),2)
        self.assertEqual(self.mutations,[])
        db.conn.execute('DROP TRIGGER fail_pair');self.restart();self.adapter.catch_up(max_events=1)
        self.assertEqual(self.row(self.reaction)['status'],'skipped');self.assertEqual(self.mutations,[])

    def test_no_ack_cursor_advance_and_later_distinct_withdrawal_is_no_effect(self):
        deletion=self.pair();db=self.run.mapping_store
        before=db.cursor_position('test','relay',agent_id=base.AGENT2_PK)
        self.adapter.catch_up(max_events=1)
        self.assertEqual(db.cursor_position('test','relay',agent_id=base.AGENT2_PK),before)
        later=base.FGS.sign_event(base.AGENT2_KEY,5,[['e',self.reaction['id']]],'',self.run.now_ts+2)
        self.capture(later);self.assertEqual(self.count(),0)
        self.adapter.deliver(later);self.assertEqual(self.mutations,[])

    def test_missing_or_modified_counterpart_never_counts_as_terminal_proof(self):
        deletion=self.pair();self.adapter.catch_up(max_events=1);db=self.run.mapping_store
        db.conn.execute("UPDATE delivery SET content_hash=? WHERE source_id=?",('0'*64,deletion['id']))
        self.assertFalse(outlet.reaction_supersession.superseded(db,'test',base.CHANNEL,base.AGENT2_PK,self.adapter.app,self.reaction))
        with self.assertRaises((StoreError,base.FGS.GroupSyncError)):self.adapter.deliver(self.reaction)
        self.assertEqual(self.mutations,[])


    def test_explicit_channel_pair_and_preferred_body_keep_one_work_unit_budget(self):
        deletion=self.pair(stock=False)
        body=base.FGS.sign_event(base.AGENT2_KEY,9,[['h',base.CHANNEL]],'independent fixture body',self.run.now_ts)
        self.world.events.append(body);self.world.relay_events.append(body)
        self.adapter.catch_up(max_events=1)
        saved=self.run.mapping_store.delivery_by_source('test',body['id'],'b2f',agent_id=base.AGENT2_PK)
        self.assertEqual(saved['status'],'acked');self.assertIsNone(self.row(self.reaction))
        self.assertTrue(self.adapter.slice_pending);self.assertEqual(self.mutations,[])
        self.adapter.catch_up(max_events=1)
        self.assertEqual(self.row(deletion)['status'],'skipped');self.assertFalse(self.adapter.slice_pending)
        self.assertEqual(self.mutations,[])

    def test_cleanup_failure_rolls_back_pair_before_restart(self):
        deletion=self.pair();db=self.run.mapping_store
        db.conn.execute('INSERT INTO outlet_work_turn VALUES(?,?,5)',('test',base.AGENT2_PK))
        db.conn.execute("CREATE TRIGGER fail_cleanup BEFORE DELETE ON reaction_inbox BEGIN SELECT RAISE(ABORT,'offline cleanup'); END")
        with self.assertRaises(StoreError):self.adapter.catch_up(max_events=1)
        self.assertIsNone(self.row(self.reaction));self.assertIsNone(self.row(deletion));self.assertEqual(self.count(),2)
        self.assertEqual(self.mutations,[])
        self.assertEqual(db.conn.execute('SELECT fresh_turns FROM outlet_work_turn').fetchone()[0],5)
        db.conn.execute('DROP TRIGGER fail_cleanup');self.restart();self.adapter.catch_up(max_events=1)
        self.assertEqual(self.run.mapping_store.conn.execute('SELECT fresh_turns FROM outlet_work_turn').fetchone()[0],6)
        self.assertEqual(self.count(),0);self.assertEqual(self.row(deletion)['status'],'skipped')

    def test_already_attempted_withdrawal_keeps_conservative_recovery(self):
        deletion=self.pair();db=self.run.mapping_store
        outlet.outlet_work.capture(db,'test',base.CHANNEL,base.AGENT2_PK,[deletion],floor=0,now=self.run.now_ts,retained={deletion['id']})
        row,turn=outlet.outlet_work.choose(db,'test',base.CHANNEL,base.AGENT2_PK,[deletion],now=self.run.now_ts)
        outlet.outlet_work.start(db,'test',base.AGENT2_PK,row,turn,now=self.run.now_ts)
        self.assertFalse(outlet.reaction_supersession.eligible(db,'test',base.CHANNEL,base.AGENT2_PK,self.reaction,deletion,{}))
        with self.assertRaises(outlet.OutletDeferred):self.adapter.catch_up(max_events=1)
        self.assertEqual(self.mutations,['create']);self.assertEqual(self.row(self.reaction)['status'],'acked')
        self.assertIsNone(self.row(deletion))


    def test_continuous_pairs_age_due_unknown_readback_by_eighth_turn_across_restart(self):
        self.assembly();old=self.reaction;self.lose='create'
        with self.assertRaises(base.FGS.GroupSyncError):self.adapter.deliver(old)
        db=self.run.mapping_store
        outlet.outlet_work.capture(db,'test',base.CHANNEL,base.AGENT2_PK,[old],floor=0,now=self.run.now_ts,retained=set())
        # A prior aging selection has leased this uncertain recovery then crashed.
        db.conn.execute('INSERT INTO outlet_work_turn VALUES(?,?,7)',('test',base.AGENT2_PK))
        row,turn=outlet.outlet_work.choose(db,'test',base.CHANNEL,base.AGENT2_PK,[old],now=self.run.now_ts)
        self.assertEqual(turn,0)
        outlet.outlet_work.start(db,'test',base.AGENT2_PK,row,turn,now=self.run.now_ts)
        now=datetime.fromtimestamp(self.run.now_ts+120,timezone.utc)
        pairs=[]
        for index in range(8):
            root=base.FGS.sign_event(base.MIRROR_KEY,9,[['h',base.CHANNEL]],'independent '+str(index),self.run.now_ts+60+index)
            self.world.events.append(root);self.world.relay_events.append(root)
            add=base.FGS.sign_event(base.AGENT2_KEY,7,[['e',root['id']]],'💬',self.run.now_ts+70+index)
            delete=base.FGS.sign_event(base.AGENT2_KEY,5,[['e',add['id']]],'',self.run.now_ts+80+index)
            pairs.append((add,delete));self.capture(add,delete)
            if index in (3,6):self.restart()
            self.world.clock=now;self.run.auth_clock=lambda:now;self.adapter.clock=lambda:now
            self.adapter.catch_up(max_events=1)
        self.assertEqual(self.row(old)['status'],'acked')
        self.assertTrue(self.reads);self.assertEqual(self.mutations,['create'])
        for add,delete in pairs[:7]:
            for event in (add,delete):
                self.assertEqual(self.row(event)['status'],'skipped');self.assertEqual(self.row(event)['attempts'],0)
        self.assertIsNone(self.row(pairs[-1][0]))
        saved=self.run.mapping_store.conn.execute('SELECT fresh_turns FROM outlet_work_turn WHERE binding_id=? AND agent_id=?',('test',base.AGENT2_PK)).fetchone()
        self.assertEqual(saved[0],0)

    def test_turn_commit_failure_rolls_back_pair_and_queues(self):
        deletion=self.pair();db=self.run.mapping_store
        db.conn.execute("CREATE TRIGGER fail_turn BEFORE INSERT ON outlet_work_turn BEGIN SELECT RAISE(ABORT,'offline turn'); END")
        with self.assertRaises(StoreError):self.adapter.catch_up(max_events=1)
        self.assertIsNone(self.row(self.reaction));self.assertIsNone(self.row(deletion));self.assertEqual(self.count(),2)
        self.assertEqual(self.mutations,[])
        db.conn.execute('DROP TRIGGER fail_turn');self.restart();self.adapter.catch_up(max_events=1)
        self.assertEqual(self.row(deletion)['attempts'],0);self.assertEqual(self.count(),0)

if __name__=='__main__':unittest.main()
