"""Actual runtime/card/outward assembly, offline native and signed IO only."""
import asyncio
import copy
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import sys
import types
import unittest
from unittest import mock
from urllib.parse import urlsplit

import test_hostd_fallback_approval_producer as outward
import test_hostd_fallback_onboarding as cards
import test_hostd_wiring_lifecycle as root_fixture
from hostd import remote_approval, onboarding_runtime as runtime, fallback_discovery, fallback_onboarding, fallback_approval_producer, http_pool, store
from hostd.scheduler import Scheduler
from hostd.join_effects import Nip98Relay
from hostd.signed_reads import SignedReader
import recovery_authority as authority

base, gs, claims = cards.base, cards.gs, cards.claims
SUBJECT, SUBJECT_OWNER, SUBJECT_APP, BINDING = cards.SUBJECT, cards.SUBJECT_OWNER, cards.SUBJECT_APP, cards.BINDING
setUpModule, tearDownModule = cards.setUpModule, cards.tearDownModule


class RuntimeHTTPS(cards.NativeCardsHTTPS):
    def __init__(self, testcase):
        super().__init__(testcase)
        self.card_entered, self.card_release = threading.Event(), threading.Event()
        self.block_card = False
    class Connection(cards.NativeCardsHTTPS.Connection):
        def request(self, method, path, body=None, headers=None):
            s=self.server
            if urlsplit(path).path == '/open-apis/im/v1/chats':
                s.calls.append((method,path));s.testcase.assertEqual(method,'GET')
                self.response=cards.discovery_tests.Response({'code':0,'data':{'items':[{'chat_id':base.CHAT}], 'has_more':False}})
                return
            if (s.block_card and method=='POST' and urlsplit(path).path=='/open-apis/im/v1/messages'
                    and json.loads(body)['msg_type']=='interactive'):
                s.card_entered.set();s.testcase.assertTrue(s.card_release.wait(10))
            super().request(method,path,body,headers)


