"""Fallback cards: real SQLite/native bot IO/public signatures, offline only."""
import asyncio
import base64
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import test_hostd_fallback_discovery as discovery_tests
import test_hostd_approval_journal as journal
from hostd import agent_catalog, fallback_discovery, http_pool, onboarding, store
from hostd.join_cards import BotCards
from hostd.join_effects import JoinEffects, Nip98Relay
from hostd.signed_reads import SignedReader
import recovery_authority as authority

fallback = (importlib.import_module('hostd.fallback_onboarding')
            if importlib.util.find_spec('hostd.fallback_onboarding') else None)
base, gs, claims = discovery_tests.base, discovery_tests.gs, discovery_tests.claims
SUBJECT, SUBJECT_OWNER, SUBJECT_APP = discovery_tests.SUBJECT, discovery_tests.SUBJECT_OWNER, discovery_tests.SUBJECT_APP
BINDING = discovery_tests.BINDING
setUpModule, tearDownModule = discovery_tests.setUpModule, discovery_tests.tearDownModule
FROZEN_SQL = dict(journal.FROZEN, _UPGRADE_V7_SCHEMA='b1f593c51d4591d7f78dae333c5df30dbffe1514bf07c238748bb776c6ea0199')


class NativeCardsHTTPS(discovery_tests.NativeBotHTTPS):
    def __init__(self, testcase):
        super().__init__(testcase)
        self.views, self.card_posts, self.dm_posts, self.contact_reads = {}, [], [], []
        self.contact_union, self.contact_hook = 'on_subjectowner', None
        self.post_mode, self.history_partial, self.sender_app = '', False, base.AGENT_APP
    class Connection(discovery_tests.NativeBotHTTPS.Connection):
        def request(self, method, path, body=None, headers=None):
            s = self.server; u = urlsplit(path); params = parse_qs(u.query)
            if u.path.endswith('/tenant_access_token/internal') or u.path.endswith('/scopes') or u.path.endswith('/members/list'):
                super().request(method, path, body, headers)
                if u.path.endswith('/members/list') and params.get('member_types') == ['user']:
                    value = json.loads(self.response.body)
                    value['data'].update(users=[{'member_id': 'on_localowner'}, {'member_id': 'on_subjectowner'}], user_total=2)
                    self.response = discovery_tests.Response(value)
                return
            s.calls.append((method, path)); t = s.testcase
            self.lost = False
            if u.path.startswith('/open-apis/contact/v3/users/'):
                t.assertEqual(method, 'GET'); t.assertEqual(params['user_id_type'], ['open_id'])
                open_id = u.path.rsplit('/', 1)[1]; s.contact_reads.append(open_id)
                if s.contact_hook is not None:
                    hook, s.contact_hook = s.contact_hook, None; hook()
                value = {'user': {'open_id': open_id, 'union_id': s.contact_union}}
            elif u.path == '/open-apis/im/v1/messages' and method == 'POST':
                data = json.loads(body)
                if params['receive_id_type'] == ['union_id']:
                    t.assertIn(data['receive_id'], ('on_subjectowner', 'on_localowner'))
                    t.assertEqual(data['msg_type'], 'text'); s.dm_posts.append(copy.deepcopy(data))
                    mid, chat = 'om_dm' + str(len(s.dm_posts)), 'oc_dm'
                else:
                    t.assertEqual(params['receive_id_type'], ['chat_id'])
                    t.assertEqual(data['receive_id'], base.CHAT); t.assertEqual(data['msg_type'], 'interactive')
                    s.card_posts.append(copy.deepcopy(data)); mid, chat = 'om_fallback' + str(len(s.card_posts)), base.CHAT
                s.views[mid] = {'message_id': mid, 'chat_id': chat, 'msg_type': data['msg_type'],
                    'sender': {'id': s.sender_app, 'id_type': 'app_id', 'sender_type': 'app'},
                    'create_time': str(t.now * 1000),
                    'body': {'content': data['content']}}
                self.lost = s.post_mode == 'lost' and data['msg_type'] == 'interactive'
                value = {'message_id': mid, 'chat_id': chat}
            elif u.path == '/open-apis/im/v1/messages' and method == 'GET':
                t.assertEqual(params['container_id_type'], ['chat'])
                t.assertEqual(params['container_id'], [base.CHAT])
                value = {'items': [v for v in s.views.values() if v['chat_id'] == base.CHAT], 'has_more': s.history_partial,
                         'page_token': 'same' if s.history_partial else ''}
            elif u.path.startswith('/open-apis/im/v1/messages/'):
                mid = u.path.rsplit('/', 1)[1]
                if method == 'GET': value = {'items': [s.views[mid]] if mid in s.views else []}
                elif method == 'PATCH':
                    s.views[mid]['body']['content'] = json.loads(body)['content']; value = {}
                else: t.fail('unsupported card transport mutation')
            else: t.fail('unexpected native endpoint')
            self.response = discovery_tests.Response({'code': 0, 'data': value})
        def getresponse(self):
            if getattr(self, 'lost', False): raise OSError('offline lost native response')
            return super().getresponse()


