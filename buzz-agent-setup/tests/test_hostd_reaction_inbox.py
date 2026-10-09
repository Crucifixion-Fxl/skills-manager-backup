"""Real feed callback -> durable Store -> own outlet with offline transports."""
import asyncio
import copy
import json
from datetime import datetime,timezone
from types import SimpleNamespace
import unittest
import sqlite3
from unittest import mock
import test_hostd_outlet_reaction_recovery as recovery
import outlet
import store
inbox = outlet.reaction_inbox
from hostd.__main__ import Hostd

base = recovery.base
setUpModule = recovery.setUpModule
tearDownModule = recovery.tearDownModule

class Durability(base.TmpCase):
    assembly = recovery.Recovery.assembly
    row = recovery.Recovery.row
    deletion = recovery.Recovery.deletion

    def catch_up_due(self, **kwargs):
        # Recovery is now intentionally paced. Advance only the offline fixture
        # clock to the durable lease; no production retry gate is bypassed.
        rows=outlet.outlet_work.pending(self.run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK)
        now=datetime.fromtimestamp(max([int(base.NOW.timestamp()),*[r['retry_at'] for r in rows]])+1,timezone.utc)
        self.world.clock=now;self.run.auth_clock=lambda:now;self.adapter.clock=lambda:now
        try:return self.adapter.catch_up(**kwargs)
        except outlet.OutletDeferred:
            self.assertTrue(outlet.outlet_work.pending(self.run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK))

    def host(self):
        host = object.__new__(Hostd)
        host.store_path = self.run.mapping_store.path
        host.reg = SimpleNamespace(bindings={'test': SimpleNamespace(channel_id=base.CHANNEL)})
        host.workers = {'test': SimpleNamespace(outlet_specs={base.AGENT2_PK: object()}, outlet_rescan=False)}
        host.mirrors = {'test': base.MIRROR_PK}
        host._running = True
        host.trace = lambda *a, **kw: None
        host.mark = lambda *a, **kw: None
        host.retain_notice_hint = lambda *a: True
        host._round_slots = SimpleNamespace(promote=lambda *a: None)
        async def ledger(call): return call()
        host._ledger_call = ledger
        return host

    def capture(self, *events):
        host = self.host()
        async def feed():
            for event in events: await host.on_relay('test', event)
        asyncio.run(feed())

    def disappear(self, *events):
        ids = {event['id'] for event in events}
        for name in ('events', 'relay_events'):
            setattr(self.world, name, [event for event in getattr(self.world, name) if event['id'] not in ids])

    def test_verified_feed_reaction_survives_relay_deletion_before_worker_query(self):
        self.assembly()
        deletion = self.deletion()
        self.capture(self.reaction, deletion)
        self.disappear(self.reaction, deletion)
        # Real failure: the frame arrived but catch_up can no longer fetch it.
        self.catch_up_due()
        self.assertEqual(self.mutations, ['create', 'delete'])
        self.assertEqual(self.row(self.reaction)['status'], 'removed')
        self.assertEqual(self.row(deletion)['status'], 'acked')
        self.catch_up_due()
        self.assertEqual(self.mutations, ['create', 'delete'])

    def count(self):
        return self.run.mapping_store.conn.execute('SELECT count(*) FROM reaction_inbox').fetchone()[0]

    def restart(self):
        path = self.run.mapping_store.path
        self.run.mapping_store.close()
        self.run.mapping_store = store.Store(path)
        self.addCleanup(self.run.mapping_store.close)
        from state_store import StateAdapter
        state_adapter = StateAdapter(self.run.mapping_store, 'test', self.tmp / 'state',
            profile_id=f'bot:{base.AGENT_APP}:union_id:v1')
        self.run.state = state_adapter.load()
        self.run.persist = lambda: state_adapter.save(self.run.state)
        self.run._history.clear()
        self.adapter = outlet.AgentOutlet(self.run, base.AGENT2_PK, self.tmp / 'outlet.env',
            bot_client=self.adapter.client, trusted_relays={'https://relay.test'},
            http=self.world.http_get, clock=lambda: base.NOW)

    def test_restart_reordered_stock_withdrawal_is_causal_and_sliced(self):
        self.assembly()
        self.reaction = base.FGS.sign_event(base.AGENT2_KEY,7,[['e',self.root_event['id']]],'💬',self.run.now_ts)
        deletion = base.FGS.sign_event(base.AGENT2_KEY,5,[['e',self.reaction['id']]],'',self.run.now_ts-1)
        self.capture(deletion, self.reaction, self.reaction)
        self.assertEqual(self.count(),2)
        self.disappear(*[e for e in self.world.events if e.get('kind') in (5,7)])
        self.restart()
        self.catch_up_due(max_events=1)
        self.assertEqual(self.mutations,['create']); self.assertTrue(self.adapter.slice_pending)
        self.catch_up_due(max_events=1)
        self.assertEqual(self.mutations,['create','delete']); self.assertEqual(self.count(),0)
        self.capture(deletion,self.reaction)
        self.assertEqual(self.count(),0)

    def test_unknown_post_is_retained_but_never_repeated_even_after_restart(self):
        self.assembly(); self.capture(self.reaction); self.disappear(self.reaction)
        self.lose='create'
        with self.assertRaises(base.FGS.GroupSyncError): self.adapter.catch_up()
        self.assertEqual(self.count(),1)
        self.world.reactions.clear(); self.restart()
        for _ in range(2):
            with self.assertRaises(base.FGS.GroupSyncError): self.adapter.catch_up()
        self.assertEqual(self.mutations,['create']); self.assertEqual(self.row(self.reaction)['status'],'unknown')
        self.assertEqual(self.count(),1)

    def test_unknown_post_exact_receipt_readback_then_withdrawal(self):
        self.assembly(); deletion=self.deletion(); self.capture(self.reaction,deletion)
        self.disappear(self.reaction,deletion); self.lose='create'
        with self.assertRaises(base.FGS.GroupSyncError): self.adapter.catch_up()
        self.restart(); self.catch_up_due()
        self.assertEqual(self.mutations,['create','delete']); self.assertEqual(self.count(),0)

    def test_unknown_delete_readback_after_restart_does_not_repeat_delete(self):
        self.assembly(); deletion=self.deletion(); self.capture(self.reaction,deletion)
        self.disappear(self.reaction,deletion); self.lose='delete'
        with self.assertRaises(base.FGS.GroupSyncError): self.adapter.catch_up()
        self.assertEqual(self.count(),1); self.restart(); self.catch_up_due()
        self.assertEqual(self.mutations,['create','delete']); self.assertEqual(self.count(),0)

    def test_crash_after_receipt_before_cleanup_does_not_repeat_post(self):
        self.assembly(); self.capture(self.reaction); self.disappear(self.reaction)
        with mock.patch.object(inbox,'forget',side_effect=store.StoreError(store.ERROR)):
            with self.assertRaises(store.StoreError): self.adapter.catch_up()
        self.assertEqual(self.row(self.reaction)['status'],'acked'); self.assertEqual(self.count(),1)
        self.restart(); self.catch_up_due()
        self.assertEqual(self.mutations,['create']); self.assertEqual(self.count(),0)

    def test_current_grant_or_binding_revocation_blocks_retained_reaction(self):
        self.assembly(); self.capture(self.reaction); self.disappear(self.reaction)
        db=self.run.mapping_store
        for table,where in [('agent_chat','agent_id'),('agent','pubkey')]:
            db.conn.execute(f"UPDATE {table} SET status='paused' WHERE {where}=?",(base.AGENT2_PK,))
            with self.assertRaises(base.FGS.GroupSyncError): self.adapter.catch_up()
            self.assertEqual(self.mutations,[]); self.assertEqual(self.count(),1)
            db.conn.execute(f"UPDATE {table} SET status='active' WHERE {where}=?",(base.AGENT2_PK,))
        db.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='test'")
        with self.assertRaises(base.FGS.GroupSyncError): self.adapter.catch_up()
        self.assertEqual(self.mutations,[]); self.assertEqual(self.count(),1)

    def test_fresh_signed_policy_revocation_blocks_without_evicting(self):
        self.assembly(); self.capture(self.reaction); self.disappear(self.reaction)
        revoke=base.FGS.sign_event(base.OWNER_KEY,30177,[['d',base.AGENT2_PK]],'{}',self.run.now_ts+1)
        self.world.relay_events.append(revoke)
        with self.assertRaises(base.FGS.GroupSyncError): self.adapter.catch_up()
        self.assertEqual(self.mutations,[]); self.assertEqual(self.count(),1)

    def test_reject_wrong_author_channel_unsigned_and_arbitrary_payload(self):
        self.assembly()
        cases=[base.FGS.sign_event(base.OWNER_KEY,7,[['h',base.CHANNEL],['e',self.root_event['id']]],'✅',self.run.now_ts),
            base.FGS.sign_event(base.AGENT2_KEY,7,[['h','foreign'],['e',self.root_event['id']]],'✅',self.run.now_ts),
            {**self.reaction,'sig':'0'*128},
            base.FGS.sign_event(base.AGENT2_KEY,7,[['e',self.root_event['id']]],'secret body',self.run.now_ts),
            base.FGS.sign_event(base.AGENT2_KEY,5,[['e',self.reaction['id']]],'private reason',self.run.now_ts),
            base.FGS.sign_event(base.AGENT2_KEY,7,[['e',self.root_event['id']],['auth','secret']], '✅',self.run.now_ts),
            base.FGS.sign_event(base.AGENT2_KEY,7,[['e',self.root_event['id']],['e',self.root_event['id']]],'✅',self.run.now_ts)]
        self.capture(*cases); self.assertEqual(self.count(),0)
        self.capture({**self.reaction,'authorization':'SECRET_CREDENTIAL','body':'SECRET_BODY'})
        encoded=self.run.mapping_store.conn.execute('SELECT event_json FROM reaction_inbox').fetchone()[0]
        self.assertNotIn('SECRET',encoded); self.assertEqual(set(json.loads(encoded)),inbox.FIELDS)

    def test_no_h_cross_channel_target_cannot_authorize_native_effect(self):
        self.assembly(); self.disappear(self.reaction)
        other=base.FGS.sign_event(base.AGENT2_KEY,9,[['h','foreign']],'not retained',self.run.now_ts)
        self.world.events.append(other)
        event=base.FGS.sign_event(base.AGENT2_KEY,7,[['e',other['id']]],'✅',self.run.now_ts)
        self.capture(event)
        self.catch_up_due()
        self.assertEqual(self.mutations,[]); self.assertEqual(self.count(),0)

    def foreign_pair(self):
        original=base.FGS.sign_event(base.AGENT2_KEY,9,[['h','foreign']],'not retained',self.run.now_ts-2)
        self.world.events.append(original)
        reaction=base.FGS.sign_event(base.AGENT2_KEY,7,[['e',original['id']]],'👀',self.run.now_ts-1)
        deletion=base.FGS.sign_event(base.AGENT2_KEY,5,[['e',reaction['id']]],'',self.run.now_ts)
        return original,reaction,deletion

    def test_foreign_hint_cannot_starve_later_local_reaction_or_withdrawal(self):
        self.assembly(); original,foreign,withdrawal=self.foreign_pair()
        self.capture(foreign,withdrawal,self.reaction); self.disappear(self.reaction)
        self.catch_up_due(); self.catch_up_due()
        self.assertEqual(self.mutations,['create']); self.assertEqual(self.count(),0)
        self.assertIsNone(self.row(foreign)); self.assertIsNone(self.row(withdrawal))

    def test_unknown_hint_does_not_starve_local_and_remains_durable(self):
        self.assembly()
        unknown=base.FGS.sign_event(base.AGENT2_KEY,7,[['e','a'*64]],'👀',self.run.now_ts-1)
        self.capture(unknown,self.reaction); self.disappear(self.reaction)
        self.catch_up_due(); self.restart(); self.catch_up_due()
        self.assertEqual(self.mutations,['create']); self.assertEqual(self.count(),1)
        self.assertIsNone(self.row(unknown))

    def test_capture_rejects_event_already_older_than_canonical_floor(self):
        self.assembly(); self.disappear(self.reaction)
        self.run.state.floor=self.run.state.buzz_floor=self.run.now_ts-30; self.run.persist()
        self.adapter.initial_since=max(self.run.state.floor,self.run.state.buzz_floor)
        old=base.FGS.sign_event(base.AGENT2_KEY,7,[['h',base.CHANNEL],['e',self.root_event['id']]],'✅',self.run.now_ts-60)
        self.capture(old); self.catch_up_due()
        self.assertEqual(self.count(),0); self.assertEqual(self.mutations,[])

    def test_capture_without_initialized_baseline_does_not_grant_replay_exception(self):
        self.assembly()
        self.run.mapping_store.conn.execute("DELETE FROM state_scalar WHERE binding_id='test' AND field='buzz_floor'")
        self.capture(self.reaction); self.assertEqual(self.count(),0)

    def test_existing_unknown_before_capture_floor_retains_own_receipt_recovery(self):
        self.assembly(); self.lose='create'
        with self.assertRaises(base.FGS.GroupSyncError): self.adapter.deliver(self.reaction)
        self.run.state.floor=self.run.state.buzz_floor=self.run.now_ts+30; self.run.persist()
        self.capture(self.reaction); self.disappear(self.reaction); self.restart()
        self.adapter.initial_since=max(self.run.state.floor,self.run.state.buzz_floor)
        self.catch_up_due()
        self.assertEqual(self.mutations,['create']); self.assertEqual(self.count(),0)

    def test_late_foreign_withdrawal_uses_only_exact_negative_author_target(self):
        self.assembly(); original,foreign,withdrawal=self.foreign_pair()
        self.capture(foreign); self.disappear(self.reaction); self.catch_up_due(); self.restart()
        self.assertEqual(self.count(),0)
        self.capture(withdrawal); self.assertEqual(self.count(),0)
        other=base.FGS.sign_event(base.AGENT2_KEY,5,[['e','a'*64]],'',self.run.now_ts)
        self.capture(other); self.assertEqual(self.count(),1)
        wrong_author=base.FGS.sign_event(base.OWNER_KEY,5,[['e',foreign['id']]],'',self.run.now_ts)
        self.assertFalse(inbox.foreign_reference(self.run.mapping_store,'test',base.CHANNEL,base.OWNER_PK,wrong_author))
        self.catch_up_due(); self.assertEqual(self.count(),1); self.assertEqual(self.mutations,[])

    def test_unknown_withdrawal_before_create_survives_restart_and_later_target(self):
        self.assembly(); deletion=self.deletion(); self.capture(deletion); self.disappear(self.reaction,deletion)
        self.catch_up_due(); self.restart(); self.assertEqual(self.count(),1)
        self.capture(self.reaction); self.catch_up_due()
        self.assertEqual(self.mutations,['create','delete']); self.assertEqual(self.count(),0)

    def test_untrusted_or_incomplete_foreign_proof_preserves_unknown_and_progresses(self):
        self.assembly(); original,foreign,_=self.foreign_pair()
        self.capture(foreign,self.reaction); self.disappear(self.reaction)
        transport=self.adapter.http
        def incomplete(url,headers,timeout,**kwargs):
            filters=json.loads(kwargs.get('body',b'[]'))
            if filters==[{'kinds':[9],'ids':[original['id']],'limit':2}]:
                return 200,json.dumps([original,original]).encode()
            return transport(url,headers,timeout,**kwargs)
        self.adapter.http=incomplete
        self.catch_up_due()
        self.assertEqual(self.mutations,['create']); self.assertEqual(self.count(),1)
        self.assertEqual(self.run.mapping_store.conn.execute('SELECT count(*) FROM reaction_foreign').fetchone()[0],0)
        self.assertFalse(inbox.reject_foreign(self.run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,
            foreign,{**original,'sig':'0'*128},now=self.run.now_ts))
        self.assertFalse(inbox.reject_foreign(self.run.mapping_store,'test',base.CHANNEL,base.AGENT2_PK,
            foreign,self.root_event,now=self.run.now_ts))

    def test_negative_metadata_eviction_never_evicts_pending_or_creates_native_rights(self):
        self.assembly(); original,foreign,_=self.foreign_pair(); db=self.run.mapping_store
        self.capture(self.reaction)
        with db.transaction():
            for n in range(256):
                db.conn.execute('INSERT INTO reaction_foreign VALUES(?,?,?,?,?,?,?)',
                    ('test',base.AGENT2_PK,f'{n:064x}',base.CHANNEL,'a'*64,'foreign',0))
        self.assertTrue(inbox.reject_foreign(db,'test',base.CHANNEL,base.AGENT2_PK,foreign,original,now=self.run.now_ts))
        self.assertEqual(db.conn.execute('SELECT count(*) FROM reaction_foreign').fetchone()[0],256)
        self.assertEqual(self.count(),1); self.assertEqual(self.mutations,[])
        self.assertIsNone(self.row(foreign))
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute("UPDATE reaction_foreign SET foreign_channel='other'")

    def test_ambiguous_signed_target_is_not_a_foreign_proof(self):
        self.assembly(); self.disappear(self.reaction)
        original=base.FGS.sign_event(base.AGENT2_KEY,9,[['h','foreign'],['h','other']],'not retained',self.run.now_ts-2)
        self.world.events.append(original)
        event=base.FGS.sign_event(base.AGENT2_KEY,7,[['e',original['id']]],'👀',self.run.now_ts-1)
        self.capture(event,self.reaction); self.catch_up_due()
        self.assertEqual(self.mutations,['create']); self.assertEqual(self.count(),1)
        self.assertEqual(self.run.mapping_store.conn.execute('SELECT count(*) FROM reaction_foreign').fetchone()[0],0)

    def test_global_negative_bound_evicts_only_old_negative_metadata(self):
        self.assembly(); original,foreign,_=self.foreign_pair(); db=self.run.mapping_store
        self.capture(self.reaction)
        with db.transaction():
            for n in range(8192):
                db.conn.execute('INSERT INTO reaction_foreign VALUES(?,?,?,?,?,?,?)',
                    ('test',f'{n//256:064x}',f'{n:064x}',base.CHANNEL,'a'*64,'foreign',0))
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute('INSERT INTO reaction_foreign VALUES(?,?,?,?,?,?,?)',
                ('test',base.AGENT2_PK,foreign['id'],base.CHANNEL,original['id'],'foreign',1))
        self.assertTrue(inbox.reject_foreign(db,'test',base.CHANNEL,base.AGENT2_PK,foreign,original,now=self.run.now_ts))
        self.assertEqual(db.conn.execute('SELECT count(*) FROM reaction_foreign').fetchone()[0],8192)
        self.assertEqual(self.count(),1); self.assertEqual(self.mutations,[])
        self.assertIsNone(self.row(foreign))

    def test_no_h_cross_author_withdrawal_does_not_delete_existing_reaction(self):
        self.assembly(); self.adapter.deliver(self.reaction); self.disappear(self.reaction)
        event=base.FGS.sign_event(base.OWNER_KEY,5,[['e',self.reaction['id']]],'',self.run.now_ts)
        self.capture(event); self.catch_up_due()
        self.assertEqual(self.mutations,['create']); self.assertEqual(self.count(),0)

    def test_sql_capacity_duplicate_immutability_and_corrupt_readback(self):
        self.assembly(); db=self.run.mapping_store
        events=[base.FGS.sign_event(base.AGENT2_KEY,7,[['e',self.root_event['id']]],'✅',self.run.now_ts+i) for i in range(257)]
        with db.transaction():
            for event in events[:256]: self.assertTrue(inbox.retain(db,'test',base.CHANNEL,base.AGENT2_PK,event))
        self.assertTrue(inbox.retain(db,'test',base.CHANNEL,base.AGENT2_PK,events[0]))
        with self.assertRaises(store.StoreError): inbox.retain(db,'test',base.CHANNEL,base.AGENT2_PK,events[256])
        self.assertEqual(self.count(),256)
        with self.assertRaises(sqlite3.IntegrityError): db.conn.execute("UPDATE reaction_inbox SET event_json='{}'")
        db.conn.execute('DELETE FROM reaction_inbox')
        db.conn.execute('INSERT INTO reaction_inbox VALUES(?,?,?,?,?,?)',('test',base.AGENT2_PK,self.reaction['id'],base.CHANNEL,'{}',0))
        with self.assertRaises(store.StoreError): self.adapter.catch_up()
        self.assertEqual(self.mutations,[])

    def test_retained_reaction_survives_restart_floor_beyond_event_timestamp(self):
        self.assembly(); self.capture(self.reaction); self.disappear(self.reaction)
        self.run.state.floor=self.run.state.buzz_floor=self.run.now_ts+3600; self.run.persist()
        self.restart(); self.adapter.initial_since=max(self.run.state.floor,self.run.state.buzz_floor)
        self.catch_up_due()
        self.assertEqual(self.mutations,['create']); self.assertEqual(self.count(),0)

    def test_owned_feed_callback_uses_same_durable_capture(self):
        self.assembly(); host=self.host()
        spec=SimpleNamespace(env_file=self.tmp/'outlet.env')
        host.workers['test'].outlet_responsibilities=set()
        host.onboarding=SimpleNamespace(catalog=SimpleNamespace(records=[]),
            effects=SimpleNamespace(specs={base.AGENT2_PK:spec}),relay=SimpleNamespace(owner=base.OWNER_PK))
        host.onboarding_config=SimpleNamespace(trusted_relays={'https://relay.test'})
        host.outlet_tasks={};host.outlet_status={}
        async def follow(identity,env,channel,on_event,on_status,**kwargs):
            self.assertEqual(kwargs['author'],base.AGENT2_PK)
            await on_event(identity,self.reaction)
        async def drive():
            host._spawn=lambda kind,identity,factory: asyncio.create_task(factory())
            with mock.patch('hostd.__main__.relay_feed.follow',side_effect=follow) as feed:
                await host.refresh_outlets('test')
                await asyncio.gather(*host.outlet_tasks.values())
                self.assertEqual(feed.call_count,1)
        asyncio.run(drive())
        self.assertEqual(self.count(),1)
        self.disappear(self.reaction); self.catch_up_due()
        self.assertEqual(self.mutations,['create'])

    def test_storage_failure_does_not_acknowledge_capture_or_hide_error(self):
        self.assembly(); host=self.host(); host.mark=mock.Mock()
        with mock.patch('hostd.reaction_inbox.retain',side_effect=store.StoreError(store.ERROR)):
            with self.assertRaises(store.StoreError): asyncio.run(host.on_relay('test',self.reaction))
        host.mark.assert_not_called(); self.assertEqual(self.count(),0)

    def test_global_capacity_and_size_are_enforced_by_sql(self):
        self.assembly(); db=self.run.mapping_store
        with db.transaction():
            for n in range(8192):
                db.conn.execute('INSERT INTO reaction_inbox VALUES(?,?,?,?,?,?)',
                    ('test',f'{n//256:064x}',f'{n:064x}',base.CHANNEL,'{}',0))
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute('INSERT INTO reaction_inbox VALUES(?,?,?,?,?,?)',
                ('test',base.AGENT2_PK,self.reaction['id'],base.CHANNEL,'{}',0))
        self.assertEqual(self.count(),8192)
        db.conn.execute('DELETE FROM reaction_inbox')
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute('INSERT INTO reaction_inbox VALUES(?,?,?,?,?,?)',
                ('test',base.AGENT2_PK,self.reaction['id'],base.CHANNEL,'x'*2049,0))

    def test_schema13_additive_migration_retains_original_rows(self):
        self.assembly(); db=self.run.mapping_store
        before=[tuple(row) for row in db.conn.execute('SELECT * FROM delivery ORDER BY id')]
        path=db.path; db.close()
        raw=sqlite3.connect(path)
        raw.execute('DROP TABLE join_adoption');raw.execute('DROP TABLE feishu_scan_token');raw.execute('DROP TABLE feishu_scan');raw.execute('DROP TABLE feishu_ingest');raw.execute('DROP TABLE outlet_work_turn');raw.execute('DROP TABLE outlet_work');raw.execute('DROP TABLE outlet_pending'); raw.execute('DROP TABLE console_pause'); raw.execute('DROP TABLE reaction_foreign'); raw.execute('DROP TABLE reaction_inbox'); raw.execute('PRAGMA user_version=13'); raw.commit(); raw.close()
        db=store.Store(path); self.addCleanup(db.close); self.run.mapping_store=db
        self.assertEqual(db.conn.execute('PRAGMA user_version').fetchone()[0],store.SCHEMA_VERSION)
        self.assertEqual([tuple(row) for row in db.conn.execute('SELECT * FROM delivery ORDER BY id')],before)
        self.assertEqual(self.count(),0)

if __name__ == '__main__': unittest.main()