class RuntimeFallbackTests(base.TmpCase, unittest.IsolatedAsyncioTestCase):
    assembly, seed = cards.FallbackOnboardingTests.assembly, cards.FallbackOnboardingTests.seed
    setup_discovery = cards.FallbackOnboardingTests.setup_discovery
    setup_fallback = cards.FallbackOnboardingTests.setup_fallback
    policy = cards.FallbackOnboardingTests.policy
    mirror_change = cards.FallbackOnboardingTests.mirror_change
    save_catalog = cards.FallbackOnboardingTests.save_catalog
    decision = cards.FallbackOnboardingTests.decision
    assert_no_runtime = cards.FallbackOnboardingTests.assert_no_runtime
    matches = staticmethod(outward.OutwardTests.matches)
    add_policy = outward.OutwardTests.add_policy
    member_present = outward.OutwardTests.member_present

    def setup_runtime(self, *, own=False):
        self.setup_fallback();self.add_policy('anyone')
        self.pool.close();self.https=RuntimeHTTPS(self)
        self.pool=http_pool.HttpPool(connection_factory=self.https.connect);self.addCleanup(self.pool.close)
        self.client.http_pool=self.pool
        self.outward,self.outward_gets,self.timeline=[],[],[]
        self.mode,self.readback_mode,self.roster_mode='','',''
        self.post_hook,self.read_hook=None,None
        self.entered,self.release=threading.Event(),threading.Event();self.check_unknown=False
        original = self.relay.http
        def low(url, headers, timeout, body=None):
            if url.endswith('/events'):
                event = json.loads(body)
                auth = json.loads(base64.b64decode(headers['Authorization'].split(' ', 1)[1]))
                self.assertTrue(gs._nip01_event_verified(auth))
                authority.exact_tag(auth, 'u', url); authority.exact_tag(auth, 'method', 'POST')
                authority.exact_tag(auth, 'payload', hashlib.sha256(body).hexdigest())
                self.assertTrue(gs._nip01_event_verified(event))
                stage = {9000: 'member', 30078: 'approval'}[event['kind']]
                self.assertEqual(auth['pubkey'], event['pubkey'])
                if stage == 'member':
                    self.assertEqual(event['pubkey'], base.OWNER_PK)
                    self.assertEqual(event['tags'], [['h', base.CHANNEL], ['p', SUBJECT], ['role', 'bot']])
                    self.assertEqual(event['content'], '')
                else:
                    self.assertEqual(event['pubkey'], base.MIRROR_PK)
                    self.assertIn('x-auth-tag', headers)
                    remote_approval.decode(event, now=self.now)
                    self.assertEqual({m['pubkey']: m['role'] for m in self.world.members}.get(SUBJECT), 'bot')
                if self.check_unknown:
                    # A separately opened SQL connection observes the committed
                    # UNKNOWN before physical mutation; no worker-thread SQL.
                    with sqlite3.connect('file:' + str(self.db.path) + '?mode=ro', uri=True) as observed:
                        row = observed.execute('SELECT state,event_id FROM fallback_outward WHERE request_id=? AND stage=?',
                            (self.request_id, stage)).fetchone()
                    self.assertEqual(row, ('unknown', event['id']))
                self.timeline.append(('POST', stage)); self.outward.append(copy.deepcopy(event))
                if self.post_hook is not None:
                    hook, self.post_hook = self.post_hook, None; hook(stage)
                if self.mode == 'blocked-' + stage:
                    self.entered.set(); self.assertTrue(self.release.wait(10))
                if self.mode == 'unreachable-' + stage: raise OSError('offline unknown outcome')
                if stage == 'member':
                    policies = [e for e in self.world.relay_events if e['kind'] == 10100 and e['pubkey'] == SUBJECT]
                    current = authority.latest(policies)
                    policy = json.loads(current['content'])['channel_add_policy']
                    if policy == 'nobody' or (policy == 'owner_only' and event['pubkey'] != SUBJECT_OWNER):
                        return 200, json.dumps({'accepted': False, 'event_id': event['id'], 'message': 'policy:' + policy}).encode()
                    if self.mode != 'no-roster': self.member_present()
                if not any(e['id'] == event['id'] for e in self.world.relay_events):
                    self.world.relay_events.append(copy.deepcopy(event))
                if self.mode == 'lost-' + stage: raise OSError('offline lost response')
                return 200, json.dumps({'accepted': True, 'event_id': event['id'], 'message': 'duplicate:'}).encode()
            if url.endswith('/query'):
                # Use the actual native owner-authenticated query first. This
                # retains transport/signature checks for every public frame.
                status, raw = original(url, headers, timeout, body)
                filters, rows = json.loads(body), json.loads(raw)
                rows = [e for e in rows if any(self.matches(e, f) for f in filters)]
                outward_query = any('ids' in f or any(k in (9000, 30078) for k in f.get('kinds', [])) for f in filters)
                if outward_query:
                    self.outward_gets.append(copy.deepcopy(filters)); self.timeline.append(('GET', 'outward'))
                    if self.read_hook is not None:
                        hook, self.read_hook = self.read_hook, None; hook()
                    if self.readback_mode == 'empty': rows = []
                    elif self.readback_mode == 'forged' and rows: rows[-1]['sig'] = '0' * 128
                    elif self.readback_mode == 'alternate' and rows:
                        key = base.OWNER_KEY if rows[-1]['kind'] == 9000 else base.MIRROR_KEY
                        rows[-1]['sig'] = gs.sync.nk.schnorr_sign(bytes.fromhex(rows[-1]['id']), bytes.fromhex(key), b'\1' * 32).hex()
                    elif self.readback_mode == 'partial': return 503, b'{}'
                    elif self.readback_mode == 'saturated': rows = rows * 257
                if self.roster_mode and any(39002 in f.get('kinds', []) for f in filters):
                    for i, e in enumerate(rows):
                        if e['kind'] == 39002:
                            if self.roster_mode == 'forged': rows[i]['sig'] = '0' * 128
                            elif self.roster_mode == 'stale':
                                rows[i] = gs.sign_event(claims.PIN_KEY, 39002, e['tags'], '', self.now - 100000)
                return status, json.dumps(rows).encode()
            return original(url, headers, timeout, body)
        self.relay = Nip98Relay('https://relay.test', self.env.signer_env, claims.PIN,
            http=low, trusted_relays=('https://relay.test',), clock=lambda: self.now)
        self.scheduler=Scheduler();self.addCleanup(self.scheduler.close)
        binding_dir=self.tmp/'new-bindings';binding_dir.mkdir(mode=0o700)
        template=base.write_owner_only(self.tmp/'template.json','{}')
        self.runtime_config=runtime.RuntimeConfig(1,self.env.signer_env,'https://relay.test',claims.PIN,
            str(template),str(binding_dir),str(self.legacy_path),str(self.catalog_path),('https://relay.test',))
        if own:self.own_issuer()
        self.services=[]
        self.addCleanup(self.cleanup_services)
        return self

    def cleanup_services(self):
        self.release.set();self.https.card_release.set()
        for service in self.services:service.close()

    def own_issuer(self):
        selected=self.cfg['agents'][self.cfg['desk_pubkey']]
        prompt=base.write_owner_only(self.tmp/'own-prompt.md','offline prompt')
        responsible=base.write_owner_only(self.tmp/'own-responsible.json','{}')
        tag=claims.profile(claims.AGENT_KEY,base.OWNER_KEY,self.now)['tags'][0]
        env=base.write_owner_only(self.tmp/'own-agent.env',f'BUZZ_PRIVATE_KEY={claims.AGENT_KEY}\n'
            f'BUZZ_ACP_AGENT_OWNER={base.OWNER_PK}\nBUZZ_AUTH_TAG=\'{json.dumps(tag)}\'\n'
            f'BUZZ_ACP_CHANNELS={base.CHANNEL}\nBUZZ_ACP_SYSTEM_PROMPT_FILE={prompt}\nBUZZ_RESPONSIBLE_CONFIG={responsible}\n')
        self.catalog_doc['agents']=[{'name':'own-issuer','env_file':str(env),'unit':'buzz-own-issuer.service',
            'capabilities':{'summary':'offline own issuer','repos':[]},
            'feishu':{'app_id':base.AGENT_APP,'lark_config_dir':selected['lark_config_dir'],
                      'lark_data_dir':selected['lark_data_dir']}}]
        self.save_catalog()

    async def create(self):
        service=await runtime.OnboardingRuntime.create(self.runtime_config,self.db,registrar=None,
            scheduler=self.scheduler,http_pool=self.pool,http=self.relay.http,
            runner=lambda *a,**k:self.fail('no CLI'),base_env={},clock=lambda:self.now)
        self.services.append(service);return service

    def member_hint(self, event='native-added-1'):
        return {'type':'im.chat.member.bot.added_v1','app':base.AGENT_APP,'chat_id':base.CHAT,
            'event_id':event,'operator_id':{'open_id':'ou_subjectowner','union_id':'on_subjectowner'},
            'subject_pubkey': 'f'*64, 'subject_app_id':'cli_forged'}

    def callback(self, row, **changes):
        event={'type':'card.action.trigger','app':base.AGENT_APP,'event_id':'native-owner-click-runtime',
            'operator':{'open_id':'ou_subjectowner','union_id':'on_subjectowner'},
            'context':{'open_chat_id':base.CHAT,'open_message_id':row['card_message_id']},
            'action':{'value':{'request_id':row['request_id'],'generation':row['card_generation'],'decision':'approve'}}}
        for k,v in changes.items():
            if k=='generation':event['action']['value'][k]=v
            elif k=='operator':event['operator']=v
            elif k=='decision':event['action']['value'][k]=v
            else:event[k]=v
        return event

    def fallback_rows(self):
        return [r for r in self.db.join_requests() if self.db.fallback_provenance(r['request_id']) is not None]

    async def seed_card(self, *, approved=False):
        actual=fallback_discovery.FallbackDiscoverer(self.db,self.clients,self.relay,clock=lambda:self.now)
        service=fallback_onboarding.FallbackOnboarding(self.db,actual,self.clients,self.relay,
            catalog_path=self.catalog_path,legacy_join_path=self.legacy_path,clock=lambda:self.now)
        result=await service.request(BINDING,SUBJECT,'native-seed-card')
        self.assertEqual(result.status,'pending' if self.https.post_mode=='lost' else 'requested')
        self.request_id=result.request_id;row=self.db.join_request(result.request_id)
        if approved:self.assertEqual((await service.decide(self.decision(row))).status,'approved')
        return self.db.join_request(result.request_id)

    def producer(self):
        actual=fallback_discovery.FallbackDiscoverer(self.db,self.clients,self.relay,clock=lambda:self.now)
        return fallback_approval_producer.FallbackApprovalProducer(self.db,actual,self.clients,self.relay,
            catalog_path=self.catalog_path,legacy_join_path=self.legacy_path,clock=lambda:self.now)

    async def recover(self, service):
        method=getattr(service,'recover_fallback',None)
        self.assertTrue(callable(method),'actual runtime periodic/startup recovery is missing')
        await method()

    async def test_control_actual_native_discovery_card_click_producer_without_runtime_effect(self):
        self.setup_runtime();row=await self.seed_card(approved=True)
        result=await self.producer().run(row['request_id'])
        self.assertEqual(result.status,'record_verified')
        self.assertEqual([e['kind'] for e in self.outward],[9000,30078]);self.assert_no_runtime()
        self.assertEqual(self.db.join_request(row['request_id'])['status'],'approved')

    async def test_control_actual_own_catalog_factory_remains_native_and_distinct(self):
        self.setup_runtime(own=True);service=await self.create()
        self.assertEqual(tuple(service.records),(base.AGENT_APP,))
        self.assertIn(claims.AGENT,service.effects.specs)
        self.assertEqual(service.clients[base.AGENT_APP].http_pool,self.pool)
        self.assertEqual(self.https.card_posts,[])
        self.assertIsNone(self.db.fallback_provenance('JOIN-12345678'))

    async def test_control_actual_own_new_binding_still_uses_ordinary_coordinator(self):
        self.setup_runtime(own=True)
        self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id=?",(BINDING,))
        self.mirror_change(lambda body:body['feishu'].update(bindings=[]))
        service=await self.create()
        service.enqueue_feed(base.AGENT_APP,{'type':'_connected','app':base.AGENT_APP})
        await service.drain()
        own=[r for r in self.db.join_requests() if r['agent_id']==claims.AGENT]
        self.assertEqual(len(own),1);self.assertEqual(own[0]['kind'],'new_binding')
        self.assertEqual(own[0]['status'],'requested');self.assertTrue(own[0]['card_message_id'])
        self.assertIsNone(self.db.fallback_provenance(own[0]['request_id']))
        self.assertEqual(self.outward,[]);self.assert_no_runtime()

    async def test_control_unrecognized_feed_app_is_rejected_without_native_io(self):
        self.setup_runtime();service=await self.create()
        before=(len(self.queries),len(self.https.calls))
        result=service.enqueue_feed('cli_attacker',self.member_hint())
        self.assertEqual(result['toast']['type'],'error');self.assertEqual(before,(len(self.queries),len(self.https.calls)))
        self.assertEqual(self.db.join_requests(),[])

    async def test_sync_only_profile_mismatch_remains_pending_not_a_usable_feed(self):
        self.setup_runtime();selected=self.cfg['agents'][self.cfg['desk_pubkey']]
        base.write_owner_only(Path(selected['lark_config_dir'])/'config.json',json.dumps({'apps':[{'appId':'cli_other'}]}))
        service=await self.create()
        self.assertNotIn(base.AGENT_APP,service.eligible_apps)
        self.assertEqual(service.enqueue_feed(base.AGENT_APP,self.member_hint())['toast']['type'],'error')
        await self.recover(service)
        self.assertEqual(self.db.join_requests(),[]);self.assertEqual(self.https.card_posts,[])
        self.assertIn('怎么解决',service.last_notice)

    async def test_factory_sync_only_issuer_builds_actual_adapters_without_agent_or_spec(self):
        self.setup_runtime();service=await self.create()
        for name,cls in [('fallback_discoverer',fallback_discovery.FallbackDiscoverer),
                ('fallback_onboarding',fallback_onboarding.FallbackOnboarding),
                ('fallback_producer',fallback_approval_producer.FallbackApprovalProducer)]:
            self.assertIsInstance(getattr(service,name,None),cls)
            self.assertIs(getattr(service,name).store,self.db)
        self.assertEqual(service.records,{})
        self.assertEqual(service.effects.specs,{})
        self.assertEqual(service.eligible_apps,(base.AGENT_APP,))
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM agent').fetchone()[0],0)

    async def test_connected_actual_runtime_discovers_public_subject_and_sends_one_card(self):
        self.setup_runtime();service=await self.create()
        toast=service.enqueue_feed(base.AGENT_APP,{'type':'_connected','app':base.AGENT_APP})
        self.assertNotEqual(toast['toast']['type'],'error')
        await service.drain();rows=self.fallback_rows();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['agent_id'],SUBJECT);self.assertEqual(rows[0]['status'],'requested')
        self.assertEqual(len(self.https.card_posts),1);self.assert_no_runtime()
        service.enqueue_feed(base.AGENT_APP,{'type':'_connected','app':base.AGENT_APP});await service.drain()
        self.assertEqual(len(self.fallback_rows()),1);self.assertEqual(len(self.https.card_posts),1)

    async def test_member_hint_ignores_forged_subject_and_native_queue_is_nonblocking(self):
        self.setup_runtime();service=await self.create()
        before=(len(self.queries),len(self.https.calls))
        answer=service.enqueue_feed(base.AGENT_APP,self.member_hint())
        self.assertNotEqual(answer['toast']['type'],'error');self.assertEqual(before,(len(self.queries),len(self.https.calls)))
        await service.drain();self.assertEqual([r['agent_id'] for r in self.fallback_rows()],[SUBJECT])
        self.assertEqual(self.outward,[]);self.assert_no_runtime()

    async def test_marker_callback_queues_without_io_then_actual_first_decision_and_two_posts(self):
        self.setup_runtime(own=True);row=await self.seed_card();service=await self.create()
        before=(len(self.queries),len(self.https.calls));answer=service.enqueue_feed(base.AGENT_APP,self.callback(row))
        self.assertNotEqual(answer['toast']['type'],'error');self.assertEqual(before,(len(self.queries),len(self.https.calls)))
        self.assertNotIn('已批准',answer['toast']['content']);self.assertEqual(self.db.join_request(row['request_id'])['status'],'requested')
        await service.drain();self.assertEqual(self.db.join_request(row['request_id'])['status'],'approved')
        self.assertEqual([e['kind'] for e in self.outward],[9000,30078]);self.assert_no_runtime()
        service.enqueue_feed(base.AGENT_APP,self.callback(row));await service.drain()
        self.assertEqual(len(self.outward),2)

    async def test_wrong_issuer_generation_or_operator_never_becomes_approval(self):
        self.setup_runtime(own=True);row=await self.seed_card();service=await self.create()
        variants=[dict(app='cli_foreignbot'),dict(generation=row['card_generation']+1),
                  dict(operator={'open_id':'ou_other','union_id':'on_other'})]
        for changes in variants:
            service.enqueue_feed(base.AGENT_APP,self.callback(row,**changes));await service.drain()
            self.assertEqual(self.db.join_request(row['request_id'])['status'],'requested')
            self.assertEqual(self.outward,[]);self.assert_no_runtime()
        # Positive control proves these negatives reach the marker callback lane.
        service.enqueue_feed(base.AGENT_APP,self.callback(row));await service.drain()
        self.assertEqual(self.db.join_request(row['request_id'])['status'],'approved')

    async def test_denied_marker_is_terminal_metadata_and_no_implicit_reinvite(self):
        self.setup_runtime();row=await self.seed_card();service=await self.create()
        service.enqueue_feed(base.AGENT_APP,self.callback(row,decision='deny'));await service.drain()
        self.assertEqual(self.db.join_request(row['request_id'])['status'],'denied')
        await self.recover(service);service.enqueue_feed(base.AGENT_APP,self.member_hint('native-reinvite'));await service.drain()
        self.assertEqual(len(self.fallback_rows()),1);self.assertEqual(len(self.https.card_posts),1)
        self.assertEqual(self.outward,[]);self.assert_no_runtime()
        self.assertIn('怎么解决',service.last_notice)

    async def test_expired_marker_cannot_publish_or_rotate_original_card(self):
        self.setup_runtime();row=await self.seed_card();service=await self.create();self.now=row['deadline']+1
        service.enqueue_feed(base.AGENT_APP,self.callback(row));await self.recover(service);await service.drain()
        self.assertNotEqual(self.db.join_request(row['request_id'])['status'],'approved')
        self.assertEqual(len(self.https.card_posts),1);self.assertEqual(self.outward,[]);self.assert_no_runtime()

    async def test_partial_native_or_forged_current_public_snapshot_is_pending(self):
        self.setup_runtime();service=await self.create()
        for failure in ('partial','forged'):
            self.https.partial=failure=='partial';self.query_mode='forged' if failure=='forged' else ''
            await self.recover(service);self.assertEqual(self.fallback_rows(),[])
            self.assertEqual(self.https.card_posts,[]);self.assertEqual(self.outward,[])
            self.assertIn('怎么解决',service.last_notice)
        self.https.partial=False;self.query_mode='';await self.recover(service)
        self.assertEqual(len(self.fallback_rows()),1)

    async def test_retired_binding_during_native_read_never_reserves_card(self):
        self.setup_runtime();service=await self.create();entered,release=threading.Event(),threading.Event()
        def gate():entered.set();self.assertTrue(release.wait(10))
        self.assertTrue(callable(getattr(service,'recover_fallback',None)))
        self.https.bot_hook=gate;task=asyncio.create_task(self.recover(service))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait,2))
            self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id=?",(BINDING,))
        finally:
            release.set();await asyncio.gather(task,return_exceptions=True)
        await task;self.assertEqual(self.fallback_rows(),[]);self.assertEqual(self.https.card_posts,[])

    async def test_callback_scope_revoked_during_owner_lookup_never_decides_or_publishes(self):
        self.setup_runtime(own=True);row=await self.seed_card();service=await self.create()
        entered,release=threading.Event(),threading.Event()
        def gate():entered.set();self.assertTrue(release.wait(10))
        self.https.contact_hook=gate
        self.assertNotEqual(service.enqueue_feed(base.AGENT_APP,self.callback(row))['toast']['type'],'error')
        task=asyncio.create_task(service.drain())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait,2))
            self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id=?",(BINDING,))
        finally:
            release.set();await asyncio.gather(task,return_exceptions=True)
        await task;self.assertEqual(self.db.join_request(row['request_id'])['status'],'requested')
        self.assertEqual(self.outward,[]);self.assert_no_runtime()

    async def test_shared_metadata_and_card_queue_bound_has_no_immediate_io(self):
        self.setup_runtime(own=True);row=await self.seed_card();service=await self.create()
        before=(len(self.queries),len(self.https.calls));accepted=0
        for i in range(140):
            value=self.callback(row);value['event_id']='native-queued-'+str(i)
            if service.enqueue_feed(base.AGENT_APP,value)['toast']['type']!='error':accepted+=1
        self.assertEqual(accepted,128)
        self.assertEqual(service.enqueue_feed(base.AGENT_APP,self.member_hint())['toast']['type'],'error')
        self.assertEqual(before,(len(self.queries),len(self.https.calls)))
        service.close();self.assertEqual(service.enqueue_feed(base.AGENT_APP,self.callback(row))['toast']['type'],'error')
        self.assertEqual(self.db.join_request(row['request_id'])['status'],'requested')

    async def test_unknown_member_reopen_runtime_uses_original_get_only(self):
        self.setup_runtime();row=await self.seed_card(approved=True);self.mode='lost-member'
        self.assertEqual((await self.producer().run(row['request_id'])).status,'pending')
        self.assertEqual(len(self.outward),1);pin=self.db.fallback_outward(row['request_id'],'member').pin
        self.db.close();self.db=store.Store(self.tmp/'sql'/'hostd.db');self.addCleanup(self.db.close)
        self.mode='';service=await self.create();await self.recover(service)
        self.assertEqual(self.db.fallback_outward(row['request_id'],'member').state,'acked')
        self.assertEqual(self.db.fallback_outward(row['request_id'],'member').pin,pin)
        self.assertEqual([e['kind'] for e in self.outward],[9000,30078]);self.assert_no_runtime()

    async def test_unknown_approval_reopen_runtime_get_only_original_signature(self):
        self.setup_runtime();row=await self.seed_card(approved=True);self.mode='lost-approval'
        self.assertEqual((await self.producer().run(row['request_id'])).status,'pending')
        self.assertEqual(len(self.outward),2);pin=self.db.fallback_outward(row['request_id'],'approval').pin
        self.db.close();self.db=store.Store(self.tmp/'sql'/'hostd.db');self.addCleanup(self.db.close)
        self.mode='';service=await self.create();await self.recover(service)
        self.assertEqual(self.db.fallback_outward(row['request_id'],'approval').state,'acked')
        self.assertEqual(self.db.fallback_outward(row['request_id'],'approval').pin,pin)
        self.assertEqual(len(self.outward),2);self.assertTrue(self.outward_gets);self.assert_no_runtime()

    async def test_unknown_absent_original_event_runtime_never_resends_or_repin(self):
        self.setup_runtime();row=await self.seed_card(approved=True);self.mode='unreachable-member'
        await self.producer().run(row['request_id']);pin=self.db.fallback_outward(row['request_id'],'member').pin
        self.db.close();self.db=store.Store(self.tmp/'sql'/'hostd.db');self.addCleanup(self.db.close)
        service=await self.create();await self.recover(service);await self.recover(service)
        self.assertEqual(len(self.outward),1);self.assertEqual(self.db.fallback_outward(row['request_id'],'member').pin,pin)
        self.assertEqual(self.db.fallback_outward(row['request_id'],'member').state,'unknown')
        self.assertIsNone(self.db.fallback_outward(row['request_id'],'approval'));self.assert_no_runtime()

    async def test_unknown_card_reopen_native_original_get_no_duplicate_card(self):
        self.setup_runtime();self.https.post_mode='lost';row=await self.seed_card()
        self.assertEqual(row['card_generation'],0);self.assertEqual(len(self.https.card_posts),1)
        transport=self.db.join_transport(row['request_id'])
        self.assertEqual((transport['send_generation'],transport['send_status']),(1,'unknown'))
        self.db.close();self.db=store.Store(self.tmp/'sql'/'hostd.db');self.addCleanup(self.db.close)
        self.https.post_mode='';service=await self.create();await self.recover(service)
        restored=self.db.join_request(row['request_id']);self.assertTrue(restored['card_message_id'])
        self.assertEqual(restored['card_generation'],1);self.assertEqual(len(self.https.card_posts),1)
        self.assertEqual(self.outward,[]);self.assert_no_runtime()

    async def test_concurrent_drain_and_recovery_share_owned_lock_and_one_native_card(self):
        self.setup_runtime();service=await self.create()
        self.assertTrue(callable(getattr(service,'recover_fallback',None)))
        self.https.block_card=True
        self.assertNotEqual(service.enqueue_feed(base.AGENT_APP,self.member_hint())['toast']['type'],'error')
        first=asyncio.create_task(service.drain());others=[]
        try:
            self.assertTrue(await asyncio.to_thread(self.https.card_entered.wait,5))
            self.assertTrue(service._lock.locked())
            others=[asyncio.create_task(service.drain()),asyncio.create_task(self.recover(service))]
            await asyncio.sleep(.02);self.assertTrue(all(not task.done() for task in others))
            self.assertEqual(self.outward,[]);self.assert_no_runtime()
        finally:
            self.https.card_release.set()
            results=await asyncio.gather(first,*others,return_exceptions=True)
        self.assertTrue(all(result is None for result in results))
        self.assertEqual(len(self.fallback_rows()),1);self.assertEqual(len(self.https.card_posts),1)
        self.assertFalse(service._lock.locked());self.assert_no_runtime()

    async def test_close_and_repeated_cancel_join_original_dispatched_card_io(self):
        self.setup_runtime();service=await self.create();self.https.block_card=True
        self.assertNotEqual(service.enqueue_feed(base.AGENT_APP,self.member_hint())['toast']['type'],'error')
        task=asyncio.create_task(service.drain())
        try:
            self.assertTrue(await asyncio.to_thread(self.https.card_entered.wait,2))
            service.close();task.cancel();await asyncio.sleep(.01);task.cancel();await asyncio.sleep(.01)
            self.assertFalse(task.done());self.assertEqual(service.enqueue_feed(base.AGENT_APP,self.member_hint())['toast']['type'],'error')
            self.assertEqual(self.db.conn.execute('SELECT count(*) FROM binding').fetchone()[0],1)
        finally:
            self.https.card_release.set();await asyncio.gather(task,return_exceptions=True)
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(len(self.https.card_posts),1)
        rows=self.fallback_rows();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'requested')
        self.assertEqual(self.outward,[]);self.assert_no_runtime()

    async def test_runtime_start_performs_recovery_without_waiting_for_new_feed(self):
        self.setup_runtime();service=await self.create()
        self.assertTrue(callable(getattr(service,'recover_fallback',None)))
        task=service.start()
        try:
            for _ in range(500):
                if self.fallback_rows() and self.fallback_rows()[0]['card_message_id']:break
                await asyncio.sleep(.01)
            self.assertEqual(len(self.fallback_rows()),1)
            self.assertTrue(self.fallback_rows()[0]['card_message_id']);self.assertEqual(len(self.https.card_posts),1)
        finally:
            service.close();await asyncio.wait_for(task,10)
        self.assertEqual(self.outward,[]);self.assert_no_runtime()

    async def test_close_repeated_cancel_reaps_member_io_and_retains_unknown_pin(self):
        self.setup_runtime(own=True);row=await self.seed_card();service=await self.create()
        self.mode='blocked-member'
        self.assertNotEqual(service.enqueue_feed(base.AGENT_APP,self.callback(row))['toast']['type'],'error')
        task=asyncio.create_task(service.drain())
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait,5))
            self.assertEqual(self.db.fallback_outward(row['request_id'],'member').state,'unknown')
            service.close();task.cancel();await asyncio.sleep(.01);task.cancel();await asyncio.sleep(.01)
            self.assertFalse(task.done());self.assertEqual(self.db.join_request(row['request_id'])['status'],'approved')
        finally:
            self.release.set();await asyncio.gather(task,return_exceptions=True)
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual([e['kind'] for e in self.outward],[9000])
        self.assertEqual(self.db.fallback_outward(row['request_id'],'member').state,'unknown')
        self.assertIsNone(self.db.fallback_outward(row['request_id'],'approval'));self.assert_no_runtime()

    async def test_root_actual_factory_sync_only_registry_profile_and_spawn_lane(self):
        self.setup_runtime();selected=self.cfg['agents'][self.cfg['desk_pubkey']]
        reg=root_fixture.registry.Registry()
        reg.bindings[BINDING]=root_fixture.registry.Binding(BINDING,self.env.config,self.tmp/'state',base.CHANNEL,
            base.CHAT,base.AGENT_APP,selected['lark_config_dir'],selected['lark_data_dir'],'https://relay.test')
        h=root_fixture.hd.Hostd(reg,self.tmp/'status.json',state_db=self.db.path,onboarding_config=self.runtime_config)
        self.addCleanup(h.scheduler.close);self.addCleanup(h.http_pool.close)
        h.http_pool.close();h.http_pool=self.pool
        self.db.conn.execute("UPDATE binding SET status='active' WHERE binding_id=?",(BINDING,))
        # Root's actual factory uses default low IO; inject only those native
        # transport defaults, without replacing factory, Coordinator or Store.
        defaults=runtime.OnboardingRuntime.create.__func__.__kwdefaults__
        try:
            import websockets
            missing_socket=False
        except ImportError:
            missing_socket=True
        socket_stub=types.ModuleType('websockets')
        socket_stub.connect=lambda *a,**k:self.fail('no socket connection')
        previous_socket=sys.modules.get('websockets')
        if missing_socket:sys.modules['websockets']=socket_stub
        try:
            with mock.patch.dict(defaults,{'http':self.relay.http,'clock':lambda:self.now,'runner':lambda *a,**k:self.fail('no CLI')}):
                await h.start_onboarding()
        finally:
            if missing_socket:
                if previous_socket is None:sys.modules.pop('websockets',None)
                else:sys.modules['websockets']=previous_socket
        try:
            self.assertIsInstance(h.onboarding,runtime.OnboardingRuntime)
            self.assertEqual(h.onboarding.records,{})
            self.assertNotIn(base.AGENT_APP,h.onboarding.eligible_apps)
            self.assertEqual(tuple(map(str,h.app_profiles[base.AGENT_APP])),
                (selected['lark_config_dir'],selected['lark_data_dir']))
            # Use the root spawn/supervisor with only the external feed body held.
            async def low_feed(app, bindings):
                self.assertEqual(app,base.AGENT_APP);self.assertEqual([b.name for b in bindings],[BINDING])
                h.onboarding.enqueue_feed(app,{'type':'_connected','app':app})
                self.assertEqual(len(h.onboarding._queue),0)  # No issuer authority yet.
                await asyncio.Future()
            h.feishu_child=low_feed;h._running=True;task=h.spawn_app(base.AGENT_APP)
            await asyncio.sleep(.03);await h.onboarding.drain()
            self.assertIn(base.AGENT_APP,h.onboarding.eligible_apps)
            self.assertEqual(len(h.runtime_store.join_requests()),1)
            self.assertIsNotNone(h.runtime_store.fallback_provenance(h.runtime_store.join_requests()[0]['request_id']))
        finally:
            h._running=False
            for task in h._tasks:task.cancel()
            await asyncio.gather(*h._tasks,return_exceptions=True)
            await h.close_onboarding()


if __name__=='__main__':unittest.main()