class FallbackOnboardingTests(base.TmpCase, unittest.IsolatedAsyncioTestCase):
    assembly, seed = claims.RoundPublisher.assembly, claims.RoundPublisher.seed
    setup_discovery = discovery_tests.FallbackDiscoveryTests.setup_discovery
    policy = discovery_tests.FallbackDiscoveryTests.policy
    mirror_change = discovery_tests.FallbackDiscoveryTests.mirror_change

    def setup_fallback(self):
        self.setup_discovery()
        # The genuine approval control has a verified human owner already in
        # the channel/group. Only the SUBJECT bot is absent before approval.
        # Match the bridge's channel-member People projection, rather than
        # inventing a global identity endpoint for a nonmember owner.
        self.world.members.append({'pubkey': SUBJECT_OWNER, 'role': 'member'})
        self.pool.close(); self.https = NativeCardsHTTPS(self)
        self.pool = http_pool.HttpPool(connection_factory=self.https.connect); self.addCleanup(self.pool.close)
        self.client.http_pool = self.pool
        self.people_mode = ''; self.people_reads = 0
        original = self.relay.http
        def signed_http(url, headers, timeout, body=None):
            if url == gs.people_url('https://relay.test', base.CHANNEL):
                self.assertIsNone(body)
                auth = json.loads(base64.b64decode(headers['Authorization'].split(' ', 1)[1]))
                self.assertTrue(gs._nip01_event_verified(auth)); self.assertEqual(auth['pubkey'], base.OWNER_PK)
                authority.exact_tag(auth, 'u', url); authority.exact_tag(auth, 'method', 'GET')
                self.people_reads += 1
                members = {m['pubkey'] for m in self.world.members}
                unions = {pk: union for pk, union in ((base.OWNER_PK, 'on_localowner'), (SUBJECT_OWNER, 'on_subjectowner'))
                          if pk in members}
                if self.people_mode == 'ambiguous': unions[base.OWNER_PK] = 'on_subjectowner'
                if self.people_mode == 'missing': unions.pop(SUBJECT_OWNER)
                return 200, json.dumps({'channel': base.CHANNEL, 'people': {}, 'union_ids': unions}).encode()
            return original(url, headers, timeout, body)
        self.relay = Nip98Relay('https://relay.test', self.env.signer_env, claims.PIN,
            http=signed_http, trusted_relays=('https://relay.test',), clock=lambda: self.now)
        self.catalog_path, self.legacy_path = self.tmp / 'catalog.json', self.tmp / 'legacy.json'
        self.catalog_doc = {'version': 1, 'owner_pubkey': base.OWNER_PK,
            'buzz': {'cli_path': self.env.buzz_cli, 'cli_sha256': hashlib.sha256(Path(self.env.buzz_cli).read_bytes()).hexdigest()},
            'state_dir': str(self.tmp / 'state'), 'lark_cli': '/offline/lark-cli', 'agents': []}
        self.save_catalog()
        return self

    def save_catalog(self):
        base.write_owner_only(self.catalog_path, json.dumps(self.catalog_doc))
        base.write_owner_only(self.legacy_path, json.dumps(dict(self.catalog_doc, agents=[])))

    def own_subject_catalog(self, *, blocked=False, same_app_other_pub=False):
        key = '6'.zfill(64) if same_app_other_pub else discovery_tests.SUBJECT_KEY
        pub = gs._signer_pubkey(key)
        cfg, data = self.tmp / 'own-cfg', self.tmp / 'own-data'; cfg.mkdir(); data.mkdir()
        base.write_owner_only(cfg / 'config.json', json.dumps({'apps': [{'appId': SUBJECT_APP}]}))
        env, prompt, responsible = self.tmp / 'own.env', self.tmp / 'own.md', self.tmp / 'own-responsible.json'
        tag = claims.profile(key, base.OWNER_KEY, self.now)['tags'][0]
        base.write_owner_only(prompt, 'offline own prompt'); base.write_owner_only(responsible, '{}')
        base.write_owner_only(env, f'BUZZ_PRIVATE_KEY={key}\nBUZZ_ACP_AGENT_OWNER={base.OWNER_PK}\n'
            f'BUZZ_AUTH_TAG=\'{json.dumps(tag)}\'\nBUZZ_ACP_CHANNELS={base.CHANNEL}\n'
            f'BUZZ_ACP_SYSTEM_PROMPT_FILE={prompt}\nBUZZ_RESPONSIBLE_CONFIG={responsible}\n')
        if blocked: env.chmod(0o644)
        self.catalog_doc['agents'] = [{'name': 'own', 'env_file': str(env), 'unit': 'buzz-local-own.service',
            'capabilities': {'summary': 'offline own', 'repos': []},
            'feishu': {'app_id': SUBJECT_APP, 'lark_config_dir': str(cfg), 'lark_data_dir': str(data)}}]
        self.save_catalog()
        previous = sys.modules['hostd.secrets_store'].app_secret
        def synthetic_secret(app, c, d):
            if (app, c, d) == (SUBJECT_APP, str(cfg), str(data)): return 'offline-own-only'
            return previous(app, c, d)
        sys.modules['hostd.secrets_store'].app_secret = synthetic_secret
        if not blocked and not same_app_other_pub:
            self.world.relay_events.append(claims.profile(key, base.OWNER_KEY, self.now))
            self.policy(pub, base.OWNER_KEY, {'feishu': {'app_id': SUBJECT_APP}})
        return pub

    def identity(self):
        reader = SignedReader(self.relay)
        async def people(app, now):
            self.assertEqual(app, base.AGENT_APP)
            return onboarding.PeopleBindings(await reader.read('people', channel=base.CHANNEL))
        async def profile(pub, now):
            rows = await reader.read('query', filters=[{'kinds': [0], 'authors': [pub], 'limit': 257}])
            self.assertLess(len(rows), 256)
            self.assertTrue(all(e['pubkey'] == pub and e['kind'] == 0 for e in rows))
            return authority.latest(rows)
        return onboarding.IdentityResolver(self.clients, people, profile)

    def instance(self, db=None):
        self.assertIsNotNone(fallback, 'dedicated fallback onboarding feature is missing')
        database = db or self.db
        discoverer = fallback_discovery.FallbackDiscoverer(database, self.clients, self.relay, clock=lambda: self.now)
        return fallback.FallbackOnboarding(database, discoverer, self.clients, self.relay,
            catalog_path=self.catalog_path, legacy_join_path=self.legacy_path, clock=lambda: self.now)

    async def request(self, service=None, event='native-member-result-1'):
        return await (service or self.instance()).request(BINDING, SUBJECT, event)

    async def pending(self, action):
        value = await action
        self.assertEqual(value.status, 'pending'); self.assertIn('怎么解决', value.notice); self.assertIn('复制给 AI', value.notice)
        return value

    def decision(self, row, **changes):
        values = dict(app_id=base.AGENT_APP, chat_id=base.CHAT, message_id=row['card_message_id'],
            event_id='native-owner-click-1', request_id=row['request_id'], generation=row['card_generation'],
            approved=True, operator=onboarding.Operator('ou_subjectowner', 'on_subjectowner'))
        values.update(changes)
        return onboarding.CardDecision(**values)

    def assert_no_runtime(self):
        for table in ('effect_plan', 'effect_step', 'agent_chat', 'remote_grant', 'restart_operation'):
            self.assertEqual(self.db.conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table)

    def seed_plain_subject_request(self):
        # Offline fixture seed, never Store.register_agent(active). An ordinary
        # cross-app request does not acquire fallback provenance or permission.
        self.db.conn.execute('INSERT INTO agent VALUES(?,?,?,?,?,?)',
            (SUBJECT, SUBJECT_OWNER, SUBJECT_APP, None, 'paused', self.now))
        row = self.db.create_join('JOIN-aabbccdd', SUBJECT, SUBJECT_OWNER, base.AGENT_APP, base.CHAT,
            kind='channel', binding_id=BINDING, now=self.now)
        return row

    async def test_native_card_control_sender_app_chat_and_generation_are_read_back_from_actual_bot_api(self):
        self.setup_fallback(); row = self.seed_plain_subject_request()
        mid = await BotCards(self.clients).send(row, 1)
        self.assertTrue(mid.startswith('om_')); self.assertEqual(len(self.https.card_posts), 1)
        self.assertEqual(self.https.views[mid]['sender']['id'], base.AGENT_APP)
        self.assertIn('申请 JOIN-aabbccdd · 卡片 1', self.https.views[mid]['body']['content'])
        self.assert_no_runtime()

    async def test_native_identity_control_uses_issuer_open_id_union_and_authenticated_people_for_subject_owner(self):
        self.setup_fallback(); identity = self.identity()
        person = await identity.resolve(base.AGENT_APP, onboarding.Operator('ou_subjectowner', 'on_subjectowner'), now=self.now)
        self.assertEqual((person.app_id, person.pubkey), (base.AGENT_APP, SUBJECT_OWNER))
        self.assertEqual(await identity.owner(SUBJECT, base.AGENT_APP, now=self.now), SUBJECT_OWNER)
        self.assertEqual(self.https.contact_reads, ['ou_subjectowner']); self.assertGreater(self.people_reads, 0)

    async def test_actual_protected_catalog_control_classifies_own_record_without_caller_verified_flags(self):
        self.setup_fallback(); self.own_subject_catalog()
        catalog = await asyncio.to_thread(agent_catalog.load, self.catalog_path, legacy_join_path=self.legacy_path)
        self.assertEqual(len(catalog.records), 1)
        row = catalog.records[0]
        self.assertEqual((row.pubkey, row.app_id, row.owner_pubkey, row.status),
            (SUBJECT, SUBJECT_APP, base.OWNER_PK, 'own_bot_verified'))

    async def test_native_card_negative_control_wrong_actual_sender_never_finishes_readback(self):
        self.setup_fallback(); row = self.seed_plain_subject_request(); self.https.sender_app = 'cli_wrong'
        with self.assertRaises(ValueError): await BotCards(self.clients).send(row, 1)
        self.assertEqual(self.db.join_request(row['request_id'])['card_generation'], 0)

    async def test_strict_local_card_decision_control_does_not_accept_ordinary_cross_app_request_as_provenance(self):
        self.setup_fallback(); row = self.seed_plain_subject_request()
        self.db.rotate_card(row['request_id'], 'om_control', now=self.now)
        self.assertTrue(self.db.decide_join(row['request_id'], 'control-click', SUBJECT_OWNER, base.AGENT_APP,
            'om_control', 1, approved=True, now=self.now))
        self.assertIsNone(self.db.card_approval_decision(row['request_id'])); self.assert_no_runtime()

    async def test_public_subject_card_has_dedicated_issuer_provenance_and_no_local_activation(self):
        self.setup_fallback(); result = await self.request(); self.assertEqual(result.status, 'requested')
        row = self.db.join_request(result.request_id); agent = self.db.conn.execute('SELECT * FROM agent WHERE pubkey=?', (SUBJECT,)).fetchone()
        self.assertEqual((row['agent_id'], row['owner_pubkey'], row['callback_app_id'], row['binding_id']),
            (SUBJECT, SUBJECT_OWNER, base.AGENT_APP, BINDING))
        self.assertEqual((agent['app_id'], agent['config_path'], agent['status']), (SUBJECT_APP, None, 'paused'))
        marker = self.db.fallback_provenance(result.request_id)
        self.assertEqual((marker.subject_app_id, marker.issuer_app_id, marker.claimed_at), (SUBJECT_APP, base.AGENT_APP, self.now-100))
        self.assertEqual(len(self.https.card_posts), 1); self.assert_no_runtime()
        columns = {r[1] for r in self.db.conn.execute('PRAGMA table_info(fallback_request)')}
        self.assertFalse(columns & {'body','content','secret','key','token','env_file','config_path','open_id','union_id','card_message_id'})

    async def test_same_owner_absent_from_actual_catalog_is_not_mistaken_for_own_local(self):
        self.setup_fallback()
        self.world.relay_events = [e for e in self.world.relay_events if e['id'] not in (self.subject_profile['id'], self.subject_policy['id'])]
        self.world.relay_events.append(claims.profile(discovery_tests.SUBJECT_KEY, base.OWNER_KEY, self.now-10))
        self.policy(SUBJECT, base.OWNER_KEY, {'feishu': {'app_id': SUBJECT_APP}}, self.now-10)
        value = await self.request(); self.assertEqual(value.status, 'requested')
        self.assertEqual(self.db.join_request(value.request_id)['owner_pubkey'], base.OWNER_PK)

    async def test_actual_own_catalog_record_takes_own_path_without_fallback_card_or_public_row(self):
        self.setup_fallback(); self.own_subject_catalog()
        value = await self.request(); self.assertEqual(value.status, 'own_local')
        self.assertEqual(self.https.card_posts, []); self.assertEqual(self.db.join_requests(), [])

    async def test_blocked_own_pub_catalog_record_cannot_be_treated_as_foreign(self):
        self.setup_fallback(); self.own_subject_catalog(blocked=True)
        await self.pending(self.request()); self.assertEqual(self.https.card_posts, [])

    async def test_actual_catalog_same_app_different_pub_cannot_be_treated_as_foreign(self):
        self.setup_fallback(); self.own_subject_catalog(same_app_other_pub=True)
        await self.pending(self.request()); self.assertEqual(self.https.card_posts, [])

    async def test_preexisting_paused_null_agent_is_never_borrowed_or_overwritten(self):
        self.setup_fallback(); self.db.conn.execute('INSERT INTO agent VALUES(?,?,?,?,?,?)',
            (SUBJECT, SUBJECT_OWNER, SUBJECT_APP, None, 'paused', self.now-100))
        old = tuple(self.db.conn.execute('SELECT * FROM agent WHERE pubkey=?', (SUBJECT,)).fetchone())
        await self.pending(self.request())
        self.assertEqual(tuple(self.db.conn.execute('SELECT * FROM agent WHERE pubkey=?', (SUBJECT,)).fetchone()), old)
        self.assertEqual(self.db.join_requests(), []); self.assertEqual(self.https.card_posts, [])

    async def test_duplicate_member_notifications_reuse_original_request_card_and_timestamp(self):
        self.setup_fallback(); first = await self.request(); row = self.db.join_request(first.request_id)
        second = await self.request(event='native-member-result-2')
        self.assertEqual(second.request_id, first.request_id); self.assertEqual(self.db.join_request(first.request_id), row)
        self.assertEqual(len(self.https.card_posts), 1); self.assert_no_runtime()

    async def test_lost_card_response_close_reopen_recovers_original_native_get_without_post(self):
        self.setup_fallback(); self.https.post_mode = 'lost'
        first = await self.pending(self.request()); self.assertTrue(first.request_id)
        self.assertEqual(self.db.join_transport(first.request_id)['send_status'], 'unknown')
        original = self.db.join_request(first.request_id); first_calls = len(self.https.calls)
        self.db.close(); self.now += 301
        with store.Store(self.db.path) as reopened:
            value = await self.request(self.instance(reopened), event='native-member-result-2')
            self.assertEqual(value.request_id, first.request_id)
            current = reopened.join_request(first.request_id)
            self.assertEqual((current['created_at'], current['card_generation']), (original['created_at'], 1))
            self.assertEqual(current['card_message_id'], 'om_fallback1')
        self.assertEqual(len(self.https.card_posts), 1)
        self.assertFalse(any(m == 'POST' and not p.endswith('/tenant_access_token/internal')
                             for m, p in self.https.calls[first_calls:]))

    async def test_unknown_card_missing_history_is_pending_forever_without_repost_or_new_generation(self):
        self.setup_fallback(); self.https.post_mode = 'lost'; first = await self.pending(self.request())
        self.https.views.clear(); first_calls = len(self.https.calls); self.now += 301
        await self.pending(self.request(event='native-member-result-2'))
        self.assertEqual(len(self.https.card_posts), 1)
        self.assertEqual(self.db.join_transport(first.request_id)['send_generation'], 1)
        self.assertFalse(any(m == 'POST' and not p.endswith('/tenant_access_token/internal')
                             for m, p in self.https.calls[first_calls:]))

    async def test_exact_owner_click_approves_subject_app_but_records_native_issuer_app_and_no_effects(self):
        self.setup_fallback(); value = await self.request(); row = self.db.join_request(value.request_id)
        result = await self.instance().decide(self.decision(row)); self.assertEqual(result.status, 'approved')
        current = self.db.join_request(value.request_id); self.assertEqual(current['status'], 'approved')
        proof = self.db.fallback_approval_decision(value.request_id)
        self.assertEqual((proof.subject_app_id, proof.issuer_app_id, proof.subject_owner_pubkey), (SUBJECT_APP, base.AGENT_APP, SUBJECT_OWNER))
        self.assertIsNone(self.db.card_approval_decision(value.request_id)); self.assert_no_runtime()
        self.assertEqual(self.db.conn.execute('SELECT app_id FROM join_decision').fetchone()[0], base.AGENT_APP)

    async def test_wrong_callback_app_card_generation_or_chat_cannot_decide(self):
        self.setup_fallback(); value = await self.request(); row = self.db.join_request(value.request_id)
        for changes in ({'app_id': 'cli_foreignbot'}, {'message_id': 'om_forged'}, {'generation': 2}, {'chat_id': 'oc_other'}):
            with self.subTest(changes=changes): await self.pending(self.instance().decide(self.decision(row, **changes)))
        self.assertEqual(self.db.join_request(value.request_id)['status'], 'requested')
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM join_decision').fetchone()[0], 0)

    async def test_nonowner_native_union_or_ambiguous_people_never_changes_request(self):
        self.setup_fallback(); value = await self.request(); row = self.db.join_request(value.request_id)
        self.https.contact_union = 'on_localowner'
        await self.pending(self.instance().decide(self.decision(row, operator=onboarding.Operator('ou_nonowner', 'on_localowner'))))
        self.https.contact_union = 'on_subjectowner'; self.people_mode = 'ambiguous'
        await self.pending(self.instance().decide(self.decision(row)))
        self.assertEqual(self.db.join_request(value.request_id)['status'], 'requested')

    async def test_expired_original_discovery_metadata_is_not_itself_callback_authority(self):
        self.setup_fallback(); value = await self.request(); row = self.db.join_request(value.request_id)
        self.now += 31; self.mirror_change(lambda d: d['feishu'].update(bindings=[]))
        await self.pending(self.instance().decide(self.decision(row)))
        self.assertEqual(self.db.join_request(value.request_id)['status'], 'requested'); self.assert_no_runtime()

    async def test_same_chat_new_claimed_at_scope_cannot_approve_old_card(self):
        self.setup_fallback(); value = await self.request(); row = self.db.join_request(value.request_id)
        self.mirror_change(lambda d: d['feishu']['bindings'][0].update(claimed_at=self.now-50))
        await self.pending(self.instance().decide(self.decision(row)))
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM join_decision').fetchone()[0], 0)

    async def test_current_sql_revoke_during_native_operator_io_is_pending_without_decision(self):
        self.setup_fallback(); value = await self.request(); row = self.db.join_request(value.request_id)
        loop, reached = asyncio.get_running_loop(), threading.Event()
        def revoke():
            self.db.conn.execute("UPDATE binding SET status='retired' WHERE binding_id=?", (BINDING,)); reached.set()
        def hook():
            loop.call_soon_threadsafe(revoke); self.assertTrue(reached.wait(5))
        self.https.contact_hook = hook
        await self.pending(self.instance().decide(self.decision(row)))
        self.assertTrue(reached.is_set()); self.assertEqual(self.db.conn.execute('SELECT count(*) FROM join_decision').fetchone()[0], 0)

    async def test_discovery_forgery_partial_or_catalog_unreadable_never_creates_card_provenance(self):
        self.setup_fallback(); self.query_mode = 'forged'; await self.pending(self.request())
        self.query_mode = ''; self.https.partial = True; await self.pending(self.request())
        self.https.partial = False; self.catalog_path.chmod(0o644); await self.pending(self.request())
        self.assertEqual(self.db.join_requests(), []); self.assertEqual(self.https.card_posts, [])

    async def test_local_coordinator_sweeper_and_card_handler_skip_fallback_marker_and_actual_effect_plan(self):
        self.setup_fallback(); value = await self.request(); row = self.db.join_request(value.request_id)
        own_effects = JoinEffects(self.db, {}, None, None, clock=lambda: self.now)
        local = onboarding.Coordinator(self.db, self.identity(), BotCards(self.clients), effects=own_effects, clock=lambda: self.now)
        self.addCleanup(local.close); self.now += 301
        original_contacts, original_queries = list(self.https.contact_reads), len(self.queries)
        await local.tick(now=self.now)
        local.enqueue_card(self.decision(row), now=self.now); await local.drain()
        self.assertEqual(len(self.https.card_posts), 1)
        self.assertEqual(self.https.contact_reads, original_contacts); self.assertEqual(len(self.queries), original_queries)
        self.assertEqual(self.db.join_request(value.request_id)['status'], 'requested')
        self.assert_no_runtime()

    async def test_generic_local_sql_decision_and_status_advance_reject_marker_even_exact_owner_card(self):
        self.setup_fallback(); value = await self.request(); row = self.db.join_request(value.request_id)
        self.assertFalse(self.db.decide_join(value.request_id, 'borrowed-local-click', SUBJECT_OWNER, base.AGENT_APP,
            row['card_message_id'], row['card_generation'], approved=True, now=self.now))
        self.assertFalse(self.db.advance_join(value.request_id, expected='requested', target='expired', now=self.now+7*86400))
        self.assertEqual(self.db.join_request(value.request_id)['status'], 'requested')

    async def test_terminal_foreign_metadata_is_held_not_reused_as_new_intent(self):
        self.setup_fallback(); value = await self.request(); row = self.db.join_request(value.request_id)
        result = await self.instance().decide(self.decision(row, approved=False)); self.assertEqual(result.status, 'denied')
        await self.pending(self.request(event='new-member-result-after-denial'))
        self.assertEqual(len(self.https.card_posts), 1); self.assert_no_runtime()


