"""Actual Worker membership/intro effects with protected files and signed directory."""
import json
import asyncio
from types import SimpleNamespace
import sys
from pathlib import Path
import unittest

TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS), str(TESTS.parent / 'scripts'), str(TESTS.parent / 'scripts' / 'hostd')]
import test_hostd_worker_outlets as outlets
from state_store import StateAdapter
from store import Store
base = outlets.base
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule


class BotApprovalTests(base.TmpCase):
    assembly = outlets.WorkerOutlets.assembly

    def invite(self, *, configured=True, channel_allowed=False):
        world, run, worker, spec = self.assembly()
        cfg = json.loads(worker.config.read_text())
        if not configured:
            cfg['agents'].pop(base.AGENT2_PK)
            base.write_owner_only(worker.config, json.dumps(cfg))
        if not channel_allowed:
            raw = spec.env_file.read_text().replace('BUZZ_ACP_CHANNELS=' + base.CHANNEL, 'BUZZ_ACP_CHANNELS=')
            base.write_owner_only(spec.env_file, raw)
        world.members = [m for m in world.members if m['pubkey'] != base.AGENT2_PK]
        world.bots.pop(outlets.own.APP, None)
        world.bot_members.pop(outlets.own.APP, None)
        worker.run({'members'})
        world.bots[outlets.own.APP] = world.bot_members[outlets.own.APP] = outlets.own.MEMBER
        world.relay_writes.clear(); world.bot_calls.clear()
        return world, run, worker, spec

    def adds(self, world):
        return [e for e in world.relay_writes if e['kind'] == 9000 and ['p', base.AGENT2_PK] in e['tags']]

    def state(self, run):
        return StateAdapter(run.mapping_store, 'test', self.tmp / 'state').load()

    def grant(self, run, *, status='active', app=None, binding='test'):
        store = run.mapping_store
        store.register_agent(base.AGENT2_PK, owner_pubkey=base.OWNER_PK,
                             app_id=app or outlets.own.APP, now=int(base.NOW.timestamp()))
        store.record_agent_chat(base.AGENT2_PK, base.CHAT, base.FGS.chat_ref(base.CHAT),
                                binding_id=binding, status=status, now=int(base.NOW.timestamp()))

    def test_invited_own_bot_without_approval_or_allowlist_cannot_join_or_intro(self):
        world, run, worker, _ = self.invite()
        for _ in range(2):
            report = worker.run({'members'})
            self.assertEqual(self.adds(world), [])
            self.assertEqual(report['agent_intros_sent'], 0)
            self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)
            self.assertNotIn('b:' + outlets.own.APP, self.state(run).feishu_seen)
            self.assertIn(outlets.own.APP, world.bots)
        self.assertEqual(run.mapping_store.conn.execute('SELECT count(*) FROM agent_chat').fetchone()[0], 0)

    def test_exact_protected_legacy_agent_remains_authorized_without_new_approval(self):
        world, run, worker, _ = self.invite(channel_allowed=True)
        report = worker.run({'members'})
        self.assertEqual(len(self.adds(world)), 1)
        self.assertEqual(report['agent_intros_sent'], 1)
        worker.run({'members'})
        self.assertEqual(len(self.adds(world)), 1)
        self.assertTrue(self.state(run).agent_intros[base.AGENT2_PK].startswith('om_'))
        self.assertEqual(run.mapping_store.conn.execute('SELECT count(*) FROM join_request').fetchone()[0], 0)

    def test_dynamic_bot_needs_active_exact_grant_even_after_env_changed(self):
        world, run, worker, _ = self.invite(configured=False, channel_allowed=True)
        worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        self.grant(run)
        self.approve(run, worker, done=True)
        report = worker.run({'members'})
        self.assertEqual(len(self.adds(world)), 1)
        self.assertEqual(report['agent_intros_sent'], 1)

    def test_poisoned_public_roster_is_not_an_intro_or_legacy_grant(self):
        world, run, worker, _ = self.invite(configured=False)
        world.members.append({'pubkey': base.AGENT2_PK, 'role': 'bot'})
        report = worker.run({'members'})
        self.assertEqual(report['agent_intros_sent'], 0)
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)
        self.assertEqual(self.adds(world), [])
        self.assertIn(outlets.own.APP, world.bots)

    def test_paused_grant_overrides_old_config_and_allowlist(self):
        world, run, worker, _ = self.invite(channel_allowed=True)
        self.grant(run, status='paused')
        worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)

    def test_changed_profile_identity_fails_closed(self):
        world, run, worker, spec = self.invite(channel_allowed=True)
        self.grant(run)
        base.write_owner_only(Path(spec.lark_config_dir) / 'config.json', json.dumps({'apps': [{'appId': 'cli_changed'}]}))
        with self.assertRaises(base.FGS.GroupSyncError):
            worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)

    def test_revoked_env_after_grant_does_not_repair_membership(self):
        world, run, worker, _ = self.invite(channel_allowed=False)
        self.grant(run)
        worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)

    def test_requested_dynamic_join_cannot_borrow_public_role_or_env(self):
        world, run, worker, _ = self.invite(configured=False, channel_allowed=True)
        store = run.mapping_store
        store.register_agent(base.AGENT2_PK, owner_pubkey=base.OWNER_PK,
                             app_id=outlets.own.APP, now=int(base.NOW.timestamp()))
        store.create_join('JOIN-aabbccdd', base.AGENT2_PK, base.OWNER_PK, outlets.own.APP,
                          base.CHAT, kind='channel', binding_id='test', now=int(base.NOW.timestamp()))
        worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)

    def test_signed_public_policy_app_change_rejects_membership_and_intro(self):
        world, run, worker, _ = self.invite(channel_allowed=True)
        self.grant(run)
        world.relay_events = [e for e in world.relay_events if not (e['kind'] == 30177 and ['d', base.AGENT2_PK] in e['tags'])]
        world.relay_events.append(base.FGS.sign_event(base.OWNER_KEY, 30177, [['d', base.AGENT2_PK]],
            json.dumps({'feishu': {'app_id': 'cli_changed'}}), int(base.NOW.timestamp()) - 1))
        worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)

    def test_paused_grant_during_public_read_is_rechecked_before_effect(self):
        world, run, worker, _ = self.invite(channel_allowed=True)
        self.grant(run)
        original = run.clients.http
        changed = []
        def http(url, headers, timeout, **kwargs):
            result = original(url, headers, timeout, **kwargs)
            filters = json.loads(kwargs.get('body', b'null'))
            if url.endswith('/query') and isinstance(filters, list) and any(
                    f.get('kinds') == [30177] and f.get('#d') == [base.AGENT2_PK] and f.get('limit') == 2 for f in filters):
                run.mapping_store.conn.execute("UPDATE agent_chat SET status='paused' WHERE agent_id=?", (base.AGENT2_PK,))
                changed.append(True)
            return result
        run.clients.http = http
        worker.run({'members'})
        self.assertTrue(changed)
        self.assertEqual(self.adds(world), [])
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)

    def test_unknown_member_event_is_retained_without_replay_after_revocation(self):
        world, run, worker, spec = self.invite(channel_allowed=True)
        original = run.clients.http
        attempts = []
        def http(url, headers, timeout, **kwargs):
            event = json.loads(kwargs.get('body', b'null'))
            if url.endswith('/events') and isinstance(event, dict) and event.get('kind') == 9000:
                attempts.append(event)
                raise OSError('fixture unknown response')
            return original(url, headers, timeout, **kwargs)
        run.clients.http = http
        worker.run({'members'})
        before = self.state(run).member_events
        self.assertEqual(len(before), 1)
        self.assertEqual(list(before.values()), attempts)
        raw = spec.env_file.read_text().replace('BUZZ_ACP_CHANNELS=' + base.CHANNEL, 'BUZZ_ACP_CHANNELS=')
        base.write_owner_only(spec.env_file, raw)
        worker.run({'members'})
        self.assertEqual(self.state(run).member_events, before)
        self.assertEqual(len(attempts), 1)
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)

    def test_paused_binding_rejects_bot_effect_even_with_grant(self):
        world, run, worker, _ = self.invite(channel_allowed=True)
        self.grant(run)
        run.mapping_store.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='test'")
        worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)

    def test_revoked_and_mismatched_grants_never_fall_back_to_legacy(self):
        world, run, worker, _ = self.invite(channel_allowed=True)
        self.grant(run)
        db = run.mapping_store.conn
        for status in ('paused', 'waiting_receipt', 'degraded', 'retired'):
            with self.subTest(status=status):
                db.execute('UPDATE agent_chat SET status=? WHERE agent_id=?', (status, base.AGENT2_PK))
                worker.run({'members'})
                self.assertEqual(self.adds(world), [])
        db.execute("UPDATE agent_chat SET status='active',chat_ref=? WHERE agent_id=?", (base.FGS.chat_ref('oc_other'), base.AGENT2_PK))
        worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        db.execute('UPDATE agent_chat SET chat_ref=? WHERE agent_id=?', (base.FGS.chat_ref(base.CHAT), base.AGENT2_PK))
        db.execute("UPDATE agent SET status='paused' WHERE pubkey=?", (base.AGENT2_PK,))
        worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)

    def test_directory_identity_without_local_spec_does_not_delegate_owner(self):
        world, run, worker, _ = self.invite(configured=False, channel_allowed=True)
        worker.outlet_specs = {}
        worker.run({'members'})
        self.assertEqual(self.adds(world), [])
        self.assertNotIn(base.AGENT2_PK, self.state(run).agent_intros)
        self.assertIn(outlets.own.APP, world.bots)

    def approve(self, run, worker, *, done=False):
        store = run.mapping_store
        now = int(base.NOW.timestamp())
        store.create_join('JOIN-aabbccdd', base.AGENT2_PK, base.OWNER_PK, outlets.own.APP,
                          base.CHAT, kind='channel', binding_id='test', now=now)
        generation = store.rotate_card('JOIN-aabbccdd', 'om_approval', now=now)
        self.assertTrue(store.decide_join('JOIN-aabbccdd', 'evt_approved', base.OWNER_PK,
            outlets.own.APP, 'om_approval', generation, approved=True, now=now))
        store.ensure_effect_plan('JOIN-aabbccdd', base.CHANNEL, 'test', worker.outlet_specs[base.AGENT2_PK].env_file, worker.config, now=now)
        if done:
            store.advance_join('JOIN-aabbccdd', expected='approved', target='applied', now=now)
            store.advance_join('JOIN-aabbccdd', expected='applied', target='done', now=now)

    def buzz(self, world, worker):
        world.members.append({'pubkey': base.AGENT2_PK, 'role': 'bot'})
        worker.cached = {}; worker.setup_at = 0; worker.outlet_rescan = True
        return worker.run({'buzz'})

    def test_paused_grant_survives_actual_buzz_outlet_and_followup_members(self):
        world, run, worker, _ = self.invite(channel_allowed=True)
        self.grant(run, status='paused')
        self.buzz(world, worker)
        self.assertEqual(run.mapping_store.conn.execute('SELECT status FROM agent_chat WHERE agent_id=?', (base.AGENT2_PK,)).fetchone()[0], 'paused')
        report = worker.run({'members'})
        self.assertEqual(report['agent_intros_sent'], 0)

    def test_requested_dynamic_join_poisoned_roster_cannot_mint_grant(self):
        world, run, worker, _ = self.invite(configured=False, channel_allowed=True)
        store = run.mapping_store
        store.register_agent(base.AGENT2_PK, owner_pubkey=base.OWNER_PK, app_id=outlets.own.APP, now=int(base.NOW.timestamp()))
        store.create_join('JOIN-aabbccdd', base.AGENT2_PK, base.OWNER_PK, outlets.own.APP,
                          base.CHAT, kind='channel', binding_id='test', now=int(base.NOW.timestamp()))
        self.buzz(world, worker)
        self.assertIsNone(store.conn.execute('SELECT * FROM agent_chat WHERE agent_id=?', (base.AGENT2_PK,)).fetchone())
        self.assertEqual(worker.run({'members'})['agent_intros_sent'], 0)

    def test_orphan_active_grant_cannot_authorize_dynamic_bot(self):
        world, run, worker, _ = self.invite(configured=False, channel_allowed=True)
        self.grant(run)
        self.buzz(world, worker)
        self.assertEqual(worker.run({'members'})['agent_intros_sent'], 0)
        self.assertEqual(self.adds(world), [])

    def test_approved_dynamic_outlet_can_prove_readiness_without_creating_grant(self):
        world, run, worker, _ = self.invite(configured=False, channel_allowed=True)
        store = run.mapping_store
        store.register_agent(base.AGENT2_PK, owner_pubkey=base.OWNER_PK, app_id=outlets.own.APP, now=int(base.NOW.timestamp()))
        self.approve(run, worker)
        report = self.buzz(world, worker)
        self.assertEqual(report['hostd']['outlet_results'][base.AGENT2_PK]['status'], 'verified')
        self.assertIsNone(store.conn.execute('SELECT * FROM agent_chat WHERE agent_id=?', (base.AGENT2_PK,)).fetchone())
        self.assertEqual(worker.run({'members'})['agent_intros_sent'], 0)
        self.grant(run)
        store.advance_join('JOIN-aabbccdd', expected='approved', target='applied', now=int(base.NOW.timestamp()))
        store.advance_join('JOIN-aabbccdd', expected='applied', target='done', now=int(base.NOW.timestamp()))
        self.assertEqual(worker.run({'members'})['agent_intros_sent'], 1)

    def test_reconnect_requested_join_does_not_revoke_explicit_legacy_authority(self):
        world, run, worker, _ = self.invite(channel_allowed=True)
        store = run.mapping_store
        store.register_agent(base.AGENT2_PK, owner_pubkey=base.OWNER_PK, app_id=outlets.own.APP, now=int(base.NOW.timestamp()))
        store.create_join('JOIN-aabbccdd', base.AGENT2_PK, base.OWNER_PK, outlets.own.APP,
                          base.CHAT, kind='channel', binding_id='test', now=int(base.NOW.timestamp()))
        self.assertEqual(worker.run({'members'})['agent_intros_sent'], 1)
        self.assertEqual(store.join_request('JOIN-aabbccdd')['status'], 'requested')
        self.assertIsNone(store.conn.execute('SELECT * FROM agent_chat WHERE agent_id=?', (base.AGENT2_PK,)).fetchone())

    def test_done_without_grant_cannot_restart_outlet_from_public_role(self):
        world, run, worker, _ = self.invite(configured=False, channel_allowed=True)
        run.mapping_store.register_agent(base.AGENT2_PK, owner_pubkey=base.OWNER_PK, app_id=outlets.own.APP, now=int(base.NOW.timestamp()))
        self.approve(run, worker, done=True)
        report = self.buzz(world, worker)
        self.assertEqual(report['hostd']['outlet_results'][base.AGENT2_PK]['status'], 'pending')
        self.assertIsNone(run.mapping_store.conn.execute('SELECT * FROM agent_chat WHERE agent_id=?', (base.AGENT2_PK,)).fetchone())

    def test_actual_worker_registrar_and_join_readback_complete_before_dynamic_delivery(self):
        from hostd import join_effects as effects
        from hostd.runtime_registration import RuntimeRegistrar
        import registry
        world, run, worker, ownspec = self.invite(configured=False, channel_allowed=True)
        store = run.mapping_store; now = int(base.NOW.timestamp())
        store.register_agent(base.AGENT2_PK, owner_pubkey=base.OWNER_PK, app_id=outlets.own.APP, now=now)
        self.approve(run, worker)
        store.conn.execute("UPDATE binding SET status='active' WHERE binding_id='test'")
        prompt=self.tmp/'approved-prompt'; responsible=self.tmp/'approved-responsible'; timer=self.tmp/'old-join.json'
        base.write_owner_only(prompt, effects.legacy.PROMPT_BEGIN+'\n'+base.CHANNEL+'\n'+effects.legacy.PROMPT_END)
        base.write_owner_only(responsible,json.dumps({'channels':[base.CHANNEL]}))
        base.write_owner_only(timer,json.dumps({'version':1,'owner_pubkey':base.OWNER_PK,'agents':[]}))
        base.write_owner_only(ownspec.env_file,ownspec.env_file.read_text()+f'BUZZ_ACP_SYSTEM_PROMPT_FILE={prompt}\nBUZZ_RESPONSIBLE_CONFIG={responsible}\n')
        spec=effects.AgentSpec(base.AGENT2_PK,base.OWNER_PK,outlets.own.APP,str(ownspec.env_file),str(prompt),str(responsible),'fixture-agent.service',str(timer),str(self.tmp/'timer-state'))
        event=outlets.own.OwnOutlet.event(self)
        world.events.append(event);world.relay_events.append(event)
        report=self.buzz(world,worker);worker.last=report
        self.assertEqual(report['hostd']['outlet_results'][base.AGENT2_PK]['status'],'verified')
        self.assertIsNone(store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK))
        self.assertFalse([args for args,_ in world.bot_calls if '--content' in args])
        self.assertIsNone(store.conn.execute('SELECT * FROM agent_chat WHERE agent_id=?',(base.AGENT2_PK,)).fetchone())
        outer=self
        class Ops:
            def unit_status(self,unit):return 'loaded','active'
            def process(self,unit):return 123,456,effects.legacy.parse_env(ownspec.env_file.read_text())
            def invocation_id(self,unit):return 'a'*32
            def journal_invocation(self,unit,pid,invocation):return 'subscribed to channel '+base.CHANNEL
        class Relay:
            owner=base.OWNER_PK
            def owner_of(self,pub):
                profile=next(e for e in world.relay_events if e['kind']==0 and e['pubkey']==pub)
                outer.assertTrue(base.FGS._nip01_event_verified(profile))
                return effects.authority.attested_owner(profile,pub)
            def members(self,channel):
                outer.assertEqual(channel,base.CHANNEL)
                return {m['pubkey']:m['role'] for m in world.members}
        async def readback():
            task=asyncio.current_task();reg=registry.Registry();cfg=json.loads(worker.config.read_text())
            app=cfg['agents'][cfg['desk_pubkey']]
            reg.bindings['test']=registry.Binding('test',worker.config,self.tmp/'state',base.CHANNEL,base.CHAT,
                app['app_id'],app['lark_config_dir'],app['lark_data_dir'],'https://relay.test')
            host=SimpleNamespace(runtime_store=store,onboarding=SimpleNamespace(records={outlets.own.APP:ownspec}),reg=reg,
                workers={'test':worker},status={'apps':{identity:{'feishu':'connected'} for identity in (outlets.own.APP,app['app_id'])},
                'bindings':{'test':{'runs':1,'relay':'connected'}}},app_tasks={identity:task for identity in (outlets.own.APP,app['app_id'])},
                binding_tasks={'test':{'worker':task,'relay':task}},outlet_tasks={('test',base.AGENT2_PK):task},
                outlet_status={('test',base.AGENT2_PK):'connected'},_binding_state=lambda name:'active')
            registrar=RuntimeRegistrar(host,clock=lambda:now)
            adapter=effects.JoinEffects(store,{spec.pubkey:spec},Relay(),effects.AgentRuntime(Ops()),registrar,
                clients=run.clients.agents,clock=lambda:now)
            row=store.join_request('JOIN-aabbccdd')
            result=await adapter.readback(row)
            self.assertTrue(result.verified)
        asyncio.run(readback())
        self.assertEqual(store.conn.execute('SELECT status FROM agent_chat WHERE agent_id=?',(base.AGENT2_PK,)).fetchone()[0],'active')
        store.advance_join('JOIN-aabbccdd',expected='approved',target='applied',now=now)
        store.advance_join('JOIN-aabbccdd',expected='applied',target='done',now=now)
        worker.outlet_rescan=True;worker.run({'buzz'})
        self.assertEqual(store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK)['status'],'acked')


if __name__ == '__main__':
    unittest.main()
