"""Pre-effect approved JOIN adoption; isolated SQL/files and signed test relay."""
import json
import asyncio
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from dataclasses import asdict
from pathlib import Path

HOSTD = Path(__file__).resolve().parents[1] / 'scripts' / 'hostd'
sys.path.insert(0, str(HOSTD))
import store
import join_effects as effects


class AdoptionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'hostd.db'
        self.db = store.Store(self.path)
        self.addCleanup(self.db.close)
        self.agent, self.owner = 'a' * 64, 'b' * 64
        self.db.register_agent(self.agent, owner_pubkey=self.owner, app_id='cli_agent',
                               config_path='/protected/agent.env', now=10)
        self.db.create_join('JOIN-12345678', self.agent, self.owner, 'cli_agent', 'oc_group',
                            kind='new_binding', now=10)
        self.db.rotate_card('JOIN-12345678', 'om_card', now=11)
        self.assertTrue(self.db.decide_join('JOIN-12345678', 'event_approve', self.owner,
                         'cli_agent', 'om_card', 1, approved=True, now=12))
        self.row = self.db.join_request('JOIN-12345678')
        self.plan = self.db.ensure_effect_plan('JOIN-12345678',
            '11111111-1111-4111-8111-111111111111', 'JOIN-12345678',
            '/protected/unused/mirror.env', '/protected/unused/config.json', now=13)
        self.db.reconcile_bindings([store.BindingRecord('winner',
            '22222222-2222-4222-8222-222222222222', 'oc_group', 'cli_reader',
            '/protected/winner.json', '/protected/reader', '/protected/data',
            'c' * 64, '', 'active')], now=14)
        self.target = self.db.bindings()[0]
        self.decision = self.db.card_approval_decision(self.row['request_id'])
        self.assertIsNotNone(self.decision)

    def adopt(self):
        return self.db.adopt_join_binding(self.row, self.plan, self.target, self.decision,
             secret_ref='/protected/agent.env', proof_refs={'checked_at':20, 'events':['d' * 64],
             'files_hash':'e' * 64, 'config_hash':'f' * 64, 'preparation_hash':'c' * 64}, now=20)

    def test_atomic_adoption_preserves_approval_and_original_plan_across_restart(self):
        row, plan = self.adopt()
        self.assertEqual((row['kind'], row['binding_id']), ('channel', 'winner'))
        self.assertEqual(plan['channel_id'], self.target['channel_id'])
        self.assertEqual(plan['secret_ref'], '/protected/agent.env')
        self.assertEqual(self.db.card_approval_decision(row['request_id']), self.decision)
        audit = self.db.join_adoption(row['request_id'])
        self.assertEqual(json.loads(audit['original_request']), self.row)
        self.assertEqual(json.loads(audit['original_plan']), self.plan)
        self.assertEqual(self.db.effect_steps(row['request_id']), [])
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM agent_chat').fetchone()[0], 0)
        with store.Store(self.path) as reopened:
            self.assertEqual(reopened.join_adoption(row['request_id']), audit)
            self.assertEqual(reopened.effect_plan(row['request_id']), plan)
        with self.assertRaises(store.StoreError): self.adopt()

    def test_optional_legacy_agent_path_stays_null_but_wrong_path_or_identity_refuses(self):
        self.db.conn.execute('UPDATE agent SET config_path=?',('/protected/wrong.env',))
        with self.assertRaises(store.StoreError): self.adopt()
        self.db.conn.execute('UPDATE agent SET config_path=NULL')
        self.db.conn.execute('UPDATE agent SET app_id=?',('cli_wrong',))
        with self.assertRaises(store.StoreError): self.adopt()
        self.db.conn.execute('UPDATE agent SET app_id=?',('cli_agent',))
        self.adopt()
        self.assertIsNone(self.db.conn.execute('SELECT config_path FROM agent').fetchone()[0])
        self.assertEqual(self.db.effect_plan(self.row['request_id'])['secret_ref'],'/protected/agent.env')

    def test_partial_or_unknown_effect_is_never_adopted(self):
        self.db.reserve_effect_step(self.row['request_id'], 'channel', 'e' * 64, now=15)
        self.db.defer_effect_step(self.row['request_id'], 'channel', status='unknown', now=16)
        with self.assertRaises(store.StoreError): self.adopt()
        self.assertEqual(self.db.join_request(self.row['request_id']), self.row)
        self.assertEqual(self.db.effect_plan(self.row['request_id']), self.plan)

    def test_transaction_cancellation_rolls_back_audit_and_effective_route(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.db.transaction():
                self.adopt()
                raise KeyboardInterrupt
        self.assertIsNone(self.db.join_adoption(self.row['request_id']))
        self.assertEqual(self.db.join_request(self.row['request_id']), self.row)
        self.assertEqual(self.db.effect_plan(self.row['request_id']), self.plan)

    def test_audit_is_append_only_and_target_pause_or_changed_decision_cas_refuses(self):
        self.db.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='winner'")
        with self.assertRaises(store.StoreError): self.adopt()
        self.db.conn.execute("UPDATE binding SET status='active' WHERE binding_id='winner'")
        self.adopt()
        audit = self.db.join_adoption(self.row['request_id'])
        replacement = dict(audit, proof_hash='d'*64)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute('INSERT OR REPLACE INTO join_adoption VALUES(?,?,?,?,?,?,?,?)',tuple(replacement.values()))
        self.assertEqual(self.db.join_adoption(self.row['request_id']),audit)
        with self.assertRaises(sqlite3.IntegrityError): self.db.conn.execute('UPDATE join_adoption SET proof_hash=?', ('d'*64,))
        with self.assertRaises(sqlite3.IntegrityError): self.db.conn.execute('DELETE FROM join_adoption')

    def test_schema18_upgrade_preserves_decisions_plans_steps_and_business_rows(self):
        self.db.reserve_effect_step(self.row['request_id'], 'channel', 'e' * 64, now=15)
        self.db.defer_effect_step(self.row['request_id'], 'channel', status='unknown', now=16)
        tables = ('join_request','join_decision','effect_plan','effect_step','binding','agent','agent_chat')
        before = {table:[tuple(r) for r in self.db.conn.execute('SELECT * FROM '+table)] for table in tables}
        self.db.conn.execute('DROP TABLE join_adoption')
        self.db.conn.execute('PRAGMA user_version=18')
        with store.Store(self.path) as upgraded:
            self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0], 19)
            self.assertIsNone(upgraded.join_adoption(self.row['request_id']))
            for table in tables:
                self.assertEqual([tuple(r) for r in upgraded.conn.execute('SELECT * FROM '+table)], before[table])
        with store.Store(self.path) as reopened:
            self.assertEqual(reopened.conn.execute('PRAGMA user_version').fetchone()[0], 19)

    def test_schema18_migration_failure_rolls_back_and_retries_without_losing_ledger(self):
        before = self.db.join_request(self.row['request_id'])
        self.db.conn.execute('DROP TABLE join_adoption')
        self.db.conn.execute('PRAGMA user_version=18')
        with mock.patch.object(store, '_UPGRADE_V19_SCHEMA', store._UPGRADE_V19_SCHEMA + '; INVALID SQL;'):
            with self.assertRaises(store.StoreError): store.Store(self.path)
        self.assertEqual(self.db.conn.execute('PRAGMA user_version').fetchone()[0], 18)
        self.assertFalse(self.db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='join_adoption'").fetchone())
        self.assertEqual(self.db.join_request(self.row['request_id']), before)
        self.assertEqual(self.db.card_approval_decision(self.row['request_id']), self.decision)
        with store.Store(self.path) as upgraded:
            self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0], 19)
            self.assertEqual(upgraded.effect_plan(self.row['request_id']), self.plan)


class AdoptionEffectsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.base.chmod(0o700)
        self.owner_key, self.agent_key, self.mirror_key, self.relay_key = ('2'*64, '3'*64, '4'*64, '5'*64)
        gs = effects.gs
        self.owner, self.agent, self.mirror, self.pin = map(gs._signer_pubkey,
            (self.owner_key, self.agent_key, self.mirror_key, self.relay_key))
        self.channel = '22222222-2222-4222-8222-222222222222'
        self.now = 100; self.publications = []; self.mutate = None; self.extra_profiles = []; self.extra_policies = []; self.extra_rosters = []
        self.prompt = self.file('prompt', 'Original prompt\n')
        self.responsible = self.file('responsible', '{"channels":[]}')
        self.env = self.file('agent.env', f'BUZZ_PRIVATE_KEY={self.agent_key}\nBUZZ_ACP_AGENT_OWNER={self.owner}\nBUZZ_ACP_CHANNELS=\nBUZZ_ACP_SYSTEM_PROMPT_FILE={self.prompt}\nBUZZ_RESPONSIBLE_CONFIG={self.responsible}\n')
        timer = self.file('timer', json.dumps({'version':1, 'owner_pubkey':self.owner, 'agents':[]}))
        owner_env = self.file('owner.env', f'BUZZ_PRIVATE_KEY={self.owner_key}\n')
        mirror_env = self.file('winner.env', f'BUZZ_PRIVATE_KEY={self.mirror_key}\n')
        self.cfg = self.file('winner.json', json.dumps({'channel_id':self.channel, 'chat_id':'oc_group',
            'sync_app_id':'cli_reader', 'mirror_pubkey':self.mirror, 'mirror_env_file':mirror_env}))
        self.binding_dir = self.base/'bindings'; self.binding_dir.mkdir(mode=0o700)
        self.spec = effects.AgentSpec(self.agent, self.owner, 'cli_agent', self.env, self.prompt,
            self.responsible, 'agent.service', timer, str(self.base), str(self.binding_dir),
            'cli_agent', str(self.base/'profile'), str(self.base/'data'), self.cfg)
        self.db = store.Store(self.base/'sql'/'hostd.db'); self.addCleanup(self.db.close)
        self.db.register_agent(self.agent, owner_pubkey=self.owner, app_id='cli_agent', config_path=self.env, now=10)
        self.db.create_join('JOIN-12345678', self.agent, self.owner, 'cli_agent', 'oc_group', kind='new_binding', now=10)
        self.db.rotate_card('JOIN-12345678', 'om_card', now=11)
        self.db.decide_join('JOIN-12345678', 'event_approve', self.owner, 'cli_agent', 'om_card', 1, approved=True, now=20)
        self.row = self.db.join_request('JOIN-12345678')
        reserve = self.binding_dir/self.row['request_id']
        self.plan = self.db.ensure_effect_plan(self.row['request_id'], '11111111-1111-4111-8111-111111111111',
            self.row['request_id'], str(reserve/'mirror.env'), str(reserve/'config.json'), now=21)
        self.db.reconcile_bindings([store.BindingRecord('winner', self.channel, 'oc_group', 'cli_reader', self.cfg,
            str(self.base/'reader'), str(self.base/'readerdata'), self.mirror, gs.chat_ref('oc_group'), 'active')], now=15)
        self.roles = {self.owner:'owner', self.mirror:'bot'}
        self.private = True; self.claim_owner_key = self.owner_key; self.heartbeat = 90; self.roster_at = 90
        self.duplicate_claim = False; self.policy_app = 'cli_agent'
        parent = self
        class Client:
            app_id = 'cli_agent'; present = True; complete = True; identity_app = 'cli_agent'
            def identity(inner): return inner.identity_app, ''
            def member_listing(inner, chat, user_id_type):
                assert chat == 'oc_group'
                return gs.MemberListing({'on_owner':'Owner'}, {inner.app_id:'ou_bot'} if inner.present else {}, inner.complete)
        class Runtime:
            active = False; restarts = 0
            def verify(inner, spec, channel): return inner.active
            def activate(inner, spec, channel): inner.active=True; inner.restarts+=1; return True
        class Registrar:
            def __init__(inner): inner.registrations=set()
            async def prepare_members(inner, row, plan):
                return effects.MemberPreparation(row['callback_app_id'],'oc_group',parent.now,True,((parent.owner,'on_owner'),),(),'f'*64)
            async def register(inner, row, plan):
                assert row['kind']=='channel' and plan['binding_id']=='winner'
                inner.registrations.add((row['request_id'], plan['binding_id']))
            async def readback(inner, row, plan):
                active = (row['request_id'],plan['binding_id']) in inner.registrations
                return effects.RegistrationProof(row['request_id'],plan['binding_id'],plan['channel_id'],'oc_group',
                    row['callback_app_id'],active,active,active,active,parent.now,'e'*64)
        self.client, self.runtime, self.registrar = Client(), Runtime(), Registrar()
        self.relay = effects.Nip98Relay('https://relay.test', owner_env, self.pin, http=self.http,
            trusted_relays=('https://relay.test',), clock=lambda:self.now)
        self.adapter = self.new_adapter()

    def file(self, name, value):
        path=self.base/name; path.write_text(value); path.chmod(0o600); return str(path)

    def new_adapter(self):
        return effects.JoinEffects(self.db, {self.agent:self.spec}, self.relay, self.runtime,
            self.registrar, clients={'cli_agent':self.client}, clock=lambda:self.now)

    def profile(self, key, owner_key=None):
        gs=effects.gs; pub=gs._signer_pubkey(key); owner_key=owner_key or self.owner_key
        owner=gs._signer_pubkey(owner_key)
        sig=gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{pub}:'.encode()).digest(),bytes.fromhex(owner_key),b'\0'*32).hex()
        return gs.sign_event(key,0,[['auth',owner,'',sig]],'{}',10)

    def events(self, kind):
        gs=effects.gs
        if kind==0: return [self.profile(self.agent_key), self.profile(self.mirror_key,self.claim_owner_key)] + self.extra_profiles
        if kind==30177:
            claim={'channel':self.channel,'chat_ref':gs.chat_ref('oc_group'),'claimed_at':15,
                   'heartbeat':self.heartbeat,'sync_app':{'version':1,'app_id':'cli_reader'}}
            return [gs.sign_event(self.owner_key,30177,[['d',self.agent]],json.dumps({'feishu':{'app_id':self.policy_app}}),20),
                gs.sign_event(self.claim_owner_key,30177,[['d',self.mirror]],json.dumps({'feishu':{'mirror':True,
                    'bindings':[claim,claim] if self.duplicate_claim else [claim]}}),90)] + self.extra_policies
        if kind==39000: return [gs.sign_event(self.relay_key,39000,[['d',self.channel],['private' if self.private else 'public']],'',15)]
        if kind==39002: return [gs.sign_event(self.relay_key,39002,[['d',self.channel]]+
                [['p',p,'',role] for p,role in self.roles.items()],'',self.roster_at)] + self.extra_rosters
        return []

    def http(self, url, headers, timeout, body=None):
        value=json.loads(body)
        if url.endswith('/query'):
            fil=value[0];rows=self.events(fil['kinds'][0])
            if 'authors' in fil: rows=[e for e in rows if e['pubkey'] in fil['authors']]
            if '#d' in fil: rows=[e for e in rows if effects.authority.tags(e,'d')[0][1] in fil['#d']]
            if self.mutate and fil['kinds']==[39002]:
                callback,self.mutate=self.mutate,None;callback()
            return 200,json.dumps(rows).encode()
        self.assertTrue(effects.gs._nip01_event_verified(value))
        self.publications.append(value)
        self.assertEqual(value['kind'],9000,'adoption must never create channel, mirror or claim')
        self.roles[value['tags'][1][1]]=value['tags'][2][1]
        return 200,json.dumps({'accepted':True,'event_id':value['id']}).encode()

    async def test_approved_loser_adopts_then_bound_path_finishes_without_new_binding_or_approval(self):
        original_decision=self.db.card_approval_decision(self.row['request_id'])
        cfg=Path(self.cfg).read_bytes()
        await self.adapter.apply(self.row)
        self.assertTrue((await self.adapter.readback(self.row)).verified)
        self.assertEqual(self.db.card_approval_decision(self.row['request_id']),original_decision)
        self.assertEqual(len(self.db.bindings()),1)
        self.assertEqual(len(self.publications),1)
        self.assertEqual(self.runtime.restarts,1)
        self.assertEqual(Path(self.cfg).read_bytes(),cfg)
        self.assertFalse(Path(self.plan['secret_ref']).exists())
        self.assertFalse(Path(self.plan['config_path']).exists())
        self.assertEqual(json.loads(self.db.join_adoption(self.row['request_id'])['original_plan']),self.plan)
        self.assertEqual(self.db.conn.execute('SELECT binding_id FROM agent_chat').fetchone()[0],'winner')
        await self.new_adapter().apply(self.db.join_request(self.row['request_id']))
        self.assertTrue((await self.new_adapter().readback(self.db.join_request(self.row['request_id']))).verified)
        self.assertEqual(len(self.publications),1)
        self.assertEqual(self.runtime.restarts,1)
        self.assertEqual(len(self.registrar.registrations),1)

    async def test_proof_negative_cases_leave_original_approval_and_plan_untouched(self):
        cases = [('cross_owner',lambda:setattr(self,'claim_owner_key','6'*64)),
                 ('wrong_app',lambda:setattr(self,'policy_app','cli_other')),
                 ('stale_claim',lambda:setattr(self,'heartbeat',-2000)),
                 ('future_roster',lambda:setattr(self,'roster_at',10000)),
                 ('owner_revoked',lambda:self.roles.pop(self.owner)),
                 ('mirror_revoked',lambda:self.roles.pop(self.mirror)),
                 ('public_channel',lambda:setattr(self,'private',False)),
                 ('ambiguous_claim',lambda:setattr(self,'duplicate_claim',True)),
                 ('not_native_member',lambda:setattr(self.client,'present',False)),
                 ('partial_roster',lambda:setattr(self.client,'complete',False)),
                 ('identity_changed',lambda:setattr(self.client,'identity_app','cli_other'))]
        for name,change in cases:
            with self.subTest(name=name):
                change()
                with self.assertRaises(effects.EffectError): await self.adapter.apply(self.row)
                self.assertEqual(self.db.join_request(self.row['request_id']),self.row)
                self.assertEqual(self.db.effect_plan(self.row['request_id']),self.plan)
                self.assertIsNone(self.db.join_adoption(self.row['request_id']))
                self.assertEqual(self.publications,[])
                self.claim_owner_key=self.owner_key;self.policy_app='cli_agent';self.heartbeat=90;self.roster_at=90
                self.roles={self.owner:'owner',self.mirror:'bot'}
                self.private=True;self.duplicate_claim=False;self.client.present=True;self.client.complete=True;self.client.identity_app='cli_agent'

    async def test_latest_roster_revocation_or_malformed_update_overrides_older_valid_membership(self):
        gs=effects.gs
        for tags in ([['p',self.mirror,'','bot']], [['p',self.owner,'','owner']],
                     [['p',self.owner,'','owner'],['p',self.owner,'','admin'],['p',self.mirror,'','bot']]):
            with self.subTest(tags=tags):
                self.extra_rosters=[gs.sign_event(self.relay_key,39002,[['d',self.channel]]+tags,'',91)]
                with self.assertRaises(effects.EffectError): await self.adapter.apply(self.row)
                self.assertEqual(self.db.join_request(self.row['request_id']),self.row)
                self.assertIsNone(self.db.join_adoption(self.row['request_id']))
                self.assertEqual(self.publications,[])

    async def test_legacy_blank_chat_ref_and_distinct_local_clock_require_exact_fresh_signed_claim(self):
        self.db.conn.execute("UPDATE binding SET chat_ref='',claimed_at=13 WHERE binding_id='winner'")
        self.roster_at=14  # Latest unchanged relay state can predate a renewed claim.
        self.db.conn.execute("UPDATE agent SET config_path=NULL")
        await self.adapter.apply(self.row)
        self.assertEqual(self.db.join_request(self.row['request_id'])['binding_id'], 'winner')
        self.assertIsNotNone(self.db.join_adoption(self.row['request_id']))

    async def test_pause_during_signed_read_and_artifact_unknown_refuse_before_effect(self):
        self.mutate=lambda:self.db.conn.execute("UPDATE binding SET status='paused' WHERE binding_id='winner'")
        with self.assertRaises(effects.EffectError): await self.adapter.apply(self.row)
        self.db.conn.execute("UPDATE binding SET status='active' WHERE binding_id='winner'")
        path=Path(self.plan['secret_ref']);path.parent.mkdir(mode=0o700);path.write_text('unverified artifact')
        with self.assertRaises(effects.EffectError): await self.adapter.apply(self.row)
        path.unlink()
        self.db.reserve_effect_step(self.row['request_id'],'channel','e'*64,now=90)
        self.db.defer_effect_step(self.row['request_id'],'channel',status='unknown',now=91)
        with self.assertRaises(effects.EffectError): await self.adapter.apply(self.row)
        self.assertIsNone(self.db.join_adoption(self.row['request_id']))
        self.assertEqual(self.publications,[]);self.assertEqual(self.runtime.restarts,0)

    async def test_target_status_and_tuple_changes_during_read_refuse_atomically(self):
        baseline=self.db.bindings()[0]
        mutations=[('status',v) for v in ('pending','paused','degraded','conflict','retired')]
        mutations += [('channel_id','33333333-3333-4333-8333-333333333333'),
                      ('config_path',self.env),('mirror_pubkey','a'*64),('chat_id','oc_else'),
                      ('claimed_at',16),('chat_ref','f'*64),('sync_app_id','cli_wrong')]
        for key,value in mutations:
            with self.subTest(key=key,value=value):
                self.mutate=lambda k=key,v=value:self.db.conn.execute('UPDATE binding SET '+k+'=? WHERE binding_id=?',(v,'winner'))
                with self.assertRaises(effects.EffectError): await self.adapter.apply(self.row)
                self.db.conn.execute('UPDATE binding SET '+key+'=? WHERE binding_id=?',(baseline[key],'winner'))
                self.assertEqual(self.db.join_request(self.row['request_id']),self.row)
                self.assertIsNone(self.db.join_adoption(self.row['request_id']))
                self.assertEqual(self.publications,[])

    async def test_card_generation_and_plan_changes_during_read_refuse(self):
        self.mutate=lambda:self.db.conn.execute('UPDATE join_request SET card_generation=2')
        with self.assertRaises(effects.EffectError): await self.adapter.apply(self.row)
        self.db.conn.execute('UPDATE join_request SET card_generation=1')
        self.mutate=lambda:self.db.conn.execute('UPDATE effect_plan SET updated_at=99')
        with self.assertRaises(effects.EffectError): await self.adapter.apply(self.row)
        self.assertIsNone(self.db.join_adoption(self.row['request_id']))
        self.assertEqual(self.publications,[])

    async def test_crash_after_adoption_commit_resumes_canonical_bound_route_without_duplicate_effects(self):
        original=self.db.adopt_join_binding
        def committed_then_crash(*args,**kwargs):
            original(*args,**kwargs)
            raise asyncio.CancelledError
        self.db.adopt_join_binding=committed_then_crash
        with self.assertRaises(asyncio.CancelledError): await self.adapter.apply(self.row)
        self.assertIsNotNone(self.db.join_adoption(self.row['request_id']))
        self.assertEqual(self.publications,[])
        self.db.adopt_join_binding=original
        await self.new_adapter().apply(self.db.join_request(self.row['request_id']))
        self.assertTrue((await self.new_adapter().readback(self.db.join_request(self.row['request_id']))).verified)
        self.assertEqual(len(self.publications),1)
        self.assertEqual(self.runtime.restarts,1)

    async def test_two_separately_approved_losers_race_and_retry_one_winner(self):
        from dataclasses import replace
        gs=effects.gs; key='7'*64; agent=gs._signer_pubkey(key)
        prompt=self.file('second.prompt','Second prompt\n'); responsible=self.file('second.responsible','{"channels":[]}')
        env=self.file('second.env',f'BUZZ_PRIVATE_KEY={key}\nBUZZ_ACP_AGENT_OWNER={self.owner}\nBUZZ_ACP_CHANNELS=\nBUZZ_ACP_SYSTEM_PROMPT_FILE={prompt}\nBUZZ_RESPONSIBLE_CONFIG={responsible}\n')
        spec=replace(self.spec,pubkey=agent,app_id='cli_second',reader_app_id='cli_second',
                     env_file=env,prompt_file=prompt,responsible_file=responsible,unit='second.service')
        self.db.register_agent(agent,owner_pubkey=self.owner,app_id='cli_second',config_path=env,now=10)
        self.db.create_join('JOIN-abcdef12',agent,self.owner,'cli_second','oc_group',kind='new_binding',now=10)
        self.db.rotate_card('JOIN-abcdef12','om_second',now=11)
        self.db.decide_join('JOIN-abcdef12','approve_second',self.owner,'cli_second','om_second',1,approved=True,now=20)
        second=self.db.join_request('JOIN-abcdef12')
        self.extra_profiles=[self.profile(key)]
        self.extra_policies=[gs.sign_event(self.owner_key,30177,[['d',agent]],json.dumps({'feishu':{'app_id':'cli_second'}}),20)]
        client=type(self.client)();client.app_id=client.identity_app='cli_second'
        runtime=type(self.runtime)()
        adapter=effects.JoinEffects(self.db,{agent:spec},self.relay,runtime,self.registrar,
                                    clients={'cli_second':client},clock=lambda:self.now)
        result=await asyncio.gather(self.adapter.apply(self.row),adapter.apply(second),return_exceptions=True)
        self.assertTrue(any(not isinstance(x,Exception) for x in result))
        # The shared timer-state lock deliberately serializes publishers. Retry
        # the blocked loser with its unchanged approval, as the real scheduler does.
        for request,worker in ((self.row,self.adapter),(second,adapter)):
            await worker.apply(self.db.join_request(request['request_id']))
            self.assertTrue((await worker.readback(self.db.join_request(request['request_id']))).verified)
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM join_adoption').fetchone()[0],2)
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM join_decision').fetchone()[0],2)
        self.assertEqual(len(self.db.bindings()),1)
        self.assertEqual(len(self.publications),2)
        self.assertEqual(len(self.registrar.registrations),2)


if __name__ == '__main__':
    unittest.main()