class FallbackSchemaTests(base.TmpCase):
    def test_original_schema_one_through_seven_sql_bytes_remain_exact(self):
        for name, expected in FROZEN_SQL.items():
            self.assertEqual(hashlib.sha256(getattr(store, name).encode()).hexdigest(), expected, name)

    def seed_old_seven(self):
        helper = journal.ApprovalJournal('test_reads_first_actual_approved_current_card_decision_as_hashes')
        # The historical fixtures independently named a binding "local".
        # Give the actual approval fixture a separate ID, not a SQL overwrite.
        naming = mock.patch.object(journal, 'BINDING', 'approval-seed')
        naming.start(); self.addCleanup(naming.stop)
        helper.setUp(); self.addCleanup(helper.doCleanups)
        db = helper.db; journal.historical.seed_schema5(db.conn)
        legacy = journal.historical.RemoteLedger()
        evidence = store.RemoteGrantEvidence(**vars(legacy.evidence()))
        grant = db.activate_remote_grant(evidence, expected_revision=0, now=20)
        delivery = db.reserve_remote_delivery(grant.target_id, journal.historical.SOURCE, 'message',
            revision=grant.revision, scope_hash=grant.scope_hash, source_at=20, content_hash=journal.historical.CONTENT, now=21)
        db.mark_remote_unknown(delivery.record.id, revision=grant.revision, scope_hash=grant.scope_hash, now=22)
        db.ack_remote_delivery(delivery.record.id, revision=grant.revision, scope_hash=grant.scope_hash,
            sender_app_id=journal.historical.APP, message_id='om_seed', receipt_hash=journal.historical.RECEIPT, now=23)
        db.commit_remote_cursor(grant.target_id, 20, revision=grant.revision, scope_hash=grant.scope_hash, complete=True, now=24)
        pin = helper.pin(); helper.reserve(pin); helper.unknown(pin)
        frozen = store._SCHEMA_V6 + store._UPGRADE_V7_SCHEMA
        with sqlite3.connect(':memory:') as schema:
            schema.executescript(frozen)
            names = [row[0] for row in schema.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {name: [tuple(row) for row in db.conn.execute(f'SELECT * FROM "{name}"')] for name in names}
        self.assertEqual(len(before), 28); self.assertTrue(all(before.values()))
        path = self.tmp / 'old' / 'state.db'; path.parent.mkdir(mode=0o700)
        with sqlite3.connect(path) as old:
            old.executescript(frozen)
            for name, rows in before.items():
                old.executemany(f'INSERT INTO "{name}" VALUES({",".join("?" for _ in rows[0])})', rows)
            old.execute('PRAGMA user_version=7')
        path.chmod(0o600)
        return path, before, pin

    def test_actual_populated_seven_upgrade_keeps_all_old_rows_foreign_keys_and_unknown_original_publication(self):
        path, before, pin = self.seed_old_seven()
        with store.Store(path) as upgraded:
            self.assertEqual(upgraded.conn.execute('PRAGMA user_version').fetchone()[0], store.SCHEMA_VERSION)
            for name, rows in before.items():
                self.assertEqual([tuple(row) for row in upgraded.conn.execute(f'SELECT * FROM "{name}"')], rows, name)
            self.assertEqual(upgraded.conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(upgraded.conn.execute('SELECT count(*) FROM fallback_request').fetchone()[0], 0)
            self.assertEqual(upgraded.approval_publication(journal.REQUEST).pin, pin)
            self.assertEqual(upgraded.approval_publication(journal.REQUEST).state, 'unknown')

    def test_drifted_actual_seven_is_refused_without_fallback_partial_schema_or_row_changes(self):
        path, before, _ = self.seed_old_seven()
        with sqlite3.connect(path) as old: old.execute('CREATE TABLE unexpected_drift(metadata TEXT)')
        with self.assertRaises(store.StoreError): store.Store(path)
        with sqlite3.connect(path) as old:
            self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0], 7)
            self.assertFalse(old.execute("SELECT name FROM sqlite_master WHERE name='fallback_request'").fetchone())
            for name, rows in before.items():
                self.assertEqual(old.execute(f'SELECT * FROM "{name}"').fetchall(), rows, name)
